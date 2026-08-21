# Chub

## 项目介绍

Chub 是面向个人设备、本地优先的轻量 AI 工作站控制面。它在统一的安全与状态边界内组织设备能力、AI Session 和任务入口，负责接收请求、选择执行目标、协调运行、恢复任务、确认最终状态并交付结果；Chub 本身不是模型，也不作为通用对话 Agent 执行任务。

当前 Codex 是唯一完整接入的 Agent Runtime，负责实际的分析、编码和工具调用；Quick Worker 承载需要跨 Web 重启继续运行的后台 AI 任务；OpenClaw 在微信链路中只承担可信消息网关和通道适配。当前 Runtime 架构、能力矩阵和 AI Runtime 实现规范见 [Chub AI Runtime 架构设计](docs/CHUB_AI_RUNTIME_DESIGN.md)。具体业务能力和使用入口见“当前功能”，不在项目定位中重复列举。

长期目标是让 Chub 成为不依赖单一 Agent 产品的个人 AI 工作站：在保持统一安全、逻辑 Session、任务和最终状态语义的前提下，通过稳定契约接入经过验证的 Agent Runtime。项目继续坚持本地优先、可靠终态和适合个人维护的复杂度，支持 macOS LaunchAgent 与 Ubuntu systemd user service；新 Runtime 只由真实需求驱动，并在能力、权限和恢复机制通过验证后接入。

## AI Agent 快速理解

本文是 Chub 项目入口，负责项目定位、当前能力、运行入口、核心安全边界、架构概览和专项文档导航。本文不替代 Session、Worker、Runtime、OpenClaw、自动化、前端或用量等子模块设计，也不重复维护微信固定指令和用户可见回复格式；这些细节以对应专项文档和[集成能力清单](docs/CHUB_INTEGRATION_CAPABILITIES.md)为准。

按以下顺序判断项目行为：先用本文确认项目范围和当前能力，再用[Chub 总体架构与演进设计](docs/CHUB_ARCHITECTURE_DESIGN.md)确认进程、领域和状态所有权；需要确认“现在可以调用什么”时读取[集成能力清单](docs/CHUB_INTEGRATION_CAPABILITIES.md)；最后进入对应子模块文档。项目资料页面的登记、摘要和状态以 `docs/design_documents.json` 为准；“已验收”不等于归档。目标架构、阶段记录或历史归档不能覆盖当前能力契约。

当前不可放宽的边界：Chub 是控制面和可靠协调者，不是模型或通用 Agent；Codex 是当前唯一完整接入的 Runtime；每类状态只有一个权威来源，异步操作必须确认最终状态；客户端不能提供任意命令、路径、Runtime、Session 或收件人；认证、白名单和敏感信息保护失败时必须失败关闭，外部通道的身份、路由、协议或结果不确定时不得回退或放宽权限。具体身份、路由、权限、恢复和通知规则由对应专项文档维护。

## 架构与文档入口

本文用于说明 Chub 是什么、当前提供什么能力以及如何使用；[Chub 总体架构与演进设计](docs/CHUB_ARCHITECTURE_DESIGN.md)是项目的核心架构依据，定义系统边界、职责分层、状态所有权和演进原则。下面的架构和子模块索引只描述当前职责，不代表目标目录已经完全落地；新增功能和专项设计必须先遵循总体架构，再进入对应领域文档。

### 当前进程架构

```text
Browser / Mobile Browser / Chub CLI
  -> Chub Web、API、WebSocket 和固定本机入口
       |-> Quick Worker -> 固定 Runtime Runner -> Codex Runner
       |-> tmux / ttyd -> Codex 实时终端
       |-> OpenClaw Plugin -> 微信固定调度 API
       |-> Automation Runner -> Debug Chrome / 固定扩展
       `-> Notification Service -> 预配置飞书目标
