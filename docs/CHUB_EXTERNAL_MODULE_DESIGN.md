# Chub AI Runtime 外置模块功能设计

> 状态：持续维护
> 主要读者：需要维护 Runtime ZIP 或评估新增 Runtime 的 AI Agent；维护人员用于确认模块管理范围和恢复边界。
> 本文负责：定义当前 Runtime 外置模块的架构、ZIP 协议、版本槽位导入与覆盖、删除边界、设置页行为及复检要求。
> 本文不负责：能力编排模块（见[Chub 能力编排外置架构设计](CHUB_TASK_ORCHESTRATION_EXTERNALIZATION_DESIGN.md)）、AI Runtime 的 Adapter/Runner 通用实现规范（见[Chub AI Runtime 架构设计](CHUB_AI_RUNTIME_DESIGN.md)）、AI Session/Worker 的状态机与恢复细节，或微信固定指令和用户可见回复格式（见[Chub 集成能力清单](CHUB_INTEGRATION_CAPABILITIES.md)第 4 节）。
> 维护说明：Runtime ZIP 外置已是当前实现：第一方 Codex 可由固定槽位的 ZIP 或 `builtin-dev` 开发源码提供。本文中的“已验证”仅表示已有自动化或历史实机结论；未验证平台、第二 Runtime 及列出的复检项目不因此成为已验收能力。

## AI 可执行契约

1. Chub 当前外置的是**可信本机 Runtime 模块**。模块以 ZIP 导入、在固定目录安装并由 Web 与 Quick Worker 分别加载；它不是多用户插件市场、Codex Skill、提示词文件或外部通道可调用的扩展机制。
2. 当前安装协议只接受 `runtime` 类型，且维护入口端到端只支持 `runtime_id=codex` 的第一方 Codex 实现。第二 Runtime 尚未接入；其他 `runtime_id` 即使清单能够解析，也不得成为活动实现，必须在受控预检或激活回滚中保持未生效。能力编排外置由独立设计维护，当前微信任务润色继续由主项目的固定入口、设置页和既有状态所有权处理。
3. Runtime 模块只提供 Runtime 私有实现和注册信息：Adapter、Worker Runner、能力、显示信息及可选专属设置。Chub 核心继续拥有认证、固定 API、逻辑 AI Session、Quick Worker 任务/租约/终态、通知、操作日志和页面交互。
4. Codex 的逻辑 `runtime_id` 固定为 `codex`。每个 `implementation_id` 是可维护的版本槽位：正式 ZIP 使用 `codex-` 加六位数字，`builtin-dev` 直接加载仓库开发源码。兼容的 ZIP 可以覆盖同一正式槽位，开发刷新可原地重载 `builtin-dev`；两者只在目标槽位存在排队或运行的 Quick Worker 任务时拒绝。默认实现只用于新建 Chub Session；Session 创建时固定槽位，原生 Session 与后续任务均使用该槽位，默认切换不检查或影响已有 Session、排队任务和运行任务。维护者不能把已受理任务改失败、改投新版本或重试。Web 与 Worker 注册表都确认后才能报告最终结果；HTTP 成功或目录写入本身都不代表切换成功。
5. 单个 ZIP、清单、依赖或入口故障只能令该模块不可用或本次操作失败，不得阻塞 Chub 控制面、无关 Runtime、只读能力或独立服务。外置机制不放宽 loopback/Tailnet 来源校验、固定路由、输入限制、敏感信息保护或失败关闭要求。
6. 覆盖或开发刷新不改写既有 Session 或任务；已启动任务保留已取得的 Runner，不提供把旧 Session 或旧任务迁移、重放或改投的机制。物理删除仍只在不再被 Session 引用且无非终态任务时允许。状态清理恢复记录不等于正常模块操作的清理步骤；它只在已有受控恢复记录时重试固定的 Chub 自有运行态收敛，普通导入、覆盖和删除不会清理 Session、任务或第三方原生数据。Chub 通用设置、其他模块设置、第三方原生数据、操作日志和明确要求保留的数据不在清理范围。

## 当前架构

### 当前范围

当前生产从固定安装目录发现正式 Codex Runtime ZIP。第一方 Codex Runtime 的开发源码保留在仓库 `runtime-modules/codex-runtime/`，`builtin-dev` 直接从该目录重新加载，不复制到 ZIP 安装目录。设置页可设置一个健康且启用的默认实现；它只决定之后新建 Chub Session 的实现。Session 创建时立即保存固定槽位，Quick Worker、微信、自动化和周报等后续任务均从 Session 读取该槽位，页面、外部指令和请求正文均不提供 `implementation_id`。清单字段保留通用 `runtime_id` 是为后续独立接入做准备，不构成当前第二 Runtime 的安装或维护能力。

