# Chub 正式部署包：AI 安装与验收指引

> 文档类型：随正式部署包交付的目标设备操作指引，不属于项目资料页登记的当前设计文档，也不定义 Chub 的产品能力、架构或模块契约。项目定位、当前能力与设计边界分别以项目 README、能力清单和对应专项设计为准。

本文是解压后目标设备 AI 的唯一部署操作入口。它只完成 Chub 核心部署及适用的平台恢复验收；Runtime、插件包、OpenClaw 和其他第三方能力由工作站启动后维护者根据实际环境手动配置，不属于本流程。目标 AI 必须逐步确认本机环境和每项业务终态，并根据本文报告 `succeeded`、`failed` 或 `recovery_pending`；不得猜测发行版、账号、凭据、OpenClaw 路径、微信 Owner 或浏览器配置。

当前支持 macOS LaunchAgent、原生 Ubuntu systemd user service，以及已启用 systemd 的 Ubuntu WSL2。WSL1、非 Ubuntu WSL、未启用 systemd 的 WSL，或无法使用 `systemctl --user` 的 Ubuntu 环境不得继续执行受管服务安装。Windows 不是 Chub 的原生运行平台；Windows 只通过 WSL2 的 localhost 转发访问 Chub。

来源设备的本机配置、账号、Token、Cookie、浏览器数据、Session、任务和运行态不得复制到目标设备。

## 1. 部署目标、AI 权限与最终结论

1. AI 负责执行本文件中的环境检查、依赖安装、解压、虚拟环境创建、配置文件创建、`chub install`、健康检查、适用的平台恢复检查和部署报告写入；维护者不承担常规安装或部署步骤。
2. 维护者只在 AI 无法取得必要权限时完成系统的 `sudo` 授权，以及 Codex 登录、账号、API Key、OpenClaw、微信绑定、Owner 或其他凭据的本人专属操作。AI 在操作完成后继续执行，不得读取、记录或输出秘密。
3. AI 只可在维护者提供的 ZIP 路径及其本机主目录下的全新目标目录内操作，不得覆盖已有 Chub 目录。项目、虚拟环境、工作目录、配置和运行态必须位于本机文件系统，例如 `/home/<user>/workspace/chub` 或 `$HOME/workspace/chub`，不得放在 `/mnt/c/`、`/mnt/d/` 等 Windows 挂载目录。
4. 部署目录的平级目录是部署报告、隐藏检查点和 `DEPLOYMENT_NOTES.md` 的唯一额外写入位置。它必须位于本机文件系统；AI 只能在其中创建或更新这些部署记录，不能在那里创建或运行 Chub 项目、虚拟环境、配置或运行态。源 ZIP 父目录保持只读，不因部署写入报告或检查点。
5. 首轮成功标准为 Chub 核心、Web、Quick Worker 和适用的平台验收均通过。Codex Runtime、插件包、OpenClaw、微信、Debug Chrome 图形自动化、浏览器 Profile、Windows GUI 集成和 Tailnet 访问均不属于部署流程或部署成功条件。
6. AI 只有在本文件第 8 节所有适用条件均通过、且部署报告已成功写入时才能报告 `succeeded`。任一必需检查或报告写入失败时报告 `failed`；若 WSL2 重启后执行 Agent 尚未重新连接，则报告 `recovery_pending`，不得把重启前的检查当作最终成功。

## 2. 通用环境检查与报告初始化

AI 先确认维护者提供的源 ZIP 是普通文件、目标目录不存在或为空、部署目录的平级目录可安全写入记录。部署问题记录与报告初始化是第一个持久化检查点：在任何环境检查、解压、依赖安装或服务安装前，AI 必须读取或创建 `DEPLOYMENT_NOTES.md`，再创建或恢复报告文件；任一步失败时立即报告 `failed`，不得继续部署。

`DEPLOYMENT_NOTES.md` 固定在 `<deployment-parent>/DEPLOYMENT_NOTES.md`，跨次部署保留。每项记录必须包含部署版本、日期、现象、已确认原因、建议主项目解决方案和下次部署检查条件。部署前，AI 逐项复核旧记录：已解决项移除，未解决项保留并在本次报告中标记。部署后，AI 追加本次新发现的问题；即使问题未阻断部署，例如命令误用或配置修复，也必须记录。该文件只保存简短、脱敏摘要，不得包含账号、凭据、完整路径、完整配置或原始日志。

