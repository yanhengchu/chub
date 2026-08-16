# Chub–OpenClaw 接入设计

> 状态：持续维护。
>
> 本文只维护 Chub 与 OpenClaw/微信 ClawBot 的端到端业务、身份、权限、Session 路由和通知边界。当前能力名称以[集成能力清单](CHUB_INTEGRATION_CAPABILITIES.md)为准；插件协议、构建和部署以[Chub OpenClaw 插件说明](../integrations/openclaw/chub/README.md)为准。

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

## 2. 运行状态与管理入口

以下状态相互独立：

| 状态 | 含义 |
| --- | --- |
| Gateway 正常 | 服务、进程、端口和 RPC 可用 |
| Channel 正常 | 微信插件和本地通道进程可用 |
| ClawBot 已绑定 | 微信服务端当前绑定这台 Gateway |
| Owner 已配置 | 指定微信身份具有 Owner 权限 |
| Chub 模式就绪 | 固定配置、Codex、工作区和通知条件满足 |
| 任务或通知成功 | 对应业务已经到达独立终态 |

同一个 ClawBot 同时只能绑定一台 Gateway；本地 Channel 正常不能代替真实微信收发确认。

首页“OpenClaw 环境”卡片只提供状态、受控启停/重启和微信绑定，不暴露配置正文、任意命令或原始日志。操作成功以最终实例和健康状态为准。

首次接入由 OpenClaw 自身完成 Gateway 初始化、腾讯微信插件安装和 ClawBot 登录；Chub 不接管 OpenClaw 安装或升级：

```bash
openclaw onboard --install-daemon
openclaw plugins install "@tencent-weixin/openclaw-weixin"
openclaw config set plugins.entries.openclaw-weixin.enabled true
openclaw gateway restart
openclaw channels login --channel openclaw-weixin
```

官方组件完成后，还需按 [Chub OpenClaw 插件说明](../integrations/openclaw/chub/README.md)的“首次部署配置”安装并配置 Chub 插件，设置固定 Tailnet `baseUrl`，并分别确认插件侧 `weixinChubMode` 和 Chub 侧 `openclaw.weixin_chub_mode.enabled`。首次获批准的私聊配对可能在 Owner 为空时建立唯一 Owner；已有部署应运行 `openclaw doctor` 检查，并按其现场提示修复缺失配置。首页只展示 Owner 状态，不代替 OpenClaw 的身份配置。

常用只读检查：

```bash
openclaw gateway status --json
openclaw gateway probe
openclaw channels status --probe --json
openclaw config validate
```

## 3. 身份与权限

当前部署边界为：同一节点上的 Chub 与 OpenClaw、单一健康 ClawBot、单一微信 Owner。

- 微信统一调度接口只接受本节点真实 Tailnet socket 来源，不接受 Hub Token 或转发 Header 替代。
- 群聊、未知发送者、缺少稳定消息标识或缺少可信回送路由时不进入 Chub 任务。
- 消息正文只能提供业务内容，不能指定内部 Session ID、工作区、权限、模型、路径、命令、接口或收件人。
- 微信 Chub 模式的 `Full access` 只随当前受控 Owner 和绑定 Session 生效，不成为 Session 永久权限，也不适用于其他入口。
- 当前不支持第二个 Owner、多个并行 ClawBot 或跨节点任务提交；需要扩展时必须重新设计身份隔离。

普通 OpenClaw Agent 仍使用自身 Shell 审批和文件边界，不因微信 Chub 例外而获得额外权限。

## 4. 微信统一调度

Chub 插件只负责取得可信通道上下文、调用一次固定接口并执行 Chub 返回的交付决定。协议版本、请求字段和 `pass` / `reply` / `handled` 语义由[插件说明](../integrations/openclaw/chub/README.md)维护。

Chub 内部实现按状态模型、固定指令解析、消息格式化和有状态调度分层。模型层只定义兼容的持久化与公开结果字段；解析和格式化层只处理输入值并返回结果，不读取状态文件、不持有锁也不调用外部服务；Manager 仍是状态写入、并发协调、幂等、重启、通知和 Quick Worker 协作的唯一所有者。该维护边界不改变插件协议或本节业务顺序。

Chub 按以下顺序处理消息：

