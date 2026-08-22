# Chub 总体架构与演进设计

> 状态：持续维护。
> 主要读者：AI Agent、实现和排障 Agent；维护人员用于确认项目边界、状态所有权和架构验收标准。
> 本文负责：Chub 当前的进程边界、主要职责、状态所有权和跨模块约束，是各专项设计的上层依据；当前可用能力以[集成能力清单](CHUB_INTEGRATION_CAPABILITIES.md)为准。
> 本文不负责：专项功能的完整实现契约、固定指令语法、插件源码或部署步骤；这些内容由对应专项文档维护。

本文与项目 README 共同构成核心项目文档：README 回答“Chub 是什么、如何使用”，本文回答“Chub 如何组织、各状态由谁负责、跨模块如何协作”。其他专项设计、能力契约和维护资料均位于其后，并不得与这两份核心文档冲突。

## 1. 定位与总体结论

Chub 是面向个人设备、本地优先的轻量 AI 工作站控制面。它统一管理可信入口、设备能力、业务规则、AI Session、需求、自动化、状态、通知和用户可见终态；具体任务由受控执行器或外部 Runtime 完成。

当前架构遵循以下结论：

1. Chub 是本地优先的模块化单体，不拆分为通用微服务平台。
2. Web 是控制面和业务协调入口；Quick Worker 独立执行需要跨 Web 重启继续的后台任务。
3. 实时终端、后台任务、OpenClaw 和自动化使用各自的运行边界，不为形式统一合并生命周期。
4. 每类状态只有一个权威来源；页面、微信和通知不以进程创建或同步回执代替最终状态。
5. 新增功能先明确职责和状态所有权，再按实际需要小步收拢结构。

## 2. 当前系统上下文

```text
维护者
  |-- Browser / Mobile Browser ------> Chub Web 页面与受保护 API
  |-- chub CLI ----------------------> Chub API、固定本机命令或状态文件服务
  `-- WeChat ClawBot
         `-> OpenClaw + Chub Plugin --(127.0.0.1)--> Chub 固定微信调度 API

Chub Web
  |-- Unix socket -------------------> Quick Worker -> Codex Runner
  |-- ttyd --------------------------> tmux -> Codex 实时终端
  |-- OpenClaw CLI / message send ---> OpenClaw Gateway 与微信通道
  |-- CDP ---------------------------> 受管 Debug Chrome
  |-- HTTPS -------------------------> 固定飞书通知目标
  `-- 固定脚本 ----------------------> 本机维护、升级与 Web 重启