AI 使用完整的源 ZIP 文件名去掉 `.zip` 后缀，追加 `-report.md` 作为本次固定的报告文件名，**不得追加时间戳或其他字段**，例如：

```text
<deployment-parent>/chub-release-1.2.3-202609181030-a1b2c3d4e5f6-report.md
```

若同名文件已存在，AI 仅在 `-report` 后、`.md` 前追加受控序号后创建新文件，例如 `...-report-2.md`，绝不覆盖既有报告。部署目录平级目录还允许存在唯一隐藏检查点 `.<source-zip-stem>.deployment-checkpoint`，它只保存报告文件名、当前状态和当前阶段。WSL2 重连时，AI 必须先读取该检查点恢复 `REPORT_PATH`，并更新同一份报告，不得新建第二份报告。报告文件仅记录本次部署的摘要，不记录源 ZIP 的完整路径、Windows 用户目录、密码、Token、Cookie、账号、完整配置、原始日志或完整命令输出。AI 在开始时写入 ZIP 文件名、报告文件名、报告生成时间、目标平台识别结果、当前阶段和 `in_progress` 状态，并持续记录遇到的问题、实际采取的解决动作、解决结果、未解决问题和恢复条件；每完成或失败一个阶段都必须同步更新报告和检查点。

AI 按以下固定步骤初始化或恢复报告，其中 `SOURCE_ZIP` 是维护者提供的 ZIP 路径。若没有检查点且目标文件已经存在，循环只生成下一个受控序号；检查点存在时只恢复其中登记的报告，不选择其他报告：

```bash
SOURCE_ZIP="<provided-source-zip>"
TARGET_DIR="$HOME/workspace/chub"
test -f "$SOURCE_ZIP"
SOURCE_ZIP_NAME="$(basename "$SOURCE_ZIP")"
REPORT_DIRECTORY="$(dirname "$TARGET_DIR")"
REPORT_STEM="${SOURCE_ZIP_NAME%.zip}"
REPORT_PATH="$REPORT_DIRECTORY/$REPORT_STEM-report.md"
DEPLOYMENT_CHECKPOINT_PATH="$REPORT_DIRECTORY/.$REPORT_STEM.deployment-checkpoint"
DEPLOYMENT_NOTES_PATH="$REPORT_DIRECTORY/DEPLOYMENT_NOTES.md"
umask 077
mkdir -p "$REPORT_DIRECTORY"
test -d "$REPORT_DIRECTORY"
test ! -L "$REPORT_DIRECTORY"
test ! -L "$DEPLOYMENT_CHECKPOINT_PATH"
if [ -e "$DEPLOYMENT_NOTES_PATH" ]; then
  test -f "$DEPLOYMENT_NOTES_PATH"
  test ! -L "$DEPLOYMENT_NOTES_PATH"
  test "$(wc -c < "$DEPLOYMENT_NOTES_PATH")" -le 65536
else
  printf '# Chub Deployment Notes\n' > "$DEPLOYMENT_NOTES_PATH"
  chmod 600 "$DEPLOYMENT_NOTES_PATH"
fi
sed -n '1,1000p' "$DEPLOYMENT_NOTES_PATH"
record_checkpoint() {
  printf 'report_file=%s\nstatus=%s\nstage=%s\n' \
    "$(basename "$REPORT_PATH")" "$1" "$2" > "$DEPLOYMENT_CHECKPOINT_PATH"
}
record_stage() {
  printf '\n- Status: %s\n- Current stage: %s\n' "$1" "$2" >> "$REPORT_PATH"
  record_checkpoint "$1" "$2"
}
if [ -f "$DEPLOYMENT_CHECKPOINT_PATH" ]; then
  REPORT_FILE="$(sed -n 's/^report_file=//p' "$DEPLOYMENT_CHECKPOINT_PATH" | head -n 1)"
  case "$REPORT_FILE" in ''|*/*) exit 1 ;; esac
  REPORT_PATH="$REPORT_DIRECTORY/$REPORT_FILE"
  test ! -L "$REPORT_PATH"
  test -s "$REPORT_PATH"
else
  REPORT_INDEX=2
  while [ -e "$REPORT_PATH" ]; do
    REPORT_PATH="$REPORT_DIRECTORY/$REPORT_STEM-report-$REPORT_INDEX.md"
    REPORT_INDEX=$((REPORT_INDEX + 1))
  done
  printf '# Chub Deployment Report\n\n- Package: %s\n- Report: %s\n- Status: in_progress\n- Current stage: report_initialized\n' \
    "$SOURCE_ZIP_NAME" "$(basename "$REPORT_PATH")" > "$REPORT_PATH"
  record_checkpoint in_progress report_initialized
fi
test -s "$REPORT_PATH"
```

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

