# 微信 Chub 模式设计

> 状态：微信模式专用状态接口和状态路由已在 Ubuntu 接入；微信专用任务提交尚未实现。

## 1. 目标

将微信 ClawBot 作为 Chub 快速交互的另一个入口。启用“微信 Chub 模式”后，当前绑定微信账号的每条普通私聊消息直接提交给 Chub 的微信专用快速交互能力。Chub 在该能力内部查找或创建并复用同一个微信专用 Codex Session，再执行任务并原路返回最终结果。

OpenClaw 在该模式中只负责微信消息接收、任务状态回送和通道投递；不调用 OpenClaw Agent、不读取或调用其 LLM，也不改写用户任务或 Codex 最终结果。任务仍会由 Chub 调用 Codex CLI，因此仍使用该 Session 已选定的 Codex 模型、权限和工作区。

## 2. 核心链路

```text
当前绑定微信账号的私聊
  -> ClawBot / OpenClaw Gateway
  -> 微信 Chub 模式路由
  -> Chub 微信专用快速交互入口
  -> Chub 确保微信专用 Session 存在
  -> Codex CLI（固定 Session）
  -> Chub 最终状态
  -> OpenClaw 通道投递
  -> 当前绑定微信账号
```

模式路由必须在 OpenClaw 的模型调度之前处理命中的微信消息，并结束该轮 Agent 调度。未启用该模式时，现有 OpenClaw Agent 与 Tool 行为不变。

## 3. 当前背景与运行流程

当前 OpenClaw Gateway 与微信 ClawBot 是单一账号绑定关系，只服务维护者本人；该账号就是唯一 Owner。该单一绑定已由 OpenClaw 微信入口负责，微信 Chub 模式只判定“已启用且为私聊”，不再读取、配置或比对发送者标识。

这是后续实现和迁移的固定约束：不得为微信 Chub 模式新增第二套 Owner 标识、发送者比对、身份映射、账号选择、用户角色体系或跨账号 Session 隔离。只有实际改变为多账号绑定时，才重新单独设计身份边界。

### 3.1 OpenClaw 的模式判定

OpenClaw 与 Chub 部署在同一可信 Tailnet 内。插件只连接配置中唯一固定的 Chub Tailnet 地址，不接受微信消息、模型输出或任何客户端参数提供目标地址；Chub 只接受真实 Tailnet socket 来源的该类受保护请求，不信任转发地址 Header。此固定网络边界只用于 OpenClaw 与 Chub 的服务间调用，不扩大微信侧的访问范围。

Chub 通过配置保存微信 Chub 模式是否启用，并提供固定的只读“微信 Chub 模式路由状态”接口。当前 PoC 的接口只返回模式是否启用，以及固定工作区和 Codex PTY 基础依赖是否可用于状态路由；不验证微信专用 Session、权限、模型或任务提交能力。OpenClaw 的 Chub 插件在 `before_dispatch` 钩子中处理每条入站消息；该钩子位于 OpenClaw 创建 Agent、构建提示词和调用 LLM 之前，能够直接结束本轮消息处理。

```text
OpenClaw 收到消息
  -> before_dispatch 钩子
  -> 不是微信私聊：不处理，继续 OpenClaw 原流程
  -> 经固定 Tailnet 地址向 Chub 读取“微信 Chub 模式路由状态”
       -> 未启用：不处理，继续 OpenClaw Agent / LLM
       -> 已启用且就绪：当前阶段返回状态确认；任务提交阶段调用 Chub 快速交互入口并返回 handled
       -> 已启用但未就绪，或 Chub 不可达 / 超时 / 状态无效：返回 handled 失败提示
```

`handled` 表示当前消息已经由插件处理，OpenClaw 直接发送当前阶段的状态确认或明确失败提示，不再创建 Agent 或调用 LLM。正式任务提交阶段才发送“任务已提交”。模式状态读取和任务提交均只通过固定 Tailnet 地址访问固定 Chub 接口；这不是任务状态轮询。模式已启用但 Chub 不可达、返回无效状态或任务提交失败时，钩子必须返回已处理的失败提示，不能回退交给 OpenClaw Agent，避免模式异常时意外调用模型或改变消息含义。

