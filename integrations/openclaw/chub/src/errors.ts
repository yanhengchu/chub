export type ChubToolFailure = {
  available: false;
  error: string;
  message: string;
};

const MESSAGES = {
  chub_configuration_invalid: "Chub 工具配置无效",
  chub_authentication_failed: "Chub 状态检查未通过本机访问校验",
  chub_status_unavailable: "Chub 暂时无法提供状态",
  chub_timeout: "Chub 状态检查超时",
  chub_cancelled: "Chub 状态检查已取消",
  chub_response_too_large: "Chub 状态响应超过限制",
  chub_response_invalid: "Chub 返回了无法识别的状态",
  chub_unreachable: "当前设备的 Chub 暂时无法访问",
  notification_target_not_found: "飞书通知目标未配置",
  notification_target_disabled: "飞书通知目标已停用",
  notification_recipient_not_found: "指定的飞书提醒对象未配置",
  mention_all_not_allowed: "该飞书通知目标不允许提醒所有人",
  notification_message_too_large: "飞书通知内容超过限制",
  notification_registry_unavailable: "Chub 通知配置不可用",
  notification_registry_invalid: "Chub 通知配置无效",
  notification_secret_unavailable: "飞书机器人凭证不可用",
  notification_secret_invalid: "飞书机器人凭证无效",
  notification_secret_permissions: "飞书机器人凭证权限不安全",
  notification_timeout: "飞书通知请求超时",
  notification_provider_unavailable: "飞书通知服务暂时不可用",
  notification_provider_invalid: "飞书通知服务返回了无效响应",
  notification_rejected: "飞书拒绝了通知请求",
  notification_request_conflict: "通知请求标识已用于其他内容",
  notifications_disabled: "Chub 通知功能已关闭",
} as const;

export type ChubErrorCode = keyof typeof MESSAGES;

export function chubFailure(error: ChubErrorCode): ChubToolFailure {
  return { available: false, error, message: MESSAGES[error] };
}
