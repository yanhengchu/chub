# Chub

Chub 是面向个人设备的轻量管理服务，提供统一的 Web 管理入口，用于查看节点状态、运行受控维护任务、管理 Codex 会话、执行浏览器自动化，以及连接 OpenClaw 和微信 ClawBot。

项目支持 macOS LaunchAgent 与 Ubuntu systemd user service，并以手机端可用、可信网络访问、明确的操作结果和简单维护为主要原则。基础节点能力、Codex 交互和 OpenClaw 核心链路均已完成双平台验收；后续能力由真实需求驱动。

## 当前功能

| 功能域 | 当前可用能力 | 主要入口 |
| --- | --- | --- |
| 设备管理 | 查看节点与系统状态，执行后端白名单维护任务，查看受限、脱敏的操作日志和运行日志 | 首页、日志页 |
| Codex 会话 | 新建、进入、停止和归档 Session；选择权限、模型和推理等级；通过实时终端或快速交互复用同一原生 Session | 首页、Session 页 |
| 需求储备 | 使用 R1–R9 保存、更新和查看轻量需求，并从微信提交到当前 Session 执行 | 命令行、微信 ClawBot |
| OpenClaw 与微信 | 查看并维护 Gateway 和微信通道；将可信微信私聊交给 Chub 固定路由或 Codex 任务；任务结束后按原路返回结果 | 首页、设置页、微信 ClawBot |
| 自动化任务 | 管理独立 Debug Chrome，复用登录状态运行配置驱动任务；当前支持飞书 Wiki Markdown 下载和周报资料准备 | 首页、自动化页、命令行 |
| 周报 | 校验当期资料和周期，确认重点后生成、复核并展示正式周报 | 自动化页、周报页 |
| 通知 | 向预配置的飞书目标发送有界纯文本通知，不接受任意目标或 Webhook | 命令行、Chub API、OpenClaw Tool |
| 项目资料 | 在可信网络内查看已登记的项目说明、设计方案与维护文档，并由维护入口控制首页显示 | 首页、项目资料页 |
| 执行设置 | 即时启停微信普通任务的执行前润色与翻译 | 设置页 |
| 界面风格 | 切换 Standard/Cyber 风格 | 设置页 |

页面、微信和翻译快速任务统一由独立 Worker 承载，Web 重启不会中断已接受的任务。恢复、通知和协调重启的完整边界见[快速交互独立 Worker 设计](docs/QUICK_INTERACTION_WORKER_DESIGN.md)。

电脑端命令、插件和固定 API 的查询入口是 [Chub 集成能力清单](docs/CHUB_INTEGRATION_CAPABILITIES.md)；其中第 4 节是微信固定指令的唯一产品契约。身份、安全、路由和并发等内部边界由对应设计文档维护。

## 快速开始

前置条件：Python 3.12 或更高版本。先使用 `python3 --version` 确认当前命令满足要求，
再创建项目虚拟环境。

创建环境并安装依赖：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
chmod 600 .env
```

按平台创建本机配置：

```bash
cp config/settings.macos.example.yaml config/settings.local.yaml
# 或
cp config/settings.ubuntu.example.yaml config/settings.local.yaml
```

启动服务：

```bash
.venv/bin/python main.py
```

`.env` 和 `config/settings.local.yaml` 只保存本机配置，不应提交。主要配置包括：

- `HUB_TOKEN`：节点访问令牌。
- `HUB_CONFIG_FILE`：配置文件路径，默认 `config/settings.local.yaml`。
- `app.page_title`：浏览器标签和首页标题；省略时使用应用名称。
- `security.allow_tailscale`：默认开启，允许真实 Tailnet socket 来源访问受保护接口；不信任客户端转发 Header，也不识别具体用户。
- `ai_usage`：AI 额度供应商、地区和 API 方式的固定订阅页配置；不保存 Cookie、Authorization 或其他上游凭据。

平台模板默认监听 `127.0.0.1`。需要通过手机远程访问时，将 `server.host` 改为节点自己的 Tailscale IP，不要使用 `0.0.0.0` 或普通局域网地址。如果 Tailnet 将来加入其他人的设备，应重新评估设备级授权和高风险操作边界。

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
chub logs
chub uninstall
```

