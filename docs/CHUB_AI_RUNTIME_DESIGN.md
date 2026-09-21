# Chub AI Runtime 架构设计

> 状态：已验收
> 主要读者：AI Agent；维护者通过与 AI Agent 协作，理解并确认本文规则。
> 本文负责：Runtime 共享契约、实现槽位、Adapter/Runner 边界、Native Session 与多 Runtime 接入判定。
> 本文不负责：Codex 等具体 Runtime 的私有行为、Chub Session 生命周期、Worker 任务恢复、Runtime 插件模块安装和微信路由。
> 维护说明：Runtime 插件模块已作为当前实现交付；本文定义所有 Runtime 共用的边界。本文“已验收”仅表示共享契约已验收，不替代任一具体 Runtime 的私有 Adapter/Runner、目标平台或用户可见行为验收。当前唯一接入的 Codex 私有行为以[Chub Codex Runtime 设计](CHUB_CODEX_RUNTIME_DESIGN.md)为准；ZIP 生命周期、开发源码发现和通用适配阶段以[Chub AI Runtime 插件模块设计](CHUB_RUNTIME_PLUGIN_DESIGN.md)为准。未来 Runtime 的私有实现、配置和验收只在其独立专项设计中定义。

## AI 可执行契约

Runtime 是 Chub 的受控本机执行实现。客户端、页面、微信和其他外部入口不能选择 Runtime 命令、路径、环境变量、Native ID 或实现槽位；它们只调用 Chub 已定义的 Session 和任务用例。

会话默认配置保存在本机 `config/ai-runtimes.local.yaml`，包括默认 Runtime、权限、模型和推理等级。它仅应用于创建时未明确指定对应参数的后续 Chub Session；已有 Session 与已受理任务继续使用创建时快照。今日关注等仍在使用的内部会话也使用同一默认 Runtime 选择，并由各自专项设计定义必要的固定权限或参数。旧搜索/今日关注运行状态及已退役微信专属权限、模型、推理等级字段不做兼容，升级时直接清理；按当前规则创建的 Session 不因此被追溯修改。当前只有 Codex 可选；新增 Runtime 后，只有已实现新建 Session 能力的 Runtime 才能进入默认 Runtime 选择。

新建 Session 固定提供 `chub`、`home` 与 `workspace` 三个内置工作目录。选择 `workspace` 后，任务可在该受信根目录及其全部子目录内工作，无需将每个项目子目录登记为独立工作区。只有需要把某个目录单独展示并作为 Session 的默认工作目录时，维护者才在本机 `settings.local.yaml` 的 `ai_runtime.shared.extra_workspaces` 显式登记；每项使用固定 ID、名称和路径，ID 不得覆盖内置目录。页面与 API 只接受后端已加载的目录 ID，不能传入任意路径；已创建 Session 继续保存其创建时的工作目录，移除已使用的额外目录前必须先处理关联 Session。Quick Worker 重载后才会使用新增或移除的额外目录映射执行新任务。

AI Agent 应先按问题范围选择文档，不把任一单篇当作完整的 Runtime 实现说明：

| 当前任务 | 必须先读 | 再读 |
| --- | --- | --- |
| 判断共享能力、Adapter/Runner 边界、实现兼容性或多 Runtime 路由 | 本文 | 涉及 Session、Worker 或微信时，进入对应专项设计。 |
| 新接入一个 Runtime | 本文的共享契约与验收边界 | [Runtime 插件模块设计](CHUB_RUNTIME_PLUGIN_DESIGN.md)的交付/生命周期/检查表，以及该 Runtime 的私有专项设计。 |
| 导入、启用、覆盖、刷新或移除 Runtime 制品 | [Runtime 插件模块设计](CHUB_RUNTIME_PLUGIN_DESIGN.md) | 本文仅用于核对 Descriptor、能力与兼容规则。 |
| 修改 Codex 的认证、Native 数据、用量、缓存或私有展示 | [Chub Codex Runtime 设计](CHUB_CODEX_RUNTIME_DESIGN.md) | 本文用于核对不变的共享边界。 |

