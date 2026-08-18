# Chub AI Session 状态模型设计

> 状态：已验收。
>
> 本文遵循[Chub 总体架构](CHUB_ARCHITECTURE_DESIGN.md)，只维护 Session、Activity、交互入口、槽位和单 writer 语义。长期 Runtime 架构与演进路线由[AI Runtime 架构演进设计](AI_RUNTIME_ARCHITECTURE_DESIGN.md)维护；非实时任务的执行、恢复、通知终态和 Web 重启由[快速交互独立 Worker 设计](QUICK_INTERACTION_WORKER_DESIGN.md)维护；微信业务路由由[Chub–OpenClaw 接入设计](CHUB_OPENCLAW_INTEGRATION_DESIGN.md)维护。

## 1. 模型概览

Chub 当前管理 Codex CLI Session，并用两个正交状态描述产品状态：

- `session_status`：Chub 受管交互运行时的生命周期。
- `activity_status`：该 Session 当前是否正在处理 Turn。
- `activity_source`：执行中的 Turn 来自实时终端还是快速交互；它是来源，不是第三个状态轴。

连接、任务、权限、标题、槽位和归档均为独立维度，不扩张核心状态枚举。

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

- 实时终端和快速交互必须通过同一 Session 互斥门禁。
- 快速交互执行时可以保留已有终端连接用于查看输出，但禁止新的终端输入。
- 已有快速任务、实时 Turn 或原生 writer 被占用时，拒绝第二个 Turn，不中断现有任务。
- 停止、权限切换、归档等操作也必须通过 Session 互斥检查；页面按钮状态不能代替后端最终校验。
- 微信当前绑定 Session 为 `unknown` 时，允许受控停止残留终端并确认 writer 已释放后再提交；无法确认时失败关闭。
- 子进程创建、停止请求返回或 Hook 到达都不是最终状态，必须确认运行时、Turn 或任务终态。

快速交互 Execution、Worker 租约、恢复屏障、翻译队列、通知和协调重启的权威状态由独立 Worker 体系维护，本文只消费其结果生成 Session Activity。

## 5. 独立维度

### 5.1 标识、标题和槽位

- `id` 是 Chub 内部稳定标识；`codex_session_id` 是原生 Codex 标识，二者不混用。
- `title` 是 Chub 本地展示元数据；空标题统一显示“未命名 Session”，修改标题不改变原生上下文、Activity 或权限。
- `weixin_session_slot` 是 `S1`–`S9` 的唯一来源。列表位置、标题或当前浏览器不能生成虚拟槽位。
- Web 无槽位 Session 显示 `S · 标题`；微信只展示已分配槽位的 Session。
- Session 列表按创建时间倒序排列，排序不改变槽位编号。

### 5.2 权限、归档和入口偏好

- 权限模式在创建时写入 Session 配置；设置页只保存后续新建 Session 的默认值。
- 归档表示从活动列表移除并释放微信槽位，不是新的 Session 状态；当前页面不提供恢复入口。
- 入口偏好默认为快速交互，按 Session 保存在当前浏览器，不跨设备同步，也不写入后端状态。
- 快速交互执行中仍可进入快速交互页查看进度，但不能进入实时终端。
- 翻译 Session 是内部只读 Session，不进入微信槽位；Web 是否展示只影响列表入口，不中断任务。

## 6. 状态来源与转换

Chub 从 tmux/Codex 运行时、Codex Hook、Worker 任务和持久化记录组合状态：

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

| Codex 信号 | Session | Activity |
| --- | --- | --- |
| 记录已创建、无原生 ID | `new` | `unknown` |
| tmux/Codex TUI 存活 | `running` | 由 Hook 或 Worker 决定 |
| 原生 Session 存在、tmux 不存在 | `stopped` | 由 Worker 任务决定 |
| 终端 Hook 开始 | 不变 | `working + terminal` |
| 快速任务开始 | 不变 | `working + quick` |
| 当前 Turn 结束 | 不变 | `idle + none` |

## 7. 当前维护边界

本文是以下内容的唯一说明：

- Session 与 Activity 枚举、合法组合和页面主状态。
- 实时终端与快速交互的共享 Session、入口语义和单 writer 约束。
- Chub/原生标识、标题、微信槽位、归档和浏览器入口偏好。
- Codex 运行时、Hook 和 Worker 投影到 Session 状态的规则。

以下内容由其他文档维护：

- Worker 任务、恢复、通知和 Web 重启：[快速交互独立 Worker 设计](QUICK_INTERACTION_WORKER_DESIGN.md)。
- 微信指令、绑定、权限和原路通知：[Chub–OpenClaw 接入设计](CHUB_OPENCLAW_INTEGRATION_DESIGN.md)。
- 前端分层、公共交互和视觉规范：[Chub 前端 UI 模块化设计](FRONTEND_UI_DESIGN.md)。

当前模型已在 Codex 会话、首页、快速交互和微信 Session 展示中落地，并完成 macOS、Ubuntu 验收。
