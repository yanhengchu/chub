# Chub AI Session 状态模型设计

> 状态：持续维护。
> 主要读者：AI Agent、实现和排障 Agent；维护人员用于确认 Session 的核心状态和展示边界。
> 本文负责：Chub 逻辑 Session、原生 Session 映射、入口类型、Activity、使用状态投影、首页展示语义和 Session 操作定义。
> 本文不负责：操作的接口编排和实现细节、Runtime 私有协议、Quick Worker 任务恢复与通知、终端桥接实现、升级流程和微信路由；这些内容以对应专项文档为准。

## 1. 核心定义

Chub Session 是 Chub 管理的一条逻辑记录，可以关联一个 Runtime 的原生 Session，也可以暂时没有原生 Session。

标识含义固定如下：

- `session_id`：Chub 逻辑 Session 的稳定标识。页面、API 和任务入口统一使用它。
- `native_session_id`：Runtime 原生 Session 的不透明标识，只由后端解析和保存，客户端不能提交或替换；内部翻译 Session 可在安全确认后由 Worker 轮换当前绑定。
- `runtime_id`：原生 Session 所属 Runtime，由后端固定写入；当前生产值为 `codex`。

同一 `(runtime_id, native_session_id)` 只能绑定一条 Chub Session。找不到映射时，不得根据标题、工作目录或页面位置猜测归属。

原生 Session 与 Chub 逻辑记录通过三条链路汇聚，用户列表统一展示为 Session，不再区分“内部/外部”产品类型：

| 链路 | 先出现的身份 | 原生 ID 的权威确认 | 未被确认时的处理 |
| --- | --- | --- | --- |
| 快速交互 | Chub `quick` Session | Quick Worker 关联当前任务的可信原生 ID | 自动发现短暂观察，不创建重复记录 |
| 实时终端 | Chub `terminal` Session | 当前 Chub 终端 Hook 回传的原生 ID | 自动发现短暂观察，不创建重复记录 |
| 原生自动发现 | Runtime 原生 ID | Runtime Adapter 发现结果 | 确认没有上述待回传认领后，创建并绑定 `terminal` 记录 |

自动发现可继续展示 Runtime 中已有的原生 Session，但不是 Quick Worker 或终端 Hook 的替代 writer。发现到未知原生 ID 且存在正在等待原生 ID 的 Quick 任务或实时终端时，只在内存中短暂观察；该窗口只延迟该条未知发现项的展示，不阻塞 Runtime、Worker、列表、其他 Session 或原生写入。Worker/Hook 认领时，直接绑定到原有逻辑记录；超过固定短窗口仍未被认领时，才创建一条可接管的 `terminal` 记录。重启导致观察缓存丢失只会重新开始观察，不能据此猜测归属。

每次实际新建实时终端载体前（包括重启终端后端），Session Manager 生成并持久化一个新的 `terminal_launch_id`；Launcher 与 Hook 必须原样传递，只有当前代次的 Hook 能为未绑定 terminal 认领原生 ID。停止终端会使该代次失效；迟到、缺失或不匹配的 Hook 只被丢弃，不能修改映射。Quick Worker 在每次任务启动前登记当前 `worker_task_id`，无论该 Session 是否已绑定原生 ID；回传原生 ID 时同时提供 Worker 的 `execution_id`，Manager 只接受仍与该 Session 登记匹配的任务/执行代次。Web 从本地任务状态恢复进行中的 Quick 任务时，必须先重建同一认领，再开始向 Worker 对账。任务终态会释放未完成的认领，旧任务结果不得绑定后续 Session 状态。

创建 Chub 映射不等于接管 writer。自动发现的原生 Session 有外部 writer 时显示“其他应用 · 正在使用”；writer 释放后，同一记录显示“实时终端 · 等待输入”，用户进入时 Chub 才接管终端 writer。writer 检查失败只能显示未知，不能当作空闲。`discovered` 仅是内部来源元数据，不改变用户可见的 Session 类型或列表位置。

