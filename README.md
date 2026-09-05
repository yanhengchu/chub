# Chub

## 项目介绍

Chub 是面向个人设备、本地优先的轻量 AI 工作站控制面。它按 Chub 核心、AI Runtime 和第三方服务三层组织设备能力、AI Session 和任务入口，在统一的安全与状态边界内协调运行、恢复任务、确认最终状态并交付结果；Chub 本身不是模型，也不作为通用对话 Agent 执行任务。

当前 Codex 是唯一完整接入的 Agent Runtime，负责实际的分析、编码和工具调用；Quick Worker 承载需要跨 Web 重启继续运行的后台 AI 任务；OpenClaw 在微信链路中只承担可信消息网关和通道适配。当前 Runtime 架构、能力矩阵和 AI Runtime 实现规范见 [Chub AI Runtime 架构设计](docs/CHUB_AI_RUNTIME_DESIGN.md)。具体业务能力和使用入口见“当前功能”，不在项目定位中重复列举。

长期目标是让 Chub 成为不依赖单一 Agent 产品的个人 AI 工作站：在保持统一安全、逻辑 Session、任务和最终状态语义的前提下，通过稳定契约接入经过验证的 Agent Runtime。项目继续坚持本地优先、可靠终态和适合个人维护的复杂度，支持 macOS LaunchAgent 与 Ubuntu systemd user service；新 Runtime 只由真实需求驱动，并在能力、权限和恢复机制通过验证后接入。

## AI Agent 快速理解

本文是 Chub 项目入口，负责项目定位、当前能力、运行入口、核心安全边界、架构概览和专项文档导航。本文不替代 Session、Worker、Runtime、OpenClaw、自动化、前端或用量等子模块设计，也不重复维护微信固定指令和用户可见回复格式；这些细节以对应专项文档和[集成能力清单](docs/CHUB_INTEGRATION_CAPABILITIES.md)为准。

按以下顺序判断项目行为：先用本文确认项目范围和当前能力，再用[Chub 总体架构设计](docs/CHUB_ARCHITECTURE_DESIGN.md)确认进程、领域和状态所有权；需要确认“现在可以调用什么”时读取[集成能力清单](docs/CHUB_INTEGRATION_CAPABILITIES.md)；最后进入对应子模块文档。项目资料页面的登记、摘要和状态以 `docs/design_documents.json` 为准；“已验收”不等于归档。历史资料不能覆盖当前能力契约。

当前不可放宽的边界：Chub 是控制面和可靠协调者，不是模型或通用 Agent；Codex 是当前唯一完整接入的 Runtime；每类状态只有一个权威来源，异步操作必须确认最终状态；客户端不能提供任意命令、路径、Runtime、Session 或收件人；认证、白名单和敏感信息保护失败时必须失败关闭，外部通道的身份、路由、协议或结果不确定时不得回退或放宽权限。具体身份、路由、权限、恢复和通知规则由对应专项文档维护。

## 架构与文档入口

本文用于说明 Chub 是什么、当前提供什么能力以及如何使用；[Chub 总体架构设计](docs/CHUB_ARCHITECTURE_DESIGN.md)是项目的核心架构依据，定义系统边界、职责分层、状态所有权和跨模块约束。下面的架构和子模块索引只描述当前职责；新增功能和专项设计必须先遵循总体架构，再进入对应领域文档。

### 当前进程架构

```text
Browser / chub CLI / 固定自动化
  -> Chub 核心层：Web、API、配置、安全、维护、自动化与通知
       |-> Chub Debug Chrome -> Debug Chrome 浏览器实例
       `-> Automation Runner -> Debug Chrome / 固定扩展

AI Runtime 层
  -> Chub Quick Worker -> 固定 Runtime Runner -> Codex Runner
  `-> ttyd -> 固定 tmux carrier -> Codex 实时终端

微信 ClawBot -> OpenClaw（第三方服务层）
  -> Chub 插件 -> 固定 Chub 调度入口
  -> 只读/维护能力调用核心层；AI 任务调用 AI Runtime 公开能力
```

核心层不依赖具体 AI Runtime 或 OpenClaw；AI Runtime 与第三方服务均依赖核心层，第三方只有需要 AI 时才调用 AI Runtime 的公开能力。Web/API 的启动组合只负责注册公开契约，不拥有上层业务或私有状态。进程生命周期、状态所有权和外部边界详见总体架构，不在此重复展开。

### 仓库子模块索引