macOS 使用两个独立 LaunchAgent，Ubuntu 使用两个独立 systemd user service，分别承载 Web 和
Quick Worker。`chub restart` 只重启 Web，不停止 Worker；`chub status` 同时显示两个服务，
`chub worker-health` 通过本机私有 IPC 读取 Worker 健康信息。当前 Worker 已经接管页面、微信和翻译快速任务；
macOS 与 Ubuntu 均已确认单独重启 Web 不会停止 Worker、Runner 或现有任务，恢复后的结果和通知不会重复。
Worker 不健康时，Session 写操作保持失败关闭，不回退到 Web Runner。

服务直接依赖当前工作区和 `.venv`；移动目录或变更服务定义后需要重新安装。Web 配置变更使用
`chub restart` 生效；Worker 升级和服务定义变更需要重新安装生效。

## 日常使用

### Codex 会话

Codex PTY 依赖 `codex`、`ttyd` 和 `tmux`。安装服务时，Chub 会从当前终端 `PATH` 记录所需程序路径。

节点页面支持新建、进入、停止和归档 Session，并可为新 Session 选择默认权限、模型和推理等级。同一 Session 提供两种入口：

- **实时终端**：使用原生 Codex TUI，适合审批、持续操作和实时输出。
- **快速交互**：提交后台任务并在时间线查看状态和结果，适合手机或普通网络。

新建 Session 默认进入快速交互；入口偏好、Session 导航和未发送草稿只保存在当前浏览器。两种入口共享原生 Session，并保持单 writer 互斥；快速交互任务由独立 Worker 执行和恢复。

Session、Activity、槽位、标题和入口语义见[Chub AI Session 状态模型设计](docs/AI_SESSION_STATE_DESIGN.md)；后台任务、通知终态和跨 Web 重启见[快速交互独立 Worker 设计](docs/QUICK_INTERACTION_WORKER_DESIGN.md)。

实时终端依赖稳定的 WebSocket 链路。普通页面可以打开但终端无响应时，应优先检查 Tailscale 是否直连、DERP 中继质量，以及 `/codex/.../terminal/ws` 是否成功升级为 `101 Switching Protocols`。

### 轻量需求储备

轻量需求储备使用 `R1`–`R9` 九个活动槽位，不创建专用 Session。维护者明确要求保存或更新已经讨论成型的小需求后，编码 Agent 通过本机 `chub request save` 或 `chub request update` 受控写入；`chub request list` 和 `chub request show` 用于检查活动需求。保存和更新从标准输入读取完整正文，不直接编辑状态文件。

微信 Chub 模式可在状态摘要中查看活动需求，并将指定需求提交到当前 Session 或归档非运行需求。完整的本机命令、微信语法、长度限制和失败语义见[Chub 集成能力清单](docs/CHUB_INTEGRATION_CAPABILITIES.md)；需求执行的持久化、恢复和通知边界见[Chub–OpenClaw 接入设计](docs/CHUB_OPENCLAW_INTEGRATION_DESIGN.md)。

### AI 额度

首页 Codex 卡片通过统一的 `/api/ai/usage` 展示周额度、今日用量和重置时间。ChatGPT 账号登录优先使用账户日桶，当天桶缺失时显示明确标记的本机 Token；API Key 方式复用已登录的受管 Debug Chrome。两种来源不会互相降级，完整数据口径、接口和缓存规则见[Chub AI 额度与用量采集设计](docs/AI_QUOTA_USAGE_DESIGN.md)。

### OpenClaw 与微信 ClawBot

微信设备任务固定经过：

```text
微信 ClawBot → OpenClaw → Chub → OpenClaw → 微信 ClawBot
```