Session 与 Worker 的领域状态分别以对应专项设计为准；Runtime 文档不替代其任务恢复或用户可见通知契约。

Runtime 的 `implementation_id` 是可维护的**实现槽位**：默认实现只影响新建 Session，Session 创建后固定该槽位。它不是任务编排插件模块 ZIP 的 `implementation_ref`；后者是包含内容摘要的**不可变产物引用**，只用于编排请求快照，并由[Chub 任务编排插件模块架构设计](CHUB_TASK_ORCHESTRATION_PLUGIN_DESIGN.md)定义。两者不得跨领域复用或互相替代。

每个 Runtime 实现以不可变 `RuntimeDescriptor` 注册：

| 字段 | 规则 |
| --- | --- |
| `runtime_id` | 稳定的逻辑 Runtime 标识；当前为 `codex`。 |
| `implementation_id` | 具体可信实现槽位；当前开发实现为 `codex-runtime-dev`。 |
| `native_session_compatibility_id` | 同一 Native 格式的兼容组；已绑定 Session 只能使用兼容实现。 |
| `capabilities` | 固定能力集合；Adapter 与 Runner 必须声明完全一致的 Runtime 身份和能力。 |

默认实现只影响之后新建的 Chub Session。Session 创建时固定实现槽位；已有 Session、Native 映射和已受理任务不因默认切换、模块覆盖或刷新而改投。

## 多 Runtime 通用契约

当前实现仍只有 Codex；本节定义 Chub 同时装载多个 Runtime 时必须保持的共享语义。自动化以 Codex 和最小测试 Runtime 验证这些规则，不将测试 Runtime 作为产品能力或正式交付。插件 ZIP、开发加载和生命周期调整以[Chub AI Runtime 插件模块设计](CHUB_RUNTIME_PLUGIN_DESIGN.md)为准；任何具体 Runtime 的私有实现计划不在本文定义。

1. **三个独立选择层级**：全局默认 `runtime_id` 决定新建 Session 的逻辑 Runtime；每个逻辑 Runtime 各自保存默认 `implementation_id`；Session 创建时固定保存二者。没有已保存默认时，后端只能从健康且具备新建 Session 所需能力的候选中稳定选取并立即持久化，不能由注册顺序决定。插件导入和启用状态在新任务提交时验证，避免生命周期切换期间丢失既有默认槽位。非 Codex Runtime 按 `runtime_id`、`implementation_id` 的固定升序选择；Codex 保留既有开发实现优先规则，避免升级时改变当前新建 Session 的默认槽位。
2. **调用方不选择 Runtime**：页面、微信、自动化和 OpenClaw 继续只提交受控 Session/任务用例。维护者只能在受保护设置中改变后续新 Session 的默认 Runtime 或默认实现；专属业务设置不得覆盖该默认 Runtime。切换默认 Runtime 时，通用默认模型和推理等级立即重置为“跟随目标 Runtime 默认”，再按目标 Runtime 的模型目录重绘；不得把前一 Runtime 的模型 ID 或推理等级带入新 Runtime。它们是当前默认 Runtime 的一组配置，不保留为各 Runtime 的历史偏好。请求正文、路由参数和外部指令不得携带任意 Runtime、实现、命令、路径、环境或 Native ID。
3. **Session 是唯一执行路由依据**：已有 Session 的 Runtime 归属不能由当前默认 Runtime、当前活动 Adapter、页面分组或历史任务推断。模型目录校验、Runner 提交、Native discovery、writer probe、resume、归档和删除必须按 Session 固定的 Runtime 与实现槽位路由；另一 Runtime 的不可用、停用或刷新不得改变其行为。
4. **能力决定可选性**：只有同时通过 Runtime 健康、插件生命周期、Runtime 启用状态和新建 Session 所需能力校验的候选才可进入默认 Runtime 选择。用量快照、登录页和 Runtime 专属设置由 capability 声明控制；缺少这些可选能力的 Runtime 不得伪造 Codex 行为。`GET /api/ai/usage` 只读取当前默认实现的实际快照；目标 Runtime 未声明 `usage_snapshot` 或读取失败时，如实返回不可用状态。Runtime 的账户切换同样是其私有能力：`codex auth` 和 Codex 账户卡片固定指向 Codex 的默认实现，不随全局默认 Runtime 切换；其他 Runtime 如需账户切换或额度，必须自行提供对应私有实现与专项验收。提供用量快照的 Runtime 必须显式写入自己的 `runtime_id`，共享模型不提供 Runtime 身份默认值。
5. **共享工作区不等于 Runtime 私有配置**：`chub`、`home`、`workspace` 与维护者登记的额外工作区是 Chub 对新 Session 的受控公共映射；Session、Quick Worker 的共享状态和通用超时同样不属于任一 Runtime 私有配置。具体 Runtime 的命令、认证、私有缓存、Native 数据源和专属运行目录继续由该 Runtime 自己定义。