1. 校验模式、同节点来源、Owner、私聊、消息幂等标识和回送路由。
2. 匹配能力清单登记的固定路由。
3. 未命中的非空文字或可信语音转写作为普通快速交互任务提交。
4. 返回放行、固定回复或普通任务的即时提交结果；异步结果不在同步响应中伪装完成。

固定指令原则上要求整条消息匹配。新建和指定槽位切换允许附带任务正文；停止、归档、重启和续提类指令不接受附带正文。所有指定槽位的指令都将 `N` 与 `SN` 视为同一槽位编号。未匹配内容进入普通任务，不由插件解释。

### 4.1 固定指令统一回复

微信 Chub 固定指令及中文别名默认使用“指令结果 + 统一状态尾部”的回复结构。帮助、`Usage` 用法错误、新建/切换并提交任务或恢复待续提任务的结果，以及重启/停止的首次受理或进行中提示只返回完成当前判断所需的精简文案，不附加完整 Session 和额度；异步操作最终通知仍附带现场状态。其余指令结果按路由表达真实操作状态；尾部为：

```text
Sessions

<Session list>

Weekly 75% · Today 2M
```

固定回复的指令结果、状态、错误、用法、统计和占位文案统一使用英文；只有 Session 标题和任务标题保留来源原文，不翻译或改写。中文指令别名仍正常匹配，不决定回复语言。`Weekly` 与今日用量采用短格式并保持同一行，不增加 `Codex` 标题。Session 首行只用 `▶` 标记当前绑定、`!` 标记不可用或状态未知，不使用圆点或状态文字标记运行状态。正在运行的 Session 必须在 Session 行下一行紧邻显示 `Task · <摘要>`；存在可信任务摘要时显示原文任务名，否则显示 `Task · Running`。没有 `Task` 行即表示该 Session 当前没有运行任务。Session 标题和任务名称继续受微信 Chub 模式配置长度限制。

统一尾部采集失败时降级为 `Sessions` 下显示 `Unavailable`，额度行显示 `Weekly Unavailable`；空列表直接显示 `No sessions` 而不保留 `Sessions` 标题；未展示数量显示 `<N> more Sessions`。尾部不得改变前部指令结果的成功或失败语义。所有附带统一尾部的固定指令回复，以及重启或停止完成、失败或取消的独立通知，都必须使用本次请求或已保存的微信回送路由读取运行任务快照：快照包含可信摘要时显示 `Task · <摘要>`，快照缺失或读取失败时才降级为 `Task · Running`，不得跨路由查找任务标题。新建/切换并提交任务或恢复待续提任务的结果直接使用“操作状态 + 可信 Session + Task”上下文，不再重复附加完整 Session/额度尾部。`restart` 和 `session stop N` 的同步受理及进行中回复不附带状态，独立通知使用操作后状态；独立通知不再单列关联 Session 和关联任务，运行上下文只由统一状态尾部表达。独立通知应在 ClawBot 健康校验完成后、实际发送前尽可能晚地生成 Session 状态，并以持久化的当前绑定覆盖异步 Session 缓存中的旧 `current` 标记。普通任务提交回执和最终结果不附加该尾部，也不受固定回复英文规则约束。

新增或扩展固定路由时，必须同步登记为固定指令、复用英文固定文案、精简回复边界和统一尾部格式、覆盖成功与失败测试，并更新能力清单；不得在单一路由内另行拼装 Session 或额度格式。

### 4.2 状态与槽位

- `chub` 状态查询每次采集现场状态，只读且不修改槽位或通知状态。
- 状态主体只展示可见 Session 和统一 AI 用量；所有固定回复的空列表都直接显示 `No sessions`，不保留 `Sessions` 标题；异常集中列出，单一数据源失败不清空其他内容。
- `sync` 及中文别名是显式写操作：稳定扫描后原子补齐槽位，失败不提交部分结果。
- `S1`–`S9` 是 Chub 持久槽位，不等于列表位置；所有列表按创建时间倒序，排序不改变槽位编号。
- 当前绑定使用箭头标识，运行状态只由对应的 `Task` 行表达；运行任务快照优先从已持久化的原任务正文生成有界、单行、脱敏标题，再按 `task_name_max_width` 显示宽度截取，不受普通任务完成通知短摘要长度限制，也不调用模型生成。提交回执、提交失败、Session 列表、带任务的 Session 指令和完成通知统一复用 `Task · <摘要>`，并使用相同的摘要与显示宽度规则。显示宽度统一按半角 1、汉字与全角 2 计算，并保护 Emoji、组合字符等完整字形；Session 和任务默认宽度分别为 30 和 64，旧字符数配置不再接受。

