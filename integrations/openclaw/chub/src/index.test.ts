import { describe, expect, it, vi } from "vitest";

import plugin from "./index.js";

type HookHandler = (event: any, context: any) => any;

function createPluginApi(pluginConfig: Record<string, unknown> = {}) {
  const hooks = new Map<string, HookHandler>();
  const runContext = new Map<string, unknown>();
  const tools: unknown[] = [];
  const api = {
    pluginConfig,
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

function dispatchResponse({
  disposition = "reply",
  message = "Submitted\n▶ S1 · 检查状态\nTask · 检查状态",
}: {
  disposition?: "pass" | "reply" | "handled";
  message?: string | null;
} = {}) {
  return new Response(JSON.stringify({
    success: true,
    data: {
      protocol_version: 3,
      disposition,
      message,
    },
  }), { status: 200 });
}

const directEvent = {
  channel: "openclaw-weixin",
  content: "检查状态",
  isGroup: false,
  timestamp: 1_700_000_000_000,
};

const directContext = {
  accountId: "weixin-account",
  conversationId: "owner@im.wechat",
  sessionKey: "weixin-session",
};

describe("Weixin Chub mode", () => {
  it("uses one versioned dispatch request without Agent or LLM routing", async () => {
    const fetchMock = vi.fn().mockResolvedValue(dispatchResponse());
    vi.stubGlobal("fetch", fetchMock);
    const { hooks } = createPluginApi({
      baseUrl: "http://100.64.0.1:8080",
      weixinChubMode: true,
    });

    const result = await hooks.get("before_dispatch")?.(
      directEvent,
      directContext,
    );

    expect(result).toEqual({
      handled: true,
      text: "Submitted\n▶ S1 · 检查状态\nTask · 检查状态",
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(new URL(fetchMock.mock.calls[0][0]).pathname).toBe(
      "/api/openclaw/wechat-chub-mode/dispatch",
    );
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ method: "POST" });
    const submitted = JSON.parse(fetchMock.mock.calls[0][1].body);
    expect(submitted).toMatchObject({
      protocol_version: 3,
      content: "检查状态",
      message_type: "text",
      reply_account_id: "weixin-account",
      reply_recipient: "owner@im.wechat",
    });
    expect(submitted.message_id).toMatch(/^openclaw-weixin:[0-9a-f]{64}$/);
    expect(submitted.correlation_id).toMatch(
      /^openclaw-session:[0-9a-f]{64}$/,
    );
    expect(JSON.stringify(submitted)).not.toContain("weixin-session");
    vi.unstubAllGlobals();
  });

  it("passes the message back to the normal OpenClaw flow when Chub decides", async () => {
    const fetchMock = vi.fn().mockResolvedValue(dispatchResponse({
      disposition: "pass",
      message: null,
    }));
    vi.stubGlobal("fetch", fetchMock);
    const { hooks } = createPluginApi({
      baseUrl: "http://100.64.0.1:8080",
      weixinChubMode: true,
    });

    await expect(hooks.get("before_dispatch")?.(
      directEvent,
      directContext,
    )).resolves.toBeUndefined();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    vi.unstubAllGlobals();
  });

  it("suppresses an extra reply after Chub has handled delivery", async () => {
    const fetchMock = vi.fn().mockResolvedValue(dispatchResponse({
      disposition: "handled",
      message: null,
    }));
    vi.stubGlobal("fetch", fetchMock);
    const { hooks } = createPluginApi({
      baseUrl: "http://100.64.0.1:8080",
      weixinChubMode: true,
    });

    await expect(hooks.get("before_dispatch")?.(
      directEvent,
      directContext,
    )).resolves.toEqual({ handled: true });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    vi.unstubAllGlobals();
  });

  it("returns Chub-owned bounded business failures", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(dispatchResponse({
      message: "任务提交失败：已有微信任务正在执行，请等待完成后重试。",
    })));
    const { hooks } = createPluginApi({
      baseUrl: "http://100.64.0.1:8080",
      weixinChubMode: true,
    });

    await expect(hooks.get("before_dispatch")?.(
      directEvent,
      directContext,
    )).resolves.toEqual({
      handled: true,
      text: "任务提交失败：已有微信任务正在执行，请等待完成后重试。",
    });
    vi.unstubAllGlobals();
  });

  it("fails closed on protocol mismatch without falling back to an Agent", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      success: false,
      error: { code: "weixin_chub_mode_protocol_mismatch", message: "detail" },
    }), { status: 409 })));
    const { hooks } = createPluginApi({
      baseUrl: "http://100.64.0.1:8080",
      weixinChubMode: true,
    });

    await expect(hooks.get("before_dispatch")?.(
      directEvent,
      directContext,
    )).resolves.toEqual({
      handled: true,
      text: "Chub 消息通道暂时不可用，请稍后重试。",
    });
    vi.unstubAllGlobals();
  });

  it("fails closed when Chub is unavailable", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("unavailable")));
    const { hooks } = createPluginApi({
      baseUrl: "http://100.64.0.1:8080",
      weixinChubMode: true,
    });

    await expect(hooks.get("before_dispatch")?.(
      directEvent,
      directContext,
    )).resolves.toEqual({
      handled: true,
      text: "Chub 消息通道暂时不可用，请稍后重试。",
    });
    vi.unstubAllGlobals();
  });

  it("reports an unknown submission state when Chub times out", async () => {
    const timeout = new Error("timed out");
    timeout.name = "TimeoutError";
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(timeout));
    const { hooks } = createPluginApi({
      baseUrl: "http://100.64.0.1:8080",
      weixinChubMode: true,
    });

    await expect(hooks.get("before_dispatch")?.(
      directEvent,
      directContext,
    )).resolves.toEqual({
      handled: true,
      text: "Chub 响应超时，当前提交状态未知，请勿重复发送。",
    });
    vi.unstubAllGlobals();
  });

  it("forwards trusted voice transcripts and honors Chub silent handling", async () => {
    const fetchMock = vi.fn().mockResolvedValue(dispatchResponse({
      disposition: "handled",
      message: null,
    }));
    vi.stubGlobal("fetch", fetchMock);
    const { hooks } = createPluginApi({
      baseUrl: "http://100.64.0.1:8080",
      weixinChubMode: true,
    });

    const result = await hooks.get("before_dispatch")?.({
      ...directEvent,
      content: "[[chub-weixin-voice-transcript]]",
      body: "检查语音识别是否准确",
    }, directContext);

    expect(result).toEqual({ handled: true });
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toMatchObject({
      content: "检查语音识别是否准确",
      message_type: "voice",
    });
    vi.unstubAllGlobals();
  });

  it("does not rewrite or truncate a Chub-owned reply", async () => {
    const chubReply = `Chub 原样回执：${"内容".repeat(1_400)}`;
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(dispatchResponse({
      message: chubReply,
    })));
    const { hooks } = createPluginApi({
      baseUrl: "http://100.64.0.1:8080",
      weixinChubMode: true,
    });

    const result = await hooks.get("before_dispatch")?.({
      ...directEvent,
      content: "[[chub-weixin-voice-transcript]]",
      body: "语音内容".repeat(1_000),
    }, directContext);

    expect(result.text).toBe(chubReply);
    vi.unstubAllGlobals();
  });

  it("does not echo typed markers or duplicate voice messages", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(dispatchResponse({ message: "任务已提交。" }))
      .mockResolvedValueOnce(dispatchResponse({
        message: "该消息已处理，任务不会重复执行。",
      }));
    vi.stubGlobal("fetch", fetchMock);
    const { hooks } = createPluginApi({
      baseUrl: "http://100.64.0.1:8080",
      weixinChubMode: true,
    });

    const typed = await hooks.get("before_dispatch")?.({
      ...directEvent,
      content: "[[chub-weixin-voice-transcript]]",
      body: "[[chub-weixin-voice-transcript]]",
    }, directContext);
    const duplicate = await hooks.get("before_dispatch")?.({
      ...directEvent,
      content: "[[chub-weixin-voice-transcript]]",
      body: "真实转写",
    }, directContext);

    expect(typed.text).toBe("任务已提交。");
    expect(duplicate.text).toBe("该消息已处理，任务不会重复执行。");
    expect(duplicate.text).not.toContain("语音识别内容");
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toMatchObject({
      content: "[[chub-weixin-voice-transcript]]",
      message_type: "text",
    });
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toMatchObject({
      content: "真实转写",
      message_type: "voice",
    });
    vi.unstubAllGlobals();
  });

  it("fails closed before dispatch without stable identity or reply route", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const { hooks } = createPluginApi({
      baseUrl: "http://100.64.0.1:8080",
      weixinChubMode: true,
    });

    await expect(hooks.get("before_dispatch")?.({
      ...directEvent,
      timestamp: undefined,
    }, directContext)).resolves.toEqual({
      handled: true,
      text: "Chub 消息通道暂时不可用，请稍后重试。",
    });
    await expect(hooks.get("before_dispatch")?.(
      directEvent,
      { sessionKey: "session" },
    )).resolves.toEqual({
      handled: true,
      text: "Chub 消息通道暂时不可用，请稍后重试。",
    });
    expect(fetchMock).not.toHaveBeenCalled();
    vi.unstubAllGlobals();
  });

  it("explains empty text or missing voice transcripts without calling Chub", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const { hooks } = createPluginApi({
      baseUrl: "http://100.64.0.1:8080",
      weixinChubMode: true,
    });

    await expect(hooks.get("before_dispatch")?.({
      ...directEvent,
      content: "",
    }, directContext)).resolves.toEqual({
      handled: true,
      text: "未识别到可处理的文字或语音转写，请重新发送文字，或稍后重试语音。",
    });
    expect(fetchMock).not.toHaveBeenCalled();
    vi.unstubAllGlobals();
  });

  it("does not intercept groups, other channels, or disabled plugin routing", async () => {
    const enabled = createPluginApi({ weixinChubMode: true });
    await expect(enabled.hooks.get("before_dispatch")?.({
      ...directEvent,
      isGroup: true,
    }, directContext)).resolves.toBeUndefined();
    await expect(enabled.hooks.get("before_dispatch")?.({
      ...directEvent,
      channel: "telegram",
    }, directContext)).resolves.toBeUndefined();

    const disabled = createPluginApi();
    await expect(disabled.hooks.get("before_dispatch")?.(
      directEvent,
      directContext,
    )).resolves.toBeUndefined();
  });
});
