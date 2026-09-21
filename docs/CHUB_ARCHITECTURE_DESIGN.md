# Chub 总体架构设计

> 状态：持续维护
> 主要读者：AI Agent；维护者通过与 AI Agent 协作，理解并确认本文规则。
> 本文负责：Chub 三层架构、进程边界、依赖方向、状态所有权、核心调用链和跨模块约束。
> 本文不负责：专项功能的完整操作契约、固定指令语法、Runtime 插件 ZIP 协议与模块维护流程、插件实现或部署步骤；这些内容以对应专项文档为准。

本文与 [README](../README.md) 是理解项目的首要入口。README 说明产品、当前能力和使用方式；本文定义所有专项设计必须遵循的三层职责、依赖和状态边界。当前可调用能力以[集成能力清单](CHUB_INTEGRATION_CAPABILITIES.md)为准；目标分层不能将尚未实现的能力写成当前可用能力。

## 1. 系统定位与三层架构

Chub 是个人设备上的本地优先 AI 工作站控制面。它组织可信入口、设备能力、AI Session、任务、自动化、通知和最终状态；它不是模型，也不作为通用对话 Agent 执行任务。

Chub 按维护者授信的个人工作站运行，优先保证本地可用、局部恢复和最终状态可确认，不按多用户平台或零信任插件市场设计。此定位不放宽外部入口的安全边界：微信、OpenClaw 和远程浏览器仍只能通过已确认的身份、固定路由和受控用例访问 Chub，不能获得任意命令、路径、设备控制或敏感数据。

Chub 同时是插件模块的本机宿主：核心提供工作台壳、可信入口、统一生命周期和最终状态确认；插件模块提供自身的业务页面、设置、数据与流程。统一层管理配置校验后的导入、移除、启用和禁用状态：导入记录是首页状态和设置侧栏插件菜单的唯一可见性来源，移除后两者同时隐藏；启用记录决定模块业务写入是否可用。插件自身负责加载、运行态确认、回滚与业务扩展动作。模块不是第四个业务层，也不能反向成为核心依赖。Deliveryline 当前确认的是插件生命周期、设置页插件状态和首页入口；仓库中原有的交付线创建与澄清流程属于待重构旧原型，不作为架构层的稳定业务能力。

Chub 按以下三层组织。分层首先约束职责与代码依赖，不要求立即把每层拆成独立进程；当前仍是模块化单体，Web、Quick Worker 和外部 Gateway 保持各自的现有进程边界。

```text
Browser / chub CLI / 固定自动化 ─────────────────────────> Chub 核心层
微信 ClawBot -> OpenClaw（第三方服务层） ────────────────> Chub 核心层
                                               └─（需要 AI）-> AI Runtime 层 -> Chub 核心层

AI Runtime 层 ──────────────────────────────────────────> Chub 核心层
```

| 层 | 定位 | 可以依赖 | 不得依赖 |
| --- | --- | --- | --- |
| Chub 核心层 | 最小可运行的平台、控制面与受控维护能力 | 标准库、固定平台/基础适配 | AI Runtime 实现、OpenClaw 或其他具体第三方服务 |
| AI Runtime 层 | AI 会话、任务与本机 AI Agent 执行能力 | Chub 核心层的公开能力 | 具体第三方服务 |
| 第三方服务层 | 外部服务、通道和协议适配 | Chub 核心层；需要 AI 时可调用 AI Runtime 公开能力 | 其他第三方的内部实现、Core/Runtime 私有状态 |

因此，AI Runtime 与第三方服务都依赖核心层；第三方服务只有在需要提交或读取 AI 任务时才依赖 AI Runtime。核心层不反向导入或假设上层存在，AI Runtime 也不反向依赖 OpenClaw 等具体集成。

`app/application.py`、Web/API 路由注册和服务启动属于部署组合根：它们可以注册可选层的公开契约并完成依赖注入，但不拥有上层业务规则、不读取上层私有状态，也不得在核心业务代码中直接导入具体 Runtime 或第三方实现。组合根不是第四个业务层。