| 路径 | 当前职责 | 细节入口 |
| --- | --- | --- |
| `app/application.py`、`app/api/`、`app/web/` | 部署组合、FastAPI 接口、页面、WebSocket 和项目资料展示 | [总体架构](docs/CHUB_ARCHITECTURE_DESIGN.md)、[前端 UI 设计](docs/FRONTEND_UI_DESIGN.md) |
| `app/ai_session/`、`app/codex/` | 逻辑 AI Session、实时终端和当前 Codex 正式入口 | [Session 状态模型](docs/AI_SESSION_STATE_DESIGN.md)、[AI Runtime 设计](docs/CHUB_AI_RUNTIME_DESIGN.md) |
| `app/ai_runtime/`、`app/quick_worker*.py` | Runtime 契约、固定 Runner、后台任务、租约、恢复和终态 | [AI Runtime 设计](docs/CHUB_AI_RUNTIME_DESIGN.md)、[Quick Worker 设计](docs/CHUB_QUICK_WORKER_DESIGN.md) |
| `app/automations/` | Debug Chrome、配置驱动自动化、下载产物和任务状态 | [总体架构](docs/CHUB_ARCHITECTURE_DESIGN.md)、[周报自动化设计](docs/WEEKLY_REPORT_AUTOMATION_DESIGN.md) |
| `app/notifications/`、`app/requests/` | 核心层通知与 R1–R9 需求储备 | [总体架构](docs/CHUB_ARCHITECTURE_DESIGN.md)、[能力清单](docs/CHUB_INTEGRATION_CAPABILITIES.md) |
| `app/ai_runtime/`、`app/ai_usage/` | Runtime 用量契约、共享快照与展示口径 | [总体架构](docs/CHUB_ARCHITECTURE_DESIGN.md)、[额度设计](docs/CODEX_AI_QUOTA_USAGE_DESIGN.md) |
| `app/core/`、`app/tasks/` | 配置、安全、日志、平台检测、白名单维护任务 | [总体架构](docs/CHUB_ARCHITECTURE_DESIGN.md) |
| `app/services/` | 当前跨领域服务和协调逻辑；不是新的统一领域边界 | [总体架构](docs/CHUB_ARCHITECTURE_DESIGN.md) |
| `integrations/openclaw/chub/` | Chub OpenClaw 插件源码、构建、部署和协议验收 | [OpenClaw 定制设计](docs/OPENCLAW_CUSTOMIZATION_DESIGN.md)、[插件说明](integrations/openclaw/chub/README.md) |
| `scripts/`、`config/`、`tests/`、`docs/` | 服务安装与维护、示例配置、回归测试和项目资料 | [总体架构](docs/CHUB_ARCHITECTURE_DESIGN.md)、本文“项目资料维护” |

## 当前功能

| 功能域 | 当前可用能力 | 主要入口 |
| --- | --- | --- |
| 设备管理 | 查看节点与系统状态，执行后端白名单维护任务，查看受限、脱敏的操作日志和运行日志 | 首页、设置页、日志页 |
| Codex 会话 | 按“快速会话”和“实时会话”分组查看 Session，各组按创建时间倒序；创建时选择“实时终端”或“快速交互”，并通用执行停止、归档、重命名和删除 | 首页、Session 页 |
| 需求储备 | 使用 R1–R9 保存、更新、查看、归档和删除轻量需求 | 命令行、微信 ClawBot |
| OpenClaw 与微信 | 查看并维护 Gateway 和微信通道；将可信微信私聊交给 Chub 固定路由或 Codex 任务；任务结束后按原路返回结果 | 首页、设置页、微信 ClawBot |
| 自动化任务 | 管理独立 Debug Chrome，复用登录状态运行配置驱动任务；当前支持飞书 Wiki Markdown 下载和周报资料准备 | 首页、自动化页、命令行 |
| 周报 | 校验当期资料和周期，确认重点后生成、复核并展示正式周报 | 自动化页、周报页 |
| 通知 | 向预配置的飞书目标发送有界纯文本通知，不接受任意目标或 Webhook | 命令行、Chub API、OpenClaw Tool |
| 项目资料 | 在可信网络内查看已登记的项目说明、设计方案与维护文档，并由维护入口控制首页显示 | 首页、项目资料页 |
| 任务编排 | 调整微信普通任务的执行前润色、模型和推理等级 | 首页 AI Session 分组下的微信任务润色配置页 |
| 外观 | 切换 Standard、Code Dark、Studio Cyan 主题，以及小、默认、大三档文字大小 | 设置页 |

页面、微信和翻译快速任务统一由独立 Worker 承载，Web 重启不会中断已接受的任务。Worker 服务、恢复、通知和协调重启的完整边界见[Chub Quick Worker 独立服务设计](docs/CHUB_QUICK_WORKER_DESIGN.md)。

电脑端命令、插件和固定 API 的查询入口是 [Chub 集成能力清单](docs/CHUB_INTEGRATION_CAPABILITIES.md)；其中第 4 节是微信固定指令的唯一产品契约。身份、安全、路由和并发等内部边界由对应设计文档维护。

## 快速开始

前置条件：Python 3.12 或更高版本。先使用 `python3 --version` 确认当前命令满足要求，
再创建项目虚拟环境。

创建环境并安装依赖：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

按平台创建本机配置：

```bash
cp config/settings.example.yaml config/settings.local.yaml
```

启动服务：

```bash
.venv/bin/python main.py
```

`config/settings.local.yaml` 只保存本机配置，不应提交。主要配置包括：

- `app.page_title`：浏览器标签和首页标题；省略时使用应用名称。
- `security.allow_tailscale`：默认开启，允许真实 Tailnet socket 来源访问受保护接口；不信任客户端转发 Header，也不识别具体用户。
- `ai_runtime.codex`：Codex Runtime 的部署可用性、工作目录、运行数据目录、终端票据、并发和快速交互超时等部署级配置。设置页的“接收新任务”是独立运行策略，不会改写此处的 `enabled`。旧顶层 `codex_pty` 已移除。
- “设置 → AI Runtime → 通用配置”维护已接入 Runtime 共用的用量时区；Codex 的 Sub2API 用量来源在“设置 → AI Runtime → Codex Runtime”维护。两类设置均保存于未提交的 `config/ai-runtimes.local.yaml`，按 `general` 与 Runtime ID 隔离；采集路径固定，不保存 Cookie、Authorization 或其他上游凭据。
- `openclaw.integration_config_path` 与 `openclaw.integration_state_dir`：仅当 OpenClaw Gateway 使用与 Chub 不同的 profile 或进程环境时，同时登记其实际配置文件和状态目录，供设置页读取插件配置与安装索引；不填写时按 Chub 进程可见的 OpenClaw 环境变量和默认位置推导。

