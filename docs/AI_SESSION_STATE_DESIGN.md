# Chub AI Session 状态模型设计

> 状态：已验收。
> 主要读者：AI Agent、实现和排障 Agent；维护人员用于确认 Session 状态、入口边界和验收范围。
> 本文负责：Chub AI Session 的 Session、Activity、交互入口、槽位、归档和单 writer 语义，遵循[Chub 总体架构](CHUB_ARCHITECTURE_DESIGN.md)。
> 本文不负责：长期 Runtime 架构与演进路线（见[Chub AI Runtime 架构设计](CHUB_AI_RUNTIME_DESIGN.md)）、非实时任务执行/恢复/通知/Worker 重启（见[Chub Quick Worker 独立服务设计](CHUB_QUICK_WORKER_DESIGN.md)）以及微信业务路由（见[OpenClaw 定制集成设计](OPENCLAW_CUSTOMIZATION_DESIGN.md)）。

## 1. 模型概览

Chub 当前管理 Codex CLI Session，并用两个正交状态描述产品状态：

- `session_status`：Chub 受管交互运行时的生命周期。
- `activity_status`：该 Session 当前是否正在处理 Turn。
- `activity_source`：执行中的 Turn 来自实时终端还是快速交互；它是来源，不是第三个状态轴。

连接、任务、权限、标题、槽位和归档均为独立维度，不扩张核心状态枚举。

每条 Chub 逻辑 Session 都必须带有由后端固定写入的 `runtime_id`；当前值为 `codex`，但不能把缺失字段默认猜测为 `codex`。原生 Session 映射由 `(runtime_id, native_session_id)` 共同唯一确定，因此不同 Runtime 可以使用同名原生 ID，同一 Runtime 不得重复绑定。

同一 Session 提供两个入口：

| 入口 | 用途 |
| --- | --- |
| 实时终端 | 进入 Codex 原生 TUI，查看实时输出、连续输入和处理审批 |
| 快速交互 | 从普通页面提交单次需求，在时间线查看状态和结果 |

两种入口共享同一个原生 Session，不是两类 Session。入口偏好只决定当前浏览器的默认导航，不代表运行状态。

## 2. 核心状态

### 2.1 Session 状态

| 状态 | 含义 |
| --- | --- |
| `new` | Chub 已创建记录，尚未建立可恢复的原生 Session |
| `running` | Chub 管理的 Codex 交互运行时存在 |
| `stopped` | 交互运行时不存在，但原生 Session 可以恢复 |
| `error` | 原生 Session 建立、恢复或管理失败，所有入口均不可用 |

`session_status` 不表示是否正在执行任务。`stopped` Session 仍可通过快速交互继续使用原生上下文。

### 2.2 Activity 状态与来源

| Activity | Source | 含义 |
| --- | --- | --- |
| `unknown` | `none` | 无法可靠确认活动状态；不得推断为空闲 |
| `idle` | `none` | 当前没有已确认的 Turn，等待输入 |
| `working` | `terminal` | 实时终端正在处理唯一 Turn |
| `working` | `quick` | 快速交互正在处理唯一 Turn |

约束：

- 同一 Session 同一时刻最多一个执行中 Turn。
- `activity_source` 只记录当前 Turn，结束后立即恢复为 `none`。
- `working + terminal` 必须同时满足 `session_status=running`。
- `stopped + working + quick` 合法，表示终端运行时已停止，但后台任务仍在执行。
- Activity 只描述 Chub 已确认的 Turn，提交前仍必须检查 Worker 租约和 Codex 原生 writer。
- 状态来源不完整、事件乱序或恢复无法确认时使用 `unknown`，不沿用可能过期的 `idle`。

## 3. 页面语义

| Session | Activity | 页面主状态 |
| --- | --- | --- |
| `new + unknown` | 尚未启动 | 尚未启动 · 可进入 |
| `running + working + terminal` | 实时执行 | 实时终端 · 执行中 |
| `running/stopped + working + quick` | 后台执行 | 快速交互 · 执行中 |
| `running/stopped + idle` | 可接受输入 | 会话 · 等待输入 |
| `running/stopped + unknown` | 状态不确定 | 会话 · 状态未知 |
| `error + unknown` | Session 不可用 | 会话异常 · 可重试 |

