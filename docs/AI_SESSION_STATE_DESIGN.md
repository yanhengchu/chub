# Chub AI Session 状态模型设计

> 状态：已实现并验收。当前模型已在 Codex CLI 会话逻辑中落地，并保留接入其他 AI CLI 的适配边界。

## 1. 背景与目标

Chub 当前直接管理 Codex CLI 会话，但产品层真正管理的是“可被远程进入、停止、恢复和提交任务的
AI Session”。后续接入其他 AI CLI 时，页面、API 和互斥策略不应继续依赖 Codex 专有字段或 Hook
名称。

当前实现使用 Session 状态和 Activity 状态两个正交状态轴描述 Chub 中的 AI Session；
当 Activity 为执行中时，再使用 Activity Source 标记本次 Turn 由哪个交互入口触发：

- `session_status`：Chub 受管交互运行时的生命周期状态。
- `activity_status`：该 AI Session 通过任意受管入口当前是否正在处理任务。
- `activity_source`：当前正在执行的 Turn 来自实时终端还是快速交互。

两个状态轴共同组成 Session 的核心状态，来源字段不是第三个状态轴，只对执行中的 Activity
补充归属。连接、权限、
后台任务、归档和错误详情作为独立维度，
不扩张核心枚举。

### 1.1 交互入口术语

Chub 为同一 AI Session 提供两种交互入口：

| 名称 | 定义 |
| --- | --- |
| 实时终端 | 进入 AI CLI 原生 TUI，通过持续连接查看实时输出、连续输入并处理审批 |
| 快速交互 | 不进入 TUI，从普通页面提交单次需求，由后台执行并在交互记录中查看结果 |

两者统称“交互入口”，共享同一 AI Session，不是两个独立 Session。产品页面和文档不再使用
“实时交互”作为入口名称，统一使用“实时终端”，避免与 Activity 或快速交互混淆。

首页允许用户为每个 Session 切换默认交互入口。由于当前只有“实时终端”和“快速交互”两个选项，
入口按钮采用两态直接切换，不打开选择弹窗，也不需要单独保存确认。该选择只决定点击 Session 后
进入实时终端页面还是快速交互页面，属于当前浏览器的导航偏好，不是 Session 状态，也不能写入
`activity_source`。
`activity_source` 始终由实际开始执行的 Turn 决定。

## 2. 设计结论

“Session 状态 + Activity 状态”适合作为 Chub 的通用核心模型，原因如下：

- 生命周期与任务活动相互独立：交互运行时存在，不代表正在执行任务；
  交互运行时停止时，后台入口仍可能执行任务。
- 页面可以稳定区分“等待输入”“正在执行”和“无法确认”；实时终端是否已停止由
  `session_status` 和对应操作入口表达，不占用页面主活动状态。
- 实时终端和快速交互只归属执行过程；Turn 结束后统一回到 Session 自身的等待状态。
- AI CLI 只需通过适配器映射到 Chub 状态，不要求不同 CLI 使用相同的原生事件。
- 活动状态失真时可以降级为 `unknown`，不会误报为空闲或执行中。

该模型不能单独承载所有产品状态。快速交互、浏览器连接、权限模式和归档状态必须独立保存，
再由页面组合展示。

## 3. 核心状态

### 3.1 Session 状态

当前保持最小集合：

| 状态 | 含义 | 允许的核心操作 |
| --- | --- | --- |
| `new` | Chub 已创建记录，但 AI CLI 尚未建立可恢复的原生 Session | 进入、启动 |
| `running` | Chub 管理的 AI CLI 交互运行时存在 | 进入、停止；是否可提交任务取决于 Activity |
| `stopped` | 交互运行时不存在，但原生 Session 可恢复 | 恢复、归档 |
| `error` | 原生 Session 建立、恢复或管理失败，导致所有交互入口均不可用 | 查看错误、重试、停止清理 |

`session_status` 只描述 Chub 管理的交互运行时，不表示 AI 供应方会话是否存在，也不表示是否
有后台任务正在执行。一个 `stopped` Session 仍可拥有完整的原生会话记录，并可以通过
快速交互等受管入口处理任务。

### 3.2 Activity 状态

