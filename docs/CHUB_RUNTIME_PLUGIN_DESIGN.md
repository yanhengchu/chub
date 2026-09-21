# Chub AI Runtime 插件模块设计

> 状态：持续维护
> 主要读者：AI Agent；维护者通过设置页完成受控的导入、启用、切换和验收。
> 本文负责：Runtime 插件的交付边界、模块协议、Web/Quick Worker 装配、生命周期、恢复边界和新 Runtime 接入检查表。
> 本文不负责：插件宿主的通用发现、生命周期、页面承载、能力调用和状态隔离（见[Chub 总体架构设计](CHUB_ARCHITECTURE_DESIGN.md)）、Adapter/Runner 的共享能力语义（见[Chub AI Runtime 架构设计](CHUB_AI_RUNTIME_DESIGN.md)）、Session/Worker 状态机、任务编排插件、微信固定指令，或任何 Runtime 的私有认证、Native 数据与目标平台实现。

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

这不表示新 Runtime 可以绕过共享契约。它必须按共享能力提供模型目录和受限错误；需要额外的 Runtime 私有能力时，必须在对应专项设计中声明能力、权限和失败边界。`usage_snapshot`、登录页与账户切换属于 Runtime 私有可选能力：未声明或读取失败时，通用用量入口如实报告目标 Runtime 不可用；如需提供额度或账户切换，模块必须返回显式 `runtime_id` 的快照并在自己的专项设计中定义认证与恢复边界。通用页面按 Session 固定实现读取模型目录，错误来源只显示“上游 Runtime”，不识别具体 Runtime 身份。无法由现有契约表达的新需求，先扩展共享契约并完成所有已接入 Runtime 回归，不在 Chub 主模块按 Runtime 名称加分支。

## 模块交付与装配

