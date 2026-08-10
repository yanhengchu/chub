# 微信 Chub 模式设计

> 状态：任务级微信原路回送已实现、通过自动化验证并同步运行产物，等待 OpenClaw Gateway 加载最新插件后完成真实微信端到端验收。

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

### 2.1 术语与数据归属

| 名称 | 含义 | 保存位置与规则 |
| --- | --- | --- |
| `accountId` | ClawBot 账号实例，表示由哪个机器人发送消息 | OpenClaw 保存账号与登录状态。Chub 全局 `weixin_account_id` 是可选兼容性覆盖，当前单 ClawBot 部署保持 `null`，发送时选择唯一健康账号；微信任务另保存提交时的任务级副本。 |
| `senderId` / `weixin_recipient` | 给 ClawBot 发消息的微信用户标识，出站时作为接收人；当前微信插件可能经 `conversationId` 提供同一私聊路由 | 页面来源快速交互使用 Chub 的全局 `weixin_recipient`；微信任务保存本次消息的任务级接收人。它不是公开微信号，也不作为 Chub 的第二套 Owner 认证。 |
| Context Token | 微信通道实际发送所需的最近入站上下文 | 只由 OpenClaw 按 `accountId + senderId` 持久化和恢复；Chub 不保存、不读取也不接收该 Token。 |
| 全局默认通知路由 | Chub 页面来源快速交互完成通知 | 固定 `weixin_recipient`，账号默认动态选择唯一健康 ClawBot；可选 `weixin_account_id` 仅作为兼容性覆盖。 |
| 任务原路回送路由 | 微信发起任务完成后的回送目标 | Chub 为每个任务私有保存不可变的 `accountId + senderId`，不覆盖全局配置，也不回退到全局目标。 |

重新绑定 ClawBot 后，通常变化的是 `accountId`。在当前单 ClawBot 部署中，全局账号保持 `null` 即可自动使用新账号；同一微信用户先向新 ClawBot 发送一条消息，OpenClaw 会为新 `accountId + senderId` 刷新 Context Token。只有接收微信用户发生变化时，才需要调整全局 `weixin_recipient`。通道内部标识不视为平台永久不变的公开账号。

## 3. 当前背景与运行流程

当前 OpenClaw Gateway 与微信 ClawBot 是单一 Owner 范围，只服务维护者本人。微信 Chub 模式不新增可配置的 Owner 映射，但必须读取 `before_dispatch` 提供的可信 `accountId` 和本次私聊发送者 ID，将二者作为本次任务不可变的回送路由；它们不由消息正文、模型或客户端自定义字段推导。

这是后续实现和迁移的固定约束：回送路由只解决“结果回到本次消息来源”，不构成第二套身份认证，也不允许消息选择任意账号或收件人。当前只允许恰好一个健康运行中的 ClawBot；切换账号时先停用旧账号并完成新账号绑定。若同时存在多个健康 ClawBot，提交失败关闭，不能猜测或回退到全局配置。只有实际改变为多 Owner 或多账号并行时，才重新设计身份与 Session 隔离。

### 3.1 OpenClaw 的模式判定

OpenClaw 与 Chub 部署在同一可信 Tailnet 内。插件只连接配置中唯一固定的 Chub Tailnet 地址，不接受微信消息、模型输出或任何客户端参数提供目标地址；Chub 只接受真实 Tailnet socket 来源的该类受保护请求，不信任转发地址 Header。此固定网络边界只用于 OpenClaw 与 Chub 的服务间调用，不扩大微信侧的访问范围。

Chub 通过配置保存微信 Chub 模式是否启用，并提供固定的只读“微信 Chub 模式路由状态”接口和任务提交接口。状态接口校验固定工作区、权限、Codex、模型配置及完成通知能力，`ready` 不依赖页面来源任务的默认通知路由；本次微信消息的真实回送路由在提交时单独校验。状态查询不会创建 Session 或启动任务。OpenClaw 的 Chub 插件在 `before_dispatch` 钩子中处理每条入站消息；该钩子位于 OpenClaw 创建 Agent、构建提示词和调用 LLM 之前，能够直接结束本轮消息处理。

