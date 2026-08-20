# Chub 总体架构与演进设计

> 状态：持续维护。
> 主要读者：AI Agent、实现和排障 Agent；维护人员用于确认项目边界、状态所有权和架构验收标准。
> 本文负责：Chub 当前系统架构、进程与状态所有权、长期分层和演进原则，是各专项设计的上层约束；当前可用能力以[集成能力清单](CHUB_INTEGRATION_CAPABILITIES.md)为准，AI Runtime 演进由[Chub AI Runtime 架构设计](CHUB_AI_RUNTIME_DESIGN.md)细化。
> 本文不负责：专项功能的完整实现契约、固定指令语法、插件源码/部署步骤或目标分层的直接落地；各专项文档负责自己的权威边界，本文不表示目标分层已经全部实现，也不要求一次性重构现有模块。

本文与项目 README 共同构成核心项目文档：README 回答“Chub 是什么、如何使用”，本文回答“Chub 如何组织、各状态由谁负责、后续如何演进”。其他专项设计、能力契约和维护资料均位于其后，并不得与这两份核心文档冲突。

## 1. 定位与总体结论

Chub 是面向个人设备、本地优先的轻量 AI 工作站控制面。它统一管理可信入口、设备能力、业务规则、AI Session、需求、自动化、状态、通知和用户可见终态；具体任务由受控执行器或外部 Runtime 完成。

当前和长期架构均遵循以下结论：

1. Chub 保持本地优先的模块化单体，不拆分为通用微服务平台。
2. Web 是控制面与业务协调入口，不承载必须跨 Web 重启继续的后台任务进程。
3. Quick Worker 是独立的可靠 AI 后台执行进程；其他执行机制按真实业务边界独立维护，不为形式统一强行接入 Worker。
4. OpenClaw、Codex、Debug Chrome、飞书和操作系统服务均属于 Chub 边界外的运行时或集成对象，由固定 Adapter/Manager 受控调用。
5. 每类状态只有一个权威来源；页面、微信和通知只消费状态，不根据提示文案或子进程创建猜测成功。
6. 架构调整采用渐进式模块化：先明确所有权和接口，再在真实改动中收拢耦合，不进行目录驱动的大规模搬迁。

## 2. 当前系统上下文

```text
维护者
  |-- Browser / Mobile Browser ------> Chub Web 页面与受保护 API
  |-- chub CLI ----------------------> Chub API、固定本机命令或状态文件服务
  `-- WeChat ClawBot
         `-> OpenClaw + Chub Plugin --(127.0.0.1)--> Chub 固定微信调度 API

Chub Web
  |-- Unix socket -------------------> Quick Worker -> fixed Runtime Runner registry -> Codex Runner
  |-- tmux / ttyd -------------------> Codex 实时终端
  |-- OpenClaw CLI / message send ---> OpenClaw Gateway 与微信通道
  |-- CDP ---------------------------> 受管 Debug Chrome
  |-- HTTPS -------------------------> 固定飞书通知目标
  `-- 固定脚本 ----------------------> 本机维护与 Web 重启
