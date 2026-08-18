# Chub–OpenClaw 接入设计

> 状态：持续维护。
>
> 本文遵循[Chub 总体架构](CHUB_ARCHITECTURE_DESIGN.md)，只维护 Chub 与 OpenClaw/微信 ClawBot 的端到端业务、身份、权限、路由、幂等和通知边界。

在本接入中，Chub 是个人 AI 工作站的控制面和可靠任务协调中枢，拥有设备业务规则、权限校验、Session/Request 选择、任务提交、恢复与终态管理；OpenClaw 是独立 Agent 平台，但在微信 Chub 链路中只承担通道网关、可信消息上下文和收发适配，不参与设备任务的自主判断或执行。Codex 是实际完成分析、编码和任务执行的 Agent。

本文不重复维护以下内容：

- 当前能力、微信固定指令和用户可见格式：[集成能力清单](CHUB_INTEGRATION_CAPABILITIES.md)。
- Chub 插件协议、构建、部署和升级：[Chub OpenClaw 插件说明](../integrations/openclaw/chub/README.md)。
- Session、Activity、槽位和单 writer 语义：[AI Session 状态模型](AI_SESSION_STATE_DESIGN.md)。
- 后台任务、翻译队列、恢复、通知终态和 Web 重启：[快速交互独立 Worker 设计](QUICK_INTERACTION_WORKER_DESIGN.md)。
- 微信 Context Token 的插件兼容处理：[Context Token 补丁规范](WEIXIN_CLAWBOT_CONTEXT_TOKEN_AI_PATCH.md)。

## 1. 当前架构

Chub 当前提供四类 OpenClaw 能力：

1. 在首页查看和维护本机 Gateway、微信通道与 Tailscale 入口。
2. 向普通 OpenClaw Agent 提供受限的状态查询和飞书通知 Tool。
3. 在微信 Chub 模式下，将可信私聊交给 Chub 单一调度入口处理。
4. 将异步任务最终结果按任务保存的微信路由原路送回。

```text
普通 Agent
  -> chub_get_status / chub_send_notification
  -> Chub 固定能力

微信 Chub 模式
  微信私聊 -> Chub 插件 before_dispatch
           -> POST /api/openclaw/wechat-chub-mode/dispatch
           -> 固定路由，或 Quick Worker -> Codex
           -> 同步决定由 Hook 原路交付
           -> 异步结果由 Chub 按任务保存路由发送
```

整条微信 Chub 链路不调用 OpenClaw Agent 或 LLM 做路由，也不把 Hook 已触发、HTTP 200、任务已建立或通知已开始当作最终成功。

## 2. 状态与责任边界

以下状态相互独立，任何前置状态正常都不能代替后续业务终态：

| 状态 | 权威来源与含义 |
| --- | --- |
| Gateway 正常 | OpenClaw 服务、进程、端口和 RPC 可用 |
| Channel 正常 | 微信插件和本地通道进程可用 |
| ClawBot 已绑定 | 微信服务端当前绑定这台 Gateway |
| Owner 已配置 | OpenClaw 当前允许的唯一微信身份 |
| Chub 模式就绪 | Chub 固定配置、Codex、工作区和通知条件满足 |
| 任务完成 | Quick Worker 和 Codex 已进入对应终态 |
| 通知完成 | 保存路由上的独立发送流程已进入终态 |

同一个 ClawBot 同时只能绑定一台 Gateway；本地 Channel 正常不能代替真实微信收发确认。Chub 首页只提供 OpenClaw 状态、受控维护和绑定入口，不接管 OpenClaw 的安装、升级、身份配置或插件部署。

状态所有权遵循以下边界：

- OpenClaw 拥有 Gateway、通道、账号、Owner 和微信消息上下文。
- Chub 拥有入口鉴权、业务路由、当前绑定、Session/Request 选择、操作状态和通知关联。
- Quick Worker 拥有非实时任务、Session 租约、执行终态和恢复依据。
- 插件只传递可信上下文并执行 Chub 的交付决定，不拥有业务指令、Session、Request 或任务状态。

## 3. 身份与权限

当前部署边界为：同一节点上的 Chub 与 OpenClaw、单一健康 ClawBot、单一微信 Owner。

- 微信统一调度接口只接受本节点真实 Tailnet socket 来源，不接受 Hub Token 或转发 Header 替代。
- 群聊、未知发送者、缺少稳定消息标识或缺少可信回送路由时不进入 Chub 任务。
- 消息正文只能提供业务内容，不能指定内部 Session ID、工作区、权限、模型、路径、命令、接口或收件人。
- 微信 Chub 模式的 `Full access` 只随当前受控 Owner 和绑定 Session 生效，不成为 Session 永久权限，也不适用于其他入口。
- 当前不支持第二个 Owner、多个并行 ClawBot 或跨节点任务提交；扩展这些范围前必须重新设计身份隔离和调用方认证。

普通 OpenClaw Agent 仍使用自身 Shell 审批和文件边界，不因微信 Chub 例外而获得额外权限。

## 4. 微信统一调度

Chub 插件只负责取得可信通道上下文、调用一次固定接口并执行 Chub 返回的交付决定。协议版本、请求字段和 `pass` / `reply` / `handled` 语义由[插件说明](../integrations/openclaw/chub/README.md)维护。

Chub 按以下顺序处理消息：

