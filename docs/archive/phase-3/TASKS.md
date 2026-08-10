# Chub 第三阶段高层计划

> 归档状态：第三阶段已闭环，macOS、Ubuntu 和真实微信核心链路均已验收通过。本文冻结为阶段任务记录。

## 1. 已完成范围

- [x] 完成 OpenClaw Gateway 在 macOS 和 Ubuntu 的安装、初始化、后台服务及状态管理。
- [x] 在 Chub 首页提供 OpenClaw 状态、固定启停操作和受控微信绑定流程。
- [x] 完成单一微信 Owner 的绑定、授权、Gateway 重启恢复和真实消息收发。
- [x] 提供固定、只读的 `chub_get_status` Tool，并完成超时、认证和有界响应处理。
- [x] 提供飞书固定目标通知、原文保护、短期幂等和受控提醒能力。
- [x] 提供微信 Chub 模式，将私聊提交到固定专用 Codex Session，不调用 OpenClaw Agent 或 LLM。
- [x] 完成同节点 Tailnet 认证、任务幂等、单 writer、忙时拒绝、异常失败关闭和操作日志关联。
- [x] 区分页面默认微信通知路由与微信任务原路回送路由，禁止两者互相兜底。
- [x] 完成微信 Context Token 持久化兼容处理及 Gateway 重启后的出站恢复。
- [x] 在 macOS、Ubuntu 和真实微信完成插件加载、重新绑定、任务执行与最终结果回送验收。

## 2. 当前维护基线

- 微信 Chub 模式仅适用于固定 Tailnet、单 Owner、单健康 ClawBot 和单一专用 Session。
- 该专用 Session 可使用已批准的 `Full access`；其他 Agent、身份、入口和 Session 不继承此例外。
- Chub 插件源码以 `integrations/openclaw/chub/` 为唯一来源，运行目录只接收构建并验证的产物。
- 微信插件升级或重装后，重新检查 Context Token 持久化能力和真实出站结果。
- Gateway、任务和通知均以最终状态为准，不能把进程启动、HTTP 200 或 Tool Call 创建视为操作成功。

## 3. 按需扩展

- 飞书指定人员提醒等待提供群成员 Open ID。
- 多 Owner、多 ClawBot、跨节点提交需要重新设计身份和 Session 隔离。
- 新增 Chub Tool 必须由具体需求驱动，使用固定参数、明确风险和最终状态。
- 连续电脑交互、自动事件通知和多节点统一入口另行设计。

第三阶段没有默认遗留开发任务。BRClient 相关基础 LLM 和 Bedrock 快速交互入口已经移除。