```

Web 是控制面和业务协调入口；Quick Worker 独立负责需要跨 Web 重启继续运行的后台 AI 任务；Codex、OpenClaw、Chrome、飞书和操作系统服务都通过固定 Adapter、Manager 或脚本受控访问。进程生命周期、状态所有权和外部边界详见总体架构，不在此重复展开。

### 仓库子模块索引

| 路径 | 当前职责 | 细节入口 |
| --- | --- | --- |
| `app/api/`、`app/web/` | FastAPI 接口、页面、WebSocket 和项目资料展示 | [总体架构](docs/CHUB_ARCHITECTURE_DESIGN.md)、[前端 UI 设计](docs/FRONTEND_UI_DESIGN.md) |
| `app/ai_session/`、`app/codex/` | 逻辑 AI Session、实时终端和当前 Codex 正式入口 | [Session 状态模型](docs/AI_SESSION_STATE_DESIGN.md)、[AI Runtime 设计](docs/CHUB_AI_RUNTIME_DESIGN.md) |
| `app/ai_runtime/`、`app/quick_worker*.py` | Runtime 契约、固定 Runner、后台任务、租约、恢复和终态 | [AI Runtime 设计](docs/CHUB_AI_RUNTIME_DESIGN.md)、[Quick Worker 设计](docs/CHUB_QUICK_WORKER_DESIGN.md) |
| `app/automations/` | Debug Chrome、配置驱动自动化、下载产物和任务状态 | [总体架构](docs/CHUB_ARCHITECTURE_DESIGN.md)、[周报自动化设计](docs/WEEKLY_REPORT_AUTOMATION_DESIGN.md) |
| `app/notifications/`、`app/requests/`、`app/ai_usage/` | 通知、R1–R9 需求储备、Codex/OpenAI 用量 | [总体架构](docs/CHUB_ARCHITECTURE_DESIGN.md)、[能力清单](docs/CHUB_INTEGRATION_CAPABILITIES.md)、[额度设计](docs/CODEX_AI_QUOTA_USAGE_DESIGN.md) |
| `app/core/`、`app/tasks/` | 配置、安全、日志、平台检测、白名单维护任务 | [总体架构](docs/CHUB_ARCHITECTURE_DESIGN.md) |
| `app/services/` | 当前跨领域服务和协调逻辑；不是新的统一领域边界 | [总体架构](docs/CHUB_ARCHITECTURE_DESIGN.md#4-当前逻辑分层) |
| `integrations/openclaw/chub/` | Chub OpenClaw 插件源码、构建、部署和协议验收 | [OpenClaw 定制设计](docs/OPENCLAW_CUSTOMIZATION_DESIGN.md)、[插件说明](integrations/openclaw/chub/README.md) |
| `scripts/`、`config/`、`tests/`、`docs/` | 服务安装与维护、示例配置、回归测试和项目资料 | [总体架构](docs/CHUB_ARCHITECTURE_DESIGN.md)、本文“项目资料维护” |

## 当前功能

| 功能域 | 当前可用能力 | 主要入口 |
| --- | --- | --- |
| 设备管理 | 查看节点与系统状态，执行后端白名单维护任务，查看受限、脱敏的操作日志和运行日志 | 首页、设置页、日志页 |
| Codex 会话 | 在统一列表按创建时间倒序查看 Session；创建时选择“实时终端”或“快速交互”，并通用执行停止、归档、重命名和删除 | 首页、Session 页 |
| 需求储备 | 使用 R1–R9 保存、更新和查看轻量需求，并从微信提交到当前 Session 执行 | 命令行、微信 ClawBot |
| OpenClaw 与微信 | 查看并维护 Gateway 和微信通道；将可信微信私聊交给 Chub 固定路由或 Codex 任务；任务结束后按原路返回结果 | 首页、设置页、微信 ClawBot |
| 自动化任务 | 管理独立 Debug Chrome，复用登录状态运行配置驱动任务；当前支持飞书 Wiki Markdown 下载和周报资料准备 | 首页、自动化页、命令行 |
| 周报 | 校验当期资料和周期，确认重点后生成、复核并展示正式周报 | 自动化页、周报页 |
| 通知 | 向预配置的飞书目标发送有界纯文本通知，不接受任意目标或 Webhook | 命令行、Chub API、OpenClaw Tool |
| 项目资料 | 在可信网络内查看已登记的项目说明、设计方案与维护文档，并由维护入口控制首页显示 | 首页、项目资料页 |
| 执行设置 | 即时启停微信普通任务的执行前润色与翻译 | 设置页 |
| 界面风格 | 切换 Standard/Cyber 风格 | 设置页 |

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
- `ai_usage`：AI 额度供应商、地区和 API 方式的固定订阅页配置；不保存 Cookie、Authorization 或其他上游凭据。

Chub 始终监听 `127.0.0.1:<port>`，供本机浏览器与同机 OpenClaw 使用。`server.tailnet_host` 是可选的第二监听地址：设为节点自己的 Tailscale IP 后，Chub 才会同时监听该地址以供手机远程访问；留空则仅本机访问。不要配置 `0.0.0.0` 或普通局域网地址。Tailnet 请求只信任真实 socket 来源，不信任客户端转发 Header；如果 Tailnet 将来加入其他人的设备，应重新评估设备级授权和高风险操作边界。

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
chub status
chub worker-health
chub worker-drain
chub worker-reload
chub worker-recover
chub logs
chub uninstall
```