| 状态 | 含义 | 安全含义 |
| --- | --- | --- |
| `unknown` | Chub 无法可靠确认当前活动 | 不得自动推断为空闲；风险操作需要拒绝或确认 |
| `idle` | 当前没有已确认的 Turn 正在处理，等待新输入 | 原生 writer 同时空闲时才可以提交新的后台交互 |
| `working` | 当前唯一的 Turn 正在处理 | 不允许为同一 Session 再启动新 Turn |

`activity_status` 是 AI Session 级状态，不依赖交互运行时是否存在。实时终端和快速交互都会贡献
Activity；页面可以另外展示具体执行入口，但不得因交互运行时处于 `stopped` 就忽略正在执行的
后台任务。

### 3.3 Activity 来源

| 值 | 含义 |
| --- | --- |
| `terminal` | 当前 Turn 由实时终端触发 |
| `quick` | 当前 Turn 由快速交互触发 |
| `none` | 当前没有可归属的执行中 Turn |

约束：

- `activity_status=working` 时，`activity_source` 必须是 `terminal` 或 `quick`。
- `activity_status=idle` 时，`activity_source` 必须是 `none`。
- `activity_status=unknown` 时，不猜测执行入口，`activity_source` 使用 `none`。
- `activity_source` 不记录上一次 Turn 来源，Turn 结束时必须立即清空。
- 同一 Session 同一时刻最多允许一个执行中 Turn，因此 `terminal` 和 `quick` 不能同时存在。
- Activity 只描述 Chub 已确认的 Turn 状态，不能替代原生 Codex writer 互斥判断；快速交互提交前还要检查 writer，实际 `resume` 仍需处理检查与启动之间的竞态冲突。
- 微信 Chub 专用 Session 是 `unknown` 的受控处理例外：入口自动停止该 Session 的残留终端并确认 writer 释放，再提交快速交互；普通页面仍保留人工确认语义。
- `activity_source=terminal` 时，`session_status` 必须为 `running`。

## 4. 合法组合与页面语义

| Session | Activity | Source | 页面主状态 | 合法性 |
| --- | --- | --- | --- | --- |
| `new` | `unknown` | `none` | 尚未启动 · 可进入 | 合法 |
| `running` | `working` | `terminal` | 实时终端 · 执行中 | 合法 |
| `running` | `working` | `quick` | 快速交互 · 执行中 | 合法 |
| `stopped` | `working` | `quick` | 快速交互 · 执行中 | 合法 |
| `stopped` | `working` | `terminal` | 不展示 | 非法 |
| `running/stopped` | `idle` | `none` | 会话 · 等待输入 | 合法 |
| `running/stopped` | `unknown` | `none` | 会话 · 状态未知 | 合法 |
| `error` | `unknown` | `none` | 会话异常 · 可重试 | 合法 |

`stopped + working + quick` 是合法组合，表示交互运行时已停止，但快速交互 Turn 正在执行。
此时页面优先展示“快速交互 · 执行中”。Turn 结束后不保留“快速交互”归属，统一展示
“会话 · 等待输入”；实时终端是否需要恢复，由操作入口根据 `session_status` 决定。

`stopped + idle + none` 的页面主状态仍为“会话 · 等待输入”。此时 `session_status` 只负责让
操作入口执行“恢复实时终端”，而不把主状态改回“会话已停止”。因此页面主状态描述 Session
是否可以接受下一次输入，操作入口描述实时终端是否需要恢复。

页面展示是核心状态与独立维度合成后的产品状态，不反向修改核心状态。当前展示优先级：

1. Session 级错误。
2. `working` 下由 `activity_source` 确定的执行入口。
3. `idle/unknown` 下的 Session Activity。
4. 操作入口根据 `session_status` 决定是直接进入还是恢复实时终端。

终端页面、代理或 WebSocket 的访问错误属于独立通道错误。只要 AI Session 本身仍可通过快速交互
处理任务，就不能把 `session_status` 写成 `error`；页面可以单独提示终端访问异常。

## 5. 独立状态维度

以下信息不属于 Session 或 Activity 枚举：

### 5.1 原生 Session 标识

- `provider`：AI CLI 类型，例如 `codex`。
- `provider_session_id`：原生会话标识。
- `provider_metadata`：仅由适配器解释的固定结构、有界元数据，不得保存凭证或交互正文。

Chub 的 `session_id` 保持稳定，不能直接等同于供应方 Session ID。

### 5.2 连接状态

浏览器页面、WebSocket、PTY 和终端代理连接只描述访问通道，不代表 AI 是否执行任务：

