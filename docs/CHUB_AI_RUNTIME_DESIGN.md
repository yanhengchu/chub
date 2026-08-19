# Chub AI Runtime 架构设计

> 状态：已验收。
> 主要读者：AI Agent、实现和排障 Agent；维护人员用于确认当前能力、接入边界和验收结果。
> 本文负责：说明 Chub 当前已经落地的 AI Runtime 架构，以及未来接入另一个 Runtime 时必须遵守的契约和验收规则。
> 本文不负责：Session/Activity 的完整产品枚举（见 [AI Session 状态模型](AI_SESSION_STATE_DESIGN.md)）、Quick Worker 的任务恢复和通知细节（见 [Chub Quick Worker 独立服务设计](CHUB_QUICK_WORKER_DESIGN.md)）、微信路由和用户可见指令（见 [OpenClaw 定制集成设计](OPENCLAW_CUSTOMIZATION_DESIGN.md)）。
> 维护说明：此前的分阶段实施记录已经收敛为当前契约；除非行为、状态所有权或接入边界变化，不再在本文追加过程性阶段日志，也不再为后续工作增加阶段编号。

## AI 可执行契约

本文只维护两部分设计：第一部分是当前已经落地的架构，第二部分是未来接入另一个 Runtime 的设计。历史阶段的实施过程不覆盖当前规则，其他专项文档的领域契约也不在本文复制。

AI Agent 处理 Runtime、Session 或 Quick Worker 需求时，先执行以下判断：

1. **Chub 是控制面，不是模型或通用 Agent。** Chub 拥有入口认证、权限、逻辑 AI Session、任务状态、通知和用户可见最终结果；Runtime 负责实际分析、编码和工具执行。
2. **当前生产只有 `codex`。** `runtime_id` 由后端固定注册和写入，客户端、微信正文、任务正文和页面不能选择 Runtime、Runner、命令、工作目录或环境变量。
3. **状态必须有唯一权威来源。** AI Session 的逻辑状态归 AI Session Manager；后台任务、租约、恢复和终态归 Quick Worker；Runtime 原生状态只由对应 Adapter 解释；实时连接由 Interactive Supervisor 管理。
4. **同一逻辑 Session 同时只能有一个 writer。** 门禁只阻止当前直接冲突或破坏风险，不因历史 writer、旧 PID、页面状态或未知标记把整个系统锁死。已确认空闲且属于 Chub 的终端可以被快速交互接管。
5. **可靠终态优先。** 进程创建、HTTP 200、任务已受理、Tool Call 已创建或模型已返回都不等于成功；必须确认任务、Session、通知或维护操作的最终状态。无法确认时失败关闭并提供恢复路径。
6. **Runtime 错误保留上游原文。** Chub 不维护一套覆盖所有 Runtime 的错误翻译表；只做有界读取、纯文本展示和敏感信息脱敏。没有可用原文时才使用通用兜底错误。
7. **旧运行态默认不兼容。** Chub 自有且不再适用的旧 Session、任务、租约和协议数据在受控升级边界内直接清理并初始化新格式；不增加双读、双写或长期迁移分支。用户配置、第三方原生数据和明确要求保留的数据不适用此规则。
8. **服务操作局部生效。** Web、Quick Worker、ClawBot 的普通重启彼此独立；系统升级与恢复只锁定直接受影响的 AI Runtime 写入和相关服务，不阻断无关只读能力或独立入口。
9. **能力不足不能静默模拟。** Runtime 不支持的能力必须返回不支持或不可用；不得通过猜测、自动切换、放宽权限或重复提交制造表面兼容。

## 第一部分：当前已落地设计

### 1. 当前结论与范围

此前的 Runtime 架构收敛工作已经完成并通过维护者验收，结果已落地到当前代码和服务。本文现在只维护当前契约和后续接入规则，不再保留分阶段实施步骤或重复验收清单。

当前结论：

