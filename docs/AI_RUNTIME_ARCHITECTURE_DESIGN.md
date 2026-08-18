# AI Runtime 架构演进设计

> 状态：阶段 0、阶段 1、阶段 2 已验收通过。
>
> 当前进度：阶段 0、阶段 1、阶段 2 均已于 2026-08-18 验收通过；阶段 3 及后续演进尚未启动。
>
> 本文在[Chub 总体架构](CHUB_ARCHITECTURE_DESIGN.md)约束下，维护 AI Runtime 的长期目标架构、职责边界、统一契约、演进阶段和验收门槛。第一阶段已经将 Codex 专用实现收敛到 Runtime Adapter/Runner 边界，为未来按需接入其他 Agent Runtime 建立了稳定基础。当前 Session 状态语义以[AI Session 状态模型](AI_SESSION_STATE_DESIGN.md)为准；非实时任务可靠性以[快速交互独立 Worker 设计](QUICK_INTERACTION_WORKER_DESIGN.md)为准。本文不表示 Chub 已支持多 Runtime，也不指定下一个接入产品。

## 1. 演进目标与结论

Chub 的长期目标是成为个人设备上的 AI 工作站控制面与可靠任务调度平台。Chub 统一拥有设备安全、业务入口、逻辑 AI Session、任务状态、需求储备、通知和用户可见终态；实际分析、编码和工具执行由可替换的 Agent Runtime 完成。

本专项遵循总体架构的模块化单体、状态单一所有者、可靠执行与外部适配分层原则。调整范围限制在 AI Session、交互终端、Quick Worker 和 Agent Runtime 边界，不同步重构设备管理、自动化、通知或 OpenClaw 领域。

本架构演进确认以下方向：

1. Codex 是当前唯一完整接入的 Agent Runtime，但不再作为 Chub 上层领域模型的永久边界。
2. Chub 建立运行时无关的 AI Session Manager，管理逻辑 Session 生命周期和业务状态。
3. Codex CLI、Hooks、本地 Session 发现、writer lock、模型目录、PTY 命令和结果解析下沉到 Codex Runtime Adapter。
4. Quick Worker 演进为运行时无关的可靠后台执行层，继续拥有任务、租约、超时、取消、恢复和终态，不接管全部 AI Session 生命周期。
5. 实时终端由 Session Manager 协调、Runtime Adapter 实现；后台任务由 Quick Worker 调度、Worker 侧 Runtime Runner 实现。两条入口共享同一逻辑 Session 和单 writer 规则。
6. 在没有第二个明确 Runtime 需求前，只完成能降低现有耦合且可由 Codex 回归证明的边界，不建设空泛插件框架。

## 2. 第一阶段实施与验收

第一阶段完成阶段 0“冻结现行契约”和阶段 1“建立 Runtime 适配边界”。目标是在不改变 AI 业务用户可见行为、公共接口、持久化格式或 Worker 协议的前提下，让 Codex 私有能力通过明确的 Adapter/Runner 边界提供，为后续通用化奠定可验证基础。项目资料导航为记录本次架构文档而同步调整：首页固定优先展示项目说明和总体架构，完整列表使用文档索引顺序；这是本轮唯一页面变化，不改变 AI Session 或任务交互。

### 2.1 本轮范围与结果

本轮已完成：

1. 固化 Codex Session、实时终端、快速交互、翻译、微信任务、Request 执行、权限、单 writer、恢复和通知终态的现行契约。
2. 建立最小 Runtime 能力模型、能力协议、规范化标识与结果模型，以及后端固定注册表；生产注册表当前只包含 `codex`，本阶段已抽取的 Adapter 能力如果声明后未实现对应协议则拒绝注册，调用缺失能力时失败关闭。Runner 通用注册仍留在阶段 2。
3. 将 Codex CLI 可用性、模型目录、Hooks、Session 发现、writer 探测、终端命令、原生事件和错误解析收拢到 Codex Adapter；兼容 Manager 只消费规范化 Session、模型和进程规格并转换为现有公开模型。
4. 将后台 Turn 的 Codex 命令、事件摘要与有界结果解析收拢到 Codex Runner；本轮仍由现有 Worker 协议固定选择它。
5. 保留 `CodexPtyManager`、现有 API 和业务服务作为兼容外观，逐项委托新的 Runtime 边界，不同步迁移所有调用方。
6. 建立 Runtime 契约测试和受控测试 Runtime，只用于验证共享层边界，不提供用户入口。

本轮明确不做：

- 不增加 Runtime 选择器，不接入第二个 Runtime。
- 不引入 AI Session Manager、Interactive Supervisor 或通用 `/api/ai/sessions`。
- 不修改 Quick Worker 协议版本、任务状态格式、Session 持久化字段或本机配置。
- 不迁移 `codex_session_id`，不改变 S1–S9、R1–R9、通知格式或微信指令。
- 不调整服务进程、安装方式，也不引入数据库、队列或新常驻服务。

### 2.2 实施策略与顺序

