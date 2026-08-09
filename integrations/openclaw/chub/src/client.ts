import { chubFailure, type ChubToolFailure } from "./errors.js";

const MAX_RESPONSE_BYTES = 64 * 1024;

export type ChubConfig = {
  baseUrl?: string;
  timeoutMs?: number;
  wechatChubStatusMode?: boolean;
};

export type ChubStatus = {
  available: true;
  node: string;
  platform: string;
  chubVersion: string;
  cpuPercent?: number;
  memoryPercent?: number;
  diskPercent?: number;
  uptimeSeconds?: number;
  checkedAt: string;
};

export type WeixinChubModeStatus = {
  available: true;
  enabled: boolean;
  ready: boolean;
  code: "ready" | "disabled" | "configuration_invalid" | "codex_unavailable";
};

export type NotificationRequest = {
  target: string;
  message: string;
  mentionMode: "none" | "recipients" | "all";
  recipients: string[];
};

export type NotificationResult = {
  accepted: true;
  target: string;
  provider: "feishu";
  status: "accepted";
  duplicate: boolean;
};

type FetchLike = typeof fetch;

function isTailnetHost(hostname: string): boolean {
  if (hostname.includes(":")) {
    return hostname.toLowerCase().startsWith("fd7a:115c:a1e0:");
  }
  const octets = hostname.split(".").map((part) => Number(part));
  if (
    octets.length !== 4
    || octets.some((part) => !Number.isInteger(part) || part < 0 || part > 255)
  ) {
    return false;
  }
  return octets[0] === 100 && octets[1] >= 64 && octets[1] <= 127;
}

export function apiUrl(baseUrl: string, path: string): URL {
  const url = new URL(baseUrl);
  if (
    !["http:", "https:"].includes(url.protocol)
    || url.username
    || url.password
    || url.search
    || url.hash
    || (url.pathname !== "/" && url.pathname !== "")
    || !isTailnetHost(url.hostname)
  ) {
    throw new Error("invalid_chub_base_url");
  }
  url.pathname = path;
  return url;
}

export function statusUrl(baseUrl: string): URL {
  return apiUrl(baseUrl, "/api/status");
}

export function weixinChubModeStatusUrl(baseUrl: string): URL {
  return apiUrl(baseUrl, "/api/openclaw/wechat-chub-mode/status");
}

function finiteNumber(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

export async function sendChubNotification(
  config: ChubConfig,
  request: NotificationRequest,
  signal?: AbortSignal,
  fetchImpl: FetchLike = fetch,
): Promise<NotificationResult | ChubToolFailure> {
  let url: URL;
  try {
    if (!config.baseUrl) {
      throw new Error("invalid_chub_base_url");
    }
    url = apiUrl(config.baseUrl, "/api/notifications/send");
  } catch (_error) {
    return chubFailure("chub_configuration_invalid");
  }

  const timeoutSignal = AbortSignal.timeout(config.timeoutMs ?? 3_000);
  const requestSignal = signal
    ? AbortSignal.any([signal, timeoutSignal])
    : timeoutSignal;
  try {
    const response = await fetchImpl(url, {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        request_id: crypto.randomUUID(),
        target: request.target,
        message: request.message,
        mention_mode: request.mentionMode,
        recipients: request.recipients,
      }),
      redirect: "error",
      signal: requestSignal,
    });
    const declaredLength = Number(response.headers.get("content-length") || "0");
    if (declaredLength > MAX_RESPONSE_BYTES) {
      throw new Error("chub_response_too_large");
    }
    const bytes = await readBoundedBody(response);
    let payload: unknown;
    try {
      payload = JSON.parse(new TextDecoder().decode(bytes));
    } catch (_error) {
      throw new Error("invalid_chub_response");
    }
    if (!response.ok) {
      const error = payload && typeof payload === "object"
        ? (payload as { error?: { code?: unknown } }).error?.code
        : undefined;
      if (typeof error === "string" && error in NOTIFICATION_ERROR_CODES) {
        return chubFailure(error as keyof typeof NOTIFICATION_ERROR_CODES);
      }
      if (response.status === 401 || response.status === 403) {
        return chubFailure("chub_authentication_failed");
      }
      return chubFailure("notification_provider_unavailable");
    }
    if (!payload || typeof payload !== "object") {
      throw new Error("invalid_chub_response");
    }
    const body = payload as Record<string, unknown>;
    const data = body.data as Record<string, unknown> | undefined;
    if (
      body.success !== true
      || data?.provider !== "feishu"
      || data?.status !== "accepted"
      || typeof data?.target !== "string"
      || typeof data?.duplicate !== "boolean"
    ) {
      throw new Error("invalid_chub_response");
    }
    return {
      accepted: true,
      target: data.target,
      provider: "feishu",
      status: "accepted",
      duplicate: data.duplicate,
    };
  } catch (error) {
    if (error instanceof Error && error.name === "TimeoutError") {
      return chubFailure("notification_timeout");
    }
    if (signal?.aborted) {
      return chubFailure("chub_cancelled");
    }
    if (error instanceof Error && error.message === "chub_response_too_large") {
      return chubFailure("chub_response_too_large");
    }
    if (error instanceof Error && error.message === "invalid_chub_response") {
      return chubFailure("chub_response_invalid");
    }
    return chubFailure("chub_unreachable");
  }
}

