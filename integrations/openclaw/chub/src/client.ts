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

export type WeixinChubModeSubmission = {
  available: true;
  accepted: true;
  duplicate: boolean;
  newSession: boolean;
  code: "submitted";
  message: string;
};

export type WeixinChubModeSubmissionFailure = {
  available: false;
  error: string;
  message: string;
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

export function weixinChubModeSubmitUrl(baseUrl: string): URL {
  return apiUrl(baseUrl, "/api/openclaw/wechat-chub-mode/submit");
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

const WEIXIN_SUBMISSION_ERRORS: Record<string, string> = {
  weixin_chub_mode_in_progress: "任务提交失败：已有微信任务正在执行，请等待完成后重试。",
  weixin_chub_mode_mode_disabled: "任务提交失败：微信 Chub 模式已关闭。",
  weixin_chub_mode_configuration_invalid: "任务提交失败：微信 Chub 模式配置无效。",
  weixin_chub_mode_codex_unavailable: "任务提交失败：Codex 当前不可用，请稍后重试。",
  weixin_chub_mode_delivery_route_invalid: "任务提交失败：无法确认本次消息的微信回送通道，请稍后重试。",
  weixin_chub_mode_message_conflict: "任务提交失败：该消息的回送通道与首次提交不一致。",
  weixin_chub_mode_submission_failed: "任务提交失败，请稍后重试。",
  weixin_chub_mode_submission_interrupted: "上次提交被 Chub 重启中断，请重新发送任务。",
  weixin_chub_mode_state_unavailable: "任务提交失败：Chub 当前状态不可用，请稍后重试。",
  weixin_chub_mode_source_required: "任务提交失败：OpenClaw 与 Chub 的连接校验未通过。",
};

function weixinSubmissionFailure(
  error: string,
  message: string,
): WeixinChubModeSubmissionFailure {
  return { available: false, error, message };
}

export async function submitWeixinChubModeTask(
  config: ChubConfig,
  request: {
    messageId: string;
    prompt: string;
    correlationId?: string;
    replyAccountId: string;
    replyRecipient: string;
  },
  signal?: AbortSignal,
  fetchImpl: FetchLike = fetch,
): Promise<WeixinChubModeSubmission | WeixinChubModeSubmissionFailure> {
  let url: URL;
  try {
    if (!config.baseUrl) {
      throw new Error("invalid_chub_base_url");
    }
    url = weixinChubModeSubmitUrl(config.baseUrl);
  } catch (_error) {
    return weixinSubmissionFailure(
      "chub_configuration_invalid",
      "任务提交失败：微信 Chub 模式连接配置无效。",
    );
  }
  if (
    request.messageId.length === 0
    || request.messageId.length > 500
    || request.prompt.trim().length === 0
    || request.prompt.length > 8_000
    || (request.correlationId?.length ?? 0) > 500
    || request.replyAccountId.trim().length === 0
    || request.replyAccountId.length > 200
    || request.replyRecipient.trim().length === 0
    || request.replyRecipient.length > 500
    || !request.replyRecipient.endsWith("@im.wechat")
  ) {
    return weixinSubmissionFailure(
      "weixin_chub_mode_request_invalid",
      "任务提交失败：消息内容为空或超过长度限制。",
    );
  }

  const timeoutSignal = AbortSignal.timeout(
    Math.max(config.timeoutMs ?? 3_000, 10_000),
  );
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
        message_id: request.messageId,
        prompt: request.prompt,
        correlation_id: request.correlationId,
        reply_account_id: request.replyAccountId,
        reply_recipient: request.replyRecipient,
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
      const body = payload && typeof payload === "object"
        ? payload as { error?: { code?: unknown } }
        : undefined;
      const error = typeof body?.error?.code === "string"
        ? body.error.code
        : "weixin_chub_mode_submission_failed";
      const message = WEIXIN_SUBMISSION_ERRORS[error]
        ?? (response.status === 401 || response.status === 403
          ? "任务提交失败：OpenClaw 未通过 Chub 微信任务入口认证。"
          : "任务提交失败，请稍后重试。");
      return weixinSubmissionFailure(error, message);
    }
    if (!payload || typeof payload !== "object") {
      throw new Error("invalid_chub_response");
    }
    const body = payload as Record<string, unknown>;
    const data = body.data as Record<string, unknown> | undefined;
    if (
      body.success !== true
      || data?.accepted !== true
      || typeof data?.duplicate !== "boolean"
      || typeof data?.new_session !== "boolean"
      || data?.code !== "submitted"
      || typeof data?.message !== "string"
      || data.message.length === 0
      || data.message.length > 500
    ) {
      throw new Error("invalid_chub_response");
    }
    return {
      available: true,
      accepted: true,
      duplicate: data.duplicate,
      newSession: data.new_session,
      code: "submitted",
      message: data.message,
    };
  } catch (error) {
    if (error instanceof Error && error.name === "TimeoutError") {
      return weixinSubmissionFailure(
        "chub_timeout",
        "任务提交确认超时，任务可能已经启动。\n请等待完成通知，不要立即重复发送。",
      );
    }
    if (signal?.aborted) {
      return weixinSubmissionFailure("chub_cancelled", "任务提交已取消。请重新发送任务。");
    }
    if (error instanceof Error && error.message === "chub_response_too_large") {
      return weixinSubmissionFailure(
        "chub_response_too_large",
        "任务提交失败：Chub 返回的任务状态超过限制。",
      );
    }
    if (error instanceof Error && error.message === "invalid_chub_response") {
      return weixinSubmissionFailure(
        "chub_response_invalid",
        "任务提交失败：Chub 返回了无法识别的任务状态。",
      );
    }
    return weixinSubmissionFailure(
      "chub_unreachable",
      "任务提交失败：当前设备的 Chub 暂时无法访问。",
    );
  }
}
