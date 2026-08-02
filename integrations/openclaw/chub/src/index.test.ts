import { describe, expect, it, vi } from "vitest";

import plugin from "./index.js";

type HookHandler = (event: any, context: any) => any;

function createPluginApi() {
  const hooks = new Map<string, HookHandler>();
  const runContext = new Map<string, unknown>();
  const tools: unknown[] = [];
  const api = {
    pluginConfig: {},
    on: vi.fn((name: string, handler: HookHandler) => hooks.set(name, handler)),
    registerTool: vi.fn((tool: unknown) => tools.push(tool)),
    runContext: {
      setRunContext: vi.fn(({ runId, namespace, value }) => {
        runContext.set(`${runId}:${namespace}`, value);
        return true;
      }),
      getRunContext: vi.fn(({ runId, namespace }) =>
        runContext.get(`${runId}:${namespace}`)),
    },
  };
  plugin.register(api as never);
  return { hooks, tools };
}

describe("notification verbatim hooks", () => {
  it("replaces corrupted model arguments with the exact inbound message", () => {
    const { hooks } = createPluginApi();
    hooks.get("message_received")?.({
      content: [
        "调用 chub_send_notification 向 test 飞书群发送普通消息。",
        "消息内容：微信飞书通知链路验收通过。",
      ].join("\n"),
      runId: "run-1",
    }, { runId: "run-1" });

    const result = hooks.get("before_tool_call")?.({
      toolName: "chub_send_notification",
      runId: "run-1",
      params: {
        target: "test",
        message: "微信顺书通知链路验收通过",
        message_source: "verbatim",
        mention_mode: "none",
      },
    }, { runId: "run-1" });

    expect(result).toEqual({
      params: {
        target: "test",
        message: "微信飞书通知链路验收通过。",
        message_source: "verbatim",
        mention_mode: "none",
      },
    });
  });

  it("captures exact text from local TUI and agent model input", () => {
    const { hooks } = createPluginApi();
    hooks.get("llm_input")?.({
      prompt: [
        "向 test 发送普通消息。",
        "消息内容：微信飞书通知原文保护验收通过。",
      ].join("\n"),
      runId: "run-local",
    }, { runId: "run-local" });

    const result = hooks.get("before_tool_call")?.({
      toolName: "chub_send_notification",
      runId: "run-local",
      params: {
        target: "test",
        message: "微信顼書通知原文保护验收通过．",
        mention_mode: "none",
      },
    }, { runId: "run-local" });

    expect(result.params.message).toBe("微信飞书通知原文保护验收通过。");
  });

  it("blocks verbatim delivery when the original message is unavailable", () => {
    const { hooks } = createPluginApi();
    const result = hooks.get("before_tool_call")?.({
      toolName: "chub_send_notification",
      runId: "run-2",
      params: {
        target: "test",
        message: "model-generated fallback",
        mention_mode: "none",
      },
    }, { runId: "run-2" });

    expect(result).toMatchObject({ block: true });
  });

  it("allows explicitly generated notification content", () => {
    const { hooks } = createPluginApi();
    const result = hooks.get("before_tool_call")?.({
      toolName: "chub_send_notification",
      runId: "run-3",
      params: {
        target: "test",
        message: "AI 生成的摘要",
        message_source: "generated",
        mention_mode: "none",
      },
    }, { runId: "run-3" });

    expect(result).toBeUndefined();
  });
});
