# Chub 集成能力清单

> 状态：持续维护。本文是 Chub 当前插件、插件能力、固定 API 和消息路由的统一查询入口；标记为“已实现”“已接入”或“已验收”的条目可视为当前可用，“待实现”条目只表示已记录的设计。

本文只维护 Chub 的外部集成能力；项目整体功能与使用入口见 [README](../README.md)。

## 1. 插件列表

| 插件 | 状态 | 简介 |
| --- | --- | --- |
| `chub` | 已实现 | Chub 仓库维护的 OpenClaw 插件，提供 Chub Tool、微信消息转发和飞书原文保护 |
| `@tencent-weixin/openclaw-weixin` | 已接入 | 腾讯微信插件，提供 ClawBot 账号绑定、微信消息收发和语音转写 |

## 2. `chub` 插件功能

| 功能 | 状态 | 简介 |
| --- | --- | --- |
| `chub_get_status` | 已实现 | OpenClaw Tool，查询 Chub 节点健康和基础状态 |
| `chub_send_notification` | 已实现 | OpenClaw Tool，向 Chub 预配置的飞书目标发送消息 |
| `before_dispatch` | 已实现 | OpenClaw Hook，在 Agent 运行前把可信微信私聊转发到 Chub |
| `message_received` / `llm_input` / `before_tool_call`（通知原文保护） | 已实现 | 记录用户原文并按 `runId` 关联，避免通知正文被模型意外改写 |

## 3. `chub` 插件调用的 API

只记录插件实际调用的 Chub API，不展开项目其他接口。

| 请求 | 状态 | 简介 |
| --- | --- | --- |
| `GET /api/status` | 已实现 | 由 `chub_get_status` 调用，查询节点健康和基础状态 |
| `POST /api/notifications/send` | 已实现 | 由 `chub_send_notification` 调用，向预配置飞书目标发送消息 |
| `POST /api/openclaw/wechat-chub-mode/dispatch` | 已实现 | 由 `before_dispatch` 调用，把微信消息交给 Chub 统一分发 |

## 4. Chub 微信消息路由

OpenClaw 插件只转发消息，路由匹配和处理全部由 Chub 的统一 `dispatch` 接口负责。

### 4.1 路由列表

| 路由 | 匹配条件 | 状态 | 功能 |
| --- | --- | --- | --- |
| `mode_pass`（模式放行） | 微信 Chub 模式关闭 | 已实现 | 放行到原 OpenClaw Agent 流程 |
| `task_status_check`（任务状态检查） | 正文完全匹配固定状态检查短语 | 已验收 | 运行中回复状态；已结束未通知或通知失败时触发原路通知；无相关任务时回复空结果 |
| `task_submit`（普通任务提交） | 其他非空正文 | 已实现 | 提交到微信专用 Session，由 Codex 执行 |

路由 ID 是本文使用的统一标识。

当前已实现的路由顺序为：

```text
安全与幂等检查
  -> mode_pass
  -> task_status_check
  -> task_submit
```

`task_submit` 是当前默认兜底路由。

### 4.2 路由说明

`mode_pass`：微信 Chub 模式关闭时返回 `pass`，插件将消息交回原 OpenClaw Agent 流程。

`task_submit`：未匹配固定路由的非空消息提交到微信专用 Session。

`task_status_check`：正文去除首尾空白后，必须完全等于以下任一固定短语：

- `检查任务状态`
- `任务状态`
- `查询任务结果`
- `任务结果`

包含其他文字或参数时不匹配本路由，继续按普通任务处理。

| 任务情况 | 处理方式 |
| --- | --- |
| 正在执行 | 回复任务状态和摘要 |
| 已结束但待通知 | 触发原路结果通知 |
| 结果正在发送 | 回复正在发送 |
| 无相关任务 | 回复当前没有待检查任务 |

## 5. 相关文档

- 项目整体功能与使用入口：[README](../README.md)
- 集成架构与安全边界：[Chub–OpenClaw 接入设计](CHUB_OPENCLAW_INTEGRATION_DESIGN.md)
- 插件协议、构建与部署：[Chub OpenClaw 插件说明](../integrations/openclaw/chub/README.md)