普通 Session 的 native ID 不允许被不同结果替换；内部翻译 Session 仍复用同一逻辑 Session，但 Worker 可以为新的只读翻译执行返回新的 native ID。只有确认旧 native Session 没有活动 writer 且新 ID 未被其他 Chub Session 占用时，才更新当前绑定。轮换只替换 Chub 当前映射，不主动删除或归档旧 native Session；唯一例外是 4.1 定义的、历史误导入记录的固定启动清理。旧 ID 不再作为该逻辑 Session 的当前入口，未来如需扩展清理范围必须另行定义并复检原生数据边界。

每条 Session 创建时固定一种入口类型：

| `session_mode` | 入口 | 用途 |
| --- | --- | --- |
| `terminal` | 实时终端 | 进入原生 Codex TUI，查看实时输出、连续输入和处理审批 |
| `quick` | 快速交互 | 提交后台任务，在时间线查看任务状态和结果 |

类型创建后不可切换。入口类型决定允许的交互方式，不代表 Runtime 的另一种状态；微信和 ClawBot 只使用 `quick` Session。

## 2. 两组核心状态

### 2.1 Session 生命周期

`status` 描述 Chub 记录对应的入口生命周期，不描述当前是否正在处理一个 Turn。

| 状态 | 含义 |
| --- | --- |
| `new` | Chub 记录已创建，尚未确认可恢复的原生 Session 或执行入口 |
| `running` | Chub 管理的入口运行时存在 |
| `stopped` | 当前入口运行时不存在，但 Session 记录仍可恢复或继续提交 |
| `error` | Chub 无法可靠建立、恢复或管理该入口 |

### 2.2 Activity 与来源

`activity` 描述当前是否存在已确认的 Turn；`activity_source` 只说明 Turn 的入口来源。

| Activity | Source | 含义 |
| --- | --- | --- |
| `unknown` | `none` | 无法可靠确认当前活动状态，不能当作空闲 |
| `idle` | `none` | 没有已确认的 Turn，等待下一次输入 |
| `working` | `terminal` | 实时终端正在处理 Turn |
| `working` | `quick` | 快速交互正在处理 Turn |

基本约束：

- 同一逻辑 Session 同时最多一个 Chub writer。
- `activity_source` 只描述当前 Turn，Turn 结束后恢复为 `none`。
- `unknown` 只表示无法确认，不等于 `idle`，也不等于失败。
- `status`、`activity` 和原生 Runtime 状态相互关联但不互相替代；任何入口都必须以自己的权威来源确认最终状态。

## 3. Session 使用状态

列表和详情可以公开一个只读的 `usage` 投影。它由统一方法根据 `session_id` 解析 Chub 记录、原生映射、终端 owner 和 Quick Worker 状态；调用方不直接拼接 `native_session_id`。

`usage` 包含三部分：

| 字段 | 含义 |
| --- | --- |
| `native_session_present` | 是否已经绑定原生 Session；为否时表示当前只有 Chub 本地记录 |
| `owner` | 当前 writer 的可确认归属 |
| `phase` | 当前可确认的使用阶段 |

`owner` 的含义：

| Owner | 含义 |
| --- | --- |
| `none` | 当前没有检测到 writer，可安全判断为未占用 |
| `terminal` | writer 属于 Chub 实时终端 |
| `quick_worker` | writer/任务属于 Chub Quick Worker |
| `external` | 检测到 writer，但不能证明属于 Chub，按其他进程占用处理 |
| `unknown` | writer 状态检查失败，无法安全判断归属 |

`phase` 的含义：

| Phase | 含义 |
| --- | --- |
| `idle` | 当前没有已确认的 Chub 执行 |
| `running` | Chub 实时终端正在执行 |
| `waiting_result` | Quick Worker 任务已占用 Session，等待任务结果或终态；仅供内部判断，不单独展示给用户 |
| `unknown` | 无法可靠判断阶段 |