1. **建立行为基线。** 盘点 Codex 私有调用点，为现有入口、状态、错误、权限和恢复补齐契约回归；基线未稳定前不移动职责。
2. **先定义最小契约。** 只抽取当前 Codex 已被真实调用的能力、标识和结果，不为假设中的 Runtime 设计扩展点。
3. **先读后写地收拢 Adapter。** 依次迁移可用性与模型目录、Session 发现与状态探测、writer 探测，再迁移创建、停止、归档、Hook 和实时终端命令；每一步保持兼容外观和独立回归。
4. **单独收拢 Codex Runner。** 把后台命令、进程输入和事件/终态解析放入 Codex Runner，但保持现有 Worker 提交、存储、租约与恢复语义不变。通用 Runner 注册和协议字段留到阶段 2。
5. **固定注册，不接受外部选择。** Runtime 和 Runner 只能由后端注册表按受控业务规则选择，任何 API、微信输入或任务协议都不能携带任意实现、命令、路径或环境变量。
6. **兼容委托后再删除重复实现。** 只有新边界通过同一组回归后，原调用点才改为委托；旧逻辑没有调用方并完成搜索确认后再删除，不保留双写或双执行。

出现下列情况时停止当前步骤并先修订设计：必须改变公共响应、微信行为、持久化格式或 Worker 协议；无法无损映射权限；无法确认 Session 归属或 writer；需要新增常驻进程；现有行为与权威专项文档冲突。

### 2.3 本轮交付物

- 一份可由测试重复证明的现行行为与耦合点基线。
- Runtime 能力、能力协议、标识、规范化 Session/模型/进程/事件/Turn 结果和错误的最小共享契约。
- 只注册 `codex` 的固定 Runtime 注册表，以及不可进入生产入口的测试 Runtime。
- 分别承载交互/Session 能力和后台 Turn 能力的 Codex Adapter、Codex Runner。
- 继续保持现有外观的 `CodexPtyManager` 和 Worker 调用路径。
- Runtime 契约测试、Codex 专用测试和现有业务回归。

### 2.4 本轮验收标准

以下条件必须全部满足，阶段 0 和阶段 1 才算完成：

**用户行为与兼容性**

- Codex Session 创建、发现、进入、恢复、停止、重命名和归档行为不变。
- 实时终端、快速交互、翻译、微信普通任务和 Request 执行保持现有入口、状态和结果格式。
- S1–S9、R1–R9、模型、推理等级、权限配置、通知和额度展示不变。
- `/api/codex/*`、Worker 协议、现有状态文件及 macOS/Ubuntu 服务定义不变，无需数据迁移或重新安装。

**架构边界**

- Runtime 共享契约不读取 Codex 私有 Session 文件、Hooks、writer lock、配置键或事件格式。
- Codex 私有命令、路径、标识校验和事件解析只存在于 Codex Adapter/Runner；共享层只消费规范化模型。
- `CodexPtyManager` 作为兼容外观委托新边界，不新增 Codex 私有职责。
- Worker 的幂等、租约、进程组、超时、取消、恢复和终态所有权不变；阶段 1 不提前引入通用 Worker 协议。
- 生产注册表只包含后端固定的 `codex`，缺失能力或注册异常时失败关闭。

**可靠性与安全**

- 同一原生 Session 的实时终端与后台任务继续严格单 writer，无法确认时拒绝写入。
- 权限配置必须无损映射，不能静默提升权限或回退到更宽松模式。
- Runner 崩溃、超时、取消、事件异常、结果超限和 Session 映射异常仍收敛为现有可靠终态，不产生重复任务。
- Web 重启不打断 Worker 已接受任务，恢复后的状态、通知和协调重启不重复。
- 客户端仍不能选择任意 Runtime、可执行文件、命令、工作目录、路径或环境变量。

**验证门槛**

- Runtime 契约测试覆盖能力缺失或虚假声明、非法原生 ID、权限不可映射、writer 不可确认、事件异常和结果超限。
- Codex Adapter/Runner 测试覆盖当前支持的 Session、终端、后台 Turn、模型和权限能力。
- 现有相关测试与全量测试通过；没有新增无期限兼容分支、双写或未使用抽象。
- macOS 与 Ubuntu 的业务和服务语义保持一致；无法实机验证的平台在交付结果中明确说明。

### 2.5 阶段 1 整体情况与验收结论

阶段 0 作为现行行为基线，与阶段 1 一并完成维护者验收。2026-08-18 最终结论：**阶段 1 验收通过**。

**目标**

- 在不改变 AI 业务用户可见行为、公共接口、持久化格式或 Worker 协议的前提下，建立明确、可验证的 Agent Runtime 适配边界。
- 将 Codex 私有命令、Session 发现、Hooks、writer、模型、终端和事件解析从共享业务职责中收拢，为后续通用化奠定基础。
- 保持权限无损映射、单 writer、可靠终态和固定后端选择，不因内部解耦放宽安全边界。

**实施方案**

- 建立最小 Runtime 能力、协议、规范化模型、标准错误和固定注册表，生产环境只注册 `codex`。
- 由 Codex Adapter 承担 Session、终端、模型、Hooks、writer 和原生维护操作，由 Codex Runner 承担后台命令、事件摘要和有界结果解析。
- 保留 `CodexPtyManager`、现有 API 和业务服务作为兼容外观；Quick Worker 继续拥有幂等、租约、进程组、超时、取消、恢复和终态。
- 通过共享契约测试、Codex 专用测试和现有业务回归逐步替换旧调用点，不保留双写、双执行或客户端 Runtime 选择入口。

**已验收内容**

