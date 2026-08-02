import { Type } from "typebox";
import {
  buildJsonPluginConfigSchema,
  definePluginEntry,
  jsonResult,
} from "openclaw/plugin-sdk/core";

import type { ChubConfig } from "./client.js";
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
});

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
