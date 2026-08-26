# Chub 总体架构设计

> 状态：持续维护。
> 主要读者：AI Agent、实现和排障 Agent；维护人员用于确认当前系统边界和验收范围。
> 本文负责：Chub 当前的进程边界、领域职责、状态所有权、核心调用链和跨模块约束。
> 本文不负责：专项功能的完整操作契约、固定指令语法、插件实现或部署步骤；这些内容以对应专项文档为准。

本文与 [README](../README.md) 是理解项目的首要入口。README 说明产品、当前能力和使用方式；本文说明当前进程如何协作、状态归谁拥有，以及模块间不可跨越的边界。当前可调用能力以[集成能力清单](CHUB_INTEGRATION_CAPABILITIES.md)为准。

## 1. 系统定位

Chub 是个人设备上的本地优先 AI 工作站控制面。它组织可信入口、设备能力、AI Session、任务、自动化、通知和最终状态；它不是模型，也不作为通用对话 Agent 执行任务。

当前完整接入的 Runtime 是 Codex。Chub 保持模块化单体：Web 负责控制面和业务协调，Quick Worker 负责可跨 Web 重启的后台 AI 任务；实时终端、自动化、OpenClaw 和通知各自通过固定适配边界接入。Chub 不把这些生命周期合并为通用任务队列，也不接管 OpenClaw 的普通 Agent。

## 2. 进程与外部边界

```text
维护者
  |-- Browser / Mobile Browser ----> Chub Web（页面、API、WebSocket）
  |-- chub CLI --------------------> 固定本机维护入口
  `-- WeChat ClawBot
       `-> OpenClaw + Chub Plugin --(真实 loopback)--> Chub 固定微信调度 API

Chub
  |-- Unix socket --> Chub Quick Worker --> 固定 Codex Runner
  |-- ttyd ---------> 固定 tmux ----> Codex 实时终端
  |-- Unix socket --> Chub Debug Chrome --> Debug Chrome 浏览器实例
  |-- CDP ----------> 受管 Debug Chrome / 固定扩展
  |-- OpenClaw CLI -> Gateway / 微信消息通道
  |-- HTTPS --------> 预配置飞书通知目标
  `-- 固定脚本 -----> Chub、Chub Quick Worker、Chub Debug Chrome、OpenClaw Gateway 和系统维护操作