具体指令和中文别名只在[集成能力清单](CHUB_INTEGRATION_CAPABILITIES.md)的“微信 ClawBot 指令”章节维护。

### 4.3 普通任务与 Session

- 普通文字和可信语音任务成功受理后立即返回 `Submitted`、可信的 `[▶ ]S<槽位> · <标题>` 和 `Task · <摘要>`；`▶` 只在该 Session 仍为当前绑定时显示。明确未提交时使用 `Not submitted · <原因>`、可靠确认的任务目标 Session 和相同的 Task 行；目标未建立或无法确认时不得借用原当前会话。Session 固定在 Task 前；槽位、归属或会话标题无法可靠确认时省略 Session，降级为“状态 + Task”。新建/切换并提交任务以及恢复待续提任务使用相同层级。重复消息复用首次持久化回执且不重复执行，但发送前重新判断当前绑定：已不是当前会话时移除 `▶`，原槽位已释放或复用时移除 Session 行。插件等待统一调度接口超时时立即回复提交状态未知并提示不要重复发送；插件此时没有取得 Chub 的可信 Session 和统一摘要，因此该基础设施异常保持单行。该超时不能证明 Chub 未受理，后台若已受理，翻译和主任务终态仍可按原路送达。
- 默认复用当前绑定 Session；不同 Session 可以并行，同一原生 Session 始终只有一个 writer。
- 当前 Session 忙时拒绝新任务，并短期保存最近一次待续提正文；用户可在当前 Session 续提或新建 Session 后续提。
- 新建、重命名、切换、停止、归档和待续提均由 Chub 固定路由完成；重命名只更新当前绑定 Session 的本地展示标题，不改变原生上下文或任务状态；停止先同步返回已安排，再异步取消目标 Session 的活动任务并停止底层会话，最终结果只按本次保存的微信路由发送。停止终态无法持久化时仍按该路由尽力回送真实结果，同时保留状态异常且不伪造持久化成功。停止保留槽位、历史和当前绑定，只有归档才释放槽位。不产生任务的固定操作按统一格式返回操作结果、Session 列表和 Codex 用量；新建/切换并提交任务或恢复待续提任务时只返回精简任务上下文。微信 Session 列表标题和 `chub` 状态任务名称使用微信 Chub 模式的独立配置上限；任务原文先按配置允许的最大长度持久化，再在展示时按当前配置截取，插件不感知 Session 业务。
- 标题、槽位、Activity 和单 writer 语义由[AI Session 状态模型](AI_SESSION_STATE_DESIGN.md)维护。
- 页面、微信和翻译任务统一由 Quick Worker 承载；恢复、任务级重启和通知终态由[Worker 设计](QUICK_INTERACTION_WORKER_DESIGN.md)维护。

文本优化与翻译是可选辅助能力：开启后，普通任务镜像到独立的内部只读 Session 和持久化 FIFO；关闭只阻止新翻译，不中断已接受任务。翻译 Session 不进入微信槽位，结果失败不改变主任务终态。

提交回执只确认任务进入 Chub，不代表翻译或主任务完成。翻译和主任务执行结果均属于后续独立通知，互不等待；主任务执行超时通过后续 `Timed out` 通知表达，与插件等待提交响应超时不是同一状态。仅主任务成功通知在结果底部追加统一额度行，提交回执、翻译、失败和超时通知不追加。由于同步回执与异步通知经过不同发送时机，后台不会用固定延时伪造交付顺序，真实到达顺序仍以微信通道为准。

### 4.4 语音可信来源

只有微信通道确认消息类型为语音且存在非空转写时，才能标记 `voice`。任务正文始终使用干净转写；内部来源标记不进入正文、摘要、日志或对外接口。普通文字伪造标记仍按文字处理，空正文且没有可信转写时不提交 Chub。

腾讯微信插件升级后若缺少等价能力，使用仓库参考补丁 `integrations/openclaw/patches/weixin-clawbot-voice-transcript-origin.patch` 恢复，并重新验证普通文字、真实语音、伪造标记、重复消息和模式关闭场景；不得只修改未确认的运行目录。

## 5. 幂等与状态

