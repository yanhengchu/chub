# Chub

Chub 是面向个人设备的轻量管理服务，提供统一的 Web 管理入口，用于查看节点状态、运行受控维护任务、管理 Codex 会话、执行浏览器自动化，以及连接 OpenClaw 和微信 ClawBot。

项目支持 macOS LaunchAgent 与 Ubuntu systemd user service，并以手机端可用、可信网络访问、明确的操作结果和简单维护为主要原则。基础节点能力、Codex 交互和 OpenClaw 核心链路均已完成双平台验收；后续能力由真实需求驱动。

## 核心能力

- **设备管理**：查看节点状态、运行白名单维护任务，并读取经过限制和脱敏的操作日志与运行日志。
- **Codex 会话**：通过实时终端或快速交互使用同一原生 Codex Session，支持权限、模型、推理等级和会话生命周期管理。
- **微信 Chub 模式**：OpenClaw 在模型调度前把可信微信私聊交给 Chub；固定路由直接回复，普通任务完成后向原发送者回送结果。可显式启用独立只读 Session，额外生成原文、中文润色和英文翻译。
- **自动化任务**：复用独立 Debug Chrome 的登录状态执行配置驱动任务，目前包括飞书 Wiki Markdown 下载和周报资料准备。
- **通知与资料**：向预配置的飞书目标发送受控通知，并在 Web 页面查看当前项目文档和本期周报。
- **界面风格**：默认使用简约的 Standard，也可在设置页切换到 Cyber；两种风格共用业务功能和交互结构。

具体插件、API 和微信路由以 [Chub 集成能力清单](docs/CHUB_INTEGRATION_CAPABILITIES.md)为准。

## 快速开始

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
chub logs
chub uninstall
```

macOS 使用 LaunchAgent，Ubuntu 使用 systemd user service。服务直接依赖当前工作区和 `.venv`；移动目录后需要重新安装，修改配置后使用 `chub restart` 生效。

## 日常使用

### Codex 会话

Codex PTY 依赖 `codex`、`ttyd` 和 `tmux`。安装服务时，Chub 会从当前终端 `PATH` 记录所需程序路径。

节点页面支持新建、进入、停止和归档会话，并可为新 Session 选择默认权限、模型和推理等级。同一 Session 提供两种入口：

- **实时终端**：使用原生 Codex TUI，适合审批、持续操作和实时输出。
- **快速交互**：提交后台任务并在时间线查看状态和结果，适合手机或普通网络。

两种入口共享原生 Session，并保持单 writer 互斥。快速交互默认最长运行 6 小时；长时间运行会提示仍在执行，不会在 10 分钟时误判超时。需要重启 Chub 的快速交互会先保存并通知任务结果，再登记延迟重启。

实时终端依赖稳定的 WebSocket 链路。普通页面可以打开但终端无响应时，应优先检查 Tailscale 是否直连、DERP 中继质量，以及 `/codex/.../terminal/ws` 是否成功升级为 `101 Switching Protocols`。

完整 Session 状态、互斥、通知和延迟重启规则见 [Chub AI Session 状态模型设计](docs/AI_SESSION_STATE_DESIGN.md)。

### OpenClaw 与微信 ClawBot

微信设备任务固定经过：

```text
微信 ClawBot → OpenClaw → Chub → OpenClaw → 微信 ClawBot
```

OpenClaw 只提供可信入口和通道上下文，Chub 负责路由、安全校验和最终状态。固定路由直接返回结果且不创建 Codex 任务；其他非空消息进入普通任务，普通文字静默受理，可信语音返回识别回显，任务成功、失败或超时后再原路发送最终结果。整条链路不调用 OpenClaw Agent，也不把拦截、提交或通知发送等同于任务成功。

首页“OpenClaw 环境”卡片用于查看 Gateway、微信通道和 Tailscale 入口，并提供受控的启停、重启及微信绑定操作。绑定成功只表示通道登录完成，不代表发送者配对或 Owner 权限已经配置。

端到端状态和安全边界见 [Chub–OpenClaw 接入设计](docs/CHUB_OPENCLAW_INTEGRATION_DESIGN.md)；插件协议、构建和部署见 [Chub OpenClaw 插件说明](integrations/openclaw/chub/README.md)。

### 飞书通知

飞书目标登记在 `~/.config/chub/notifications/registry.yaml`，Webhook 保存在同目录权限为 `600` 的独立文件中。通知只允许预配置目标、有界纯文本和已登记人员；调用方不能指定任意 URL、Open ID 或 Secret 路径。

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

### 界面风格

设置页可选择 `Standard`（简约标准版）或 `Cyber`（科技终端版），偏好保存在当前浏览器并应用到公共页面。Standard 是新增页面和功能的默认基线；Cyber 保持相同的信息结构、安全边界、键盘操作和响应式能力。

完整视觉规范见 [Chub 前端 UI 模块化设计](docs/ARCHITECTURE_EVOLUTION_DESIGN.md)。

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
- `/api/automations/*`：自动化环境、任务和结果。
- `/api/openclaw/*`：OpenClaw 状态与受控操作。
- `/api/logs`、`/api/maintenance/*`：日志和节点维护。

## 项目资料维护

README 是项目入口和文档管理规则的维护入口；详细需求、设计与操作边界放在对应专项文档中，避免重复维护。

- **当前文档**：仍描述当前有效的需求、架构、功能或流程，已实现或已验收不代表需要归档。
- **阶段记录**：阶段已经闭环，但仍被当前工作引用或尚未被新文档替代。
- **归档文档**：只用于历史追溯，移动到 `docs/archive/phase-N/` 并原则上冻结。

当前项目资料统一登记在 `docs/design_documents.json`。普通文档使用相对于 `docs/` 的 Markdown 路径；项目根 README 使用唯一保留别名 `@project/README.md`，不能借此读取其他根目录文件。索引状态只使用“调研中”“待实现”“进行中”“待验收”“已验收”或“持续维护”。归档或移动文档时，应同步索引和相关引用。

## 测试

```bash
.venv/bin/python -m pip install -e ".[test]"
.venv/bin/python -m pytest
```

## 相关文档

当前设计与维护文档：

- [Chub AI Session 状态模型设计](docs/AI_SESSION_STATE_DESIGN.md)
- [Chub–OpenClaw 接入设计](docs/CHUB_OPENCLAW_INTEGRATION_DESIGN.md)
- [Chub 集成能力清单](docs/CHUB_INTEGRATION_CAPABILITIES.md)
- [Chub OpenClaw 插件说明](integrations/openclaw/chub/README.md)
- [微信 ClawBot Context Token 持久化 AI 补丁规范](docs/WEIXIN_CLAWBOT_CONTEXT_TOKEN_AI_PATCH.md)
- [本期工作周报自动化与生成设计](docs/WEEKLY_REPORT_AUTOMATION_DESIGN.md)
- [Chub 前端 UI 模块化设计](docs/ARCHITECTURE_EVOLUTION_DESIGN.md)

阶段归档：

- [第一阶段](docs/archive/phase-1/README.md)
- [第二阶段](docs/archive/phase-2/README.md)
- [第三阶段](docs/archive/phase-3/README.md)