Runtime 插件复用[总体架构](CHUB_ARCHITECTURE_DESIGN.md#212-插件宿主通用能力)规定的发现、导入、启停、页面承载、公开能力调用、状态隔离和恢复能力。本节只补充 Runtime 专属的 Manifest 字段、Adapter/Runner 装配、双端注册确认和实现槽位规则。

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

模块身份、包路径安全、安装隔离、暂存和操作状态遵循[总体架构](CHUB_ARCHITECTURE_DESIGN.md#212-插件宿主通用能力)的宿主规则；Runtime 只增加上述 Runtime 身份字段和双端装配校验。

### 开发实现与装配

开发源码发现遵循总体架构规定的受控索引、扫描顺序和候选隔离规则。Runtime 开发模块使用 `runtime/<module>/` 布局，Manifest 根目录放置 `chub-module.json`，并在自己的 Python 命名空间中提供入口；未登记、版本不匹配、依赖缺失或入口失败的候选保持不可用，不影响其他模块。

`runtime/<module>/` 是一个 Runtime 开发模块的根目录：根目录放 `chub-module.json` 与该模块私有依赖声明；需要多个 Python 文件时，模块在根目录下定义自己的 Python 包，例如 Codex 的 `chub_codex_runtime/`。该包是 Manifest `entry` 的导入命名空间，不是额外的模块类型或生命周期层级。`orchestration/` 与 `business/` 的目录职责由总体架构定义，不由 Runtime 加载器解释。

本机模块源的信任、发布和清理边界遵循总体架构与部署文档。Runtime 仍要求 Web 和 Quick Worker 分别确认同一目标实现；在两端完成确认前，不能把该 Runtime 写入当前已接入能力。

正式 ZIP 与开发源码都由 Web 和 Quick Worker 独立发现。启动扫描与导入预检验证清单、Chub 版本、可选依赖、入口、显示信息、Descriptor 身份和共享能力。Adapter 与 Runner 应从同一 Descriptor 构造；若未来允许独立构造，必须新增完整身份比较及拒绝测试。当前 Codex 正式槽位标识为 `codex-` 加六位数字；这是 Codex 私有发布规则，不是通用协议要求。

## 生命周期与恢复边界

插件管理使用总体架构规定的通用模块生命周期；Runtime 专属操作再按目标 Runtime/实现槽位执行。开发制品为 `development:<implementation_id>`，ZIP 制品为 `runtime:<implementation_id>`。

- 导入或覆盖前只检查目标槽位的非终态任务；不建立全局维护锁或排空无关任务。
- Web 和 Worker 分别加载目标槽位；确认 Worker 实现列表和可用状态收敛后，才记录最终成功。
- 默认切换只是后续新 Session 的偏好更新，不替换已有 Adapter、Runner、Native 映射或任务快照。
- 覆盖或刷新失败时，只恢复本次目标槽位；通信或最终状态不能确认时明确记录状态未知，不猜测成功或回滚成功。
- 物理删除仅在目标槽位没有已绑定 Session、没有非终态任务且不是当前默认槽位时允许；否则说明局部解除条件。开发刷新不复制、删除或替换安装目录。

普通导入、覆盖、删除和刷新按总体架构的模块状态隔离规则执行；Runtime 不清理逻辑 Session、任务、Chub 通用设置、其他模块设置、第三方/Runtime 原生数据、操作日志、用户工作区或明确保留的数据。旧 `builtin-dev` 绑定和旧 `codex-runtime` 生命周期记录是固定的不兼容 Chub 自有运行态，只在升级恢复的固定边界直接删除；不保留双读、映射、迁移或回退。

## 页面、外部入口与维护者操作

设置页只向维护者可信网络展示 Runtime 插件的名称、版本、导入/启用/可用状态和受控失败原因。导入不等于启用，启用不等于成为默认；删除必须使用明确说明解除条件与不可恢复性的确认交互。导入或激活失败必须区分 Quick Worker 通信、Worker 未就绪、注册表缺少目标实现和“已识别但 Runtime 不可执行”；最后一种提示维护者安装或修复本机所需 AI 工具。页面只展示固定、受长度限制的诊断，绝不透传 Worker 原始异常、命令输出、路径或凭据。Runtime 不可用时，只拒绝直接依赖它的新提交。

每个逻辑 Runtime 的详情页只读取和更新该 `runtime_id` 自己的默认 `implementation_id`；它不改变全局新 Session 默认 Runtime，也不能把其他 Runtime 的实现槽位写入本 Runtime。开发源码制品以实际发现结果识别，不以 Codex 的固定实现 ID 判断。

Runtime 如需提供微信固定指令组，Chub 必须在受控前缀目录中预先登记其逻辑 Runtime 与前缀，才能在该 Runtime 未导入时明确回复未导入状态；目录不保存子命令语法或处理逻辑。Runtime 导入、启用且可用后，只有目标 Runtime 自己的解析器可以解释该组子命令。当前 `codex` 前缀固定归 Codex Runtime；未导入、停用或不可用时不得回退为普通任务。

维护者通过设置页执行模块操作。第一方 Codex ZIP 可用 `python scripts/build/build-codex-runtime-zip.py --implementation-id codex-010001 --version 1.0.1 --description "简短发版特性说明"` 构建。不要直接复制、替换或删除安装目录。页面最终状态和操作日志是维护结果依据；无法确认时再调查对应错误，不手动清理其他 Runtime、Worker 或用户数据。

## 新 Runtime 接入检查表

接入不是“准备一个 ZIP 后设为默认”即可完成。开始本清单前，必须先完成[AI Runtime 架构设计](CHUB_AI_RUNTIME_DESIGN.md)规定的共享契约判定，并为目标 Runtime 建立私有专项设计，明确本机依赖、认证与敏感数据、Native 状态归属、故障隔离、目标平台和验收用例。下列检查只覆盖模块交付、装配和与共享底座的集成；私有链路的最终可用性仍由该 Runtime 专项验收确认。

- 模块声明共享能力，并提供 Adapter、Runner、模型目录和受限错误；用量、登录页、账户切换和私有设置仅在模块实际提供时声明并完成专项验收，未提供时由对应入口如实展示不可用状态。
- Manifest、Adapter 与 Runner 的 Runtime/实现/Native 兼容组身份一致，Web 与 Worker 均能独立加载。
- 私有认证、配置、缓存、命令与 Native 数据仅在模块边界内；不创建 Chub 路由、页面导航、外部指令、后台服务、任意命令或任意路径入口。
- 默认切换后，新 Session 使用该 Runtime；已有 Codex 与新 Runtime Session 仍按固定实现完成模型校验、任务提交、恢复、Native 操作和最终状态确认。
- 缺少专项设计声明的必需 Runtime 能力时，拒绝对应的新任务，不回退；真实外部入口、私有 Native 行为和目标平台由对应专项验收。
- 覆盖、停用、移除、损坏入口和单端加载失败只影响目标槽位；无关 Runtime 的既有任务与只读能力继续工作。

## 验收范围与复检

已由自动化覆盖：最小测试 Runtime 与 Codex 的默认切换、Session 固定归属、Worker 提交快照、模型目录、Native 映射、单来源失败隔离、开发发现、通用生命周期和公共入口回归。历史 macOS 验证覆盖 Codex ZIP 的构建、导入、移除、重新导入、Web/Worker 注册确认与 Quick Worker 提交。

未验证或不承诺：Ubuntu 实机服务、真实微信收发、人为破坏安装目录/依赖后的实机恢复，以及任何尚未接入 Runtime 的私有链路。Runtime 协议、生命周期、共享状态所有权、公开 API、用户可见 Runtime 行为或微信路由变化时，必须复跑相关自动化；涉及真实微信时由维护者在微信客户端验收。

## 相关文档

- [Chub AI Runtime 架构设计](CHUB_AI_RUNTIME_DESIGN.md)：共享能力、Adapter/Runner 和多 Runtime 路由。
- [Chub 总体架构设计](CHUB_ARCHITECTURE_DESIGN.md)：所有插件模块共用的宿主能力、生命周期、页面承载、能力调用和状态隔离。
- [Chub Session 状态模型设计](AI_SESSION_STATE_DESIGN.md)：Session、Native 映射和 writer 语义。
- [Chub Quick Worker 独立服务设计](CHUB_QUICK_WORKER_DESIGN.md)：任务、租约、恢复和通知终态。
- [Chub Codex Runtime 设计](CHUB_CODEX_RUNTIME_DESIGN.md)：Codex 私有认证、用量和 Native 行为。
- [Chub 集成能力清单](CHUB_INTEGRATION_CAPABILITIES.md)：当前可调用能力与微信用户可见契约。