前三项必须同时成立：`WSL_DISTRO_NAME` 非空、当前内核标记为 Microsoft/WSL、`wsl.exe -l -v` 的输出中存在同名发行版且其 Version 列为 `2`。AI 必须把该匹配结果写入报告；仅看到 Windows 上其他发行版为 Version 2 不足以通过。随后 PID 1 必须为 `systemd` 且 `systemctl --user` 可用。任一条件不满足时，不得按 WSL2 路径继续。WSL2 使用与原生 Ubuntu 相同的发行版默认 Python 包，不安装固定的 `python3.12`、`python3.12-venv` 或 `python3.12-dev` 包。第 7 节的 Windows localhost 和 WSL 恢复检查是 WSL2 的额外必需项。

## 4. 固定解压、虚拟环境、依赖和本机配置

AI 使用以下固定流程解压。`TARGET_DIR` 必须位于本机文件系统且为不存在或空的非符号链接目录；ZIP 完整性、解压结果和项目根目录不符合任一条件时停止并以当前阶段写入报告。正式 ZIP 会为受管运行脚本写入 Unix `0755` 权限；但 Windows 解压工具、挂载文件系统或部分 ZIP 解压实现仍可能丢弃该元数据。权限恢复必须在创建虚拟环境和执行任何 Chub 脚本前完成：

```bash
test ! -L "$TARGET_DIR"
if [ -e "$TARGET_DIR" ]; then
  test -d "$TARGET_DIR"
  test -z "$(find "$TARGET_DIR" -mindepth 1 -maxdepth 1 -print -quit)"
else
  mkdir -p "$TARGET_DIR"
fi
unzip -t "$SOURCE_ZIP" >/dev/null
unzip -q "$SOURCE_ZIP" -d "$TARGET_DIR"
cd "$TARGET_DIR"
test -f pyproject.toml
test -f requirements.txt
test -f config/settings.example.yaml
test -f scripts/chub
find scripts -type f -exec chmod u+x {} +
test -x scripts/chub
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip --timeout 30 --retries 1
.venv/bin/python -m pip install --timeout 30 --retries 1 -r requirements.txt
cp config/settings.example.yaml config/settings.local.yaml
```

完成本节后执行 `record_stage in_progress core_dependencies_installed`。后续每个章节完成、失败或进入 WSL 恢复等待时，同样调用 `record_stage` 写入对应状态和阶段。

当前维护入口 `chub runtime dependencies` 同样以 `requirements.txt` 修复环境，因此首次部署继续使用该文件作为依赖安装入口；不得只修改文档改为 editable 安装而保留两个不一致的依赖维护入口。

若官方 Python 源安装失败，AI 记录失败类别和已完成下载，不无限等待或无提示重试。维护者已通过环境变量 `CHUB_PIP_INDEX_URL` 提供可信镜像时，AI 可使用该地址重试同一条依赖安装命令：

```bash
.venv/bin/python -m pip install --timeout 30 --retries 1 --index-url "$CHUB_PIP_INDEX_URL" -r requirements.txt
```

未提供可信镜像时，报告依赖安装失败，不在本文中猜测或写死公共镜像地址。

AI 从示例创建并结构化填写 `config/settings.local.yaml`。首轮核心验收只需调整下列字段，其余示例字段保持默认；不得使用无上下文正则替换 YAML 字段：

