# Chub–OpenClaw 接入设计

> 状态：持续维护。本文描述当前微信 ClawBot 单入口调度、微信专用任务、完成通知和安全边界；具体能力状态与插件部署状态分别以能力清单和插件说明为准。Codex 状态路由的 Session 列表扩展已在当前节点完成真实微信验收，Ubuntu 尚待同步。

当前可用插件、插件能力、固定 API 和消息路由统一登记在[Chub 集成能力清单](CHUB_INTEGRATION_CAPABILITIES.md)；本文只解释其架构与行为边界。

## 1. 当前架构

Chub 与 OpenClaw 当前提供四类能力：

1. Chub 管理本机 OpenClaw Gateway；首页展示 Gateway、消息通道和访问入口，Owner 检查结果合并到通道与总体状态中。
2. OpenClaw Agent 通过受限 Tool 查询 Chub 状态或发送飞书通知。
3. 微信 Chub 模式在模型调度前拦截符合条件的私聊，只调用 Chub 单一调度入口；固定路由直接返回结果且不创建 Codex 任务，其他非空业务正文按普通任务处理。
4. 微信 Chub 任务结束后通过任务保存的 ClawBot 路由发送最终结果；页面快速交互只有在完成通知和全局收件人均已配置时才发送微信结果。任务摘要只是提交状态和最终消息中的关联信息。

```text
OpenClaw Agent 能力（TUI 或未进入微信 Chub 模式的消息）
  OpenClaw Agent -> chub_get_status | chub_send_notification -> Chub

微信 Chub 模式（当前实现）
  微信私聊 -> before_dispatch -> Chub 统一消息调度接口
           -> 模式检查 / 固定只读路由 / 普通任务提交
           -> 普通任务进入 Chub 快速交互 + Codex CLI
           -> Hook 同步原路交付 Chub 回执
           -> 任务结束后由 Chub 使用 openclaw message send 发送最终通知

页面快速交互通知
  Chub 快速交互 -> openclaw message send -> 全局固定微信收件人
```

微信 Chub 模式的同步调度回执由 Hook 原路返回；异步最终结果、微信任务重启结果和页面完成通知使用 `openclaw message send` 投递。两条路径都不调用 `openclaw agent`，也不得借此触发新的设备操作。Chub 直接发送飞书则调用自身 Notification Service，不经过 OpenClaw。

以下状态必须独立判断：

| 状态 | 含义 |
| --- | --- |
| Gateway 正常 | 后台服务、进程、端口和 RPC 正常 |
| Channel 正常 | 微信插件和本地通道进程正常 |
| ClawBot 已绑定 | 微信服务端当前仍绑定这台 Gateway |
| Owner 已配置 | 指定微信身份具有 Owner 权限 |
| Chub 模式就绪 | 固定配置、工作区、Codex、模型和完成通知开关满足启动条件；实际 ClawBot 路由在任务提交时校验 |
| 任务或 Tool 成功 | 已取得目标能力的最终结果 |

同一个 ClawBot 同时只能绑定一台 Gateway。在另一台设备重新扫码后，旧设备可能仍保留本地 Channel 和 Owner 信息，最终状态以真实微信收发为准。

## 2. 安装与状态

macOS 使用 launchd，Ubuntu 使用 systemd user service。OpenClaw 可通过官方安装脚本或已有 Node.js 环境安装；初始化后由 OpenClaw 自身管理 Gateway 服务：

```bash
openclaw --version
openclaw doctor
openclaw onboard --install-daemon
openclaw gateway status --json
openclaw gateway probe
```

安装并启用微信插件：

```bash
openclaw plugins install "@tencent-weixin/openclaw-weixin"
openclaw config set plugins.entries.openclaw-weixin.enabled true
openclaw gateway restart
openclaw channels login --channel openclaw-weixin
```

最后一条命令生成绑定二维码；Chub 首页“OpenClaw 环境”卡片也使用同一固定命令提供二维码和验证码流程。绑定只建立消息通道，不自动完成发送者配对或 Owner 授权。

常用只读检查：

```bash
openclaw gateway status --json
openclaw gateway probe
openclaw channels status --probe --json
openclaw config get commands.ownerAllowFrom
openclaw exec-policy show
openclaw config validate
```

Chub 首页卡片只提供 Gateway、消息通道、Tailscale 访问入口、固定启停、重启和微信绑定，不单独展示 Owner 身份或数量，也不提供安装、升级、任意命令、配置正文或原始日志。Owner 未配置或检查失败会进入通道提示和总体“功能受限”状态。卡片刷新失败时保留最近成功内容；卡内操作以最终状态结束，不把子进程创建视为成功。

