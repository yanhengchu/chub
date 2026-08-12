# Chub 集成能力清单

> 状态：持续维护。本文是 Chub 当前插件、插件能力、固定 API 和消息路由的统一查询入口；正文只登记当前已经实现、接入或验收的能力。

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

- 固定路由直接返回结果，不创建 Codex 任务：固定状态短语进入任务状态检查，`codex` 进入 Codex 状态查询，`codex switch` 进入当前绑定切换。
- 其他非空消息进入普通任务提交，作为默认兜底路由。

### 4.1 路由列表

| 路由 | 匹配条件 | 状态 | 功能 |
| --- | --- | --- | --- |
| `mode_pass`（模式放行） | 微信 Chub 模式关闭 | 已实现 | 放行到原 OpenClaw Agent 流程 |
| `task_status_check`（任务状态检查） | 正文完全匹配固定状态检查短语 | 已验收 | 汇总同一微信路由的执行中任务；已结束未通知或通知失败时触发原路通知 |
| `codex_status`（Codex 状态） | 正文去除首尾空白后完全等于 `codex` | 已验收 | 回复额度、每日 Token 用量，以及符合微信 Chub 调度配置的 Session |
| `codex_switch`（绑定切换） | `codex switch` 或 `codex switch n` | 已验收 | 实时选择下一个或指定编号的可绑定 Session，只影响后续普通任务 |
| `task_submit`（普通任务提交） | 其他非空正文 | 已实现 | 提交到微信通道当前绑定 Session，由 Codex 执行 |

路由 ID 是本文使用的统一标识。

当前已实现的路由顺序为：

```text
安全与幂等检查
  -> mode_pass
  -> task_status_check
  -> codex_status
  -> codex_switch
  -> task_submit
```

`task_submit` 是当前默认兜底路由。

### 4.2 路由说明

`mode_pass`：微信 Chub 模式关闭时返回 `pass`，插件将消息交回原 OpenClaw Agent 流程。

`task_status_check`：正文去除首尾空白后，必须完全等于以下任一固定短语：

- `检查任务状态`
- `任务状态`
- `查询任务结果`
- `任务结果`

包含其他文字或参数时不匹配本路由，继续按普通任务处理。

| 任务情况 | 处理方式 |
| --- | --- |
| 一个或多个任务正在执行 | 汇总回复任务数量和摘要，最多展示 10 项 |
| 已结束但待通知 | 触发原路结果通知 |
| 执行中且另有通知失败任务 | 回复执行中任务，并同时触发旧结果原路补发 |
| 结果正在发送 | 回复正在发送 |
| 无相关任务 | 回复当前没有待检查任务 |

`codex_status`：调用 Codex App Server 的只读账户接口获取 7 天额度窗口和每日 Token 统计，并换行列出符合微信 Chub 当前工作区、权限、模型及推理等级配置且允许绑定的 Session，不创建 Codex 任务。列表按内部 Session ID 稳定排序，最多展示前 10 项；当前绑定项只增加 `[Current]`，不改变顺序。`Available` 可立即接收任务，`Busy` 可切换但暂不能接收新任务，`Unavailable` 不展示。标题使用有界脱敏摘要，不返回 Session ID、路径、权限、模型或错误正文。账户与 Session 状态并行、有界读取，单项失败或超时保留另一部分结果。

示例：

```text
Codex Usage: Weekly 35% left · Daily tokens 81.8M (08-11)
Active sessions:
1. 微信 Chub [Current] · Available
2. 项目维护 · Busy
```

`codex_switch`：每次实时读取与 `codex_status` 相同的前 10 项列表，不保存快照。

- `codex switch`：切换到 `[Current]` 的下一项，末尾循环到第一项；当前绑定不在列表时从第一项开始。
- `codex switch n`：切换到实时列表中的指定正整数编号。
- `Busy` 允许切换；提交新任务时仍由单 Session、单 writer 规则拒绝。
- 切换只影响后续普通任务，不迁移或停止原 Session 的任务；目标复检或状态写入失败时保持原绑定。
- 切换流程在统一 9 秒预算内并行读取 Session 与用量；成功后按 `codex_status` 格式返回结果，不生成单独的成功文案，以列表中的 `[Current]` 确认绑定。
- 零、负数、非数字、越界或多余参数返回用法或最新列表，不进入普通任务。
- 重复微信消息复用首次切换结果，不再次切换。

`task_submit`：未匹配固定路由的非空消息提交到微信通道当前绑定 Session。绑定不存在或配置不匹配时创建并绑定新 Session；暂时忙碌或不可提交不会触发新建。

## 5. 相关文档

- 项目整体功能与使用入口：[README](../README.md)
- 集成架构与安全边界：[Chub–OpenClaw 接入设计](CHUB_OPENCLAW_INTEGRATION_DESIGN.md)
- 插件协议、构建与部署：[Chub OpenClaw 插件说明](../integrations/openclaw/chub/README.md)