- Codex Session、实时终端、快速交互、翻译、微信普通任务、Request、S1–S9、R1–R9、模型、推理等级、权限、通知和额度展示保持原有入口与语义；维护者实际使用未发现回归。
- `/api/codex/*`、Quick Worker 协议版本、状态文件、本机配置和服务定义未改变，无需数据迁移或重新安装。
- Runtime 能力缺失或虚假声明、非法原生 Session ID、权限不可映射、writer 不可确认、事件异常、结果超限及归档/恢复异常均失败关闭；超长原生标题有界保留，不再静默丢失。
- 项目资料导航按约定固定优先展示项目说明和总体架构，完整列表遵循文档索引顺序；这是本轮唯一页面变化，AI Session 和任务交互未改变。
- Python 全量测试 `1086 passed, 36 skipped`，相关编译与 diff 检查通过。Ubuntu 上完成 Web 健康检查和独立 Quick Worker 重载验证：Worker generation 与 PID 已更新，协议版本保持 `6`，状态为 `ready`，损坏任务为 `0`，重载后的快速交互执行链路正常。

本轮未在 macOS 实机重复运行验收；macOS LaunchAgent 与 Ubuntu systemd user service 的定义和业务语义未调整。阶段 2 的通用 Worker 协议、Runner 注册和 `runtime_id` 字段仍需单独立项，不属于阶段 1 遗留问题。

## 3. 当前基线与演进动因

当前架构已经具备可靠任务执行基础，并完成 Runtime 适配边界和通用 Worker 的代码收敛，但产品与 Session 领域外观仍以 Codex 为中心：

- `CodexPtyManager` 继续拥有兼容外观、逻辑 Session 协调、Session Store 和 tmux/ttyd 生命周期，Codex 原生发现、模型、Hooks、writer、终端命令和维护操作已经委托 Adapter。
- Quick Worker 协议 `7` 已使用通用 Runtime 任务、固定 Runner 注册表和 Worker 生成的执行标识；Codex 命令、原生事件与结果解释只存在于 Codex Runner 边界。
- Worker 继续拥有幂等、租约、进程组、超时、取消、恢复和终态；Codex 私有数据与命令解释由固定 Adapter/Runner 承担。
- API、配置、操作日志和部分业务服务仍使用 Codex 命名；Chub Session ID 与 Codex 原生 Session ID 已经分离，并通过规范化模型和受控映射衔接。
- 共享契约已有独立测试 Runtime 验证，但生产注册表仍只包含 `codex`，尚未形成多 Runtime 产品能力。

完成第二阶段代码实施后仍需渐进处理以下问题：

- 接入第二个 Runtime 前，仍需在阶段 3 完成 AI Session Manager，避免同步修改所有业务入口。
- 不同 Runtime 的权限、事件、恢复、锁和模型语义必须继续通过能力协议显式表达，不能伪装成 Codex。
- 公共命名、API 和持久化迁移只能在内部边界稳定后单独设计，不能把第一阶段的内部解耦扩大成兼容性变化。
- 当前只有一个生产 Runtime，契约边界虽可由测试证明，跨 Runtime 的真实差异仍需在第二个明确接入需求出现后验证。

## 4. 设计原则与非目标

### 4.1 设计原则

- **逻辑状态归 Chub。** 页面、微信槽位、Request、权限授权关系和用户可见终态不由 Runtime 决定。
- **原生状态归 Adapter。** 原生 Session、命令参数、事件和本地数据格式只在对应 Runtime 实现内解释。
- **可靠执行归 Worker。** 后台任务的幂等、租约、进程组、超时、取消、恢复和终态继续由 Worker 统一保证。
- **能力显式声明。** Runtime 不支持的能力明确不可用，不用猜测、静默降级或不安全模拟补齐。
- **标识保持分层。** Chub Session ID 稳定且与 Runtime 无关；原生 Session ID 对共享层是受限长度的不透明字符串。
- **失败关闭。** 无法确认 writer、Session 映射、任务终态或协议版本时拒绝写入，不自动切换 Runtime 或创建重复任务。
- **渐进兼容。** 先保持 Codex 行为完全一致，再迁移命名、API 和持久化；不一次性重写现有链路。

### 4.2 非目标

本设计当前不包含：

- 立即接入 DeepSeek-TUI、Claude Code 或其他 Runtime。
- 在页面或微信增加 Runtime 选择器。
- 在不同 Runtime 之间迁移或合并历史上下文。
- 建设远程 Worker、分布式队列、数据库或通用插件市场。
- 把模型供应商 API 兼容等同于 Agent Runtime 兼容。
- 为统一接口削弱现有 Codex 权限、沙箱、恢复或终态保证。

## 5. 目标架构

```text
Browser / WeChat / OpenClaw / CLI
                |
                v
        Chub Web / Business Services
        - 身份、权限和固定业务规则
        - Request、通知和用户可见终态
                |
                v
          AI Session Manager
        - Chub 逻辑 Session 生命周期
        - Runtime 选择与能力校验
        - 单 writer 协调与状态投影
          /                     \
         v                       v
Interactive Supervisor       Quick Worker
- 终端访问与连接             - 后台任务与 Session 租约
- 实时运行时生命周期         - 幂等、取消、超时和恢复
         \                       /
          v                     v
             Agent Runtime Layer
        - 统一能力与结果契约
        - Runtime 专用 Adapter/Runner
                     |
                     v
           Codex CLI（当前唯一实现）
```

该架构不要求新增常驻服务。第一阶段可在现有 Web 和 Worker 进程内复用同一套 Runtime 契约与 Codex 实现；只有真实运行隔离需求出现时，才评估独立 Runtime Service。

