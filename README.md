# Hub

Hub 是一个面向个人设备的轻量管理服务。

第二阶段已于 2026-07-26 正式闭环：macOS 与 Ubuntu 基础节点能力、移动端 Web 管理页面、受控任务接口、Codex PTY 与快速交互、配置驱动自动化及前端 UI 模块化均已实现，并通过 Ubuntu、MacBook 和手机端验收。第三阶段以 OpenClaw 接入、飞书群机器人 Webhook 单向通知、微信 ClawBot 双向指令交互，以及其他 LLM 模型接入为核心。OpenClaw 已在 MacBook 与 Ubuntu 完成安装、初始化、Gateway 运行和基础聊天的首轮验收，后续进入 Chub 受限能力接入与消息通道实施。

## 快速开始

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

## 本地配置

- `HUB_TOKEN`：节点访问令牌。
- `HUB_CONFIG_FILE`：默认 `config/settings.local.yaml`。
- `security.allow_tailscale`：默认 `true`。当 `server.host` 为本机 Tailscale IP 时，来自同一 Tailnet 的真实连接可免 Hub Token；非 Tailscale 来源仍需 Token。不需要该能力时可显式设为 `false`。
- `llm`：默认启用并从当前用户的 `~/.openclaw/openclaw.json` 只读加载模型配置和文件型 SecretRef。OpenClaw 只有一个 Provider/模型时自动选择；存在多个候选时需在 Chub 配置中指定 `provider` 和 `model`。
- `app.page_title`：浏览器标签标题和首页顶部标题，可按节点设置，例如 `MacBook · Hub`、`Ubuntu · Hub` 或 `设备.hub`；省略时浏览器标签使用 `<app.name> 管理面板`，首页顶部使用 `app.name`。
- `.env` 和 `config/settings.local.yaml` 都只保留本机内容，已加入 Git 忽略。

两个平台模板默认只监听 `127.0.0.1`。如果需要手机远程访问，把 `server.host` 改成该节点自己的 Tailscale IP，不要改成 `0.0.0.0` 或普通局域网地址。当前部署的个人 Tailnet 只加入维护者本人控制的设备，因此这些设备整体视为可信网络边界，可同时启用 `security.allow_tailscale`。浏览器会先尝试免 Token 连接，并保留之前保存的 Hub Token 作为 Tailscale 验证失败时的回退；回退认证失败后再清除无效 Token。该模式只检查真实 socket 来源，不信任转发 Header，也不识别具体用户；如果以后允许其他人的设备加入 Tailnet，需要重新评估设备级授权和高风险操作边界。

## 后台服务

开发目录可以直接安装成当前用户的后台服务：

```bash
./scripts/chub install
```

安装后会创建 `~/.local/bin/chub` 链接。常用命令：

```bash
chub start
chub stop
chub restart
chub status
chub logs
chub uninstall
```

- macOS 使用 LaunchAgent。
- Ubuntu 使用 systemd user service。
- 服务直接依赖当前工作区和 `.venv`，移动目录后需要重新安装。
- `chub restart` 会重载配置和依赖路径。

## Codex PTY

Codex PTY 依赖 `codex`、`ttyd` 和 `tmux`。安装服务时，Chub 会从当前终端 `PATH` 探测这些程序及其目录，并写入后台服务配置。

节点页面的 `Codex PTY` 卡片支持：

- 从用户目录、Workspace、Chub 三个固定入口新建会话。
- 查看本机未归档会话。
- 进入、停止或归档会话；日常页面不提供删除入口，避免误删 Codex 历史。
- 在首页切换 `Ask for approval`、`Approve for me`、`Full access` 和 `Read Only` 四种权限模式；运行中的会话切换权限时会自动停止，下一次进入时按新权限启动。
- 区分尚未启动、运行、停止和异常等会话生命周期，以及执行中、等待输入和状态未知等活动状态；首页对执行中会话快速刷新，对运行中但状态未知的会话低频确认，进入等待输入或停止后结束轮询。
- 同一 Codex session 提供“实时终端”和“快速交互”两种交互入口；首页使用显示当前模式的两态按钮，点击后直接切换为另一入口，再点击 Session 进入对应页面。快速交互页面统一提供后台单次任务提交、状态、最终结果和历史记录，支持持久化置顶，列表依次展示最近一条、置顶记录和普通记录。实时终端等待输入时保持 TUI/tmux 运行并允许快速交互；实时终端执行中时拒绝快速交互，快速交互执行中时禁止进入实时终端。

节点页面同时提供操作日志和运行日志。首页可查看最近 50 或 100 行，日志详情页可按来源读取更早内容或下载经过敏感信息脱敏的当前日志文件。操作日志默认写入 `logs/operations.log`，并与应用日志一样自动轮转。

