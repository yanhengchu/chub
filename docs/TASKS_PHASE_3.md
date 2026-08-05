# Chub 第三阶段高层计划

> 状态：第三阶段首版已闭环并进入持续维护。OpenClaw、微信 ClawBot、外部模型、Chub 基础 LLM、权限基线、Chub 状态 Tool 和飞书通知基础能力已完成 MacBook、Ubuntu 核心链路验收。低风险状态变更能力、指定人员提醒和连续电脑交互均由后续真实需求驱动，不作为首版闭环条件。

## 1. M1：边界与场景设计

- [x] 确认第三阶段三条核心主线：OpenClaw、飞书单向通知与微信双向交互、OpenClaw/Chub 共享基础 LLM。
- [x] 完成 OpenClaw 与 Chub 关系的前置调研。
- [x] 形成 OpenClaw、微信双通道、共享基础 LLM 与 Chub 的接入流程初稿。
- [x] 明确 OpenClaw、飞书 Webhook、微信 ClawBot、共享基础 LLM 与 Chub 的职责和信任边界。
- [x] 固定微信设备能力调用方向为“微信 ClawBot → OpenClaw → Chub → OpenClaw → 微信 ClawBot”，不由 Chub 反向调用 OpenClaw。
- [x] 确认 Gateway 可首装在 MacBook 或 Ubuntu，实施时按在线条件选择。
- [x] 确认 ClawBot 首期 Chub 场景为通过受限 Tool 完成状态检查和结果返回。
- [x] 确认高风险指令执行前必须获得明确确认。
- [x] 确认飞书目标群由维护者提供的 Webhook 地址固定，只发送明确配置的状态、任务结果和异常消息。
- [x] 确认 ClawBot 绑定与操作授权分离，高风险能力可只授予指定账号。
- [x] 明确首版只读 Tool 的身份、会话、固定节点和调用边界；状态变更操作的操作 ID 映射随具体能力单独设计。
- [x] 补充安全、隐私、网络和成本约束；当前边界已覆盖可信网络、固定 Tool、凭证保护、有界响应、操作确认和日志脱敏。

产出：第三阶段详细方案和首个 PoC 验收标准。

## 2. M2：OpenClaw 最小接入

- [x] 在 MacBook 与 Ubuntu 完成 OpenClaw 安装、初始化、Gateway 后台运行和基础控制台验收。
- [x] 在 Chub 首页提供独立 OpenClaw 卡片，支持状态检查和固定的启动、停止、重启操作。
- [x] OpenClaw 维护操作完成后核验 Gateway 最终状态，并记录完整操作生命周期。
- [x] 完成 OpenClaw 卡片、Tailscale Serve HTTPS 入口和返回状态缓存的 MacBook 实机验收。
- [x] 完成 OpenClaw 卡片的 Ubuntu 实机验收。
- [x] 确定 OpenClaw 调用 Chub 的固定 Tailnet 连接方式。
- [x] 提供最小、固定、只读的 `chub_get_status` 能力适配。
- [x] 实现认证、超时、超大响应和无效响应的受控错误反馈；专项实机故障验收暂不要求。
- [x] 确认插件接入可独立关闭，不影响现有节点能力。

## 3. M3：飞书单向通知与微信双向通道

- [x] 确认飞书群机器人 Webhook 的消息格式、Secret 管理、大小限制和失败语义。
- [x] 实现固定目标注册表、飞书 Provider、短期幂等和单向通知 API。
- [x] 在统一 `chub` 插件中增加 `chub_send_notification`，完成 MacBook 真实 Agent 普通消息调用。
- [x] 微信与 TUI 接入统一的 `chub_send_notification` 原文保护链路，当前首版不再单列重复验收。
- [x] 在用户明确要求时完成 `@所有人` 真实验收。
- 指定人员提醒等待 Open ID 后按需验收，不作为首版任务。
- [x] 抑制 `httpx/httpcore` INFO 请求日志，避免完整飞书 Webhook URL 进入 Hub 日志。
- [x] 确认微信 ClawBot 的基础双向接口、Owner 认证、会话保持和网络条件。
- [x] 打通 ClawBot 与 OpenClaw 的基础双向消息和普通结果返回。
- [x] 在 MacBook 与 Ubuntu 完成插件安装、扫码登录、发送者配对、Owner 授权、Gateway 重启恢复和普通消息收发验收。
- [x] 在 Chub 首页实现受控微信绑定会话、二维码展示、数字验证码提交和操作互斥。
- [x] 在 MacBook 通过 Chub 首页完成真实二维码生成、扫码绑定、状态恢复和取消验收。
- [x] 在 Ubuntu 通过 Chub 首页完成二维码绑定流程验收。
- [x] 验证 ClawBot 调用 `chub_get_status` 完成状态检查。