```text
OpenClaw 收到消息
  -> before_dispatch 钩子
  -> 不是微信私聊：不处理，继续 OpenClaw 原流程
  -> 经固定 Tailnet 地址向 Chub 读取“微信 Chub 模式路由状态”
       -> 未启用：不处理，继续 OpenClaw Agent / LLM
       -> 已启用且就绪：调用 Chub 提交入口并返回 handled
       -> 已启用但未就绪，或 Chub 不可达 / 超时 / 状态无效：返回 handled 失败提示
```

`handled` 表示当前消息已经由插件处理，OpenClaw 直接发送任务已提交或明确失败提示，不再创建 Agent 或调用 LLM。只有 Chub 明确接受任务才发送“任务已提交”。模式状态读取和任务提交均只通过固定 Tailnet 地址访问固定 Chub 接口；这不是任务状态轮询。模式已启用但 Chub 不可达、返回无效状态或任务提交失败时，钩子返回已处理的失败提示，不能回退交给 OpenClaw Agent，避免模式异常时意外调用模型或改变消息含义。

不使用 `message_received` 钩子作为路由入口，因为它只是异步观察事件，不能可靠地阻止后续 Agent 流程。

### 3.2 当前提交路由

当前 Mac Gateway 已启用 `wechatChubStatusMode`。为避免引入第二个迁移开关，正式提交路由沿用该配置键，但语义已从“状态确认”转为“微信 Chub 路由”。当前绑定微信账号的每条私聊消息在 `before_dispatch` 中先读取 Chub 微信模式状态；Chub 返回 `disabled` 时继续原有流程，返回 `ready` 时提交原消息并标记 `handled`，因此不会创建 OpenClaw Agent 或调用其 LLM。Chub 不可达、未就绪、任务忙碌或提交失败时同样标记 `handled` 并返回失败提示。源码、构建产物和静态清单已经同步到当前插件目录，需在服务重载后进行真实微信任务验收。

该路由保持开启，用于真实场景验收。迁移到其他设备时，使用同一 Chub 插件源码构建并同步插件产物与清单，在目标设备的 OpenClaw 中配置固定 Chub Tailnet 地址并启用该开关后重启 Gateway；微信重新绑定后，再验证新账号与当前发送者的 Context Token 持久化和完整任务链路。Chub 提交入口仅接受与 Chub 同节点、通过真实 Tailnet socket 发起的 OpenClaw 请求；跨节点部署需要另行设计明确的调用方身份边界，不能直接放宽给整个 Tailnet。

#### 3.2.1 当前双开关与启停方式

当前路由有两层开关，但二者职责不同：

- OpenClaw 插件配置 `plugins.entries.chub.config.wechatChubStatusMode` 是部署级路由能力开关。名称为兼容当前已验收部署而保留，不再表示仅做状态检查。关闭后 `before_dispatch` 不读取 Chub 状态，微信私聊直接保留原有 Agent / LLM 流程；开启后才在每条微信私聊进入模型调度前检查状态并提交任务。
- Chub 配置 `openclaw.weixin_chub_mode.enabled` 是业务模式开关。OpenClaw 路由开关开启但 Chub 返回 `disabled` 时，插件不拦截消息，仍继续原有 Agent / LLM 流程。

当前 OpenClaw Control UI 可以展示插件加载状态和 Tool，但 OpenClaw `2026.7.1-2` 尚未在控制台中展示该插件的自定义 `wechatChubStatusMode` 配置项。插件声明配置 Schema 只表示 OpenClaw 可以校验该字段，不表示 Control UI 一定提供编辑控件。当前必须使用 OpenClaw CLI 管理该开关，不直接编辑 `openclaw.json`，也不通过禁用整个 `chub` 插件代替；禁用整个插件会同时关闭已有的 Chub 状态 Tool 和通知 Tool。

只读取非敏感的路由开关：

```bash
openclaw config get \
  plugins.entries.chub.config.wechatChubStatusMode \
  --json
```

启用或关闭路由：

```bash
# 启用
openclaw config set \
  plugins.entries.chub.config.wechatChubStatusMode \
  true --strict-json

# 关闭
openclaw config set \
  plugins.entries.chub.config.wechatChubStatusMode \
  false --strict-json

openclaw config validate
openclaw gateway restart --json
```

修改后必须确认 Gateway 已产生新进程并恢复连接，再检查插件运行时已经加载 `before_dispatch`：

```bash
openclaw gateway status --json
openclaw plugins inspect chub --runtime --json
```