- Chub 已有运行时无关的 AI Session Manager、Interactive Supervisor、Quick Worker、Runtime Adapter 和 Runtime Runner 边界。
- Codex 是当前唯一完整接入且唯一注册到生产注册表的 Runtime。
- Runtime ID、能力矩阵、原生 Session 映射、活动事件、实时终端边界和上游错误传播已有统一契约。
- `/api/codex/*`、现有页面、微信固定指令、通知格式和额度展示仍是当前正式产品入口；内部架构收敛没有新增 Runtime 选择器。
- 当前架构已经具备第二个 Runtime 的接入准备，但没有接入第二个真实 Runtime。没有明确产品需求时不启动接入实现；出现需求后直接按本文第二部分实施，不使用新的阶段编号。

### 2. 当前架构与状态所有权

```text
Browser / CLI / OpenClaw / 微信
             |
             v
       Chub Web / API / 业务服务
       - 认证、权限和固定产品规则
       - 逻辑 Session、通知和用户可见终态
          /                         \
         v                           v
 AI Session Manager             Quick Worker
 - Session 生命周期             - 后台任务、租约和终态
 - Runtime 映射和能力校验        - 进程组、超时、取消和恢复
         |                           |
         v                           v
 Interactive Supervisor        Runtime Runner
 - 实时连接和终端生命周期        - 后台 Turn 固定执行规格
         \                           /
          v                         v
              Codex Runtime Adapter
       - Codex 原生 Session、命令、事件和错误
       - 当前唯一生产 Runtime 实现
```

| 对象 | 当前唯一所有者 | 不应由谁代替 |
| --- | --- | --- |
| Chub 逻辑 Session、标题、槽位、归档和用户可见状态 | AI Session Manager / AI Session Store | Runtime、页面或 Worker |
| Runtime 原生 Session、原生 ID、命令、Hook、锁和事件格式 | 对应 Runtime Adapter | Session Manager、页面或通用 Worker |
| 实时终端票据、连接和受管终端生命周期 | Interactive Supervisor + Adapter | Quick Worker 或页面 |
| 后台任务、Session 租约、进程组、超时、取消、恢复和任务终态 | Quick Worker | Runtime Runner、Web 或通知服务 |
| Runtime 私有后台命令、事件解释和有界结果读取 | Runtime Runner | Quick Worker 核心或业务入口 |
| 通知路由、投递和用户可见文案 | Chub 通知/业务入口 | Runtime |

共享层只能消费规范化模型，不能读取 Codex 私有路径、JSONL、Hook、锁文件、配置键或 CLI 参数。Runtime 适配边界不要求新增常驻服务；当前 Web 和 Worker 内的固定组合已经满足产品需求。

### 3. 当前 Runtime 契约

#### 3.1 固定标识

- `session_id`：Chub 生成的稳定逻辑 Session ID。
- `runtime_id`：后端固定注册的 Runtime 标识，当前只有 `codex`。
- `native_session_id`：Runtime 返回的不透明原生 ID，只做长度、格式和归属校验；映射唯一性由 `(runtime_id, native_session_id)` 共同确定。
- `task_id`：Quick Worker 的幂等任务 ID。
- `execution_id`：Worker 为一次 Runner 执行生成的关联 ID，不替代 `task_id`。

客户端不能填写或覆盖上述标识。现有 Codex 公共入口可继续使用 `codex_session_id` 等历史产品命名，但内部不得把它当作通用 Session ID，也不得为旧运行态保留兼容读取。

#### 3.2 能力矩阵

每个 Runtime 通过后端能力矩阵声明支持、拒绝或不可用能力。当前 Codex 生产组合必须保持现有业务能力；测试 Runtime 只用于契约测试，不进入生产入口。