Chub 只负责权限模式选择和会话生命周期；具体审批交互、权限显示和命令执行仍由 Codex CLI 原生界面处理。首页权限选择会映射到 Codex 的工作区、只读和完全访问配置，运行中的会话切换权限会先停止当前 PTY，避免旧进程继续使用旧权限。

Codex PTY 终端通过 WebSocket 持续传输输入和输出，依赖稳定的双向实时链路。Tailscale 跨网络访问时，即使首页和文档等普通 HTTP 页面可以正常打开，如果路径经过质量不稳定的 DERP 中继、存在较高抖动、丢包、MTU 问题或网络切换，仍可能出现 ttyd 页面外壳已加载但终端内容未显示、输入无响应或连接中断。这里的限制不只是带宽问题，链路稳定性和延迟同样重要。当前产品不提供基于轮询或终端快照的非实时降级模式，Codex PTY 应优先在稳定的 Tailscale 直连或可靠网络中使用。排查时查看浏览器网络面板中 `/codex/.../terminal/ws` 是否成功升级为 `101 Switching Protocols`，并结合应用日志中的 `terminal_websocket_*` 和 `terminal_http_*` 记录判断连接或上游 ttyd 是否失败。

快速交互页面可在提交按钮左侧临时切换 `Codex CLI` 与 `Amazon Bedrock API`。选择只在当前页面有效，刷新、离开后重新进入或浏览器返回恢复页面时均默认回到 Codex CLI。Codex 模式沿用工作区、权限和实时终端互斥逻辑；Bedrock 模式直接调用 Chub 基础 LLM，不读取工作区、不停止实时终端，也不修改 Codex Session activity。两种任务共用本机交互历史、置顶和分页，并在时间左侧保存实际执行来源；Bedrock 记录同时保存提交时的 Provider 与模型快照。快速交互历史会保存提交内容和最终结果，运行日志与操作日志不记录正文。

Codex 快速交互允许长任务持续执行：运行超过 10 分钟时页面提示“执行时间较长，仍在运行”，不将其误判为超时。真正的执行上限由 `codex_pty.quick_interaction_timeout_seconds` 配置，默认 `21600` 秒（6 小时），允许范围为 10 分钟至 24 小时；达到上限后才会终止进程并记录为超时。修改该配置后需要重启 Chub 服务。

## OpenClaw Gateway

首页 OpenClaw 卡片用于管理当前节点上的 Gateway。它展示安装、初始化、后台服务、连接探测、版本、监听状态、消息通道运行摘要和当前通道的 Owner 权限摘要，并提供固定的启动、停止和重启操作；停止和重启需要二次确认。消息通道在 Gateway 就绪后独立检查，检查失败不会覆盖 Gateway 状态；已配置通道但未配置对应 Owner 时显示“功能受限”，不返回具体身份或凭证。Tailscale Serve 可用时，卡片只展示标准 HTTPS 访问地址，整个地址区域可点击进入控制台；本机 loopback 地址只作为排障入口，不在首页展示。所有接口均使用 Hub Token 或未被关闭的 Tailnet 可信访问，操作以 Gateway 最终状态而不是命令进程退出作为成功依据，并写入完整操作日志。

该卡片不提供安装、卸载、升级、初始化配置、控制台代理、任意命令或原始日志入口，也不会向页面返回 OpenClaw 配置和凭证。OpenClaw CLI 必须能从 Chub 服务的 `PATH` 找到；macOS 和 Ubuntu 使用同一套接口，由 OpenClaw 自身管理对应的 LaunchAgent 或 systemd user service。

最近一次成功检测的 OpenClaw 展示状态按节点保存在浏览器会话中。从次级页面返回或首页被重建时，页面先恢复该状态，再静默刷新最新结果，避免卡片短暂回到空占位；刷新失败时保留上次结果并单独提示。退出节点或认证失效时会清理缓存。

## 基础 LLM

Chub 提供独立的底层 LLM 模块，按数据模型、OpenClaw 配置源、`openai-completions` 传输和统一 Service 分层。它直接复用当前用户 OpenClaw 配置中的 Provider、模型和文件型 `singleValue` SecretRef，不复制或持久化 API Key，也不通过 OpenClaw Gateway 转发请求。配置和 Secret 在首次调用时懒加载，并按文件修改状态自动刷新；HTTP 客户端复用连接池，在 Chub 退出时关闭。首版只支持 `/chat/completions` 文本调用，默认最多并发 2 个请求、单次响应不超过 1 MiB；配置缺失、凭证权限不安全或模型不可用只会导致该次 LLM 调用失败，不阻止 Chub、Codex PTY 或自动化运行。当前仅通过快速交互页面提供固定 Bedrock 执行入口，不提供独立首页卡片或通用公共 LLM API。

## 自动化任务

