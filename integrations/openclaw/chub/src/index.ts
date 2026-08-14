import { Type } from "typebox";
import {
  buildJsonPluginConfigSchema,
  definePluginEntry,
  jsonResult,
} from "openclaw/plugin-sdk/core";

import type { ChubConfig } from "./client.js";
import {
  dispatchWeixinChubMessage,
} from "./client.js";
import { getStatusTool } from "./tools/get-status.js";
import {
  sendNotificationTool,
  type SendNotificationParameters,
} from "./tools/send-notification.js";
import {
  extractVerbatimNotificationMessage,
} from "./verbatim-message.js";

const VERBATIM_MESSAGE_TTL_MS = 5 * 60 * 1000;
const VERBATIM_MESSAGE_MAX_RUNS = 100;
const WEIXIN_VOICE_TRANSCRIPT_MARKER = "[[chub-weixin-voice-transcript]]";
const WEIXIN_CHANNEL_FAILURE = "Chub 消息通道暂时不可用，请稍后重试。";
const WEIXIN_CONTENT_FAILURE =
  "未识别到可处理的文字或语音转写，请重新发送文字，或稍后重试语音。";

const configSchema = Type.Object({
  baseUrl: Type.Optional(Type.String({
    description: "Fixed Chub base URL on the local Tailnet.",
  })),
  timeoutMs: Type.Optional(Type.Integer({
    minimum: 500,
    maximum: 10_000,
    default: 3_000,
  })),
  weixinChubMode: Type.Optional(Type.Boolean({
    default: false,
    description: "Forward Weixin direct messages to the single fixed Chub dispatch endpoint without running an OpenClaw agent or LLM.",
  })),
});

async function sha256(value: string): Promise<string> {
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(value),
  );
  return Array.from(new Uint8Array(digest))
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

async function submissionIdentity(
  event: {
    content: string;
    channel?: string;
    sessionKey?: string;
    senderId?: string;
    timestamp?: number;
    messageType?: "text" | "voice";
  },
  context: {
    accountId?: string;
    conversationId?: string;
    sessionKey?: string;
    senderId?: string;
  },
): Promise<{ messageId: string; correlationId?: string } | null> {
  if (!Number.isFinite(event.timestamp)) {
    return null;
  }
  const sessionKey = context.sessionKey ?? event.sessionKey ?? "";
  const messageDigest = await sha256(JSON.stringify([
    event.channel ?? "",
    context.accountId?.trim() ?? "",
    context.conversationId ?? "",
    context.senderId?.trim()
      || event.senderId?.trim()
      || context.conversationId?.trim()
      || "",
    event.timestamp,
    event.messageType ?? "text",
    event.content,
  ]));
  return {
    messageId: `openclaw-weixin:${messageDigest}`,
    correlationId: sessionKey
      ? `openclaw-session:${await sha256(sessionKey)}`
      : undefined,
  };
}

function weixinMessage(event: { content: string; body?: string }): {
  content: string;
  messageType: "text" | "voice";
} {
  if (event.content === WEIXIN_VOICE_TRANSCRIPT_MARKER) {
    const transcript = event.body?.trim();
    if (transcript && transcript !== event.content) {
      return { content: transcript, messageType: "voice" };
    }
  }
  return { content: event.content, messageType: "text" };
}