不使用 `message_received` 钩子作为路由入口，因为它只是异步观察事件，不能可靠地阻止后续 Agent 流程。

### 3.2 当前状态路由 PoC

当前 Ubuntu Gateway 已启用 `wechatChubStatusMode`。该开关只用于状态路由阶段，不代表最终任务提交能力：当前绑定微信账号的每条私聊消息在 `before_dispatch` 中固定调用 Chub 微信模式状态接口；Chub 返回 `disabled` 时继续原有流程，返回 `ready` 时回复状态文本并标记 `handled`，因此不会创建 OpenClaw Agent 或调用其 LLM。Chub 不可达或未就绪时同样标记 `handled` 并返回失败提示。两条真实微信消息已验证早期通用状态路由行为；专用接口接入后需重新执行一次真实微信验证。

PoC 保持开启，用于后续功能调试。迁移到 Mac 时，使用同一 Chub 插件源码构建并同步插件产物与清单，在 Mac 的 OpenClaw 中配置该 Mac 对应的固定 Chub Tailnet 地址并启用该开关后重启 Gateway；微信重新绑定后，再验证固定收件人的 context token 持久化和状态路由。当前专用状态接口只证明固定网络和 Hook 可用，不表示 Chub 已可接收快速交互任务。

### 3.3 跨设备 OpenClaw 变更规范

Chub 插件的核心代码已经纳入本仓库，`integrations/openclaw/chub/` 是微信 Chub 模式路由的唯一源码。另一台设备上实际被 OpenClaw 加载的插件目录属于运行产物，不应在其中独立演进或成为唯一依据；迁移、修复和后续开发都先修改本仓库源码、测试和清单，再同步到已核实的运行目录。

当前状态路由 PoC 的对应关系如下。正式任务提交能力应沿用同一位置扩展，而不是另建脚本或复制实现。

| 仓库内容 | 迁移时的作用 |
| --- | --- |
| `integrations/openclaw/chub/src/index.ts` | `before_dispatch` 路由、微信私聊范围和 `handled` 语义。 |
| `integrations/openclaw/chub/src/client.ts` | 固定 Tailnet 地址校验及 Chub 状态请求。 |
| `integrations/openclaw/chub/openclaw.plugin.json` | OpenClaw 静态配置清单，必须包含新增配置项。 |
| `integrations/openclaw/chub/src/index.test.ts` | 路由不进入 Agent 或 LLM 的行为验证。 |
| `integrations/openclaw/chub/README.md` | 构建、校验和当前 PoC 的最小说明。 |

`openclaw.plugin.json` 与构建产物同等重要。OpenClaw 会按该静态清单识别插件配置；只同步 `dist/` 而没有同步清单时，新开关可能不会被识别，路由也不会按预期启用。因此每次调整插件配置或 Hook 后，必须同步源码构建产物和清单，并用运行时检查确认。

在另一台设备迁移或让 AI 执行变更时，按以下顺序进行：

```text
1. 读取本设计、AGENTS.md、插件 README，以及微信 context token 补丁规范。
2. 执行 openclaw plugins inspect chub --json，确认实际启用的插件根目录；
   只向该已确认目录同步，不能猜测或修改其他扩展目录。
3. 在本仓库 integrations/openclaw/chub/ 中修改源码和测试，执行 npm test、
   npm run plugin:validate；通过后得到 dist/。
4. 将 dist/ 的内容和 openclaw.plugin.json 一起同步到已确认的插件根目录。
   未安装插件时，才从本仓库该目录执行 OpenClaw 的插件安装，并再次确认根目录。
5. 只配置固定 Chub Tailnet baseUrl、短 timeoutMs 和需要的模式开关；不写入 Hub Token，
   不接受微信消息提供地址或权限，也不新增发送者标识或 Owner 比对配置。
6. 执行 openclaw config validate、重启 Gateway，再执行
   openclaw plugins inspect chub --runtime --json，确认配置项和 before_dispatch Hook
   均已被运行时识别。
7. 微信重新绑定后，用真实私聊验证；确认消息被 handled，且没有新的
   OpenClaw Agent / LLM 会话使用记录。
```