不要为排障直接输出整个 `plugins.entries.chub.config`，其中包含本机固定 Tailnet 地址。Chub 侧开关不属于 OpenClaw 配置，不能通过 OpenClaw CLI 或 Control UI 修改；当前在本机 `config/settings.local.yaml` 中配置，并通过 `scripts/chub-web-restart` 重启 Chub 后生效。当前阶段尚未提供 Chub 页面开关。

两层开关的固定行为如下：

| OpenClaw 路由开关 | Chub 模式开关与状态 | 微信私聊行为 |
| --- | --- | --- |
| 关闭 | 任意 | 不读取 Chub，继续 OpenClaw Agent / LLM。 |
| 开启 | 关闭（`disabled`） | 不拦截，继续 OpenClaw Agent / LLM。 |
| 开启 | 开启且 `ready` | 提交 Chub 快速交互并返回 `handled`，不调用 Agent / LLM。 |
| 开启 | 开启但未就绪、不可达或响应无效 | 返回 `handled` 失败提示，不回退 Agent / LLM。 |

这不是两个长期并列的业务开关。OpenClaw 开关控制插件是否安装并接管该入口，Chub 开关表达日常业务状态；正式提交路由已直接沿用原配置键，没有并存第二个模式。日常启停由 Chub 统一控制。OpenClaw 仍可关闭该路由开关，或通过停用、卸载插件作部署级撤销；停用整个插件会影响其他 Chub 能力，不作为日常开关。

### 3.3 跨设备 OpenClaw 变更规范

Chub 插件的核心代码已经纳入本仓库，`integrations/openclaw/chub/` 是微信 Chub 模式路由的唯一源码。另一台设备上实际被 OpenClaw 加载的插件目录属于运行产物，不应在其中独立演进或成为唯一依据；迁移、修复和后续开发都先修改本仓库源码、测试和清单，再同步到已核实的运行目录。

这是一条强制维护规则：凡是因 Chub 功能产生的 OpenClaw 侧变更，都必须在同一次本仓库改动中包含可评审源码、静态配置清单、覆盖新行为的测试、插件说明和对应设计文档记录。构建生成的 `dist/` 用于部署且由源码重建，不作为唯一变更记录；实际 OpenClaw 插件目录不得直接修复后遗漏回写本仓库。部署同步只在仓库测试和清单校验通过后进行，并记录当前是否已同步、是否已重载以及是否完成真实场景验收。

当前正式提交路由的对应关系如下，迁移时不得另建脚本或复制实现。

| 仓库内容 | 迁移时的作用 |
| --- | --- |
| `integrations/openclaw/chub/src/index.ts` | `before_dispatch` 路由、微信私聊范围和 `handled` 语义。 |
| `integrations/openclaw/chub/src/client.ts` | 固定 Tailnet 地址校验，以及 Chub 状态和任务提交请求。 |
| `integrations/openclaw/chub/openclaw.plugin.json` | OpenClaw 静态配置清单，必须包含新增配置项。 |
| `integrations/openclaw/chub/src/index.test.ts` | 路由不进入 Agent 或 LLM 的行为验证。 |
| `integrations/openclaw/chub/README.md` | 构建、校验和当前提交路由的最小说明。 |

`openclaw.plugin.json` 与构建产物同等重要。OpenClaw 会按该静态清单识别插件配置；只同步 `dist/` 而没有同步清单时，新开关可能不会被识别，路由也不会按预期启用。因此每次调整插件配置或 Hook 后，必须同步源码构建产物和清单，并用运行时检查确认。

#### 3.3.1 本次 OpenClaw 侧变更记录（2026-08-10）

本次在正式任务提交基础上完成任务级原路回送，Chub 仓库已经同步记录以下内容：

- `before_dispatch` 在状态为 `ready` 时调用固定任务提交接口，并继续以 `handled` 阻止 Agent 与 LLM 调度；`disabled` 仍保留原流程，其他异常均失败关闭。
- 固定客户端新增有界 POST 请求、严格成功响应校验和受控错误映射，不信任服务端任意错误文本。
- 因当前 Hook 不提供微信平台原始消息 ID，插件使用固定上下文、毫秒时间戳和原始正文生成非敏感稳定摘要；缺少稳定时间戳时拒绝提交。
- Hook 从可信事件上下文提取 `accountId` 与私聊发送者路由随任务提交；优先读取 `senderId`，并兼容当前微信插件将 `OriginatingTo` 映射为 `conversationId` 的标准私聊上下文。缺少任一字段时失败关闭。提交超时窗口独立提高到 10 秒，降低已启动但即时响应超时的不确定性。
- Chub 提交前确认路由账号是唯一健康 ClawBot；任务持久化自己的不可变回送路由，完成时只向该账号与该发送者发送，不读取全局目标作为后备。
- 幂等状态只保存回送路由摘要；同一消息标识携带不同路由时返回冲突。原始账号和收件人只保存在权限为 `600` 的私有快速交互状态中，不进入接口响应、页面或操作日志。
- 沿用已部署的 `wechatChubStatusMode` 配置键，更新其正式路由语义，不新增并存开关。
- 源码、清单、插件 README 和自动化测试已更新；构建产物已同步到当前核实的 OpenClaw 插件目录。当前仍需重载 Gateway 并完成真实微信任务验收，验收前不标记完成。