```yaml
node:
  id: "<new-device-id>"
  name: "<readable-device-name>"
  type: "macos" # Ubuntu 和 WSL2 使用 "ubuntu"
security:
  allow_tailscale: false
```

- `node.id` 必须是新设备唯一标识，`node.name` 必须可读，`node.type` 必须与已确认的平台匹配；
- `app.name` 与 `app.page_title` 由部署步骤从 `node.name` 同步生成，页面标题固定为 `<node.name> · Hub`；
- `server.port` 默认是 `8080`。仅在端口冲突时修改为未被本机占用的端口；
- `ai_runtime.shared.workspace` 必须是本机受信工作目录。默认 `~/workspace` 可保留，但目标目录不存在时创建它；
- 首轮本机验收保持 `security.allow_tailscale: false`，Tailnet 验收另行执行；
- 不填写或复制通知、OpenClaw、微信、API Key、Token、Cookie、账号或其他凭据。

在项目根目录使用 YAML 解析器精确更新这些路径；新创建的本机配置不要求保留示例注释或排版：

```bash
CHUB_NODE_ID="<new-device-id>"
CHUB_NODE_NAME="<readable-device-name>"
CHUB_NODE_TYPE="ubuntu" # macOS 使用 macos
.venv/bin/python - "$CHUB_NODE_ID" "$CHUB_NODE_NAME" "$CHUB_NODE_TYPE" <<'PY'
import sys
from pathlib import Path

import yaml

path = Path("config/settings.local.yaml")
payload = yaml.safe_load(path.read_text(encoding="utf-8"))
if not isinstance(payload, dict) or not isinstance(payload.get("node"), dict):
    raise SystemExit("settings.local.yaml has no valid node mapping")
payload["node"].update(
    {"id": sys.argv[1], "name": sys.argv[2], "type": sys.argv[3]}
)
app = payload.get("app")
if not isinstance(app, dict):
    raise SystemExit("settings.local.yaml has no valid app mapping")
app["name"] = sys.argv[2]
app["page_title"] = f"{sys.argv[2]} · Hub"
security = payload.setdefault("security", {})
if not isinstance(security, dict):
    raise SystemExit("settings.local.yaml has no valid security mapping")
security["allow_tailscale"] = False
path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
PY
```

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

首次执行 `./scripts/chub install` 时，Chub 会在 `TARGET_DIR` 的父目录创建 `chub-local-modules/` 与空索引 `chub-modules.json`。该目录用于维护者在工作站启动后登记设备定制模块；部署流程不写入模块内容、不扫描或执行其中脚本，也不将其配置写入部署报告。若目录已存在，安装、升级和恢复均不得覆盖、删除或修改它。

只有下列结果同时成立，才报告 Chub 核心安装成功：

1. `chub check` 成功；未安装 Codex CLI 时出现“Codex Runtime will be disabled”告警是首轮核心部署中的非阻断信息，但不能把 Codex Runtime 记为可用；
2. Web 健康接口返回成功响应且 `data.status=ok`，`chub status --verbose` 显示 Web 与 Quick Worker 均可用；
3. Ubuntu 和 WSL2 的 `systemctl --user is-active chub.service` 与 `systemctl --user is-active chub-quick-worker.service` 都返回 `active`；macOS 由 `chub status --verbose` 的 LaunchAgent 状态确认；
4. 服务定义、Web 健康和 Worker 健康来自同一次目标设备安装，不以 ZIP 解压、进程创建或单个 HTTP 200 替代。

若 `chub install`、`chub check` 或健康检查失败，停止后续插件、OpenClaw 和微信步骤。报告失败阶段、已确认结果和恢复条件；不得通过直接运行 `main.py`、修改监听地址或跳过 Worker 来宣称服务已安装。

核心服务验证通过后执行 `record_stage in_progress core_services_verified`。macOS 或原生 Ubuntu 的第 6 节完成后，再执行 `record_stage in_progress platform_recovery_verified`。

## 6. 通用恢复确认