const plugin: ReturnType<typeof definePluginEntry> = definePluginEntry({
  id: "chub",
  name: "Chub",
  description: "Use explicitly allowlisted Chub capabilities through a fixed Tailnet connection.",
  configSchema: buildJsonPluginConfigSchema(
    configSchema as unknown as Record<string, unknown>,
  ),
  register(api) {
    const config = (api.pluginConfig ?? {}) as ChubConfig;
    const verbatimMessages = new Map<string, {
      message: string;
      expiresAt: number;
    }>();

    api.on("before_dispatch", async (event, context) => {
      if (
        config.weixinChubMode !== true
        || event.channel !== "openclaw-weixin"
        || event.isGroup === true
      ) {
        return;
      }

      const message = weixinMessage(event);
      if (!message.content.trim()) {
        return {
          handled: true,
          text: WEIXIN_CONTENT_FAILURE,
        };
      }
      const identity = await submissionIdentity({ ...event, ...message }, context);
      if (identity === null) {
        return {
          handled: true,
          text: WEIXIN_CHANNEL_FAILURE,
        };
      }
      const replyAccountId = context.accountId?.trim();
      const replyRecipient = context.senderId?.trim()
        || event.senderId?.trim()
        || context.conversationId?.trim();
      if (
        !replyAccountId
        || !replyRecipient
        || !replyRecipient.endsWith("@im.wechat")
      ) {
        return {
          handled: true,
          text: WEIXIN_CHANNEL_FAILURE,
        };
      }
      const dispatch = await dispatchWeixinChubMessage(config, {
        ...identity,
        ...message,
        replyAccountId,
        replyRecipient,
      });
      if (!dispatch.available) {
        return {
          handled: true,
          text: dispatch.message,
        };
      }
      if (dispatch.disposition === "pass") {
        return;
      }
      if (dispatch.disposition === "handled") {
        return { handled: true };
      }
      return {
        handled: true,
        text: dispatch.message ?? WEIXIN_CHANNEL_FAILURE,
      };
    });

    const rememberVerbatimMessage = (
      runId: string | undefined,
      content: string,
    ) => {
      if (!runId) {
        return;
      }

      const message = extractVerbatimNotificationMessage(content);
      if (message === null) {
        return;
      }

      const now = Date.now();
      for (const [storedRunId, stored] of verbatimMessages) {
        if (stored.expiresAt <= now) {
          verbatimMessages.delete(storedRunId);
        }
      }
      while (verbatimMessages.size >= VERBATIM_MESSAGE_MAX_RUNS) {
        const oldestRunId = verbatimMessages.keys().next().value;
        if (typeof oldestRunId !== "string") {
          break;
        }
        verbatimMessages.delete(oldestRunId);
      }
      verbatimMessages.set(runId, {
        message,
        expiresAt: now + VERBATIM_MESSAGE_TTL_MS,
      });
    };

    api.on("message_received", (event, context) => {
      rememberVerbatimMessage(
        context.runId ?? event.runId,
        event.content,
      );
    });

    api.on("llm_input", (event, context) => {
      rememberVerbatimMessage(
        context.runId ?? event.runId,
        event.prompt,
      );
    });

    api.on("before_tool_call", (event, context) => {
      if (event.toolName !== sendNotificationTool.name) {
        return;
      }

      const messageSource = event.params.message_source ?? "verbatim";
      if (messageSource === "generated") {
        return;
      }
      if (messageSource !== "verbatim") {
        return {
          block: true,
          blockReason: "通知正文来源无效，已阻止发送。",
        };
      }

      const runId = context.runId ?? event.runId;
      const storedMessage = runId ? verbatimMessages.get(runId) : undefined;
      if (
        !storedMessage
        || storedMessage.expiresAt <= Date.now()
        || storedMessage.message.length === 0
      ) {
        if (runId) {
          verbatimMessages.delete(runId);
        }
        return {
          block: true,
          blockReason: "未取得当前请求中“消息内容：”后的原始正文，已阻止发送。请在当前消息中明确提供消息内容。",
        };
      }

      return {
        params: {
          ...event.params,
          message: storedMessage.message,
          message_source: "verbatim",
        },
      };
    });

    api.registerTool({
      name: getStatusTool.name,
      label: getStatusTool.label,
      description: getStatusTool.description,
      parameters: getStatusTool.parameters,
      execute: async (_toolCallId, params, signal) => jsonResult(
        await getStatusTool.execute(params as object, config, { signal }),
      ),
    }, { optional: true });

    api.registerTool({
      name: sendNotificationTool.name,
      label: sendNotificationTool.label,
      description: sendNotificationTool.description,
      parameters: sendNotificationTool.parameters,
      execute: async (_toolCallId, params, signal) => jsonResult(
        await sendNotificationTool.execute(
          params as SendNotificationParameters,
          config,
          { signal },
        ),
      ),
    }, { optional: true });
  },
});

export default plugin;