在另一台设备迁移或让 AI 执行变更时，按以下顺序进行：

```text
1. 读取本设计、AGENTS.md、插件 README，以及微信 Context Token 补丁规范。
2. 执行 openclaw plugins inspect chub --json，确认实际启用的插件根目录；
   只向该已确认目录同步，不能猜测或修改其他扩展目录。
3. 在本仓库 integrations/openclaw/chub/ 中修改源码和测试，执行 npm test、
   npm run plugin:validate；通过后得到 dist/。
4. 将 dist/ 的内容和 openclaw.plugin.json 一起同步到已确认的插件根目录。
   未安装插件时，才从本仓库该目录执行 OpenClaw 的插件安装，并再次确认根目录。
5. 只配置固定 Chub Tailnet baseUrl、短 timeoutMs 和需要的模式开关；不写入 Hub Token，
   不接受微信正文提供地址、账号、收件人或权限，也不新增 Owner 映射配置。
6. 执行 openclaw config validate、重启 Gateway，再执行
   openclaw plugins inspect chub --runtime --json，确认配置项和 before_dispatch Hook
   均已被运行时识别。
7. 微信重新绑定后，用真实私聊验证；确认消息被 handled，且没有新的
   OpenClaw Agent / LLM 会话使用记录。
```

同步步骤中的路径应使用当前设备实际检出的项目目录和第 2 步确认的插件根目录，不将用户目录、主机名、Tailnet 地址、账号或凭证写入本文档或脚本。配置迁移后设置 `wechatChubStatusMode: true`，由 Chub 路由状态接口统一决定是否进入任务提交。需要撤销时，将该开关关闭并重启 Gateway，微信消息会恢复原有 Agent 流程。

微信出站 Context Token 持久化属于第三方 WeChat 插件的兼容补丁，按[微信 ClawBot Context Token 持久化 AI 补丁规范](WEIXIN_CLAWBOT_CONTEXT_TOKEN_AI_PATCH.md)及其中的参考 patch 单独维护。它不应合并进 Chub 插件源码；迁移验收时同时确认该补丁仍有效，确保 Chub 完成通知能够回送。

### 3.4 最终 Chub 可用快速校验接口

状态路由已不再使用通用 `/api/status`，而由 Chub 提供固定只读接口 `GET /api/openclaw/wechat-chub-mode/status`。它只接受真实 Tailnet socket 来源，不接受 Hub Token 作为替代认证，响应保持有界且不包含 Session ID、模型、工作区、任务正文或凭证：

```text
data.enabled = 是否在 Chub 中启用微信 Chub 模式
data.ready = 当前 Chub 任务接收链路是否就绪
data.code = ready | disabled | configuration_invalid | codex_unavailable
```

状态由 Chub 的 `openclaw.weixin_chub_mode.enabled` 集中控制，默认关闭；当前固定工作区由 `workspace_id` 配置。`ready` 表示模式已启用，固定工作区、权限、Codex、所选模型和完成通知能力可用；它不要求配置全局 `weixin_account_id` 或 `weixin_recipient`。状态查询本身不创建 Session、不启动 Codex，也不执行 OpenClaw 通道命令。提交接口会在受锁范围内复核条件，并通过本机 `openclaw channels status` 校验本次路由账号为唯一健康 ClawBot；校验失败时不创建 Session、不启动 Codex。

状态组合固定为：`disabled` 对应 `enabled=false, ready=false`；`ready` 对应两者均为 `true`；`configuration_invalid` 和 `codex_unavailable` 均对应 `enabled=true, ready=false`。接口自身不可用时使用 HTTP 失败响应，OpenClaw 将其视为已处理失败，不回退调用 LLM。任务提交接口会再次校验这些条件，不能只依赖此前的状态结果。