macOS 和原生 Ubuntu 在核心安装后重新执行一次 `./scripts/chub check`，并将服务和健康终态写入部署报告。原生 Ubuntu 必须记录 `Linger=yes` 的配置结果；本轮不因未要求的主机重启而中断其他任务，因此报告只能写“用户级服务恢复已配置，未做真实主机重启验证”，不得写成已验证的重启恢复。需要把主机重启恢复纳入成功标准时，必须由维护者明确批准一次独立的重启验收。

部署报告必须持续记录：平台和 Python 版本、安装目录的非敏感标识、各阶段状态、遇到的问题、AI 实际采取的解决动作、解决结果、未解决问题、恢复条件和 Web/Worker/健康检查终态。报告使用简短摘要，不记录 Runtime、插件包或第三方能力的配置状态，也不粘贴原始日志、配置内容或秘密。

## 7. WSL2 的 Windows localhost 与恢复验收

本节只适用于 Ubuntu WSL2。先在 WSL 内完成第 5 节的 loopback 健康检查，再从 WSL 调用 Windows PowerShell，检查 Windows 主机能访问 WSL 转发的 localhost：

```bash
powershell.exe -NoProfile -Command "Invoke-WebRequest -UseBasicParsing http://localhost:${CHUB_PORT} | Select-Object -ExpandProperty StatusCode"
```

结果必须为 `200`。若 `powershell.exe` 不可用、请求被拒绝或结果不是 `200`，该项失败；AI 不得改为 `0.0.0.0`、普通局域网监听、端口代理或信任转发 Header 绕过。

确认 Windows localhost 后，AI 先调用 `record_stage recovery_pending wsl_shutdown_requested`，记录“重启前检查通过”的检查点和重连后必须执行的四条命令：

```bash
systemctl --user is-active chub.service
systemctl --user is-active chub-quick-worker.service
cd "$TARGET_DIR" && ./scripts/chub check
powershell.exe -NoProfile -Command "Invoke-WebRequest -UseBasicParsing http://localhost:${CHUB_PORT} | Select-Object -ExpandProperty StatusCode"
```

`wsl.exe --shutdown` 会终止当前 Windows 用户下的全部 WSL 发行版及其进程。AI 必须在报告中写明这一直接影响，并取得维护者对本次 WSL 恢复验收的明确确认后，才可从 WSL 执行：

```bash
wsl.exe --shutdown
```

该命令会终止当前 WSL Agent 进程。Agent 编排环境重新连接同一 Ubuntu WSL2 发行版和同一 Linux 用户后，必须执行上述四项检查并调用 `record_stage in_progress wsl_recovery_verified` 更新同一份报告。两个服务恢复为 `active`、`chub check` 成功且 Windows localhost 请求再次返回 `200`，才表示本轮 WSL2 服务恢复验收通过。Windows localhost 转发未就绪时，报告 `failed` 并说明恢复条件；不得无界或自动重复执行 `wsl.exe --shutdown`，也不得依赖或检查特定转发进程名称。重连不可用时保留 `recovery_pending` 报告；AI 不得要求维护者代替执行常规恢复检查。

### WSL2 异常关机循环的条件化排查

以下流程只在 WSL2 重连后 Chub、Quick Worker 或 Windows localhost 反复中断时使用，不是正常部署步骤，也不因其他设备曾出现该问题而预先修改系统更新策略。

2026-09 的一台 Ubuntu WSL2 设备曾在 `wsl.exe --shutdown` 后进入约 2 至 4 分钟一次的 poweroff/reboot 循环：用户服务被停止，`8080` 监听和 Windows localhost 转发随之中断。该设备的 journal 已确认 Ubuntu 自动更新的关机路径触发 poweroff，导致站点无法稳定启动。此结论只适用于该设备；其他 WSL2 设备必须先完成以下取证，不能仅凭服务为 `active` 就归因为相同问题：

```bash
cat /proc/sys/kernel/random/boot_id
uptime
systemctl status unattended-upgrades.service --no-pager
journalctl -b -u unattended-upgrades.service --no-pager
systemctl --user is-active chub.service
systemctl --user is-active chub-quick-worker.service
powershell.exe -NoProfile -Command "Invoke-WebRequest -UseBasicParsing http://localhost:${CHUB_PORT} | Select-Object -ExpandProperty StatusCode"
```

