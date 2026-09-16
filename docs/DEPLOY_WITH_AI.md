# Chub 正式部署包：AI 安装与验收指引

本文是解压后目标设备 AI 的唯一部署操作入口。它使用一套通用流程完成 Chub 核心部署，再按目标平台执行必要的关注事项和恢复验收。目标 AI 必须逐步确认本机环境和每项业务终态，并根据本文报告 `succeeded`、`failed` 或 `recovery_pending`；不得猜测发行版、账号、凭据、OpenClaw 路径、微信 Owner 或浏览器配置。

当前支持 macOS LaunchAgent、原生 Ubuntu systemd user service，以及已启用 systemd 的 Ubuntu WSL2。WSL1、非 Ubuntu WSL、未启用 systemd 的 WSL，或无法使用 `systemctl --user` 的 Ubuntu 环境不得继续执行受管服务安装。Windows 不是 Chub 的原生运行平台；Windows 只通过 WSL2 的 localhost 转发访问 Chub。

来源设备的本机配置、账号、Token、Cookie、浏览器数据、Session、任务和运行态不得复制到目标设备。

## 1. 部署目标、AI 权限与最终结论

1. AI 负责执行本文件中的环境检查、依赖安装、解压、虚拟环境创建、配置文件创建、`chub install`、健康检查、适用的平台恢复检查和部署报告写入；维护者不承担常规安装或部署步骤。
2. 维护者只在 AI 无法取得必要权限时完成系统的 `sudo` 授权，以及 Codex 登录、账号、API Key、OpenClaw、微信绑定、Owner 或其他凭据的本人专属操作。AI 在操作完成后继续执行，不得读取、记录或输出秘密。
3. AI 只可在维护者提供的 ZIP 路径及其本机主目录下的全新目标目录内操作，不得覆盖已有 Chub 目录。项目、虚拟环境、工作目录、配置和运行态必须位于本机文件系统，例如 `/home/<user>/workspace/chub` 或 `$HOME/workspace/chub`，不得放在 `/mnt/c/`、`/mnt/d/` 等 Windows 挂载目录。
4. 源 ZIP 的父目录是部署报告的唯一额外写入位置。它可以是 Windows 挂载目录，但 AI 只能在该目录创建本次部署的报告文件，不能在那里创建或运行 Chub 项目、虚拟环境、配置或运行态。
5. 首轮成功标准为 Chub 核心、Web、Quick Worker 和适用的平台验收均通过。Codex Runtime 仅在维护者要求本轮包含 AI Runtime 且已完成其专属登录时纳入成功标准。OpenClaw、微信、Debug Chrome 图形自动化、浏览器 Profile、Windows GUI 集成和 Tailnet 访问均不属于首轮成功条件。
6. AI 只有在本文件第 8 节所有适用条件均通过、且部署报告已成功写入时才能报告 `succeeded`。任一必需检查或报告写入失败时报告 `failed`；若 WSL2 重启后执行 Agent 尚未重新连接，则报告 `recovery_pending`，不得把重启前的检查当作最终成功。

## 2. 通用环境检查与报告初始化

AI 先确认维护者提供的源 ZIP 是普通文件、目标目录不存在或为空、源 ZIP 父目录可写入报告。AI 从源 ZIP 的文件名创建本次固定的 `REPORT_PATH`，例如：

```text
<source-zip-parent>/chub-deployment-report-<UTC时间>.md
```

若同名文件已存在，AI 仅追加受控序号后创建新文件，绝不覆盖既有报告。`REPORT_PATH` 必须写入报告头部，并作为部署任务检查点保存；WSL2 重连后必须恢复并更新该路径指向的同一份报告，不得新建第二份报告。报告文件仅记录本次部署的摘要，不记录源 ZIP 的完整路径、Windows 用户目录、密码、Token、Cookie、账号、完整配置、原始日志或完整命令输出。AI 在开始时写入 ZIP 文件名、报告生成时间、目标平台识别结果和当前阶段；每完成或失败一个阶段就更新同一份报告。

在目标设备的本机终端执行：