```

Chub 不接管 OpenClaw 的普通 Agent，也不把 Codex、OpenClaw 或模型供应商当作自身内部模块；它只通过受限接口协调这些系统。

## 3. 当前进程与运行边界

### 3.1 Chub Web

FastAPI Web 进程当前负责：

- 页面、HTTP API、WebSocket 和统一响应。
- 真实 loopback/Tailnet 来源、安全 Header 和入口权限。
- 启动时装配 Codex、Quick Interaction、微信、自动化、通知、重启和用量等 Manager。
- 业务校验、操作日志、状态投影、通知协调和 Web 重启恢复。
- 管理实时终端的连接和 `ttyd` Web 桥；不拥有原生 Codex 的执行、历史或 writer 锁。

### 3.2 Quick Worker

Quick Worker 是与 Web 独立部署的本机服务，通过私有 Unix socket 通信，负责：

- 页面、微信和翻译非实时 AI 任务。
- 幂等提交、Session 租约、Runner 进程组、超时、取消和终态。
- 任务持久化、恢复、Web 离线期间继续执行及近期终态交付。

Worker 不负责身份认证、页面、微信路由、通知目标或设备维护。Worker 重启是独立恢复操作：确认后只关闭快速任务提交、清理 Worker 自身任务并重建 Worker；不重启 Web、OpenClaw 或实时终端。实时终端使用独立的 Codex PTY/tmux 链路。

### 3.3 受管子进程与外部系统

- **Codex 实时终端**：`ttyd` 是可重建的 Web 桥，tmux 保存交互载体，原生 Codex 负责执行、历史和 writer 锁。
- **自动化执行**：由固定任务、Runner 和跨进程锁协调 Debug Chrome。
- **Web 重启**：只通过固定脚本和重启协调器执行。
- **OpenClaw 与通知**：分别由固定集成和固定通知目标受控调用。

这些执行机制具有不同生命周期，当前不合并成通用任务队列。

### 3.4 信任边界与横切约束

总体架构只规定所有领域共同遵守的边界，具体身份、路由和协议由对应专项文档维护：

- 受保护接口只接受真实 loopback 或按配置允许的真实 Tailnet 来源；客户端不能扩大权限、命令或路径范围。
- Web、Worker、OpenClaw 和外部 Runtime 通过固定接口、脚本或适配器访问；未知身份、路由、协议或结果时失败关闭。
- 凭证和本机秘密只保存在受限配置或状态中；页面、通知和日志只传递必要的非敏感标识。
- 异步操作区分受理、运行和最终状态；进程创建、HTTP 200 或 Tool Call 已创建都不等于成功。

### 3.5 版本与协议元数据管理

升级和恢复只使用后端固定的版本、协议和运行态边界，不接受客户端提供的版本、路径或命令：

| 类型 | 权威来源 | 管理规则 |
| --- | --- | --- |
| Web 代码版本 | `app/core/build_info.py` 的 `WEB_CODE_VERSION` | 随 Web 代码基线发布；受控升级确认新 Web 实例必须匹配目标版本 |
| Session 数据版本 | `app/core/build_info.py` 的 `SESSION_SCHEMA_VERSION` | Session 持久化结构或语义变化时递增；不为已改变且不再适用的 Chub 旧运行态增加长期兼容分支 |
| Quick Worker IPC 协议 | `app/quick_worker.py` 的 `PROTOCOL_VERSION` | 只有 Web 与 Worker 的请求、响应或协议语义变化时递增；协议变化必须同步 Web、Worker、测试和部署产物 |
| 升级方案契约 | `app/core/build_info.py` 的 `SYSTEM_UPGRADE_CONTRACT_VERSION` | 方案格式变化时递增，只约束升级方案本身 |

版本和协议元数据只用于后端固定的升级方案、运行态清理和最终校验；系统升级/恢复的目标、范围、门禁和失败边界见 7.6。相关流程只影响直接受影响的 AI Runtime 写入和服务，不删除 Codex 原生 Session、用户配置或业务数据；升级方案异常时仍保留当前版本运行态恢复能力，但必须明确不执行代码版本升级。

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

## 5. 领域边界

### 5.1 设备与维护

拥有节点状态、白名单维护操作、操作日志、运行日志和受控服务维护。Web 重启、Quick Worker 重启以及系统升级/运行态恢复分别按目标服务的边界执行；操作成功以最终实例或系统状态为准。

### 5.2 AI Session 与执行

AI Session Manager/Store 管理逻辑 Session 和 Activity，Interactive Supervisor 管理实时终端，Quick Worker 管理后台任务；实时终端和快速交互不互相接管 writer。Runtime Adapter 只解释 Codex 私有进程状态，AI Usage Service 管理用量。

### 5.3 Request 需求储备

拥有 R1–R9 活动槽位、需求正文、运行关联和归档状态。Request 可以提交到 AI Session，但不拥有 Session 或 Worker 任务终态。

### 5.4 自动化与周报

拥有 Debug Chrome 环境、自动化配置、任务状态、下载产物和周报流程；只执行已配置任务和固定扩展。

### 5.5 通知

拥有固定目标注册表、供应商调用和消息限制。业务领域拥有通知业务状态，Notification Service 只负责受控传输，不决定主任务成功。

### 5.6 OpenClaw 与微信

拥有通道状态读取、固定指令入口、微信路由和原路回送关联；设备任务的权限、Session 和任务终态仍由 Chub 对应领域拥有。ClawBot 的 `restart` API action 以及微信 `restart clawbot` 指令都是重启与恢复入口，负责固定插件/补丁基线同步和 Gateway/通道最终确认。

### 5.7 项目资料与设置

项目资料只读展示；设置只保存经过校验的节点级业务开关，不提供任意文件读取或通用配置编辑。

## 6. 状态所有权

| 状态 | 权威来源 | 其他模块如何使用 |
| --- | --- | --- |
| 节点与服务健康 | 操作系统、进程和健康探测 | Web 聚合展示，不缓存为永久真相 |
| Chub AI Session 元数据 | AI Session Manager、AI Session Store | 页面、微信和 Worker 使用 Chub Session ID 关联 |
| 原生 Codex Session 与 writer | Codex 本地状态、Runtime Adapter | Chub 只保存受校验映射，不猜测或接管 writer |
| AI 后台任务与租约 | Quick Worker | Web 恢复后重建投影并确认通知 |
| 实时终端 Web 桥 | Interactive Supervisor、ttyd | Web 重启时可关闭并按逻辑 Session 重建 |
| 实时终端载体 | 固定名称的 tmux | 普通 Web 重启保留，重新进入时复用 |
| Request | Request Backlog Store | 执行时关联 Worker task/run ID，不复制任务终态 |
| 微信绑定与通道 | OpenClaw；Chub 保存受控路由快照 | 每次提交校验，失败不回退全局目标 |
| 自动化任务与产物 | Automation Store、锁和 artifacts | 页面读取受限状态与固定产物 |
| 通知业务状态 | 发起通知的领域状态记录 | 主任务结果与通知终态分开保存，传输结果回写原记录 |
| 重启协调 | Deferred Restart State + 新实例健康 | 新实例健康且 ID 变化后才算成功 |
| Quick Worker 重启操作 | Quick Worker maintenance operation state | 记录清理、重建和新 generation 健康结果；不拥有 Web 或实时终端状态 |
| 系统升级/运行态恢复与 Runtime 写入锁 | System Upgrade Coordinator + 持久化操作状态 | 记录方案、阶段、失败边界和最终验证；只锁定直接受影响的 Runtime 写入 |
| ClawBot 重启与恢复 | OpenClaw Manager + 固定插件/补丁清单 | 同步固定兼容基线并确认 Gateway、消息通道和运行产物；不修改任意第三方版本 |
| 配置 | 环境变量、受控 YAML 和私有状态 | 客户端只能修改明确开放的设置 |

所有持久状态必须有大小、权限、格式和恢复边界。跨领域只传递稳定标识和公开模型，不直接读取对方私有状态文件。

## 7. 核心调用链与维护流程

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
  -> 清理仍有映射的旧 ttyd
  -> 用户进入 Session 时新建 ttyd 并 attach 原 tmux
  -> 恢复 Worker 投影、通知和重启终态
```