```

面向页面和服务清单的固定名称如下；技术正文可在需要说明实现边界时补充括号内的角色：

| 固定名称 | 服务/组件边界 | 说明 |
| --- | --- | --- |
| `Chub` | `chub.service` 或 macOS Chub LaunchAgent | Chub Web 控制面 |
| `Chub Quick Worker` | `chub-quick-worker.service` 或 macOS Worker LaunchAgent | 后台任务执行面 |
| `Chub Debug Chrome` | `chub-debug-chrome.service`；macOS 沿用现有浏览器适配 | Debug Chrome Supervisor 与按需浏览器控制 |
| `OpenClaw Gateway` | 第三方 Gateway 服务 | 承载 ClawBot、微信通道和 OpenClaw 插件 |

`ClawBot` 是微信交互入口，不是独立服务名称；`Debug Chrome 浏览器实例` 是 Chub Debug Chrome 管理的按需资源，也不是独立服务。系统升级执行器是维护用 oneshot 服务，不列入常驻服务清单。

| 边界 | 当前职责 | 不拥有的内容 |
| --- | --- | --- |
| Chub | 页面、API、入口认证、业务校验、状态投影、操作日志、通知协调和 Web 重启协调 | 原生 Codex 执行、Worker 任务终态、OpenClaw 通道状态 |
| Chub Quick Worker | 页面/微信/翻译的非实时任务、幂等提交、Session 租约、Runner、超时、取消、任务持久化和终态 | 页面、身份认证、微信路由、通知目标、设备维护 |
| Chub Debug Chrome | Ubuntu 上 Debug Chrome Supervisor 的启动、停止、进程归属和 CDP 状态 | Web 生命周期、自动化任务状态、任意浏览器或任意命令 |
| 受管 Debug Chrome 实例 | Supervisor 与 Chrome Adapter 提供的实时浏览器状态 | 系统升级不自动启动实例；工作站环境按需控制，自动化只负责使用 |
| 实时终端链路 | `ttyd` Web 桥、固定 tmux 载体和原生 Codex TUI | Quick Worker 任务与租约 |
| OpenClaw 与微信 | 可信消息入口、通道上下文、固定插件路由和原路回送 | Chub 设备能力、Session 权限、任务终态 |
| 自动化 | 固定任务配置、跨进程锁、Runner、Debug Chrome 与受限产物 | 通用浏览器控制或任意任务执行 |
| 通知 | 已登记目标的有界文本投递 | 主业务任务的成功判断 |

## 3. 代码职责地图

| 路径 | 当前职责 |
| --- | --- |
| `app/api/`、`app/web/` | FastAPI 页面与接口、WebSocket、前端展示 |
| `app/ai_session/`、`app/codex/` | 逻辑 Session、实时终端、Codex 映射与入口 |
| `app/ai_runtime/`、`app/quick_worker*.py` | Runtime 契约、固定 Runner、Quick Worker 服务和本机协议 |
| `app/automations/` | 自动化配置、环境、锁、Runner 与产物 |
| `app/notifications/`、`app/requests/`、`app/ai_usage/` | 通知投递、R1–R9 需求储备、额度用量 |
| `app/core/`、`app/tasks/` | 配置、安全、日志、平台检测和白名单维护任务 |
| `app/services/` | 已存在的跨领域协调与可复用服务；不是独立领域或通用基础设施 |
| `integrations/openclaw/chub/` | Chub OpenClaw 插件源码、构建产物、测试与部署资料 |
| `scripts/`、`config/` | 受控服务维护、本机配置与配置示例 |

## 4. 领域与状态所有权

每类状态只有一个权威来源。页面、Webhook、进程创建、HTTP 200、任务受理和 Tool Call 都不能单独代表最终成功。

| 状态或资源 | 权威来源 | 其他模块的使用方式 |
| --- | --- | --- |
| 节点与服务健康 | 操作系统、进程和健康探测 | Web 聚合展示，不缓存为永久真相 |
| Chub AI Session 元数据 | AI Session Manager / Store | 页面、微信和 Worker 使用 Chub Session ID 关联 |
| 原生 Codex Session 与 writer | Codex 本地状态、Runtime Adapter | Chub 仅保存已校验映射，不猜测或接管 writer |
| 后台任务与租约 | Chub Quick Worker | Chub 恢复投影并确认通知 |
| 实时终端桥与载体 | Interactive Supervisor、`ttyd`、tmux | Web 重启后重建 Web 桥并复用原 tmux |
| 微信绑定与通道 | OpenClaw；Chub 保存受控路由快照 | 每次提交校验，路由异常时不回退 |
| 自动化任务与产物 | Automation Store、锁和 artifacts | 页面只读取受限状态和固定产物 |
| 通知业务状态 | 发起通知的业务领域 | Notification Service 仅回写投递终态 |
| Web 重启协调 | Deferred Restart State + 新实例健康 | 新实例 ID 变化且健康后才成功 |
| Worker 重启 | Worker maintenance operation state | 确认新的 generation、协议和健康 |
| 系统恢复 | System Upgrade Coordinator + 持久化操作状态 | 记录固定范围、阶段与最终验证 |
| OpenClaw Gateway 重启与恢复 | OpenClaw Manager + 固定插件/补丁清单 | 确认 Gateway、通道和运行产物 |
| 配置 | 环境变量、受控 YAML 和私有状态 | 客户端只能修改明确开放的设置 |

### 4.1 共享资料与本机运行态

`data/` 必须区分可通过仓库同步的共享资料和不可提交的本机运行态：

- `data/shared/` 只保存明确允许进入 Git 的 Chub 共享资料；当前需求储备权威文件为 `data/shared/chub/requests.json`，其状态所有者是 Chub 需求储备服务，不是 OpenClaw。
- `data/local/state/`、`data/local/runtime/` 和 `data/local/artifacts/` 保存本机配置外的运行状态、锁、缓存、临时文件或产物，默认不进入 Git。
- OpenClaw、微信和 CLI 都是访问入口，不能直接拥有或替换共享需求文件。
- Chub 不自动执行 Git `pull`、`commit` 或 `push`。共享文件未合并、格式非法或同步状态无法确认时，需求读写失败关闭，不覆盖其他设备内容。

共享需求不保存 Token、Cookie、账号凭证、本机路径秘密或其他不适合进入 Git 历史的内容。变更共享资料路径或所有权时，必须同步 README、集成能力清单、配置示例、迁移说明和相关测试。

所有持久状态都必须有大小、权限、格式和恢复边界。领域之间只交换稳定标识和公开模型，不直接修改对方私有状态文件。

## 5. 核心调用链

### 5.1 页面与微信后台任务

```text
入口认证与业务校验
  -> 选择 Chub Session
  -> Worker 幂等提交并原子获取租约
  -> 固定 Runtime Runner 执行
  -> Worker 写入任务终态并释放租约
  -> Web 恢复投影，页面展示或按保存路由通知