核心层必须能在没有 AI Runtime、没有 OpenClaw 的情况下启动并提供配置、CLI、设备维护、固定自动化、项目资料和只读状态。AI Runtime 或第三方服务不可用时，只有其直接能力失败关闭；无关的核心能力和独立服务继续可用。

## 2. 各层职责与公开边界

### 2.1 Chub 核心层

核心层负责系统最小可运行能力和跨层安全边界：

- CLI、配置、安全校验、日志、操作记录、通知基础设施和受控状态读取。
- Web/API 的通用入口、认证、页面壳、设备状态、项目资料和需求储备。
- macOS LaunchAgent、Ubuntu systemd user service、固定白名单脚本、系统升级与恢复。
- Debug Chrome 基础能力、固定自动化的配置、环境、锁、Runner 和受限产物。
- 对 AI Runtime 和第三方服务提供固定的配置、安全、日志、通知、维护和状态查询契约。
- 模块的发现、安装、启停、版本、局部恢复和能力调用边界；核心只提供宿主能力，不拥有业务模块的业务状态与流程。

核心层不拥有 AI Session、AI 任务、模型选择、Agent writer 或第三方通道状态。入口适配器可以把已校验的请求交给对应层的公开用例，但不得在核心层复制 AI 或第三方业务规则。

通知是核心层提供的有界投递能力；某项通知是否代表业务完成，始终由发起它的领域决定。固定自动化默认属于核心层；自动化需要 AI 处理内容时，只能调用 AI Runtime 的公开任务用例，不能自行启动或管理 Agent。

### 2.1.1 插件模块边界

插件模块分为 Runtime、任务编排和工作台业务模块三类。它们使用各自的协议、安装目录、注册表和恢复规则，不能互相借用模块类型或扩大为通用插件市场。内置开发源码统一位于 `modules/`，由 `modules/chub-modules.json` 显式登记；平级 `chub-local-modules/` 是同构的本机模块源，必须使用自己的显式索引，且不属于 Chub 部署、升级或恢复边界。扫描始终先内置、后本机；本机同 ID 项直接忽略。Runtime 模块和任务编排统一分发边界已实现，但当前没有启用非空阶段插件；业务模块的首个固定开发实现是 Deliveryline，源码根目录为 `modules/business/deliveryline/`，其领域模型、存储、协作、业务 API 和模块页面资源均随模块源码维护。当前业务模块宿主管理其受控实现、导入和启用状态，并提供首页入口；交付线创建、整体澄清、交付项和 Chub 执行关联仍处于产品重构范围。

`modules/` 只保存受版本控制的开发模块，不保存已安装 ZIP、任务、缓存或其他运行态。`chub-modules.json` 是内置目录的发现索引；`runtime/` 放实现 Adapter 与 Worker Runner 的 AI Runtime，`orchestration/` 为未来受限任务编排阶段实现预留，`business/` 放工作台业务模块的开发制品。每个类型目录可以包含多个模块，实际加载身份始终以索引和各模块 Manifest 为准。平级 `chub-local-modules/` 采用相同的类型目录布局，但只承载当前设备维护者自行登记的代码。

模块可在 Chub 工作台中拥有固定分区和模块专属设置。核心不因模块维护、普通配置变化、单项故障或状态暂时未知增加全局门禁；能由模块或执行层安全尝试的操作应先尝试并按最终结果收敛。只有会造成直接数据破坏、安全越界或不可恢复冲突的操作才需拒绝。模块升级、停用或故障默认只影响后续新业务请求；已受理业务记录按其创建时快照和专项恢复规则收敛。移除模块时，只能处理该模块声明的模块专属数据，不能清理 Chub 通用设置、其他模块、Session、Worker 任务或第三方原生数据。

### 2.1.2 插件宿主通用能力

本节是所有插件模块使用 Chub 宿主能力的唯一公共依据。Runtime、任务编排和工作台业务模块都复用这些能力；各专项文档只说明本模块使用哪些能力、增加哪些专属限制，不重复定义发现、生命周期、页面承载、能力调用和恢复规则。