Chub 始终监听 `127.0.0.1:<port>`，供本机浏览器与同机 OpenClaw 使用。每次启动时，Chub 通过固定 `tailscale ip -4` 查询本机当前地址，并在每个可用地址的 `server.port` 上自动增加 Tailnet 监听；不再维护固定 Tailnet 地址配置。若没有可用地址或地址当前不可绑定，Chub 保持 loopback 可用并在首页标记远程访问不可用，不会因 Tailnet 监听失败停止控制面，也不会影响本机 Codex Runtime、快速任务或本机实时终端。工作台会显示实际成功监听的 Tailnet 地址和端口；Tailscale 地址在 Web 运行期间变化时，需重启 Web 重新建立监听。不要配置 `0.0.0.0` 或普通局域网地址。Tailnet 请求只信任真实 socket 来源，不信任客户端转发 Header；如果 Tailnet 将来加入其他人的设备，应重新评估设备级授权和高风险操作边界。

Android 手机访问使用 Tailscale Android 客户端连接同一 Tailnet，再用 Android Chrome 打开节点的 Tailscale IP 和端口；Chub 当前提供的是移动浏览器页面，不是原生 Android 应用或离线 PWA。Android 端可使用首页、设置、日志和快速交互等 Web 能力，实时终端仍依赖稳定的 WebSocket 和可信 Tailnet 连接；首次验收应覆盖 Chrome 的窄屏布局、软键盘展开/收起、横向无溢出和断线重连。

## 后台服务

从当前开发目录安装用户级后台服务：

```bash
./scripts/chub install
```

安装后可使用：

```bash
chub start
chub stop
chub restart
chub status [--verbose]
chub check
chub worker-health
chub worker-drain
chub worker-reload
chub worker-recover
chub worker-start
chub worker-stop
chub worker-service-status
chub network-restart
chub logs [web|worker|upgrade]
chub version
chub uninstall
```

macOS 使用 Chub、Chub Quick Worker 和系统升级执行器三个独立 LaunchAgent，Ubuntu 使用 Chub、Chub Quick Worker、Chub Debug Chrome
和系统升级执行器四个独立 systemd user service。`chub start`、`chub stop` 和 `chub restart` 都只控制 Chub Web，不启动、停止或要求 Chub Quick Worker 空闲；Quick Worker 使用 `worker-start`、`worker-stop`、`worker-reload` 或 `worker-recover` 独立维护。`chub status` 默认以简洁摘要显示
Web、Quick Worker、Debug Chrome 与系统升级执行器的最终可用状态；排障时使用 `chub status --verbose` 查看平台服务管理器的原始详情。
`chub check` 汇总项目配置、服务、Web、Quick Worker 和系统状态并在检查失败时返回非零码；
`chub worker-health` 通过本机私有 IPC 读取 Worker 健康信息；`chub worker-drain` 停止接收新任务并等待已受理任务收敛；`chub worker-reload` 关闭新提交、清理排队和执行中的任务、独立重载 Worker 并确认新 generation 健康；`chub worker-recover` 保留为本机服务恢复入口；`chub worker-start` 和 `chub worker-stop` 只控制 Quick Worker，启动或停止后确认服务状态，停止要求没有在途任务。重启成功日志会记录 generation、协议和空闲核验摘要。`worker-drain`、`worker-reload`、`worker-recover`、`worker-start` 和 `worker-stop` 只在本机终端执行，不能从正在运行的快速任务内部调用；首页核心服务卡对 Chub 和 Quick Worker 仅提供“重启”，停止态的 Worker 也通过“重启”恢复。当前 Worker 已经接管页面、微信和翻译快速任务；
macOS 与 Ubuntu 均已确认单独重启 Web 不会停止 Worker、Runner 或现有任务，恢复后的结果和通知不会重复。
Worker 不健康时，快速交互 Session 写操作保持失败关闭，不回退到 Web Runner；实时终端使用独立的 Codex PTY/tmux 链路。
Ubuntu 的自动化 Debug Chrome 由独立 Supervisor 持有，Web 只通过本机受限 socket 请求启动、停止和查询；Supervisor
不可用时自动化浏览器控制失败关闭，不回退为 Web 子进程启动。`chub uninstall` 会停止该 Supervisor
及其受管浏览器；macOS 继续使用现有 LaunchAgent 与浏览器生命周期。Debug Chrome 的服务定义和恢复由自动化维护入口单独处理；系统升级与 Worker 恢复不会启动、停止或修复该服务。首次安装 Chub 或服务定义发生变化时，仍需执行一次 `chub install`，以安装独立的系统升级执行器。