## 6. 组件职责

### 6.1 AI Session Manager

AI Session Manager 是 Chub 的领域服务，拥有：

- 创建、读取、列出、重命名、停止和归档逻辑 Session。
- 保存 `runtime_id`、工作区、权限配置、模型配置、标题和原生 Session 映射。
- 校验 Runtime 能力，并把通用操作交给指定 Adapter。
- 汇总交互运行时、Worker 租约和 Runtime 事件，投影 Session/Activity 状态。
- 协调实时终端与后台任务的单 writer 门禁。
- 向页面、微信和其他业务服务提供稳定的 Chub Session 视图。

AI Session Manager 不负责：

- 拼接 Runtime CLI 参数。
- 解析 Runtime 私有文件、锁、Hook 或事件。
- 直接管理后台 Runner 进程组。
- 将某个 Runtime 的原生状态直接暴露为产品状态。

### 6.2 Agent Runtime Adapter

每个 Runtime Adapter 只实现该 Runtime 的具体能力，包括：

- 可用性、版本和依赖检查。
- 模型与推理等级目录。
- 原生 Session 的创建、恢复、停止、归档和发现。
- 实时终端启动命令及生命周期探测。
- 后台 Turn 的 Runner 规格、结构化事件和最终结果解析。
- 权限配置映射和支持能力校验。
- 原生 writer/活动状态探测。
- 原生错误到 Chub 标准错误的有界映射。

共享层只使用规范化结果，不依赖 Codex 的 UUID、JSONL、数据库、Hooks、配置键或锁文件路径。

### 6.3 Quick Worker

Quick Worker 长期保持以下职责：

- 接受带稳定任务 ID、Chub Session ID 和 `runtime_id` 的后台任务。
- 原子持有逻辑 Session 租约，保证同一 Session 只有一个后台 writer。
- 通过后端固定 Runtime 注册表选择 Runner，客户端不能指定可执行文件、任意路径或环境变量。
- 监督 Runner 进程组，处理超时、取消、退出和结果大小限制。
- 保存规范化事件、原生 Session 映射和任务终态，供 Web 重启后恢复。
- 在 Runtime 不可用、协议不兼容或原生 Session 无法确认时失败关闭。

Worker 不拥有 Session 标题、微信槽位、Request、通知路由或页面状态，也不负责实时终端连接。

### 6.4 Interactive Supervisor

实时终端继续与后台 Worker 分离：

- Session Manager 负责访问授权和单 writer 协调。
- Interactive Supervisor 负责终端票据、连接和受管运行时进程。
- Runtime Adapter 提供固定终端启动方式、原生 Session 恢复和状态探测。
- Runtime 不支持交互终端时，该能力明确隐藏或禁用，后台任务仍可独立可用。

## 7. 统一契约

### 7.1 Runtime 能力

每个 Runtime 必须声明能力，而不是让上层通过产品名称推断：

| 能力 | 含义 |
| --- | --- |
| `runtime_status` | 可确认 CLI/Runtime 可用性、版本和协议兼容性 |
| `background_turn` | 可执行受监督的非交互 Turn 并返回可靠终态 |
| `task_cancel` | 可取消完整执行进程组并收敛到可靠终态 |
| `native_session_mapping` | 可返回并校验归属于当前 Runtime 的不透明原生 Session 标识 |
| `interactive_terminal` | 可启动或恢复受管实时终端 |
| `session_resume` | 可继续指定原生 Session |
| `session_archive` | 可安全归档原生 Session |
| `structured_events` | 可提供有界、可校验的结构化执行事件 |
| `writer_probe` | 可确认原生 Session 是否存在其他 writer |
| `model_catalog` | 可列出可选模型与推理等级 |
| `permission_profiles` | 可映射 Chub 要求的权限模式 |

能力按接入范围分级：

- **后台任务最低门槛**：必须具备 `runtime_status`、`background_turn`、`task_cancel`、`native_session_mapping`、`structured_events` 和 `permission_profiles`；任一项缺失都不能作为正式后台 Runtime 接入。
- **共享 Session 门槛**：后台任务需要继续已有原生 Session 时，额外要求 `session_resume`；需要和实时终端共用同一逻辑 Session 时，再要求可靠 `writer_probe`。
- **交互终端门槛**：开放实时终端必须具备 `interactive_terminal`，并满足对应的恢复、进程监督和 writer 仲裁要求。
- **可选能力**：`session_archive` 和 `model_catalog` 缺失时关闭对应入口，不以兼容文案或上层猜测模拟支持。

当前 Codex Adapter 必须声明并保持现有能力。候选 Runtime 先通过相应级别的契约测试，再进入产品接入；不能通过降低可靠终态、权限或单 writer 标准换取表面兼容。

其中 `task_cancel` 表示 Runtime 的后台执行可以纳入 Worker 的统一取消契约，不表示把取消所有权交给 Runner。进程组、超时、取消请求和最终任务状态仍由 Worker 核心管理；Runner 只提供 Runtime 专用的固定执行规格与结果解释。

### 7.2 标准标识与状态

- `session_id`：Chub 生成的稳定逻辑 Session ID。
- `runtime_id`：后端固定注册的 Runtime 标识，当前为 `codex`。
- `native_session_id`：Runtime 返回的不透明原生标识，只做格式、长度和归属校验。
- `task_id`：Quick Worker 幂等任务标识。
- `execution_id`：一次 Runner 执行标识，用于进程和事件关联，不替代任务 ID。