```bash
uname -s
python3 --version
python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)'
command -v curl
command -v unzip
```

只有 Python 为 3.12 或更高版本、且 `curl` 与 `unzip` 可用时才进入通用安装步骤。若缺少组件，按第 3 节的平台关注事项安装并重新检查。AI 不得改为公网监听、普通局域网监听、前台进程或跳过 Quick Worker 来宣称部署成功。

## 3. 平台识别与关注事项

### macOS

`uname -s` 为 `Darwin` 时，AI 确认当前用户可使用 `launchctl`。若 `python3` 缺失或版本低于 3.12，且本机已安装 Homebrew，AI 可在维护者完成必要授权后执行 `brew install python`，再重新执行第 2 节版本检查；若没有受信的本机包管理器，报告阻塞条件，不猜测或下载任意安装器。

核心安装后，macOS 仅需按第 6 节确认 `chub check`、`chub status --verbose` 和 loopback 健康接口；`chub status --verbose` 必须显示 Web 和 Quick Worker 可用。macOS 不执行 systemd、linger、Windows localhost 或 `wsl.exe --shutdown` 检查。

### 原生 Ubuntu

`uname -s` 为 `Linux` 且 `/etc/os-release` 的 `ID` 为 `ubuntu` 时，AI 确认 PID 1 为 `systemd`，且 `systemctl --user show-environment` 成功。原生 Ubuntu 与 WSL2 都必须检查 linger：

```bash
loginctl show-user "$USER" -p Linger
```

若显示 `Linger=no`，AI 请求一次必要的维护者授权后执行并再次确认：

```bash
sudo loginctl enable-linger "$USER"
loginctl show-user "$USER" -p Linger
```

linger 仅确认用户级服务可在未登录时由 systemd user manager 保持；它不等于已完成真实主机重启恢复验证。缺少基础组件时使用发行版默认 Python 包名：

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip python3-dev build-essential curl unzip
```

安装后重新运行第 2 节的 Python 版本检查。若发行版默认 Python 低于 3.12，AI 报告不满足 Chub 支持条件，不以版本固定的 apt 包、第三方 PPA 或未登记来源绕过。

### Ubuntu WSL2

除原生 Ubuntu 的全部要求外，AI 还必须确认当前会话确实是 WSL2，而不是只看到 Windows 上存在某个 WSL2 发行版：

```bash
test -n "${WSL_DISTRO_NAME:-}"
grep -qi microsoft /proc/sys/kernel/osrelease
wsl.exe -l -v
ps -p 1 -o comm=
systemctl --user show-environment >/dev/null
```

前三项必须同时成立：`WSL_DISTRO_NAME` 非空、当前内核标记为 Microsoft/WSL、`wsl.exe -l -v` 的输出中同名发行版为 Version 2。随后 PID 1 必须为 `systemd` 且 `systemctl --user` 可用。任一条件不满足时，不得按 WSL2 路径继续。WSL2 使用与原生 Ubuntu 相同的发行版默认 Python 包，不安装固定的 `python3.12`、`python3.12-venv` 或 `python3.12-dev` 包。第 7 节的 Windows localhost 和 WSL 恢复检查是 WSL2 的额外必需项。

## 4. 解压、虚拟环境、依赖和本机配置

AI 在全新的本机目标目录解压 ZIP，并确认解压根目录包含 `pyproject.toml`、`requirements.txt`、`config/settings.example.yaml` 和 `scripts/chub`。ZIP 解压后可能不保留脚本执行权限，以下恢复步骤是固定步骤：

```bash
chmod u+x scripts/chub scripts/chub-web-restart scripts/chub-system-upgrade-start scripts/chub-system-upgrade-restart
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip --timeout 30 --retries 1
.venv/bin/python -m pip install --timeout 30 --retries 1 -r requirements.txt
cp config/settings.example.yaml config/settings.local.yaml
```

当前维护入口 `chub runtime dependencies` 同样以 `requirements.txt` 修复环境，因此首次部署继续使用该文件作为依赖安装入口；不得只修改文档改为 editable 安装而保留两个不一致的依赖维护入口。

若官方 Python 源安装失败，AI 记录失败类别和已完成下载，不无限等待或无提示重试。维护者已通过环境变量 `CHUB_PIP_INDEX_URL` 提供可信镜像时，AI 可使用该地址重试同一条依赖安装命令：

```bash
.venv/bin/python -m pip install --timeout 30 --retries 1 --index-url "$CHUB_PIP_INDEX_URL" -r requirements.txt
```

未提供可信镜像时，报告依赖安装失败，不在本文中猜测或写死公共镜像地址。

AI 从示例创建并填写 `config/settings.local.yaml`。首轮核心验收只需调整下列字段，其余示例字段保持默认：

```yaml
node:
  id: "<new-device-id>"
  name: "<readable-device-name>"
  type: "macos" # Ubuntu 和 WSL2 使用 "ubuntu"