Chub 已提供固定任务入口 `POST /api/openclaw/wechat-chub-mode/submit`。它在 Tailnet 认证之外继续核对请求 socket 来源必须等于 Chub 当前节点地址，只供同机 OpenClaw 调用；Hub Token、转发 Header 和其他 Tailnet 节点均不能替代该限制。请求只接受以下有界字段，其他 Session、模型、权限、路径或命令字段直接拒绝：

```text
message_id = OpenClaw 生成的稳定非敏感消息标识，必填，用于幂等
prompt = 原始任务正文，必填
correlation_id = OpenClaw 提供的非敏感关联标识，可选
reply_account_id = before_dispatch 可信上下文中的本次 ClawBot 账号，必填
reply_recipient = 本次私聊发送者的微信通道标识，必填
```

成功响应只包含是否接受、是否为重复提交、是否新建专用 Session、固定状态码和有界提示，不返回 Session ID、任务 ID、任务正文、账号、收件人或配置。首次请求会先持久化预留记录，再创建或复用固定专用 Session 并提交既有快速交互；同一 `message_id` 且路由一致时只重放首次结果，路由不一致时返回冲突且不执行。状态文件异常时入口关闭，任务已启动但最终关联保存失败时也会明确返回“已启动但状态未保存”并阻止再次提交，避免误报成功和重复执行。

幂等记录是本机有界历史，按时间保留最近最多 5000 条且状态文件不超过 8 MiB；非常久远、已被淘汰的消息 ID 不再具备重放保证。幂等状态不保存任务正文或原始回送标识，只保存路由 SHA-256 摘要；私有快速交互状态保存任务级路由，以便服务重启后仍保持绑定，文件权限为 `600`。接口、页面模型和操作日志只展示安全的路由类型，不返回原始标识。

最终插件流程为：

```text
当前绑定微信账号的私聊
  -> before_dispatch
  -> 固定 Tailnet 调用微信 Chub 模式状态接口
       -> disabled：继续原有 OpenClaw Agent / LLM
       -> ready：提交微信专用快速交互并返回 handled
       -> 非 ready、不可达、超时或无效响应：返回 handled 失败提示，不调用 LLM
```

`wechatChubStatusMode` 只保留一个正式提交路由，不再并存原状态确认行为。

启用微信 Chub 模式后的完整流程如下：

```text
维护者在微信发送普通消息
  -> OpenClaw 识别当前已启用微信 Chub 模式
  -> 不启动 Agent、不调用 OpenClaw LLM
  -> 从 Hook 上下文取得本次账号与发送者，连同原始正文和稳定消息标识提交
  -> 立即回复“任务已提交”

Chub 接收任务
  -> 在内部锁定微信专用 Session
  -> 已有有效 Session：直接复用
  -> 没有有效 Session：按 Chub 固定配置创建一个专用 Session
  -> 复用既有快速交互后台执行
  -> Codex CLI 在微信专用 Session 中执行
  -> Chub 保存最终任务结果
  -> 复用既有微信完成通知
  -> openclaw message send 使用本任务保存的账号，发送给本次消息发送者
```

微信专用 Session 的标识只保存在 Chub 自己的持久化状态中。OpenClaw、微信消息和回送通知都不读取、保存或传入 Session ID，因此不会因 Gateway 重启、Session ID 过期或并发消息产生外部状态同步问题。Chub 在首次创建时使用其固定的微信模式配置决定工作区、权限、模型和推理等级；微信消息只能提供任务正文，不能指定这些参数。

模型或推理等级未显式配置时，`null` 表示仅在首次创建 Session 时采用 Codex 当时的默认值；Session 创建后记录的实际模型仍视为有效，后续任务和服务重启都继续复用该 Session，不因默认值解析结果与 `null` 形式不同而重建。只有工作区、权限或显式指定的模型配置不再匹配，或 Session 已失效时才创建新 Session。

该 Session 长期复用，因此微信任务拥有连续的 Codex 上下文和任务历史。网页可以查看该 Session，但不承担它的创建和绑定。同一原生 Session 不能被多个 `codex exec resume` 或实时终端进程同时持有 writer，TUI 内部追问队列也不代表跨进程并行写入可用，因此 Chub 不为微信入口另建队列，也不并发启动第二个快速交互。

