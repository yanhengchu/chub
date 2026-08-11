# OpenClaw–Chub 集成与 ClawBot 消息调度设计

> 状态：微信 ClawBot 单接口调度 v2 已完成仓库实现、自动化验证和 macOS 真实文本/语音链路验收，Ubuntu 待配套部署验收。本文只维护当前 OpenClaw、Chub 插件、微信专用任务、完成通知和安全边界。

## 1. 当前架构

Chub 与 OpenClaw 当前提供四类能力：

1. Chub 管理本机 OpenClaw Gateway；首页展示 Gateway、消息通道和访问入口，Owner 检查结果合并到通道与总体状态中。
2. OpenClaw Agent 通过受限 Tool 查询 Chub 状态或发送飞书通知。
3. 微信 Chub 模式在模型调度前拦截符合条件的私聊，只调用 Chub 单一调度入口；普通内容由 Chub 提交 Codex 任务，后续固定查询和白名单任务仍在该入口内扩展。
4. Chub 快速交互完成后，通过本机 ClawBot 发送微信最终结果；任务摘要只是提交状态和最终消息中的关联信息。

```text
OpenClaw Agent 能力（TUI 或未进入微信 Chub 模式的消息）
  OpenClaw Agent -> chub_get_status | chub_send_notification -> Chub

微信 Chub 模式（当前实现）
  微信私聊 -> before_dispatch -> Chub 统一消息调度接口
           -> 模式检查 / 普通内容进入 Chub 快速交互 + Codex CLI
           -> 后续增量：固定查询 / 白名单任务
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
| Chub 模式就绪 | 固定配置、Codex、通知和同节点路由可用 |
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

当前采用可信单用户策略：一个微信通道账号、一个允许发送者和一个 Owner。扫码、发送者配对与 Owner 权限是三个独立步骤，不得批准未知请求。

普通 OpenClaw Agent 路径使用 Gateway 的 Shell 审批和文件边界：

- 当前电脑使用 `host=gateway`；只有明确指定已配对 Node 时才使用 Node。
- Shell 默认按白名单执行，未命中时审批，审批不可用或超时则拒绝。
- 不将 `bash`、`sh`、`zsh`、`python`、`node`、`osascript` 等通用解释器整体加入持久白名单。
- 无关任务不得读取凭证、密钥、密码库、系统钥匙串、浏览器登录数据或其他敏感路径。
- 对敏感数据的修改、移动、删除、轮换或外发必须先说明准确目标并取得明确确认。

这些是模型和应用约束，不是操作系统级隔离。如果允许其他微信身份或不可信输入访问，必须重新评估 Sandbox、独立 Agent 和文件工具权限。

微信 Chub 模式是单独批准的例外：固定 Tailnet 内的单一 Owner 可以通过固定专用 Session 使用 `Full access`，不逐条审批。该权限不属于 OpenClaw Agent，也不扩展到其他账号、入口或 Session；关闭微信 Chub 模式即可撤销入口。

## 4. Chub OpenClaw 插件

插件源码位于 `integrations/openclaw/chub/`，统一承载以下能力：

| 能力 | 用途 |
| --- | --- |
| `chub_get_status` | 查询当前设备的 Chub 健康和基础状态 |
| `chub_send_notification` | 向预先配置的飞书目标发送消息 |
| `before_dispatch` Hook | 只向 Chub 单一入口转发，不在插件内做业务路由 |
| 原文保护 Hook | 在明确要求原样发送时覆盖模型生成的通知正文 |

插件只使用固定 Tailnet `baseUrl`、固定 API 路径、严格 Schema、受控超时和有界响应，不配置 Hub Token，也不接受任意 URL、文件路径或命令。

仓库内 `integrations/openclaw/chub/` 是插件的唯一源码和发布来源。OpenClaw 不直接从该目录运行插件；安装时会把经过构建和验证的产物部署到当前用户的扩展目录，通常为 `~/.openclaw/extensions/chub/`，实际位置以 `openclaw plugins inspect chub --runtime --json` 返回的 `rootDir` 和 `source` 为准。运行目录只是可替换部署副本，不得直接修改后形成仓库外实现。

修改后必须先执行：

```bash
cd integrations/openclaw/chub
npm ci
npm run plugin:build
npm run plugin:validate
npm test
```

再从仓库插件目录执行标准部署：

```bash
openclaw plugins install "$PWD" --force
openclaw gateway restart
openclaw plugins inspect chub --runtime --json
openclaw channels status --probe --json
```

安装结果必须显示 `sourcePath` 指向 Chub 仓库插件目录、运行时 `source` 指向 OpenClaw 扩展目录中的构建产物、插件状态为 `loaded`，并确认受影响的微信账号恢复 `running`。协议升级不保留兼容层时，插件与 Chub 必须配套切换；切换窗口内版本不一致会按设计失败关闭，不得回退 Agent。

微信链路只调用一个固定接口：

```text
POST /api/openclaw/wechat-chub-mode/dispatch
```

请求固定携带协议版本 `2`，用 `content` 携带干净正文、用 `message_type` 区分可信的 `text` / `voice` 来源；响应只使用同版本、`pass` / `reply` 交付决定和 Chub 生成的有界 `message`，不暴露业务状态码。版本不匹配时失败关闭，插件只放行或逐字交付同步回执；Chub 独立负责同步业务文案以及异步最终结果和重启结果，双方不得重复发送同一阶段消息。

插件只负责部署开关、微信私聊范围、可信账号与发送者、原始正文、语音来源归一化、幂等标识、固定 Tailnet 地址、超时和有界响应。它不识别“查看结果”等指令，不检查 Codex 或 Session 状态，不生成或修改任务摘要、提交状态、语音回显和失败文案，也不让正文指定动作、接口、任务编号、Session、权限、模型、路径或命令。Chub 不可达、协议不匹配或响应无效时，插件只返回统一通道失败，不解释具体业务原因。

Chub 统一调度接口当前负责模式关闭时放行和普通快速交互提交。后续在同一 `dispatch` 接口内部增加“查看结果”等完全匹配的固定查询和批准后的白名单任务，不为每种指令增加新的插件接口。固定只读查询落地后直接读取当前账号与发送者关联的任务，不调用 Codex，Codex、模型或专用 Session 不可用也不能阻止查询。

插件使用 `weixinChubMode` 部署开关。Chub 与插件使用同一版本化调度协议；版本不匹配时明确失败，不回退 Agent。

统一协议、职责边界、构建部署和最小验收同时维护在插件源码目录的 `integrations/openclaw/chub/README.md`。

## 5. ClawBot 统一调度与专用任务

### 5.1 模式与路由

Chub 配置 `openclaw.weixin_chub_mode` 决定业务状态和专用 Session 默认值：

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
| 开启 | 已启用 | 普通内容提交 Codex 任务；后续由 Chub 增加固定查询和白名单任务路由 |
| 开启 | Chub 不可达或响应异常 | 返回受控失败，不回退 Agent / LLM |

当前不实现“查看结果”等固定查询，所有非空业务正文按普通任务提交规则处理。后续把“查看结果”作为首个固定只读路由加入同一个 `dispatch` 接口：只在正文完全匹配时生效，查询当前账号和发送者关联的最近微信任务，执行中返回状态和摘要，已经结束返回成功、失败或超时结果，没有历史则返回固定空状态。查询不创建快速交互、不占用 Codex writer，也不接受任务编号或 Session 参数。后续固定指令必须逐项加入 Chub 白名单路由，不能由插件或正文自由指定动作。

### 5.2 提交状态与任务摘要

普通任务受理后立即回复“任务已提交”、任务摘要和“完成后将原路发送结果”。提交状态只表示 Chub 已完成安全校验、幂等预留和后台任务建立，不代表任务成功；最终状态以快速交互结果为准。

任务摘要由 Chub 从首个有效句子确定性提取，统一为单行并限制在 48 个字符，不调用 Agent 或 LLM。摘要过滤内部语音标记、控制字符并遮蔽明显的 Bearer、Token、Authorization、Cookie、Secret、Password 和 Webhook 值；生成后随任务持久化，提交状态、最终结果、分段结果和重启结果复用同一份摘要。

可信微信语音任务在首次成功回执末尾追加有界识别原文，便于核对转写；普通文字、重复消息和失败回执不追加。语音转写进入任务正文时保持干净，不把内部来源标记交给 Codex。该回显及长度控制由 Chub 生成，插件只传递可信类型并逐字交付 Chub 的完整回执。

### 5.3 语音转写可信来源

腾讯微信插件可能把带转写的语音消息转换为普通正文，导致 Chub 插件无法仅凭正文区分语音转写和用户键入文字。微信通道适配必须保留可信来源，并满足：

1. 只有上游消息项类型确认为语音且包含非空转写时才设置内部来源标记。
2. 进入任务和原 OpenClaw Agent 流程的正文始终是干净转写；内部标记只用于 Hook 识别，不进入任务正文、摘要、日志或对外接口。
3. Chub 插件只有同时取得可信标记和不同的干净正文时才向 Chub 发送 `message_type: voice`；用户键入相同标记仍按 `text` 处理。
4. Chub 决定识别原文只在普通任务首次提交成功回执中显示；固定查询、重复消息、提交失败和普通文字不追加，插件不参与判断或拼接。
5. Chub 将整条提交回执限制为最多 3000 个字符，超出时只截断识别原文并明确提示；插件只验证响应边界并原样交付。

微信插件升级、重装或实际加载目录变化后，先用 `openclaw plugins inspect openclaw-weixin --json` 确认运行来源。若上游已提供可信语音来源和干净转写，应直接适配上游字段；否则按 `integrations/openclaw/patches/weixin-clawbot-voice-transcript-origin.patch` 恢复等价能力。应用前必须 dry-run，同时维护仓库参考补丁和实际构建产物，不得只修改运行目录。完成后重载 Gateway，并分别验证普通文字、真实短语音、普通文字伪造标记、重复语音和模式关闭场景。

### 5.4 信任与权限

当前部署固定为同节点 Chub 与 OpenClaw、单一微信 Owner、单一健康 ClawBot：

- 统一调度接口只接受来自 Chub 当前节点真实 Tailnet socket 的请求，不接受 Hub Token 或转发 Header 替代。
- 微信消息只能提供正文，不能指定 Session、工作区、权限、模型、推理等级、文件路径、命令、接口或回送目标。
- 回送账号和发送者只能来自 `before_dispatch` 的可信通道上下文，并在普通任务提交前确认对应账号是唯一健康 ClawBot。
- 群聊、缺少稳定消息标识、缺少回送路由或路由不可信时不进入 Chub 任务。
- 微信专用 Session 的 `Full access` 例外只适用于固定 Tailnet、当前单 Owner 和该专用 Session；关闭业务模式即撤销入口，不扩展给其他 Agent、身份、入口或 Session。

增加第二个 Owner、多个并行 ClawBot 或跨节点提交前，必须重新设计身份认证、Session 隔离和调用方边界，不能直接放宽当前规则。

### 5.5 Session 与并发

普通微信任务长期复用一个 Chub 管理的专用 Session，以保留 Codex 上下文。Session ID 只保存在 Chub 私有状态中，OpenClaw 和微信消息不能读取或指定。

- 没有有效 Session 时按固定配置创建；模型或推理等级为 `null` 时仅在首次创建时跟随 Codex 默认值。
- 同一原生 Codex Session 同时只允许一个 writer，不维护额外消息队列。
- 已有快速交互、明确执行中或 writer 仍被占用时拒绝新的普通任务，不中断现有任务；后续落地的“查看结果”等只读路由不受此限制。
- 专用 Session 状态为 `unknown` 时，Chub 可以撤销页面票据、停止残留终端，并在确认 writer 释放后提交；失败时保持关闭，不并行 `resume`。
- Writer 探测依赖 `CODEX_HOME/thread-writer-locks/` 的只读兼容边界，Codex CLI 升级后必须回归。

### 5.6 幂等与状态

OpenClaw Hook 当前不提供微信平台原始消息 ID。插件使用固定通道上下文、账号、会话、发送者、时间戳和原始正文生成 SHA-256 消息标识；缺少稳定时间戳时失败关闭。

Chub 在产生副作用前持久化统一调度预留：

- 相同消息标识和相同路由只重放首次调度结果，不重复执行 Codex 或白名单任务。
- 相同消息标识携带不同路由时返回冲突。
- 首次失败会被记录，重复消息不自动重试。
- 服务重启时未完成的副作用预留转为固定失败，避免不确定状态下重复执行。
- 后续“查看结果”等只读路由允许重复查询，但同一平台消息的重复投递仍返回一致响应。

幂等历史最多保留 5000 条且不超过 8 MiB，只保存回送路由摘要，不保存任务正文或原始账号。完整任务和任务级路由保存在权限为 `600` 的快速交互私有状态中；接口、页面和操作日志不返回原始路由。

统一调度、任务提交、Session 回收、Codex 终态和微信通知分别记录自己的状态。消息被拦截、HTTP 返回成功或后台任务建立都不能代替任务和通知的最终结果。

## 6. 微信完成通知

快速交互完成通知只在任务成功、失败或超时后发送结果。普通结果完整单条发送，不附加页面跳转提示；超过 `max_message_chars` 时优先按段落编号分段，最多 5 条，超过总量才截断并提示到快速交互页面查看。整批通知共用一个超时，全部送达才记录为已发送；中途失败记录已送达数量并停止，不自动重试。通知状态独立记录为发送中、已发送、失败或跳过，通知故障不改变任务最终状态。

快速交互触发 Chub 延迟重启时，任务完成结果必须先进入通知终态。新实例通过本机健康接口确认恢复后，只有微信 Chub 来源任务会按任务保存的账号和发送者再发送一条固定重启结果；页面来源只更新快速交互时间线。重启结果通知使用独立的至多一次状态，发送中再次中断时不自动重试，失败不改变任务或重启结果，也不切换到全局目标。

两类路由严格隔离：

- 页面来源使用 `openclaw.quick_interaction_completion.weixin_recipient`；账号默认选择唯一健康 ClawBot，`weixin_account_id` 只作为兼容性覆盖。
- 微信 Chub 任务保存本次 Hook 提供的账号和发送者，完成时只按该路由回送。

微信任务的路由缺失、账号停止或投递失败时不切换到全局目标。收件人需要先主动向对应 ClawBot 发送消息，使微信插件获得该账号与收件人的 Context Token。当前按约 10 分钟有效的实测口径维护；超过该时间没有新的微信入站消息时，主动通知可能失败，出站消息不会续期，用户再次向同一 ClawBot 发送消息后才会刷新。该时间不是上游公开承诺的精确 TTL。

八分钟提醒依赖后续“查看结果”路由，当前不实现。查询路由完成后，再增加任务执行约 8 分钟仍未结束时至多原路发送一次的提醒，引导用户稍后发送“查看结果”。该提醒不续期、不自动重试，也不改变任务状态。用户发送“查看结果”后，新的微信入站先刷新 Context Token，再进入同一个统一调度接口：任务执行中直接回复状态，任务已经结束则回复有界终态结果；整个查询过程不创建快速交互、不占用 Codex writer。

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

当前链路使用 Chub 私有状态把非敏感消息标识、OpenClaw 会话关联、Chub 操作 ID、快速交互任务和通知终态串联起来；OpenClaw 与 Chub 的独立运行日志仍主要依赖时间窗口交叉定位。单 Session 场景可以排障，后续引入并发前应让两侧日志共同记录同一个非敏感追踪标识和 `text` / `voice` 类型，不记录正文、完整账号、完整收件人、Session Key 或 Context Token。

| 现象 | 优先检查 |
| --- | --- |
| Gateway 正常但微信无回复 | `channels status --probe` 检查运行状态，再看通道发送日志并做真实微信收发；不要只看 `lastOutboundAt` |
| 微信只收到统一通道失败 | 检查 Chub 可达性、插件与 Chub 协议版本、插件运行时来源和 Hook |
| 旧设备仍显示微信信息 | ClawBot 是否已在另一台设备重新绑定 |
| 微信 Chub 消息进入 Agent | 插件路由开关、Chub 模式状态和运行时 Hook |
| 微信 Chub 任务被拒绝 | 专用 Session 是否执行中、writer 是否占用、路由账号是否唯一健康 |
| 完成通知失败 | 任务保存的账号与发送者、Context Token 和当前通道状态 |
| 飞书正文被改写 | `content_mode`、原文 Hook 和当前 `runId` 关联 |

涉及插件、微信通道、权限或通知行为的变更，至少执行相应静态测试和配置校验，并在受影响平台做一次真实最终结果检查。Gateway、微信绑定、Owner、状态 Tool 和飞书通知已完成 macOS、Ubuntu 核心验收；微信 Chub 单入口 v2 已完成 macOS 真实文本、真实语音、同步提交状态和异步最终结果验收，Ubuntu 仍待配套部署验收。

## 9. 后续边界

指定人员飞书提醒、多 Owner、多 ClawBot、跨节点微信提交、连续电脑交互、自动事件通知和更多 Chub Tool 均由真实需求单独设计。不得因当前 Owner、Tailnet 或微信 Chub `Full access` 例外自动扩大其他入口权限。

“多个 Session 并行处理微信任务”保留为低优先级待评估项。当前单专用 Session 在长任务执行期间会拒绝新的普通任务，但现阶段使用频率和收益不足以立即扩展。只有并发需求稳定出现后再评估一个长期主 Session 加受控独立工作槽位；不直接采用多个 `Full access` Session 自动轮询。后续设计必须同时解决上下文归属、共享工作区写冲突、任务与微信结果关联、并发上限、幂等、停止与重启协调和权限例外边界。