设置页的“AI Runtime”分组动态读取已加载 Runtime 的名称和说明，并提供 Runtime ZIP 的预检、导入/覆盖、设为默认、移除及状态查看。导入覆盖和“刷新开发代码”只检查目标槽位的排队或运行任务，不暂停、检查或阻断已绑定 Session、旧 PID、历史 writer 或页面 `unknown`。Web 与 Quick Worker 都确认新注册表后才报告成功。只有明确物理删除 ZIP 槽位时，才检查该槽位是否仍被 Session 或非终态任务引用。页面不提供客户端 Runtime、Runner、命令、路径或环境变量选择器。当前微信任务润色仍是独立的固定设置页，不是外置能力编排模块；其通用目标边界见[Chub 能力编排外置架构设计](CHUB_TASK_ORCHESTRATION_EXTERNALIZATION_DESIGN.md)。

```text
维护者选择 Runtime ZIP
          |
          v
受保护的预检和安装入口
  - ZIP、清单、依赖、模块入口、Adapter/Runner wiring
          |
          v
固定 Runtime 安装目录 <----> Web Runtime 注册表
          |                         |
          |                         v
          |                    设置页和 AI Session Manager
          v
Quick Worker 按目标实现热刷新并确认注册表
          |
          v
目标 Runtime 槽位的注册确认与最终操作记录
```

Web 与 Quick Worker 各自构造进程内 Runtime 实例，但都从同一个安装目录和同一份 Runtime 模块协议加载。两者不会共享 Python 对象或以某一方的加载成功替代另一方的确认。

### 与其他领域的契约

Runtime 模块只拥有 Runtime 私有实现和清单声明。Adapter/Runner 的共享契约、能力矩阵和新增 Runtime 实现规范以[Chub AI Runtime 架构设计](CHUB_AI_RUNTIME_DESIGN.md)为准；逻辑 Session 和单 writer 语义以[Chub Session 状态模型](AI_SESSION_STATE_DESIGN.md)为准；任务、租约、恢复和通知终态以[Chub Quick Worker 独立服务设计](CHUB_QUICK_WORKER_DESIGN.md)为准。

模块安装流程只负责把已验证的目标 Runtime 槽位加入 Web 与 Worker 注册表。Chub 负责保存 Session 与任务的槽位引用、记录交互任务并投影 native/Worker 最终状态；模块不得接管上述领域状态，也不得创建路由、页面导航、外部指令、后台服务、任意命令或任意文件路径。

当前 Codex 模块先以清单校验模块 Descriptor 的 `runtime_id`、`implementation_id` 和 `native_session_compatibility_id`，再由同一个 Adapter Descriptor 构造 Worker Runner，因此两端的完整身份相同。通用 `validate_runtime_wiring()` 当前校验 `runtime_id` 与能力集合；它不单独比较槽位和 Native 兼容组。新增 Runtime 模块不得绕开“同一 Descriptor 构造 Adapter/Runner”的模式；若改为独立构造，两端必须补齐完整身份比较及相应拒绝测试后才能接入。

## Runtime ZIP 协议

### 包形态

模块包固定为 ZIP，包根目录必须有唯一的 `chub-module.json`。当前协议版本为 `1`，只接受 `module_type: "runtime"`。清单包含以下字段：

| 字段 | 规则 |
| --- | --- |
| `protocol_version` | 必须为 `1`；未知协议直接拒绝，不兼容读取旧协议。 |
| `module_id` | 全局唯一的小写实现标识；必须与 `implementation_id` 一致。 |
| `runtime_id`、`implementation_id`、`native_session_compatibility_id` | 分别声明逻辑 Runtime、版本槽位和原生 Session 兼容组；三者必须与 Module、Adapter 和 Runner 的 Descriptor 一致。当前维护入口只接受并完整维护 `runtime_id=codex`；正式 Codex ZIP 的 `implementation_id` 固定为 `codex-` 加六位数字，覆盖同一槽位必须保持该原生兼容组。 |
| `module_type` | 当前仅允许 `runtime`。 |
| `display_name`、`description`、`version` | 用于设置页展示和安装记录；入口返回的展示信息必须与清单一致。正式 Codex ZIP 的 `description` 必须是本次发版的简短特性说明（最多 300 字），说明用户或维护者可感知的变化；不能只重复“使用 Codex CLI”。 |
| `chub_version` | 必须精确匹配当前 Chub 版本。 |
| `entry` | 固定为 `<python_module>:<factory>`；工厂接收当前 `Settings` 并返回 Runtime 注册对象。 |
| `dependencies` | 可选的包内 requirements 相对路径；依赖安装到该模块自己的目录。 |