| 能力 | 当前含义 |
| --- | --- |
| `runtime_status` | 确认 Runtime/CLI 可用性、版本和协议兼容性 |
| `native_session_mapping` | 创建、发现并校验 Runtime 原生 Session 映射 |
| `session_resume` / `session_archive` | 恢复或归档原生 Session；缺失时关闭对应入口 |
| `interactive_terminal` | 启动、恢复和探测受管实时终端 |
| `background_turn` | 执行受监督的后台 Turn 并提供规范化结果 |
| `task_cancel` | 让后台执行纳入 Worker 的取消契约；任务终态仍由 Worker 决定 |
| `structured_events` / `activity_events` | 提供有界、可校验的执行和 Activity 事件 |
| `writer_probe` | 确认原生 Session 是否有其他 writer |
| `model_catalog` | 提供模型和推理等级目录 |
| `permission_profiles` | 无损映射 Chub 产品级权限配置 |

后台 Runtime 的最低接入能力是 `runtime_status`、`background_turn`、`task_cancel`、`native_session_mapping`、`structured_events` 和 `permission_profiles`。如果要复用已有原生 Session，需要 `session_resume`；如果还要和实时终端共享 Session，需要可靠的 `writer_probe` 和 `activity_events`；实时终端自身需要 `interactive_terminal`。能力缺失只影响直接依赖它的入口，不扩散为全局不可用。

#### 3.3 权限、事件和错误

- Adapter 必须报告权限配置能否无损映射；无法映射时拒绝创建或执行，不自动提升权限。
- 原生事件、Hook 文件、路径、权限、格式、限长读取和清理由 Adapter 负责；Session Manager 只消费规范化 Activity。
- Runner 失败时先读取 Runtime 错误原文，再回退到 `stderr`；两者为空才使用 `Task runner exited unsuccessfully.` 等通用兜底。上游未知错误也必须进入明确失败终态。
- 错误原文只作为有界纯文本保存、查询和通知，不作为命令、HTML 或新任务输入执行。`Authorization`、`Bearer`、配置 Token、终端票据等敏感值必须在 Runner/Worker 边界脱敏。
- `error_code` 只表示 Chub/Worker 终态分类，不要求与上游错误一一对应；未知错误不得触发自动重试、自动切换 Runtime 或重复提交。

### 4. 当前 Session、实时终端与快速交互

完整的 Session/Activity 枚举以 [AI Session 状态模型](AI_SESSION_STATE_DESIGN.md) 为准。这里保留 AI Agent 必须执行的入口规则：

1. 同一逻辑 Session 可以从多个 Web 页面进入实时终端；后进入的页面接管唯一终端连接，旧页面退出到首页，但不能中断 tmux 中正在执行的 Codex Turn。
2. 实时终端正在执行 Turn 时，快速交互不得提交；不得抢占、并发写入或中断正在执行的 Turn。
3. 实时终端已确认等待输入且属于 Chub 时，快速交互可以接管：关闭终端连接、停止受管终端、确认原生 writer 已释放，再提交 Worker；旧终端页面返回首页。
4. 快速交互执行时，新的实时终端进入必须被拒绝或退出到首页；任务结束并确认租约释放后才恢复可进入。
5. 外部 writer、归属无法确认、状态未知且存在数据破坏风险时失败关闭；仅因为历史 writer、旧 PID、页面仍打开或受管终端进程存在，不得永久阻塞空闲 Session。
6. 进程创建、停止请求返回、Hook 到达、任务受理和 WebSocket 建立都不是最终状态；必须确认 Turn、租约或任务终态。

### 5. 当前 Quick Worker、重启和升级边界

Quick Worker 是独立本机服务，页面快速交互、微信 Chub 非实时任务和翻译任务都通过固定 `codex` Runner 执行。Worker 继续拥有任务幂等、Session 租约、翻译 FIFO、进程组、超时、取消、恢复、结果大小限制和任务终态；Web 不回退到内置 Runner。