报告和 `DEPLOYMENT_NOTES.md` 必须记录脱敏后的设备/WSL/Ubuntu 版本、boot ID 是否变化、相关 unit 状态、失败现象、已确认原因、采取的恢复动作及安全更新影响；不得粘贴完整 journal 或本机路径。若已确认同一原因，维护者可明确批准在该设备上执行以下恢复动作：

```bash
sudo systemctl disable --now unattended-upgrades.service
sudo systemctl mask unattended-upgrades.service
```

不得用宽泛的 `sed` 替换修改 `/etc/apt/apt.conf.d/`，也不得在未经确认的设备上自动执行上述命令。该恢复动作会关闭该设备的自动安全更新；部署报告必须把人工更新责任列为未消除的运维风险。恢复后应观察至少 5 分钟，确认 boot ID 未变化、两个 Chub 用户服务持续为 `active` 且 Windows localhost 持续返回 `200`。仍发生 poweroff/reboot 时，保留 `failed` 或 `recovery_pending`，并将取证结果作为 WSL/Ubuntu 环境问题继续排查，不归咎于 Chub 服务安装。

## 8. 核心部署结论与后续手动配置

Chub 核心安装和适用的平台恢复验收通过后，本部署流程结束。随包的 Runtime 和任务编排插件 ZIP 仅作为可供后续导入的制品，不在部署过程中导入、启用、选择版本或验证；Codex 登录、OpenClaw、微信、Tailnet、Debug Chrome 图形自动化和其他第三方能力同样不在本流程中执行、提示或判定。维护者在工作站启动后，根据实际环境和对应专项文档手动决定是否配置这些能力；它们的成功或失败不回滚已完成的 Chub 核心部署。

AI 在报告 `succeeded`、`failed` 或 `recovery_pending` 前，必须先调用 `record_stage` 把相同的最终状态、最后完成或失败的阶段、服务与健康终态以及恢复条件写入 `REPORT_PATH` 和检查点，并执行 `test -s "$REPORT_PATH"`。报告文件不存在、为空、无法更新或最终状态与任务结论不一致时，部署结论只能是 `failed`；不得将 Web、Worker 或 HTTP 健康检查通过单独报告为部署成功。只有写入最终报告成功后，才可删除 `DEPLOYMENT_CHECKPOINT_PATH`；删除失败不改变报告已写入的事实，但必须在报告中记录该残留文件和清理方式。

核心部署成功时使用以下固定收尾步骤；失败和 `recovery_pending` 保留检查点，供后续恢复同一报告：

```bash
record_stage succeeded core_deployment_completed
test -s "$REPORT_PATH"
if ! rm -f "$DEPLOYMENT_CHECKPOINT_PATH"; then
  printf '\n- Checkpoint cleanup: pending; remove %s after confirming this report.\n' \
    "$(basename "$DEPLOYMENT_CHECKPOINT_PATH")" >> "$REPORT_PATH"
fi
```

AI 只能按下列规则报告最终结论：

| 结果 | 必须满足的条件 |
| --- | --- |
| `succeeded` | 第 2 至第 6 节通用条件均通过，适用的平台关注事项已通过，部署报告已写入部署目录平级目录；WSL2 还必须通过第 7 节的 Windows localhost 和重连后服务恢复。Runtime、插件包、OpenClaw 和其他第三方能力不属于本条件。 |
| `failed` | 任一必需环境、依赖、配置、服务、健康、平台恢复检查或报告写入失败。已可写入的报告必须说明失败步骤、已确认结果、可操作的恢复条件和未执行步骤；若 Chub 核心已成功但报告无法写入，AI 的最终结论必须明确记录核心状态与报告交付失败。 |
| `recovery_pending` | 仅适用于 WSL2 已执行 `wsl.exe --shutdown`、重启前检查通过、报告检查点已写入，但 Agent 尚未重新连接而无法完成恢复检查的情况。恢复连接后必须继续收敛为 `succeeded` 或 `failed`。 |

最终报告只记录版本、平台、是否启用 systemd/linger、Chub 版本、服务状态、健康结果、适用的平台专项状态、恢复结果、可选模块状态和失败恢复条件；不得记录密码、Token、Cookie、账号、完整路径、完整配置或原始日志。