同步步骤中的路径应使用当前设备实际检出的项目目录和第 2 步确认的插件根目录，不将用户目录、主机名、Tailnet 地址、账号或凭证写入本文档或脚本。配置迁移后的当前 PoC 仅需设置 `wechatChubStatusMode: true`；正式能力落地后由 Chub 路由状态接口统一决定是否进入任务提交。需要撤销时，将该开关关闭并重启 Gateway，微信消息会恢复原有 Agent 流程。

微信出站 context token 持久化属于第三方 WeChat 插件的兼容补丁，按[微信 ClawBot Context Token 持久化 AI 补丁规范](WEIXIN_CLAWBOT_CONTEXT_TOKEN_AI_PATCH.md)及其中的参考 patch 单独维护。它不应合并进 Chub 插件源码；迁移验收时同时确认该补丁仍有效，确保 Chub 完成通知能够回送。

### 3.4 最终 Chub 可用快速校验接口

状态路由已不再使用通用 `/api/status`，而由 Chub 提供固定只读接口 `GET /api/openclaw/wechat-chub-mode/status`。它只接受真实 Tailnet socket 来源，不接受 Hub Token 作为替代认证，响应保持有界且不包含 Session ID、模型、工作区、任务正文或凭证：

```text
data.enabled = 是否在 Chub 中启用微信 Chub 模式
data.ready = 当前状态路由是否就绪；正式任务提交阶段改为任务接收是否就绪
data.code = ready | disabled | configuration_invalid | codex_unavailable
```

状态由 Chub 的 `openclaw.weixin_chub_mode.enabled` 集中控制，默认关闭；当前固定工作区由 `workspace_id` 配置。当前状态路由阶段中，`ready` 仅表示模式已启用、固定工作区有效且 Codex PTY 依赖可用，因而可以安全完成状态路由；它不表示微信任务提交接口已实现，也不创建 Session、不启动 Codex或探测模型远端状态。任务提交能力落地后，`ready` 才表示可以接收并启动后续任务；提交接口仍需自行复核条件，已失效的专用 Session 由其按既定规则新建。

状态组合固定为：`disabled` 对应 `enabled=false, ready=false`；`ready` 对应两者均为 `true`；`configuration_invalid` 和 `codex_unavailable` 均对应 `enabled=true, ready=false`。接口自身不可用时使用 HTTP 失败响应，OpenClaw 将其视为已处理失败，不回退调用 LLM。后续任务提交接口必须再次校验这些条件，不能只依赖此前的状态结果。

最终插件流程为：

```text
当前绑定微信账号的私聊
  -> before_dispatch
  -> 固定 Tailnet 调用微信 Chub 模式状态接口
       -> disabled：继续原有 OpenClaw Agent / LLM
       -> ready：当前阶段返回状态确认；任务提交阶段提交微信专用快速交互并返回 handled
       -> 非 ready、不可达、超时或无效响应：返回 handled 失败提示，不调用 LLM
```

当前 `wechatChubStatusMode` PoC 在完成最终接口和任务提交后由正式微信 Chub 模式路由替代，不与正式模式并存。

启用微信 Chub 模式后的完整流程如下：