页面主状态优先级为：Session 错误、执行中的入口、空闲或未知状态。终端连接或 WebSocket 错误属于通道错误；只要快速交互仍可用，就不能把整个 Session 写成 `error`。

操作入口根据 `session_status` 决定直接进入还是恢复终端，但不能反向修改 Activity。

## 4. 单 writer 与任务边界

当前产品的终端与快速交互规则固定为：

1. 同一 Session 可以在两个 Web 页面打开实时终端；后进入的页面接管唯一终端连接，先前页面返回首页，但不得停止或中断 tmux 中正在执行的 Codex Turn。
2. 实时终端正在执行 Turn 时，快速交互必须拒绝提交；不得抢占、中断或并发写入该 Turn。
3. 实时终端已确认等待输入时，快速交互可以接管：关闭 Web 终端连接并停止受管终端，确认 writer 已释放后才提交 Worker；终端页面返回首页，Quick Worker 成为唯一 writer。

除上述执行中、外部 writer、归属无法确认或 writer 无法释放的边界外，不增加门禁。尤其是 Chub 受管实时终端在等待输入时仍保留原生 writer 进程，不能仅因该进程存在而拒绝快速交互接管。

- 实时终端和快速交互必须通过同一 Session 互斥门禁。快速交互提交到已确认空闲的 Chub 受管终端时，快速交互接管唯一 writer：先关闭终端连接并停止终端，确认原生 writer 已释放后再提交 Worker；原终端页面返回首页。
- 实时终端是同一个交互入口而非每个页面各自的 writer：同一 Session 可以从新页面重复进入，新页面接管唯一终端连接，旧页面断开并返回首页。
- 快速交互执行时拒绝进入实时终端；实时终端正在处理 Turn 时拒绝创建快速交互。仅“已确认空闲且归属 Chub”的终端可由快速交互接管；外部或归属无法确认的原生 writer 继续拒绝。原生 writer 探测用于后台任务提交和无法确认归属的新 writer；同一受管实时终端的页面重连可通过 tmux 或 Chub 终端进程标识确认归属，不阻塞重连。
- 已有快速任务、实时 Turn 或无法确认归属的原生 writer 被占用时，拒绝第二个 Turn，不中断现有任务。
- 停止、权限切换、归档等操作也必须通过 Session 互斥检查；页面按钮状态不能代替后端最终校验。
- 微信当前绑定 Session 为 `unknown` 时，允许受控停止残留终端并确认 writer 已释放后再提交；无法确认时失败关闭。
- 子进程创建、停止请求返回或 Runtime 活动事件到达都不是最终状态，必须确认运行时、Turn 或任务终态。

快速交互 Execution、Worker 租约、恢复屏障、翻译队列、通知和协调重启的权威状态由独立 Worker 体系维护，本文只消费其结果生成 Session Activity。

## 5. 独立维度

### 5.1 标识、标题和槽位

- `id` 是 Chub 内部稳定标识；当前 Codex API 的 `codex_session_id` 是原生 Codex 标识，内部统一使用不透明的 `native_session_id`，二者不混用。
- `runtime_id` 是后端固定的 Runtime 归属；页面、微信正文和任务请求不能自行指定或覆盖。
- `title` 是 Chub 本地展示元数据；空标题统一显示“未命名 Session”，修改标题不改变原生上下文、Activity 或权限。
- `weixin_session_slot` 是 `S1`–`S9` 的唯一来源。列表位置、标题或当前浏览器不能生成虚拟槽位。
- Web 无槽位 Session 显示 `S · 标题`；微信只展示已分配槽位的 Session。
- Session 列表按创建时间倒序排列，排序不改变槽位编号。

### 5.2 权限、归档和入口偏好

- 权限模式在创建时写入 Session 配置；设置页只保存后续新建 Session 的默认值。
- 归档表示从活动列表移除并释放微信槽位，不是新的 Session 状态；当前页面不提供恢复入口。
- 入口偏好默认为快速交互，按 Session 保存在当前浏览器，不跨设备同步，也不写入后端状态。
- 快速交互执行中仍可进入快速交互页查看进度，但不能进入实时终端；实时终端可从新页面重新进入并接管旧页面连接。
- 翻译 Session 是内部只读 Session，不进入微信槽位；Web 是否展示只影响列表入口，不中断任务。