const NOTIFICATION_ERROR_CODES = {
  notification_target_not_found: true,
  notification_target_disabled: true,
  notification_recipient_not_found: true,
  mention_all_not_allowed: true,
  notification_message_too_large: true,
  notification_registry_unavailable: true,
  notification_registry_invalid: true,
  notification_secret_unavailable: true,
  notification_secret_invalid: true,
  notification_secret_permissions: true,
  notification_timeout: true,
  notification_provider_unavailable: true,
  notification_provider_invalid: true,
  notification_rejected: true,
  notification_request_conflict: true,
  notifications_disabled: true,
} as const;

function nonNegativeInteger(value: unknown): number | undefined {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0
    ? value
    : undefined;
}

function parseStatus(payload: unknown): ChubStatus {
  if (!payload || typeof payload !== "object") {
    throw new Error("invalid_chub_response");
  }
  const body = payload as Record<string, unknown>;
  const data = body.data as Record<string, unknown> | undefined;
  const node = data?.node as Record<string, unknown> | undefined;
  const system = data?.system as Record<string, unknown> | undefined;
  const hub = data?.hub as Record<string, unknown> | undefined;
  if (
    body.success !== true
    || typeof node?.name !== "string"
    || typeof system?.operating_system !== "string"
    || typeof hub?.version !== "string"
    || typeof hub?.current_time !== "string"
  ) {
    throw new Error("invalid_chub_response");
  }
  return {
    available: true,
    node: node.name,
    platform: system.operating_system,
    chubVersion: hub.version,
    cpuPercent: finiteNumber(system.cpu_percent),
    memoryPercent: finiteNumber(system.memory_percent),
    diskPercent: finiteNumber(system.disk_percent),
    uptimeSeconds: nonNegativeInteger(system.uptime_seconds),
    checkedAt: hub.current_time,
  };
}

function parseWeixinChubModeStatus(payload: unknown): WeixinChubModeStatus {
  if (!payload || typeof payload !== "object") {
    throw new Error("invalid_chub_response");
  }
  const body = payload as Record<string, unknown>;
  const data = body.data as Record<string, unknown> | undefined;
  const code = data?.code;
  if (
    body.success !== true
    || typeof data?.enabled !== "boolean"
    || typeof data?.ready !== "boolean"
    || ![
      "ready",
      "disabled",
      "configuration_invalid",
      "codex_unavailable",
    ].includes(String(code))
  ) {
    throw new Error("invalid_chub_response");
  }
  const status = {
    available: true as const,
    enabled: data.enabled,
    ready: data.ready,
    code: code as WeixinChubModeStatus["code"],
  };
  if (
    (status.code === "ready" && (!status.enabled || !status.ready))
    || (status.code === "disabled" && (status.enabled || status.ready))
    || (["configuration_invalid", "codex_unavailable"].includes(status.code)
      && (!status.enabled || status.ready))
  ) {
    throw new Error("invalid_chub_response");
  }
  return status;
}

