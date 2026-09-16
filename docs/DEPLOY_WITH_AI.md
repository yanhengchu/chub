# Chub 正式部署包：AI 安装与 WSL2 验收指引

本文是解压后目标设备 AI 的唯一安装操作入口。目标 AI 负责从全新 WSL2 Ubuntu 环境完成 Chub 核心部署、服务启动、Windows localhost 可达性和 WSL 重启恢复验收，并根据本文件给出明确的 `succeeded`、`failed` 或 `recovery_pending` 最终结论。AI 必须先确认本机环境和每一步的最终结果；不得猜测发行版、账号、凭据、OpenClaw 路径、微信 Owner 或浏览器配置。来源设备的本机配置、账号、Token、Cookie、浏览器数据、Session、任务和运行态不得复制到目标设备。

当前部署包可用于 macOS 或 Ubuntu。**Windows WSL 仅按 Ubuntu WSL2 + systemd 路径进行首次真实验收，尚未视为已验证平台。** WSL1、非 Ubuntu 发行版、未启用 systemd 的 WSL，或无法使用 `systemctl --user` 的环境不得继续执行服务安装。

## 1. 部署目标、AI 权限与最终结论

1. AI 执行本文件中的固定环境检查、依赖安装、解压、虚拟环境创建、配置文件创建、`chub install`、健康检查、Windows localhost 检查和 WSL 恢复检查；维护者不承担常规安装或部署步骤。
2. 维护者只在 AI 无法取得必要权限时完成系统的 `sudo` 授权，以及在 Codex 登录、账号、API Key、OpenClaw、微信绑定、Owner 或其他凭据步骤中完成本人专属操作。AI 在该操作完成后继续执行，不得读取、记录或输出秘密。
3. AI 只可在维护者提供的 ZIP 路径及其 Linux 主目录下全新目标目录内操作，不得覆盖已有 Chub 目录。项目和工作目录必须位于 Linux 文件系统，例如 `/home/<user>/chub` 与 `/home/<user>/workspace`，不得放在 `/mnt/c/`、`/mnt/d/` 等 Windows 挂载目录。
4. 首轮成功标准为 Chub 核心、Web、Quick Worker、Windows localhost 可达性和一次 WSL 重启后的服务恢复。Codex Runtime 仅在维护者要求本轮包含 AI Runtime 且已完成其专属登录时纳入成功标准。OpenClaw、微信、Debug Chrome 图形自动化、浏览器 Profile、Windows GUI 集成和 Tailnet 访问均不属于首轮成功条件。
5. AI 只有在本文件第 7 节所有适用条件均通过时才能报告 `succeeded`；任一必需检查失败报告 `failed`。运行 `wsl.exe --shutdown` 后若执行 Agent 无法自动重新连接目标 WSL，则报告 `recovery_pending`，不得把重启前的检查当作最终成功。

## 2. WSL2 Ubuntu 前置检查

在 WSL 终端、使用目标 Linux 用户执行：

```bash
uname -s
. /etc/os-release && printf '%s\n' "$ID"
ps -p 1 -o comm=
systemctl --user show-environment >/dev/null
python3.12 --version
command -v curl
command -v unzip
```

只有结果满足以下条件才继续：`Linux`、Ubuntu、PID 1 为 `systemd`、`systemctl --user` 可用、Python 为 3.12 或更高版本，且 `curl` 与 `unzip` 可用。缺少系统组件时，AI 按下列固定 Ubuntu 安装命令处理；只有 `sudo` 授权被拒绝或系统包安装失败时才报告阻塞。AI 不得把 WSL 改为公网监听、跳过 systemd 或用前台进程替代受管服务作为验收结论。

对于新的 Ubuntu WSL2 环境，AI 使用以下固定命令安装基础依赖：

```bash
sudo apt update
sudo apt install -y python3.12 python3.12-venv python3-pip python3.12-dev build-essential curl unzip
```

为验证用户级服务能在 WSL 重新启动后恢复，先检查 linger：

```bash
loginctl show-user "$USER" -p Linger
```

若显示 `Linger=no`，AI 请求一次必要的维护者授权后执行以下固定操作并再次确认：

```bash
sudo loginctl enable-linger "$USER"
loginctl show-user "$USER" -p Linger
```

## 3. 解压、创建环境和本机配置

在维护者指定的全新 Linux 主目录目标位置解压后，进入解压根目录。若 ZIP 解压后未保留脚本执行权限，恢复固定脚本的用户执行权限：

```bash
chmod u+x scripts/chub scripts/chub-web-restart scripts/chub-system-upgrade-start scripts/chub-system-upgrade-restart
python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
cp config/settings.example.yaml config/settings.local.yaml
```

AI 创建并填写 `config/settings.local.yaml`。首轮 WSL2 验收至少确认：

- `node.id` 为新设备唯一标识，`node.name` 为该设备可识别名称，`node.type` 为 `ubuntu`；
- `ai_runtime.codex.workspace` 是 Linux 文件系统内的受信工作目录；
- `server.port` 使用未被本机占用的端口；
- 首轮本机验收可将 `security.allow_tailscale` 设为 `false`，后续 Tailnet 验收再单独启用；
- 不填写或复制通知、OpenClaw、微信、API Key、Token、Cookie、账号或其他凭据。

配置文件仅保存在目标设备，不能提交、传回或写入部署验收记录。填写需要账号、Token、Cookie、API Key、OpenClaw 或微信信息时，AI 停止该项并向维护者请求专属输入；首轮核心验收不需要这些字段。