1. 校验模式、同节点来源、Owner、私聊、消息幂等标识和回送路由。
2. 匹配能力清单登记的固定路由。
3. 将未命中的非空文字或可信语音转写作为普通快速交互任务提交。
4. 返回放行、固定回复或即时提交决定；异步任务结果不在同步响应中伪装完成。

固定指令、普通任务回执和任务终态都由 Chub 生成，插件只能原样交付。具体指令、中文别名、匹配规则和回复格式统一由[能力清单第 4 节](CHUB_INTEGRATION_CAPABILITIES.md#4-微信-clawbot-指令)维护。

### 4.1 Session 与 Request 路由

- Chub 持有当前绑定和持久槽位；插件不能选择、创建或改写 Session/Request。
- 普通任务在消息到达时锁定目标 Session。Session 忙、失效或 writer 不可确认时失败关闭，不自动切换目标。
- Session 的标题、槽位、Activity 和单 writer 规则由状态模型维护；接入层只消费其公开状态并执行路由。
- Request 只允许通过受控入口维护。微信执行时锁定槽位版本和本次运行关联，旧任务不得修改已更新、归档或复用的槽位。
- 涉及切换后提交、续提或 Request 执行的多阶段路由，必须保存父消息和稳定派生标识；重复投递只能恢复或复用原决定，不能再次产生副作用。

状态摘要和通知中的任务信息必须来自本次路由保存的可信快照。单一数据源读取失败只能降级对应字段，不得猜测其他 Session/Request，也不得改变操作的真实结果。

### 4.2 文本与语音前处理

文本优化是 Chub 在普通任务提交前选择的可选处理门，插件不参与判断。开启后目标 Session 在原消息到达时即被锁定，翻译队列及跨重启恢复由 Worker 设计维护；固定指令绕过该处理门。优化失败、结果超限或目标已不可提交时失败关闭，不执行原文或切换目标。

只有微信通道确认消息类型为语音且提供非空转写时，才能进入语音路径。任务正文始终使用干净转写；内部来源标记不进入正文、摘要、日志或对外接口，普通文字也不能自行声明为可信语音。

## 5. 幂等与持久化边界

插件根据可信通道上下文生成非敏感消息标识；缺少稳定标识时失败关闭。Chub 在产生副作用前持久化路由决定，并遵循：

- 相同标识和路由复用首次决定，不重复执行。
- 相同标识携带不同路由时拒绝。
- 首次失败不因重复投递自动重试。
- 已存在的派生任务按稳定标识确认，不重新提交。
- 服务重启后无法确认的副作用按失败收敛，不猜测成功。

统一调度、Session/Request 操作、Worker 任务、Codex 终态和微信通知分别记录状态；前一阶段成功不能代替后一阶段。具体任务恢复、租约和通知补偿由 Worker 设计维护，接入层只保存恢复所需的身份、路由和业务关联。

## 6. 结果与通知边界

微信 Chub 任务保存本次 Hook 提供的账号和发送者，结束后只按该路由发送：

- 任务成功、失败或超时后进入独立通知流程；通知失败不改变任务结果。
- 发送前重新校验任务保存的 Session/Request 关联；槽位失效或复用时不得显示新对象。
- 路由缺失、账号停止或发送失败时不回退到全局收件人。
- 超长结果按段落有界分段，最多发送 5 条；部分送达后停止，不自动无限重试。
- 页面快速交互使用单独配置的全局固定接收人，与微信任务原路路由隔离。

通知标题、字段、状态标记和用量展示以能力清单为准；通知记录、补偿和任务级 Web 重启以 Worker 设计为准。微信出站依赖通道维护的 Context Token，Chub 和 Chub 插件不读取或保存 Token 正文；兼容性要求见[补丁规范](WEIXIN_CLAWBOT_CONTEXT_TOKEN_AI_PATCH.md)。

## 7. OpenClaw Agent Tool 与管理入口

普通 OpenClaw Agent 可以通过固定 Tool 读取 Chub 基础状态，或向 Chub 预配置的飞书目标发送有界纯文本。调用方不能指定任意 URL、Open ID、Secret 路径或扩大设备权限。

用户要求原文发送时必须使用当前运行关联的可信原文，无法确认时拒绝；只有明确要求撰写、总结或改写时才允许生成内容。微信 Chub 私聊不通过这些 Tool 完成路由，Chub 直接发送飞书也不经过 OpenClaw。

首页“OpenClaw 环境”卡片只展示必要状态并提供受控启停、重启和绑定操作，不暴露配置正文、任意命令或原始日志。操作成功必须以最终实例和健康状态确认。OpenClaw 和微信通道的安装升级遵循上游说明；Chub 插件的部署、诊断和协议升级步骤见[插件说明](../integrations/openclaw/chub/README.md)。

## 8. 验收边界

自动化验证负责协议、身份校验、路由、幂等和失败边界。涉及微信通道、回送路由、通知格式或插件协议的变化，还需由维护者本人在受影响平台完成真实文字和语音回归；Gateway、Channel 或发送日志正常都不能代替微信客户端实际收到结果。

当前 Gateway 管理、微信绑定、单 Owner、统一调度、普通任务、原路通知和飞书 Tool 已纳入 macOS、Ubuntu 验收基线。Requests 的本机存储、固定指令、提交幂等、恢复和通知关联已完成自动化验证，但相关固定指令的真实微信收发仍待维护者验收。未改变插件协议时，不因 Chub 内部业务调整重建插件；具体部署和协议验收状态由插件说明维护。
