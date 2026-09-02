# Chub 总体架构设计

> 状态：持续维护。
> 主要读者：AI Agent、实现和排障 Agent；维护人员用于确认系统分层、状态边界和验收范围。
> 本文负责：Chub 三层架构、进程边界、依赖方向、状态所有权、核心调用链和跨模块约束。
> 本文不负责：专项功能的完整操作契约、固定指令语法、插件实现或部署步骤；这些内容以对应专项文档为准。

本文与 [README](../README.md) 是理解项目的首要入口。README 说明产品、当前能力和使用方式；本文定义所有专项设计必须遵循的三层职责、依赖和状态边界。当前可调用能力以[集成能力清单](CHUB_INTEGRATION_CAPABILITIES.md)为准；目标分层不能将尚未实现的能力写成当前可用能力。

## 1. 系统定位与三层架构

Chub 是个人设备上的本地优先 AI 工作站控制面。它组织可信入口、设备能力、AI Session、任务、自动化、通知和最终状态；它不是模型，也不作为通用对话 Agent 执行任务。

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
- Quick Worker、固定 Runner、Runtime Adapter、实时终端载体和本机 AI Agent 调用。
- 模型、推理等级、AI 用量及 AI 任务相关的业务终态。

当前生产只注册 `codex`，客户端不能选择 Runtime，也尚未提供暂停 Runtime 提交的设置。未来若实现 Runtime 选择或暂停新任务，必须按独立需求定义其配置、受理门禁和已受理任务的收敛语义。Runtime 缺失或不健康时，新任务必须失败关闭，不自动降级到其他 Runtime。已受理且拥有任务 ID 的任务由其权威执行器继续收敛；配置切换不取消、迁移或重放已受理任务。查看、停止、归档等已有 Session 维护能力按各自契约保持可用。

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
  |-- Unix socket --> Chub Quick Worker --> 固定 Codex Runner
  `-- ttyd ---------> 固定 tmux ----> Codex 实时终端

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
| `app/ai_runtime/`、`app/ai_session/`、`app/codex/`、`app/quick_worker*.py`、`app/ai_usage/` | AI Runtime 层 | Runtime 契约、Session、Worker、Runner、终端与 AI 用量 |
| `integrations/openclaw/chub/`、OpenClaw/微信适配协调 | 第三方服务层 | 插件、通道、绑定、固定路由和第三方协议 |
| `app/services/` | 过渡区 | 已有跨领域协调；新增逻辑不得以此作为新的通用领域，应按三层归属落位 |

当前部分入口和协调代码仍跨越历史目录边界。这不改变本文件的依赖规则：新增或重构时优先抽取最小公开用例，调用方不能直接操作其他层私有状态。

## 5. 领域与状态所有权

每类状态只有一个权威来源。页面、Webhook、进程创建、HTTP 200、任务受理和 Tool Call 都不能单独代表最终成功。领域之间只交换稳定标识和公开模型，不直接修改对方私有状态文件。

| 状态或资源 | 权威层与来源 | 其他层的使用方式 |
| --- | --- | --- |
| 节点、平台服务、配置、维护操作、自动化任务与产物 | 核心层；操作系统、受控配置、Automation Store 与锁 | 聚合展示或调用固定维护用例 |
| Chub AI Session 元数据、后台任务、租约、Runtime 健康与用量 | AI Runtime 层；Session Manager、Quick Worker、Runtime Adapter | 核心与第三方只使用公开 ID、投影和任务用例 |
| 原生 Codex Session 与 writer | 本机 Codex 状态、Runtime Adapter | Chub 仅保存已校验映射，不猜测或接管 writer |
| 实时终端桥与载体 | AI Runtime 层的 Interactive Supervisor、`ttyd`、tmux | Web 重启后重建桥并复用原 tmux |
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

## 7. 维护与恢复边界

| 操作 | 直接影响 | 成功条件 | 不影响 |
| --- | --- | --- | --- |
| Web 重启 | 核心层 Chub、可重建的 ttyd Web 桥 | 新实例 ID 变化且健康 | Quick Worker、OpenClaw Gateway、tmux、原生 Codex、已受理任务 |
| Worker 重启 | AI Runtime 层 Quick Worker 任务、租约和运行映射 | 新 generation、协议和健康确认 | Chub、OpenClaw Gateway、tmux、原生 Codex |
| 系统升级 | 受影响的 AI Runtime 写入、Worker 与 Chub 自有运行态 | Web/Worker、目标版本、Session 映射和必要通知确认 | 原生 Codex、用户配置、项目资料、OpenClaw 与无关服务 |
| OpenClaw Gateway 重启与恢复 | 第三方服务层 Gateway、微信通道与固定运行产物 | Gateway、已配置通道和兼容基线确认 | 核心层、AI Runtime、实时终端 |

门禁只覆盖直接冲突或数据破坏风险，按资源局部生效。“升级与恢复”由独立于 Chub 的核心层平台执行器编排：Ubuntu 使用 systemd user oneshot service，macOS 使用独立 LaunchAgent；Chub 只持久化操作并启动执行器，不能持有随后会停止 Chub 的升级进程。执行器只停止直接受影响的 Chub 与 Quick Worker，按固定升级计划处理必要的 Chub 自有运行态，然后恢复并确认 Web、Worker、目标协议和写入可用。它不调用 OpenClaw CLI，也不处理 Gateway、插件、补丁或消息通道；这些第三方服务只通过其独立的恢复入口维护。升级计划无法读取或校验时仍按固定规则执行当前版本运行态恢复，并明确不升级代码版本。执行器不接受任意命令、路径、版本或清理目标，也不删除原生 Codex 数据、用户配置或业务资料。

若升级在最终验证阶段失败，但持久化组件结果已确认新 Chub Web 与 Quick Worker 均成功，升级记录仍保持失败并允许后续复检或恢复；它不再单独阻断 AI Runtime 新写入。后续提交仍须通过各自的 Runtime 和 Worker 实时健康检查。该失败记录尚未收敛前，不能跳过它发起新的升级，必须继续当前恢复操作；缺少任一成功结果、组件报告无法安全读取，或失败发生在更早的清理/服务切换阶段时，写入继续失败关闭。

## 8. 跨层不可违反约束

- 受保护接口只接受真实 loopback，或配置允许时的真实 Tailnet socket；不信任客户端转发 Header。
- 客户端不能提供任意文件路径、系统命令、Runtime、Session 原生 ID、收件人、版本或恢复目标。
- 第三方服务和入口适配器只能调用公开用例，不能直接修改核心层或 AI Runtime 层私有状态。
- 同一逻辑 Session 同时只有一个 writer；Chub 不接管其他应用占用的原生 Session。
- 异步操作必须记录并确认 `requested`、`started`、`succeeded` 或 `failed` 的业务终态；受理和启动不是成功。
- 外部身份、路由、协议、占用状态或最终结果无法确认时，相关高风险操作失败关闭；无关只读能力和独立服务继续可用。
- 配置、Token、终端票据和其他秘密不得进入页面、日志、通知、测试输出或示例配置。
- macOS LaunchAgent 与 Ubuntu systemd user service 都是支持目标；未实机验证的平台不能宣称已验证。

## 9. 专项文档入口与复检

| 需要确认的内容 | 权威文档 |
| --- | --- |
| 当前命令、插件、固定 API 与微信用户可见契约 | [集成能力清单](CHUB_INTEGRATION_CAPABILITIES.md) |
| Runtime、Adapter、Runner 与能力矩阵 | [AI Runtime 架构设计](CHUB_AI_RUNTIME_DESIGN.md) |
| Session、Activity、usage 与单 writer 语义 | [AI Session 状态模型](AI_SESSION_STATE_DESIGN.md) |
| Worker 任务、恢复、通知与重启协调 | [Quick Worker 独立服务设计](CHUB_QUICK_WORKER_DESIGN.md) |
| OpenClaw、微信身份、路由与插件协议 | [OpenClaw 定制集成设计](OPENCLAW_CUSTOMIZATION_DESIGN.md) |
| 页面分层与视觉交互 | [前端 UI 模块化设计](FRONTEND_UI_DESIGN.md) |
| 额度与自动化 | README 的专项文档索引 |

### 验收范围与复检

- 已确认：当前进程边界、状态所有权、维护操作范围，以及 Web、Quick Worker、OpenClaw 和 Debug Chrome 的独立恢复语义。
- 未验证或不承诺：本文件声明的目录收敛与公开用例尚未自动等同于代码迁移完成；新增 Runtime、第三方服务或未实际复检的平台必须按专项文档完成验证。
- 重新验收触发：修改层间依赖方向、状态权威来源、信任边界、固定协议、升级组件清单、维护操作范围，或将现有跨层协调迁移为公开用例时，必须复检受影响的最终状态、失败关闭边界和平台服务恢复。