`chub install` 和 `chub uninstall` 仍默认拒绝中断活动或排队任务，并在空闲时先关闭 Worker 提交门禁；`chub stop` 仅停止 Chub Web，不对 Worker 施加前置条件。Quick Worker 专用的 `worker-reload` 是恢复入口，确认后会取消排队任务、停止执行中任务并重建 Worker，不自动重放。跨协议升级统一使用首页或微信 `upgrade` 的系统升级与恢复流程，直接清理旧 Worker 数据并确认目标协议；旧任务会被中断且不可恢复，不保留读取或迁移逻辑。
`chub system-upgrade-service` 是独立的本机恢复入口，只安装或恢复升级执行器；发现未完成的升级操作时会启动该执行器，不停止无关服务。
首次安装、移动项目目录或修改 Web/Worker/Chrome/系统升级服务定义后，必须先从本机终端执行 `chub install`；如果已有未完成的升级操作，`chub install` 会安全拒绝，此时先执行 `chub system-upgrade-service` 恢复操作，待最终状态确认后再重新安装服务定义。

`chub logs` 默认跟随 Web 日志，也可明确选择 `worker` 或 `upgrade`；`chub version`（或 `chub --version`）显示当前本机版本与平台。当前 `chub` CLI 是仓库内的本机服务管理入口；项目尚未发布 npm/PyPI 或独立发行包。服务直接依赖当前工作区和 `.venv`；移动目录或变更服务定义后需要重新安装。Web 配置变更使用
`chub restart` 生效；Worker 代码升级使用 `chub worker-reload`，Worker 失败恢复使用首页入口或 `chub worker-recover`，只有服务定义变化时才需要重新执行安装。

## 日常使用

### Codex 会话

Codex PTY 依赖 `codex`、`ttyd` 和 `tmux`。安装服务时，Chub 会从当前终端 `PATH` 记录所需程序路径。

普通 Chub 重启只重启 Chub 控制面，不停止独立的 Chub Quick Worker 或 OpenClaw Gateway；已接受的快速任务继续运行。它会关闭并在需要时重建 Chub 自己的 `ttyd` Web 桥，但保留固定 tmux 和原生 Codex；再次进入同一
实时 Session 时会重新连接原 tmux。升级/恢复清理 Chub 运行态后，新实例按升级操作保存的旧逻辑 ID
与原生 Session ID 关联，自动重新绑定仍存在的 Chub tmux。
原生 Codex 不会因此被重启；若进程仍携带旧的 Chub 标识，Hook 会通过受控别名把 Activity 事件写入新的 Session。

首页使用一个“新建会话”按钮和弹窗创建 Session，默认创建快速交互；弹窗中的“实时会话”开关打开时才创建实时终端。首页统一按创建时间倒序展示 Session，并按“快速会话”和“实时会话”分组：

- **实时终端**：使用原生 Codex TUI，适合审批、持续操作和实时输出；只显示在 Chub Web。
- **快速交互**：提交后台任务并在时间线查看状态和结果，适合手机、普通网络和微信入口；只使用 Quick Worker。

AI Runtime 是两类新建会话的共同前提。Quick Worker 仅承载快速交互：Worker 不可用但 Runtime 可用时，首页锁定为实时会话，仍可创建并进入实时终端；快速交互页和微信 `new` 则拒绝新建快速会话。没有可用 AI Runtime 时，三个入口都不能创建会话。模型和推理等级默认跟随 AI Runtime；快速交互中选择的模型和等级只保存到当前 Chub Session，并影响该 Session 后续任务。Session 类型创建后不可切换；同一 Session 不会在两类入口间共享 writer。微信和 ClawBot 只使用快速交互 Session，实时终端 Session 不进入微信槽位或手机快速交互列表。

Session、Activity、usage 投影、槽位、标题和入口语义见[Chub AI Session 状态模型设计](docs/AI_SESSION_STATE_DESIGN.md)。后台任务、通知终态和 Worker 服务恢复见[Chub Quick Worker 独立服务设计](docs/CHUB_QUICK_WORKER_DESIGN.md)。

实时终端依赖稳定的 WebSocket 链路。普通页面可以打开但终端无响应时，应优先检查 Tailscale 是否直连、DERP 中继质量，以及 `/codex/.../terminal/ws` 是否成功升级为 `101 Switching Protocols`。

### 轻量需求储备

轻量需求储备使用 `R1`–`R9` 九个活动槽位，不创建专用 Session。维护者明确要求保存或更新已经讨论成型的小需求后，编码 Agent 通过本机 `chub request save` 或 `chub request update` 受控写入；`chub request list` 和 `chub request show` 用于检查活动需求。需求权威文件位于 `data/shared/chub/requests.json`，属于 Chub 共享资料，可由维护者通过 Git 提交和同步；Chub 不自动执行 `pull`、`commit` 或 `push`。保存和更新从标准输入读取完整正文，必须保留真实换行，不得将换行写成字面量 `\\n`；写入后应使用 `chub request show RN` 检查段落格式。不直接编辑状态文件。

微信 Chub 模式可在状态摘要中查看活动需求，并归档或删除指定需求。需求执行由维护者在 AI 对话中发送普通任务完成，不提供微信固定执行指令。完整的本机命令、微信语法、长度限制和失败语义见[Chub 集成能力清单](docs/CHUB_INTEGRATION_CAPABILITIES.md)；OpenClaw 定制、微信路由、持久化和通知边界见[OpenClaw 定制集成设计](docs/OPENCLAW_CUSTOMIZATION_DESIGN.md)。

### AI 额度

