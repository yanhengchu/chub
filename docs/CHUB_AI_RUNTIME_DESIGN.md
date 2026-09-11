# Chub AI Runtime 架构设计

> 状态：已验收
> 主要读者：AI Agent；维护者通过与 AI Agent 协作，理解并确认本文规则。
> 本文负责：Runtime 共享契约、实现槽位、Adapter/Runner 边界、Native Session 与新增 Runtime 的接入判定。
> 本文不负责：Codex 等具体 Runtime 的私有行为、Chub Session 生命周期、Worker 任务恢复、Runtime 插件模块安装和微信路由。
> 维护说明：Runtime 插件模块已作为当前实现交付；本文定义所有 Runtime 共用的边界。本文“已验收”仅表示共享 Runtime 契约已验收，不替代各 Runtime ZIP 生命周期、目标平台或私有行为的专项验收。当前唯一接入的 Codex 私有行为以[Chub Codex Runtime 设计](CHUB_CODEX_RUNTIME_DESIGN.md)为准，ZIP 生命周期以[Chub AI Runtime 插件模块设计](CHUB_RUNTIME_PLUGIN_DESIGN.md)为准。

## AI 可执行契约

Runtime 是 Chub 的受控本机执行实现。客户端、页面、微信和其他外部入口不能选择 Runtime 命令、路径、环境变量、Native ID 或实现槽位；它们只调用 Chub 已定义的 Session 和任务用例。

AI Runtime 通用配置保存在本机 `config/ai-runtimes.local.yaml`。其中的新建 Session 默认权限仅应用于创建时未明确指定权限的后续 Chub Session；已有 Session 与已受理任务不受影响。

新建 Session 固定提供 `chub`、`home` 与 `workspace` 三个内置工作目录。选择 `workspace` 后，任务可在该受信根目录及其全部子目录内工作，无需将每个项目子目录登记为独立工作区。只有需要把某个目录单独展示并作为 Session 的默认工作目录时，维护者才在本机 `settings.local.yaml` 的 `ai_runtime.codex.extra_workspaces` 显式登记；每项使用固定 ID、名称和路径，ID 不得覆盖内置目录。页面与 API 只接受后端已加载的目录 ID，不能传入任意路径；已创建 Session 继续保存其创建时的工作目录，移除已使用的额外目录前必须先处理关联 Session。Quick Worker 重载后才会使用新增或移除的额外目录映射执行新任务。

AI Agent 应先按问题范围选择文档：共享 Runtime 能力、Adapter/Runner 与 Native 兼容规则看本文；Codex 私有行为看[Chub Codex Runtime 设计](CHUB_CODEX_RUNTIME_DESIGN.md)；Runtime 插件模块 ZIP 的导入、覆盖、删除和 `builtin-dev` 重载看[Chub AI Runtime 插件模块设计](CHUB_RUNTIME_PLUGIN_DESIGN.md)；Session 与 Worker 领域状态分别看对应专项设计。

Runtime 的 `implementation_id` 是可维护的**实现槽位**：默认实现只影响新建 Session，Session 创建后固定该槽位。它不是任务编排插件模块 ZIP 的 `implementation_ref`；后者是包含内容摘要的**不可变产物引用**，只用于编排请求快照，并由[Chub 任务编排插件模块架构设计](CHUB_TASK_ORCHESTRATION_PLUGIN_DESIGN.md)定义。两者不得跨领域复用或互相替代。

每个 Runtime 实现以不可变 `RuntimeDescriptor` 注册：

| 字段 | 规则 |
| --- | --- |
| `runtime_id` | 稳定的逻辑 Runtime 标识；当前为 `codex`。 |
| `implementation_id` | 具体可信实现槽位；当前开发实现为 `builtin-dev`。 |
| `native_session_compatibility_id` | 同一 Native 格式的兼容组；已绑定 Session 只能使用兼容实现。 |
| `capabilities` | 固定能力集合；Adapter 与 Runner 必须声明完全一致的 Runtime 身份和能力。 |

默认实现只影响之后新建的 Chub Session。Session 创建时固定实现槽位；已有 Session、Native 映射和已受理任务不因默认切换、模块覆盖或刷新而改投。

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

Adapter discovery 将 Runtime 原生数据规范化为只读 `RuntimeNativeSession`，并返回本次扫描是否完整及可选归档状态索引。发现失败只影响该 Runtime 的 Native 列表，不阻断已有 Chub Session、Worker 已受理任务或无关 Runtime。Runtime 应隔离单项记录和辅助元数据来源的读取失败：可用项继续作为本次只读结果返回，下一次 discovery 重新读取原生来源；不完整结果不得用原生项缺席推断删除。只有完整扫描与可用状态索引共同确认缺席时，Chub 才可清理对应绑定；明确归档状态可直接收敛。不得把本机历史数据量或单一辅助来源异常扩大为全局提交门禁。具体 Runtime 的数据源、字段、资源释放和可选元数据消费规则只在其专项设计中维护。

writer probe 的结果只回答当前 Native Session 是否由外部进程占用。明确占用时，Chub 拒绝会造成双写的提交与生命周期操作；探测失败时，具体高风险操作失败关闭。无 writer 或可安全尝试的 Native 操作必须由执行层给出最终结果，不能仅因历史状态、旧 PID 或页面投影拒绝。

归档和删除由 Adapter 执行，并以原生最终状态查询作为幂等依据。无法确认原生终态时，Chub 保留自己的映射和任务记录，不宣称操作成功。

## 当前接入摘要

当前唯一接入的 Runtime 是 Codex，并以 `codex` CLI 作为其本机执行依赖；缺失依赖只关闭 Codex 相关的新提交。Codex 的 Native 数据来源、命令、用量、认证、缓存和展示口径均属于专属实现事实，以[Chub Codex Runtime 设计](CHUB_CODEX_RUNTIME_DESIGN.md)为唯一来源。本节不为 Codex 或后续 Runtime 增加第二份私有契约。

## 维护与失败边界

Runtime 停用只影响新的任务受理；已受理任务按创建快照收敛。实现槽位的导入、覆盖、移除和开发刷新按[Chub AI Runtime 插件模块设计](CHUB_RUNTIME_PLUGIN_DESIGN.md)执行，只有 Web 与 Quick Worker 都确认注册表后才报告成功。

单一实现加载、依赖或入口失败只能使该实现不可用；它不得阻断 Chub 控制面、只读状态、其他 Runtime 或独立服务。Runtime 操作的进程创建、HTTP 成功和文件写入均不是成功条件，必须由 Worker、Adapter 或原生最终状态确认。

## 验收范围与复检

已验证：Codex 后台提交、首个 Native 绑定、`resume`、模型校验、Native 发现投影、writer 保护和原生归档/删除的相关自动化路径。

修改能力集合、Descriptor identity、Runner 请求/结果、Native 发现或 writer 判断、实现槽位兼容规则，必须重新验收 Web、Quick Worker 和受影响的 Runtime 插件操作。