统一判断顺序为：Quick Worker → Chub 实时终端 → 原生 writer → 无原生 Session。检测到 writer 但无法证明属于 Chub 时，必须返回 `external`；检查失败返回 `unknown`，不得沿用历史 `idle`。

`usage` 是列表展示和使用前预判，不是最终业务结果。页面可以据此显示状态并按具体操作契约提示或置灰入口；不能把 `unknown` 作为所有操作的通用禁用条件。具体操作的最终门禁和未知状态处理由对应操作接口及其执行层负责。

原生 ID 重复、Hook 无效或 Worker 结果冲突只影响对应 Session 的映射处理；列表读取、其他 Session、Quick Worker、设置和维护入口必须继续可用。重复回传同一 ID 按幂等成功处理；自动发现记录可被当前可信的 Worker/终端回传收回；两条明确受管记录冲突时不得自动抢占 writer，也不得把冲突扩散成控制面错误。冲突 Hook 的局部诊断只在对应 Session 显示；后续可信 Worker 绑定或新的终端启动成功后必须清除，不能形成永久异常提示。

### 3.1 首页展示文案

首页状态按“占用/异常优先，入口状态其次”选择一条文案；状态文案统一使用“入口类型 · 当前状态”。

| 首页文案 | 含义 |
| --- | --- |
| `其他应用 · 正在使用` | 原生 Session 被外部进程占用，Chub 不接管写入 |
| `占用状态未知 · 请刷新` | 无法确认 writer 归属，暂不允许高风险操作 |
| `活动状态未知 · 请刷新` | 无法确认当前是否正在处理 Turn |
| `会话异常 · 可重试` | Chub Session 生命周期或恢复异常 |
| `终端连接异常 · 可重试` | Chub 终端桥或连接异常 |
| `尚未启动 · 可进入` | Chub 记录尚未启动原生 Session |
| `快速交互 · 待输入` | 快速交互当前空闲，可以提交新任务 |
| `快速交互 · 执行中` | 快速交互正在执行任务 |
| `实时终端 · 等待输入` | 实时终端已建立但当前空闲 |
| `实时终端 · 执行中` | 实时终端正在处理 Turn |

“占用状态未知”和“活动状态未知”不能混用。Quick Worker 的内部 `waiting_result` 阶段统一映射为“快速交互 · 执行中”；任务时间线继续展示任务自身的等待、完成、失败、超时和停止状态。

### 3.2 快速交互 Session 切换栏

快速交互页面的 Session 切换栏复用同一份 `usage` 投影，只提供导航和紧凑状态，不另建一套 Session 状态。用户可见状态固定为：

| 切换栏状态 | 含义 |
| --- | --- |
| `待输入` | 当前 Session 可接受新的快速任务 |
| `执行中` | Quick Worker 或其他 Chub 入口正在处理当前 Session |
| `其他应用占用` | 原生 Session 被 Chub 之外的进程占用 |
| `状态未知` | 无法确认当前占用状态 |
| `异常` | Chub Session 当前异常 |
| `需终端` | 当前权限模式需要通过实时终端处理 |

外部占用和状态未知的 Session 仍允许切换查看历史内容。重命名、停止、归档、删除四项 Session 操作的具体定义见 3.3–3.7；切换栏不展示 Quick Worker 的内部 `waiting_result`，统一显示为“执行中”；任务时间线负责表达任务自己的详细状态。

首页入口按 Session 类型区分：`quick` Session 即使外部占用或状态未知，也允许进入快速交互页面查看历史；`terminal` Session 在这两种状态下不建立实时终端连接，并提示释放外部占用或刷新后重试。状态在首页列表加载、快速交互页面加载/切换、用户主动刷新和操作按钮触发时重新判断。首页和快速交互页面的 Session 列表在存在执行中任务、Quick Worker 运行或待确认状态时，按现有退避策略自动轮询；全部 Session 无需继续观察时停止轮询。该轮询只更新展示状态，不替代操作接口执行时的后端门禁。