Session 与 Activity 枚举继续使用现行状态模型。Runtime 私有事件必须先转换为规范化的 Session、Turn 和任务事件，再由上层消费。

### 7.3 权限语义

Chub 保留产品级权限配置，Adapter 负责映射：

- Adapter 必须明确报告某个权限配置是否可无损实现。
- 无法映射时拒绝创建或执行，不自动提升权限。
- 微信 Chub 模式的 Full access 仍由入口绑定关系授权，不成为 Session 或 Runtime 的永久属性。
- Runtime 自身的确认提示不能替代 Chub 对入口、工作区和任务类型的校验。

## 8. 数据、API 与升级策略

### 8.1 持久化

阶段 2 不修改现有 Session Store，也不在服务启动时解释、迁移或补写旧 Session 字段。目标 Session 记录中的 `runtime_id` 和通用 `native_session_id` 留到阶段 3 单独设计并统一升级；微信 S1–S9 槽位仍只绑定稳定的 Chub Session ID。

Quick Worker 已统一使用协议 `7` 的当前版本目录。跨协议升级在确认排空后直接清理固定旧协议任务、墓碑、租约和交付覆盖数据，不读取、迁移、展示、恢复或重派旧任务，也不保留旧协议模型、读取器、迁移器或启动兼容开关。

### 8.2 API 与用户界面

迁移初期保留 `/api/codex/*` 和现有页面行为，内部先改用 Session Manager。通用 API 只有在内部边界稳定后再引入，例如 `/api/ai/sessions`；旧 API 的弃用必须单独设计，不能在内部重构时顺带删除。

产品展示可以逐步使用“AI Session”，同时明确当前 Runtime 为 Codex。模型、推理等级和终端入口根据 Runtime 能力返回，不为尚未接入的 Runtime 增加空选择项。

微信固定指令、S1–S9 槽位、Request 和通知格式不因内部解耦改变。只有用户可见行为发生变化时才调整能力清单第 4 节。

### 8.3 Worker 协议

Worker 协议升级后，提交规格使用通用 Runtime 字段和规范化结果：

- 协议版本必须精确匹配，不做字段猜测或模糊降级。
- Web 与 Worker 同步发布，升级前先通过本机受限维护入口让 Worker 进入 `draining`；进入后原子拒绝新提交，但继续提供健康、查询、取消和交付确认，并让已受理任务按原语义收敛。
- drain 完成必须表示当前 Worker 不存在 `queued`、`accepted`、`starting`、`running` 或结果尚未确定的任务；否则中止升级并继续使用原版本，不能让新 Worker 猜测恢复。
- 新版本的任务操作只接受和写入新协议；健康诊断协议保持独立且受限。部署维护直接删除固定旧协议目录，旧任务结果、墓碑、租约、交付状态和任务 ID 占用均不保留。
- Runtime Runner 只能来自固定注册表；协议不能携带任意可执行文件、命令、工作目录或环境变量。
- Runner 事件、stdout、stderr 和最终结果继续执行权限、字节、行长、数量和敏感信息限制。

## 9. 演进阶段

### 阶段 0：冻结现行契约（已完成，验收通过）

- 将本文作为长期架构入口，记录现有 Codex 耦合点和不得退化的行为。
- 为 Session、Worker、权限、终态和恢复补齐面向契约的回归测试。
- 不改变 API、页面、微信、服务或持久化。

完成标准：现有行为有可重复基线，架构名词和状态所有权无冲突。

### 阶段 1：建立 Runtime 适配边界（已完成，验收通过）

- 新建轻量 Runtime 契约和固定注册表。
- 将 Codex 命令、Hooks、发现、模型目录、writer 探测和事件解析收拢为 Codex Adapter/Runner。
- `CodexPtyManager` 先作为兼容外观委托新边界，不立即迁移所有调用方。
- 使用测试 Runtime 验证共享层不读取 Codex 私有数据，但不提供用户入口。

完成标准：Codex 功能和用户行为不变，Runtime 专用测试与共享契约测试分离。

### 阶段 2：通用化 Quick Worker（已完成，验收通过）

阶段 2 的目标是在不改变现有用户入口和业务语义的前提下，把 Quick Worker 从“可靠执行 Codex 任务”收敛为“按固定 Runtime 注册执行后台任务”的通用可靠执行层。本阶段只证明 Worker 与 Runtime 的边界，不以接入第二个生产 Runtime 作为完成条件。

#### 9.2.1 目标与范围

本阶段必须完成：

1. **通用任务契约。** Worker 提交规格和幂等摘要使用不可变的 `runtime_id` 与通用 `native_session_id`；Worker 在 Runner 真正启动前生成 `execution_id`，并由状态、规范化事件和终态共同保存。生产协议与生产任务记录不再使用 `CodexTaskSubmission`、`runner_kind=codex` 或 `codex_session_id` 作为共享模型。
2. **固定 Runner 注册。** 在 Worker 组合入口建立后端固定的 Runtime Runner 注册表，当前生产环境仍只注册 `codex`。`runtime_id` 由 Chub 内部根据受控业务上下文解析并校验，不从页面、微信正文或其他外部输入直接选择。
3. **职责彻底分离。** Worker 核心继续拥有幂等、Session 租约、翻译 FIFO、固定工作区解析、进程组、输入输出文件、超时、取消、恢复、终态和资源上限；Runner 只负责 Runtime 可用性与能力校验、固定进程规格、原生事件解释、原生 Session 映射和规范化结果解析。
4. **覆盖全部后台任务。** 页面快速交互、微信普通任务和翻译任务使用同一通用协议与持久化模型；`standard`、`weixin`、`translation` 仍是 Chub 业务任务类型，不能被错误下沉为 Codex Runner 私有分支。
5. **安全完成协议切换。** 建立可观察、可等待的受控 drain；协议升级统一切换代码和服务，直接清理固定旧协议目录，不迁移、读取或恢复旧任务。