Web 重启的目标是重新加载 Chub Web 当前代码和配置，恢复控制面、状态投影和页面入口。

- **范围**：只重启 Chub Web。Quick Worker、OpenClaw Gateway、已接受的后台任务和原生 Codex
  进程保持运行；实时终端的 `ttyd` 只是可重建的 Web 桥，固定 tmux 和原生 writer 不因
  Web 重启被停止。
- **门禁**：只处理当前 Web 重启的重复请求和直接维护冲突。不因 Quick Worker 忙、ClawBot
  状态或实时终端正在使用而拒绝 Web 重启；任务级请求在自身任务和通知进入终态后执行。
- **结果**：新实例完成启动、实例 ID 已变化且健康检查通过后，才记录 `succeeded`。仅创建
  重启进程、旧实例退出或 HTTP 受理不能作为成功；启动或健康确认失败时记录 `failed` 并
  展示原因。

重启后，实时终端再次进入时按原逻辑 Session 重新创建 `ttyd` 并连接原 tmux；无法确认
Session 归属或 writer 状态时保持失败关闭，不接管其他 writer。升级与恢复的运行态清理
不属于普通 Web 重启范围。

### 7.5 Quick Worker 重启

Quick Worker 重启的目标是清理 Worker 当前运行态并恢复一个可用的新 Worker，不是等待任务自然完成，也不是 Web 或实时终端的重启。