### 3.3 Session 操作通用规则

重命名、停止、归档和删除都属于 Session 操作，但不共享完全相同的门禁。统一规则如下：

- 调用方只提交 `session_id`；后端负责解析 Chub 记录、`native_session_id` 和当前 `usage`，客户端不能替换原生 ID。
- 每项操作只影响其定义的对象，不因为一个 Session 的状态阻塞其他 Session；读取和查看历史不因写入门禁一并关闭。
- 门禁只阻止当前存在直接冲突或破坏风险的操作。明确的 `external` 原生 writer 仍阻止会冲突的写入或生命周期操作；Chub 自己的执行态只有在具体操作明确要求时阻止，Chub 重启后的 `unknown` 投影不应单独阻止归档或删除，最终由原生操作结果决定。
- 页面按钮状态只是交互提示，操作接口必须在执行时重新判断当前状态；状态变化后以接口最终结果为准。
- 结果至少区分成功、明确失败和状态未知。涉及原生 Session 与 Chub 两侧的操作，成功条件和失败后的保留边界由具体操作定义。
- Session 列表可以为了展示执行进度和状态收敛进行按需轮询；没有执行中或待确认状态时不轮询。归档、停止等操作仍必须在按钮触发和后端执行时重新判断当前状态，不能依赖上一次列表结果。
- 操作入口不能先用普通列表对账删除目标映射，再报告“Session 不存在”；原生记录已归档或已删除时，应在操作流程内确认终态并继续清理。页面列表发生滞后时，已从活动列表移除的目标按已完成处理并刷新或离开当前页面。

### 3.4 重命名

1. **目标**：修改 Session 在 Chub 中的展示名称。
2. **范围**：只修改 Chub Session 元数据，不修改原生上下文、任务、终端、槽位或权限。
3. **门禁**：Session 必须存在，名称必须符合 Chub 的输入规则；明确检测到原生 Session 被其他进程占用时禁止重命名，避免在 Chub 不拥有 writer 时继续修改该 Session 的管理状态。Quick Worker 执行或实时终端占用不阻止重命名；无法确认占用状态时仍按本地元数据操作处理，不把 `unknown` 扩大为通用禁用。内部固定用途的 Session 是否允许重命名，按其专用规则处理。
4. **结果**：保存成功后返回新名称；失败时保留原名称并返回原因；没有原生 Session 不影响重命名成功。

### 3.5 停止

1. **目标**：停止 Chub 当前拥有的实时终端或 Quick Worker 执行，并保留 Session 供后续查看或恢复。
2. **范围**：停止当前 Chub 运行载体或任务，保留 Chub Session、历史任务、原生 Session 映射和槽位；不等同于归档或删除。
3. **门禁**：先判断原生 Session 是否被其他进程占用；外部占用或无法确认占用状态时禁止操作。通过后，仅当 `usage.owner=terminal` 且 `phase=running`，或 `usage.owner=quick_worker` 且处于活动任务阶段时允许停止；空闲、等待输入、尚未启动和已停止状态不提供停止操作。
4. **结果**：成功时确认 Chub 实时终端或 Quick Worker 已停止，并将 Session 置为可恢复的停止/空闲状态；失败时保留 Session 和关联数据并返回原因；最终状态无法确认时返回状态未知，不伪造已停止。

### 3.6 归档