```text
维护者在微信发送普通消息
  -> OpenClaw 识别当前已启用微信 Chub 模式
  -> 不启动 Agent、不调用 OpenClaw LLM
  -> 调用 Chub 的“提交微信快速交互”入口，传原始任务正文、微信消息 ID 和固定关联标识
  -> 立即回复“任务已提交”

Chub 接收任务
  -> 在内部锁定微信专用 Session
  -> 已有有效 Session：直接复用
  -> 没有有效 Session：按 Chub 固定配置创建一个专用 Session
  -> 复用既有快速交互后台执行
  -> Codex CLI 在微信专用 Session 中执行
  -> Chub 保存最终任务结果
  -> 复用既有微信完成通知
  -> openclaw message send 发送结果给当前固定收件人
```

微信专用 Session 的标识只保存在 Chub 自己的持久化状态中。OpenClaw、微信消息和回送通知都不读取、保存或传入 Session ID，因此不会因 Gateway 重启、Session ID 过期或并发消息产生外部状态同步问题。Chub 在首次创建时使用其固定的微信模式配置决定工作区、权限、模型和推理等级；微信消息只能提供任务正文，不能指定这些参数。

该 Session 长期复用，因此微信任务拥有连续的 Codex 上下文和任务历史。网页可以查看该 Session，但不承担它的创建和绑定；同一 Session 正在执行任务时，新的微信消息只回复“当前任务正在执行”，不排队、不丢弃，也不转交 OpenClaw Agent。

微信入口在任务提交成功后不查询任务状态。任务完成后的成功、失败或超时结果由 Chub 已有的异步完成通知直接推送；任务启动不等于任务成功。

### 3.5 重复消息与 Session 就绪

OpenClaw 将微信平台的消息 ID 原样作为不透明的幂等标识传给 Chub。Chub 保存“微信消息 ID -> 快速交互任务或首次失败原因”的关联：首次消息创建任务，重复投递只返回同一任务或同一失败原因，绝不再次启动 Codex，也不自动重试。微信正常情况下不应重复投递，但该关联用于覆盖 Gateway 或 Chub 重启等异常重复消息。

启用模式时，Chub 必须先校验微信专用 Session 的创建配置和运行依赖：固定工作区可用、Codex 可用、所选模型和推理等级可用，且权限允许快速交互（`Ask for approval` 不可用）。未通过校验时不能启用模式。

如果提交任务时发现已保存的微信专用 Session 已归档、删除或无法读取，Chub 才创建新的专用 Session，并在即时回复和操作日志中明确“已创建新的微信专用 Session”。这表示上下文从新 Session 开始，不能静默伪装为旧上下文仍在继续。

## 4. 模式边界与回送

微信 Chub 模式只增加一个 Chub 拥有的专用入站能力，Codex 快速交互和完成通知沿用当前能力：

```text
微信消息
  -> Chub 提交微信快速交互
  -> Chub 内部获取或创建微信专用 Session
  -> 既有 QuickInteractionManager
  -> 既有 OpenClawCompletionNotifier
  -> 固定 ClawBot / 固定微信收件人
```

因此首版不需要新增任务状态查询、OpenClaw 轮询、任务队列，OpenClaw 也不需要持有固定 Session ID。当前绑定微信账号每次发来消息时，微信 ClawBot 已将该收件人的 context token 持久化；任务完成后 Chub 复用现有固定收件人回送能力，Gateway 重启后仍可恢复该出站上下文。完成通知独立记录发送中、已发送、失败或跳过；微信投递失败不会改变 Codex 任务最终状态，也不自动重试。服务重启中断执行中的任务时，Chub 只持久化明确失败原因，不在关停过程中尝试微信回送；维护者下次向 ClawBot 发送消息后重新请求内容。若任务执行时间过长导致回送失败，也采用同一处理方式。主动取消目前也不发送完成通知，保持现有语义。

仅当前绑定账号的私聊消息可进入该模式。单账号绑定已确定入口身份范围，Chub 不新增任何 Owner 校验、发送者比对或身份映射。群聊或模式未启用时不提交任务，并继续保留既有 OpenClaw 行为。通道控制只保留固定的状态、停止和退出模式操作，且不进入 Codex。

## 5. 启用规则与必要约束