- **范围**：关闭新快速任务提交，取消排队任务，停止执行中任务并写入明确的未完成终态；清理 Worker 自有任务、租约和运行映射，启动新 Worker 并确认新的 generation、协议和健康状态。任务不自动重放；Web、ClawBot、实时终端的 tmux 和原生 Codex 不在范围内。
- **门禁**：只保留认证、重复维护和系统升级直接冲突等必要保护。不因 Worker 当前不健康、协议不兼容、任务排队/执行中或 Web 状态而拒绝恢复；这些状态正是允许执行恢复的原因。
- **结果**：只有新 Worker 的健康、协议和任务计数状态确认后才记录成功；受理请求、进程创建或旧 Worker 退出不能作为成功。失败时保留失败原因和固定恢复入口，不自动重放已中断任务。

详细任务状态、租约、通知和维护操作契约以 [Chub Quick Worker 独立服务设计](CHUB_QUICK_WORKER_DESIGN.md) 为准。

### 7.6 系统升级与运行态恢复

系统升级与运行态恢复是系统兜底的维护流程，目标是在可控范围内切换已准备的版本方案，或在方案不可用时重建当前版本运行态。它不是普通 Web 重启的别名，也不以外围服务“看起来正常”作为开始条件。

- **范围**：确认后锁定直接受影响的 Chub AI Runtime 写入，停止 Quick Worker，终止在途快速任务并清理 Chub 自有 Session 关联、Hook、租约和其他固定运行态；按固定脚本重启 Web/Worker，并重新确认实例、协议、Session 映射及必要通知状态。Codex 原生 Session、用户配置、日志、项目资料和业务数据保留；无关只读能力、ClawBot 和其他独立服务不因该流程整体不可用。
- **门禁**：只校验可信入口、持久化升级状态可安全读写、固定升级/重启脚本、已安装服务定义、固定运行态清理路径、没有并发维护操作，以及执行过程中的有界写入等待。不检查当前 Web/Worker 健康、任务是否排队或执行中，也不等待任务自然排空。升级方案无法读取或校验时，降级为固定的当前版本运行态恢复，并明确不执行代码版本升级。
- **结果**：只有新 Web/Worker 实例、目标版本/协议、Session 映射和必要通知状态完成最终确认后才记录成功。破坏性阶段开始前失败可释放写入锁；开始清理或切换后失败则继续保持受影响写入失败关闭，并保留同一固定恢复路径。进程已创建、HTTP 200 或任务已受理不能作为成功。

该流程的具体阶段、失败恢复和方案字段以当前实现及对应 Runtime/Worker 专项文档为准；总架构只约束范围、状态所有权和门禁方向。

### 7.7 ClawBot 重启与恢复

ClawBot 的目标是恢复 OpenClaw Gateway 和微信消息通道，不把当前状态正常作为重启前提。页面/API 使用 `restart` action，微信使用 `restart clawbot`；两者在需要时先同步仓库固定的 Chub 插件、微信适配器目标版本和补丁清单，再执行 Gateway 重启。

- **范围**：只影响 OpenClaw Gateway、微信消息通道及其固定插件/补丁运行副本；不重启 Chub Web、Quick Worker 或实时终端。
- **门禁**：只保留可信入口、OpenClaw 可执行文件、固定服务定义、目标版本可确认和 OpenClaw 维护操作互斥。Gateway 停止、未知、未配置、通道异常和 Agent 任务不构成全局拒绝。
- **结果**：同步、Gateway 健康、已配置消息通道运行和插件/补丁运行产物均确认后才算完整恢复成功；没有配置消息通道时只报告 Gateway 已恢复及通道未配置，不把 ClawBot 宣称为已就绪；版本、完整性或锚点无法确认时失败关闭，不盲目覆盖。

页面显示“重启与恢复”，API action 继续使用 `restart`，微信指令使用 `restart clawbot`；详细插件、补丁和微信验收边界以 [OpenClaw 定制集成设计](OPENCLAW_CUSTOMIZATION_DESIGN.md) 为准。