OpenClaw 提供可信入口和通道上下文，Chub 负责业务路由、安全校验和最终状态；整条链路不调用 OpenClaw Agent，也不把消息拦截或任务提交等同于最终成功。

首页“OpenClaw 环境”卡片用于查看 Gateway、微信通道和 Tailscale 入口，并提供受控的启停、重启及微信绑定操作。绑定成功只表示通道登录完成，不代表发送者配对或 Owner 权限已经配置。

端到端状态和安全边界见 [Chub–OpenClaw 接入设计](docs/CHUB_OPENCLAW_INTEGRATION_DESIGN.md)；插件协议、构建和部署见仓库内维护资料 [Chub OpenClaw 插件说明](integrations/openclaw/chub/README.md)。

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

首页可管理浏览器环境、检查站点登录状态并运行任务；命令行也可调用统一 Runner：

```bash
.venv/bin/python -m app.automations.command run <task-id>
```

Runner 不会自行启动或停止 Debug Chrome。浏览器 Profile 未初始化、端口冲突或环境正被使用时会明确失败。周报资料下载、周期校验、人工重点确认和正式生成规则见 [本期工作周报自动化与生成设计](docs/WEEKLY_REPORT_AUTOMATION_DESIGN.md)。

### 微信执行前润色

设置页的“自动润色后执行”是节点级设置，需要 Hub Token 或可信 Tailnet 访问。开启后，微信普通任务先在独立只读 Session 生成中文润色和 English，再把润色后的中文提交到原目标 Session；翻译受理后不发送处理中回执，主任务被接收后发送包含实际提交文本和 English 的 `Started` 通知。固定指令绕过该流程，`direct <task>` / `直接执行 <正文>` 可单次直接执行原文。首次状态由本机配置的 `translation_enabled` 决定，页面修改后由 Chub 私有状态持久化并即时生效，无需改写 YAML 或重启服务。

### 界面风格

设置页可选择 `Standard`（简约标准版）或 `Cyber`（科技终端版），偏好保存在当前浏览器并应用到公共页面。Standard 是新增页面和功能的默认基线；Cyber 保持相同的信息结构、安全边界、键盘操作和响应式能力。

完整视觉规范见 [Chub 前端 UI 模块化设计](docs/FRONTEND_UI_DESIGN.md)。

## 数据与安全

`data/` 中的运行数据不提交：

- `data/state/`：需要跨重启恢复的会话、任务、通知和页面状态。
- `data/runtime/`：可重新生成的执行事件、临时附件、锁和任务日志。
- `data/artifacts/`：自动化下载材料和周报正式产物。

升级旧数据布局时，应先等待任务结束，再执行 `chub stop`、`./scripts/chub-data-migrate` 和 `chub start`。迁移不会覆盖同名业务产物。

受保护接口使用 Hub Token，或在未关闭时接受真实 Tailnet socket 来源。健康检查和项目资料详情是可信网络内的只读页面；公开展示的文档和周报不得包含 Token、Cookie、账号信息、本机秘密或其他不适合直接访问的内容。

主要接口类别：

- `/api/health`、`/api/status`：健康和节点状态。
- `/api/codex/*`：Codex 会话与快速交互。
- `/api/ai/usage`：统一 AI 额度、今日用量和重置时间。
- `/api/automations/*`：自动化环境、任务和结果。
- `/api/openclaw/*`：OpenClaw 状态与受控操作。
- `/api/logs`、`/api/maintenance/*`：日志和节点维护。

## 项目资料维护

README 是项目入口和文档管理规则的维护入口；详细契约放在对应专项文档中。文档按以下职责维护：