- 无浏览器连接时，运行时仍可保持 `running + idle`。
- 浏览器已连接时，也可能是 `running + working`。
- 连接状态不得覆盖 `activity_status`。

### 5.3 执行任务

快速交互属于独立的 Execution：

- 包含任务 ID、入口类型、状态、开始/更新时间和最终结果。
- 同一 Session 可以保留多个历史 Execution，但同一时刻最多允许一个 Execution 处于执行中；
  Chub 使用 Session 级协调锁和原生 writer 探测共同保证该约束。
- 快速交互执行期间可以保留已经建立的实时终端连接，但必须阻止该终端发起新的 Turn；
  仅禁止再次进入页面不足以保证互斥。
- 页面可以用“快速交互 · 执行中”覆盖基础展示，但不把快速交互写成新的
  `session_status`。
- Execution 开始和结束必须更新 `activity_status`，不能只依赖可能乱序或过期的 Hook 事件。
- Execution 开始时设置对应 `activity_source`，当前 Turn 结束时将其清空为 `none`。
- 服务重启时，未完成的快速交互统一结束为失败，并同步清理 `quick` 来源；
  实时终端运行时回退为 `unknown + none`，已停止时回到 `idle + none`。

#### 5.3.1 快速交互触发的延迟重启

快速交互如果需要重启 Chub，不能在任务执行中直接调用服务管理器。任务只能调用一次项目固定的
`scripts/chub-web-restart`；该脚本在快速交互环境中只登记当前任务的重启请求，不立即停止服务。
Chub 仅在本次任务成功结束后将其提升为节点级待重启状态，并遵循以下规则：

- 先保存并展示本次最终结果；配置了微信完成通知时，还要等待通知进入已发送、失败或跳过的终态。
- 如果没有其他快速交互正在执行，短暂留出结果读取时间后立即重启；如果仍有其他快速交互，等待它们
  全部结束后重启，无论这些任务自身是否要求重启。
- 待重启期间拒绝新快速交互，避免等待集合不断扩大；空闲的实时终端或仅存活的 tmux Session 不阻塞重启。
- 多个登记合并为一次节点级重启，但每个成功登记的任务保留自身操作 ID、来源和共享的节点重启操作 ID，最终状态分别回写。登记任务失败、超时或取消时不提升为待重启，也不会自动重启。
- 任务最终结果由 Chub 追加准确状态：无其他任务时提示“即将重启”，有其他任务时提示当前等待数量；
  登记失败时明确提示本次不会自动重启。
- 待重启状态持久化为 `waiting` 或 `started`，并保存发起实例 ID。节点级状态文件继续只描述一次实际重启；
  多任务关联和通知状态保存在各自的快速交互私有状态中。旧实例只记录重启已开始；新实例必须等本机健康接口
  返回新的实例 ID 后，才清理节点状态并记录最终成功，不能把子进程创建成功或应用对象构造完成当作服务恢复。
  仅当旧实例已将状态推进为 `started` 时，新实例才在每个关联任务后追加“Chub 已完成自动重启，服务已恢复。”
  系统回复；该回复不调用 Codex 或 LLM。
- 微信 Chub 来源任务在健康确认后使用任务保存的账号和发送者原路发送独立重启结果；页面来源只更新页面时间线。
  重启通知与任务完成通知分开记录，采用至多一次语义：`pending` 可以在健康实例继续处理，调用前先写为
  `sending`，若此时再次中断则标记失败而不自动重试。失败不改变任务或重启结果，也不回退全局收件人。
- 重启脚本未能启动时，仍在运行的旧实例将关联任务记为 `start_failed` 并通知微信来源；服务退出后始终未恢复
  无法由 Chub 自身通知，需依赖 systemd、LaunchAgent 或外部监控。
- 待重启尚未自动触发时，如果维护者通过页面以外的服务管理方式手动重启，新实例同样消费并清除该状态，
  不再执行第二次重启，也不展示自动重启成功回复。清理失败时服务仍可启动，但保持受控阻断并记录运行日志，
  避免循环重启。
- 页面维护接口在存在执行中的快速交互或已有待重启状态时拒绝立即重启。维护者直接调用系统服务管理器仍属于
  强制操作，可能中断任务；该路径只用于明确的人工处置。