| 通用能力 | Chub 宿主负责 | 模块负责 |
| --- | --- | --- |
| 发现与预检 | 按受控索引发现候选，校验模块类型、协议、Chub 版本、Manifest、依赖和入口；单个候选失败时隔离该候选 | 提供本模块 Manifest、入口、版本和类型专属字段 |
| 导入与生命周期 | 提供导入、移除、启用、禁用、状态查询、操作记录和最终状态确认；导入不自动启用，生命周期变化按模块范围生效 | 遵守对应模块协议，完成模块入口加载、模块状态检查和类型专属恢复 |
| 工作台与设置承载 | 提供统一页面壳、导航分区、设置容器、可信入口和模块可见性投影；模块未导入、未启用或不可用时按状态隐藏或限制入口 | 提供业务页面内容、设置项和注册信息；不得复制宿主页面壳或自行创建平行生命周期 |
| 公共能力调用 | 通过受控的公开用例向当前模块或请求授予能力投影，校验调用方、范围、参数和最终结果 | 只能调用本次上下文获授权的能力，不得直接读取其他领域私有状态或绕过公开用例 |
| 状态隔离 | 保存模块注册、安装、启用、操作和宿主运行状态；保护其他模块、Session、Worker、通用设置和第三方数据 | 保存自身业务状态、专属设置和业务关联，并声明可清理的模块专属数据 |
| 变更与恢复 | 以模块为最小影响范围；不因单模块故障阻断无关能力；已受理记录按创建时快照和专项规则收敛 | 保存类型专属快照、检查点和恢复信息，不能改写宿主或其他模块的权威状态 |

模块协议可以缩小上述能力范围，但不能扩大为任意文件、命令、网络、入口路由、Session、Worker 或其他模块访问。模块类型的 Manifest 字段、装配方式和专属生命周期写在对应专项设计中；通用能力、状态所有权和失败隔离仍以本文为准。

当前实现仍是模块化单体。`modules/` 中的开发源码由主项目组合根按模块清单发现，并通过业务模块入口注册 API、工作台页面片段、设置页面片段、静态资源和模块状态钩子；主项目只装配通用壳与宿主能力。正式业务模块 ZIP 导入后由宿主完成清单校验、安全解压和独立安装目录记录；启用状态写入生命周期状态，下一次 Web 启动时按安装目录中的 Manifest 和入口加载模块代码。导入不自动启用，启用或停用只影响后续启动装配，不改写模块业务数据。

当前业务模块的最小装配契约是：开发源码由索引登记 `module_id` 和 `module_type`；Manifest 提供 `protocol_version`、`chub_version`、`display_name`、`version` 和 `entry`；入口返回模块定义，声明模块 API、工作台/设置模板、静态资源以及需要的状态和公共能力钩子。开发源码允许 `chub_version: dev` 或当前 Chub 版本；正式 ZIP 必须精确匹配当前 Chub 版本，协议版本必须精确匹配宿主支持版本。正式 ZIP 的清单位于包根，业务代码位于包内命名空间，`entry` 必须能从该包根加载。核心只按这份契约装配，不在 `app/application.py`、核心 Web 路由或通用前端脚本中写入具体业务模块 ID。

业务模块宿主状态统一保存在 `BusinessModulesConfig.state_file` 指向的生命周期文件；模块专属业务数据、设置和恢复目录由模块入口声明并由模块自己解析，不能新增到核心 `BusinessModulesConfig`。当前 Deliveryline 没有需要维护者调整的专属设置，因此不新增空设置项；以后确有配置时，应由 Deliveryline 模块自己的设置片段和模块专属状态承载。

### 2.1.3 能力目录与公开用例

[Chub 集成能力清单](CHUB_INTEGRATION_CAPABILITIES.md)按场景登记当前 Chub 能做什么，以及各入口能够使用哪些能力；它是产品能力的统一目录，不是新的执行层或第二份状态机。核心层与 AI Runtime 层各自以公开用例实际提供能力，仍由所属层维护权限、状态和最终结果。