- README 只维护项目概览、安装、主要使用入口、数据安全和文档导航。
- 集成能力清单登记当前可调用的命令、插件和固定 API，并维护微信固定指令的唯一产品契约；不解释内部实现、身份安全或调度协议字段。
- 专项设计只维护本领域的现行行为和边界；跨领域规则引用其权威文档，不复制完整正文。
- 插件 README 维护插件协议、源码、构建和部署；第三方补丁文档只维护异常恢复，不承担日常使用说明。
- 文档的页面登记、摘要和展示状态以 `docs/design_documents.json` 为准；各文档顶部可以补充维护与验收说明，但不得与索引状态冲突。

文档生命周期：

- **当前文档**：仍描述当前有效的需求、架构、功能或流程，已实现或已验收不代表需要归档。
- **阶段记录**：阶段已经闭环，但仍被当前工作引用或尚未被新文档替代。
- **归档文档**：只用于历史追溯，移动到 `docs/archive/phase-N/` 并原则上冻结。

项目资料页面展示的当前文档统一登记在 `docs/design_documents.json`，列表按文件最后更新时间倒序显示，首页展示最近更新且未隐藏的五份。普通文档使用相对于 `docs/` 的 Markdown 路径；项目根 README 使用唯一保留别名 `@project/README.md`，不能借此读取其他根目录文件。索引状态只使用“调研中”“待实现”“进行中”“待验收”“已验收”或“持续维护”。

项目资料页的“隐藏/恢复显示”只控制首页展示，状态保存在本机私有运行数据中，不移动、冻结或改写仓库文档。生命周期归档仍需把历史文档移动到 `docs/archive/phase-N/`，并同步索引和相关引用。

插件 README、`.agents/skills/` 下的技能文档和 `docs/archive/` 下的阶段记录属于仓库内维护资料，不进入可信网络只读的项目资料页面，也不扩展 `@project/` 文件读取范围。README 在项目资料页渲染时不会为这些仓库内目标提供下钻；需要查看时从工作区 Markdown 导航进入。

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
| [Chub 集成能力清单](docs/CHUB_INTEGRATION_CAPABILITIES.md) | 当前可用命令、插件、固定 API，以及微信固定指令唯一产品契约 |
| [Chub AI Session 状态模型设计](docs/AI_SESSION_STATE_DESIGN.md) | Session、Activity、入口、槽位和单 writer 语义 |
| [快速交互独立 Worker 设计](docs/QUICK_INTERACTION_WORKER_DESIGN.md) | 非实时任务、恢复、通知终态和协调重启 |
| [Chub AI 额度与用量采集设计](docs/AI_QUOTA_USAGE_DESIGN.md) | AI 用量来源、统一接口、缓存和展示口径 |
| [Chub–OpenClaw 接入设计](docs/CHUB_OPENCLAW_INTEGRATION_DESIGN.md) | OpenClaw/微信端到端业务、身份、权限、Session/Request 状态和通知边界 |
| [Chub OpenClaw 插件说明](integrations/openclaw/chub/README.md) | 仓库内维护的 Chub 插件协议、源码、构建、部署和协议验收 |
| [微信 Context Token 补丁规范](docs/WEIXIN_CLAWBOT_CONTEXT_TOKEN_AI_PATCH.md) | 第三方微信插件升级后的兼容复检和恢复 |
| [本期工作周报自动化与生成设计](docs/WEEKLY_REPORT_AUTOMATION_DESIGN.md) | 飞书资料准备、确认门禁、周报生成和复核 |
| [Chub 前端 UI 模块化设计](docs/FRONTEND_UI_DESIGN.md) | 前端分层、公共交互和 Standard/Cyber 视觉契约 |

日常了解项目先看本文；确认“现在能调用什么”看能力清单；开发或排障时再进入对应专项设计。`docs/design_documents.json` 维护页面登记和展示状态，页面顺序由文件最后更新时间决定。

仓库内阶段归档：

- [第一阶段](docs/archive/phase-1/README.md)
- [第二阶段](docs/archive/phase-2/README.md)
- [第三阶段](docs/archive/phase-3/README.md)
- [第四阶段](docs/archive/phase-4/README.md)
