# Chub AI Runtime 架构设计

> 状态：已验收
> 主要读者：需要评估、生成或维护 Runtime Adapter/Runner 的 AI Agent；维护人员用于确认当前能力、接入边界和验收结果。
> 本文负责：说明 Chub 当前已经落地的 AI Runtime 架构，并定义实现任一 AI Runtime 所需的通用契约、边界和验收规则。
> 本文不负责：Session/Activity 的完整产品枚举（见 [AI Session 状态模型](AI_SESSION_STATE_DESIGN.md)）、Quick Worker 的任务恢复和通知细节（见 [Chub Quick Worker 独立服务设计](CHUB_QUICK_WORKER_DESIGN.md)）、微信路由和用户可见指令（见 [OpenClaw 定制集成设计](OPENCLAW_CUSTOMIZATION_DESIGN.md)）。
> 维护说明：此前的分阶段实施记录已经收敛为当前契约；本文不记录项目排期或过程性阶段日志，只有行为、状态所有权或 Runtime 实现边界变化时才需要更新。

## AI 可执行契约

本文只维护两部分设计：第一部分说明 AI Runtime 架构和当前 Codex 落地，第二部分说明如何按统一契约实现一个 AI Runtime。第二部分是规范性实现要求，不是项目计划；历史阶段的实施过程不覆盖当前规则，其他专项文档的领域契约也不在本文复制。

按[Chub 总体架构](CHUB_ARCHITECTURE_DESIGN.md)，本文覆盖 AI Runtime 层。该层依赖 Chub 核心层的公开配置、安全、日志、通知和维护能力；第三方服务只有需要 AI 时才调用本文定义的公开任务用例。核心层、OpenClaw 和其他第三方服务不得直接读写 Runtime、Session Manager 或 Worker 的私有状态。

AI Agent 处理 Runtime、Session 或 Quick Worker 需求时，先执行以下判断：

1. **Chub 是控制面，不是模型或通用 Agent。** Chub 拥有入口认证、权限、逻辑 AI Session、任务状态、通知和用户可见最终结果；Runtime 负责实际分析、编码和工具执行。
2. **当前生产只有 `codex`。** `runtime_id` 由后端固定注册和写入，客户端、微信正文、任务正文和页面不能选择 Runtime、Runner、命令、工作目录或环境变量。
3. **状态必须有唯一权威来源。** AI Session 的逻辑状态归 AI Session Manager；后台任务、租约、恢复和终态归 Quick Worker；Runtime 原生状态只由对应 Adapter 解释；实时连接由 Interactive Supervisor 管理。
4. **同一逻辑 Session 同时只能有一个 writer。** 门禁只阻止当前直接冲突或破坏风险，不因历史 writer、旧 PID、页面状态或未知标记把整个系统锁死。`terminal` 与 `quick` 是创建后不可切换的入口类型，不互相接管 writer；当前 Quick Worker 只能在自己的 Session 租约内完成自己的原生 ID 绑定，其他 writer 必须失败关闭。
5. **可靠终态优先。** 进程创建、HTTP 200、任务已受理、Tool Call 已创建或模型已返回都不等于成功；必须确认任务、Session、通知或维护操作的最终状态。无法确认时失败关闭并提供恢复路径。
6. **Session 配置分层。** Chub 逻辑 Session 保存后续任务的期望权限、模型和推理等级；Runtime 原生 Session 的 `active_*` 字段只表示实际观察到的生效状态。新建 Session 的模型和推理等级为空时跟随 Runtime 默认，页面修改只更新当前 Chub Session，不修改 Runtime 全局默认；任务受理时必须保存配置快照。
7. **Runtime 错误保留上游原文。** Chub 不维护一套覆盖所有 Runtime 的错误翻译表；只做有界读取、纯文本展示和敏感信息脱敏。没有可用原文时才使用通用兜底错误。
8. **旧运行态默认不兼容。** Chub 自有且不再适用的旧 Session、任务、租约和协议数据在受控升级边界内直接清理并初始化新格式；不增加双读、双写或长期迁移分支。用户配置、第三方原生数据和明确要求保留的数据不适用此规则。
9. **服务操作局部生效。** Web、Quick Worker、ClawBot 的普通重启彼此独立；升级与恢复只重建 Chub 自有 AI 运行态、Chub Web 与 Quick Worker，只锁定直接受影响的 AI Runtime 写入和相关服务，不阻断无关只读能力或独立入口。
10. **能力不足不能静默模拟。** Runtime 不支持的能力必须返回不支持或不可用；不得通过猜测、自动切换、放宽权限或重复提交制造表面兼容。

## 第一部分：AI Runtime 架构

### 1. 当前结论与范围

此前的 Runtime 架构收敛工作已经完成并通过维护者验收，结果已落地到当前代码和服务。本部分说明架构、职责、状态所有权和当前 Codex 实现，不记录项目排期或分阶段实施日志。

当前结论：

- Chub 已有运行时无关的 AI Session Manager、Interactive Supervisor、Quick Worker、Runtime Adapter 和 Runtime Runner 边界。
- Codex 是当前唯一完整接入且唯一注册到生产注册表的 Runtime。
- Runtime ID、能力矩阵、原生 Session 映射、活动事件、实时终端边界和上游错误传播已有统一契约。
- `/api/codex/*`、现有页面、微信固定指令、通知格式和额度展示仍是当前正式产品入口；内部架构收敛没有新增 Runtime 选择器。
- 当前架构已经具备实现其他 Runtime 所需的边界，但没有接入第二个真实 Runtime。需要新增 Runtime 时，按本文第二部分的实现规范执行。