ZIP 不以文件名或存放目录决定模块身份。压缩包和清单都受固定大小、文件数量和路径限制；绝对路径、`..` 路径和符号链接会被拒绝。安装目录、暂存目录、清单、安装元数据和恢复记录均以受限权限创建。

### 加载与装配

Chub 仅扫描固定 Runtime 安装目录。每个模块以 Runtime ID 私有 Python 命名空间加载，避免不同 ZIP 的同名包复用解释器缓存。安装前和启动扫描时均会验证：

- 清单格式、Chub 版本和可选依赖清单；
- Python 入口可加载且返回 Runtime 注册对象；
- 清单、显示信息和 `runtime_id` 一致；
- Adapter、Worker Runner、能力矩阵和 Runtime 状态满足共享契约；
- 注册表中不存在重复 Runtime 或重复默认 Runtime。

依赖安装、入口加载或装配校验失败时，候选模块不激活。启动时发现损坏安装目录时，Chub 将它标为不可用并继续加载其他 Runtime；控制面、模块列表和导入入口仍可用。

## 导入、覆盖与删除

### 维护流程

维护者只能通过设置页的 Runtime 模块入口导入、覆盖或删除 Codex 版本槽位。导入前，服务端先完成只读预检；非 Codex Runtime、无效 ZIP 或无法完整装配的候选均不得激活，也不会排空 Quick Worker。开发版刷新还要求 Codex Runtime 已启用、Quick Worker 健康且协议可确认，以及目标槽位没有排队或运行任务。有效 ZIP 按以下顺序处理：

1. 只在目标槽位有排队或运行任务时拒绝；不为导入、覆盖、默认切换或开发刷新增加全局维护锁、排空或提交门禁。
2. Web 与 Worker 分别加载目标槽位；已启动任务保留其已取得的 Runner，已有 Session 按绑定槽位继续解析原生状态。
3. 确认 Worker 的实现列表和目标槽位可用状态已经收敛，再完成导入操作记录。

导入、覆盖和删除操作均记录 `requested`、`started`、`succeeded` 或 `failed`。设置页只在最终成功后更新模块状态；失败会显示可诊断原因，不把“已接受”或“重载已请求”显示为成功。

### 恢复和失败边界

导入或覆盖会记录受限的激活日志。ZIP 的 Web 注册或 Worker 明确拒绝时，Chub 撤销本次目录替换并重新确认原槽位；开发重载被 Worker 明确拒绝时，Web 恢复此前已加载的开发 Runtime。Worker 通信或最终健康无法确认时，不猜测已恢复或已生效，操作明确进入状态未知。

当 Web 与 Worker 已确认新注册表后，目标槽位可供新 Session 选择。默认切换只是偏好更新，不更换现有 Adapter、Runner、native session 或任务绑定。进程在导入中断时，下一次启动仅恢复或清理本次未完成的替换，不猜测成功。只有已存在的受控状态清理记录时，启动恢复才会处理其记录的 Chub 自有运行态；它不会由普通模块维护临时创建，也不会清理关联 Session、任务或用户数据。

覆盖 Runtime 不会自动切换已有任务到其他 Runtime，也不影响无关 Runtime、只读能力或独立服务。物理删除是局部破坏操作：仅当目标槽位没有已绑定 Session、没有非终态任务且不是当前默认槽位时才允许；否则保持该槽位可运行并提示解除条件。开发源码不复制、删除或替换安装目录，刷新仅重载当前源码。

### 状态清理边界

单个槽位的导入、覆盖或删除不清理逻辑 Chub Session、共享历史或其他槽位的任务。每个 Chub Session 在创建时固定实际 `implementation_id`；任务和原生 Session 不自动改投默认实现，即使兼容组相同。Chub 自身的历史 writer、旧 PID、页面状态和 `unknown` 投影不得阻止默认切换、导入、覆盖或已有 native session 的正常使用。只有物理删除仍被引用的槽位才拒绝，并给出“归档/删除关联 Session、等待任务终态或改用其他默认槽位”的局部恢复路径。

清理不得覆盖 Chub 通用设置、其他 Runtime 的设置或状态、第三方原生数据、操作日志、用户工作区和明确要求保留的数据。首次安装没有目标 Runtime 数据时不执行无关清理。

## 页面与外部边界