能力目录中的“可用”只表示 Chub 当前具备该项产品能力，不自动授予每个调用方。页面、CLI、OpenClaw、自动化和插件模块只能调用其入口、任务范围和当前状态允许的公开用例。能力 ID 本身不是 CLI、HTTP API 或可直接导入的函数；宿主按本节规则完成授权和投影后，模块才能使用当前上下文允许的能力。文档用于说明语义，运行时注册表和每次调用的校验才是实际可用性的权威来源。

### 2.2 AI Runtime 层

AI Runtime 层提供 Chub 的 AI 能力。当前完整接入的 Runtime 是 Codex；新增 Runtime 必须通过稳定 Runtime 契约接入，不能把具体 Agent 逻辑泄漏到核心层或第三方服务层。

它负责：

- Runtime 注册、能力目录、健康状态和由后端固定的提交门禁。
- AI Session、任务提交、幂等、租约、超时、取消、恢复和最终状态。
- Quick Worker、固定 Runner、Runtime Adapter 和本机 AI Agent 调用。
- 模型、推理等级、AI 用量及 AI 任务相关的业务终态。

当前唯一接入的 Runtime 是 Codex；核心已支持通过 Runtime 契约并存加载多个 Runtime。外部调用方不能选择 Runtime；维护者只能在设置页切换后续新 Session 的默认 Runtime 或其当前实现版本。停用只拒绝对应 Runtime 的后续新任务，不取消、迁移或重放已受理任务，也不改变 Worker 服务健康。Runtime 缺失、不健康或被停用时，新任务必须失败关闭，不自动降级到其他 Runtime；查看、停止、归档等已有 Session 维护能力按其创建时固定的 Runtime 与实现契约保持可用。多 Runtime 的具体私有认证、用量、Native 数据和平台验收仍由对应专项设计定义。

### 2.3 第三方服务层

第三方服务层适配外部服务、账号、协议和通道。当前主要实现是 OpenClaw 与微信 ClawBot；以后新增消息、协作或设备服务时同样纳入本层。

它负责：

- 第三方服务配置、连接、健康、绑定、通道上下文和协议适配。
- 可信外部请求的身份与路由校验，并转换为 Chub 已定义的固定用例。
- 按保存的受控路由发送外部结果，维护第三方自身的连接和通道状态。

第三方服务不拥有 Chub Session、任务、租约、配置或运行态，不能直接读写其私有文件或内部对象。它只能调用核心层或 AI Runtime 层公开的固定用例，不得传递任意命令、路径、Runtime ID、原生 Session ID 或收件人。

微信设备能力保持固定链路：

```text
微信 ClawBot → OpenClaw → Chub 固定能力 → OpenClaw → 微信 ClawBot
```

不涉及 AI 的状态查询或固定维护指令只调用核心层；需要 AI 的普通任务调用 AI Runtime 的任务用例。Chub 不通过 OpenClaw Agent 执行设备能力，收到消息、创建 Tool Call 或任务受理均不代表最终成功。

## 3. 进程与外部边界

```text
维护者与固定入口
  |-- Browser ---------------------> Chub Web（核心层入口与页面）
  |-- chub CLI --------------------> 核心层固定本机维护入口
  `-- 固定自动化 -------------------> 核心层自动化入口

第三方服务层
  `-- 微信 ClawBot -> OpenClaw + Chub Plugin --(真实 loopback)--> Chub 第三方入口适配

AI Runtime 层
  `-- Unix socket --> Chub Quick Worker --> 固定 Runtime Runner

核心层
  |-- Unix socket --> Chub Debug Chrome --> Debug Chrome 浏览器实例
  |-- CDP ----------> 受管 Debug Chrome / 固定扩展
  |-- HTTPS --------> 预配置飞书通知目标
  `-- 固定脚本 -----> Chub、Quick Worker、Debug Chrome 与系统维护操作

```