微信提交时按以下规则处理：已有快速交互或 Activity 明确为 `working` 时直接拒绝，不中断已确认的工作；Activity 为 `unknown` 时，仅对这个微信专用 Session 自动撤销页面票据、关闭终端连接、停止 ttyd/tmux，并确认 Session 已停止且原生 writer 已释放，然后无二次确认地提交快速交互；Activity 为 `idle` 但 writer 仍被实时终端持有时拒绝并提示先停止终端。自动停止只规范化 Chub 的运行载体，不删除原生 Codex Session、上下文或历史。停止失败、writer 未释放或 `resume` 启动时发生竞态冲突都作为本次微信任务失败，不转交 OpenClaw Agent。

Codex 当前没有公开 writer 状态查询接口。Chub 只读探测本机 `CODEX_HOME/thread-writer-locks/` 中对应原生 Session 的锁，不创建、不改写锁文件；该路径属于 Codex 内部兼容边界，升级 Codex CLI 后必须回归验证。即使预检显示 writer 已释放，实际 `resume` 仍可能因竞态返回 `thread-store conflict`，Chub 会将其归一为“Session 正在由其他进程使用”，不会把底层错误日志直接回送微信。

微信入口在任务提交成功后不查询任务状态。任务完成后的成功、失败或超时结果由 Chub 已有的异步完成通知直接推送；任务启动不等于任务成功。即使用户在执行期间切换绑定或提交其他消息，已经启动的任务仍只使用提交时保存的路由；该路由缺失、账号停止或投递失败时标记通知失败，不改投全局收件人，也不自动重试。

### 3.5 重复消息与 Session 就绪

当前 OpenClaw `before_dispatch` Hook 没有暴露微信平台原始消息 ID。插件因此使用固定通道、账号、会话、发送者、微信消息毫秒时间戳和原始正文生成 SHA-256 幂等标识；只传递该摘要，以及回送所必需的账号和发送者，不传会话键或会话标识原文。缺少稳定时间戳或回送字段时直接拒绝提交且不回退 LLM。该方式能覆盖同一消息的 Gateway 或 Chub 重投；若后续 Hook 正式提供平台消息 ID，应改为直接使用该不透明 ID，减少对组合字段的依赖。

Chub 保存“稳定消息标识 + 回送路由摘要 -> 快速交互任务或首次失败原因”的关联：首次消息创建任务，重复投递且路由一致时只返回同一任务或同一失败原因；同一标识关联不同路由时拒绝冲突，绝不再次启动 Codex。微信正常情况下不应重复投递，但该关联用于覆盖 Gateway 或 Chub 重启等异常重复消息。

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
  -> 本任务的 ClawBot / 本次消息发送者