1. **目标**：结束 Session 的活动生命周期，使其从活动列表移除，并在存在原生 Session 时执行原生归档。
2. **范围**：涉及原生 Session（如存在）、Chub Session 记录、关联任务、终端载体和微信槽位；不影响项目文件、用户配置或其他 Session。
3. **门禁**：明确检测到原生 Session 被外部进程占用时禁止归档；仅当 Chub 明确知道实时终端处于 `running`，或 Quick Worker 处于 `running`/`waiting_result` 时禁止归档。`unknown` owner、未知 phase、Chub 重启后的状态未知都不构成归档门禁；没有原生 Session 时不增加原生归档门禁，但仍禁止已知正在执行的 Chub 任务。
4. **结果**：对于未知或非执行状态，Web 和 ClawBot 统一优先尝试原生归档；原生归档成功后再清理 Quick Worker 任务、关闭终端、清理 Chub 记录并释放槽位；原生记录已归档时直接进入清理，不重复执行原生归档；原生归档明确失败或结果无法确认时不清理 Chub 侧数据，直接返回原生失败/未知原因。无原生 Session，或 Chub 映射已在并发对账中移除时，跳过原生动作，直接清理 Chub 侧数据、任务和槽位并按已完成处理。若原生归档结果已实际完成但后续清理中断，重试时先确认原生已归档，再继续清理，不重复执行原生归档。任一 Chub 清理或槽位释放结果无法确认时不得宣称归档完成，保留 Chub 记录和可重试/对账路径。

### 3.7 删除

1. **目标**：永久删除 Session 及其 Chub 关联数据；存在原生 Session 时同时删除原生记录。
2. **范围**：涉及原生 Session（如存在）、Chub Session 记录、任务历史、终端载体、Hook 和微信槽位；不影响项目文件、用户配置或其他 Session。
3. **门禁**：明确检测到原生 Session 被外部进程占用时禁止删除；Chub 实时终端/Quick Worker 执行中、Chub 重启后的 `unknown` owner 或未知 phase 都允许发起删除，不因 Chub 自身投影增加门禁。Quick Worker 不可用时，只要本地任务状态完整且该 Session 没有活动或未跟踪任务，仍允许删除；本地状态异常、活动任务或未跟踪任务仍失败关闭。没有原生 Session 时跳过原生删除，不因缺少原生 ID 增加门禁。
4. **结果**：存在原生 Session 时先取消 Quick Worker，并尽力关闭 Chub 实时终端载体，然后立即尝试原生删除；关闭载体失败、Chub 状态未知或历史 writer 标记不应阻止该原生调用。原生删除成功后再清理 Chub 关联数据、任务和槽位；原生删除明确失败或结果无法确认时，保留 Chub 记录和必要关联信息并返回原生原因/状态未知。原生删除结果无法确认时，重试前先查询原生是否已经删除：已删除则跳过原生动作并继续清理，仍存在才再次尝试，依然无法确认则保留映射并返回状态未知。若 Chub 映射已在并发对账中移除，删除按已完成处理并继续清理任务和槽位。Chub 清理结果无法确认时不得宣称删除完成，并保留后续重试或对账路径。没有原生 Session 时，Chub 侧记录和任务清理成功即视为删除成功。

## 4. 入口和操作边界

- `terminal` Session 只能进入实时终端；`quick` Session 只能进入快速交互。不存在两个入口之间的自动接管或类型切换。
- 原生 Session 被其他进程占用时，Chub 不得接管或继续向其写入。页面和写接口应提示：`This is open in another app, close it there to continue here.`
- Chub 自己持有终端或 Quick Worker 时，状态应明确显示 owner 和 phase；是否允许某个具体操作由对应操作的最终门禁决定。
- `unknown` 只在确有数据冲突或写入风险的操作上失败关闭，并提供刷新、等待或释放外部占用的恢复路径；不能把一个 Session 的未知状态扩散成全局不可用。

### 4.1 其他元数据