## 3. 身份与权限

当前采用可信单用户策略：一个健康 ClawBot 和一个获准的微信 Owner 发送者。扫码、发送者配对与 Owner 权限是三个独立步骤，不得批准未知请求。

普通 OpenClaw Agent 路径使用 Gateway 的 Shell 审批和文件边界：

- 当前电脑使用 `host=gateway`；只有明确指定已配对 Node 时才使用 Node。
- Shell 默认按白名单执行，未命中时审批，审批不可用或超时则拒绝。
- 不将 `bash`、`sh`、`zsh`、`python`、`node`、`osascript` 等通用解释器整体加入持久白名单。
- 无关任务不得读取凭证、密钥、密码库、系统钥匙串、浏览器登录数据或其他敏感路径。
- 对敏感数据的修改、移动、删除、轮换或外发必须先说明准确目标并取得明确确认。

这些是模型和应用约束，不是操作系统级隔离。如果允许其他微信身份或不可信输入访问，必须重新评估 Sandbox、独立 Agent 和文件工具权限。

微信 Chub 模式是单独批准的例外：固定 Tailnet 内的单一 Owner 可以通过微信通道当前绑定 Session 使用 `Full access`，不逐条审批。该权限随受控绑定关系生效，不属于 Session 的永久属性，也不扩展到其他账号或入口；关闭微信 Chub 模式即可撤销入口。

## 4. Chub OpenClaw 插件

插件源码位于 `integrations/openclaw/chub/`，负责 OpenClaw Agent 的受限 Chub Tool、微信私聊转发和飞书通知原文保护。
当前能力名称和状态只在[Chub 集成能力清单](CHUB_INTEGRATION_CAPABILITIES.md)中统一登记；插件协议、构建、部署、最小验收和协议升级同步清单以[Chub OpenClaw 插件说明](../integrations/openclaw/chub/README.md)为准。

插件只使用固定 Tailnet `baseUrl`、固定 API 路径、严格 Schema、受控超时和有界响应，不配置 Hub Token，也不接受任意 URL、文件路径或命令。飞书原文保护的会话访问必须显式开启，只用于按当前 `runId` 取得用户明确提供的通知原文，不改变微信 Chub 消息调度权限。

仓库内 `integrations/openclaw/chub/` 是插件的唯一源码和发布来源。OpenClaw 运行经过构建验证后安装到用户扩展目录的部署副本，不得直接修改运行副本形成仓库外实现。协议不兼容变更时，插件与 Chub 必须配套切换；版本不一致时失败关闭，不回退 Agent。

微信链路只调用一个固定接口：

```text
POST /api/openclaw/wechat-chub-mode/dispatch
```

请求固定携带协议版本 `3`，用 `content` 携带干净正文、用 `message_type` 区分可信的 `text` / `voice` 来源；响应只使用同版本的 `pass` / `reply` / `handled` 交付决定和 Chub 生成的有界 `message`，不暴露业务状态码。`handled` 仅表示 Chub 已完成本次同步路由、插件无需额外回复，不代表后台通知已经送达。版本不匹配时失败关闭，插件只执行交付决定；Chub 独立负责业务判断、同步文案以及异步最终结果和重启结果，双方不得重复发送同一阶段消息。

插件只负责部署开关、微信私聊范围、可信账号与发送者、原始正文、语音来源归一化、幂等标识、固定 Tailnet 地址、超时和有界响应。它不识别业务指令，不检查 Codex 或 Session 状态，不生成或修改任务摘要、提交状态、语音回显和失败文案，也不让正文指定动作、接口、任务编号、Session、权限、模型、路径或命令。Chub 不可达、协议不匹配或响应无效时，插件只返回统一通道失败，不解释具体业务原因。

Chub 统一调度接口处理模式关闭时放行、固定只读路由和普通快速交互提交；已登记路由及匹配条件以[Chub 集成能力清单](CHUB_INTEGRATION_CAPABILITIES.md)为准。

插件使用 `weixinChubMode` 部署开关。Chub 与插件使用同一版本化调度协议；版本不匹配时明确失败，不回退 Agent。

## 5. ClawBot 统一调度与绑定任务

### 5.1 模式与路由

Chub 配置 `openclaw.weixin_chub_mode` 决定业务状态和当前绑定 Session 的匹配条件：

```yaml
openclaw:
  weixin_chub_mode:
    enabled: false
    workspace_id: "chub"
    permission_mode: "full-access"
    model: null
    reasoning_effort: null
```