| 固定名称 | 所属层与服务/组件边界 | 说明 |
| --- | --- | --- |
| `Chub` | 核心层；`chub.service` 或 macOS Chub LaunchAgent | Web 控制面、通用入口和跨层组合 |
| `Chub Quick Worker` | AI Runtime 层；`chub-quick-worker.service` 或 macOS Worker LaunchAgent | 后台 AI 任务执行面 |
| `Chub Debug Chrome` | 核心层；`chub-debug-chrome.service` 或 macOS 浏览器适配 | Debug Chrome Supervisor 与按需浏览器控制 |
| `OpenClaw Gateway` | 第三方服务层；第三方 Gateway 服务 | ClawBot、微信通道和 OpenClaw 插件 |

`ClawBot` 是微信交互入口，不是独立服务名称；`Debug Chrome 浏览器实例` 是 Chub Debug Chrome 管理的按需资源，也不是独立服务。系统升级执行器是核心层维护用 oneshot 服务，不列入常驻服务清单。

## 4. 当前代码职责地图

当前目录按历史职责逐步收敛到三层；目录迁移必须在公开契约与测试到位后进行，不能只为形式批量搬动文件。

| 当前路径 | 归属方向 | 当前职责 |
| --- | --- | --- |
| `app/core/`、`app/tasks/`、`app/automations/`、`app/notifications/`、`app/requests/` | 核心层 | 配置、安全、日志、维护任务、固定自动化、通知和需求储备 |
| `app/application.py`、`app/api/`、`app/web/`、`scripts/`、`config/` | 部署组合根与核心层入口 | Web、CLI、受控服务维护与配置；仅注册或调用对应层公开能力 |
| `app/ai_runtime/`、`app/ai_session/`、`app/ai_interactions/`、`app/api/ai.py`、`app/quick_worker*.py`、`app/ai_usage/`、`modules/runtime/` | AI Runtime 层 | Runtime 契约、插件 ZIP、Session、快速交互与 Worker、Runner、`/api/ai/*` 入口，以及由 Runtime 归属的 AI 用量与专属设置 |
| `modules/orchestration/`、`modules/business/` | 模块开发源码 | 分别保存任务编排和工作台业务模块的受控开发制品；模块是否可用仍由各自协议、生命周期和宿主状态决定 |
| `integrations/openclaw/chub/`、OpenClaw/微信适配协调 | 第三方服务层 | 插件、通道、绑定、固定路由和第三方协议 |
| `app/services/` | 过渡区 | 已有跨领域协调；新增逻辑不得以此作为新的通用领域，应按三层归属落位 |

当前部分入口和协调代码仍跨越历史目录边界。这不改变本文件的依赖规则：新增或重构时优先抽取最小公开用例，调用方不能直接操作其他层私有状态。

当前结构收敛以“保持既有入口契约”为前提渐进进行。微信协调入口继续作为兼容门面；Session 目录、槽位同步、可见列表和只读快照已收敛为独立协作单元；任务提交与重试已由统一任务编排分发器承担，旧文本处理编排已退役。后续新增非空阶段或拆分入口协作单元时，必须保留微信指令、回执格式、状态所有权、路由、权限和已受理任务行为。

## 5. 领域与状态所有权

每类状态只有一个权威来源。页面、Webhook、进程创建、HTTP 200、任务受理和 Tool Call 都不能单独代表最终成功。领域之间只交换稳定标识和公开模型，不直接修改对方私有状态文件。