这里的“返回结果”指结果已经持久化并可由页面读取，且已配置的完成通知已结束；服务端无法判断维护者是否已经
实际阅读页面内容，因此使用短暂宽限而不是等待浏览器确认。当前不设置强制等待上限：只要已有快速交互仍在执行，
自动重启就继续等待。快速交互页面在待重启期间继续轮询，跨过短暂的服务不可用窗口后原位显示系统回复；手动
重启将关联任务的内部待处理状态结束为 `cleared`，页面显示自动重启计划已由其他服务重启清除，不发送微信自动重启成功消息。

### 5.4 权限与归档

- 权限模式在创建时写入 Session 配置，不是运行状态；设置页只保存后续新建会话的默认值。
- 归档描述原生会话是否出现在活动清单，不是运行时状态。
- 删除是操作结果，不保留 `deleted` Session 状态。

### 5.5 交互入口偏好

- 默认值为 `terminal`，表示点击 Session 进入实时终端。
- 用户也可以选择 `quick`，表示点击 Session 进入快速交互页面。
- 入口按钮显示当前模式；点击后立即切换为另一模式，并通过按钮提示说明将要切换到的目标。
- 两个固定选项不使用下拉箭头或选择弹窗，避免增加无意义的选择和保存步骤。
- 偏好按 Session 保存在浏览器本地，不跨设备同步，不进入后端 Session 模型。
- 尚未建立原生 Session 时可以进入实时终端，也可以由快速交互的首次任务创建原生 Codex Session；`Ask for approval` 权限仍需先进入实时终端。快速交互页面同时承载任务提交和交互记录。
- 快速交互执行中仍允许进入快速交互页面查看进度，但继续禁止进入实时终端。

## 6. 状态来源与可信度

每个适配器负责从进程、Hook、协议事件或持久化记录生成 Chub 状态。通用状态至少需要：

- 当前值。
- `status_updated_at` 与 `activity_updated_at`。
- 状态来源，例如 `process`、`hook`、`protocol` 或 `recovery`。
- 最近错误的固定错误码和有界说明。

当前代码仍共用一个 `updated_at`；后续接入第二种 AI CLI 前，应拆分生命周期和活动更新时间，
避免进程刷新掩盖 Activity 已经过期。

状态可信规则：

- 进程存在只能证明 `running`，不能证明 `idle`。
- 明确存在执行中的 Turn 时设置为 `working`。
- 明确当前 Turn 已结束，且状态来源完整时，才设置为 `idle`。
- 服务重启后只恢复出运行时、但没有新的活动事件时，映射为 `unknown`。
- 事件乱序、读取失败或超过适配器有效期时，降级为 `unknown`，不沿用可能过期的 `idle`。

## 7. 通用状态转换

```text
new ──启动成功──> running + unknown
unknown ──可信活动事件──> idle/working
idle + none ──实时终端 Turn 开始──> working + terminal
idle + none ──快速交互 Turn 开始──> working + quick
working + terminal/quick ──当前 Turn 完成──> idle + none
running ──停止确认──> stopped
stopped ──恢复成功──> running + unknown
new/running/stopped ──运行时失败──> error
error ──重试成功──> running + unknown
error ──确认已停止──> stopped/new
```

约束：

- 不能仅凭子进程成功创建就写入 `running`，必须确认运行时存在或已就绪。
- 不能仅凭停止请求成功返回就写入 `stopped`，必须确认原运行时已结束。
- Activity 变化不能改变 Session 运行时生命周期。
- 是否放行新任务同时取决于 Activity、Execution 互斥策略和适配器能力，不能只检查
  `session_status`。
- `session_status=error` 时禁止启动新 Turn；仅终端访问失败时不得使用该状态。

## 8. 当前 Codex 映射

| Codex 信号 | Chub Session | Chub Activity | Activity Source |
| --- | --- | --- | --- |
| Chub 记录已创建、没有 Codex session ID | `new` | `unknown` | `none` |
| tmux/Codex TUI 存活 | `running` | 保留或按 Hook 更新 | 按 Activity 更新 |
| 原生 Session 存在、tmux 不存在 | `stopped` | 根据后台 Execution 聚合 | 根据 Execution 更新 |
| 原生 Session 无法建立、恢复或继续管理 | `error` | `unknown` | `none` |
| 实时终端 `UserPromptSubmit` Hook | 不变 | `working` | `terminal` |
| 快速交互开始 | 不变 | `working` | `quick` |
| 当前 Turn 结束 | 不变 | `idle` | `none` |
| 服务重启后仅确认 tmux 存活 | `running` | `unknown` | `none` |

