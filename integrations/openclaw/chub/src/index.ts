import { Type } from "typebox";
import {
  buildJsonPluginConfigSchema,
  definePluginEntry,
} from "openclaw/plugin-sdk/core";

import type { ChubConfig } from "./client.js";
import {
  dispatchWeixinChubMessage,
} from "./client.js";

const WEIXIN_VOICE_TRANSCRIPT_MARKER = "[[chub-weixin-voice-transcript]]";
const WEIXIN_CHANNEL_FAILURE = "Chub 消息通道暂时不可用，请稍后重试。";
const WEIXIN_CONTENT_FAILURE =
  "未识别到可处理的文字或语音转写，请重新发送文字，或稍后重试语音。";

const configSchema = Type.Object({
  baseUrl: Type.Optional(Type.String({
    description: "Fixed local Chub base URL; must use 127.0.0.1.",
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
  description: "Forward approved Weixin direct messages to Chub through a fixed local connection.",
  configSchema: buildJsonPluginConfigSchema(
    configSchema as unknown as Record<string, unknown>,
  ),
  register(api) {
    const config = (api.pluginConfig ?? {}) as ChubConfig;

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
  },
});

export default plugin;