插件配置 `weixinChubMode` 只控制 OpenClaw 是否启用统一转发，Chub 配置控制业务模式。当前行为为：

| 插件路由 | Chub 模式 | 微信私聊行为 |
| --- | --- | --- |
| 关闭 | 任意 | 保持原 OpenClaw Agent / LLM 流程 |
| 开启 | 关闭 | 调用统一接口，由 Chub 返回放行 |
| 开启 | 已启用 | Chub 执行已登记的固定路由，其他非空正文提交普通任务 |
| 开启 | Chub 不可达或响应异常 | 返回受控失败，不回退 Agent / LLM |

Chub 先判断是否命中能力清单登记的固定路由：命中时直接返回结果，不创建 Codex 任务；未命中的非空正文才按普通任务提交。具体短语、顺序和响应行为只在[能力清单](CHUB_INTEGRATION_CAPABILITIES.md)维护。消息正文不能提供任意 Session ID、任务编号、动作或目标接口；只允许固定 `codex switch` 路由使用实时列表编号切换绑定。

Codex 状态路由按内部 Session ID 稳定排序，只展示符合当前配置且允许绑定的前 10 个 Session；`[Current]` 只标识后续普通任务的当前目标，不影响顺序。`Available` 和 `Busy` 均可绑定，`Unavailable` 不展示。查询本身不切换、创建、启动、停止或回收 Session；绑定切换由独立固定路由实时复检并原子写入，不依赖列表快照。切换时 Session 与用量读取共用一个 9 秒预算，成功后按 Codex 状态格式返回结果，以 `[Current]` 代替单独的成功文案，避免同步请求超时后才改变绑定。

### 5.2 提交状态与任务摘要

普通任务受理后立即回复“任务已提交”、任务摘要和“完成后将原路发送结果”。提交状态只表示 Chub 已完成安全校验、幂等预留和后台任务建立，不代表任务成功；最终状态以快速交互结果为准。

任务摘要由 Chub 从首个有效句子确定性提取，统一为单行并限制在 13 个字符；超出时保留前 12 个字符并追加省略号，不调用 Agent 或 LLM。摘要过滤内部语音标记、控制字符并遮蔽明显的 Bearer、Token、Authorization、Cookie、Secret、Password 和 Webhook 值；生成后随任务持久化，页面、提交状态、最终结果、分段结果和重启结果复用同一份摘要。

可信微信语音任务在首次成功回执末尾追加有界识别原文，便于核对转写；普通文字、重复消息和失败回执不追加。语音转写进入任务正文时保持干净，不把内部来源标记交给 Codex。该回显及长度控制由 Chub 生成，插件只传递可信类型并逐字交付 Chub 的完整回执。

### 5.3 语音转写可信来源

腾讯微信插件可能把带转写的语音消息转换为普通正文，导致 Chub 插件无法仅凭正文区分语音转写和用户键入文字。微信通道适配必须保留可信来源，并满足：

1. 只有上游消息项类型确认为语音且包含非空转写时才设置内部来源标记。
2. 进入任务和原 OpenClaw Agent 流程的正文始终是干净转写；内部标记只用于 Hook 识别，不进入任务正文、摘要、日志或对外接口。
3. Chub 插件只有同时取得可信标记和不同的干净正文时才向 Chub 发送 `message_type: voice`；用户键入相同标记仍按 `text` 处理。
4. Chub 决定识别原文只在普通任务首次提交成功回执中显示；重复消息、提交失败和普通文字不追加，插件不参与判断或拼接。
5. Chub 将整条提交回执限制为最多 3000 个字符，超出时只截断识别原文并明确提示；插件只验证响应边界并原样交付。

微信插件升级、重装或实际加载目录变化后，先用 `openclaw plugins inspect openclaw-weixin --json` 确认运行来源。若上游已提供可信语音来源和干净转写，应直接适配上游字段；否则按 `integrations/openclaw/patches/weixin-clawbot-voice-transcript-origin.patch` 恢复等价能力。应用前必须 dry-run，同时维护仓库参考补丁和实际构建产物，不得只修改运行目录。完成后重载 Gateway，并分别验证普通文字、真实短语音、普通文字伪造标记、重复语音和模式关闭场景。

### 5.4 信任与权限

当前部署固定为同节点 Chub 与 OpenClaw、单一微信 Owner、单一健康 ClawBot：