## 6. 状态来源与转换

Chub 从 tmux/Codex 运行时、Runtime Adapter 规范化事件、Worker 任务和持久化记录组合状态：

```text
new --启动确认--> running + unknown
unknown --可信事件--> idle / working
idle --终端 Turn--> working + terminal
idle --快速任务--> working + quick
working --Turn 终态--> idle + none
running --停止确认--> stopped
stopped --恢复确认--> running + unknown
new/running/stopped --运行时失败--> error + unknown
```

可信规则：

- tmux 或进程存在只能证明 `running`，不能证明 `idle`。
- 明确 Turn 开始时才写入 `working`；明确终态且来源完整时才恢复 `idle`。
- Web 重启后由 Worker 恢复快速任务和租约投影；无法确认时保持 `unknown` 并关闭写入。
- Activity 变化不改变 Session 运行时生命周期。

当前 Codex 映射：

- `runtime_id` 由后端固定入口写入，当前生产值为 `codex`，客户端和微信正文不能指定。
- `native_session_id` 是对应 Runtime 的不透明标识；AI Session Store 以 `(runtime_id, native_session_id)` 约束唯一映射，不把不同 Runtime 的同名原生 ID 当成冲突。第二 Runtime 未接入前，生产仍只有 Codex 映射。
- Codex Hook/活动事件的路径、文件格式、权限、限长读取和清理由 Runtime Adapter 负责；本文只定义 Adapter 转换后的 Activity 语义，不允许上层按 Codex 私有文件自行判断状态。

| Codex 信号 | Session | Activity |
| --- | --- | --- |
| 记录已创建、无原生 ID | `new` | `unknown` |
| tmux/Codex TUI 存活 | `running` | 由 Adapter 活动事件或 Worker 决定 |
| 原生 Session 存在、tmux 不存在 | `stopped` | 由 Worker 任务决定 |
| Adapter 活动事件：终端 Turn 开始 | 不变 | `working + terminal` |
| 快速任务开始 | 不变 | `working + quick` |
| 当前 Turn 结束 | 不变 | `idle + none` |

## 7. 当前维护边界

本文是以下内容的唯一说明：

- Session 与 Activity 枚举、合法组合和页面主状态。
- 实时终端与快速交互的共享 Session、入口语义和单 writer 约束。
- Chub/原生标识、标题、微信槽位、归档和浏览器入口偏好。
- Runtime Adapter 规范化事件、实时运行时和 Worker 投影到 Session 状态的规则；Adapter 内部的 Codex Hook 格式不属于本文共享契约。

以下内容由其他文档维护：

- Worker 任务、恢复、通知和重启协调：[Chub Quick Worker 独立服务设计](CHUB_QUICK_WORKER_DESIGN.md)。
- 微信指令、绑定、权限和原路通知：[OpenClaw 定制集成设计](OPENCLAW_CUSTOMIZATION_DESIGN.md)。
- 前端分层、公共交互和视觉规范：[Chub 前端 UI 模块化设计](FRONTEND_UI_DESIGN.md)。

当前模型已在 Codex 会话、首页、快速交互和微信 Session 展示中落地，并完成 macOS、Ubuntu 验收。

## 8. 验收范围与复检

- 已验收范围：Codex Session 的状态枚举、Activity 投影、实时终端与快速交互入口、S1–S9 槽位、归档和单 writer 规则，以及首页、快速交互和微信 Session 展示；macOS、Ubuntu 当前范围均已验收。
- 未验证或不承诺：第二个真实 Runtime 的产品行为、Worker 或 OpenClaw 内部实现，以及本文没有列出的新入口或新状态组合。
- 复检触发：Session/Activity 枚举或合法组合、writer 仲裁、槽位/归档语义、Runtime 原生映射、Worker 状态投影或页面主状态变化时，必须重新执行对应自动化回归和受影响平台的最终状态验收。