插件使用可信通道、账号、发送者、时间戳和正文生成非敏感消息标识；缺少稳定时间戳时失败关闭。Chub 在产生副作用前持久化预留：

- 相同标识和路由复用首次决定，不重复执行任务。
- 相同标识携带不同路由时拒绝。
- 首次失败不会因重复投递自动重试。
- 服务重启时无法确认的副作用按失败收敛，不猜测成功或重放。

统一调度、Session 操作、Quick Worker 任务、Codex 终态和微信通知分别记录状态，前一阶段成功不能代替后一阶段。

## 6. 完成通知

微信 Chub 任务保存本次 Hook 提供的账号和发送者，结束后只按该路由发送：

- 主任务成功、失败或超时后都进入独立通知流程；通知失败不改变任务结果。
- 普通任务通知使用紧凑英文框架：`Done`、`Failed` 或 `Timed out`，后接 `S<槽位> · <标题>`、`Task · <摘要>` 和保持原语言的任务结果；通知生成时任务所属 Session 若仍占用原槽位且仍为当前绑定，则 Session 行显示为 `▶ S<槽位> · <标题>`，否则不显示当前标记，槽位已复用时继续标记 `Unavailable`。框架不额外翻译或改写结果。
- `Done` 通知在最后一段底部追加 `Weekly <quota> · Today <usage>`，不附加完整 Sessions 状态；额度读取失败或超过短超时则降级为 `Weekly Unavailable`，不得改变任务成功状态或阻断结果发送。`Failed`、`Timed out` 和翻译通知不追加额度。
- 超长结果按段落有界分段，最多发送 5 条；部分送达后停止，不自动重试。
- 路由缺失、账号停止或发送失败时不回退到全局收件人。
- 页面快速交互使用单独配置的全局固定接收人，与微信任务原路路由不混用。
- 任务级 Web 重启只等待请求任务自身结果和通知终态；重启与恢复细节由 Worker 设计维护。

微信出站依赖收件人最近一次入站产生的 Context Token。当前约 10 分钟只是实测口径，不是公开 TTL；出站消息和 Gateway 重启不会续期，失效后需要收件人重新向同一 ClawBot 发送消息。持久化、惰性恢复和升级复检见[Context Token 补丁规范](WEIXIN_CLAWBOT_CONTEXT_TOKEN_AI_PATCH.md)。

## 7. Agent Tool 与飞书通知

普通 OpenClaw Agent 可以使用：

- `chub_get_status`：读取 Chub 基础状态。
- `chub_send_notification`：向 Chub 预配置飞书目标发送有界纯文本。

通知目标、人员和 Webhook 由 Chub 固定配置；调用方不能指定任意 URL、Open ID 或 Secret 路径。用户要求原文发送时必须使用当前运行的可信原文关联，无法确认时拒绝；只有明确要求撰写、总结或改写时才允许生成内容。

微信 Chub 私聊不通过这两个 Tool 完成路由，Chub 直接发送飞书也不经过 OpenClaw。

## 8. 故障判断与验收

| 现象 | 优先检查 |
| --- | --- |
| Gateway 正常但微信无回复 | Channel 探测、发送日志和真实微信收发 |
| 微信只收到统一通道失败 | Chub 可达性、插件协议版本、运行时来源和 Hook |
| 微信消息进入 Agent | 插件路由开关、Chub 模式和 `before_dispatch` |
| 普通任务被拒绝 | 当前 Session、writer、Worker 和健康 ClawBot |
| 完成通知失败 | 任务保存路由、Context Token 和通道状态 |
| 飞书正文被改写 | 内容模式、可信原文 Hook 和当前运行关联 |

`lastOutboundAt` 只能辅助判断，不能作为真实送达凭据。同步 Hook 回执可结合通道发送日志；异步结果需要同时核对 Chub 任务和通知终态，最终仍以维护者实际收到消息为准。

真实微信客户端操作只能由维护者本人完成。自动化验证负责协议、路由、幂等和失败边界；涉及微信通道、路由、通知格式或插件协议的变化，还需在受影响平台完成真实文字和语音回归。

当前 Gateway 管理、微信绑定、单 Owner、统一调度、固定路由、普通任务、原路通知和飞书 Tool 已纳入 macOS、Ubuntu 验收基线。插件平台部署状态和仍需真实回归的协议变化记录在[插件说明](../integrations/openclaw/chub/README.md)。