- 统一调度接口只接受来自 Chub 当前节点真实 Tailnet socket 的请求，不接受 Hub Token 或转发 Header 替代。
- 微信消息只能提供正文，不能指定 Session、工作区、权限、模型、推理等级、文件路径、命令、接口或回送目标。
- 回送账号和发送者只能来自 `before_dispatch` 的可信通道上下文，并在普通任务提交前确认对应账号是唯一健康 ClawBot。
- 群聊、缺少稳定消息标识、缺少回送路由或路由不可信时不进入 Chub 任务。
- `Full access` 例外只适用于固定 Tailnet、当前单 Owner 和微信通道当时的绑定关系；Session 本身仍是普通 Chub Session，解除绑定后不保留微信入口授权。

当前不支持第二个 Owner、多个并行 ClawBot 或跨节点提交，也不能通过放宽现有配置绕过身份认证、Session 隔离和调用方边界。

### 5.5 Session 复用与写入互斥

普通微信任务默认复用 Chub 保存的当前绑定 Session，以保留 Codex 上下文。Session ID 只保存在 Chub 私有状态中，不向 OpenClaw 或微信消息暴露；用户只能通过固定编号路由在实时可绑定列表中切换。

- 绑定不存在或配置不匹配时，在下一次普通任务提交时按固定配置创建并绑定；暂时忙碌或不可提交不触发新建。
- 切换只影响后续任务；原 Session 上的任务继续执行并使用任务保存的微信路由回送，因此不同 Session 可以并行执行微信任务。
- 同一原生 Codex Session 同时只允许一个 writer，不维护额外消息队列。
- 已有快速交互、明确执行中或 writer 仍被占用时拒绝新的普通任务，不中断现有任务。
- 当前绑定 Session 状态为 `unknown` 时，Chub 可以撤销页面票据、停止残留终端，并在确认 writer 释放后提交；失败时保持关闭，不并行 `resume`。
- Writer 探测依赖 `CODEX_HOME/thread-writer-locks/` 的只读兼容边界，Codex CLI 升级后必须回归。

任务状态检查按微信任务保存的原路账号和发送者关联全部 Session，不依赖当前绑定。多个任务执行中时汇总显示；同时存在通知失败的已结束任务时仍触发原路补发，避免被运行任务遮挡。

### 5.6 幂等与状态

OpenClaw Hook 当前不提供微信平台原始消息 ID。插件使用固定通道上下文、账号、会话、发送者、时间戳和原始正文生成 SHA-256 消息标识；缺少稳定时间戳时失败关闭。

Chub 在产生副作用前持久化统一调度预留：

- 相同消息标识和相同路由复用首次处理决定；已提交任务的重复投递返回固定重复提示，不重放含任务摘要的首次回执，也不重复执行 Codex 任务。
- 相同消息标识携带不同路由时返回冲突。
- 首次失败会被记录，重复消息不自动重试。
- 服务重启时未完成的副作用预留转为固定失败，避免不确定状态下重复执行。

幂等历史最多保留 5000 条且不超过 8 MiB，只保存回送路由摘要，不保存任务正文或原始账号。完整任务和任务级路由保存在权限为 `600` 的快速交互私有状态中；接口、页面和操作日志不返回原始路由。

统一调度、任务提交、Session 回收、Codex 终态和微信通知分别记录自己的状态。消息被拦截、HTTP 返回成功或后台任务建立都不能代替任务和通知的最终结果。

## 6. 微信完成通知

任务成功、失败或超时后进入完成通知流程。微信 Chub 任务使用任务保存的原路路由；页面快速交互只有在完成通知和全局收件人均已配置时发送，否则记录为跳过。普通结果在单条上限内完整发送，不附加页面跳转提示；超过 `max_message_chars` 时优先按段落编号分段，最多 5 条，超过总量才截断并提示到快速交互页面查看。整批通知共用一个超时，全部送达才记录为已发送；中途失败记录已送达数量并停止，不自动重试。用户之后发送固定任务状态检查短语时，可以显式重新登记该任务的后台原路通知。通知状态独立记录为发送中、已发送、失败或跳过，通知故障不改变任务最终状态。

快速交互触发 Chub 延迟重启时，任务完成结果必须先进入通知终态。新实例通过本机健康接口确认恢复后，只有微信 Chub 来源任务会按任务保存的账号和发送者再发送一条固定重启结果；页面来源只更新快速交互时间线。重启结果通知使用独立的至多一次状态，发送中再次中断时不自动重试，失败不改变任务或重启结果，也不切换到全局目标。

两类路由严格隔离：

- 页面来源使用 `openclaw.quick_interaction_completion.weixin_recipient`；账号默认选择唯一健康 ClawBot，`weixin_account_id` 只作为兼容性覆盖。
- 微信 Chub 任务保存本次 Hook 提供的账号和发送者，完成时只按该路由回送。

