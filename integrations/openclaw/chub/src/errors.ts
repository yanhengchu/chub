export type ChubToolFailure = {
  available: false;
  error: string;
  message: string;
};

const MESSAGES = {
  chub_configuration_invalid: "Chub 工具配置无效",
  chub_authentication_failed: "Chub 状态检查未通过 Tailnet 认证",
  chub_status_unavailable: "Chub 暂时无法提供状态",
  chub_timeout: "Chub 状态检查超时",
  chub_cancelled: "Chub 状态检查已取消",
  chub_response_too_large: "Chub 状态响应超过限制",
  chub_response_invalid: "Chub 返回了无法识别的状态",
  chub_unreachable: "当前设备的 Chub 暂时无法访问",
} as const;

export type ChubErrorCode = keyof typeof MESSAGES;

export function chubFailure(error: ChubErrorCode): ChubToolFailure {
  return { available: false, error, message: MESSAGES[error] };
}