security:
  allow_tailscale: false
```

- `node.id` 必须是新设备唯一标识，`node.name` 必须可读，`node.type` 必须与已确认的平台匹配；
- `server.port` 默认是 `8080`。仅在端口冲突时修改为未被本机占用的端口；
- `ai_runtime.codex.workspace` 必须是本机受信工作目录。默认 `~/workspace` 可保留，但目标目录不存在时创建它；
- 首轮本机验收保持 `security.allow_tailscale: false`，Tailnet 验收另行执行；
- 不填写或复制通知、OpenClaw、微信、API Key、Token、Cookie、账号或其他凭据。

配置文件只保存在目标设备，不能提交、传回或写入部署报告。填写需要账号、Token、Cookie、API Key、OpenClaw 或微信信息时，AI 停止该子项并向维护者请求专属输入；首轮核心验收不需要这些字段。

## 5. 配置端口与核心安装

在项目根目录从 Chub 配置读取实际端口：

```bash
CHUB_PORT="$(.venv/bin/python -c 'from app.core.config import load_settings; print(load_settings().server.port)')"
```

然后执行固定核心安装和确认：

```bash
./scripts/chub install
./scripts/chub check
./scripts/chub status --verbose
curl --fail --silent --show-error "http://127.0.0.1:${CHUB_PORT}/api/health"
```

只有下列结果同时成立，才报告 Chub 核心安装成功：

1. `chub check` 成功；未安装 Codex CLI 时出现“Codex Runtime will be disabled”告警是首轮核心部署中的非阻断信息，但不能把 Codex Runtime 记为可用；
2. Web 健康接口返回成功响应且 `data.status=ok`，`chub status --verbose` 显示 Web 与 Quick Worker 均可用；
3. Ubuntu 和 WSL2 的 `systemctl --user is-active chub.service` 与 `systemctl --user is-active chub-quick-worker.service` 都返回 `active`；macOS 由 `chub status --verbose` 的 LaunchAgent 状态确认；
4. 服务定义、Web 健康和 Worker 健康来自同一次目标设备安装，不以 ZIP 解压、进程创建或单个 HTTP 200 替代。

若 `chub install`、`chub check` 或健康检查失败，停止后续插件、OpenClaw 和微信步骤。报告失败阶段、已确认结果和恢复条件；不得通过直接运行 `main.py`、修改监听地址或跳过 Worker 来宣称服务已安装。

## 6. 通用恢复确认

macOS 和原生 Ubuntu 在核心安装后重新执行一次 `./scripts/chub check`，并将服务和健康终态写入部署报告。原生 Ubuntu 必须记录 `Linger=yes` 的配置结果；本轮不因未要求的主机重启而中断其他任务，因此报告只能写“用户级服务恢复已配置，未做真实主机重启验证”，不得写成已验证的重启恢复。需要把主机重启恢复纳入成功标准时，必须由维护者明确批准一次独立的重启验收。

部署报告必须记录：平台和 Python 版本、安装目录的非敏感标识、各阶段状态、遇到的问题、AI 实际采取的解决动作、解决结果、未解决问题、恢复条件、Web/Worker/健康检查终态，以及本轮可选模块的状态。报告使用简短摘要，不粘贴原始日志、配置内容或秘密。

## 7. WSL2 的 Windows localhost 与恢复验收

本节只适用于 Ubuntu WSL2。先在 WSL 内完成第 5 节的 loopback 健康检查，再从 WSL 调用 Windows PowerShell，检查 Windows 主机能访问 WSL 转发的 localhost：

```bash
powershell.exe -NoProfile -Command "Invoke-WebRequest -UseBasicParsing http://localhost:${CHUB_PORT} | Select-Object -ExpandProperty StatusCode"
```

结果必须为 `200`。若 `powershell.exe` 不可用、请求被拒绝或结果不是 `200`，该项失败；AI 不得改为 `0.0.0.0`、普通局域网监听、端口代理或信任转发 Header 绕过。

确认 Windows localhost 后，AI 先将部署报告更新为 `recovery_pending`，记录“重启前检查通过”的检查点和重连后必须执行的三条命令：

```bash
systemctl --user is-active chub.service
systemctl --user is-active chub-quick-worker.service
cd <chub-install-directory> && ./scripts/chub check
```

随后从 WSL 执行：

```bash
wsl.exe --shutdown
```

该命令会终止当前 WSL Agent 进程。Agent 编排环境重新连接同一 Ubuntu WSL2 发行版和同一 Linux 用户后，必须执行上述三项检查并更新同一份报告。两个服务恢复为 `active` 且 `chub check` 成功，才表示本轮 WSL2 服务恢复验收通过。重连不可用时保留 `recovery_pending` 报告；重连后失败则更新为 `failed`。AI 不得要求维护者代替执行常规恢复检查。

## 8. 可选 Runtime、最终结论与报告交付

Chub 核心成功后，才处理随包模块：

1. 维护者在实际运行 Chub 的系统内安装 Codex CLI，并亲自完成登录或其他认证；Windows 主机上的 Codex CLI 或登录状态不等于 WSL 服务可用。
2. 将 `bundled-modules/` 中所需 ZIP 分别放入 `data/local/artifacts/plugins/codex-runtime/` 或 `data/local/artifacts/plugins/weixin-orchestration/`。
3. 在 Chub 设置页“插件管理”导入并启用；Codex Runtime 再在其设置页选择当前使用版本。
4. 重新运行 `./scripts/chub check`，再创建一个低风险 Chub Session 验证 Codex Runtime 的最终结果。

未导入任一模块不影响 Chub 核心。模块导入或 Codex 登录失败只影响该模块，不回滚已通过的核心安装。OpenClaw、微信、Tailnet 与 Debug Chrome 只在核心和 Runtime 已完成后按对应专项文档单独设计、安装和验收；真实微信文字、语音、点击和收件只能由维护者本人在微信客户端完成。

AI 只能按下列规则报告最终结论：

| 结果 | 必须满足的条件 |
| --- | --- |
| `succeeded` | 第 2 至第 6 节通用条件均通过，适用的平台关注事项已通过，部署报告已写入源 ZIP 父目录；WSL2 还必须通过第 7 节的 Windows localhost 和重连后服务恢复。若本轮要求 Codex Runtime，还必须完成 ZIP 导入、启用、版本选择和一个低风险 Session 的业务终态确认。 |
| `failed` | 任一必需环境、依赖、配置、服务、健康、平台恢复检查或报告写入失败。已可写入的报告必须说明失败步骤、已确认结果、可操作的恢复条件和未执行步骤；若 Chub 核心已成功但报告无法写入，AI 的最终结论必须明确记录核心状态与报告交付失败。 |
| `recovery_pending` | 仅适用于 WSL2 已执行 `wsl.exe --shutdown`、重启前检查通过、报告检查点已写入，但 Agent 尚未重新连接而无法完成恢复检查的情况。恢复连接后必须继续收敛为 `succeeded` 或 `failed`。 |

最终报告只记录版本、平台、是否启用 systemd/linger、Chub 版本、服务状态、健康结果、适用的平台专项状态、恢复结果、可选模块状态和失败恢复条件；不得记录密码、Token、Cookie、账号、完整路径、完整配置或原始日志。