## 4. 安装和确认 Chub 核心

在项目根目录执行固定入口：

```bash
./scripts/chub install
./scripts/chub check
./scripts/chub status --verbose
curl --fail --silent --show-error http://127.0.0.1:8080/api/health
```

只有下列结果同时成立，才报告 Chub 核心安装成功：

1. `chub check` 成功；
2. Web 健康接口返回 `status=ok`，且 `chub status --verbose` 显示 Web 与 Quick Worker 均可用；
3. `systemctl --user is-active chub.service` 与 `systemctl --user is-active chub-quick-worker.service` 都返回 `active`；
4. 服务定义、Web 健康和 Worker 健康来自同一次目标设备安装，不以 ZIP 解压、进程创建或单个 HTTP 200 替代。

若 `chub install`、`chub check` 或健康检查失败，停止后续插件、OpenClaw 和微信步骤。保留失败信息给维护者；不得通过直接运行 `main.py`、修改监听地址或跳过 Worker 来宣称服务已安装。

## 5. Windows localhost 与 WSL 恢复验收

先在 WSL 内完成上一节的 loopback 健康检查。AI 随后从 WSL 调用 Windows PowerShell，检查 Windows 主机是否能访问 WSL 转发的 localhost：

```bash
powershell.exe -NoProfile -Command "Invoke-WebRequest -UseBasicParsing http://localhost:8080 | Select-Object -ExpandProperty StatusCode"
```

结果必须为 `200`。若 `powershell.exe` 不可用、请求被拒绝或结果不是 `200`，该项未验证，AI 必须报告 `failed` 或 `recovery_pending`，不得为绕过拒绝改为 `0.0.0.0`、普通局域网监听、端口代理或信任转发 Header。

确认 Windows localhost 后，AI 记录“重启前检查通过”的检查点，并从 WSL 执行一次：

```bash
wsl.exe --shutdown
```

该命令会终止当前 WSL Agent 进程。Agent 编排环境必须重新连接同一 Ubuntu WSL2 发行版和同一 Linux 用户后，继续执行：

```bash
systemctl --user is-active chub.service
systemctl --user is-active chub-quick-worker.service
./scripts/chub check
```

两个服务恢复为 `active` 且 `chub check` 成功，才表示本轮 WSL2 服务恢复验收通过。重连不可用时，AI 报告 `recovery_pending` 并附上重连后必须执行的三条固定检查；重连后失败则报告 `failed`。AI 不得要求维护者代替执行常规恢复检查。

## 6. 可选 Runtime 与后续能力

Chub 核心成功后，才处理随包模块：

1. 维护者在 WSL 内安装 Linux 版 Codex CLI，并亲自完成登录或其他认证；Windows 主机上的 Codex CLI 或登录状态不等于 WSL 服务可用。
2. 将 `bundled-modules/` 中所需 ZIP 分别放入 `data/local/artifacts/plugins/codex-runtime/` 或 `data/local/artifacts/plugins/weixin-orchestration/`。
3. 在 Chub 设置页“插件管理”导入并启用；Codex Runtime 再在其设置页选择当前使用版本。
4. 重新运行 `./scripts/chub check`，再创建一个低风险 Chub Session 验证 Codex Runtime 的最终结果。

未导入任一模块不影响 Chub 核心。模块导入或 Codex 登录失败只影响该模块，不回滚已通过的核心安装。

OpenClaw、微信、Tailnet 与 Debug Chrome 只在核心和 Runtime 已完成后按对应专项文档单独设计、安装和验收；它们失败不得改写 Chub 核心的成功状态。真实微信文字、语音、点击和收件只能由维护者本人在微信客户端完成。

## 7. WSL2 首轮最终验收标准

AI 只能按下列规则报告最终结论：

| 结果 | 必须满足的条件 |
| --- | --- |
| `succeeded` | Ubuntu WSL2、systemd、`systemctl --user`、Python 和基础命令均已确认；项目位于 Linux 文件系统；`chub install`、`chub check`、WSL 内健康接口和 `chub status --verbose` 均通过；Web 与 Quick Worker 的 user service 均为 `active`；Windows PowerShell 对 `http://localhost:<配置端口>` 返回 `200`；执行一次 `wsl.exe --shutdown` 并由 AI 重连后，两个服务再次为 `active` 且 `chub check` 再次通过。若本轮要求 Codex Runtime，还必须完成 ZIP 导入、启用、版本选择和一个低风险 Session 的业务终态确认。 |
| `failed` | 任一必需命令、服务、健康、Windows localhost 或重连后恢复检查失败，或发现目标不是受支持的 Ubuntu WSL2 systemd 环境。报告失败步骤、已确认结果、可操作的恢复条件和未执行步骤；不得把已受理、进程创建或单个 HTTP 200 写成成功。 |
| `recovery_pending` | 仅允许用于 `wsl.exe --shutdown` 已执行且重启前检查通过，但 Agent 运行环境尚未重新连接 WSL，因而无法检查服务恢复的情况。恢复连接后必须继续收敛为 `succeeded` 或 `failed`。 |

最终报告只记录发行版版本、是否启用 systemd/linger、Chub 版本、服务状态、健康结果、Windows localhost 状态、恢复结果、可选模块状态和失败恢复条件；不得记录密码、Token、Cookie、账号或完整配置内容。
