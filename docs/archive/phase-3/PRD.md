# Chub 第三阶段产品目标

> 归档状态：第三阶段已闭环。当前架构与维护规则以 [Chub–OpenClaw 接入设计](../../CHUB_OPENCLAW_INTEGRATION_DESIGN.md)为准，本文冻结为阶段目标记录。

## 1. 阶段定位

第三阶段在 Chub 已有设备管理、Codex 和自动化能力之上增加受控消息入口。Chub 继续负责可信设备能力、安全校验和最终状态；OpenClaw 负责 Agent 编排、微信通道和必要的结果投递；飞书 Webhook 只承担单向通知。

微信设备能力固定经过：

```text
微信 ClawBot -> OpenClaw -> Chub -> OpenClaw -> 微信 ClawBot
```

当前存在两类互不混用的微信路径：

- 普通 OpenClaw 路径：由 Agent 使用受限 `chub_get_status` 和 `chub_send_notification` Tool。
- 微信 Chub 模式：私聊在模型调度前提交到 Chub 固定专用 Session，不调用 OpenClaw Agent 或 LLM，完成后按任务保存的原路路由回送。

## 2. 当前能力

- 管理本机 OpenClaw Gateway，并分别展示 Gateway、Channel、Owner 和 Tailscale 状态。
- 通过 Chub 页面完成微信 ClawBot 二维码绑定和必要的验证码提交。
- 通过固定 Tailnet 地址和严格 Schema 提供只读 Chub 状态 Tool。
- 通过预配置飞书 Webhook 发送单向通知，支持原文保护和受控提醒。
- 将微信私聊提交为 Chub 快速交互，并复用固定微信专用 Codex Session。
- 为页面快速交互发送固定微信完成通知，为微信任务保存并使用任务级原路回送路由。

## 3. 安全边界

- Chub 不直接暴露到公网，受保护接口只接受 Hub Token 或真实可信 Tailnet socket；微信 Chub 提交还要求同节点来源。
- Tool 和 API 使用固定地址、固定能力和有界参数，不接受任意 URL、文件路径或系统命令字段。
- 通道绑定、Owner 授权和 Chub 能力授权是独立状态，不能互相推断。
- 微信 Chub 模式的 `Full access` 是维护者批准的固定 Tailnet、单 Owner、单专用 Session 例外，不适用于其他 Agent、身份、入口或 Session。
- 页面默认通知路由和微信任务原路路由互不兜底；通知失败不改变任务最终状态。
- 操作 ID、目标节点、任务状态和最终结果可关联，日志不记录消息正文、Token、Webhook 或完整微信标识。
- 高风险能力若超出微信 Chub 模式已批准范围，仍需单独设计和明确确认。

## 4. 平台与兼容性

OpenClaw Gateway 支持 macOS LaunchAgent 和 Ubuntu systemd user service。核心功能、微信绑定及微信 Chub 模式已在两端完成真实验收。

微信通用出站依赖 Context Token 持久化兼容能力。微信插件升级、重装或加载目录变化后必须重新检查；不能仅按版本号判断兼容性。

## 5. 后续边界

以下能力按真实需求独立设计，不作为第三阶段遗留任务：

- 飞书指定人员提醒。
- 多 Owner、多 ClawBot 并行和跨节点微信任务提交。
- 新的低风险状态变更 Tool。
- 连续电脑交互、自动事件通知和多节点统一入口。

BRClient 关停后，Chub 基础 LLM 和 Bedrock 快速交互入口已移除；后续模型能力按独立需求重新设计。