本阶段明确不做：

- 不接入第二个生产 Runtime，不新增 Runtime 选择器，也不向外部 API 暴露 `runtime_id` 选择能力。
- 不建设 AI Session Manager，不迁移现有 Session Store、`/api/codex/*`、`CodexPtyManager` 兼容外观或产品展示命名。
- 不改变 S1–S9、R1–R9、Session 标题与归档、微信固定指令、通知路由、额度展示或用户可见结果格式。
- 不改变实时终端、tmux/ttyd、服务进程数量或 Web 与 Worker 的独立部署关系，不引入数据库、队列或新常驻服务。
- 不为未来 Runtime 预设通用插件生命周期；只抽取当前 Codex 后台路径能够实际证明的 Runner 契约。

#### 9.2.2 实施方案与顺序

1. **冻结阶段 1 基线。** 以当前 Worker 协议、任务状态、快速交互、微信、翻译、通知、协调 Web 重启和单 writer 行为作为回归基线，先补齐协议、持久化和故障场景测试，再修改结构。
2. **先交付排空能力。** 增加仅限本机可信同用户调用的 drain 维护动作和可观察状态，并按操作 ID 记录 `requested`、`started`、`succeeded` 或 `failed`；普通停止信号导致任务中断，不能视为受控 drain。
3. **定义最小 Runner 契约。** 基于阶段 1 的 Runtime 能力与规范化模型，定义 Worker 需要的 Runner 描述、请求校验、进程规格、事件摘要和 Turn 结果；注册时校验声明能力与实现一致，缺失能力、Runtime 不可用或权限不能无损映射时在任务受理前失败关闭。
4. **通用化 Worker 核心。** 先替换任务模型和固定注册表，再依次替换进程准备、原生 Session 观察、结果解析和恢复路径。每一步都保持 Worker 对租约、翻译队列、进程组和终态的所有权，不保留 Codex 与通用路径双写或双执行。
5. **收敛 Web 提交适配。** 现有业务入口继续使用原有 Session 和 API；Web 内部固定补充 `runtime_id=codex` 并转换为通用 Worker 请求。请求重试必须复用原任务 ID，相同任务 ID 但 Runtime、原生 Session 或其他不可变规格不同必须返回冲突。
6. **执行协议升级。** 当前 Worker 原子进入 `draining` 后等待所有排队、活动和不确定任务收敛，再停止 Worker、清理固定旧协议目录并启动新版本。跨协议升级不保留旧任务结果、通知状态或任务 ID 占用。
7. **恢复并开放流量。** 新 Worker 启动后确认当前协议版本、`ready`、新 generation、零损坏记录及固定 `codex` Runner 可用；Web 完成当前协议任务、租约、翻译和通知对账后才重新开放 Session 写入。任一步失败都保持写入关闭，不自动回退或改投其他 Runtime。
8. **保持当前协议唯一。** Worker 生产代码、查询和恢复只理解当前协议；升级清理由维护命令按固定目录执行，不在 Worker 内保留旧模型、读取器、迁移器或兼容开关。

#### 9.2.3 验收标准

以下条件必须全部满足，阶段 2 才能标记为“验收通过”。

**架构与契约**

- Worker 生产提交、任务记录、查询投影和终态均使用通用 Runtime 标识；`runtime_id` 与提交时已有的原生 Session 标识纳入不可变规格和幂等校验。`execution_id` 由 Worker 生成，不能由客户端指定或替代任务 ID，并在同一次执行的状态、事件和终态中保持一致。
- Worker 核心不再构造 Codex 命令、校验 Codex UUID、解析 Codex 事件或读取 Codex 结果；Codex 专有行为只存在于 Codex Adapter/Runner 及固定注册的组合入口。
- 生产注册表只包含 `codex`，受控测试 Runner 不能进入生产入口。未知 Runtime、重复注册、能力虚假声明、权限不可映射或 Runtime 不可用均在任务受理前被拒绝。
- Worker 继续独立拥有租约、翻译 FIFO、进程组监管、超时、取消、恢复和终态；Runner 启动或 Tool Call 创建不能被视为任务成功。

**用户行为与兼容性**

- 页面快速交互、微信普通任务、翻译任务、Request 执行及其结果、通知和协调 Web 重启保持现有入口、状态和用户可见格式。
- Codex Session 创建、恢复、停止、重命名、归档、实时终端、S1–S9、R1–R9、模型、推理等级和权限配置不变。
- `/api/codex/*`、现有 Session Store、本机配置和页面均不要求迁移或重新配置；本阶段没有新增页面或交互入口。
- 同一逻辑 Session 的实时终端和后台任务仍严格单 writer；翻译 FIFO 的顺序、容量、等待期限、派生任务幂等及失败关闭语义不变。