共享配置固定为 `ai_runtime.shared`：其 `workspace`、`extra_workspaces`、`state_dir`、`runtime_dir`、并发上限和快速交互超时由 Chub 拥有。当前共享状态目录为 `data/local/state/ai-runtime`，当前 Worker 运行目录为 `data/local/runtime/ai-runtime`。旧 Codex Session、Quick Worker 和 `data/local/state/codex`、`data/local/runtime/codex` 均是升级恢复的固定删除输入；升级恢复先验证类型、所有者和权限，再直接删除，不迁移、不交接也不恢复。新 Web、Worker 和恢复后的实例都不得读取其中内容。`scripts/maintenance/chub-data-migrate` 不处理 AI Runtime 运行态。旧 `ai_runtime.codex` 共享字段和 `shared.legacy_*` 已从配置契约移除；本机配置含有这些字段时必须失败关闭，由维护者删除后再使用当前标准配置。旧通用 Runtime 设置和旧实现偏好属于退役运行态，读取时直接丢弃。Codex 活跃配置只保留 Runtime 是否启用及其私有依赖、认证和 Native 数据定义。

共享契约验收要求至少以 Codex 与一个最小测试 Runtime 并存验证：默认选择可持久化且稳定，临时健康失败不改写已保存偏好；两个 Runtime 的 Session 均按固定归属执行；一个 Runtime 的故障或维护不会影响另一 Runtime 已受理任务；以及 Worker、Native 映射、页面分组和外部入口不会将任一 Session 投递到当前默认以外的 Runtime。通过该验收只表示共享底座可用；任一候选 Runtime 仍须按自己的专项设计完成私有实现和验收。

## 能力与所有权

| 能力组 | Runtime Adapter 负责 | Runtime Runner 负责 | Chub/Worker 负责 |
| --- | --- | --- | --- |
| 运行状态 | 依赖检查和可用性原因 | 不适用 | 启用状态、入口失败关闭和展示。 |
| 后台 Turn | 请求参数校验、模型目录 | 构建固定进程、解析结构化结果和错误 | 任务、租约、超时、取消、恢复和最终状态。 |
| Native Session | ID 校验、发现、writer probe、原生归档/删除及其最终状态查询 | 不适用 | Chub 映射、唯一绑定、页面投影和操作编排。 |
| 用量与设置 | 模型目录、用量快照、登录页和 Runtime 专属设置 | 不适用 | 受保护 API、页面与通用设置边界。 |

当前后台 Runtime 至少需要 `runtime_status`、`background_turn`、`task_cancel`、`native_session_mapping`、`structured_events` 和 `permission_profiles`。支持已绑定 Session 时需要 `session_resume`；Native 生命周期操作需要 `session_archive`；writer 安全判断需要 `writer_probe`。模型、用量、登录页和设置能力按描述符单独声明。

Runtime 不创建 Chub Session、不写 Chub Store、不管理任务文件、租约、通知、微信路由或操作日志。Worker 不解析 Runtime 私有数据库、锁格式或上游错误协议；双方只交换共享契约中的有界模型和错误。

## 后台 Turn 契约

