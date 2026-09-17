# Chub AI Runtime 插件模块设计

> 状态：持续维护
> 主要读者：AI Agent；维护者通过设置页完成受控的导入、启用、切换和验收。
> 本文负责：Runtime 插件的交付边界、模块协议、Web/Quick Worker 装配、生命周期、恢复边界和新 Runtime 接入检查表。
> 本文不负责：Adapter/Runner 的共享能力语义（见[Chub AI Runtime 架构设计](CHUB_AI_RUNTIME_DESIGN.md)）、Session/Worker 状态机、任务编排插件、微信固定指令，或任何 Runtime 的私有认证、Native 数据与目标平台实现。

## AI 可执行契约

1. Runtime 插件是维护者信任的本机制品，不是插件市场、Codex Skill、提示词文件或外部入口可调用的扩展点。微信、OpenClaw、自动化和普通页面不能上传、安装、替换、删除或执行模块。
2. 插件拥有自己的 Manifest、Adapter、Worker Runner、能力声明、显示信息和可选私有设置/认证/Native 行为；Chub 拥有受保护入口、逻辑 Session、Quick Worker 任务与终态、通知、操作日志和页面壳。Chub 不实现某个 Runtime 的认证流程或私有数据源，只负责其入口权限和结果投影。
3. 模块身份只由 Manifest 的 `runtime_id` 与 `implementation_id` 决定；目录、ZIP 文件名和 Python 包名不决定身份。客户端不能提交 Runtime、实现、命令、路径、环境或 Native ID。
4. Web 与 Quick Worker 必须从同一协议分别装配目标 Runtime，并各自确认注册表。目录写入、HTTP 成功或其中一端加载成功都不等于操作成功。
5. 任一模块的清单、依赖、入口或私有 Runtime 故障只影响该模块和直接依赖它的新任务；不得阻断控制面、无关 Runtime、既有任务、只读能力或独立服务。
6. Session 创建时固定 `runtime_id + implementation_id`。默认 Runtime/实现切换、开发刷新、覆盖、停用和加载失败都不得改投、重放或取消已有 Session 与已受理任务。

## 当前范围与完成条件

当前生产只接入 Codex：正式 ZIP 使用固定安装槽位，开发实现为 `codex-runtime-dev`。最小测试 Runtime 只用于自动化验证，不属于产品能力或正式部署包。

共享控制面已经完成多 Runtime 适配。第二个及后续 Runtime 只交付独立模块和专项设计，在既有生命周期中导入、启用并设为默认后即可被 Chub 使用。不得因 Runtime 身份修改通用 Session、Quick Worker、公共 API、共享设置、工作台分组、微信普通任务路由或插件生命周期。

这不表示新 Runtime 可以绕过共享契约。它必须按共享能力提供模型目录、错误与显式 `runtime_id` 的用量快照；需要微信文本优化时必须声明 `background_turn`。通用页面按 Session 固定实现读取模型目录，错误来源只显示“上游 Runtime”，不识别具体 Runtime 身份。无法由现有契约表达的新需求，先扩展共享契约并完成所有已接入 Runtime 回归，不在 Chub 主模块按 Runtime 名称加分支。

## 模块交付与装配

### ZIP 协议

模块 ZIP 根目录必须有唯一 `chub-module.json`，当前只接受 `protocol_version: 1` 与 `module_type: "runtime"`。清单至少声明：

| 字段 | 规则 |
| --- | --- |
| `module_id` | 全局唯一，且等于 `implementation_id`。 |
| `runtime_id`、`implementation_id`、`native_session_compatibility_id` | 分别表示逻辑 Runtime、具体槽位和 Native 兼容组；必须与 Module、Adapter、Runner Descriptor 一致。 |
| `display_name`、`description`、`version` | 用于设置页与安装记录；入口返回的展示信息必须一致。 |
| `chub_version` | 必须精确匹配当前 Chub 版本。 |
| `entry` | `<python_module>:<factory>`；工厂接收当前 `Settings` 并返回 Runtime 注册对象。 |
| `dependencies` | 可选的包内 requirements 相对路径；依赖只能装入模块自己的目录。 |

ZIP 不以文件名或存放路径决定身份。压缩包、清单、文件数量和路径均受固定上限；绝对路径、`..`、符号链接和不受控文件类型直接拒绝。安装目录、暂存目录、元数据和恢复记录以受限权限创建。

### 开发实现与装配

开发源码受控发现于 `runtime-modules/<module>/` 的一级目录。每个候选需要有效清单，并以独立 Python 命名空间加载；损坏清单、缺失依赖或入口失败只隔离该候选。目录重命名不改变 Runtime 身份，刷新只清理目标实现自己的缓存。

正式 ZIP 与开发源码都由 Web 和 Quick Worker 独立发现。启动扫描与导入预检验证清单、Chub 版本、可选依赖、入口、显示信息、Descriptor 身份和共享能力。Adapter 与 Runner 应从同一 Descriptor 构造；若未来允许独立构造，必须新增完整身份比较及拒绝测试。当前 Codex 正式槽位标识为 `codex-` 加六位数字；这是 Codex 私有发布规则，不是通用协议要求。