**Drain、升级与恢复**

- Worker 确认进入 `draining` 后不再接受任何新主任务或翻译任务，同时健康、查询、取消和交付确认仍可用；已受理任务可以自然完成、取消、超时或明确失败。
- 排空及后续 Worker 重载使用不可复用的操作 ID，记录目标 generation、`requested`、`started`、`succeeded` 和 `failed`；只有排空完成及新实例最终健康确认后才能记录相应成功，旧进程无法确认的结果不得伪造。
- 健康状态能够有界、准确地反映当前协议版本、generation、排队/活动任务和损坏记录；只有当前 Worker 的排队、活动及不确定任务均为零时才允许受控重载。
- 新 Worker 只接受、读取和写入当前协议。升级维护删除固定旧协议目录，不读取、迁移、恢复或重派旧任务，也不保留旧任务 ID 占用。
- 排空期间出现新提交竞争、活动任务无法收敛、Worker 启动失败、协议不匹配或恢复对账失败时，升级中止或写入保持关闭，不产生半写、重复执行、遗失租约或误报成功。
- 升级成功以新 Worker generation、精确协议、`ready` 状态、固定 Runner 可用和 Web 恢复门禁重新开放共同确认，不能只依据服务管理命令返回成功或进程已创建。

**可靠性与安全**

- Runner 崩溃、启动异常、超时、取消、事件乱序或冲突、原生 Session 无法确认、结果缺失或超限均收敛为唯一明确终态，并最终安全释放租约；结果不确定时不得自动重放。
- 当前协议内相同任务 ID 与相同不可变规格保持幂等；Runtime、原生 Session、工作区、权限、模型、任务类型或正文摘要不同必须冲突。
- 客户端不能提供任意 Runtime 实现、可执行文件、系统命令、工作目录、文件路径或环境变量；工作区、Runner 和运行环境继续由后端固定映射。
- 任务目录、协议记录、事件、stdout、stderr 和结果继续满足所有权、`700/600` 权限、原子写入、字节数、行长、条数、非 UTF-8 处理和敏感信息保护要求。

**验证门槛**

- Runner 契约测试至少覆盖正常执行、缺失或虚假能力、非法或冲突原生标识、事件乱序、非 UTF-8、结果缺失与超限、权限不可映射和进程异常。
- Worker 测试至少覆盖普通/微信/翻译任务、幂等冲突、单 writer、队列容量与期限、取消/超时/进程组、Web 重启恢复、Worker 异常恢复、通知确认和协调重启。
- 协议升级测试至少覆盖 drain 与提交竞争、排队任务排空、固定旧目录清理、当前目录保留、generation 变化和恢复门禁；测试替身不得操作真实服务或真实本机数据。
- 相关测试、全量 Python 测试、Shell 语法检查、文档一致性检查及 `git diff --check` 全部通过；不存在双写、双执行、无期限旧协议分支或未使用抽象。
- 本机完成受控 drain、Worker 独立重载、健康/generation、任务恢复和 Web 不误重启 Worker 的实际验收。其他平台继续保持实现一致性，但不作为本阶段验收阻断项。

完成标准：Worker 核心只拥有运行时无关的可靠执行语义，Codex 专有行为全部位于固定 Adapter/Runner 边界；协议升级可安全排空、切换和恢复，现有业务无用户可见回归。

#### 9.2.4 当前实施与验收状态

截至 2026-08-18，阶段 2 已完成以下实施：

- Quick Worker 使用固定 Runtime Runner 注册表，生产组合只注册 `codex`；受控测试 Runner 仅在测试开关下存在。页面、微信和翻译提交均由 Web 内部固定映射到通用 Runtime 任务，不向外部入口提供 Runtime 选择。
- Worker 协议升级为精确版本 `7`，任务规格、状态、规范化 Runtime 事件、终态和查询投影统一保存 `runtime_id`；`execution_id` 由 Worker 在启动 Runner 前生成并保持一致。新协议使用独立版本目录，不与旧记录双写。
- Worker 生产代码只接受协议 `7`，旧协议模型、终态读取、交付确认和任务 ID 占用分支已经删除。安装和 Worker 重载只清理 `tasks`、`tombstones`、`session-leases` 与旧交付覆盖目录，保留 `tasks-v7`、`tombstones-v7` 和 `session-leases-v7`。
- Worker 提供受控 `draining` 状态，排空期间拒绝新主任务和翻译任务，但保留健康、查询、取消和交付确认；排空操作记录目标 generation 和完整操作状态。`chub worker-reload` 在协议 `7` 下先排空再独立重载 Worker，并以新 generation、精确协议、零损坏记录和 `codex` Runner 可用确认接管。
- Runner 只接受当前协议参数和任务规格，不保留旧参数映射或旧任务执行桥接。跨协议升级使用 `chub install --force` 统一安装当前版本并直接清理旧数据，旧任务会被中断且不会继续、迁移或自动重放。
- Web 与 Worker 分别写入独立操作日志，日志详情页可分别查看；页面业务入口、Session Store、`/api/codex/*`、微信固定指令、通知格式和服务进程数量未改变，Worker 重载不重启 Web。

自动化验证已覆盖通用 Runner 注册与能力拒绝、普通/微信/翻译执行、`execution_id` 关联、drain 与提交原子门禁、排队任务收敛、取消、当前协议损坏与活动记录阻断、固定旧目录清理、generation/恢复门禁、Shell 语法和服务命令回归。本轮代码 Review 已补齐提交与排空竞态、普通维护命令活动任务保护、Web 对新 Worker 的最终就绪确认及 Web/Worker 操作日志隔离。

