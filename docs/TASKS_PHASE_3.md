# Chub 第三阶段高层计划

> 状态：OpenClaw、微信 ClawBot、OpenClaw 外部模型和 Chub 基础 LLM 已完成 MacBook、Ubuntu 首轮验收。当前正在进入 Chub 受限能力与消息通道接入。

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
- [ ] 确定 OpenClaw 调用 Chub 的连接方式。
- [ ] 提供最小、固定、只读的 Chub 能力适配。
- [ ] 验证认证、超时、错误反馈和最终状态语义。
- [ ] 确认接入可独立关闭，不影响现有节点能力。

## 3. M3：飞书单向通知与微信双向通道

- [ ] 确认飞书群机器人 Webhook 的消息格式、地址管理、限流和失败重试条件。
- [ ] 打通飞书群机器人 Webhook 单向通知与结果推送。
- [x] 确认微信 ClawBot 的基础双向接口、Owner 认证、会话保持和网络条件。
- [x] 打通 ClawBot 与 OpenClaw 的基础双向消息和普通结果返回。
- [x] 在 MacBook 与 Ubuntu 完成插件安装、扫码登录、发送者配对、Owner 授权、Gateway 重启恢复和普通消息收发验收。
- [x] 在 Chub 首页实现受控微信绑定会话、二维码展示、数字验证码提交和操作互斥。
- [x] 在 MacBook 通过 Chub 首页完成真实二维码生成、扫码绑定、状态恢复和取消验收。
- [ ] 在 Ubuntu 通过 Chub 首页完成二维码绑定流程验收。
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

- [ ] 完成飞书群机器人 Webhook 单向通知链路。
- [ ] 完成“微信 ClawBot ↔ OpenClaw/共享基础 LLM ↔ Chub/目标电脑”双向交互链路。
- [ ] 验证请求、处理中状态、最终结果和操作日志可追踪。
- [ ] 验证重复请求、超时、断链和服务不可用路径。
- [ ] 完成 macOS、Ubuntu 和手机/微信入口实际验收。
- [ ] 验证首批指令权限及高风险确认符合最终安全方案。

## 6. 当前任务

OpenClaw 与 Chub 共享基础 LLM 的双端基线已经可用：OpenClaw 保存 Provider、模型和 SecretRef，Chub 只读复用配置并独立请求供应商 API。下一步优先处理 OpenClaw 自身的工具执行、沙箱、提权和审批权限，再确定 OpenClaw 调用 Chub 的最小 Tool 集合。Chub 默认启用可显式关闭的轻量 Tailscale 免 Token 模式：当前个人 Tailnet 只加入维护者本人控制的设备，这些设备整体视为可信；服务直接监听本机 Tailscale IP，并仅按真实连接来源是否属于 Tailnet 判断。浏览器保留已有 Hub Token 作为 Tailscale 验证失败时的登录回退，回退认证失败后再清除。该模式不提供用户身份体系，也不替代能力白名单和高风险确认；如果 Tailnet 的设备信任前提变化，需要重新评估授权边界。任务白名单、风险等级和各类连接参数在对应功能实施时提供并形成当次 PoC 验收标准。