### 2. 当前架构与状态所有权

```text
Chub 核心入口 / 第三方服务已完成认证与固定路由
             |
             v
       AI Runtime 公开用例
       - 接收已校验请求和固定产品规则
       - 逻辑 Session、任务与用户可见终态
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
| 实时终端票据、连接、ttyd Web 桥和固定 tmux carrier 生命周期 | Interactive Supervisor + Adapter | Quick Worker 或页面 |
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
| `interactive_terminal` | 启动、恢复和探测受管实时终端；ttyd 桥可重建，固定 tmux carrier 按逻辑 Session 复用 |
| `background_turn` | 执行受监督的后台 Turn 并提供规范化结果 |
| `task_cancel` | 让后台执行纳入 Worker 的取消契约；任务终态仍由 Worker 决定 |
| `structured_events` / `activity_events` | 提供有界、可校验的执行和 Activity 事件 |
| `writer_probe` | 确认原生 Session 是否有其他 writer |
| `model_catalog` | 提供模型和推理等级目录 |
| `permission_profiles` | 无损映射 Chub 产品级权限配置 |

后台 Runtime 的最低接入能力是 `runtime_status`、`background_turn`、`task_cancel`、`native_session_mapping`、`structured_events` 和 `permission_profiles`。如果要复用已有原生 Session，需要 `session_resume`；如果还要和实时终端共享 Session，需要可靠的 `writer_probe` 和 `activity_events`；实时终端自身需要 `interactive_terminal`。能力缺失只影响直接依赖它的入口，不扩散为全局不可用。

#### 3.3 Runtime 启用状态

- 已注册 Runtime 的健康状态、部署可用性与任务接入策略分离：健康状态由 Adapter 报告；`settings.local.yaml` 的 `ai_runtime.<runtime_id>.enabled` 决定 Runtime 是否可作为部署实例启动；设置页的“接收新任务”由 Chub 在本机受限状态文件中保存，只控制后续新 AI 任务，不中断已受理任务。设置页的“AI Runtime”分组包含“通用配置”和每个已接入 Runtime 的独立入口；Runtime 页面展示标识、健康状态、任务接入策略和可日常维护的专属配置，不展示工作目录、运行目录等部署字段。
- 启用的 Runtime 可以接受新的 Session、快速交互、实时终端和微信文本优化任务；停用只拒绝新的任务受理，不取消、阻塞或改写已受理任务，也不影响读取、停止、归档和删除已有 Session。
- 所有 Runtime 都停用时，Chub 保持基础功能模式。核心设备管理、第三方服务和已有任务的状态查看仍可用；AI 提交入口明确显示不可用原因。
- 当前生产实例只注册 `codex`。设置页的列表和启用状态为后续固定注册的 Runtime 预留管理入口，但不提供 Runtime 选择器，也不允许既有 Session 跨 Runtime 迁移。

#### 3.4 权限、事件和错误

- Adapter 必须报告权限配置能否无损映射；无法映射时拒绝创建或执行，不自动提升权限。
- 原生事件、Hook 文件、升级期间的 Session 别名、路径、权限、格式、限长读取和清理由
  Adapter 负责；Session Manager 只消费规范化 Activity。升级改绑不重启原生进程时，Adapter
  必须让旧进程继续产生的 Hook 事件归属到新的 Chub Session；别名损坏或无法确认时只能
  忽略事件并保持未知状态，不得放宽 writer 仲裁。
- Runner 失败时先读取 Runtime 错误原文；读取成功但没有文本时再回退到 `stderr`，两者为空才使用 `Task runner exited unsuccessfully.` 等通用兜底。读取或解析过程本身失败时必须保留 Chub Runtime 错误码和诊断，并进入明确失败终态。
- 错误原文只作为有界纯文本保存、查询和通知，不作为命令、HTML 或新任务输入执行。`Authorization`、`Bearer`、配置 Token、终端票据等敏感值必须在 Runner/Worker 边界脱敏。
- `error_code` 只表示 Chub/Worker 终态分类，不要求与上游错误一一对应；`error_source=runtime` 时保留上游 AI Runtime 子进程原文，`error_source=chub` 时明确标记 Chub/Worker/解析边界错误。任务被取消或超时不是错误来源，`error_source` 必须为空；失败但来源元数据缺失时只能显示“来源未确认”，不得猜测为 Runtime 或 Chub。
- API 错误响应必须带 `error.source`：`runtime` 仅用于 Chub 已确认该错误来自 Runtime API/CLI 的转换，`chub` 用于认证、参数、Worker、Session、页面通道和其他 Chub 控制面错误。API 层不改写 Runtime 原文；页面和通知将 `runtime` 标为“Codex CLI（上游 Runtime）”，将 `chub` 标为“Chub”。
- 未知错误不得触发自动重试、自动切换 Runtime 或重复提交。

### 4. Runtime 与 AI Session 的边界

AI Session 的 Session/Activity 枚举、交互入口、类型、槽位、页面语义和单 writer 产品规则以 [AI Session 状态模型](AI_SESSION_STATE_DESIGN.md) 为唯一依据；本节只定义 Runtime 必须提供的支撑能力：

- AI Session Manager 拥有 Chub 逻辑 Session 和公开元数据；Runtime Adapter 只负责原生 Session 映射、规范化事件、writer 探测和终端/后台执行能力。
- Runtime 私有进程、Hook、事件格式、原生路径和错误读取只能由 Adapter/Runner 解释，不能进入 Session 公共模型或页面契约。
- Runtime 接入不得绕过 Session Manager、Interactive Supervisor 或 Quick Worker，也不得向客户端暴露 Runtime、Runner、命令或工作目录选择器。
- 入口冲突、writer 所有权、Session 类型限制和最终状态确认由 Session/Worker 专项文档共同约束；Runtime 只提供后端完成这些判断所需的可信结果。Runtime 不把“tmux 存在”解释为原生 Codex 一定健康，也不把 Web 连接存在解释为 writer 仍由浏览器持有。

### 5. Runtime 与 Quick Worker 的边界

Quick Worker 是独立本机服务，当前生产使用固定 `codex` Runner。Worker 拥有任务幂等、租约、进程组、超时、取消、恢复、通知关联和任务终态；Runtime Runner 只负责受控启动、事件/结果/错误读取及资源能力，不拥有任务终态或通知路由。

- 核心层入口负责认证、业务校验和调用 AI Runtime 的提交用例；Worker 负责后台任务执行和 Session 租约，入口不回退到内置 Runner。
- Worker 健康、协议、任务/租约和恢复未完成时，快速交互 Session 写入失败关闭；实时终端使用独立的 Codex PTY/tmux 链路，不因 Quick Worker 恢复状态暂停。无关只读能力和独立入口按各自规则继续工作。
- 新建会话先要求可用 AI Runtime；Quick Worker 只额外约束 `quick` Session。首页默认创建 `quick`，但 Worker 不可用而 Runtime 可用时只允许创建 `terminal`；快速交互页和微信 `new` 不回退创建终端。没有可用 Runtime 时两种创建均不可用。
- Web、Worker、ClawBot 的普通重启彼此独立；升级与恢复只重建 Chub 自有 AI 运行态、Chub Web 与 Quick Worker，并以新实例健康、协议匹配、Session 映射和操作终态确认成功。AI Runtime 的启用与可用性在完成后独立检查；Runtime 被停用或不可用时关闭新 AI 任务，但不反向判定 Worker 或升级恢复失败。

任务状态、租约、恢复、通知、重启合并和维护命令的详细规则以 [Chub Quick Worker 独立服务设计](CHUB_QUICK_WORKER_DESIGN.md) 为准；本节不复制其任务状态机或维护步骤。

### 6. 当前数据、入口与安全边界

- 当前生产正式入口仍是 `/api/codex/*`、Codex 页面、微信固定路由和 Quick Worker。`/api/ai/usage` 读取默认 Runtime 的 `usage_snapshot`，响应以 `runtime_id` 标识归属；`/api/ai/runtimes/{runtime_id}/settings` 只面向设置页读取和保存该 Runtime 已声明的专属配置。它们不提供客户端 Runtime 选择器、跨 Runtime 聚合或通用 Session API。
- Runtime 可独立声明 `usage_snapshot` 和 `runtime_settings` 能力。前者由 Runtime 自行决定认证、缓存和快照口径，后者由 Runtime 自行校验字段并保存本机专属配置；核心层只做固定 Runtime ID 路由、认证、统一响应和操作日志。未声明能力的 Runtime 不展示对应设置，也不得由核心层猜测或代管其供应商配置。
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

## 第二部分：AI Runtime 实现规范

### 8. 适用范围与实现目标

本部分定义实现一个 AI Runtime 的必需边界，适用于当前 Codex 的维护和新增 Runtime 的实现。实现者必须先确定目标 Runtime、版本、运行平台和需要开放的产品场景，再按本部分完成能力确认、Adapter/Runner、固定注册和验收；这是一套实现规范，不是项目排期。

实现目标是：将 Runtime 的私有实现集中在 Adapter/Runner 和固定注册 wiring 中，不改变 Chub 逻辑 Session、Quick Worker、通知、权限、单 writer、升级恢复或既有 Codex 入口的基础语义。

除非需求明确要求，否则不做以下内容：

- 不增加通用插件市场、远程 Worker、数据库、分布式队列或新的常驻服务。
- 不增加跨 Runtime 历史上下文迁移、Session 合并或同屏聚合。
- 不为了新 Runtime 恢复旧 Chub 运行态兼容、双读、双写或启动迁移。
- 不把模型供应商 API 兼容直接当作 Agent Runtime 兼容。

### 8.1 实现前能力评估

实现者或评估 Agent 必须先按本节确认目标 Runtime 的能力，再进入 Adapter/Runner 实现。评估对象必须明确 Runtime 名称、版本、运行平台和调用方式；可以使用 CLI、HTTP 或其他受控接口，但按能力语义评估，不按命令名称判断兼容。

评估结果只能使用以下值：

- `Supported`：当前版本已实现，并有可复现的调用或测试证据。
- `Partial`：只能覆盖部分行为，必须列出缺失边界。
- `Unsupported`：当前版本不支持。
- `Unknown`：没有可靠证据，不得按支持处理。

#### 能力分级

| 兼容级别 | 必须全部满足的能力 |
| --- | --- |
| 后台兼容 | `runtime_status`、`background_turn`、`task_cancel`、`native_session_mapping`、`structured_events`、`permission_profiles` |
| Session 兼容 | 后台兼容 + `session_resume`、`session_archive` |
| 完整兼容 | Session 兼容 + `interactive_terminal`、`writer_probe`、`activity_events`、`model_catalog` |

#### 具体评估项

| 能力 | 最低评估要求 |
| --- | --- |
| `runtime_status` | 检查 Runtime/CLI、版本、依赖和运行状态，并返回明确结果。 |
| `background_turn` | 接受受控输入，提供结构化输出和可读取的最终结果。 |
| `task_cancel` | 支持取消、超时和进程/资源清理，并能确认最终状态。 |
| `native_session_mapping` | 发现、校验和创建原生 Session，至少提供原生 ID、工作目录和必要元数据。 |
| `structured_events` | 提供 Session/Turn 开始、结束、失败和错误事件，并能关联原生 Session。 |
| `permission_profiles` | 能无损表达既定权限和审批模式；无法映射时必须拒绝。 |
| `session_resume` | 能使用原生 Session ID 恢复执行，并校验 ID 归属。 |
| `session_archive` | 支持原生 Session 的归档和受控删除。 |
| `interactive_terminal` | 能启动或恢复交互终端，并绑定指定原生 Session。 |
| `writer_probe` | 能判断原生 Session 是否被其他 writer 占用，并等待释放。 |
| `activity_events` | 能提供可校验的 working/idle 等生命周期信号。 |
| `model_catalog` | 能读取和校验模型、默认值和推理等级。 |

#### 通用判断规则

- Runtime ID、命令、路径、权限和环境由后端固定，候选 Runtime 不能要求客户端传入实现选择。
- Runtime 私有文件、Hook、锁、事件格式和错误格式必须由 Adapter/Runner 隔离处理。
- 进程创建、HTTP 200、Tool Call 创建或模型回复不能单独作为成功依据。
- 错误和结果必须有界、可脱敏；未知状态不得自动重试或切换 Runtime。
- 不支持的能力只能关闭直接依赖它的入口，不能静默模拟或扩大权限。
- 评估必须确认不会破坏现有 Codex、Session、Worker、通知和升级恢复语义。

#### 评估报告与准入

评估报告至少包含 Runtime 名称、版本、平台、调用方式、每项能力的结果、证据、缺口、已知限制和建议兼容级别。任何必需能力为 `Partial`、`Unsupported` 或 `Unknown` 时，不得进入对应兼容级别或生产注册表；自评报告不能替代维护者对实际服务、外部依赖和最终状态的验收。

### 8.2 共享代码契约（实现者必读）

当前仓库的共享契约位于 `app/ai_runtime/contracts.py`、`app/ai_runtime/registry.py` 和 `app/ai_runtime/worker.py`。Runtime 实现必须直接复用 `app.ai_runtime` 导出的模型和 Protocol，不复制一套同名模型，不把 Codex 私有模型当作共享接口。下表是生成 Adapter/Runner 时必须遵守的字段和约束；所有共享 Pydantic 模型均为严格、不可变且禁止额外字段。

部署级 Runtime 配置使用 `settings.local.yaml` 的 `ai_runtime.<runtime_id>` 层级。当前 Codex 固定为 `ai_runtime.codex`，包含部署可用性、工作目录、运行目录、终端票据、并发和快速交互超时；旧顶层 `codex_pty` 不再读取。设置页不展示这类部署字段：其“接收新任务”开关是独立运行策略。设置页的 AI Runtime 通用配置保存跨 Runtime 共享的用量时区；各 Runtime 页面只保存其专属业务配置，例如 Codex 的 Sub2API 地址和订阅 ID。它们均保存于 `config/ai-runtimes.local.yaml`，但按 `general` 与 Runtime ID 隔离。

| 模型 | 必须提供的字段与边界 |
| --- | --- |
| `RuntimeDescriptor` | `runtime_id` 匹配 `^[a-z][a-z0-9-]{0,31}$`；`capabilities` 是已登记能力名的 `frozenset`。 |
| `RuntimeStatus` | `runtime_id`、`available: bool`、可选 `reason`（最多 300 字符）、`dependencies: dict[str, bool]`。 |
| `RuntimeTurnRequest` | `permission_profile` 只能是 `auto-review`、`read-only`、`full-access`；可选 `native_session_id` 最多 128、`model` 最多 128、`reasoning_effort` 最多 32。 |
| `RuntimeNativeSession` | `runtime_id`、原生 ID（1–128）、可信 `cwd`、可选标题（最多 500）、当前权限/模型/推理等级和 `created_at`/`updated_at`。 |
| `RuntimeSessionDiscoveryResult` | `sessions: tuple[RuntimeNativeSession, ...]`；可选 `archive_states: dict[str, bool]` 用于确认归档状态。 |
| `RuntimeModelCatalog` | 模型列表、可选默认模型和默认推理等级；每个模型包含 ID、名称、说明、可选默认等级和等级列表。 |
| `RuntimeTerminalRequest` | Chub `session_id`、可信 `cwd`、`permission_mode`（可额外为 `ask`）、可选原生 ID/模型/推理等级。 |
| `RuntimeProcessSpec` | `argv: tuple[str, ...]`，至少 1 项、最多 64 项；只能是后端固定可执行文件和参数，不接受 shell 字符串。 |
| `RuntimeEventSummary` | 可选且经过校验的单个 `native_session_id`；发现多个互相冲突的 ID 必须抛出冲突错误。 |
| `RuntimeActivityEvent` | 可选原生 ID、`activity`（`working`/`idle`/空）和 `activity_source`（`none`/`terminal`/`quick`）。 |
| `RuntimeTurnResult` | `text` 最多 1,000,000 字符，`truncated` 表示是否达到读取上限。 |
| `RuntimeWorkerLaunchRequest` | Worker 传入的任务 ID、受信任任务目录、释放 FD、Session/任务类型/工作区、`RuntimeTurnRequest`、Hook/重启目录和受控启动标志；测试字段只供 `fixed-test` 使用。 |
| `RuntimeWorkerLaunchSpec` | `argv`、`stdin_prompt: bool` 和受控环境字典；Prompt 不得拼入命令行，`stdin_prompt=True` 时由 Worker 写入标准输入。 |

Runtime 错误统一使用 `RuntimeOperationError(code, message, kind)`，其中 `kind` 只能是 `invalid_request`、`conflict` 或 `unavailable`。错误消息必须有界、可脱敏，不得把异常堆栈、任意路径、Token 或终端票据交给用户或通知。

Adapter 的基础 Protocol 只有以下两个成员：

```text
descriptor: RuntimeDescriptor  # property，不是方法
status() -> RuntimeStatus
```

其余能力通过可组合的结构化 Protocol 声明。实现类不需要继承基类，但必须具备下表的精确方法名、参数和返回类型；注册表会进行运行时结构检查。

### 8.3 能力与接口映射

| 能力 | 必须实现的位置 | 精确接口或行为 |
| --- | --- | --- |
| `runtime_status` | Adapter | `descriptor` property、`status()`；状态的 `runtime_id` 必须与 descriptor 一致。 |
| `native_session_mapping` | Adapter `RuntimeNativeSessionAdapter` | `validate_native_session_id(id) -> None`、`discover_sessions() -> RuntimeSessionDiscoveryResult`、`native_session_available(id) -> bool`。原生 Session 的创建由终端或后台 Runner 执行并从规范化事件确认，没有额外的通用 `create_native_session()` 方法。 |
| `session_resume` | Adapter + Runner | 使用已校验的原生 ID 填入 `RuntimeTurnRequest`；必须依赖 `native_session_mapping`，不能只在字符串层面模拟恢复。 |
| `session_archive` | Adapter `RuntimeSessionArchiveAdapter` | `run_native_action(action: "archive" 或 "delete", native_session_id: str) -> None` 执行原生动作；`native_session_archive_state(native_session_id: str)` 和 `native_session_deleted_state(native_session_id: str)` 返回 `bool` 或 `None`，分别用于在动作结果中断后确认原生是否已归档或删除。原生结果未确认成功前不得删除 Chub 映射。 |
| `interactive_terminal` | Adapter `RuntimeInteractiveTerminalAdapter` | `terminal_command(request: RuntimeTerminalRequest, port: int) -> RuntimeProcessSpec`、`terminal_backend_matches(command: tuple[str, ...], session_id: str) -> bool`。 |
| `writer_probe` | Adapter `RuntimeWriterProbeAdapter`，并由 Runner 转发 | `has_active_writer(native_session_id: str | None) -> bool`、`wait_for_writer_release(native_session_id: str | None, *, timeout: float = 3.0) -> bool`、`runtime_process_matches(command: tuple[str, ...]) -> bool`。无法确认时抛出错误，不得返回“空闲”。 |
| `activity_events` | Adapter `RuntimeActivityEventAdapter` | `read_activity_event(session_id: str) -> RuntimeActivityEvent | None`、`clear_activity_event(session_id: str) -> None`、`rebind_activity_session(old_session_id: str, new_session_id: str) -> None`。升级别名必须固定在 Runtime 私有目录、使用当前用户权限并进行有界校验。 |
| `model_catalog` | Adapter `RuntimeModelCatalogAdapter` | `validate_model(model: str | None, reasoning_effort: str | None) -> None`、`read_model_catalog() -> RuntimeModelCatalog`。 |
| `background_turn` | Worker Runner | 由 `RuntimeWorkerRunner.validate_turn()` 和 `build_launch()` 表达；Runner 必须提供固定进程规格、受控输入和最终结果读取。 |
| `structured_events` | Worker Runner | `parse_event_stream(path, *, max_event_bytes, missing_ok=False) -> RuntimeEventSummary`；只输出规范化事件摘要。 |
| `task_cancel` | Quick Worker | 没有单独的 Adapter 方法；Runner 进程必须可由 Worker 以进程组方式终止，Worker 决定取消、超时和最终状态。 |
| `permission_profiles` | Turn/Runner | `RuntimeTurnRequest` 只接受三种可映射权限；`validate_turn()` 和 `build_launch()` 必须无损映射，不能把 `ask` 或未知权限静默提升为 `full-access`。 |

`RuntimeCapabilityMatrix` 的机器状态值是小写的 `supported`、`unsupported`、`unavailable`，与评估报告使用的 `Supported`、`Partial`、`Unsupported`、`Unknown` 不同。声明能力但未实现对应 Protocol 时，`RuntimeRegistry` 必须在注册阶段失败；声明 `session_resume` 却没有原生 Session 映射、或声明完整终端却无法保证 writer 仲裁时，必须在实现自检中失败关闭。生产 Runtime 的 Adapter 与 Runner 应声明完全一致的 `runtime_id` 和能力集合；当前 `validate_runtime_wiring()` 只自动检查 `runtime_id`，能力集合一致性必须由装配测试补齐。

后台 Runner 的完整结构如下，不能只实现其中的启动方法：

```text
descriptor: RuntimeDescriptor  # property
available: bool                # property
workspace_ids: tuple[str, ...] # property
validate_turn(workspace_id: str, request: RuntimeTurnRequest) -> None
build_launch(request: RuntimeWorkerLaunchRequest) -> RuntimeWorkerLaunchSpec
has_active_writer(native_session_id: str) -> bool
native_session_available(native_session_id: str) -> bool
parse_event_stream(
    path: Path,
    *,
    max_event_bytes: int,
    missing_ok: bool = False,
) -> RuntimeEventSummary
read_error(task_dir: Path, *, max_bytes: int) -> str | None
read_result(task_dir: Path, *, max_bytes: int) -> RuntimeTurnResult
```

`RuntimeRegistry.require(runtime_id, capabilities=...)` 只返回已注册且能力满足的 Adapter；`WorkerRuntimeRegistry.require(runtime_id)` 还会拒绝 `available=False` 的 Runner。两个 registry 都会检查 descriptor 是否发生漂移，`capability_matrix()` 只用于后端状态投影，不接受客户端选择。

### 8.4 AI 生成的最小交付物

AI 根据本文生成 Runtime 时，交付物必须同时包含以下内容；只生成一个能返回 `status()` 的类不算可用 Runtime：

1. **能力报告。** 按 8.1 的固定结果和证据格式列出全部 12 项能力，给出建议兼容级别和明确不支持范围。
2. **Runtime Adapter。** 提供 `RuntimeDescriptor`、`status()`，以及能力报告中标记为 `Supported` 的全部 Adapter Protocol；所有原生 ID、路径、命令、文件、Hook、锁和错误解析只存在于该 Runtime 私有包。
3. **Worker Runner。** 只要声明后台兼容，就必须实现 `RuntimeWorkerRunner` 的完整方法集，返回 `RuntimeWorkerLaunchSpec`，并通过事件、结果、错误和资源清理测试。Runner 与 Adapter 的 descriptor 必须使用同一个 `runtime_id`。
4. **固定装配。** 在后端 factory/registry 中构造 Adapter 和 Runner，调用 `validate_runtime_wiring(adapter, runner)`，再分别注册到 `RuntimeRegistry` 与 `WorkerRuntimeRegistry`；不得把 Runtime、命令、路径或环境选择交给客户端。
5. **契约测试。** 至少覆盖重复注册、descriptor 漂移、状态 owner 不一致、能力虚假声明、非法原生 ID、权限映射、事件 ID 冲突、结果/错误上限、writer 无法确认、取消/超时/崩溃和租约释放。
6. **接入变更说明。** 列出需要新增或修改的固定入口、当前不支持的入口、外部依赖、平台验证结果和恢复方式；未完成的能力不得写入生产注册表。

最小组合关系如下，实际类名可以按 Runtime 命名，但调用顺序和固定 owner 不变：

```text
adapter = <Runtime>Adapter(...)
runner = <Runtime>WorkerRuntime(adapter, ...)
validate_runtime_wiring(adapter, runner)
RuntimeRegistry([adapter])
WorkerRuntimeRegistry([runner])
```

### 8.5 当前代码的接入限制

共享契约已经运行时无关，但当前生产装配仍有 Codex 专用表面，生成 Adapter 时必须显式处理，不能伪造 Codex 属性来绕过：

- `app/ai_session/manager.py` 和 `app/codex/manager.py` 仍调用 `network_available`、`dependencies()`、`ensure_profile()` 等 Codex 兼容方法，并在部分路径直接读取 `discovery.session_archive_states()`、`hook_dir` 或 `codex_home`；这些不是共享 Protocol。新增 Runtime 要么提供等价的 Runtime 专用 facade 并修改固定装配，要么先把调用收敛到共享 Protocol，再注册新 Runtime。
- `app/ai_session/manager.py` 当前还把 Codex 模型 DTO、工作区和单一 `runtime_id` 写在装配层；它不能仅靠注册表自动变成多 Runtime Session 管理器。需要多 Runtime 同时进入同一 Web 实例时，必须单独完成 Session 选择/聚合 wiring。
- `app/quick_worker.py` 的默认 factory 当前只构造 Codex Runner；`app/quick_worker_runner.py` 的子进程入口也按固定 `runtime-id` 分支执行 `codex` 或 `fixed-test`。新增 Runtime 必须增加后端固定分支或等价的受控 dispatch，不得把可执行文件或任意 Runtime ID 作为客户端参数直接透传。

因此，“生成可用 Adapter”在当前代码中表示：Adapter、Runner、固定装配和必要的 facade/入口改动一起通过契约与业务验收；不能把 Adapter 文件单独通过类型检查视为完成。

### 9. 实现结构与职责边界

#### 9.1 Runtime Adapter

Runtime Adapter 必须独占该 Runtime 的私有知识：可用性和版本检查、原生 Session 创建/发现/恢复/停止/归档、模型目录、权限映射、实时终端命令、writer 探测、原生事件/Activity 转换、错误原文读取和原生 ID 归属校验。

Adapter 输出只能是共享层定义的规范化结果。每个原生 ID 在进入路径、命令或状态查询前都必须校验格式和归属；发现结果中的 `runtime_id` 必须与 Adapter descriptor 一致。上层不能因为 Runtime 的文件、命令、锁、事件字段或进程状态而增加分支。

#### 9.2 Runtime Runner

Runtime Runner 必须实现 `RuntimeWorkerRunner` 的完整方法集，只提供固定后台执行规格、受控输入、结构化事件、最终结果和有界错误读取。`build_launch()` 接收 Worker 提供的受信任目录和文件描述符，返回固定 `argv`、是否通过 stdin 传 Prompt 以及最小环境；不得把 Prompt、Token 或任意客户端路径写入命令行。`parse_event_stream()` 只能读取有界的私有事件文件并提取一个已校验的原生 Session ID；`read_error()` 是诊断读取，失败时 Worker 仍必须写入任务终态；`read_result()` 读取有界最终文本。Quick Worker 继续拥有：

当 Codex Runner 使用非交互式 `codex exec` 时，必须由后端固定加入 `--skip-git-repo-check`，使受信任的普通目录和 Git 仓库使用相同的快速交互路径。该参数只跳过 Codex 的 Git 仓库前置检查，不跳过 Chub 的可信工作目录发现、Session 归属校验、权限映射、审批或沙箱；客户端不能提交、关闭或覆盖该参数。非 Git 目录没有版本控制回滚保障，实际可写范围仍必须以 Session 的权限配置为准。

- 幂等任务 ID、Session 租约和单 writer 仲裁；
- 进程组、超时、取消、输出/结果上限和资源清理；
- 崩溃、断链、未知结果、恢复对账和唯一任务终态；
- 任务通知和重启协调。

Runner 启动或 Tool Call 创建不能宣告任务成功；未知错误不得自动重放或改投其他 Runtime。

#### 9.3 固定注册与组合 wiring

1. 后端 factory 登记 `runtime_id`、Adapter、Runner 和能力矩阵；客户端和外部任务协议不能携带实现选择。
2. 注册时调用 `validate_runtime_wiring()`，再分别注册 Adapter 和 Runner；校验 descriptor 一致、声明能力有真实实现，重复注册、能力虚假声明、协议不兼容或依赖缺失在任务受理前失败。
3. AI Session Manager、Interactive Supervisor 和 Quick Worker 通过固定后端组合选择 Runtime；不把 Runtime 选择器暴露给页面、微信或 OpenClaw。
4. 如果未来确实需要多个 Runtime 同时出现在同一实例，另行设计 Session 选择/聚合 wiring；这不是当前接入的默认内容。

### 10. 按场景开放能力

实现者先选择需要支持的产品场景，再只开放满足该场景最低能力的入口。能力不足时关闭直接依赖的入口，不模拟缺失能力，也不把较低兼容级别宣称为完整兼容。

| 实现/开放场景 | 必须通过的能力和保证 |
| --- | --- |
| 后台任务 | `runtime_status`、`background_turn`、`task_cancel`、`native_session_mapping`、`structured_events`、`permission_profiles` |
| 复用已有原生 Session | 后台任务门槛 + `session_resume` |
| 与实时终端共享 Session | 上述能力 + `writer_probe`、`activity_events`、可靠的单 writer 仲裁 |
| 开放实时终端 | 上述共享 Session 能力 + `interactive_terminal`、连接恢复和进程监督 |
| 模型/归档入口 | 额外具备 `model_catalog` / `session_archive`；缺失时只关闭对应入口 |

所有场景还必须满足 Worker 的固定保证：幂等、租约、取消、超时、恢复、结果限制、敏感信息保护和最终状态确认。Runtime 不能通过声明额外能力绕过这些保证。

### 11. 规范性实现流程

以下流程适用于每一个 Runtime 实现，用于保证实现结果可复核；它不是项目排期，也不要求为每个 Runtime 增加阶段编号。

1. **定义实现范围并完成自评。** 记录目标 Runtime、版本、运行平台、调用方式、需要开放的入口、权限模式、Session 复用方式、通知方式和不支持范围；没有证据的能力标记为 `Unknown`，不写虚构能力。
2. **实现共享模型组合。** 直接使用 `app.ai_runtime` 的模型和 Protocol，确定 Adapter 与 Runner 的能力集合及依赖关系；不得先复制 Codex 实现再改名。
3. **完成 Adapter。** 先通过 Runtime 状态、原生 ID、Session 映射、权限、writer、事件和错误原文契约测试；私有路径和格式不得进入共享层。
4. **完成 Runner。** 接入固定进程规格和受控输入，验证结构化事件、最终结果、退出、取消、超时、崩溃、结果超限和原文错误传播。
5. **完成固定装配。** 调用 `validate_runtime_wiring()`，注册 Adapter/Runner，补齐当前 Codex 专用 facade 或固定 dispatch 的等价实现；不得开放客户端 Runtime 选择。
6. **绑定业务边界。** 只为已确认场景补充 Session、终端、页面或微信 wiring；不修改没有直接依赖的 Codex 入口和其他业务。
7. **执行受控数据/协议切换。** 如数据结构变化，按固定维护窗口清理不再适用的 Chub 自有运行态并初始化新格式；不迁移旧运行态，不删除原生 Runtime 数据。
8. **完成回归和最终状态验收。** 通过契约、故障、权限、跨平台、页面/API/微信和服务恢复测试后，才把 Runtime 写入当前能力清单。

### 12. 实现完成判定

一个 Runtime 只有全部满足以下条件，才允许进入生产：

- Runtime ID、Adapter、Runner、原生 Session 映射和能力矩阵完整且固定；不存在客户端实现、命令、路径或环境选择。
- 能力报告、方法签名、能力依赖和固定装配位置足以让独立 AI 复现 Adapter/Runner；声明的每项能力都有可执行证据。
- 共享层不读取 Runtime 私有文件、锁、Hook、事件字段或命令；所有私有行为集中在 Adapter/Runner。
- 权限配置可无损映射；不支持的能力明确拒绝或隐藏，不静默降级。
- 同一逻辑 Session 的实时终端和后台任务严格单 writer；`quick` 绑定竞态只能由当前 Quick Worker 租约完成，未知或外部 writer 必须拒绝并给出恢复路径；不以历史锁文件、旧 PID 或页面状态长期阻塞。
- 任务、Session、通知、重启和升级都确认最终状态；Runner 成功创建、Tool Call 创建、HTTP 200 或模型回复不作为成功依据。
- 上游错误原文按有界纯文本透传并脱敏；没有原文时才使用通用兜底；错误解析失败仍释放租约并进入明确失败终态。页面时间线只在失败终态显示“错误来源”，取消和超时只显示自身状态。
- Quick Worker 的幂等、租约、进程组、超时、取消、恢复、结果限制和通知语义不退化；未知结果不得自动重放。
- 旧 Chub 运行态按批准边界清理，不新增长期兼容分支；用户配置、第三方原生数据和明确保留数据不被误删。
- Codex 既有页面、API、微信固定入口、额度、通知、普通重启和系统升级/恢复语义无回归；无关服务和只读能力不被新 Runtime 门禁阻断。
- Python 自动化测试、契约测试、真实业务回归、服务最终状态、受影响平台和必要的浏览器/微信验收均完成；未实际验证的平台和外部依赖必须明确列出。

### 13. 实现自检与复检

实现者提交验收时，维护者或独立 Agent 按以下顺序确认：

1. 读取能力报告，逐项对照共享模型、Protocol 方法签名和能力依赖；任何 `Partial`/`Unsupported`/`Unknown` 必须与注册表和入口状态一致。
2. 检查固定注册表、能力矩阵、Runtime ID、`validate_runtime_wiring()` 和 Adapter/Runner wiring。
3. 创建并恢复 Session，分别验证实时终端、快速交互、当前 Quick Worker 的重复发现绑定、外部 writer 冲突和第二页面接管；不再验收 terminal/quick 跨类型 writer 接管。
4. 验证正常完成、上游错误、空错误、敏感信息脱敏、取消、超时、崩溃、未知结果和租约释放。
5. 验证 Web、Worker、ClawBot 普通重启独立，以及系统升级与恢复只影响直接受影响的 Runtime 写入。
6. 验证页面、API、微信固定入口、通知和当前 Codex 回归；再确认未承诺能力确实保持不可用。

本专项文档需要重新检查的触发条件：新增 Runtime；改变 Runtime ID、能力矩阵、Adapter/Runner、Session/Worker 状态所有权、单 writer、错误传播、持久化/协议格式、服务关系、升级恢复或用户可见入口。仅代码重排、测试补充或不影响契约的内部实现不需要恢复历史阶段章节。

其他文档的权威边界：

- [Chub 总体架构设计](CHUB_ARCHITECTURE_DESIGN.md)：系统进程、领域分层和全局状态所有权。
- [AI Session 状态模型](AI_SESSION_STATE_DESIGN.md)：Session/Activity 枚举、页面状态和单 writer 产品细节。
- [Chub Quick Worker 独立服务设计](CHUB_QUICK_WORKER_DESIGN.md)：任务状态、租约、通知、恢复和重启细节。
- [Chub 集成能力清单](CHUB_INTEGRATION_CAPABILITIES.md)：当前可用 API、插件能力、微信指令和用户可见格式。

## 验收范围与复检

- 已验收：已落地的 Codex Runtime 边界、AI Session/Quick Worker 所有权、能力矩阵、Adapter/Runner wiring、实时终端与快速交互规则、错误透传、现有 Codex 业务回归，以及 Runtime 实现规范、共享代码契约、能力评估和 Adapter/Runner 生成清单；本次协议 `9` 的错误来源投影已通过自动化回归。
- 已验证：设置页已列出当前已注册 Runtime 并可启用或停用；停用 Runtime 会拒绝新的 AI 任务受理，同时保持已受理任务与已有 Session 的读取和维护入口。当前只在自动化测试环境验证，尚待维护者在实际 Web 页面确认。
- 当前不承诺：第二个真实 Runtime、多 Runtime Session 聚合、客户端 Runtime 选择器，以及未在当前维护机器实际验证的平台或外部 Runtime 特有行为；本次协议 `9`、ttyd/tmux 与旧进程 Hook 别名重连已完成 macOS/Ubuntu 服务重载后的真实最终状态复检并通过维护者验收。