设置页中的 Runtime 模块区域只面向维护者可信网络开放。它展示已发现槽位的名称、版本、状态和受控失败原因；删除使用确认交互。删除确认只说明目标槽位的解除条件和不可恢复性，不承诺清理 Chub Session、任务或第三方原生数据。

微信、OpenClaw、自动化和其他外部入口不能上传、导入、覆盖、删除或执行模块，也不能借模块取得更高权限。Runtime 不可用、停用或依赖不足时，只拒绝直接依赖它的新提交；Quick Worker 健康、已有任务查询、无关 Runtime 和独立服务按各自规则继续工作。

## 冻结的后续方向

以下内容不属于当前实现，不得在页面、API、设置或用户可见文案中宣称可用：

- **Ubuntu 验收**：当前第一阶段的实机服务验收冻结，未由 macOS 验收或自动化覆盖替代。
- **第二 Runtime**：尚未实现。未来接入必须按[Chub AI Runtime 架构设计](CHUB_AI_RUNTIME_DESIGN.md)完成能力评估、Adapter/Runner、固定注册、状态边界和目标平台验收；不得由客户端动态指定 Runtime 或自动切换。

## 维护者操作

第一方 Codex ZIP 通过 `python scripts/build_codex_runtime_zip.py --implementation-id codex-010001 --version 1.0.1 --description "简短发版特性说明"` 构建，再从设置页的“AI Runtime → Runtime 模块”导入或覆盖。需要不兼容原生格式时使用新的正式标识；兼容修复可覆盖原有槽位。开发代码直接在 `runtime-modules/codex-runtime/` 修改后使用“重新加载开发代码”。不要直接复制、替换或删除固定安装目录中的文件；这会绕过 Web/Worker 注册确认与任务保护。

模块操作失败时，以页面最终提示和操作日志为准。仅在提示状态无法确认或后续启动仍显示待恢复时，再按具体错误调查；不要手动清理其他 Runtime、Worker 或用户数据来解除单个模块故障。

本文只在 Runtime 模块协议、安装恢复、状态清理、设置页交互、安全边界或验收范围变化时更新；Runtime 私有 Adapter 细节、微信业务规则和 Quick Worker 状态机由各自权威文档维护。

## 验收范围与复检

历史已验证：第一阶段覆盖 macOS 本机的 Codex ZIP 构建、移除和重新导入、Web/Worker 注册确认、Quick Worker 提交、设置页桌面与手机布局，以及无模块、损坏 ZIP、损坏安装目录隔离、依赖失败保留旧版本、激活/Worker 恢复和状态清理边界的自动化测试。

待重新验收：兼容 ZIP 覆盖、开发源码直接重载、Session 创建即绑定、默认切换不影响旧 Session、按绑定槽位执行全部 native 操作，以及删除引用保护。当前 Codex Adapter 与 Runner 通过同一 Descriptor 构造；只有未来允许两者独立构造时，才必须新增实现槽位与原生兼容组不一致的直接拒绝用例。第二 Runtime 接入前，必须新增非 Codex ZIP 在预检阶段保持未激活的用例。

未验证或不承诺：Ubuntu 实机服务、真实微信收发、人为破坏本机安装目录或依赖环境后的实机恢复，以及第二个真实 Runtime。上述范围恢复后必须另行定义验收，不能沿用本阶段结论。

Runtime ZIP 协议、导入/覆盖/删除、Web/Worker 注册确认、槽位引用边界、固定 API、设置页模块管理交互，或任何用户可见 Runtime 行为变化时，必须重新执行相关自动化测试和 macOS 产品回归；涉及微信路由、通知或用户可见行为时，由维护者在真实微信客户端完成验收。

## 相关文档

- [Chub 总体架构设计](CHUB_ARCHITECTURE_DESIGN.md)：系统边界、领域分层和全局状态所有权。
- [Chub AI Runtime 架构设计](CHUB_AI_RUNTIME_DESIGN.md)：Runtime 共享契约、Adapter/Runner 与新增 Runtime 实现规范。
- [Chub 能力编排外置架构设计](CHUB_TASK_ORCHESTRATION_EXTERNALIZATION_DESIGN.md)：未来能力编排模块的通用目标边界。
- [Chub Quick Worker 独立服务设计](CHUB_QUICK_WORKER_DESIGN.md)：任务、租约、恢复、通知终态和重启协调。
- [Chub 集成能力清单](CHUB_INTEGRATION_CAPABILITIES.md)：当前可调用能力与微信用户可见产品契约。
- [Chub 前端 UI 模块化设计](FRONTEND_UI_DESIGN.md)：设置页动态展示与交互规范。