```

微信设备能力固定经过“微信 ClawBot → OpenClaw → Chub → OpenClaw → 微信 ClawBot”。Chub 不通过 OpenClaw Agent 执行设备能力；异步结果仅按任务保存的路由回送。

### 5.2 实时终端

```text
页面进入 terminal Session
  -> Chub 校验 Session 与 writer
  -> Interactive Supervisor 建立或复用 ttyd Web 桥
  -> ttyd 连接固定 tmux
  -> tmux 承载原生 Codex TUI
```

`ttyd` 可重建，tmux 和原生 Codex 是实时交互载体。普通 Chub 重启不会停止 Chub Quick Worker、tmux、原生 Codex 或已受理后台任务。

### 5.3 自动化与通知

```text
受保护入口 -> 固定自动化配置与环境校验 -> 跨进程锁
           -> Runner -> Debug Chrome / 固定扩展 -> 受限状态与产物

业务终态 -> 预配置通知目标 -> 有界文本投递 -> 投递终态回写业务记录
```

自动化只执行固定任务；通知投递成功不替代主业务成功。

Ubuntu 的 Chub Debug Chrome 由独立用户服务持有。Chub 通过固定本机 Unix socket 请求 `status`、`start` 和 `stop`；
Web 重启不停止该 Supervisor 或其受管浏览器。Supervisor 不可用、协议异常或浏览器最终状态无法确认时，
自动化浏览器操作失败关闭，不在 Web 内回退启动。macOS 继续沿用现有 LaunchAgent/浏览器适配路径。

## 6. 维护操作边界

| 操作 | 直接影响 | 成功条件 | 不影响 |
| --- | --- | --- | --- |
| Web 重启 | Chub、可重建的 ttyd Web 桥 | 新实例 ID 变化且健康 | Chub Quick Worker、OpenClaw Gateway、tmux、原生 Codex、已受理任务 |
| Worker 重启 | Chub Quick Worker 任务、租约和运行映射 | 新 generation、协议和健康确认 | Chub、OpenClaw Gateway、tmux、原生 Codex |
| 系统升级与运行态恢复 | 受影响的 AI Runtime 写入、Worker 与 Chub 自有运行态 | Web/Worker、目标版本或当前恢复目标、Session 映射和必要通知确认 | Codex 原生 Session、用户配置、日志、项目资料和无关服务 |
| OpenClaw Gateway 重启与恢复 | OpenClaw Gateway、微信通道、固定插件/补丁运行副本 | Gateway、已配置通道和兼容基线确认 | Chub、Chub Quick Worker、实时终端 |

门禁只覆盖直接冲突或数据破坏风险，按资源局部生效。恢复操作不因目标服务当前不健康、任务繁忙或状态未知而失去固定恢复入口；执行层无法安全尝试或最终结果无法确认时才失败关闭，并保留原因与恢复路径。

系统升级与运行态恢复由独立于 Chub 的平台服务执行器统一编排：Ubuntu 使用独立的 systemd user oneshot service，macOS 使用独立的 LaunchAgent；Chub 只持久化操作并启动执行器，不能直接持有随后会停止 Chub 的升级进程。执行器先停止直接受影响的 Chub、Chub Quick Worker 和 Chub Debug Chrome，再清理 Chub 自有运行态，处理当前 `.venv` 的固定依赖清单，重建并启用服务定义，按 Chub Quick Worker、Chub Debug Chrome、Chub 顺序恢复，并调用现有 OpenClaw Gateway 固定插件/补丁同步入口。Debug Chrome 浏览器实例不属于升级启动目标，流程记录为 `skipped`，明确不会自动启动；其未启动状态不构成升级失败。每个组件结果持久化到受限状态文件，并由升级执行器日志记录固定的 operation ID、组件名和状态。重建服务定义时不得停止或删除当前升级执行器；执行器失败必须持久化失败状态并恢复核心服务。Chub、依赖、服务定义和 Chub Quick Worker 属于核心组件；Chub Debug Chrome 或 OpenClaw Gateway 失败只记录 `degraded`，不阻止核心服务达到可用状态。该流程不调用带 Worker 忙闲门禁的完整 `chub install`，也不执行 Git 或任意路径/命令。首次安装、移动项目目录或服务定义变化后，必须先从本机终端执行完整 `chub install`；若已有未完成操作，先使用 `chub system-upgrade-service` 恢复，不能用 `chub install` 中断当前恢复。

## 7. 版本与运行态边界

| 类型 | 权威来源 | 当前规则 |
| --- | --- | --- |
| Web 代码版本 | `app/core/build_info.py` 的 `WEB_CODE_VERSION` | 受控维护操作确认新 Web 实例匹配目标版本 |
| Session 数据版本 | `SESSION_SCHEMA_VERSION` | Session 持久化语义变化时更新；不再适用的 Chub 自有运行态按固定边界清理 |
| Worker IPC 协议 | `app/quick_worker.py` 的 `PROTOCOL_VERSION` | Web 与 Worker 请求、响应或语义变化时同步协议、测试和运行产物 |
| 系统升级方案契约 | `SYSTEM_UPGRADE_CONTRACT_VERSION` | 只约束受控升级/恢复方案格式 |

版本和协议字段只能由后端固定逻辑使用；客户端不能提供版本、路径、命令或清理目标。系统恢复只处理固定白名单内的 Chub 自有运行态，不删除原生 Codex 数据、用户配置或业务数据。

## 8. 跨模块不可违反约束

- 受保护接口只接受真实 loopback，或配置允许时的真实 Tailnet socket；不信任客户端转发 Header。
- 客户端不能提供任意文件路径、系统命令、Runtime、Session 原生 ID、收件人、版本或恢复目标。
- 外部身份、路由、协议、占用状态或最终结果无法确认时，相关高风险操作失败关闭；无关只读能力和独立服务继续可用。
- 同一逻辑 Session 同时只有一个 writer；Chub 不接管其他应用占用的原生 Session。
- 异步操作必须记录并确认 `requested`、`started`、`succeeded` 或 `failed` 的业务终态；受理和启动不是成功。
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
| 额度、自动化、分发与发布 | README 的专项文档索引 |

已核对当前进程边界、状态所有权、维护操作范围和专项文档入口。自动化回归覆盖核心服务定义修复、依赖处理、Worker/Supervisor/Web 顺序、浏览器实例 `skipped` 语义、独立组件降级记录和旧 Worker 终态清理；本轮已在 Ubuntu 本机实际确认两次升级与恢复的服务切换、Web/Worker/Supervisor 健康、OpenClaw Gateway 健康和 Quick Worker 最终状态。未在本轮实机验证 macOS、真实 Python 依赖安装或真实 OpenClaw 插件内容变更；这些范围不能由本轮 Ubuntu 结果代替。变更进程职责、状态权威来源、信任边界、固定协议、升级组件清单、维护操作范围或用户可见能力时，必须同步复检本文和受影响的专项契约；本文件不替代对应功能的实现与实机验收。