| 状态或资源 | 权威层与来源 | 其他层的使用方式 |
| --- | --- | --- |
| 节点、平台服务、配置、维护操作、自动化任务与产物 | 核心层；操作系统、受控配置、Automation Store 与锁 | 聚合展示或调用固定维护用例 |
| Chub AI Session 元数据、后台任务、租约、Runtime 健康与用量 | AI Runtime 层；Session Manager、Quick Worker、Runtime Adapter | 核心与第三方只使用公开 ID、投影和任务用例 |
| 原生 Runtime Session 与 writer | 当前 Runtime 的原生状态、Runtime Adapter | Chub 仅保存已校验映射，不猜测或接管 writer |
| 微信绑定、通道与 Gateway 状态 | 第三方服务层；OpenClaw | 核心保存受控路由快照并在提交时校验 |
| 通知业务状态 | 发起通知的业务领域 | 核心通知能力只回写投递终态 |
| Web 重启协调 | 核心层；Deferred Restart State + 新实例健康 | 新实例 ID 变化且健康后才成功 |
| Worker 重启 | AI Runtime 层；Worker maintenance operation state | 确认新的 generation、协议和健康 |
| Chub 工作站重建 | 核心层 `WorkstationRebuildCoordinator` 的独立操作记录；该记录不在清理范围内，由固定维护执行器推进 | 依据当前代码、配置与 requirements 结束旧 Chub 任务、清理运行态，建立默认 Runtime 并确认最终健康 |
| OpenClaw Gateway 重启与恢复 | 第三方服务层；OpenClaw Manager + 固定插件/补丁清单 | 确认 Gateway、通道和兼容基线 |

`data/shared/` 仅保存明确允许同步的 Chub 共享资料；当前需求储备的权威文件为 `data/shared/chub/requests.json`，其状态所有者是 Chub 需求储备服务，而不是 OpenClaw。`data/local/state/`、`data/local/runtime/` 和 `data/local/artifacts/` 保存本机运行态、锁、缓存和产物，默认不进入 Git。正式部署包只交付固定程序和受控扩展产物，不迁移本机配置、运行态或第三方账号；首次安装与可选 OpenClaw 接入边界见[Chub 正式部署包与安装设计](CHUB_DEPLOYMENT_PACKAGE_DESIGN.md)。OpenClaw、微信和 CLI 都是访问入口，不能拥有或替换共享资料或其他领域的私有状态；共享资料出现未合并冲突、非法格式或同步状态无法确认时必须失败关闭，Chub 不自动执行 Git 同步。所有持久状态必须限制大小、权限、格式和恢复边界。

## 6. 核心调用链

### 6.1 AI 任务

```text
核心/第三方入口完成认证与业务校验
  -> Chub 任务分发点（零个或多个逻辑编排阶段）
  -> AI Runtime 选择 Session 与最终主任务提交门禁
  -> Worker 幂等提交并原子获取租约
  -> 固定 Runtime Runner 执行
  -> Worker 写入任务终态并释放租约
  -> 核心层页面展示，或第三方层按保存路由回送
```

