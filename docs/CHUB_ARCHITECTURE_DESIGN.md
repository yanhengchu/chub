# Chub 总体架构设计

> 状态：持续维护
> 主要读者：AI Agent、实现和排障 Agent；维护人员用于确认系统分层、状态边界和验收范围。
> 本文负责：Chub 三层架构、进程边界、依赖方向、状态所有权、核心调用链和跨模块约束。
> 本文不负责：专项功能的完整操作契约、固定指令语法、Runtime ZIP 协议与模块维护流程、插件实现或部署步骤；这些内容以对应专项文档为准。

本文与 [README](../README.md) 是理解项目的首要入口。README 说明产品、当前能力和使用方式；本文定义所有专项设计必须遵循的三层职责、依赖和状态边界。当前可调用能力以[集成能力清单](CHUB_INTEGRATION_CAPABILITIES.md)为准；目标分层不能将尚未实现的能力写成当前可用能力。

## 1. 系统定位与三层架构

Chub 是个人设备上的本地优先 AI 工作站控制面。它组织可信入口、设备能力、AI Session、任务、自动化、通知和最终状态；它不是模型，也不作为通用对话 Agent 执行任务。

Chub 按维护者授信的个人工作站运行，优先保证本地可用、局部恢复和最终状态可确认，不按多用户平台或零信任插件市场设计。此定位不放宽外部入口的安全边界：微信、OpenClaw 和远程浏览器仍只能通过已确认的身份、固定路由和受控用例访问 Chub，不能获得任意命令、路径、设备控制或敏感数据。

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
- 固定自动化的配置、环境、锁、Runner、Debug Chrome 管理和受限产物。
- 对 AI Runtime 和第三方服务提供固定的配置、安全、日志、通知、维护和状态查询契约。

核心层不拥有 AI Session、AI 任务、模型选择、Agent writer 或第三方通道状态。入口适配器可以把已校验的请求交给对应层的公开用例，但不得在核心层复制 AI 或第三方业务规则。

通知是核心层提供的有界投递能力；某项通知是否代表业务完成，始终由发起它的领域决定。固定自动化默认属于核心层；自动化需要 AI 处理内容时，只能调用 AI Runtime 的公开任务用例，不能自行启动或管理 Agent。

### 2.2 AI Runtime 层

AI Runtime 层提供 Chub 的 AI 能力。当前完整接入的 Runtime 是 Codex；新增 Runtime 必须通过稳定 Runtime 契约接入，不能把具体 Agent 逻辑泄漏到核心层或第三方服务层。

它负责：

- Runtime 注册、能力目录、健康状态和由后端固定的提交门禁。
- AI Session、任务提交、幂等、租约、超时、取消、恢复和最终状态。
- Quick Worker、固定 Runner、Runtime Adapter 和本机 AI Agent 调用。
- 模型、推理等级、AI 用量及 AI 任务相关的业务终态。

当前部署只启用一个后端固定注册的 Runtime，现有实例为 Codex；客户端不能选择 Runtime。设置页可启用或停用已注册 Runtime。停用只拒绝新的 AI 任务受理，不取消、迁移或重放已受理任务，也不改变 Worker 服务健康。Runtime 缺失、不健康或被停用时，新任务必须失败关闭，不自动降级到其他 Runtime；查看、停止、归档等已有 Session 维护能力按各自契约保持可用。多个 Runtime 的选择、聚合和切换语义由 Runtime 专项设计在真实接入前单独定义。

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
| `app/ai_runtime/`、`app/ai_session/`、`app/codex/`、`app/quick_worker*.py`、`app/ai_usage/`、`runtime-modules/` | AI Runtime 层 | Runtime 契约、ZIP 模块、Session、Worker、Runner，以及由 Runtime 归属的 AI 用量与专属设置 |
| `integrations/openclaw/chub/`、OpenClaw/微信适配协调 | 第三方服务层 | 插件、通道、绑定、固定路由和第三方协议 |
| `app/services/` | 过渡区 | 已有跨领域协调；新增逻辑不得以此作为新的通用领域，应按三层归属落位 |

当前部分入口和协调代码仍跨越历史目录边界。这不改变本文件的依赖规则：新增或重构时优先抽取最小公开用例，调用方不能直接操作其他层私有状态。

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
| 系统升级 | 核心层；System Upgrade Coordinator + 持久化操作状态 | 仅协调 Chub 核心与 AI Runtime 的固定升级范围、阶段和最终验证 |
| OpenClaw Gateway 重启与恢复 | 第三方服务层；OpenClaw Manager + 固定插件/补丁清单 | 确认 Gateway、通道和兼容基线 |