async function readBoundedBody(response: Response): Promise<Uint8Array> {
  if (!response.body) {
    return new Uint8Array();
  }
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) {
        break;
      }
      total += value.byteLength;
      if (total > MAX_RESPONSE_BYTES) {
        await reader.cancel();
        throw new Error("chub_response_too_large");
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }
  const bytes = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return bytes;
}

export async function fetchChubStatus(
  config: ChubConfig,
  signal?: AbortSignal,
  fetchImpl: FetchLike = fetch,
): Promise<ChubStatus | ChubToolFailure> {
  let url: URL;
  try {
    if (!config.baseUrl) {
      throw new Error("invalid_chub_base_url");
    }
    url = statusUrl(config.baseUrl);
  } catch (_error) {
    return chubFailure("chub_configuration_invalid");
  }

  const timeoutSignal = AbortSignal.timeout(config.timeoutMs ?? 3_000);
  const requestSignal = signal
    ? AbortSignal.any([signal, timeoutSignal])
    : timeoutSignal;
  try {
    const response = await fetchImpl(url, {
      method: "GET",
      headers: { Accept: "application/json" },
      redirect: "error",
      signal: requestSignal,
    });
    if (response.status === 401 || response.status === 403) {
      return chubFailure("chub_authentication_failed");
    }
    if (!response.ok) {
      return chubFailure("chub_status_unavailable");
    }
    const declaredLength = Number(response.headers.get("content-length") || "0");
    if (declaredLength > MAX_RESPONSE_BYTES) {
      throw new Error("chub_response_too_large");
    }
    const bytes = await readBoundedBody(response);
    return parseStatus(JSON.parse(new TextDecoder().decode(bytes)));
  } catch (error) {
    if (error instanceof Error && error.name === "TimeoutError") {
      return chubFailure("chub_timeout");
    }
    if (signal?.aborted) {
      return chubFailure("chub_cancelled");
    }
    if (error instanceof Error && error.message === "chub_response_too_large") {
      return chubFailure("chub_response_too_large");
    }
    if (error instanceof SyntaxError || (error instanceof Error && error.message === "invalid_chub_response")) {
      return chubFailure("chub_response_invalid");
    }
    return chubFailure("chub_unreachable");
  }
}

export async function fetchWeixinChubModeStatus(
  config: ChubConfig,
  signal?: AbortSignal,
  fetchImpl: FetchLike = fetch,
): Promise<WeixinChubModeStatus | ChubToolFailure> {
  let url: URL;
  try {
    if (!config.baseUrl) {
      throw new Error("invalid_chub_base_url");
    }
    url = weixinChubModeStatusUrl(config.baseUrl);
  } catch (_error) {
    return chubFailure("chub_configuration_invalid");
  }

  const timeoutSignal = AbortSignal.timeout(config.timeoutMs ?? 3_000);
  const requestSignal = signal
    ? AbortSignal.any([signal, timeoutSignal])
    : timeoutSignal;
  try {
    const response = await fetchImpl(url, {
      method: "GET",
      headers: { Accept: "application/json" },
      redirect: "error",
      signal: requestSignal,
    });
    if (response.status === 401 || response.status === 403) {
      return chubFailure("chub_authentication_failed");
    }
    if (!response.ok) {
      return chubFailure("chub_status_unavailable");
    }
    const declaredLength = Number(response.headers.get("content-length") || "0");
    if (declaredLength > MAX_RESPONSE_BYTES) {
      throw new Error("chub_response_too_large");
    }
    const bytes = await readBoundedBody(response);
    return parseWeixinChubModeStatus(
      JSON.parse(new TextDecoder().decode(bytes)),
    );
  } catch (error) {
    if (error instanceof Error && error.name === "TimeoutError") {
      return chubFailure("chub_timeout");
    }
    if (signal?.aborted) {
      return chubFailure("chub_cancelled");
    }
    if (error instanceof Error && error.message === "chub_response_too_large") {
      return chubFailure("chub_response_too_large");
    }
    if (error instanceof SyntaxError || (error instanceof Error && error.message === "invalid_chub_response")) {
      return chubFailure("chub_response_invalid");
    }
    return chubFailure("chub_unreachable");
  }
}
