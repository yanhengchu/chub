import { Type } from "typebox";

import {
  sendChubNotification,
  type ChubConfig,
  type NotificationRequest,
} from "../client.js";

type ToolContext = { signal?: AbortSignal };

const parameters = Type.Object({
  target: Type.String({
    minLength: 1,
    maxLength: 64,
    pattern: "^[a-z][a-z0-9_-]*$",
    description: "已配置的飞书通知目标 ID。",
  }),
  message: Type.String({
    minLength: 1,
    maxLength: 4000,
    description: "需要发送的纯文本消息。message_source=verbatim 时此值会被插件用当前用户原文覆盖。",
  }),
  message_source: Type.Optional(Type.Union([
    Type.Literal("verbatim"),
    Type.Literal("generated"),
  ], {
    default: "verbatim",
    description: "用户明确给出“消息内容：”时使用 verbatim；只有用户要求 AI 编写正文时才使用 generated。",
  })),
  mention_mode: Type.Union([
    Type.Literal("none"),
    Type.Literal("recipients"),
    Type.Literal("all"),
  ], {
    default: "none",
    description: "提醒模式；只有用户明确要求提醒所有人时才能使用 all。",
  }),
  recipients: Type.Optional(Type.Array(Type.String({
    minLength: 1,
    maxLength: 64,
    pattern: "^[a-z][a-z0-9_-]*$",
  }), {
    maxItems: 20,
    description: "mention_mode=recipients 时使用的已配置人员别名。",
  })),
}, { additionalProperties: false });

export type SendNotificationParameters = {
  target: string;
  message: string;
  message_source?: "verbatim" | "generated";
  mention_mode: NotificationRequest["mentionMode"];
  recipients?: string[];
};

export const sendNotificationTool = {
  name: "chub_send_notification",
  label: "Chub Notification",
  description: "向已配置的飞书群发送纯文本通知。用户给出“消息内容：”时必须使用 verbatim 原文模式；只有用户要求 AI 编写正文时才使用 generated。",
  parameters,
  optional: true,
  async execute(
    params: SendNotificationParameters,
    config: ChubConfig,
    context: ToolContext,
  ) {
    return sendChubNotification(config, {
      target: params.target,
      message: params.message,
      mentionMode: params.mention_mode,
      recipients: params.recipients ?? [],
    }, context.signal);
  },
};
