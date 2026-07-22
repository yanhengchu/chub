# Hub

Hub 是一个面向个人设备的轻量管理服务。

当前阶段已经完成 macOS 与 Ubuntu 的基础节点能力、移动端 Web 管理页面、受控任务接口，以及通过手机进入本机 Codex CLI 会话的 PTY 方案。Codex PTY 已于 2026-07-21 完成 macOS、Ubuntu 和手机端验收。

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
- `app.page_title`：浏览器标签标题，可按节点设置，例如 `MacBook · Hub` 或 `Ubuntu · Hub`；省略时使用 `Hub 管理面板`。
- `.env` 和 `config/settings.local.yaml` 都只保留本机内容，已加入 Git 忽略。

两个平台模板默认只监听 `127.0.0.1`。如果需要手机远程访问，把 `server.host` 改成该节点自己的 Tailscale IP，不要改成 `0.0.0.0` 或普通局域网地址。

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
- 进入、停止、归档或删除会话。

节点页面同时提供操作日志和运行日志。首页可查看最近 50 或 100 行，日志详情页可按来源读取更早内容或下载经过敏感信息脱敏的当前日志文件。操作日志默认写入 `logs/operations.log`，并与应用日志一样自动轮转。

会话内的权限模式、审批和 Full access 由 Codex CLI 原生界面处理。Chub 只负责工作区入口、会话生命周期、终端页面和可信网络边界。

## 自动化任务

Chub 提供飞书文档下载自动化能力，复用独立 Debug Chrome 的登录状态。固定下载流程维护在随版本发布的 `config/automation_templates/feishu-document-download.yaml`；公共任务维护在随版本发布的 `config/automations.yaml`；本机任务维护在不提交的 `config/automations.local.yaml`。两类任务配置都只需填写名称和飞书 Wiki 链接，当前默认并仅支持 Markdown。首页可以选择有界面或无界面模式启动 Debug Chrome、检查飞书登录状态，并在需要登录时安全展示扫码二维码；也可以查看合并后的任务状态、运行任务，并在“全部任务”页面查看完整列表。

创建本机配置：

```bash
cp config/automations.example.yaml config/automations.local.yaml
```

需要多端共用的任务直接添加到 `config/automations.yaml`；仅当前节点使用的任务添加到本机配置。两个文件的任务 ID 不能重复。也可以通过统一 Runner 手动执行：

```bash
.venv/bin/python -m app.automations.command run <task-id>
```

Runner 不会自行启动或停止 Debug Chrome。飞书 Wiki Markdown 下载已经完成真实流程验收；当前尚未接入系统定时器，新增任务仍需逐项验收后再启用调度。

`V 国内业务周报` 可以启用专属的 `v-weekly-report-linked-documents` 扩展。主周报下载成功后，扩展只解析“各端周报”章节内同租户的飞书 Wiki 或 Docx 文档链接，去重后串行下载 Markdown 到主任务目录的 `linked/<日期>/` 子目录。单份关联文档失败不会阻止后续文档，并在任务状态中展示汇总及可展开明细。同日重新执行时会先清理该任务当天关联目录中的旧 Markdown，保证目录只反映本次执行结果。

## 接口

- `/api/health`：健康检查。
- `/api/status`：节点状态。
- `/api/tasks` 和 `/api/tasks/run`：白名单任务。
- `/api/automations`：自动化任务状态和手动运行。
- `/api/logs`：活动日志。
- `/api/maintenance/*`：节点维护操作。
- `/api/codex/*`：Codex 会话管理。

项目资料列表和设计文档详情是只读页面，可直接通过 Chub 地址访问，便于新标签页阅读；页面不要求 Hub Token，因此文档不得包含 Token、Cookie、账号信息或其他本机秘密。Chub 仍只适合部署在可信网络中。首页卡片的动态刷新接口继续要求 Hub Token。

## 测试

```bash
.venv/bin/python -m pip install -e ".[test]"
.venv/bin/python -m pytest
```

## 文档

当前阶段：

- [第二阶段产品目标](docs/PRD_PHASE_2.md)
- [第二阶段高层计划](docs/TASKS_PHASE_2.md)
- [Codex 手机远程方案探索](docs/CODEX_REMOTE_OPTIONS_PHASE_2.md)
- [配置驱动的飞书文档下载自动化方案](docs/AUTOMATION_DOWNLOAD_DESIGN.md)

第一阶段归档：

- [产品需求](docs/archive/phase-1/PRD.md)
- [技术架构](docs/archive/phase-1/ARCHITECTURE.md)
- [任务清单](docs/archive/phase-1/TASKS.md)
- [验收记录](docs/archive/phase-1/ACCEPTANCE.md)
