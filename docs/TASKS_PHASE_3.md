# Chub 第三阶段高层计划

> 状态：第三阶段当前首版能力已完成主要实现与验收，进入持续维护。OpenClaw、微信 ClawBot、外部模型、Chub 基础 LLM、权限基线和 Chub 状态 Tool 已完成 MacBook、Ubuntu 验收；飞书通知 Service、API、OpenClaw Tool、原文保护、本机 `@所有人` 和 Webhook 日志保护已完成验收并收尾。指定人员提醒和连续电脑交互保留为后续按需扩展。

## 1. M1：边界与场景设计

- [x] 确认第三阶段三条核心主线：OpenClaw、飞书单向通知与微信双向交互、OpenClaw/Chub 共享基础 LLM。
- [x] 完成 OpenClaw 与 Chub 关系的前置调研。
- [x] 形成 OpenClaw、微信双通道、共享基础 LLM 与 Chub 的接入流程初稿。
- [x] 明确 OpenClaw、飞书 Webhook、微信 ClawBot、共享基础 LLM 与 Chub 的职责和信任边界。
- [x] 确认 Gateway 可首装在 MacBook 或 Ubuntu，实施时按在线条件选择。
- [x] 确认 ClawBot 首期场景为状态检查、白名单任务执行和结果查询。
- [x] 确认高风险指令执行前必须获得明确确认。
- [x] 确认飞书目标群由维护者提供的 Webhook 地址固定，只发送明确配置的状态、任务结果和异常消息。
- [x] 确认 ClawBot 绑定与操作授权分离，高风险能力可只授予指定账号。
- [ ] 实现时明确身份、会话、节点和操作的映射关系。
- [ ] 补充安全、隐私、网络和成本约束。

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
- [ ] 有 Open ID 后验收指定人员提醒。
- [x] 抑制 `httpx/httpcore` INFO 请求日志，避免完整飞书 Webhook URL 进入 Hub 日志。
- [x] 确认微信 ClawBot 的基础双向接口、Owner 认证、会话保持和网络条件。
- [x] 打通 ClawBot 与 OpenClaw 的基础双向消息和普通结果返回。
- [x] 在 MacBook 与 Ubuntu 完成插件安装、扫码登录、发送者配对、Owner 授权、Gateway 重启恢复和普通消息收发验收。
- [x] 在 Chub 首页实现受控微信绑定会话、二维码展示、数字验证码提交和操作互斥。
- [x] 在 MacBook 通过 Chub 首页完成真实二维码生成、扫码绑定、状态恢复和取消验收。
- [x] 在 Ubuntu 通过 Chub 首页完成二维码绑定流程验收。
- [ ] 稳定 ClawBot 中的 Tool Calling 进度、最终结果和占位回复失败语义。
- [ ] 验证 ClawBot 状态检查、白名单任务执行和结果查询。
- [ ] 实现身份映射、重复消息、超时、失败反馈和高风险操作强制确认。

## 4. M4：OpenClaw 与 Chub 共享基础 LLM

- [x] 确认由维护者提供现有 LLM API、Token 和模型信息。
- [x] 确认首个 API 的兼容协议、Base URL 和模型名称。
- [x] 使用独立密钥文件与 SecretRef 完成首个模型凭证配置。
- [x] 在 MacBook 与 Ubuntu 接入 `brclient/amazon.nova-pro` 并完成基础对话验收。
- [x] Chub 只读复用 OpenClaw Provider 与文件型 SecretRef，并完成一次独立的真实 API 文本调用。
- [x] 在快速交互页面提供仅当前页面有效的 Codex CLI / Amazon Bedrock API 切换，并在本机历史中标记执行来源与模型快照。
- [x] 在 Ubuntu 完成 Chub 基础 LLM 和快速交互 Bedrock 入口的真实调用验收。
- [ ] 稳定验证首个模型的 Tool Calling 能力；已确认能成功调用，但仍存在偶发占位文本代替真实调用。
- [ ] 完成超时、额度限制和回退模型策略。
- [ ] 验证模型不可用、限流和失败时的降级反馈。

## 5. M5：端到端与安全验收

- [x] 完成飞书群机器人 Webhook 普通消息单向通知链路。
- [ ] 完成“微信 ClawBot ↔ OpenClaw/共享基础 LLM ↔ Chub/目标电脑”双向交互链路。
- [ ] 验证请求、处理中状态、最终结果和操作日志可追踪。
- [ ] 验证重复请求、超时、断链和服务不可用路径。
- [ ] 完成 macOS、Ubuntu 和手机/微信入口实际验收。
- [ ] 验证首批指令权限及高风险确认符合最终安全方案。

## 6. 当前任务

OpenClaw 与 Chub 共享基础 LLM 的双端基线已经可用；OpenClaw 权限基线和 `chub_get_status` 双平台验收已经收尾。飞书通知使用本机固定 target 注册表与独立 Secret 文件，Chub 提供受保护发送 API，统一 `chub` 插件提供 `chub_send_notification`；真实 Agent 原文调用、本机显式 `@所有人` 和 Webhook URL 新日志保护均已验收，飞书通知首版结束。指定人员提醒等待提供 Open ID，Chub 自动事件路由和连续电脑交互均作为后续独立需求，不阻塞当前首版。