2026-08-18 本机运行态验收通过：受控 Worker 重载完成，generation 已变化，协议 `7`、`ready`、零损坏记录和 `codex` Runner 可用均确认；固定旧协议目录已清理，Web 与 Worker 分别持有独立操作日志。新 Worker 实例上的页面快速交互正常执行，另行提交的真实翻译任务成功返回约定的润色与英文结构并完成交付确认。全量 Python 测试 `1115 passed, 36 skipped`，相关编译、Shell 语法和 diff 检查通过。阶段 2 据此验收通过，其他平台不作为本阶段验收阻断项。

### 阶段 3：建立 AI Session Manager

- 将逻辑 Session 生命周期、状态投影和能力校验集中到 Session Manager。
- 页面、微信调度和 API 逐步从 `CodexPtyManager` 转向通用 Session 服务。
- Interactive Supervisor 通过 Adapter 启动 Codex 实时终端。
- 完成持久化字段迁移，同时保留既有 API 和用户行为。

完成标准：上层业务不再读取 Codex 原生 ID、Hooks、锁或本地存储格式。

### 阶段 4：收敛产品命名与兼容入口

- 根据已稳定的内部边界评估通用 API 和“AI Session”展示。
- 明确旧 Codex API、配置和操作日志名称的兼容周期。
- 同步 README、状态模型、Worker 设计、能力清单和跨平台部署说明。

完成标准：现状与长期定位一致，兼容路径有明确退出条件，不保留无期限双轨实现。

### 阶段 5：按真实需求接入第二个 Runtime

- 先完成能力、权限、Session、事件、取消、恢复和跨平台评估。
- 只实现对应 Adapter/Runner，不修改上层业务语义。
- 无法满足单 writer、可靠终态或权限边界时，不进入正式接入。

该阶段不是当前承诺，也不指定候选产品。

## 10. 全阶段验证与验收门槛

每个实施阶段至少验证：

- 现有 Codex 实时终端、快速交互、翻译、微信普通任务和 Request 执行行为不变。
- Session 创建、恢复、停止、重命名、归档、槽位和标题保持一致；项目资料导航调整按第 2.1 节单独验收。
- 同一 Session 的实时终端与后台任务继续严格单 writer。
- Web 重启不打断 Worker 已接受任务，恢复后状态和通知正确收敛。
- Worker 重启、协议不兼容、Runner 崩溃、超时、取消和结果不确定均失败关闭。
- 权限模式不提升，客户端不能选择任意 Runtime、命令、路径或环境变量。
- 当前版本 Session 和任务可稳定读取；跨协议升级排空后清理固定旧 Worker 数据，不保留启动兼容路径。
- 当前维护机器完成服务安装、升级和恢复实测；其他平台只检查实现边界一致性，不作为当前阶段验收阻断项。

除现有业务测试外，应建立 Runtime 契约测试，使用受控测试实现覆盖：能力缺失、非法原生 ID、事件乱序、多个 Session ID、结果超限、writer 不可确认和进程异常。测试实现只用于证明边界，不成为生产 Runtime。

## 11. 风险与控制

| 风险 | 控制方式 |
| --- | --- |
| 为未来需求过度抽象 | 只抽取当前 Codex 已证明的能力；第二实现出现前不建设通用插件系统 |
| 内部改名引发行为回归 | 兼容外观先委托新实现，分阶段迁移调用方 |
| Worker 协议与数据不兼容 | 精确版本、活动任务 drain、固定旧目录清理和当前协议恢复测试 |
| 现有停止信号被误当作 drain | 先交付兼容的受控排空入口；排空确认前不停止 Worker、不切换协议 |
| Runtime 能力被错误等同 | 显式能力声明，缺失能力关闭对应入口 |
| 原生 Session 映射错误 | Chub ID 稳定、原生 ID 不透明、归属校验和失败关闭 |
| 权限映射造成提权 | Adapter 必须无损映射，否则拒绝执行 |
| 实时终端与后台任务并发写入 | 逻辑租约与 Runtime writer 探测共同仲裁 |
| 双轨兼容长期残留 | 每阶段定义退出条件，禁止无限期双写和重复实现 |

## 12. 文档维护边界

- [Chub 总体架构](CHUB_ARCHITECTURE_DESIGN.md)：项目级进程、领域、状态所有权、依赖方向和演进原则。
- 本文：长期架构、组件所有权、Runtime 契约和迁移阶段。
- [AI Session 状态模型](AI_SESSION_STATE_DESIGN.md)：现行 Session/Activity、入口、槽位和单 writer 产品语义。
- [快速交互独立 Worker 设计](QUICK_INTERACTION_WORKER_DESIGN.md)：现行任务状态、租约、恢复、通知和 Web 重启语义。
- [Chub–OpenClaw 接入设计](CHUB_OPENCLAW_INTEGRATION_DESIGN.md)：微信身份、路由、权限和结果回送。
- [Chub 集成能力清单](CHUB_INTEGRATION_CAPABILITIES.md)：当前已经可用的命令、插件和 API，不登记尚未实现的多 Runtime 能力。

实施阶段开始后，应只在实际职责或行为发生变化时更新对应现行文档；不能提前把目标架构写成当前状态。