## 生命周期与恢复边界

插件管理以通用 `runtime` 制品处理生命周期：开发制品为 `development:<implementation_id>`，ZIP 制品为 `runtime:<implementation_id>`。导入、启用、停用、默认实现切换、覆盖、开发刷新和移除都按目标 Runtime/实现槽位执行。

- 导入或覆盖前只检查目标槽位的非终态任务；不建立全局维护锁或排空无关任务。
- Web 和 Worker 分别加载目标槽位；确认 Worker 实现列表和可用状态收敛后，才记录最终成功。
- 默认切换只是后续新 Session 的偏好更新，不替换已有 Adapter、Runner、Native 映射或任务快照。
- 覆盖或刷新失败时，只恢复本次目标槽位；通信或最终状态不能确认时明确记录状态未知，不猜测成功或回滚成功。
- 物理删除仅在目标槽位没有已绑定 Session、没有非终态任务且不是当前默认槽位时允许；否则说明局部解除条件。开发刷新不复制、删除或替换安装目录。

普通导入、覆盖、删除和刷新不清理逻辑 Session、任务、Chub 通用设置、其他模块设置、第三方/Runtime 原生数据、操作日志、用户工作区或明确保留的数据。旧 `builtin-dev` 绑定和旧 `codex-runtime` 生命周期记录是固定的不兼容 Chub 自有运行态，可按受控恢复记录清理；不保留双读、映射或回退。

## 页面、外部入口与维护者操作

设置页只向维护者可信网络展示 Runtime 插件的名称、版本、导入/启用/可用状态和受控失败原因。导入不等于启用，启用不等于成为默认；删除必须使用明确说明解除条件与不可恢复性的确认交互。Runtime 不可用时，只拒绝直接依赖它的新提交。

维护者通过设置页执行模块操作。第一方 Codex ZIP 可用 `python scripts/build_codex_runtime_zip.py --implementation-id codex-010001 --version 1.0.1 --description "简短发版特性说明"` 构建；不要直接复制、替换或删除安装目录。页面最终状态和操作日志是维护结果依据；无法确认时再调查对应错误，不手动清理其他 Runtime、Worker 或用户数据。

## 新 Runtime 接入检查表

- 模块声明共享能力，并提供 Adapter、Runner、模型目录、错误和显式 Runtime 身份的用量快照；不支持的可选能力明确拒绝。
- Manifest、Adapter 与 Runner 的 Runtime/实现/Native 兼容组身份一致，Web 与 Worker 均能独立加载。
- 私有认证、配置、缓存、命令与 Native 数据仅在模块边界内；不创建 Chub 路由、页面导航、外部指令、后台服务、任意命令或任意路径入口。
- 默认切换后，新 Session 使用该 Runtime；已有 Codex 与新 Runtime Session 仍按固定实现完成模型校验、任务提交、恢复、Native 操作和最终状态确认。
- 缺少 `background_turn` 时拒绝新的微信文本优化，不回退；真实微信、私有 Native 行为和目标平台由该 Runtime 专项验收。
- 覆盖、停用、移除、损坏入口和单端加载失败只影响目标槽位；无关 Runtime 的既有任务与只读能力继续工作。

## 验收范围与复检

已由自动化覆盖：最小测试 Runtime 与 Codex 的默认切换、Session 固定归属、Worker 提交快照、模型目录、Native 映射、单来源失败隔离、开发发现、通用生命周期和公共入口回归。历史 macOS 验证覆盖 Codex ZIP 的构建、导入、移除、重新导入、Web/Worker 注册确认与 Quick Worker 提交。

未验证或不承诺：Ubuntu 实机服务、真实微信收发、人为破坏安装目录/依赖后的实机恢复，以及任何尚未接入 Runtime 的私有链路。Runtime 协议、生命周期、共享状态所有权、公开 API、用户可见 Runtime 行为或微信路由变化时，必须复跑相关自动化；涉及真实微信时由维护者在微信客户端验收。

## 相关文档

- [Chub AI Runtime 架构设计](CHUB_AI_RUNTIME_DESIGN.md)：共享能力、Adapter/Runner 和多 Runtime 路由。
- [Chub Session 状态模型设计](AI_SESSION_STATE_DESIGN.md)：Session、Native 映射和 writer 语义。
- [Chub Quick Worker 独立服务设计](CHUB_QUICK_WORKER_DESIGN.md)：任务、租约、恢复和通知终态。
- [Chub Codex Runtime 设计](CHUB_CODEX_RUNTIME_DESIGN.md)：Codex 私有认证、用量和 Native 行为。
- [Chub 集成能力清单](CHUB_INTEGRATION_CAPABILITIES.md)：当前可调用能力与微信用户可见契约。