## 4. M4：OpenClaw 与 Chub 共享基础 LLM

- [x] 确认由维护者提供现有 LLM API、Token 和模型信息。
- [x] 确认首个 API 的兼容协议、Base URL 和模型名称。
- [x] 使用独立密钥文件与 SecretRef 完成首个模型凭证配置。
- [x] 在 MacBook 与 Ubuntu 接入 `brclient/amazon.nova-pro` 并完成基础对话验收。
- [x] Chub 只读复用 OpenClaw Provider 与文件型 SecretRef，并完成一次独立的真实 API 文本调用。
- [x] 在快速交互页面提供仅当前页面有效的 Codex CLI / Amazon Bedrock API 切换，并在本机历史中标记执行来源与模型快照。
- [x] 在 Ubuntu 完成 Chub 基础 LLM 和快速交互 Bedrock 入口的真实调用验收。
- [x] 实现快速交互任务完成后通过固定 OpenClaw 微信账号和固定收件人异步发送有界结果摘要；通知状态与任务状态独立，已完成 macOS 页面任务、OpenClaw 命令和微信收件实机验收。
- [x] 验证首个模型可以完成实际 Tool Calling；偶发占位文本、回退模型和额度策略按真实稳定性或成本需求继续优化。

## 5. M5：端到端与安全验收

- [x] 完成飞书群机器人 Webhook 普通消息单向通知链路。
- [x] 完成“微信 ClawBot → OpenClaw → `chub_get_status` → Chub → 微信最终回复”的核心调用闭环。
- [x] 确认首版 Chub Tool 使用固定地址、固定接口和严格参数，不开放任意命令、路径或状态变更。
- [x] 完成 OpenClaw、微信基础双向消息、Chub 状态 Tool 和飞书通知在 MacBook、Ubuntu 的核心链路验收。
- [x] 确认低风险状态变更能力不是首版必选项，高风险能力仍需独立设计并逐次明确确认。

## 6. 后续维护与按需扩展

### 6.1 低风险状态变更能力

首版不要求新增状态变更 Tool。出现明确价值后，可选择参数固定、风险可控且失败可恢复的低风险白名单能力，单独设计和验收。

届时需要一起完成：

- 微信允许身份、OpenClaw 会话、Chub 操作 ID 和目标节点之间的最小映射；不建立复杂多用户权限平台。
- 首批能力只以 OpenClaw Tool 调用 Chub；验证 Chub 不会因处理微信请求而调用 Gateway 或 `openclaw agent`。
- `requested`、`started`、`succeeded`、`failed` 与微信进度、最终回复之间的明确对应。
- 重复消息、Tool 超时、模型未实际调用 Tool、Chub 不可达、结果回复失败等异常路径。
- 首批能力的参数白名单、风险等级和确认规则；高风险能力不随首批低风险能力顺带开放。
- MacBook、Ubuntu 和微信入口的真实链路验收，以及不影响现有 Web、Codex 和自动化入口的回归确认。

### 6.2 外部条件阻塞

- 飞书指定人员提醒：等待提供并配置群成员 Open ID；不影响普通消息和显式 `@所有人`。

### 6.3 按需扩展

- 连续电脑交互、Chub 自动事件路由和多节点统一入口：出现明确使用场景后单独设计。
- Tool Calling 偶发占位文本、模型不可用、限流、回退模型和额度策略：先保持当前明确失败语义，出现真实稳定性或成本需求后再优化。
- 更多 Chub Tool：必须由具体需求驱动，不以扩充 Tool 数量作为阶段目标。

OpenClaw 与 Chub 共享基础 LLM、`chub_get_status`、微信基础双向消息和飞书通知首版均已收尾，第三阶段不再保留默认必做任务。