`RuntimeTurnRequest` 只包含受控权限、可选 Native ID、模型和推理等级。首个任务不提供 Native ID；已绑定任务提供该 Session 的固定 Native ID。Runner 返回结构化事件摘要与有界最终文本，Worker 从中取得可信 Native ID、结果和可诊断错误。

权限档位固定为 `read-only`、`auto-review` 与 `full-access`。`ask` 不能进入后台任务。模型和推理等级必须先由 Adapter 的模型目录校验；非法 Native ID、缺失能力、不可用实现和不兼容 Native 格式均在任务启动前失败关闭。

Runner 只能在 Worker 提供的固定工作区、固定可执行文件和受限结果路径内运行。结果文件、事件流和错误读取都必须验证类型、所有者、权限、字节上限和单行上限；上游错误在公开模型、日志和通知中保持脱敏且有界。

## Native Session 与 writer

Adapter discovery 将 Runtime 原生数据规范化为只读 `RuntimeNativeSession`，并返回本次扫描是否完整及可选归档状态索引。`RuntimeNativeSession` 将 naive 创建/更新时间按 UTC 规范化，并将带时区值转换为 UTC；Runtime 不得用 naive 值表达本地时区时间。发现失败只影响该 Runtime 的 Native 列表，不阻断已有 Chub Session、Worker 已受理任务或无关 Runtime。Runtime 应隔离单项记录和辅助元数据来源的读取失败：可用项继续作为本次只读结果返回，下一次 discovery 重新读取原生来源；跳过任一原生记录时必须将本次结果标为不完整，不完整结果不得用原生项缺席推断删除。只有完整扫描与可用状态索引共同确认缺席时，Chub 才可清理对应绑定；明确归档状态可直接收敛。不得把本机历史数据量或单一辅助来源异常扩大为全局提交门禁。具体 Runtime 的数据源、字段、资源释放和可选元数据消费规则只在其专项设计中维护。

writer probe 的结果只回答当前 Native Session 是否由外部进程占用。明确占用时，Chub 拒绝会造成双写的提交与生命周期操作；探测失败时，具体高风险操作失败关闭。无 writer 或可安全尝试的 Native 操作必须由执行层给出最终结果，不能仅因历史状态、旧 PID 或页面投影拒绝。

归档和删除由 Adapter 执行，并以原生最终状态查询作为幂等依据。无法确认原生终态时，Chub 保留自己的映射和任务记录，不宣称操作成功。

## 当前接入摘要

当前唯一接入的 Runtime 是 Codex，并以 `codex` CLI 作为其本机执行依赖；缺失依赖只关闭 Codex 相关的新提交。Codex 的 Native 数据来源、命令、用量、认证、缓存和展示口径均属于专属实现事实，以[Chub Codex Runtime 设计](CHUB_CODEX_RUNTIME_DESIGN.md)为唯一来源。本节不为 Codex 或后续 Runtime 增加第二份私有契约。

## 维护与失败边界

Runtime 停用只影响新的任务受理；已受理任务按创建快照收敛。实现槽位的导入、覆盖、移除和开发刷新按[Chub AI Runtime 插件模块设计](CHUB_RUNTIME_PLUGIN_DESIGN.md)执行，只有 Web 与 Quick Worker 都确认注册表后才报告成功。

单一实现加载、依赖或入口失败只能使该实现不可用；它不得阻断 Chub 控制面、只读状态、其他 Runtime 或独立服务。Runtime 操作的进程创建、HTTP 成功和文件写入均不是成功条件，必须由 Worker、Adapter 或原生最终状态确认。

## 验收范围与复检

已验证：Codex 后台提交、首个 Native 绑定、`resume`、模型校验、Native 发现投影、writer 保护和原生归档/删除的相关自动化路径。

修改能力集合、Descriptor identity、Runner 请求/结果、Native 发现或 writer 判断、实现槽位兼容规则，必须重新验收 Web、Quick Worker 和受影响的 Runtime 插件操作。任一具体 Runtime 接入前还必须在其专项设计中明确本机依赖、认证与敏感数据、能力声明、Native 状态归属、故障隔离、目标平台和验收用例。