```

Chub 不接管 OpenClaw 的普通 Agent，也不把 Codex、OpenClaw 或模型供应商视为自身内部组件。它通过受限接口协调这些系统，并保存完成业务闭环所需的非敏感关联状态。

## 3. 当前进程与运行边界

### 3.1 Chub Web

FastAPI Web 进程当前负责：

- 页面、HTTP API、WebSocket 和统一响应。
- 真实 loopback/Tailnet 来源、安全 Header 和入口权限。
- 启动时装配 Codex、Quick Interaction、微信、自动化、通知、重启和用量等 Manager。
- 业务校验、操作日志、状态投影、通知协调和 Web 重启恢复。
- 管理实时终端的票据、连接与 ttyd/tmux 外围生命周期。

`app/application.py` 是当前 Composition Root，负责构造对象和连接跨模块回调。它可以继续承担装配职责，但长期不应继续吸收领域业务规则；复杂协调应进入有明确所有权的领域服务。

### 3.2 Quick Worker

Quick Worker 是与 Web 独立部署的本机服务，通过私有 Unix socket 通信，当前负责：

- 页面、微信和翻译非实时 AI 任务。
- 幂等提交、Session 租约、Runner 进程组、超时、取消和终态。
- 任务持久化、恢复、Web 离线期间继续执行及近期终态交付。

Worker 不负责身份认证、页面、微信路由、通知目标、Request 或设备维护。Worker 不健康时，涉及 AI Session 的写入失败关闭，不回退到 Web 内执行。

### 3.3 受管子进程与外部系统

- **Codex 实时终端**：由 tmux 保存交互进程，ttyd 提供受控 Web 终端。
- **Codex 后台 Runner**：由 Quick Worker 监督完整进程组。
- **自动化执行**：由 Automation Manager、固定 Runner 和跨进程锁协调 Debug Chrome。
- **Web 重启**：只通过固定脚本和 Deferred Restart Coordinator 协调。
- **OpenClaw**：独立 Gateway/Agent 平台；Chub 只管理固定状态和微信集成边界。
- **通知供应商**：由 Notification Service 按本机固定注册表调用，不接受任意目标。

这些执行机制具有不同的生命周期和安全语义，当前不需要合并成一个通用任务队列。

### 3.4 信任边界与横切约束

总体架构只规定所有领域共同遵守的安全底线，具体身份、路由和协议由对应专项文档维护：

- HTTP 请求以单用户本机工作站为信任边界：真实 loopback socket（`127.0.0.1` 或 `::1`）直接允许，真实 Tailnet socket 在启用时直接允许，其余来源拒绝；不信任客户端转发 Header。本机 CLI 的固定脚本和状态操作另以当前操作系统用户、本机文件权限及后端固定映射为边界，不因此获得任意命令或路径能力。
- 微信请求先由 OpenClaw 提供可信通道上下文，再由 Chub 校验 Owner、绑定 Session、入口能力和路由。高权限提交必须来自同机 OpenClaw 的真实 loopback socket；模型判断不能扩大授权。
- Web 与 Quick Worker 只通过私有 Unix socket 和受校验协议通信。Worker 不接受客户端指定的可执行文件、命令、任意路径或环境变量。
- Agent Runtime、OpenClaw、Chrome、通知供应商和操作系统均位于 Chub 信任边界之外，只能经固定 Adapter、Manager、注册表或脚本访问。
- 权限映射无法无损完成、身份或路由无法确认、协议不兼容、结果不确定时统一失败关闭，不回退到权限更高或约束更少的路径。
- 凭证和本机秘密只存在于私有配置或受限状态中；跨领域只传稳定非敏感标识，日志、页面和通知不得泄露敏感值。
- 异步操作必须区分受理、运行和最终状态。页面、通知和操作日志不得把进程创建、模型回复或 Tool Call 已发出解释为业务成功。
- 跨入口、进程和外部通道使用非敏感稳定标识关联入口请求、Session、任务、维护操作、Runtime 执行和通知终态；模型文本、展示标题和 Tool Call 内容不能作为追踪标识或成功依据。
- HTTP 错误统一返回 `success: false` 与 `error.code`、`error.message`、`error.source`；`source` 只允许 `chub` 或 `runtime`，由后端固定写入，客户端不能声明或覆盖。Chub 的认证、校验、协议、Worker、解析和内部边界错误标记为 `chub`；只有受控透传 Runtime 子进程诊断时标记为 `runtime`。来源标记不替代错误码，也不允许透传堆栈、凭证、终端票据或无界原文。

## 4. 当前逻辑分层

当前目录尚未完全按下列层次组织，但职责可以归纳为：

| 层次 | 当前职责 | 典型模块 |
| --- | --- | --- |
| 入口与展示 | Web 页面、API、WebSocket、Chub CLI、OpenClaw Plugin | `app/api/`、`app/web/`、`app/codex/routes.py`、`scripts/chub`、`integrations/openclaw/chub/` |
| 应用协调 | 组合用例、跨模块门禁、恢复与通知协调 | `app/application.py`、Quick Interaction、微信 Chub Mode、Deferred Restart |
| 领域服务 | AI Session、Request、自动化、通知、文档、用量和设备状态 | `app/codex/`、`app/automations/`、`app/notifications/`、`app/ai_usage/`、`app/services/` |
| 可靠执行 | 后台任务、租约、Runner、进程与终态 | Quick Worker、Automation Runner、固定维护脚本 |
| 外部适配 | Codex、OpenClaw、Chrome、飞书、操作系统 | 对应 Manager、Provider、CLI/HTTP/CDP Adapter |
| 基础设施 | 配置、安全、日志、平台检测、文件状态和锁 | `app/core/`、各领域 Store、`data/`、平台脚本 |

`app/services/` 当前同时包含领域服务、集成协调和基础工具。后续只在真实功能改动时把稳定职责迁入对应领域包，不为目录整齐批量重排。

### 4.1 当前与目标实现边界

总体架构中的目标组件不代表已经落地。关键边界如下：

| 能力 | 当前实现 | 长期目标 | 当前状态 |
| --- | --- | --- | --- |
| 逻辑 AI Session 生命周期 | AI Session Manager、AI Session Store；旧 Store 不参与启动选择或读取 | AI Session Manager、AI Session Store | 当前已落地并验收 |
| 后台 AI 执行 | Quick Worker、固定 Runtime Runner 注册表、Codex Runner | Quick Worker、固定 Runtime Runner 注册表 | 当前已落地并验收；生产只注册 Codex |
| 实时终端 | Interactive Supervisor、运行时无关的 Session 终端票据/连接状态、Codex Adapter、tmux、ttyd；不按旧 Store 回退 | Interactive Supervisor、Runtime Adapter | 当前已落地并验收；当前实现为 Codex |
| Runtime 私有协议 | Codex 命令、Hook、发现、模型、Writer 与事件解析已收敛到 Adapter/Runner；Runtime ID、能力矩阵和原生映射已统一 | 收敛到 Runtime Adapter/Runner | 当前已落地并验收；第二 Runtime 尚未接入 |
| 用户入口与正式 API | Codex 页面、微信固定路由、`/api/codex/*` | 保持当前正式 Codex 外观，不把它当作通用 Runtime 兼容别名 | 当前继续使用 |

目标组件只有在对应专项设计明确落地并通过验收后，才能在其他文档中写成当前能力。Runtime 的实现规范和能力门槛以 AI Runtime 专项设计的第二部分为准。

## 5. 领域边界

### 5.1 设备与维护

拥有节点状态、白名单维护操作、操作日志、运行日志和受控 Web 重启。客户端不能提供任意命令、路径或日志来源。操作成功以最终实例或系统状态为准。

### 5.2 AI Session 与执行

当前由 AI Session Manager、AI Session Store、Interactive Supervisor 和 Quick Worker 分别承担逻辑 Session、Activity、实时终端及后台 AI 任务；终端票据、页面接管和连接状态属于 `app.ai_session` 通用边界，Runtime Adapter 只解释 Runtime 私有进程/后端匹配。模型用量由 AI Usage Service 维护。旧 `CodexPtyManager`、Codex Session Store 仅作为历史实现和专项回归代码保留，不参与生产启动或状态读取，详见专项设计。

### 5.3 Request 需求储备

拥有 R1–R9 活动槽位、需求正文、版本、运行关联和归档状态。Request 可以提交到 AI Session，但不拥有 Session 或 Worker 任务终态；通过稳定标识消费其结果。

### 5.4 自动化与周报

拥有 Debug Chrome 环境、自动化配置、跨进程锁、任务状态、下载产物和周报流程。自动化只能执行已配置任务和固定扩展，不复用 AI Runtime 的 Session 语义。

### 5.5 通知

拥有固定目标注册表、供应商调用、消息限制和请求幂等。业务领域决定何时通知、使用哪条已批准路由，并拥有 `pending`、`sending`、`sent`、`failed` 或 `skipped` 等业务投递状态；Notification Service 和微信发送适配只负责受控传输并返回结果，不决定主任务成功。

### 5.6 OpenClaw 与微信

拥有通道状态读取、可信消息上下文、固定指令入口、微信 Chub 模式路由和原路回送关联。设备任务的权限、Session、Request 和任务终态仍由 Chub 对应领域拥有。

### 5.7 项目资料与设置

项目资料拥有只读索引、Markdown 渲染和页面展示状态；设置拥有经过校验的节点级业务开关。两者不得成为任意文件读取或通用配置编辑入口。

## 6. 状态所有权

| 状态 | 权威来源 | 其他模块如何使用 |
| --- | --- | --- |
| 节点与服务健康 | 操作系统、进程和健康探测 | Web 聚合展示，不缓存为永久真相 |
| Chub AI Session 元数据 | AI Session Manager、AI Session Store | 页面、微信和 Worker 使用 Chub Session ID 关联 |
| 原生 Agent Session | 当前 Codex 本地状态；长期 Runtime Adapter | Chub 只保存受校验映射，不解释私有格式 |
| AI 后台任务与租约 | Quick Worker | Web 恢复后重建投影并确认通知 |
| 实时终端连接 | tmux/ttyd、Terminal Registry | Session 状态只消费可信探测结果 |
| Request | Request Backlog Store | 执行时关联 Worker task/run ID，不复制任务终态 |
| 微信绑定与通道 | OpenClaw；Chub 保存受控路由快照 | 每次提交校验，失败不回退全局目标 |
| 自动化任务与产物 | Automation Store、锁和 artifacts | 页面读取受限状态与固定产物 |
| 通知业务状态 | 发起通知的领域状态记录 | 主任务结果与通知终态分开保存，传输结果回写原记录 |
| 飞书通知调用结果 | Notification Service | 调用方消费受限结果，不把供应商响应解释为主任务终态 |
| 微信任务通知投递 | 对应 Quick Interaction 或重启通知记录 | OpenClaw `message send` 只负责传输，不拥有业务状态 |
| 重启协调 | Deferred Restart State + 新实例健康 | 子进程创建不等于重启成功 |
| 配置 | 环境变量、受控 YAML 和私有状态 | 客户端只能修改明确开放的设置 |

所有持久状态必须有大小、权限、格式和恢复边界。跨领域只传递稳定标识和公开模型，不直接读取对方私有状态文件。

## 7. 核心调用链

### 7.1 页面或微信 AI 任务

```text
入口认证与业务校验
  -> 选择 Chub Session
  -> Worker 幂等提交并原子获取租约
  -> 固定 Runtime Runner 执行（当前为 Codex Runner）
  -> Worker 写入真实终态并释放租约
  -> Web 恢复/投影状态
  -> 页面展示或按保存路由通知
```

任何同步回执、进程创建、模型回复或 Tool Call 都不能替代 Worker 终态。

### 7.2 微信固定指令

```text
微信 -> OpenClaw Plugin -> Chub 固定调度
     -> 对应领域服务执行或提交任务
     -> 同步结果由 Hook 原路返回
     -> 异步终态由 Chub 使用任务保存路由发送
```

OpenClaw Agent 不参与设备指令判断，Chub 也不反向调用 OpenClaw Agent 执行设备能力。

### 7.3 自动化任务

```text
受保护入口 -> Automation Manager
           -> 固定任务配置与环境校验
           -> 跨进程锁 -> Runner -> Debug Chrome/固定扩展
           -> 状态、操作日志和受限产物
```

### 7.4 Web 重启

```text
直接维护操作或任务级登记
  -> Deferred Restart Coordinator
  -> 固定重启脚本
  -> 服务管理器启动新 Web
  -> 新实例 ID 与健康确认
  -> 恢复 Worker 投影、通知和重启终态
```

## 8. 长期目标分层

```text
Channels & Presentation
  Web / API / CLI / OpenClaw Plugin
                 |
Application Use Cases & Policies
  身份、授权、固定业务流程、跨领域协调
                 |
Domain Services
  Device | AI Session | Request | Automation | Notification | Documents
                 |
Execution Supervisors
  Quick Worker | Interactive Supervisor | Automation Runner | Maintenance
                 |
Adapters & Infrastructure
  Agent Runtime | OpenClaw | Chrome | Feishu | OS | Stores | Logs
```

目标分层是依赖方向，不要求每层成为独立进程：

- 入口只解析请求和返回统一响应，不拥有业务状态。
- 应用用例协调多个领域，但不解析外部系统私有协议。
- 领域服务拥有业务规则和公开状态模型。
- 执行层拥有进程、租约、超时、取消和真实终态。
- Adapter 把外部能力映射为领域可理解的结果。
- 基础设施提供配置、持久化、安全、日志和平台差异，不反向决定业务流程。

## 9. 当前结构的优势与需收敛问题

### 9.1 应保留的优势

- Web 与 AI Worker 已分离，Web 重启不打断已接受任务。
- 安全入口、白名单、文件限制和失败关闭规则明确。
- Session、任务、通知、重启和外部通道终态保持分离。
- 文件持久化和本机服务足以满足个人设备规模，维护成本低。
- macOS 与 Ubuntu 使用相同业务语义、不同受控服务实现。

### 9.2 渐进收敛的问题

- `application.py` 的跨模块回调和直接装配较多，应通过明确用例服务减少新增耦合。
- `app/services/` 职责混杂，后续按领域归属渐进收拢。
- Codex 私有实现已从 Session、Worker 的共享职责中收敛到 Adapter/Runner；Runtime ID、能力矩阵、原生映射、活动事件和终端连接边界已统一。协议、配置和 `/api/codex/*` 仍是当前 Codex 正式入口；新增 Runtime 按专项设计实现，不为旧版本增加兼容别名。
- 自动化、维护和 AI 任务使用不同执行机制；应先统一可靠性原则，不急于统一代码框架。
- 部分 API 和数据路径以当前实现命名；只有内部边界稳定后才迁移公共命名。

## 10. 演进原则与顺序

1. **先固化现状。** 总架构和专项文档先明确权威状态、依赖方向和不得退化的用户行为。
2. **Codex Runtime 边界收敛已完成。** 当前生产仍只注册 Codex，并已具备实现其他 Runtime 所需的边界；新增 Runtime 按专项文档的实现契约直接实施。
3. **保持当前正式入口。** 现有 API、页面、微信指令和服务部署在内部收敛期间保持当前用户可见语义；不为已改变的旧运行态、旧协议或旧入口增加长期兼容层。
4. **从真实调用提取接口。** 只有两个以上稳定调用方或明确替换需求出现时才新增共享抽象。
5. **领域内聚优先。** 新功能放入对应领域，跨领域通过公开服务和稳定 ID 协调。
6. **执行可靠性一致。** 所有异步操作都区分受理、开始和最终状态，但不要求使用同一个 Worker。
7. **不扩大基础设施。** 没有容量、隔离或恢复需求时，不引入数据库、消息队列或新常驻服务。

AI Runtime 架构收敛已经完成；新增 Runtime 和其他领域收敛仍由真实产品需求驱动，总架构文档本身不授权同步重构。

## 11. 架构验收标准

后续架构调整必须同时满足：

- 页面、CLI、微信和自动化的用户可见行为有明确权威文档。
- 入口认证、Tailnet 边界、权限和白名单不因分层调整而放宽。
- Web 重启、Worker 恢复、通知和操作日志仍以最终状态为准。
- 领域状态有唯一所有者，跨模块不直接修改其他领域私有文件。
- 外部系统私有协议被限制在 Adapter/Manager 内。
- 配置不被覆盖；数据切换必须原子、有界且不泄露本机秘密，已改变且不再适用的 Chub 旧运行态按固定边界清理，不新增长期迁移兼容分支。
- macOS 与 Ubuntu 行为一致；无法实机验证的平台明确说明。
- 架构复杂度与个人设备规模匹配，没有为假设需求增加常驻基础设施。

## 12. 专项文档关系

- [Chub AI Runtime 架构设计](CHUB_AI_RUNTIME_DESIGN.md)：AI Runtime 架构、AI Session Manager、Runtime Adapter、Worker 和实现规范。
- [Chub CLI 分发、安装与发布设计](CHUB_CLI_DISTRIBUTION_DESIGN.md)：新设备安装、核心 Chub 与可选 ClawBot 职责、npm 发布、版本管理和 GitHub Release。
- [AI Session 状态模型](AI_SESSION_STATE_DESIGN.md)：Session、Activity、入口、槽位和单 writer 产品语义。
- [Chub Quick Worker 独立服务设计](CHUB_QUICK_WORKER_DESIGN.md)：AI 后台任务、恢复、通知和重启协调语义。
- [OpenClaw 定制集成设计](OPENCLAW_CUSTOMIZATION_DESIGN.md)：OpenClaw/微信身份、路由、权限、插件定制和通知边界。
- [前端 UI 模块化设计](FRONTEND_UI_DESIGN.md)：Web 前端加载、组件、交互和视觉契约。
- [Codex AI 额度与用量采集设计](CODEX_AI_QUOTA_USAGE_DESIGN.md)：当前 Codex/OpenAI 用量接口、来源和缓存。
- [周报自动化与生成设计](WEEKLY_REPORT_AUTOMATION_DESIGN.md)：自动化资料、确认门禁和报告产物。

当前能力查询仍以[集成能力清单](CHUB_INTEGRATION_CAPABILITIES.md)为统一入口。专项文档只维护自己的权威边界，不复制总架构全文。
