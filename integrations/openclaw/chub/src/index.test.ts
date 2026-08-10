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

describe("Weixin Chub mode", () => {
  it("submits a direct Weixin message without Agent or LLM dispatch", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({
        success: true,
        data: {
          enabled: true,
          ready: true,
          code: "ready",
        },
      }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        success: true,
        data: {
          accepted: true,
          duplicate: false,
          new_session: true,
          code: "submitted",
          message: "任务已提交，已创建新的微信专用 Session；完成后将通过微信回送结果。",
        },
      }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const { hooks } = createPluginApi({
      baseUrl: "http://100.64.0.1:8080",
      wechatChubStatusMode: true,
    });

    const result = await hooks.get("before_dispatch")?.({
      channel: "openclaw-weixin",
      content: "检查状态",
      isGroup: false,
      timestamp: 1_700_000_000_000,
    }, {
      accountId: "weixin-account",
      conversationId: "owner@im.wechat",
      sessionKey: "weixin-session",
    });

    expect(result).toEqual({
      handled: true,
      text: [
        "任务已提交，已创建新的微信专用 Session；完成后将通过微信回送结果。",
        "本次消息未调用 OpenClaw Agent 或 LLM。",
      ].join("\n"),
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(new URL(fetchMock.mock.calls[0][0]).pathname).toBe(
      "/api/openclaw/wechat-chub-mode/status",
    );
    expect(new URL(fetchMock.mock.calls[1][0]).pathname).toBe(
      "/api/openclaw/wechat-chub-mode/submit",
    );
    expect(fetchMock.mock.calls[1][1]).toMatchObject({ method: "POST" });
    const submitted = JSON.parse(fetchMock.mock.calls[1][1].body);
    expect(submitted).toMatchObject({
      prompt: "检查状态",
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

  it("continues the normal Agent flow when Chub disables the mode", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      success: true,
      data: { enabled: false, ready: false, code: "disabled" },
    }), { status: 200 })));
    const { hooks } = createPluginApi({
      baseUrl: "http://100.64.0.1:8080",
      wechatChubStatusMode: true,
    });

    await expect(hooks.get("before_dispatch")?.({
      channel: "openclaw-weixin",
      isGroup: false,
    }, {})).resolves.toBeUndefined();
    vi.unstubAllGlobals();
  });

  it("handles every direct message from the single bound Weixin account", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({
        success: true,
        data: { enabled: true, ready: true, code: "ready" },
      }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        success: true,
        data: {
          accepted: true,
          duplicate: false,
          new_session: false,
          code: "submitted",
          message: "任务已提交；完成后将通过微信回送结果。",
        },
      }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const { hooks } = createPluginApi({
      baseUrl: "http://100.64.0.1:8080",
      wechatChubStatusMode: true,
    });

    await expect(hooks.get("before_dispatch")?.({
      channel: "openclaw-weixin",
      isGroup: false,
      senderId: "bound-account@im.wechat",
      content: "检查设备",
      timestamp: 1_700_000_000_001,
    }, { accountId: "weixin-account" })).resolves.toMatchObject({ handled: true });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    vi.unstubAllGlobals();
  });

  it.each([
    [
      { enabled: true, ready: false, code: "configuration_invalid" },
      "Chub 微信模式配置无效：工作区、权限、模型或微信回送配置不可用。本次消息未调用 OpenClaw Agent 或 LLM。",
    ],
    [
      { enabled: true, ready: false, code: "codex_unavailable" },
      "Chub 微信模式未就绪：Codex 运行依赖不可用。本次消息未调用 OpenClaw Agent 或 LLM。",
    ],
  ])("returns the specific Chub readiness failure", async (data, text) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      success: true,
      data,
    }), { status: 200 })));
    const { hooks } = createPluginApi({
      baseUrl: "http://100.64.0.1:8080",
      wechatChubStatusMode: true,
    });

    await expect(hooks.get("before_dispatch")?.({
      channel: "openclaw-weixin",
      isGroup: false,
    }, {})).resolves.toEqual({ handled: true, text });
    vi.unstubAllGlobals();
  });

  it("returns the Chub connection failure without falling back to an Agent", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("network unavailable")));
    const { hooks } = createPluginApi({
      baseUrl: "http://100.64.0.1:8080",
      wechatChubStatusMode: true,
    });

    await expect(hooks.get("before_dispatch")?.({
      channel: "openclaw-weixin",
      isGroup: false,
    }, {})).resolves.toEqual({
      handled: true,
      text: "Chub 微信模式检查失败：当前设备的 Chub 暂时无法访问。本次消息未调用 OpenClaw Agent 或 LLM。",
    });
    vi.unstubAllGlobals();
  });

  it("replays a duplicate submission without changing its task", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({
        success: true,
        data: { enabled: true, ready: true, code: "ready" },
      }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        success: true,
        data: {
          accepted: true,
          duplicate: true,
          new_session: false,
          code: "submitted",
          message: "任务已提交；完成后将通过微信回送结果。",
        },
      }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const { hooks } = createPluginApi({
      baseUrl: "http://100.64.0.1:8080",
      wechatChubStatusMode: true,
    });

    await expect(hooks.get("before_dispatch")?.({
      channel: "openclaw-weixin",
      content: "检查设备",
      timestamp: 1_700_000_000_002,
      isGroup: false,
      senderId: "owner@im.wechat",
    }, { accountId: "weixin-account" })).resolves.toEqual({
      handled: true,
      text: [
        "重复消息已确认，任务不会再次执行。",
        "任务已提交；完成后将通过微信回送结果。",
        "本次消息未调用 OpenClaw Agent 或 LLM。",
      ].join("\n"),
    });
    vi.unstubAllGlobals();
  });

  it("returns a bounded submit failure without falling back to an Agent", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({
        success: true,
        data: { enabled: true, ready: true, code: "ready" },
      }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        success: false,
        error: {
          code: "weixin_chub_mode_in_progress",
          message: "untrusted backend detail",
        },
      }), { status: 409 }));
    vi.stubGlobal("fetch", fetchMock);
    const { hooks } = createPluginApi({
      baseUrl: "http://100.64.0.1:8080",
      wechatChubStatusMode: true,
    });

    await expect(hooks.get("before_dispatch")?.({
      channel: "openclaw-weixin",
      content: "第二个任务",
      timestamp: 1_700_000_000_003,
      isGroup: false,
      senderId: "owner@im.wechat",
    }, { accountId: "weixin-account" })).resolves.toEqual({
      handled: true,
      text: "Chub 微信任务提交失败：微信专用任务正在执行，请等待完成。\n本次消息未调用 OpenClaw Agent 或 LLM。",
    });
    vi.unstubAllGlobals();
  });

  it("fails closed when the hook has no stable inbound timestamp", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      success: true,
      data: { enabled: true, ready: true, code: "ready" },
    }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const { hooks } = createPluginApi({
      baseUrl: "http://100.64.0.1:8080",
      wechatChubStatusMode: true,
    });

    await expect(hooks.get("before_dispatch")?.({
      channel: "openclaw-weixin",
      content: "检查设备",
      isGroup: false,
    }, {})).resolves.toEqual({
      handled: true,
      text: "Chub 微信任务提交失败：微信消息缺少稳定标识，请重新发送。\n本次消息未调用 OpenClaw Agent 或 LLM。",
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    vi.unstubAllGlobals();
  });

  it("fails closed when the inbound reply route is unavailable", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      success: true,
      data: { enabled: true, ready: true, code: "ready" },
    }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const { hooks } = createPluginApi({
      baseUrl: "http://100.64.0.1:8080",
      wechatChubStatusMode: true,
    });

    await expect(hooks.get("before_dispatch")?.({
      channel: "openclaw-weixin",
      content: "检查设备",
      timestamp: 1_700_000_000_004,
      isGroup: false,
      senderId: "owner@im.wechat",
    }, {})).resolves.toEqual({
      handled: true,
      text: "Chub 微信任务提交失败：无法确认本次消息的微信回送路由，请重新发送。\n本次消息未调用 OpenClaw Agent 或 LLM。",
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    vi.unstubAllGlobals();
  });

  it("does not intercept groups or disabled mode", async () => {
    const { hooks } = createPluginApi({ wechatChubStatusMode: true });

    await expect(hooks.get("before_dispatch")?.({
      channel: "openclaw-weixin",
      isGroup: true,
    }, {})).resolves.toBeUndefined();

    const disabled = createPluginApi();
    await expect(disabled.hooks.get("before_dispatch")?.({
      channel: "openclaw-weixin",
      isGroup: false,
    }, {})).resolves.toBeUndefined();
  });
});