## 8. 架构原则

- 入口负责认证、参数校验和展示，不直接拥有其他领域的状态。
- Web、Quick Worker、实时终端、OpenClaw 和自动化按各自生命周期运行。
- Quick Worker 重启只清理其排队/执行任务并恢复 Worker；不因 Worker、Web 或任务状态把无关服务纳入同一维护门禁。
- 系统升级与运行态恢复只锁定直接受影响的 Runtime 写入和服务；升级方案异常时优先保留可确认的当前版本恢复能力，不把“无法升级”扩大为“无法恢复”。
- ClawBot `restart` API action 与微信 `restart clawbot` 只同步固定兼容基线并恢复 Gateway/消息通道；不因当前异常状态拒绝恢复，也不自动接收任意版本或补丁。
- 领域状态由唯一模块负责，跨模块通过受控接口和稳定标识关联。
- 异步操作必须确认最终状态；不以进程创建、HTTP 200 或同步回执宣告成功。
- 只有真实需求需要时才增加抽象、兼容层或常驻服务。

## 9. 架构验收标准

后续架构调整必须同时满足：

- 页面、CLI、微信和自动化的用户可见行为有明确权威文档。
- 入口认证、Tailnet 边界、权限和白名单不因分层调整而放宽。
- Web 重启、Worker 重启、系统升级/恢复、通知和操作日志仍以最终状态为准；未知状态不能被显示或记录为成功。
- Web、Worker 和系统升级/恢复的门禁按资源局部生效；Worker 忙碌、Web 不健康或外围服务状态不能无理由扩散为全局拒绝。
- 升级方案不可用时，必须仍能区分“当前版本运行态恢复”和“代码版本升级”，并在页面、操作记录和结果中保持一致。
- ClawBot 重启必须区分“Gateway 已重启”和“ClawBot 已恢复”，插件/补丁不一致或消息通道未恢复时不能宣称整体成功。
- 领域状态有唯一所有者，跨模块不直接修改其他领域私有文件。
- 外部系统私有协议被限制在 Adapter/Manager 内。
- 配置不被覆盖；数据切换必须原子、有界且不泄露本机秘密，已改变且不再适用的 Chub 旧运行态按固定边界清理，不新增长期迁移兼容分支。
- macOS 与 Ubuntu 行为一致；无法实机验证的平台明确说明。
- 架构复杂度与个人设备规模匹配，没有为假设需求增加常驻基础设施。

## 10. 专项文档关系

- [Chub AI Runtime 架构设计](CHUB_AI_RUNTIME_DESIGN.md)：AI Runtime 架构、AI Session Manager、Runtime Adapter、Worker 和实现规范。
- [Chub CLI 分发、安装与发布设计](CHUB_CLI_DISTRIBUTION_DESIGN.md)：新设备安装、核心 Chub 与可选 ClawBot 职责、npm 发布、版本管理和 GitHub Release。
- [AI Session 状态模型](AI_SESSION_STATE_DESIGN.md)：Session、Activity、usage 投影、入口、槽位和单 writer 产品语义。
- [Chub Quick Worker 独立服务设计](CHUB_QUICK_WORKER_DESIGN.md)：AI 后台任务、恢复、通知和重启协调语义。
- [OpenClaw 定制集成设计](OPENCLAW_CUSTOMIZATION_DESIGN.md)：OpenClaw/微信身份、路由、权限、插件定制和通知边界。
- [前端 UI 模块化设计](FRONTEND_UI_DESIGN.md)：Web 前端加载、组件、交互和视觉契约。
- [Codex AI 额度与用量采集设计](CODEX_AI_QUOTA_USAGE_DESIGN.md)：当前 Codex/OpenAI 用量接口、来源和缓存。
- [周报自动化与生成设计](WEEKLY_REPORT_AUTOMATION_DESIGN.md)：自动化资料、确认门禁和报告产物。

当前能力查询仍以[集成能力清单](CHUB_INTEGRATION_CAPABILITIES.md)为统一入口。专项文档只维护自己的权威边界，不复制总架构全文。