当前 `CodexSession` 可以视为 Chub 通用模型的首个实现，但后续应逐步把模型、存储和 API 命名从
`CodexSession` 下沉为供应方无关的 `AiSession`，Codex 专有发现、命令和 Hook 留在适配器中。

## 9. 适配器边界

每个 AI CLI 适配器必须负责：

- 发现可管理的原生 Session。
- 返回受控的运行时健康状态。
- 将原生活动事件映射为 `unknown/idle/working`，并为执行中 Turn 标记交互入口。
- 提供供应方能力声明，例如是否支持恢复、后台执行、审批和只读模式。
- 提供固定命令映射，不能接受客户端传入任意命令或路径。

创建、启动、停止、恢复、归档、删除和后台执行均为可选能力。Chub 根据适配器的能力声明
决定是否提供对应操作，不为不支持的操作伪造通用实现。

Chub 通用层负责：

- 保存统一状态。
- 执行合法状态转换和最终状态确认。
- 管理同一 Session 的操作锁，并保证同一时刻最多有一个执行中 Turn。
- 在快速交互执行期间阻止实时终端提交新的 Turn，同时不把浏览器连接状态误作 Activity。
- 实时终端输入放行与快速交互启动必须通过同一 Session 锁串行化，
  不能采用先检查、后转发的非原子逻辑。
- 合成 API 和页面展示状态。
- 记录不含凭证和正文的操作日志。

## 10. 当前落地范围

当前已在 Codex 业务链路中完成以下映射：

- Session 数据和 API 增加 `activity_source`，旧数据读取时使用兼容默认值。
- Codex Hook 将实时终端 Turn 映射为 `working + terminal`。
- 快速交互开始和结束显式写入 `working + quick`、`idle + none`。
- 首页按统一 Activity 和来源展示执行入口，Turn 结束后统一显示“会话 · 等待输入”。
- 快速交互执行期间拒绝新的实时终端入口和会话变更操作；已经建立的终端连接保留输出，但禁止输入。
- 停止、权限切换、归档和删除统一先通过 Session 互斥检查，被拒绝的操作不会关闭已有连接。
- 服务重启会结束未完成的快速交互并恢复一致的 Session Activity，不保留失效的
  `working + quick`。
- 快速交互主动登记 Chub 重启时，先完成结果和通知交付，再等待节点上的全部快速交互结束后自动重启；
  待重启期间不接受新快速交互，手动重启后的新实例会消费原有标记，避免重复重启。
- 首页使用紧凑的两态交互入口切换按钮，不再单独放置“快速交互”和“交互记录”按钮；进入快速交互后固定使用
  消息时间线。首页入口偏好和每次加载 5/10 条记录的偏好属于浏览器 UI 状态，不写入 Session、Activity、
  Execution 或任务。
- 快速交互按时间线将任务映射为问答消息，统一承载提交入口、任务状态、结果和历史记录，不创建新的后端
  “会话”类型。
- 快速交互页面避免在输入区和交互记录中重复展示同一个执行状态；输入区保留提交和必要的阻断、确认
  信息，任务执行状态由交互记录统一呈现。
- 快速交互轮询按任务 ID 原位更新变化记录，不重建稳定历史；输入卡片与记录列表分别处理刷新失败，
  避免一个区域的临时错误清空或阻断另一区域。
- 快速交互记录的置顶属于任务展示元数据，不改变 Session、Activity、任务时间或执行结果；页面保持按创建时间
  的时间线顺序。

当前保留现有 `CodexSession`、API 路径和本地状态文件结构，不提前引入尚无第二个供应方使用的
通用适配器层。接入其他 AI CLI 时再提取 `AiSession` 和适配器接口。

## 11. 当前维护结论

Session、Activity、交互来源、原生 writer 和执行任务已按本文模型落地。实时终端与快速交互共享单写入者约束；服务重启和事件缺失不会把未知状态误报为空闲；异步启动、停止、通知和延迟重启均以最终状态确认结果。

单任务自动延迟重启已在 macOS 完成实机验证，微信 Chub 来源的任务完成结果与新实例恢复后的独立重启结果已完成真实原路回送验收；多任务等待、来源分流、手动清标记、启动失败、状态文件异常及 macOS/Linux 服务管理分支由自动化测试覆盖。后续接入新的 AI CLI 时保留本文状态语义，并将供应方差异限制在适配器内。