- Web 重启不会停止 Worker、Runner、翻译队列或实时 tmux；Worker 重启不会重启 Web 或 ClawBot；ClawBot 维护不参与前两者的普通互斥。
- 新 Web 只能在 Worker 健康、协议版本、任务/租约、通知和重启状态完成有界恢复对账后开放 Session 写入。Worker 不可达或状态无法确认时，写入失败关闭，只读能力可以继续工作。
- 系统升级与恢复是维护例外，只锁定直接受影响的 AI Runtime 写入、Worker 和 Web 切换；它可以按固定白名单清理 Chub Session 关联、Hook 和 Worker 运行态，不删除 Codex 原生 Session、用户配置、第三方原生数据或业务资料。
- 升级成功必须由新实例健康、协议匹配、固定 Runner 可用、Session 映射可读和最终操作日志共同确认；不能只看服务管理命令或进程创建结果。

任务恢复、通知终态、重启合并和维护命令的详细规则以 [Chub Quick Worker 独立服务设计](CHUB_QUICK_WORKER_DESIGN.md) 为准；本节不复制其完整任务状态机。

### 6. 当前数据、入口与安全边界

- 当前生产正式入口仍是 `/api/codex/*`、Codex 页面、微信固定路由和 Quick Worker；已有的 `/api/ai/usage` 只是独立的用量只读接口，不表示支持多 Runtime。除用量接口外，不为了内部通用化新增通用 Runtime 选择或 Session API，也不提供客户端 Runtime 选择器。
- Chub 自有旧运行数据、旧协议状态和已经改变的功能默认不兼容；按固定升级边界清理后从新格式初始化。Codex 原生历史、用户配置、第三方数据和明确要求保留的数据必须按各自规则处理。
- Worker 协议、任务目录、事件、stdout、stderr 和错误原文受固定字节、行长、数量、权限和敏感信息限制；不得暴露 Token、终端票据、任意路径或命令。
- 认证、固定命令/路径白名单、权限映射、微信真实身份和 OpenClaw 固定路由失败时必须失败关闭；客户端不能扩大权限。
- 不因为 Runtime 状态未知、其他 Session 忙或普通服务重启而增加全局门禁。每个门禁都必须说明直接冲突、解除条件和恢复路径。

### 7. 当前验收结论

当前已验收范围：

- 已完成的 Runtime 边界、Runtime ID、能力矩阵、Adapter/Runner 固定 wiring 和测试替身契约。
- AI Session Manager、Session 原生映射、Activity 事件、实时终端与快速交互单 writer 规则。
- Quick Worker 任务、租约、恢复、上游错误透传、通知终态以及 Web/Worker/ClawBot 独立重启语义。
- 现有 Codex 页面、API、CLI、微信固定入口和服务状态没有因 Runtime 收敛改变。

当前不承诺：

- 第二个真实 Runtime 的实现或其特有能力。
- 多 Runtime 同时展示、跨 Runtime Session 聚合/迁移或客户端 Runtime 选择。
- 未在当前维护机器实际验证的平台行为或外部 Runtime 特有的权限、事件和终端语义。

## 第二部分：后续 Runtime 接入设计

### 8. 启动条件与目标

只有出现明确产品需求、目标 Runtime 和用户可见场景后，才直接启动第二 Runtime 接入。不预设候选产品、版本、能力或上线日期，也不把这项工作命名为新的阶段。

目标是：新增 Runtime 的私有实现集中在 Adapter/Runner 和固定注册 wiring 中，不改变 Chub 逻辑 Session、Quick Worker、通知、权限、单 writer、升级恢复或既有 Codex 入口的基础语义。

除非需求明确要求，否则不做以下内容：

- 不增加通用插件市场、远程 Worker、数据库、分布式队列或新的常驻服务。
- 不增加跨 Runtime 历史上下文迁移、Session 合并或同屏聚合。
- 不为了新 Runtime 恢复旧 Chub 运行态兼容、双读、双写或启动迁移。
- 不把模型供应商 API 兼容直接当作 Agent Runtime 兼容。

### 9. 新 Runtime 必须实现的边界