macOS 使用两个独立 LaunchAgent，Ubuntu 使用两个独立 systemd user service，分别承载 Web 和
Quick Worker。`chub restart` 只重启 Web，不停止 Worker；`chub status` 同时显示两个服务，
`chub worker-health` 通过本机私有 IPC 读取 Worker 健康信息；`chub worker-drain` 停止接收新任务并等待已受理任务收敛；`chub worker-reload` 完成排空、独立重载和新 generation 健康确认；`chub worker-recover` 只用于已有失败重启且 Worker 不可达时的固定服务恢复，并确认恢复后的健康状态。`worker-drain`、`worker-reload` 和 `worker-recover` 只在本机终端执行，不能从正在运行的快速任务内部调用；首页“工作站环境”在 Worker 健康且空闲时提供固定的受控重启入口，协议不兼容时同样可重启到当前版本。当前 Worker 已经接管页面、微信和翻译快速任务；
macOS 与 Ubuntu 均已确认单独重启 Web 不会停止 Worker、Runner 或现有任务，恢复后的结果和通知不会重复。
Worker 不健康时，快速交互 Session 写操作保持失败关闭，不回退到 Web Runner；实时终端使用独立的 Codex PTY/tmux 链路。

`chub install`、`chub stop` 和 `chub uninstall` 默认拒绝中断活动或排队任务，并在空闲时先关闭 Worker 提交门禁；只有维护者确认任务可以中断时才使用对应命令的 `--force`。跨协议升级使用 `chub install --force` 统一安装当前版本并直接清理旧 Worker 数据；旧任务会被中断且不可恢复，不保留读取或迁移逻辑。

当前 `chub` CLI 是仓库内的本机服务管理入口；项目尚未发布 npm/PyPI 或独立发行包。新设备的目标流程、核心 Chub（含自动运行的 Quick Worker）与可选 ClawBot 的职责、npm 发布、版本管理和 GitHub Release 目标见 [Chub CLI 分发、安装与发布设计](docs/CHUB_CLI_DISTRIBUTION_DESIGN.md)。服务直接依赖当前工作区和 `.venv`；移动目录或变更服务定义后需要重新安装。Web 配置变更使用
`chub restart` 生效；Worker 代码升级使用 `chub worker-reload`，Worker 失败恢复使用首页入口或 `chub worker-recover`，只有服务定义变化时才需要重新执行安装。

## 日常使用

### Codex 会话

Codex PTY 依赖 `codex`、`ttyd` 和 `tmux`。安装服务时，Chub 会从当前终端 `PATH` 记录所需程序路径。

节点页面使用一个“新建会话”按钮和弹窗创建 Session，可为新 Session 选择默认权限、模型和推理等级及固定类型。首页统一按创建时间倒序展示 Session，并以简洁标记区分类型：

- **实时终端**：使用原生 Codex TUI，适合审批、持续操作和实时输出；只显示在 Chub Web。
- **快速交互**：提交后台任务并在时间线查看状态和结果，适合手机、普通网络和微信入口；只使用 Quick Worker。

新建 Session 默认选择快速交互。Session 类型创建后不可切换；同一 Session 不会在两类入口间共享 writer。微信和 ClawBot 只使用快速交互 Session，实时终端 Session 不进入微信槽位或手机快速交互列表。

Session、Activity、槽位、标题和入口语义见[Chub AI Session 状态模型设计](docs/AI_SESSION_STATE_DESIGN.md)；后台任务、通知终态和 Worker 服务恢复见[Chub Quick Worker 独立服务设计](docs/CHUB_QUICK_WORKER_DESIGN.md)。

实时终端依赖稳定的 WebSocket 链路。普通页面可以打开但终端无响应时，应优先检查 Tailscale 是否直连、DERP 中继质量，以及 `/codex/.../terminal/ws` 是否成功升级为 `101 Switching Protocols`。

### 轻量需求储备