```

因此不需要新增任务状态查询、OpenClaw 轮询或任务队列，OpenClaw 也不需要持有固定 Session ID。每次私聊入站时，微信插件按账号与发送者持久化 Context Token；任务完成后 Chub 使用该任务保存的同一账号与发送者回送，Gateway 重启后仍按这对标识恢复出站上下文。网页发起的普通快速交互使用全局 `weixin_recipient`；`weixin_account_id` 默认保持 `null` 并动态选择唯一健康 ClawBot，仅保留兼容性覆盖能力。微信入站任务绝不使用该全局路由兜底。完成通知独立记录发送中、已发送、失败或跳过；微信投递失败不会改变 Codex 任务最终状态，也不自动重试。`unknown` 自动回收单独记录 `requested`、`started`、`succeeded` 或 `failed` 操作日志。快速交互自身要求重启 Chub 时，复用通用延迟重启机制，先保存结果并结束本次微信通知，再等待节点上其他快速交互全部结束；待重启期间拒绝新任务。自动重启成功后的系统回复只追加到 Chub 快速交互时间线，不额外发送第二条微信通知；手动重启只清理待重启状态，不展示该回复。

仅当前绑定账号的私聊消息可进入该模式。单 Owner 绑定确定入口身份范围；Chub 不新增 Owner 配置或身份映射，但会保存 Hook 提供的发送者作为回送目标。群聊、回送路由缺失、多个健康 ClawBot 或模式未启用时不提交任务。通道控制只保留固定的状态、停止和退出模式操作，且不进入 Codex。

## 5. 启用规则与必要约束

微信 Chub 模式默认关闭。维护者在 Chub 中明确启用后，当前绑定账号发送的普通私聊消息即视为调用 Chub 的微信专用快速交互入口；这不是 OpenClaw Agent 的工具调用，也不会产生 OpenClaw LLM 调用。

OpenClaw 插件本地只保存“此微信入口使用 Chub 路由”的固定连接配置，不保存模式启停结果；每条消息以短超时读取 Chub 路由状态。只有 Chub 明确返回“未启用”才进入原有 Agent 流程，任何连接或状态异常均保持在 Chub 模式失败路径。这样既允许维护者在 Chub 侧集中启停，也不会因可信网络或 Chub 故障而意外切换到模型处理。

微信专用 Session 使用 Chub 为该模式固定的工作区、权限、模型和推理等级。`Ask for approval` 不允许启用此模式。维护者已批准在固定 Tailnet、单一微信 Owner、单一微信专用 Session 的范围内使用 `Full access` 直接执行，不要求逐条消息确认；该例外只适用于微信 Chub 模式，关闭模式即可停止该入口，不能扩展给其他 Agent、账号、入口或 Session。

启用前 Chub 展示并校验工作区、权限、模型和推理等级；需要暂停入口时直接关闭微信 Chub 模式。模式关闭后，新消息恢复原有 OpenClaw Agent 流程，已提交的 Codex 任务不因此取消。

Chub 只接受该入口固定调用的专用字段：原始任务正文、微信消息 ID、非敏感关联标识，以及 Hook 提供的账号和发送者回送路由。任务正文会原样交给 Codex，但接口不接受独立的系统命令、文件路径、Session、模型、权限或任意目标地址。每项任务保留关联标识、Chub 操作 ID、微信专用 Session、快速交互任务 ID、固定目标节点、最终状态和回送状态；原始回送标识只保存在私有任务状态中，日志和 API 不记录或返回。

微信 Chub 模式复用现有完成通知与本地 Context Token 持久化能力：只允许本次任务绑定的 ClawBot、本次消息发送者和 `openclaw message send`，不得调用 `openclaw agent`，也不得把通知能力用于触发新的 Chub 操作。网页来源快速交互使用全局固定接收人，账号默认选择唯一健康 ClawBot；两类路由不可互相兜底。

## 6. 验收方向

1. 模式关闭时微信消息继续进入原有 OpenClaw Agent；模式开启且 Chub 就绪时确认 `before_dispatch` 已处理消息，OpenClaw 没有模型调用。
2. 启用前验证工作区、Codex、模型、推理等级和权限；`Ask for approval` 或任一依赖不可用时确认模式不能启用，并确认 `Full access` 仅作用于已批准的微信专用 Session。
3. 启用后从微信发送首条任务，确认 Chub 只创建一个微信专用 Session 和一个快速交互任务。
4. 重复投递同一微信消息、并发发送首条消息、重启 Gateway 或 Chub 后重复投递，确认始终只执行一个 Codex 任务；首次提交失败后重复消息只返回首次失败原因，不自动重试。
5. 分别验证专用 Session 为 `working`、已有快速交互、`unknown`、`idle + writer`：前两种和 `idle + writer` 明确拒绝；`unknown` 自动关闭页面访问并停止残留终端，writer 释放后只提交一个任务，失败时不尝试并行 `resume`。
6. 确认有效专用 Session 被复用；Session 失效后只创建一个新 Session，并在即时回复和操作日志中明确上下文已重建。
7. 确认微信专用 Session 的上下文连续，任务最终结果与 Chub 页面一致。
8. 验证插件只访问固定 Chub Tailnet 地址，Chub 仅接受真实 Tailnet socket 来源且忽略转发地址 Header；确认入站消息已按本次账号与发送者持久化 Context Token，正常完成的任务原路回送。切换绑定账号后再次发送，确认无需修改 Chub 全局收件人；旧任务若仍在运行，只使用旧任务路由且失败时不改投新账号。
9. 覆盖执行中重复消息、相同消息 ID 不同路由冲突、多个健康 ClawBot、停止、超时、Chub 不可达、路由状态无效和微信回送失败；确认不自动重试、不回退全局目标，也不回退调用 LLM。
10. 验证群聊、缺少 Hook 路由及任意 Session/模型/权限/目标注入均不进入 Chub 任务，且不触发 Codex。
11. 完成 macOS、Ubuntu 和真实微信的端到端验收，再决定是否将该模式作为长期入口。