#### 9.1 Adapter

新 Runtime Adapter 必须独占该 Runtime 的私有知识：可用性和版本检查、原生 Session 创建/发现/恢复/停止/归档、模型目录、权限映射、实时终端命令、writer 探测、原生事件/Activity 转换、错误原文读取和原生 ID 归属校验。

Adapter 输出只能是共享层定义的规范化结果。上层不能因为新 Runtime 的文件、命令、锁、事件字段或进程状态而增加分支。

#### 9.2 Runner

新 Runtime Runner 只提供固定后台执行规格、受控输入、结构化事件、最终结果和有界错误读取。Quick Worker 继续拥有：

- 幂等任务 ID、Session 租约和单 writer 仲裁；
- 进程组、超时、取消、输出/结果上限和资源清理；
- 崩溃、断链、未知结果、恢复对账和唯一任务终态；
- 任务通知和重启协调。

Runner 启动或 Tool Call 创建不能宣告任务成功；未知错误不得自动重放或改投其他 Runtime。

#### 9.3 固定注册与组合 wiring

1. 后端注册表登记新 `runtime_id`、Adapter、Runner 和能力矩阵；客户端和外部任务协议不能携带实现选择。
2. 注册时校验 Adapter 与 Runner 使用同一个 `runtime_id`，声明能力必须有真实实现；重复注册、能力虚假声明、协议不兼容或依赖缺失在任务受理前失败。
3. AI Session Manager、Interactive Supervisor 和 Quick Worker 通过固定后端组合选择 Runtime；不把 Runtime 选择器暴露给页面、微信或 OpenClaw。
4. 如果未来确实需要多个 Runtime 同时出现在同一实例，另行设计 Session 选择/聚合 wiring；这不是当前接入的默认内容。

### 10. 新 Runtime 的能力门槛

| 接入场景 | 必须通过的能力和保证 |
| --- | --- |
| 后台任务 | `runtime_status`、`background_turn`、`task_cancel`、`native_session_mapping`、`structured_events`、`permission_profiles` |
| 复用已有原生 Session | 后台任务门槛 + `session_resume` |
| 与实时终端共享 Session | 上述能力 + `writer_probe`、`activity_events`、可靠的单 writer 仲裁 |
| 开放实时终端 | 上述共享 Session 能力 + `interactive_terminal`、连接恢复和进程监督 |
| 模型/归档入口 | 额外具备 `model_catalog` / `session_archive`；缺失时只关闭对应入口 |

所有场景还必须满足 Worker 的固定保证：幂等、租约、取消、超时、恢复、结果限制、敏感信息保护和最终状态确认。Runtime 不能通过声明额外能力绕过这些保证。

### 11. 接入实施顺序

1. **明确产品场景。** 记录目标 Runtime、版本、运行平台、需要开放的入口、权限模式、Session 复用方式、通知方式和不支持范围；没有明确场景不写虚构能力。
2. **完成 Adapter。** 先通过 Runtime 状态、原生 ID、Session 映射、权限、writer、事件和错误原文契约测试；私有路径和格式不得进入共享层。
3. **完成 Runner。** 接入固定进程规格和受控输入，验证结构化事件、最终结果、退出、取消、超时、崩溃、结果超限和原文错误传播。
4. **接入固定注册表。** 校验 Runtime ID 一致性和能力矩阵，默认只在后端固定组合中启用，不增加客户端任意选择。
5. **接入业务边界。** 只在确有需求的入口补充 Session、终端、页面或微信 wiring；不修改没有直接依赖的 Codex 入口和其他业务。
6. **执行受控数据/协议切换。** 如数据结构变化，按固定维护窗口清理不再适用的 Chub 自有运行态并初始化新格式；不迁移旧运行态，不删除原生 Runtime 数据。
7. **完成回归和最终状态验收。** 通过契约、故障、权限、跨平台、页面/API/微信和服务恢复测试后，才把新 Runtime 写入当前能力清单。

