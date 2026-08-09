import { Type } from "typebox";
import {
  buildJsonPluginConfigSchema,
  definePluginEntry,
  jsonResult,
} from "openclaw/plugin-sdk/core";

import type { ChubConfig } from "./client.js";
import { fetchWeixinChubModeStatus } from "./client.js";
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

const configSchema = Type.Object({
  baseUrl: Type.Optional(Type.String({
    description: "Fixed Chub base URL on the local Tailnet.",
  })),
  timeoutMs: Type.Optional(Type.Integer({
    minimum: 500,
    maximum: 10_000,
    default: 3_000,
  })),
  wechatChubStatusMode: Type.Optional(Type.Boolean({
    default: false,
    description: "Test only: reply to Weixin direct messages with fixed Chub status without running an agent or LLM.",
  })),
});

function wechatStatusReply(
  status: Awaited<ReturnType<typeof fetchWeixinChubModeStatus>>,
): string {
  if (!status.available) {
    return `Chub 微信模式检查失败：${status.message}。本次消息未调用 OpenClaw Agent 或 LLM。`;
  }
  if (!status.ready) {
    if (status.code === "configuration_invalid") {
      return "Chub 微信模式配置无效：固定工作区不可用。本次消息未调用 OpenClaw Agent 或 LLM。";
    }
    if (status.code === "codex_unavailable") {
      return "Chub 微信模式未就绪：Codex 运行依赖不可用。本次消息未调用 OpenClaw Agent 或 LLM。";
    }
    return "Chub 微信模式当前未就绪。本次消息未调用 OpenClaw Agent 或 LLM。";
  }
  return [
    "Chub 微信模式状态检查通过",
    "Chub 状态路由可用；当前阶段不会提交微信任务。",
    "本次消息未调用 OpenClaw Agent 或 LLM。",
  ].join("\n");
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

    api.on("before_dispatch", async (event) => {
      if (
        config.wechatChubStatusMode !== true
        || event.channel !== "openclaw-weixin"
        || event.isGroup === true
      ) {
        return;
      }

      const status = await fetchWeixinChubModeStatus(config);
      if (status.available && !status.enabled) {
        return;
      }
      return {
        handled: true,
        text: wechatStatusReply(status),
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