轻量需求储备使用 `R1`–`R9` 九个活动槽位，不创建专用 Session。维护者明确要求保存或更新已经讨论成型的小需求后，编码 Agent 通过本机 `chub request save` 或 `chub request update` 受控写入；`chub request list` 和 `chub request show` 用于检查活动需求。保存和更新从标准输入读取完整正文，不直接编辑状态文件。

微信 Chub 模式可在状态摘要中查看活动需求，并将指定需求提交到当前 Session 或归档非运行需求。完整的本机命令、微信语法、长度限制和失败语义见[Chub 集成能力清单](docs/CHUB_INTEGRATION_CAPABILITIES.md)；OpenClaw 定制、微信路由、持久化和通知边界见[OpenClaw 定制集成设计](docs/OPENCLAW_CUSTOMIZATION_DESIGN.md)。

### AI 额度

首页 Codex 卡片通过统一的 `/api/ai/usage` 展示周额度、今日用量和重置时间。ChatGPT 账号登录优先使用账户日桶，当天桶缺失时显示明确标记的本机 Token；API Key 方式复用已登录的受管 Debug Chrome。两种来源不会互相降级，完整数据口径、接口和缓存规则见[Codex AI 额度与用量采集设计](docs/CODEX_AI_QUOTA_USAGE_DESIGN.md)。

### OpenClaw 与微信 ClawBot

微信设备任务固定经过：

```text
微信 ClawBot → OpenClaw → Chub → OpenClaw → 微信 ClawBot
```

OpenClaw 提供可信入口和通道上下文，Chub 负责业务路由、安全校验和最终状态；整条链路不调用 OpenClaw Agent，也不把消息拦截或任务提交等同于最终成功。

首页“工作站环境”卡片中的 OpenClaw 分区用于查看 Gateway、微信通道和 Tailscale 入口，并提供受控的启停、重启及微信绑定操作。绑定成功只表示通道登录完成，不代表发送者配对或 Owner 权限已经配置。

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

```bash
cp config/automations.example.yaml config/automations.local.yaml
```

首页“工作站环境”可管理浏览器环境和检查站点登录状态，“自动化任务”卡片用于运行任务；命令行也可调用统一 Runner：

```bash
.venv/bin/python -m app.automations.command run <task-id>
```

Runner 不会自行启动或停止 Debug Chrome。浏览器 Profile 未初始化、端口冲突或环境正被使用时会明确失败。周报资料下载、周期校验、人工重点确认和正式生成规则见 [本期工作周报自动化与生成设计](docs/WEEKLY_REPORT_AUTOMATION_DESIGN.md)。

### 微信执行前润色