微信/OpenClaw 是这条调用链的第三方入口和回送方，不拥有独立的 Session 创建或任务执行实现：它只能调用 Chub 已公开并在当前上下文获授权的 `chub.session.*`、`chub.task.*` 等能力；Chub 与 AI Runtime 仍分别维护 Session、任务和最终状态。能力标识、场景和当前调用方以[Chub 集成能力清单](CHUB_INTEGRATION_CAPABILITIES.md#11-核心能力)为准。

需要任务编排的外部用户任务，在入口已完成认证、幂等、固定指令处理、输入限制和目标选择后进入 AI Runtime 层的统一分发点。分发点按受理快照推进零个或多个逻辑阶段，并且是最终物理主任务的唯一提交者；空阶段链仍经此边界直接投递。阶段只能通过受限能力投影工作，并将任务文本与可信检查点交回分发点；它们不能直接操作 Worker、Runtime、原生 Session、入口路由或通知。指令解释、确认交互、页面展示和外部回送仍属于原入口适配。通用协议以[Chub 任务编排插件模块架构设计](CHUB_TASK_ORCHESTRATION_PLUGIN_DESIGN.md)为准；具体入口能力和收敛交付不在本文定义。

### 6.2 固定自动化与通知

```text
受保护入口 -> 核心层固定自动化配置与环境校验 -> 跨进程锁
           -> Runner -> Debug Chrome / 固定扩展 -> 受限状态与产物

业务终态 -> 核心层预配置通知目标 -> 有界文本投递 -> 投递终态回写业务记录
```

Debug Chrome 是核心层的受管浏览器基础能力；固定自动化、账号登录和 Runtime 页面采集通过各自受控用例调用它，不持有浏览器生命周期或 Profile 所有权。当前公开边界分为 `ChromeLifecycleUseCase`（只读状态、Profile 生命周期和固定启停）与 `ChromeSupervisorMaintenanceUseCase`（仅 `restart: bool` 的 Supervisor reconcile）。Ubuntu 的生命周期状态、启停和默认 Playwright 连接均必须经过 Supervisor Unix socket；socket 不可用时失败关闭，不能回退为 Web/Worker 进程直接读取或控制本地 Chrome。Supervisor 服务健康要求 systemd active 且 socket `status` 可响应；浏览器实例本身可以是 stopped。macOS 返回无需 Supervisor 操作的成功结果，不创建额外服务。自动化只执行固定任务；通知投递成功不替代主业务成功。自动化需要 AI 时，从 Runner 的明确步骤调用 AI Runtime 公开用例，任务本身仍由 AI Runtime 维护终态。

本机稳定命令只经 `scripts/chub` 公开。Web 页面、API 与 deferred restart 通过 `WebRestartUseCase` 请求固定 Web 重启；Quick Worker 页面、恢复和微信维护入口通过 `QuickWorkerMaintenanceUseCase` 请求固定 Worker 重启；系统升级 Coordinator 通过 `SystemUpgradeMaintenanceUseCase` 请求固定升级 oneshot 的启动或 Worker 恢复。三个公开用例只启动各自的 `scripts/maintenance/` 独立适配，不重新执行 `scripts/chub`，也不接受调用方给出的脚本、服务或平台命令。维护适配只保留 Chub 的固定流程、运行态清理与最终状态确认，全部 `launchctl`/`systemctl` 调用必须再委托给平台适配。独立进程必须执行的固定维护实现集中在 `scripts/maintenance/`，服务定义必须指向该 canonical 路径；根目录旧维护路径已移除。正式 ZIP 构建入口集中在 `scripts/build/`，根目录旧 Python 构建入口已移除。`scripts/platform/service-management.sh` 只承载已枚举的固定平台动作：核心 Web、Quick Worker 与升级 oneshot 的定义、安装前停机、升级执行器加载/启动/状态和卸载，核心 Web/Quick Worker 的启动、重启、停止和状态读取，Debug Chrome Supervisor 的定义、停止、状态和固定 reconcile，以及平台服务详情读取；它不能成为接收任意服务名、路径或子命令的通用接口。CLI 仅解析固定命令、调用这些动作，并保留 Chub 运行态清理、维护门禁与 Web/Worker 最终状态确认。失败的系统升级操作记录属于 Chub 自有运行态，不重绑到新方案或续跑；下一次确认升级会先安全清除该记录和组件报告，再从当前固定方案重新开始。外置模块的 `tools/` 不属于运行时发现、加载或调度边界。

### 6.3 插件模块维护

核心层按 2.1.2 提供插件模块的发现、导入、启停、设置承载、公开能力调用、操作记录和最终状态确认；它不解释某种模块的业务流程。Runtime 插件的 Adapter/Runner 装配与专属生命周期由[Chub AI Runtime 插件模块设计](CHUB_RUNTIME_PLUGIN_DESIGN.md)定义；任务编排阶段、检查点和版本绑定由[Chub 任务编排插件模块架构设计](CHUB_TASK_ORCHESTRATION_PLUGIN_DESIGN.md)定义；Deliveryline 的业务模型由[Deliveryline 需求交付管理平台设计](DELIVERYLINE_PLATFORM_DESIGN.md)定义。专项文档不得重新定义 2.1.2 的通用能力。

## 7. 维护与恢复边界

| 操作 | 直接影响 | 成功条件 | 不影响 |
| --- | --- | --- | --- |
| Web 启动、停止与重启 | 核心层 Chub | 启动/重启以 Web 健康确认，重启还须确认新实例 ID；停止以服务管理器停止确认 | Quick Worker、OpenClaw Gateway、原生 Codex、已受理任务 |
| Worker 重启 | AI Runtime 层 Quick Worker 任务、租约和运行映射 | 新 generation、协议和健康确认 | Chub、OpenClaw Gateway、原生 Codex |
| Chub 工作站重建 | 停止 Chub Web、Quick Worker 与受管理的 Debug Chrome Supervisor；同步项目 requirements、清理 Chub 自有运行态并建立默认 Runtime | Web、Worker、默认 Runtime 与 Supervisor 的最终健康确认 | 原生 Codex、用户配置、日志、项目资料、OpenClaw Gateway/插件/补丁、浏览器 Profile 与无关服务 |
| OpenClaw Gateway 重启与恢复 | 第三方服务层 Gateway、微信通道与固定运行产物 | Gateway、已配置通道和兼容基线确认 | 核心层、AI Runtime |

门禁只覆盖直接冲突或数据破坏风险，按资源局部生效。`chub workstation rebuild --force` 由独立执行器完成，包含 Debug Chrome Supervisor 的停止、重建和最终健康确认，同时不删除浏览器 Profile。所有操作都不扩展到原生 Runtime 数据、用户配置、日志、项目资料、OpenClaw 或无关服务。每项维护操作必须按表中的最终状态确认，不能以受理、进程创建或 HTTP 成功替代业务完成。

升级执行、Worker 恢复、Runtime 状态、OpenClaw Gateway 维护和自动化浏览器维护分别由对应专项文档定义。总体架构不维护具体清理步骤、页面状态文案、操作日志字段或某次实机验证结论。

## 8. 跨层不可违反约束

- 受保护接口只接受真实 loopback，或配置允许时的真实 Tailnet socket；不信任客户端转发 Header。
- Web 必定监听 loopback；启动时仅在发现且能绑定 Tailscale 地址时追加 Tailnet listener。Tailscale 未启动、无地址或地址失效时只保留 loopback，Web 仍是健康可用状态，并在下一次 Web 启动重新发现；这是正常的可选能力回退，不作为健康检查或工作台警告项，不得为等待 Tailnet 阻塞、失败或重启 Web。
- 客户端不能提供任意文件路径、系统命令、Runtime、Session 原生 ID、收件人、版本或恢复目标。
- 第三方服务和入口适配器只能调用公开用例，不能直接修改核心层或 AI Runtime 层私有状态。
- 同一逻辑 Session 同时只有一个 writer；Chub 不接管其他应用占用的原生 Session。
- 异步操作必须记录并确认 `requested`、`started`、`succeeded` 或 `failed` 的业务终态；受理和启动不是成功。
- 外部身份、路由、协议、占用状态或最终结果无法确认时，相关高风险操作失败关闭；无关只读能力和独立服务继续可用。
- 配置、Token、访问票据和其他秘密不得进入页面、日志、通知、测试输出或示例配置。
- macOS LaunchAgent 与 Ubuntu systemd user service 都是支持目标；未实机验证的平台不能宣称已验证。

## 9. 相关文档

完整文档导航和各文档的唯一职责由[README](../README.md#核心项目文档)维护。本文只在涉及具体边界时链接对应专项文档；当前可调用能力以[集成能力清单](CHUB_INTEGRATION_CAPABILITIES.md)为准。

### 验收范围与复检

- 已确认：当前三层依赖方向、进程边界、状态所有权和维护影响范围。
- 未验证或不承诺：本文件的目录归属不等同于已完成代码迁移；新增 Runtime、第三方服务或未实际复检的平台必须按专项文档完成验证。
- 重新验收触发：修改层间依赖方向、状态权威来源、信任边界、维护影响范围，或将现有跨层协调迁移为公开用例时，必须复检受影响的最终状态、失败关闭边界和平台服务恢复。
