# Hub

Hub 是一个面向个人设备的轻量管理服务。

当前阶段已经完成 macOS 与 Ubuntu 的基础节点能力、移动端 Web 管理页面、受控任务接口，以及通过手机进入本机 Codex CLI 会话的 PTY 方案。Codex PTY 已于 2026-07-21 完成 macOS 和手机端验收。

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

会话内的权限模式、审批和 Full access 由 Codex CLI 原生界面处理。Chub 只负责工作区入口、会话生命周期、终端页面和可信网络边界。

## 接口

- `/api/health`：健康检查。
- `/api/status`：节点状态。
- `/api/tasks` 和 `/api/tasks/run`：白名单任务。
- `/api/logs`：活动日志。
- `/api/codex/*`：Codex 会话管理。

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

第一阶段归档：

- [产品需求](docs/archive/phase-1/PRD.md)
- [技术架构](docs/archive/phase-1/ARCHITECTURE.md)
- [任务清单](docs/archive/phase-1/TASKS.md)
- [验收记录](docs/archive/phase-1/ACCEPTANCE.md)