`/api/ai/usage` 为受保护的统一用量查询接口，供微信状态、Session 回执、任务通知及受控调用方读取当前 Runtime 的周额度、今日用量和重置时间；当前首页不展示独立额度卡片。ChatGPT 账号登录优先使用账户日桶，当天桶缺失时返回明确标记的本机 Token；Codex 的 API Key 方式复用已登录的受管 Debug Chrome。两种来源不会互相降级，完整数据口径、接口和缓存规则见[Codex AI 额度与用量采集设计](docs/CODEX_AI_QUOTA_USAGE_DESIGN.md)。

### OpenClaw 与微信 ClawBot

微信设备任务固定经过：

```text
微信 ClawBot → OpenClaw → Chub → OpenClaw → 微信 ClawBot
```

OpenClaw 提供可信入口和通道上下文，Chub 负责业务路由、安全校验和最终状态；整条链路不调用 OpenClaw Agent，也不把消息拦截或任务提交等同于最终成功。

首页“工作站环境”中的“第三方服务环境”独立展示 Gateway 和微信 ClawBot 状态，并提供受控的启动、重启及微信绑定：Gateway 停止时只显示启动，运行或降级时只显示重启；确认弹窗保留“重启与恢复”的完整影响说明。其刷新和失败反馈不影响 Chub、Quick Worker 与升级恢复状态；启动或重启成功后会写入当前浏览器会话缓存，重新进入工作台先展示这次最终状态，再在后台重新核验。设置页“第三方服务”的 OpenClaw 只核对本机 OpenClaw 配置、插件安装元数据和固定清单，不读取或展示 Gateway 信息，也不启动 OpenClaw CLI。Gateway 运行状态只在首页工作站环境查看。补丁列表只表示 Chub 已验收基线中的清单登记，补丁内容和运行时加载状态只在“重启与恢复”流程核验；设置页不再重复提供运行操作。微信普通任务的文本处理方式、文本优化模型和推理等级位于首页 AI Session 分组下“任务编排”内的微信任务润色配置页。诊断响应不展示运行目录、来源路径或完整性数据。设置页“维护与版本”的维护终端是 Chub 自有的独立 `ttyd`/`zsh` 页面：它固定从当前项目目录启动，允许本机或受信 Tailnet 浏览器执行任意 Shell 命令，不依赖或代理 OpenClaw。该入口等同于当前设备用户 Shell 权限；Tailnet 访问者获得同等权限，维护者必须只允许可信设备加入 Tailnet。重启与恢复只会同步完整已验证基线中的固定插件和补丁，再重启 Gateway 和消息通道；候选版本的局部兼容补丁必须按定制设计人工复检，不会因重启而自动应用。底层 API action 仍使用 `restart`，微信端使用 `restart clawbot`。绑定成功只表示通道登录完成，不代表发送者配对或 Owner 权限已经配置。

微信 Chub 固定维护指令为 `restart` / `restart web`（重启 Web）、`restart worker`（重启 Worker）、`restart clawbot`（重启 ClawBot）、`restart network`（重连已固定的 Ubuntu Wi-Fi/VPN）和 `upgrade`（升级系统）；均按固定目标执行，不接受附加路径或命令。网络重连与本机 `chub network-restart` 共用同一配置及终态校验：仅 Ubuntu 的 NetworkManager 可用，macOS 会明确拒绝，不会尝试切换网络；Tailscale 不在操作范围。各操作的最终结果分别以新实例、Worker、Gateway/消息通道、NetworkManager 活动连接或升级运行态确认结果为准；异步操作通过独立通知返回。

端到端状态和安全边界见 [OpenClaw 定制集成设计](docs/OPENCLAW_CUSTOMIZATION_DESIGN.md)；插件协议、构建和部署见仓库内维护资料 [Chub OpenClaw 插件说明](integrations/openclaw/chub/README.md)。

### 飞书通知

飞书目标登记在 `~/.config/chub/notifications/registry.yaml`，Webhook 保存在
`~/.config/chub/notifications/secrets/` 下权限为 `600` 的独立文件中。通知只允许预配置目标、有界纯文本和已登记人员；调用方不能指定任意 URL、Open ID 或 Secret 路径。

首次配置可从不含真实凭据的示例开始：

```bash
mkdir -p ~/.config/chub/notifications/secrets
chmod 700 ~/.config/chub/notifications ~/.config/chub/notifications/secrets
cp -n config/notifications.example.yaml ~/.config/chub/notifications/registry.yaml
touch ~/.config/chub/notifications/secrets/test.webhook
chmod 600 \
  ~/.config/chub/notifications/registry.yaml \
  ~/.config/chub/notifications/secrets/test.webhook
```

随后将完整飞书机器人 Webhook URL 作为唯一一行写入 `test.webhook`。需要指定人员时，
在 registry 的 `recipients` 中使用本机别名登记对应 Open ID；真实 Webhook、Open ID 和
registry 均不得提交到仓库。

本机可使用以下命令校验配置：

```bash
chub notification validate
chub notification list
chub notification test --target test
```

`test` 会真实发送固定测试消息。Codex 实时终端和快速交互直接使用 `chub notification send`；OpenClaw TUI 和微信入口使用 `chub_send_notification`。

### 自动化与周报