- `title`、权限、工作目录和槽位是 Session 元数据，不改变 `status`、`activity` 或原生上下文。
- `weixin_session_slot` 是 `S1`–`S9` 的后端分配结果；列表位置和标题不能生成槽位。
- 首页统一按创建时间倒序展示 Session，并以类型标记区分实时终端和快速交互。
- 内部翻译 Session 可以使用固定的 `quick` 记录，但不进入用户首页列表或微信槽位。
- Web 启动完成 Quick Worker 恢复后，会一次性清理历史上被误导入为普通 `runtime-session` 的翻译记录。仅当记录同时命中固定旧翻译工作目录、固定翻译提示词和历史自动发现标记，且 Worker、原生 writer 均确认空闲时，才先归档原生 Session、再移除 Chub 记录；状态未知、仍在使用或归档无法确认时保持原样，留待下次启动复查。该维护动作不会按标题模糊匹配，也不清理当前内部翻译 Session。

## 5. 状态来源和最小转换

每类状态只有一个权威来源：

| 状态 | 权威来源 |
| --- | --- |
| Chub Session 元数据、`status`、`activity` | AI Session Manager / Store |
| 原生 Session 和 writer | Runtime / Runtime Adapter |
| Quick Worker 任务、租约和任务终态 | Quick Worker |
| 实时终端 owner 和连接载体 | Interactive Supervisor |

页面、Hook、进程创建、HTTP 成功或任务已受理都不能单独宣称业务成功；必须由对应权威来源确认终态。事件缺失、乱序、文件不可读或外部状态无法确认时，保守写入 `unknown`，并保留可恢复路径。

最小状态关系如下：

```text
new --入口创建确认--> running + unknown
unknown --可信状态--> idle / working
idle --终端或快速任务开始--> working + 对应 source
working --确认 Turn 终态--> idle + none
running --停止确认--> stopped
任意状态 --无法继续管理--> error + unknown
```

这些转换只描述 Chub 的公开投影，不替代 Runtime 或 Quick Worker 的内部状态机。终端桥接、tmux、Hook 文件、Worker 租约、恢复和通知细节必须留在对应专项文档。

## 6. 维护边界

本文是以下内容的唯一说明：

- Session 标识、不可变入口类型、`status`、`activity` 和 `usage` 的核心语义。
- Chub Session 与原生 Session 的绑定关系及单 writer 原则。
- 首页和使用入口需要遵守的 owner、unknown 基本边界。

以下内容不在本文重复维护：

- Runtime 私有命令、Hook 文件、原生协议和 writer 锁格式：见 [Chub AI Runtime 架构设计](CHUB_AI_RUNTIME_DESIGN.md)。
- Quick Worker 任务、租约、恢复、通知和 Web 重启：见 [Chub Quick Worker 独立服务设计](CHUB_QUICK_WORKER_DESIGN.md)。
- OpenClaw、微信身份、固定指令和路由：见 [OpenClaw 定制集成设计](OPENCLAW_CUSTOMIZATION_DESIGN.md)。
- 页面组件、按钮、弹窗和视觉规范：见 [Chub 前端 UI 模块化设计](FRONTEND_UI_DESIGN.md)。

## 7. 当前维护边界与后续复检

当前实现已提供统一的 `usage` 投影、按需状态轮询、四项 Session 操作的最小门禁，以及原生优先、可重试的归档/删除清理流程。归档和删除均先处理原生 Session，再清理 Chub 记录、Quick Worker 任务、终端载体和槽位；无原生 Session 时直接清理 Chub 侧数据。

四项操作的服务端最终校验、操作日志和接口终态均优先于页面按钮状态。只有状态枚举、owner/phase 语义、原生映射、操作契约或用户可见提示变化时，才需要重新复检本节。

## 8. 验收范围与复检

- 已验证：Codex Session 首页列表、Session 使用状态投影、外部占用和未知状态展示、类型入口标识、四项操作门禁及相关自动化测试。
- 未在本文承诺：第二个 Runtime 的具体行为、Runtime/Worker 内部实现、未列出的入口和新的状态组合；未实际复检的平台不因此获得全平台承诺。
- 以下变化必须重新复检：状态枚举、owner/phase 语义、writer 归属判断、原生 Session 映射、Quick Worker 状态投影、Session 进入规则或页面用户可见提示。
