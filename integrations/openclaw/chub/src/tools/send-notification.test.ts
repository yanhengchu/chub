import { describe, expect, it, vi } from "vitest";

import { sendChubNotification } from "../client.js";


function response(body: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
    ...init,
  });
}

describe("sendChubNotification", () => {
  it("sends only structured notification fields to the fixed API", async () => {
    const fetchImpl = vi.fn(async () => response({
      success: true,
      data: {
        request_id: "request-id",
        target: "operations",
        provider: "feishu",
        status: "accepted",
        duplicate: false,
      },
    }));

    await expect(sendChubNotification(
      { baseUrl: "http://127.0.0.1:8000" },
      {
        target: "operations",
        message: "Service failed",
        mentionMode: "recipients",
        recipients: ["maintainer"],
      },
      undefined,
      fetchImpl,
    )).resolves.toEqual({
      accepted: true,
      target: "operations",
      provider: "feishu",
      status: "accepted",
      duplicate: false,
    });

    const [url, init] = fetchImpl.mock.calls[0];
    expect(url).toEqual(new URL("http://127.0.0.1:8000/api/notifications/send"));
    expect(init).toMatchObject({ method: "POST", redirect: "error" });
    const body = JSON.parse(String(init?.body));
    expect(body).toMatchObject({
      target: "operations",
      message: "Service failed",
      mention_mode: "recipients",
      recipients: ["maintainer"],
    });
    expect(body.request_id).toMatch(/^[0-9a-f-]{36}$/);
    expect(JSON.stringify(body)).not.toContain("webhook");
    expect(JSON.stringify(body)).not.toContain("open_id");
  });

  it("returns a safe configured error without exposing the API response", async () => {
    const fetchImpl = vi.fn(async () => response({
      success: false,
      error: {
        code: "mention_all_not_allowed",
        message: "internal detail",
      },
    }, { status: 422 }));

    await expect(sendChubNotification(
      { baseUrl: "http://127.0.0.1:8000" },
      {
        target: "operations",
        message: "Alert",
        mentionMode: "all",
        recipients: [],
      },
      undefined,
      fetchImpl,
    )).resolves.toEqual({
      available: false,
      error: "mention_all_not_allowed",
      message: "该飞书通知目标不允许提醒所有人",
    });
  });

  it("rejects invalid success responses", async () => {
    const fetchImpl = vi.fn(async () => response({
      success: true,
      data: { status: "delivered", secret: "must-not-leak" },
    }));

    await expect(sendChubNotification(
      { baseUrl: "http://127.0.0.1:8000" },
      {
        target: "operations",
        message: "Alert",
        mentionMode: "none",
        recipients: [],
      },
      undefined,
      fetchImpl,
    )).resolves.toMatchObject({
      error: "chub_response_invalid",
    });
  });
});