Chub 使用独立 Debug Chrome 执行配置驱动的浏览器任务。公共任务维护在 `config/automations.yaml`，本机任务维护在不提交的 `config/automations.local.yaml`：

公共配置允许只保留 `version: 2` 和空的 `tasks:`，这表示当前没有需要跨节点共享的任务；本机任务仍会正常加载。

```bash
cp config/automations.example.yaml config/automations.local.yaml
```

自动化任务卡片的“自动化环境”管理 Chub Debug Chrome、检查飞书登录状态并运行任务；命令行也可调用统一 Runner：

```bash
.venv/bin/python -m app.automations.command run <task-id>
```

Runner 不会自行启动或停止 Debug Chrome。浏览器 Profile 未初始化、端口冲突或环境正被使用时会明确失败。周报资料下载、周期校验、人工重点确认和正式生成规则见 [本期工作周报自动化与生成设计](docs/WEEKLY_REPORT_AUTOMATION_DESIGN.md)。

### 微信正文处理

正文处理设置下的翻译模型和等级独立于隐藏翻译 Session，只影响之后新提交的文本优化任务。任务提交时保存参数快照，已经进入队列的任务不因设置变化而改写；未选择模型和等级时跟随 Runtime 默认。

首页 AI Session 分组下“任务编排”内的微信任务润色配置页是节点级设置，需要真实 loopback 或可信 Tailnet 访问。微信普通任务及携带正文的 `S1`–`S9` 支持三档：`直接执行`直接提交原文；`自动润色后执行`先在独立只读 Session 生成中文润色和 English，再自动提交润色中文；`自动润色后确认执行`生成同一份内容后发送 `Translation ready`，由维护者用 `text ok`、`text next`、`text cancel` 或英文复述处理确认队头。自动润色与确认模式在原消息、固定目标和回送路由已持久化进入 Chub 队列后即受理，隐藏 Worker 交接不会阻塞微信同步回执；后续交接或润色失败会单独通知，且不会执行原文。正文超过 `translation_preprocess_max_input_chars`（默认 1200 字符）时直接提交，不进入润色、翻译或确认流程。主任务实际被接收后才发送包含润色中文和 English 的 `Started`；确认模式在受理后异步投递该通知，不增加单独的“Preparing to submit”回执。固定指令和续提指令绕过该流程，具体指令、队列和失败边界以[集成能力清单第 4 节](docs/CHUB_INTEGRATION_CAPABILITIES.md#4-微信-clawbot-指令)为准。

首次默认由 `translation_mode`（`direct` / `auto` / `confirm`）决定；未设置时兼容读取旧 `translation_enabled`（`false` 为直接执行，`true` 为自动润色后执行）。页面修改后由 Chub 私有状态持久化并即时生效，无需改写 YAML 或重启服务。

### 外观

设置页“外观”提供 `Standard`、`Code Dark`、`Studio Cyan` 三套主题，以及小（90%）、默认（100%）、大（110%）三档文字大小。偏好保存在当前浏览器，终端不受影响；主题预览、持久化、扩展规则和验收边界见下方前端设计文档。

完整视觉规范见 [Chub 前端 UI 模块化设计](docs/FRONTEND_UI_DESIGN.md)。

## 数据与安全

`data/` 按共享资料和本机运行态分开：

- `data/shared/`：允许进入 Git 的 Chub 共享资料；当前仅包含 `data/shared/chub/requests.json` 需求储备。
- `data/local/state/`：本机专属会话、任务、通知和页面状态，不提交。
- `data/local/runtime/`：本机可重新生成的执行事件、缓存、锁和任务日志，不提交。
- `data/local/artifacts/`：本机自动化下载材料和周报正式产物，不提交。

共享需求文件由 Git 工作流同步，不由后台服务自动合并。多台设备同时修改可能产生 Git 冲突；发现冲突、非法 JSON 或未完成合并时，需求读写必须失败关闭，不覆盖其他设备内容。共享需求不得保存 Token、Cookie、账号凭证、本机秘密或其他不适合进入 Git 历史的内容。

首页工作站环境包含 Chub、Quick Worker 和“升级与恢复”；其中“第三方服务环境”是独立状态分区，单独承载 OpenClaw Gateway/微信 ClawBot 的启动、重启、微信绑定和状态刷新。自动化任务的“自动化环境”继续承载 Debug Chrome。

“升级与恢复”只重建 Chub 自有 AI 运行态、Chub Web 与 Quick Worker：持久化操作、冻结新写入、有界收敛 Quick Worker、清理本次范围内的旧运行态，再由独立执行器恢复 Web/Worker，并确认新实例、协议、Worker 健康和写入恢复。Chub 行展示当前控制面状态，Quick Worker 行只展示 Worker 当前状态；其下方 AI Runtime 行按注册顺序展示每个 Runtime 的可用、已停用或不可用状态，并在页面加载和核心服务刷新时重新读取。没有注册项时显示“未配置 AI Runtime”，读取失败时显示“AI Runtime 状态暂无法确认”；这些 Runtime 状态不改变 Worker 或核心恢复成功结论。升级行没有版本或协议差异时显示“状态：无待升级”，有差异时只展示 Chub 版本、Session 数据或 Worker 协议的升级点；升级方案不可读取时显示“状态：仅可恢复。升级方案不可用”。完整影响说明保留在确认弹窗与日志。每个组件的固定结果会随升级操作 ID 写入操作日志，供多轮验收追溯，不作为首页状态。它不重建 Codex 原生 Session、用户配置、日志、项目资料或业务数据。OpenClaw Gateway、插件、补丁、消息通道、Debug Chrome 和 Python 依赖安装都不属于核心升级步骤或完成条件，应由各自维护入口单独处理。

当前核心服务维护只有三项：`Chub` 重启重建控制面，`Chub Quick Worker` 重启重建执行面，升级与恢复统一重建 Chub 自有 AI 运行态并切换 Web/Worker。三者只在直接冲突时互斥；普通 Chub 重启不要求 Worker 空闲，也不会停止 Worker 或已接受任务。核心服务卡只表达当前状态和可执行操作，历史升级结论、组件结果与操作 ID 统一从日志查询。

升级执行器不调用 OpenClaw CLI，也不读取或写入 OpenClaw 插件、补丁、Gateway 或消息通道状态。升级方案不可用时仍按固定规则执行当前版本运行态恢复，并明确不执行代码版本升级。该入口不执行 Git 同步、不接受客户端路径或命令，也不提供任意数据清理。

升级失败后，能够从持久化安全检查点继续的操作会保留“继续恢复”入口；无法安全继续时入口关闭，并明确提示在本机终端检查 `chub logs upgrade` 和服务定义。普通 Chub 或 Quick Worker 重启不能替代该恢复步骤。

最终确认 Quick Worker 健康后，若存在更早的独立 Worker 重启终态记录，系统会将其从当前状态投影中清理；历史操作日志仍保留，失败中的新操作不会被升级成功结果掩盖。

`scripts/chub-data-migrate` 只保留给历史安装的数据目录整理，不参与 AI Session 或 Worker 协议升级。新的持久化协议切换统一使用上述受控升级流程，直接清理方案白名单内的旧运行数据，不增加启动迁移、双写或旧格式兼容读取。脚本会同时迁移微信 Chub 模式和正文处理的私有状态；历史需求储备迁移到 `data/shared/chub/requests.json` 时，只合并不冲突的槽位。非法内容、重复槽位或同一槽位内容冲突会停止并保留原文件，不能覆盖共享资料。

执行历史目录迁移后，维护者必须检查未提交的 `config/settings.local.yaml`：删除已废弃的 `openclaw.weixin_chub_mode.request_state_file`，并确认顶层 `requests.state_file` 指向 `data/shared/chub/requests.json`；同时确认 Codex、自动化和项目资料路径已指向 `data/local/`。脚本不会覆盖配置文件；共享需求冲突或非法时保留原文件，并拒绝符号链接。

受保护接口只接受真实 loopback socket，或在未关闭时接受真实 Tailnet socket 来源；其他来源拒绝。健康检查和项目资料详情是可信网络内的只读页面；公开展示的文档和周报不得包含 Token、Cookie、账号信息、本机秘密或其他不适合直接访问的内容。

主要接口类别：

- `/api/health`、`/api/status`：健康和节点状态。
- `/api/codex/*`：当前 Codex 会话与快速交互的正式入口；不是通用 Runtime 选择或旧版本兼容别名。
- `/api/ai/usage`：统一 AI 额度、今日用量和重置时间。
- `/api/automations/*`：自动化环境、任务和结果。
- `/api/openclaw/*`：OpenClaw 状态与受控操作。
- `/api/logs`、`/api/maintenance/*`：日志和节点维护。

## 项目资料维护

README 是项目入口和文档管理规则的维护入口；总体架构是所有专项设计的上层约束。文档按以下权威层级维护：

文档的第一读者是 AI Agent，第二读者是项目维护者。每份当前文档的开头必须先说明“项目/功能是什么、本文负责什么、哪些内容不在本文范围”；关键规则、状态所有权、失败关闭条件和验收标准必须用明确的规范性语言保留，不能只写背景或实现过程。维护者操作步骤、版本记录和历史说明放在核心契约之后，且不得与 AI 可执行规则混在一起。

当前专项文档统一使用最低头部结构：`状态`、`主要读者`、`本文负责`、`本文不负责`；`状态` 必须与 `docs/design_documents.json` 的标准值精确一致，维护触发条件另写为补充说明。已验收的当前专项文档末尾还应写明已验证功能/平台、未验证或不承诺范围和复检触发条件；局部实机验证不能扩大为全平台或全链路承诺。

- **项目说明**：README 维护项目定位、能力概览、安装、使用入口、数据安全和文档导航。
- **总体架构**：定义系统边界、进程、领域、状态所有权、依赖方向和跨模块约束；所有专项设计必须遵循。
- **专项设计**：只维护本领域的现行行为、目标边界和演进方案；跨领域规则引用总体架构及对应权威文档，不复制完整正文。
- **当前能力契约**：集成能力清单登记当前可调用的命令、插件和固定 API，并维护微信固定指令的唯一产品契约；目标架构不能覆盖当前事实。
- **维护与归档资料**：插件 README 维护插件协议、源码、构建和部署；第三方补丁文档只维护异常恢复；阶段归档只用于历史追溯。
- 文档的页面登记、摘要和展示状态以 `docs/design_documents.json` 为准；各文档顶部可以补充维护与验收说明，但不得与索引状态冲突。

文档生命周期：

- **当前文档**：仍描述当前有效的需求、架构、功能或流程，已实现或已验收不代表需要归档。
- **阶段记录**：阶段已经闭环，但仍被当前工作引用或尚未被新文档替代。
- **归档文档**：只用于历史追溯，移动到 `docs/archive/phase-N/` 并原则上冻结。

项目资料页面展示的当前文档统一登记在 `docs/design_documents.json`，完整列表按索引顺序遵循“项目说明、总体架构、专项设计、当前能力契约、维护资料”的权威层级。首页未隐藏文档中固定优先展示项目说明和总体架构，其余位置按文件最后更新时间倒序补足五份。普通文档使用相对于 `docs/` 的 Markdown 路径；项目根 README 使用唯一保留别名 `@project/README.md`，不能借此读取其他根目录文件。索引状态只使用“调研中”“待实现”“进行中”“待验收”“已验收”或“持续维护”。

项目资料页的“隐藏/恢复显示”只控制首页展示，状态保存在本机私有运行数据中，不移动、冻结或改写仓库文档。生命周期归档仍需把历史文档移动到 `docs/archive/phase-N/`，并同步索引和相关引用。

插件 README、兼容跳转页、`.agents/skills/` 下的技能文档和 `docs/archive/` 下的阶段记录属于仓库内维护资料，不进入可信网络只读的项目资料页面，也不扩展 `@project/` 文件读取范围。兼容跳转页只保留旧链接跳转，不登记为当前权威文档。README 在项目资料页渲染时不会为这些仓库内目标提供下钻；需要查看时从工作区 Markdown 导航进入。

## 测试

```bash
.venv/bin/python -m pip install -e ".[test]"
.venv/bin/python -m pytest
```

全量测试默认跳过需要受管 Chrome 的真实浏览器回归。调整工作站、主题或快速交互页面时，启动隔离 Debug Chrome 后显式执行：

```bash
python3 .agents/skills/chrome-cdp/scripts/chrome_debug.py start --headless
CHUB_BROWSER_TESTS=1 .venv/bin/python -m pytest \
  tests/test_workspace_browser.py \
  tests/test_quick_interaction_browser.py
```

测试涉及服务定义、服务日志或平台维护脚本时，必须使用临时 `HOME`、`CHUB_SYSTEMD_USER_DIR` 和 `CHUB_SERVICE_LOG_DIR`；替身服务管理器只能写入测试临时目录，不得写入真实 `~/.config/systemd/user`、本机日志或运行态。

## 核心项目文档

| 文档 | 唯一职责 |
| --- | --- |
| [Chub 项目说明](README.md) | 项目概览、安装、日常入口、安全和文档导航 |
| [Chub 总体架构设计](docs/CHUB_ARCHITECTURE_DESIGN.md) | 核心、AI Runtime 与第三方服务三层架构、状态所有权和跨模块约束 |
| [Chub AI Runtime 架构设计](docs/CHUB_AI_RUNTIME_DESIGN.md) | AI Runtime 架构、Session Manager、Worker 职责和 Runtime 实现规范 |
| [本机大模型部署设计](docs/LOCAL_LLM_LEARNING_DEPLOYMENT_DESIGN.md) | 当前 MacBook 上独立的 Ollama 本机模型部署、常用 API、资源边界、阶段和验收 |
| [Chub AI Session 状态模型设计](docs/AI_SESSION_STATE_DESIGN.md) | Session、Activity、usage 投影、入口、操作、槽位和单 writer 语义 |
| [Chub Quick Worker 独立服务设计](docs/CHUB_QUICK_WORKER_DESIGN.md) | Quick Worker 独立服务、非实时任务、恢复、通知终态和重启协调 |
| [Codex AI 额度与用量采集设计](docs/CODEX_AI_QUOTA_USAGE_DESIGN.md) | Codex/OpenAI 用量来源、统一接口、缓存和展示口径 |
| [OpenClaw 定制集成设计](docs/OPENCLAW_CUSTOMIZATION_DESIGN.md) | OpenClaw/微信端到端业务、插件定制、Context Token、身份、路由和通知边界 |
| [本期工作周报自动化与生成设计](docs/WEEKLY_REPORT_AUTOMATION_DESIGN.md) | 飞书资料准备、确认门禁、周报生成和复核 |
| [Chub 前端 UI 模块化设计](docs/FRONTEND_UI_DESIGN.md) | 前端分层、公共交互、主题、文字大小注册与视觉 Token 契约 |
| [Chub 集成能力清单](docs/CHUB_INTEGRATION_CAPABILITIES.md) | 当前可用命令、插件、固定 API，以及微信固定指令唯一产品契约 |
| [Chub OpenClaw 插件说明](integrations/openclaw/chub/README.md) | 仓库内维护的 Chub 插件协议、源码、构建、部署和协议验收 |

日常了解项目先看本文和总体架构；开发或排障时进入对应专项设计；确认“现在能调用什么”看能力清单。`docs/design_documents.json` 的登记顺序同时维护权威层级和完整列表顺序，首页在固定核心文档后提供最近更新视图。

仓库内阶段归档（仅供历史追溯；当前 Runtime 工作不再按阶段编号规划，也不存在后续第五阶段）：

- [第一阶段](docs/archive/phase-1/README.md)
- [第二阶段](docs/archive/phase-2/README.md)
- [第三阶段](docs/archive/phase-3/README.md)
- [第四阶段](docs/archive/phase-4/README.md)