Chub 提供飞书文档下载自动化能力，复用独立 Debug Chrome 的登录状态。固定下载流程维护在随版本发布的 `config/automation_templates/feishu-document-download.yaml`；公共任务维护在随版本发布的 `config/automations.yaml`；本机任务维护在不提交的 `config/automations.local.yaml`。两类任务配置都只需填写名称和飞书 Wiki 链接，当前默认并仅支持 Markdown。首页可以选择默认 Chrome 的普通 Profile，以及有界面或无界面启动 Debug Chrome；未初始化的 Profile 会在确认默认 Chrome 已退出后通过现有 `chrome-cdp` 能力复制到独立目录。复制后的网站登录状态持续保存在 Debug Chrome 副本中，不与默认 Chrome 自动双向同步。首页还可以检查飞书登录状态、在需要登录时安全展示扫码二维码、运行任务，并在“全部任务”页面查看完整列表。详细规则见 `docs/AUTOMATION_DOWNLOAD_DESIGN.md`。

创建本机配置：

```bash
cp config/automations.example.yaml config/automations.local.yaml
```

需要多端共用的任务直接添加到 `config/automations.yaml`；仅当前节点使用的任务添加到本机配置。两个文件出现 ID 和内容完全一致的任务时会自动去重并使用公共配置；ID 相同但内容不同时会提示配置冲突，不允许本机配置静默覆盖公共任务。也可以通过统一 Runner 手动执行：

```bash
.venv/bin/python -m app.automations.command run <task-id>
```

Runner 不会自行启动或停止 Debug Chrome。飞书 Wiki Markdown 下载已经完成真实流程验收；周报整理依赖各端人员的实际完成情况，因此保留人工确认后手动执行，不接入固定时间调度。新增任务仍需逐项验收后再决定执行方式。

`V 国内业务周报` 可以启用专属的 `v-weekly-report-linked-documents` 扩展。主周报下载成功后，扩展只解析“各端周报”章节内同租户的飞书 Wiki 或 Docx 文档链接，去重后串行下载 Markdown 到主任务目录的 `linked/<日期>/` 子目录。单份关联文档失败不会阻止后续文档，并在任务状态中展示汇总及可展开明细。同日重新执行时会先清理该任务当天关联目录中的旧 Markdown，保证目录只反映本次执行结果。

## 接口

- `/api/health`：健康检查。
- `/api/status`：节点状态。
- `/api/automations`：自动化任务状态和手动运行。
- `/api/logs`：活动日志。
- `/api/maintenance/*`：节点维护操作。
- `/api/openclaw/*`：OpenClaw Gateway 状态和受控维护操作。
- `/api/codex/*`：Codex 会话管理。

项目资料列表和设计文档详情可直接通过 Chub 地址访问，便于阅读；页面内容不要求认证，因此文档不得包含 Token、Cookie、账号信息或其他本机秘密。归档状态管理仍需 Hub Token 或未被关闭的 Tailnet 可信访问，状态保存在 `data/project-documents.json`；首页只展示当前文档，全部列表可筛选当前和已归档文档。Chub 仍只适合部署在可信网络中。

设计文档统一登记在 `docs/design_documents.json`。新增文档时添加 Markdown 文件，并在索引中配置唯一的小写连字符 `id`、`title`、`summary`、`status` 和相对于 `docs/` 的 `.md` 路径；索引或引用文件异常会在页面和运行日志中明确提示。

## 测试

```bash
.venv/bin/python -m pip install -e ".[test]"
.venv/bin/python -m pytest
```

## 文档

第三阶段（规划中）：

- [第三阶段产品目标](docs/PRD_PHASE_3.md)
- [第三阶段高层计划](docs/TASKS_PHASE_3.md)
- [OpenClaw 方案调研](docs/OPENCLAW_RESEARCH.md)
- [OpenClaw 与消息通道接入设计](docs/OPENCLAW_INTEGRATION_DESIGN.md)

第二阶段（已闭环）：

- [前端 UI 模块化设计](docs/ARCHITECTURE_EVOLUTION_DESIGN.md)
- [第二阶段产品目标](docs/PRD_PHASE_2.md)
- [第二阶段高层计划](docs/TASKS_PHASE_2.md)
- [Codex 手机远程方案探索](docs/CODEX_REMOTE_OPTIONS_PHASE_2.md)
- [配置驱动的飞书文档下载自动化方案](docs/AUTOMATION_DOWNLOAD_DESIGN.md)
- [工作周报生成技能设计方案](docs/WEEKLY_REPORT_SKILL_DESIGN.md)

第一阶段归档：

- [产品需求](docs/archive/phase-1/PRD.md)
- [技术架构](docs/archive/phase-1/ARCHITECTURE.md)
- [任务清单](docs/archive/phase-1/TASKS.md)
- [验收记录](docs/archive/phase-1/ACCEPTANCE.md)