`data/shared/` 仅保存明确允许同步的 Chub 共享资料；当前需求储备的权威文件为 `data/shared/chub/requests.json`，其状态所有者是 Chub 需求储备服务，而不是 OpenClaw。`data/local/state/`、`data/local/runtime/` 和 `data/local/artifacts/` 保存本机运行态、锁、缓存和产物，默认不进入 Git。OpenClaw、微信和 CLI 都是访问入口，不能拥有或替换共享资料或其他领域的私有状态；共享资料出现未合并冲突、非法格式或同步状态无法确认时必须失败关闭，Chub 不自动执行 Git 同步。所有持久状态必须限制大小、权限、格式和恢复边界。

## 6. 核心调用链

### 6.1 AI 任务

```text
核心/第三方入口完成认证与业务校验
  -> AI Runtime 选择 Session 与提交门禁
  -> Worker 幂等提交并原子获取租约
  -> 固定 Runtime Runner 执行
  -> Worker 写入任务终态并释放租约
  -> 核心层页面展示，或第三方层按保存路由回送
```

### 6.2 固定自动化与通知

```text
受保护入口 -> 核心层固定自动化配置与环境校验 -> 跨进程锁
           -> Runner -> Debug Chrome / 固定扩展 -> 受限状态与产物

业务终态 -> 核心层预配置通知目标 -> 有界文本投递 -> 投递终态回写业务记录
```

自动化只执行固定任务；通知投递成功不替代主业务成功。自动化需要 AI 时，从 Runner 的明确步骤调用 AI Runtime 公开用例，任务本身仍由 AI Runtime 维护终态。

### 6.3 外置能力维护

核心层只提供受保护的外置能力维护入口，并执行固定的认证、操作记录和最终状态展示；它不解释 Runtime 模块协议或任务编排策略。Runtime 模块的安装、替换、移除、注册确认和状态清理由[Chub AI Runtime 外置模块功能设计](CHUB_EXTERNAL_MODULE_DESIGN.md)定义；任务编排外置的受控计划、版本快照和后续接入边界由[Chub 任务编排外置设计](CHUB_TASK_ORCHESTRATION_EXTERNALIZATION_DESIGN.md)定义。

## 7. 维护与恢复边界

| 操作 | 直接影响 | 成功条件 | 不影响 |
| --- | --- | --- | --- |
| Web 启动、停止与重启 | 核心层 Chub | 启动/重启以 Web 健康确认，重启还须确认新实例 ID；停止以服务管理器停止确认 | Quick Worker、OpenClaw Gateway、原生 Codex、已受理任务 |
| Worker 重启 | AI Runtime 层 Quick Worker 任务、租约和运行映射 | 新 generation、协议和健康确认 | Chub、OpenClaw Gateway、原生 Codex |
| 升级与恢复 | Chub 自有 AI 运行态、Chub Web 与 Quick Worker | Web 新实例、Worker、目标协议、Session 映射和写入恢复确认；AI Runtime 在完成后独立展示可用性 | 原生 Codex、用户配置、日志、项目资料、OpenClaw、Debug Chrome 与无关服务 |
| OpenClaw Gateway 重启与恢复 | 第三方服务层 Gateway、微信通道与固定运行产物 | Gateway、已配置通道和兼容基线确认 | 核心层、AI Runtime |

门禁只覆盖直接冲突或数据破坏风险，按资源局部生效。升级与恢复只处理 Chub 自有 AI 运行态、Web 与 Quick Worker；不扩展到原生 Runtime 数据、用户配置、日志、项目资料、OpenClaw、Debug Chrome 或无关服务。每项维护操作必须按表中的最终状态确认，不能以受理、进程创建或 HTTP 成功替代业务完成。

升级执行、Worker 恢复、Runtime 状态、OpenClaw Gateway 维护和自动化浏览器维护分别由对应专项文档定义。总体架构不维护具体清理步骤、页面状态文案、操作日志字段或某次实机验证结论。

## 8. 跨层不可违反约束

- 受保护接口只接受真实 loopback，或配置允许时的真实 Tailnet socket；不信任客户端转发 Header。
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
