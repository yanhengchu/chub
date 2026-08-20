import { describe, expect, it, vi } from "vitest";

import { fetchChubStatus, statusUrl } from "../client.js";

const payload = {
  success: true,
  data: {
    node: { name: "MacBook" },
    system: {
      operating_system: "Darwin",
      cpu_percent: 12.5,
      memory_percent: 63.2,
      disk_percent: 48.1,
      uptime_seconds: 86400,
    },
    hub: {
      version: "0.1.0",
      current_time: "2026-07-30T10:00:00Z",
    },
  },
};

function response(body: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
    ...init,
  });
}

describe("statusUrl", () => {
  it("fixes the API path for the local address", () => {
    expect(statusUrl("http://127.0.0.1:8000").toString()).toBe(
      "http://127.0.0.1:8000/api/status",
    );
  });

  it.each([
    "https://example.com",
    "http://100.64.1.2:8000/other",
    "http://user:secret@100.64.1.2:8000",
  ])("rejects a non-fixed or non-local target: %s", (url) => {
    expect(() => statusUrl(url)).toThrow("invalid_chub_base_url");
  });
});

describe("fetchChubStatus", () => {
  it("fails closed when the fixed base URL is missing", async () => {
    await expect(fetchChubStatus({})).resolves.toEqual({
      available: false,
      error: "chub_configuration_invalid",
      message: "Chub 工具配置无效",
    });
  });

  it("returns only the normalized status fields", async () => {
    const fetchImpl = vi.fn(async () => response(payload));
    await expect(fetchChubStatus(
      { baseUrl: "http://127.0.0.1:8000" },
      undefined,
      fetchImpl,
    )).resolves.toEqual({
      available: true,
      node: "MacBook",
      platform: "Darwin",
      chubVersion: "0.1.0",
      cpuPercent: 12.5,
      memoryPercent: 63.2,
      diskPercent: 48.1,
      uptimeSeconds: 86400,
      checkedAt: "2026-07-30T10:00:00Z",
    });
    expect(fetchImpl).toHaveBeenCalledWith(
      new URL("http://127.0.0.1:8000/api/status"),
      expect.objectContaining({ method: "GET", redirect: "error" }),
    );
  });

  it("maps authentication failures without exposing the response", async () => {
    const fetchImpl = vi.fn(async () => response(
      { secret: "must-not-leak" },
      { status: 401 },
    ));
    await expect(fetchChubStatus(
      { baseUrl: "http://127.0.0.1:8000" },
      undefined,
      fetchImpl,
    )).resolves.toEqual({
      available: false,
      error: "chub_authentication_failed",
      message: "Chub 状态检查未通过本机访问校验",
    });
  });

  it("rejects invalid and oversized responses", async () => {
    const invalid = vi.fn(async () => response({ success: true, data: {} }));
    const oversized = vi.fn(async () => new Response("x", {
      status: 200,
      headers: { "Content-Length": "65537" },
    }));
    await expect(fetchChubStatus(
      { baseUrl: "http://127.0.0.1:8000" },
      undefined,
      invalid,
    )).resolves.toMatchObject({ error: "chub_response_invalid" });
    await expect(fetchChubStatus(
      { baseUrl: "http://127.0.0.1:8000" },
      undefined,
      oversized,
    )).resolves.toMatchObject({ error: "chub_response_too_large" });
  });

  it("stops reading an oversized response without a content length", async () => {
    const oversized = vi.fn(async () => new Response("x".repeat(65_537), {
      status: 200,
    }));
    await expect(fetchChubStatus(
      { baseUrl: "http://127.0.0.1:8000" },
      undefined,
      oversized,
    )).resolves.toMatchObject({ error: "chub_response_too_large" });
  });
});
