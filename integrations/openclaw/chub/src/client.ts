const MAX_RESPONSE_BYTES = 64 * 1024;

export type ChubConfig = {
  baseUrl?: string;
  timeoutMs?: number;
  weixinChubMode?: boolean;
};

export type WeixinChubModeDispatch = {
  available: true;
  protocolVersion: 3;
  disposition: "pass" | "reply" | "handled";
  message?: string;
};

export type WeixinChubModeDispatchFailure = {
  available: false;
  error: string;
  message: string;
};

type FetchLike = typeof fetch;

export function apiUrl(baseUrl: string, path: string): URL {
  const url = new URL(baseUrl);
  if (
    !["http:", "https:"].includes(url.protocol)
    || url.username
    || url.password
    || url.search
    || url.hash
    || (url.pathname !== "/" && url.pathname !== "")
    || url.hostname !== "127.0.0.1"
  ) {
    throw new Error("invalid_chub_base_url");
  }
  url.pathname = path;
  return url;
}

export function weixinChubModeDispatchUrl(baseUrl: string): URL {
  return apiUrl(baseUrl, "/api/openclaw/wechat-chub-mode/dispatch");
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

function weixinDispatchFailure(
  error: string,
): WeixinChubModeDispatchFailure {
  return {
    available: false,
    error,
    message: error === "chub_timeout"
      ? "Chub 响应超时，当前提交状态未知，请勿重复发送。"
      : "Chub 消息通道暂时不可用，请稍后重试。",
  };
}

export async function dispatchWeixinChubMessage(
  config: ChubConfig,
  request: {
    messageId: string;
    content: string;
    messageType: "text" | "voice";
    correlationId?: string;
    replyAccountId: string;
    replyRecipient: string;
  },
  signal?: AbortSignal,
  fetchImpl: FetchLike = fetch,
): Promise<WeixinChubModeDispatch | WeixinChubModeDispatchFailure> {
  let url: URL;
  try {
    if (!config.baseUrl) {
      throw new Error("invalid_chub_base_url");
    }
    url = weixinChubModeDispatchUrl(config.baseUrl);
  } catch (_error) {
    return weixinDispatchFailure("chub_configuration_invalid");
  }
  if (
    request.messageId.length === 0
    || request.messageId.length > 500
    || request.content.trim().length === 0
    || request.content.length > 8_000
    || !["text", "voice"].includes(request.messageType)
    || (request.correlationId?.length ?? 0) > 500
    || request.replyAccountId.trim().length === 0
    || request.replyAccountId.length > 200
    || request.replyRecipient.trim().length === 0
    || request.replyRecipient.length > 500
    || !request.replyRecipient.endsWith("@im.wechat")
  ) {
    return weixinDispatchFailure("weixin_chub_mode_request_invalid");
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
        protocol_version: 3,
        message_id: request.messageId,
        content: request.content,
        message_type: request.messageType,
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
        : "weixin_chub_mode_dispatch_failed";
      return weixinDispatchFailure(error);
    }
    if (!payload || typeof payload !== "object") {
      throw new Error("invalid_chub_response");
    }
    const body = payload as Record<string, unknown>;
    const data = body.data as Record<string, unknown> | undefined;
    if (
      body.success !== true
      || data?.protocol_version !== 3
      || !["pass", "reply", "handled"].includes(String(data?.disposition))
      || (data.disposition === "reply" && (
        typeof data.message !== "string"
        || data.message.length === 0
        || Array.from(data.message).length > 3_000
      ))
      || (["pass", "handled"].includes(String(data.disposition)) && !(
        data.message === null || data.message === undefined
      ))
    ) {
      throw new Error("invalid_chub_response");
    }
    return {
      available: true,
      protocolVersion: 3,
      disposition: data.disposition as "pass" | "reply" | "handled",
      message: typeof data.message === "string" ? data.message : undefined,
    };
  } catch (error) {
    if (error instanceof Error && error.name === "TimeoutError") {
      return weixinDispatchFailure("chub_timeout");
    }
    if (signal?.aborted) {
      return weixinDispatchFailure("chub_cancelled");
    }
    if (error instanceof Error && error.message === "chub_response_too_large") {
      return weixinDispatchFailure("chub_response_too_large");
    }
    if (error instanceof Error && error.message === "invalid_chub_response") {
      return weixinDispatchFailure("chub_response_invalid");
    }
    return weixinDispatchFailure("chub_unreachable");
  }
}