微信 Chub 模式默认关闭。维护者在 Chub 中明确启用后，当前绑定账号发送的普通私聊消息即视为调用 Chub 的微信专用快速交互入口；这不是 OpenClaw Agent 的工具调用，也不会产生 OpenClaw LLM 调用。

OpenClaw 插件本地只保存“此微信入口使用 Chub 路由”的固定连接配置，不保存模式启停结果；每条消息以短超时读取 Chub 路由状态。只有 Chub 明确返回“未启用”才进入原有 Agent 流程，任何连接或状态异常均保持在 Chub 模式失败路径。这样既允许维护者在 Chub 侧集中启停，也不会因可信网络或 Chub 故障而意外切换到模型处理。

微信专用 Session 使用 Chub 为该模式固定的工作区、权限、模型和推理等级。`Ask for approval` 不允许启用此模式。维护者已批准在固定 Tailnet、单一微信 Owner、单一微信专用 Session 的范围内使用 `Full access` 直接执行，不要求逐条消息确认；该例外只适用于微信 Chub 模式，关闭模式即可停止该入口，不能扩展给其他 Agent、账号、入口或 Session。

启用前 Chub 展示并校验工作区、权限、模型和推理等级；需要暂停入口时直接关闭微信 Chub 模式。模式关闭后，新消息恢复原有 OpenClaw Agent 流程，已提交的 Codex 任务不因此取消。

Chub 只接受该入口固定调用的专用字段：原始任务正文、微信消息 ID，以及由 OpenClaw 固定生成的非敏感关联标识（允许身份、OpenClaw 会话或运行标识）。任务正文会原样交给 Codex，但接口不接受独立的系统命令、文件路径、Session、模型或权限参数。每项任务保留上述关联标识、Chub 操作 ID、微信专用 Session、快速交互任务 ID、Chub 固定目标节点、最终状态和回送状态；日志不记录完整任务正文或凭证。

微信 Chub 模式复用现有完成通知与本地 context token 持久化能力：仍只允许固定 ClawBot、固定收件人和 `openclaw message send`，不得调用 `openclaw agent`，也不得把通知能力用于触发新的 Chub 操作。实现时将该既有受控回送范围明确包含微信 Chub 模式任务。

## 6. 验收方向

1. 模式关闭时微信消息继续进入原有 OpenClaw Agent；模式开启且 Chub 就绪时确认 `before_dispatch` 已处理消息，OpenClaw 没有模型调用。
2. 启用前验证工作区、Codex、模型、推理等级和权限；`Ask for approval` 或任一依赖不可用时确认模式不能启用，并确认 `Full access` 仅作用于已批准的微信专用 Session。
3. 启用后从微信发送首条任务，确认 Chub 只创建一个微信专用 Session 和一个快速交互任务。
4. 重复投递同一微信消息、并发发送首条消息、重启 Gateway 或 Chub 后重复投递，确认始终只执行一个 Codex 任务；首次提交失败后重复消息只返回首次失败原因，不自动重试。
5. 确认有效专用 Session 被复用；Session 失效后只创建一个新 Session，并在即时回复和操作日志中明确上下文已重建。
6. 确认微信专用 Session 的上下文连续，任务最终结果与 Chub 页面一致。
7. 验证插件只访问固定 Chub Tailnet 地址，Chub 仅接受真实 Tailnet socket 来源且忽略转发地址 Header；确认入站消息已持久化固定收件人的 context token，正常完成的任务可自动回送。覆盖执行中重复消息、停止、超时、Chub 不可达、路由状态无效和微信回送失败；回送失败或服务重启中断时不自动重试，下一条维护者消息可刷新上下文并重新请求内容。模式开启但 Chub 异常时确认不回退调用 LLM。
8. 验证群聊及任意 Session/模型/权限注入均不进入 Chub 任务，且不触发 Codex；当前绑定微信账号的私聊不进行第二套身份校验。
9. 完成 macOS、Ubuntu 和真实微信的端到端验收，再决定是否将该模式作为长期入口。