微信任务的路由缺失、账号停止或投递失败时不切换到全局目标。收件人需要先主动向对应 ClawBot 发送消息，使微信插件获得该账号与收件人的 Context Token。当前按约 10 分钟有效的实测口径维护；超过该时间没有新的微信入站消息时，主动通知可能失败，出站消息不会续期，用户再次向同一 ClawBot 发送消息后才会刷新。该时间不是上游公开承诺的精确 TTL。

当前微信插件需要持久化 Context Token，并在通道启动实例与出站模块实例隔离时支持磁盘惰性恢复；还必须保留本文件定义的语音转写可信来源。日常 Gateway 重启和 ClawBot 重新绑定不要求重复打补丁；微信插件升级、重装、安装目录重建或兼容性检查失败时，应重新识别上游能力并只恢复缺失部分。Context Token 规则见[微信 ClawBot Context Token 持久化 AI 补丁规范](WEIXIN_CLAWBOT_CONTEXT_TOKEN_AI_PATCH.md)。Chub 首页不得自动修改第三方插件、重启 Gateway 或发送探测消息。

## 7. 飞书通知

通知目标注册表和 Secret 保存在本机用户配置目录，权限分别为 `700` 和 `600`，不得提交到 Git。Chub 提供固定 CLI 和受保护 API：

```bash
chub notification validate
chub notification list
chub notification test --target <target>
chub notification send --target <target> --message <text>
```

- Codex PTY 和 Chub 快速交互直接使用 Chub CLI 或通知 API。
- OpenClaw TUI 及未进入微信 Chub 模式的 Agent 使用 `chub_send_notification`。
- 默认不提醒任何人；指定人员只能使用目标预配置的别名，`@所有人` 必须由用户明确要求且目标允许。
- 用户明确提供“消息内容”时使用 `verbatim`，插件按当前 `runId` 关联进入模型前的原文；无法取得可信原文时拒绝发送。
- 只有用户要求 AI 撰写、总结或改写时才使用 `generated`。
- 飞书返回接受只表示 Webhook 已接受请求，不表示群成员已读。
- 日志不记录正文、Webhook、Token、Authorization 或完整 Open ID。

## 8. 运维与故障判断

HTTP 200、进程创建、Tool Call 发起、微信进度提示或本地 Channel 正常都不能单独证明操作成功。Gateway 操作检查最终实例和健康状态；微信绑定检查真实收发；Chub 任务、通知和飞书发送分别使用各自最终状态。

`openclaw channels status --probe --json` 的 `lastOutboundAt` 当前可能在消息实际发送成功后仍为空，因此只能辅助判断通道配置和运行状态，不能作为送达凭据。Hook 同步回执优先查看微信通道的 `text sent OK`，异步最终结果查看 Chub 快速交互和微信通知终态；最后仍以用户实际收到消息为准。

当前链路使用 Chub 私有状态把非敏感消息标识、OpenClaw 会话关联、Chub 操作 ID、快速交互任务和通知终态串联起来；OpenClaw 与 Chub 的独立运行日志主要依赖时间窗口交叉定位，能够支持当前单 Session 场景排障。日志不记录正文、完整账号、完整收件人、Session Key 或 Context Token。

| 现象 | 优先检查 |
| --- | --- |
| Gateway 正常但微信无回复 | `channels status --probe` 检查运行状态，再看通道发送日志并做真实微信收发；不要只看 `lastOutboundAt` |
| 微信只收到统一通道失败 | 检查 Chub 可达性、插件与 Chub 协议版本、插件运行时来源和 Hook |
| 旧设备仍显示微信信息 | ClawBot 是否已在另一台设备重新绑定 |
| 微信 Chub 消息进入 Agent | 插件路由开关、Chub 模式状态和运行时 Hook |
| 微信 Chub 任务被拒绝 | 当前绑定 Session 是否执行中、writer 是否占用、路由账号是否唯一健康 |
| 完成通知失败 | 任务保存的账号与发送者、Context Token 和当前通道状态 |
| 飞书正文被改写 | `content_mode`、原文 Hook 和当前 `runId` 关联 |

涉及插件、微信通道、权限或通知行为的变更，至少执行相应静态测试和配置校验，并在受影响平台做一次真实最终结果检查。Gateway、微信绑定、Owner、状态 Tool、飞书通知和微信 Chub 单入口均已纳入验收基线；具体路由状态见[能力清单](CHUB_INTEGRATION_CAPABILITIES.md)，插件的平台部署状态见[插件说明](../integrations/openclaw/chub/README.md)。