### 12. 新 Runtime 验收标准

新 Runtime 必须全部满足以下条件，才允许进入生产：

- Runtime ID、Adapter、Runner、原生 Session 映射和能力矩阵完整且固定；不存在客户端实现、命令、路径或环境选择。
- 共享层不读取 Runtime 私有文件、锁、Hook、事件字段或命令；所有私有行为集中在 Adapter/Runner。
- 权限配置可无损映射；不支持的能力明确拒绝或隐藏，不静默降级。
- 同一逻辑 Session 的实时终端和后台任务严格单 writer；已确认空闲的受管终端可接管；未知或外部 writer 只在直接风险存在时拒绝，并有清理/对账/恢复路径。
- 任务、Session、通知、重启和升级都确认最终状态；Runner 成功创建、Tool Call 创建、HTTP 200 或模型回复不作为成功依据。
- 上游错误原文按有界纯文本透传并脱敏；没有原文时才使用通用兜底；错误解析失败仍释放租约并进入明确失败终态。
- Quick Worker 的幂等、租约、进程组、超时、取消、恢复、结果限制和通知语义不退化；未知结果不得自动重放。
- 旧 Chub 运行态按批准边界清理，不新增长期兼容分支；用户配置、第三方原生数据和明确保留数据不被误删。
- Codex 既有页面、API、微信固定入口、额度、通知、普通重启和系统升级/恢复语义无回归；无关服务和只读能力不被新 Runtime 门禁阻断。
- Python 自动化测试、契约测试、真实业务回归、服务最终状态、受影响平台和必要的浏览器/微信验收均完成；未实际验证的平台和外部依赖必须明确列出。

### 13. 维护者操作与复检

维护者验收新 Runtime 时按以下顺序确认：

1. 检查固定注册表、能力矩阵、Runtime ID 和 Adapter/Runner wiring。
2. 创建并恢复 Session，分别验证实时终端、快速交互、等待输入接管、执行中互斥和第二页面接管。
3. 验证正常完成、上游错误、空错误、敏感信息脱敏、取消、超时、崩溃、未知结果和租约释放。
4. 验证 Web、Worker、ClawBot 普通重启独立，以及系统升级与恢复只影响直接受影响的 Runtime 写入。
5. 验证页面、API、微信固定入口、通知和当前 Codex 回归；再确认未承诺能力确实保持不可用。

本专项文档需要重新检查的触发条件：新增 Runtime；改变 Runtime ID、能力矩阵、Adapter/Runner、Session/Worker 状态所有权、单 writer、错误传播、持久化/协议格式、服务关系、升级恢复或用户可见入口。仅代码重排、测试补充或不影响契约的内部实现不需要恢复历史阶段章节。

其他文档的权威边界：

- [Chub 总体架构与演进设计](CHUB_ARCHITECTURE_DESIGN.md)：系统进程、领域分层和全局状态所有权。
- [AI Session 状态模型](AI_SESSION_STATE_DESIGN.md)：Session/Activity 枚举、页面状态和单 writer 产品细节。
- [Chub Quick Worker 独立服务设计](CHUB_QUICK_WORKER_DESIGN.md)：任务状态、租约、通知、恢复和重启细节。
- [Chub 集成能力清单](CHUB_INTEGRATION_CAPABILITIES.md)：当前可用 API、插件能力、微信指令和用户可见格式。

## 验收范围与复检

- 已验收：已落地的 Codex Runtime 边界、AI Session/Quick Worker 所有权、能力矩阵、Adapter/Runner wiring、实时终端与快速交互规则、错误透传及现有 Codex 业务回归。
- 当前不承诺：第二个真实 Runtime、多 Runtime Session 聚合、客户端 Runtime 选择器，以及未在当前维护机器实际验证的平台或外部 Runtime 特有行为。
- 本次文档收敛只整理已落地契约和后续接入规则，不改变代码、API、页面、服务、协议或数据。