设置页的“自动润色后执行”是节点级设置，需要真实 loopback 或可信 Tailnet 访问。开启后，微信普通任务先在独立只读 Session 生成中文润色和 English，再把润色后的中文提交到原目标 Session；翻译受理后不发送处理中回执，主任务被接收后发送包含实际提交文本和 English 的 `Started` 通知。固定指令绕过该流程，具体指令和匹配边界以[集成能力清单第 4 节](docs/CHUB_INTEGRATION_CAPABILITIES.md#4-微信-clawbot-指令)为准。首次状态由本机配置的 `translation_enabled` 决定，页面修改后由 Chub 私有状态持久化并即时生效，无需改写 YAML 或重启服务。

### 界面风格

设置页可选择 `Standard`（简约标准版）或 `Cyber`（科技终端版），偏好保存在当前浏览器并应用到公共页面。Standard 是新增页面和功能的默认基线；Cyber 保持相同的信息结构、安全边界、键盘操作和响应式能力。

完整视觉规范见 [Chub 前端 UI 模块化设计](docs/FRONTEND_UI_DESIGN.md)。

## 数据与安全

`data/` 中的运行数据不提交：

- `data/state/`：需要跨重启恢复的会话、任务、通知和页面状态。
- `data/runtime/`：可重新生成的执行事件、临时附件、锁和任务日志。
- `data/artifacts/`：自动化下载材料和周报正式产物。

首页“工作站环境”提供受控的“系统升级与恢复”状态行。它统一执行已准备的版本切换或当前版本的运行态恢复；确认后冻结受影响的 Chub AI Runtime 写入、停止 Quick Worker，在途快速任务终止并清理 Chub Session 关联、Hook 与固定 Worker 运行态。Codex 原生 Session、配置、日志、项目资料和业务数据不归档、不删除；新实例会重新发现仍存在的原生 Session。无需等待任务自然排空；启动只校验固定切换脚本、已安装服务定义和运行态清理路径，不以当前 Web 或 Worker 状态作为门禁。只有新 Web/Worker、Session 映射读取和微信 Chub Session 快照均完成最终验证，操作才标记完成；清理或服务切换失败会保持 AI Runtime 写入失败关闭，但在当前固定恢复目标和服务预检通过时释放 Web/Worker/升级入口，允许维护者继续重启或恢复。Quick Worker 已有失败重启且当前不可达时，页面提供固定服务恢复路径；任务状态未知且没有失败重启依据时仍失败关闭。该入口不下载代码、不接受客户端路径或命令，也不提供任意数据清理。

`scripts/chub-data-migrate` 只保留给历史安装的数据目录整理，不参与 AI Session 或 Worker 协议升级。新的持久化协议切换统一使用上述受控升级流程，直接清理方案白名单内的旧运行数据，不增加启动迁移、双写或旧格式兼容读取。

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
- **总体架构**：定义系统边界、进程、领域、状态所有权、依赖方向和整体演进原则；所有专项设计必须遵循。
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

全量测试默认跳过需要受管 Chrome 的真实浏览器回归。调整首页额度、主题或快速交互页面时，启动隔离 Debug Chrome 后显式执行：

```bash
python3 .agents/skills/chrome-cdp/scripts/chrome_debug.py start --headless
CHUB_BROWSER_TESTS=1 .venv/bin/python -m pytest \
  tests/test_web_quota_browser.py \
  tests/test_quick_interaction_browser.py
```

## 核心项目文档

| 文档 | 唯一职责 |
| --- | --- |
| [Chub 项目说明](README.md) | 项目概览、安装、日常入口、安全和文档导航 |
| [Chub 总体架构与演进设计](docs/CHUB_ARCHITECTURE_DESIGN.md) | 当前进程、领域与状态所有权，以及整体分层和演进原则 |
| [Chub AI Runtime 架构设计](docs/CHUB_AI_RUNTIME_DESIGN.md) | AI Runtime 架构、Session Manager、Worker 职责和 Runtime 实现规范 |
| [Chub CLI 分发、安装与发布设计](docs/CHUB_CLI_DISTRIBUTION_DESIGN.md) | 新设备安装、核心 Chub 与可选 ClawBot 职责、npm 发布、版本管理和 GitHub Release |
| [Chub AI Session 状态模型设计](docs/AI_SESSION_STATE_DESIGN.md) | Session、Activity、入口、槽位和单 writer 语义 |
| [Chub Quick Worker 独立服务设计](docs/CHUB_QUICK_WORKER_DESIGN.md) | Quick Worker 独立服务、非实时任务、恢复、通知终态和重启协调 |
| [Codex AI 额度与用量采集设计](docs/CODEX_AI_QUOTA_USAGE_DESIGN.md) | Codex/OpenAI 用量来源、统一接口、缓存和展示口径 |
| [OpenClaw 定制集成设计](docs/OPENCLAW_CUSTOMIZATION_DESIGN.md) | OpenClaw/微信端到端业务、插件定制、Context Token、身份、路由和通知边界 |
| [本期工作周报自动化与生成设计](docs/WEEKLY_REPORT_AUTOMATION_DESIGN.md) | 飞书资料准备、确认门禁、周报生成和复核 |
| [Chub 前端 UI 模块化设计](docs/FRONTEND_UI_DESIGN.md) | 前端分层、公共交互和 Standard/Cyber 视觉契约 |
| [Chub 集成能力清单](docs/CHUB_INTEGRATION_CAPABILITIES.md) | 当前可用命令、插件、固定 API，以及微信固定指令唯一产品契约 |
| [Chub OpenClaw 插件说明](integrations/openclaw/chub/README.md) | 仓库内维护的 Chub 插件协议、源码、构建、部署和协议验收 |

日常了解项目先看本文和总体架构；开发或排障时进入对应专项设计；确认“现在能调用什么”看能力清单。`docs/design_documents.json` 的登记顺序同时维护权威层级和完整列表顺序，首页在固定核心文档后提供最近更新视图。

仓库内阶段归档（仅供历史追溯；当前 Runtime 工作不再按阶段编号规划，也不存在后续第五阶段）：

- [第一阶段](docs/archive/phase-1/README.md)
- [第二阶段](docs/archive/phase-2/README.md)
- [第三阶段](docs/archive/phase-3/README.md)
- [第四阶段](docs/archive/phase-4/README.md)
