#!/usr/bin/env bash

# Internal fixed-action adapter. This is intentionally not a generic service
# manager and rejects every resource or action outside the listed operation.
set -euo pipefail

SCRIPT_DIR="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PYTHON_BIN="$PROJECT_ROOT/.venv/bin/python"
PLATFORM="${CHUB_TEST_PLATFORM:-$(uname -s)}"
SERVICE_NAME="chub-debug-chrome"
CORE_SERVICE_NAME="chub"
WORKER_SERVICE_NAME="chub-quick-worker"
SYSTEM_UPGRADE_SERVICE_NAME="chub-system-upgrade"
MACOS_LABEL="com.chub.node"
WORKER_MACOS_LABEL="com.chub.quick-worker"
SYSTEM_UPGRADE_MACOS_LABEL="com.chub.system-upgrade"
MAIN_FILE="$PROJECT_ROOT/main.py"
SERVICE_LOG_DIR="${CHUB_SERVICE_LOG_DIR:-$PROJECT_ROOT/logs}"
SERVICE_LOG_MAX_BYTES=$((2 * 1024 * 1024))
RUNTIME_PATH="${CHUB_SERVICE_RUNTIME_PATH:-$PROJECT_ROOT/.venv/bin:$HOME/.local/bin:$HOME/.npm-global/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin}"
SYSTEMD_DIR="${CHUB_SYSTEMD_USER_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user}"
LAUNCH_AGENTS_DIR="${CHUB_LAUNCH_AGENTS_DIR:-$HOME/Library/LaunchAgents}"

add_runtime_command_dir() {
    local command_path command_dir
    command_path="$(type -P "$1" 2>/dev/null || true)"
    [[ -n "$command_path" ]] || return 0
    command_dir="$(cd -P "$(dirname "$command_path")" && pwd)"
    case ":$RUNTIME_PATH:" in
        *":$command_dir:"*) ;;
        *) RUNTIME_PATH="$command_dir:$RUNTIME_PATH" ;;
    esac
}

if [[ -z "${CHUB_SERVICE_RUNTIME_PATH:-}" ]]; then
    add_runtime_command_dir codex
    add_runtime_command_dir docker
fi

fail() {
    echo "service-management: $*" >&2
    exit 1
}

prepare_definition_path() {
    local path="$1"
    mkdir -p "$(dirname "$path")"
    chmod 700 "$(dirname "$path")"
    "$PYTHON_BIN" - "$path" <<'PY'
import os
import stat
import sys
from pathlib import Path

path = Path(sys.argv[1])
parent = path.parent.lstat()
if (
    not stat.S_ISDIR(parent.st_mode)
    or stat.S_ISLNK(parent.st_mode)
    or parent.st_uid != os.getuid()
    or stat.S_IMODE(parent.st_mode) & 0o077
):
    raise SystemExit("service definition directory is unsafe")
try:
    metadata = path.lstat()
except FileNotFoundError:
    raise SystemExit(0)
if (
    stat.S_ISLNK(metadata.st_mode)
    or not stat.S_ISREG(metadata.st_mode)
    or metadata.st_uid != os.getuid()
    or stat.S_IMODE(metadata.st_mode) & 0o077
):
    raise SystemExit("service definition path is unsafe")
PY
}

secure_service_logs() {
    local log_file
    [[ "$SERVICE_LOG_DIR" == /* ]] || fail "service log directory must be absolute"
    [[ ! -L "$SERVICE_LOG_DIR" ]] || fail "service log directory must not be a symlink"
    mkdir -p "$SERVICE_LOG_DIR"
    [[ -d "$SERVICE_LOG_DIR" && ! -L "$SERVICE_LOG_DIR" ]] \
        || fail "service log directory is unavailable"
    chmod 700 "$SERVICE_LOG_DIR"
    for log_file in "$@"; do
        "$PYTHON_BIN" - "$log_file" <<'PY'
import os
import stat
import sys
from pathlib import Path

path = Path(sys.argv[1])
try:
    metadata = path.lstat()
except FileNotFoundError:
    flags = os.O_CREAT | os.O_APPEND | os.O_WRONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    os.close(descriptor)
else:
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise SystemExit(f"service log is not a regular file: {path}")
os.chmod(path, 0o600)
PY
    done
}

rotate_service_logs() {
    local log_file
    for log_file in "$@"; do
        [[ -f "$log_file" ]] || continue
        "$PYTHON_BIN" - "$log_file" "$SERVICE_LOG_MAX_BYTES" <<'PY'
from pathlib import Path
import os
import stat
import sys

path = Path(sys.argv[1])
max_bytes = int(sys.argv[2])
metadata = path.lstat()
if not stat.S_ISREG(metadata.st_mode):
    raise SystemExit(f"service log is not a regular file: {path}")
if metadata.st_size > max_bytes:
    backup = path.with_name(f"{path.name}.1")
    try:
        backup_metadata = backup.lstat()
    except FileNotFoundError:
        pass
    else:
        if not stat.S_ISREG(backup_metadata.st_mode):
            raise SystemExit(f"rotated service log is not a regular file: {backup}")
        backup.unlink()
    path.replace(backup)
    os.chmod(backup, 0o600)
PY
    done
}

macos_core_plist_path() {
    echo "$LAUNCH_AGENTS_DIR/$MACOS_LABEL.plist"
}

macos_worker_plist_path() {
    echo "$LAUNCH_AGENTS_DIR/$WORKER_MACOS_LABEL.plist"
}

macos_system_upgrade_plist_path() {
    echo "$LAUNCH_AGENTS_DIR/$SYSTEM_UPGRADE_MACOS_LABEL.plist"
}

systemd_core_unit_path() {
    echo "$SYSTEMD_DIR/$CORE_SERVICE_NAME.service"
}

systemd_worker_unit_path() {
    echo "$SYSTEMD_DIR/$WORKER_SERVICE_NAME.service"
}

systemd_system_upgrade_unit_path() {
    echo "$SYSTEMD_DIR/$SYSTEM_UPGRADE_SERVICE_NAME.service"
}

macos_domain() {
    echo "gui/$(id -u)"
}

wait_for_macos_unload() {
    local label="$1"
    local domain attempt
    domain="$(macos_domain)"
    for attempt in 1 2 3 4 5 6 7 8 9 10; do
        if ! launchctl print "$domain/$label" >/dev/null 2>&1; then
            return
        fi
        sleep 0.5
    done
    fail "macOS service did not stop"
}

service_state() {
    local resource="$1" macos_label="$2" macos_plist="$3" systemd_unit="$4"
    case "$PLATFORM" in
        Darwin)
            [[ -f "$macos_plist" ]] || { echo unknown; return 0; }
            if launchctl print "$(macos_domain)/$macos_label" >/dev/null 2>&1; then
                echo running
            else
                echo stopped
            fi
            ;;
        Linux)
            [[ -f "$systemd_unit" ]] || { echo unknown; return 0; }
            if systemctl --user is-active --quiet "$resource.service"; then
                echo running
            else
                echo stopped
            fi
            ;;
        *) fail "unsupported platform: $PLATFORM" ;;
    esac
}

start_service_resource() {
    local resource="$1" macos_label="$2" macos_plist="$3" systemd_unit="$4"
    local stdout_log="$5" stderr_log="$6" macos_missing_message="$7"
    case "$PLATFORM" in
        Darwin)
            [[ -f "$macos_plist" ]] || fail "$macos_missing_message"
            if launchctl print "$(macos_domain)/$macos_label" >/dev/null 2>&1; then
                secure_service_logs "$stdout_log" "$stderr_log"
                launchctl kickstart "$(macos_domain)/$macos_label"
            else
                secure_service_logs "$stdout_log" "$stderr_log"
                rotate_service_logs "$stdout_log" "$stderr_log"
                secure_service_logs "$stdout_log" "$stderr_log"
                launchctl bootstrap "$(macos_domain)" "$macos_plist"
            fi
            ;;
        Linux)
            systemctl --user start "$resource.service"
            ;;
        *) fail "unsupported platform: $PLATFORM" ;;
    esac
}

restart_service_resource() {
    local resource="$1" macos_label="$2" macos_plist="$3" systemd_unit="$4"
    local stdout_log="$5" stderr_log="$6" macos_missing_message="$7"
    local linux_restart_option="${8:-}"
    local require_linux_definition="${9:-true}"
    case "$PLATFORM" in
        Darwin)
            if launchctl print "$(macos_domain)/$macos_label" >/dev/null 2>&1; then
                secure_service_logs "$stdout_log" "$stderr_log"
                launchctl kickstart -k "$(macos_domain)/$macos_label"
            else
                [[ -f "$macos_plist" ]] || fail "$macos_missing_message"
                secure_service_logs "$stdout_log" "$stderr_log"
                rotate_service_logs "$stdout_log" "$stderr_log"
                secure_service_logs "$stdout_log" "$stderr_log"
                launchctl bootstrap "$(macos_domain)" "$macos_plist"
            fi
            ;;
        Linux)
            if [[ "$require_linux_definition" == "true" ]]; then
                [[ -f "$systemd_unit" ]] || fail "$resource service is not installed; run chub install"
            fi
            if [[ "$linux_restart_option" == "--no-block" ]]; then
                systemctl --user --no-block restart "$resource.service"
            else
                systemctl --user restart "$resource.service"
            fi
            ;;
        *) fail "unsupported platform: $PLATFORM" ;;
    esac
}

stop_service_resource() {
    local resource="$1" macos_label="$2"
    case "$PLATFORM" in
        Darwin)
            launchctl bootout "$(macos_domain)/$macos_label" >/dev/null 2>&1 || true
            wait_for_macos_unload "$macos_label"
            ;;
        Linux)
            systemctl --user stop "$resource.service"
            ;;
        *) fail "unsupported platform: $PLATFORM" ;;
    esac
}

start_web_service() {
    start_service_resource \
        "$CORE_SERVICE_NAME" "$MACOS_LABEL" "$(macos_core_plist_path)" \
        "$(systemd_core_unit_path)" \
        "$SERVICE_LOG_DIR/service.out.log" "$SERVICE_LOG_DIR/service.err.log" \
        "service is not installed; run chub install"
}

restart_web_service() {
    restart_service_resource \
        "$CORE_SERVICE_NAME" "$MACOS_LABEL" "$(macos_core_plist_path)" \
        "$(systemd_core_unit_path)" \
        "$SERVICE_LOG_DIR/service.out.log" "$SERVICE_LOG_DIR/service.err.log" \
        "service is not installed; run chub install"
}

restart_web_service_async() {
    restart_service_resource \
        "$CORE_SERVICE_NAME" "$MACOS_LABEL" "$(macos_core_plist_path)" \
        "$(systemd_core_unit_path)" \
        "$SERVICE_LOG_DIR/service.out.log" "$SERVICE_LOG_DIR/service.err.log" \
        "service is not installed; run chub install" "--no-block" false
}

stop_web_service() {
    stop_service_resource "$CORE_SERVICE_NAME" "$MACOS_LABEL"
}

web_service_state() {
    service_state "$CORE_SERVICE_NAME" "$MACOS_LABEL" "$(macos_core_plist_path)" \
        "$(systemd_core_unit_path)"
}

start_worker_service() {
    start_service_resource \
        "$WORKER_SERVICE_NAME" "$WORKER_MACOS_LABEL" "$(macos_worker_plist_path)" \
        "$(systemd_worker_unit_path)" \
        "$SERVICE_LOG_DIR/quick-worker.out.log" "$SERVICE_LOG_DIR/quick-worker.err.log" \
        "Quick Worker service is not installed; run chub install"
}

restart_worker_service() {
    restart_service_resource \
        "$WORKER_SERVICE_NAME" "$WORKER_MACOS_LABEL" "$(macos_worker_plist_path)" \
        "$(systemd_worker_unit_path)" \
        "$SERVICE_LOG_DIR/quick-worker.out.log" "$SERVICE_LOG_DIR/quick-worker.err.log" \
        "Quick Worker service is not installed; run chub install"
}

stop_worker_service() {
    stop_service_resource "$WORKER_SERVICE_NAME" "$WORKER_MACOS_LABEL"
}

worker_service_state() {
    service_state "$WORKER_SERVICE_NAME" "$WORKER_MACOS_LABEL" "$(macos_worker_plist_path)" \
        "$(systemd_worker_unit_path)"
}

worker_service_definition_exists() {
    case "$PLATFORM" in
        Darwin) [[ -f "$(macos_worker_plist_path)" ]] ;;
        Linux) [[ -f "$(systemd_worker_unit_path)" ]] ;;
        *) fail "unsupported platform: $PLATFORM" ;;
    esac
}

stop_core_services_if_installed() {
    case "$PLATFORM" in
        Darwin)
            if [[ -f "$(macos_system_upgrade_plist_path)" ]]; then
                launchctl bootout "$(macos_domain)/$SYSTEM_UPGRADE_MACOS_LABEL" >/dev/null 2>&1 || true
            fi
            if [[ -f "$(macos_core_plist_path)" ]]; then
                stop_web_service
            fi
            if [[ -f "$(macos_worker_plist_path)" ]]; then
                stop_worker_service
            fi
            ;;
        Linux)
            systemctl --user stop "$CORE_SERVICE_NAME.service" >/dev/null 2>&1 || true
            systemctl --user stop "$WORKER_SERVICE_NAME.service" >/dev/null 2>&1 || true
            ;;
        *) fail "unsupported platform: $PLATFORM" ;;
    esac
}

stop_recovery_services() {
    case "$PLATFORM" in
        Darwin)
            launchctl bootout "$(macos_domain)/$SYSTEM_UPGRADE_MACOS_LABEL" >/dev/null 2>&1 || true
            wait_for_macos_unload "$SYSTEM_UPGRADE_MACOS_LABEL"
            stop_web_service
            stop_worker_service
            ;;
        Linux)
            systemctl --user stop "$SYSTEM_UPGRADE_SERVICE_NAME.service" >/dev/null 2>&1 || true
            systemctl --user stop "$CORE_SERVICE_NAME.service" >/dev/null 2>&1 || true
            systemctl --user stop "$WORKER_SERVICE_NAME.service" >/dev/null 2>&1 || true
            ;;
        *) fail "unsupported platform: $PLATFORM" ;;
    esac
}

load_system_upgrade_service() {
    local restart_script="$PROJECT_ROOT/scripts/maintenance/chub-system-upgrade-restart"
    [[ -x "$restart_script" ]] || fail "system upgrade service runner is unavailable"
    case "$PLATFORM" in
        Darwin)
            local plist_path
            plist_path="$(macos_system_upgrade_plist_path)"
            write_macos_system_upgrade_service
            launchctl bootout "$(macos_domain)/$SYSTEM_UPGRADE_MACOS_LABEL" >/dev/null 2>&1 || true
            wait_for_macos_unload "$SYSTEM_UPGRADE_MACOS_LABEL"
            launchctl bootstrap "$(macos_domain)" "$plist_path"
            ;;
        Linux)
            write_systemd_system_upgrade_service
            systemctl --user daemon-reload
            ;;
        *) fail "unsupported platform: $PLATFORM" ;;
    esac
}

start_system_upgrade_service() {
    case "$PLATFORM" in
        Darwin)
            load_system_upgrade_service
            launchctl kickstart "$(macos_domain)/$SYSTEM_UPGRADE_MACOS_LABEL"
            ;;
        Linux)
            load_system_upgrade_service
            systemctl --user --no-block start "$SYSTEM_UPGRADE_SERVICE_NAME.service"
            ;;
        *) fail "unsupported platform: $PLATFORM" ;;
    esac
}

system_upgrade_service_state() {
    case "$PLATFORM" in
        Darwin)
            [[ -f "$(macos_system_upgrade_plist_path)" ]] && echo installed || echo missing
            ;;
        Linux)
            [[ -f "$(systemd_system_upgrade_unit_path)" ]] && echo installed || echo missing
            ;;
        *) fail "unsupported platform: $PLATFORM" ;;
    esac
}

system_upgrade_service_running_state() {
    case "$PLATFORM" in
        Darwin)
            local plist_path details
            plist_path="$(macos_system_upgrade_plist_path)"
            [[ -f "$plist_path" ]] || { echo missing; return 0; }
            if details="$(launchctl print "$(macos_domain)/$SYSTEM_UPGRADE_MACOS_LABEL" 2>/dev/null)" \
                && [[ "$details" == *"state = running"* ]]; then
                echo running
            else
                echo stopped
            fi
            ;;
        Linux)
            [[ -f "$(systemd_system_upgrade_unit_path)" ]] || { echo missing; return 0; }
            if systemctl --user is-active --quiet "$SYSTEM_UPGRADE_SERVICE_NAME.service"; then
                echo running
            else
                echo stopped
            fi
            ;;
        *) fail "unsupported platform: $PLATFORM" ;;
    esac
}

write_macos_core_service() {
    local plist_path
    plist_path="$(macos_core_plist_path)"
    prepare_definition_path "$plist_path"
    mkdir -p "$SERVICE_LOG_DIR"
    {
        echo '<?xml version="1.0" encoding="UTF-8"?>'
        echo '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">'
        echo '<plist version="1.0">'
        echo '<dict>'
        echo '  <key>Label</key>'
        echo "  <string>$MACOS_LABEL</string>"
        echo '  <key>ProgramArguments</key>'
        echo '  <array>'
        echo "    <string>$PYTHON_BIN</string>"
        echo "    <string>$MAIN_FILE</string>"
        echo '  </array>'
        echo '  <key>WorkingDirectory</key>'
        echo "  <string>$PROJECT_ROOT</string>"
        echo '  <key>EnvironmentVariables</key>'
        echo '  <dict>'
        echo '    <key>PATH</key>'
        echo "    <string>$RUNTIME_PATH</string>"
        echo '  </dict>'
        echo '  <key>RunAtLoad</key>'
        echo '  <true/>'
        echo '  <key>KeepAlive</key>'
        echo '  <dict>'
        echo '    <key>SuccessfulExit</key>'
        echo '    <false/>'
        echo '  </dict>'
        echo '  <key>ThrottleInterval</key>'
        echo '  <integer>5</integer>'
        echo '  <key>Umask</key>'
        echo '  <integer>63</integer>'
        echo '  <key>StandardOutPath</key>'
        echo "  <string>$SERVICE_LOG_DIR/service.out.log</string>"
        echo '  <key>StandardErrorPath</key>'
        echo "  <string>$SERVICE_LOG_DIR/service.err.log</string>"
        echo '</dict>'
        echo '</plist>'
    } > "$plist_path"
    chmod 0600 "$plist_path"
}

write_macos_worker_service() {
    local plist_path
    plist_path="$(macos_worker_plist_path)"
    prepare_definition_path "$plist_path"
    mkdir -p "$SERVICE_LOG_DIR"
    {
        echo '<?xml version="1.0" encoding="UTF-8"?>'
        echo '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">'
        echo '<plist version="1.0">'
        echo '<dict>'
        echo '  <key>Label</key>'
        echo "  <string>$WORKER_MACOS_LABEL</string>"
        echo '  <key>ProgramArguments</key>'
        echo '  <array>'
        echo "    <string>$PYTHON_BIN</string>"
        echo '    <string>-m</string>'
        echo '    <string>app.quick_worker</string>'
        echo '    <string>serve</string>'
        echo '  </array>'
        echo '  <key>WorkingDirectory</key>'
        echo "  <string>$PROJECT_ROOT</string>"
        echo '  <key>EnvironmentVariables</key>'
        echo '  <dict>'
        echo '    <key>PATH</key>'
        echo "    <string>$RUNTIME_PATH</string>"
        echo '  </dict>'
        echo '  <key>RunAtLoad</key>'
        echo '  <true/>'
        echo '  <key>KeepAlive</key>'
        echo '  <dict>'
        echo '    <key>SuccessfulExit</key>'
        echo '    <false/>'
        echo '  </dict>'
        echo '  <key>ThrottleInterval</key>'
        echo '  <integer>5</integer>'
        echo '  <key>Umask</key>'
        echo '  <integer>63</integer>'
        echo '  <key>StandardOutPath</key>'
        echo "  <string>$SERVICE_LOG_DIR/quick-worker.out.log</string>"
        echo '  <key>StandardErrorPath</key>'
        echo "  <string>$SERVICE_LOG_DIR/quick-worker.err.log</string>"
        echo '</dict>'
        echo '</plist>'
    } > "$plist_path"
    chmod 0600 "$plist_path"
}

write_macos_system_upgrade_service() {
    local plist_path
    plist_path="$(macos_system_upgrade_plist_path)"
    prepare_definition_path "$plist_path"
    secure_service_logs \
        "$SERVICE_LOG_DIR/system-upgrade.out.log" \
        "$SERVICE_LOG_DIR/system-upgrade.err.log"
    {
        echo '<?xml version="1.0" encoding="UTF-8"?>'
        echo '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">'
        echo '<plist version="1.0">'
        echo '<dict>'
        echo '  <key>Label</key>'
        echo "  <string>$SYSTEM_UPGRADE_MACOS_LABEL</string>"
        echo '  <key>ProgramArguments</key>'
        echo '  <array>'
        echo "    <string>$PROJECT_ROOT/scripts/maintenance/chub-system-upgrade-restart</string>"
        echo '    <string>--pending</string>'
        echo '  </array>'
        echo '  <key>WorkingDirectory</key>'
        echo "  <string>$PROJECT_ROOT</string>"
        echo '  <key>EnvironmentVariables</key>'
        echo '  <dict>'
        echo '    <key>PATH</key>'
        echo "    <string>$RUNTIME_PATH</string>"
        echo '  </dict>'
        echo '  <key>RunAtLoad</key>'
        echo '  <false/>'
        echo '  <key>KeepAlive</key>'
        echo '  <false/>'
        echo '  <key>ProcessType</key>'
        echo '  <string>Background</string>'
        echo '  <key>ThrottleInterval</key>'
        echo '  <integer>5</integer>'
        echo '  <key>Umask</key>'
        echo '  <integer>63</integer>'
        echo '  <key>StandardOutPath</key>'
        echo "  <string>$SERVICE_LOG_DIR/system-upgrade.out.log</string>"
        echo '  <key>StandardErrorPath</key>'
        echo "  <string>$SERVICE_LOG_DIR/system-upgrade.err.log</string>"
        echo '</dict>'
        echo '</plist>'
    } > "$plist_path"
    chmod 0600 "$plist_path"
}

write_systemd_core_service() {
    local unit_path
    unit_path="$(systemd_core_unit_path)"
    prepare_definition_path "$unit_path"
    mkdir -p "$SERVICE_LOG_DIR"
    {
        echo '[Unit]'
        echo 'Description=Chub personal device node'
        echo 'After=network.target'
        echo 'StartLimitIntervalSec=60'
        echo 'StartLimitBurst=5'
        echo
        echo '[Service]'
        echo 'Type=simple'
        printf 'WorkingDirectory=%s\n' "$PROJECT_ROOT"
        printf 'ExecStart=%s %s\n' "$PYTHON_BIN" "$MAIN_FILE"
        printf 'Environment=PATH=%s\n' "$RUNTIME_PATH"
        echo 'UMask=0077'
        echo 'Restart=on-failure'
        echo 'RestartSec=5'
        echo
        echo '[Install]'
        echo 'WantedBy=default.target'
    } > "$unit_path"
    chmod 0600 "$unit_path"
}

write_systemd_worker_service() {
    local unit_path
    unit_path="$(systemd_worker_unit_path)"
    prepare_definition_path "$unit_path"
    mkdir -p "$SERVICE_LOG_DIR"
    {
        echo '[Unit]'
        echo 'Description=Chub Quick Worker'
        echo 'After=network.target'
        echo 'StartLimitIntervalSec=60'
        echo 'StartLimitBurst=5'
        echo
        echo '[Service]'
        echo 'Type=simple'
        printf 'WorkingDirectory=%s\n' "$PROJECT_ROOT"
        printf 'ExecStart=%s -m app.quick_worker serve\n' "$PYTHON_BIN"
        printf 'Environment=PATH=%s\n' "$RUNTIME_PATH"
        echo 'UMask=0077'
        echo 'KillMode=control-group'
        echo 'Restart=on-failure'
        echo 'RestartSec=5'
        echo
        echo '[Install]'
        echo 'WantedBy=default.target'
    } > "$unit_path"
    chmod 0600 "$unit_path"
}

write_systemd_system_upgrade_service() {
    local unit_path
    unit_path="$(systemd_system_upgrade_unit_path)"
    prepare_definition_path "$unit_path"
    {
        echo '[Unit]'
        echo 'Description=Chub system upgrade and recovery runner'
        echo
        echo '[Service]'
        echo 'Type=oneshot'
        printf 'WorkingDirectory=%s\n' "$PROJECT_ROOT"
        printf 'ExecStart=%s --pending\n' "$PROJECT_ROOT/scripts/maintenance/chub-system-upgrade-restart"
        printf 'Environment=PATH=%s\n' "$RUNTIME_PATH"
        echo 'UMask=0077'
        echo 'TimeoutStartSec=infinity'
    } > "$unit_path"
    chmod 0600 "$unit_path"
}

write_core_service_definitions() {
    [[ -x "$PYTHON_BIN" ]] || fail "Python environment is unavailable"
    [[ -f "$MAIN_FILE" ]] || fail "Chub entry point is unavailable"
    case "$PLATFORM" in
        Darwin)
            write_macos_core_service
            write_macos_worker_service
            write_macos_system_upgrade_service
            ;;
        Linux)
            write_systemd_core_service
            write_systemd_worker_service
            write_systemd_system_upgrade_service
            systemctl --user daemon-reload
            systemctl --user enable "$CORE_SERVICE_NAME.service"
            systemctl --user enable "$WORKER_SERVICE_NAME.service"
            ;;
        *) fail "unsupported platform: $PLATFORM" ;;
    esac
}

write_chrome_supervisor_unit() {
    local unit_path="$SYSTEMD_DIR/$SERVICE_NAME.service"
    prepare_definition_path "$unit_path"
    {
        echo '[Unit]'
        echo 'Description=Chub Debug Chrome supervisor'
        echo 'After=network.target'
        echo 'StartLimitIntervalSec=60'
        echo 'StartLimitBurst=5'
        echo
        echo '[Service]'
        echo 'Type=simple'
        printf 'WorkingDirectory=%s\n' "$PROJECT_ROOT"
        printf 'ExecStart=%s -m app.automations.chrome_supervisor serve\n' "$PYTHON_BIN"
        printf 'Environment=PATH=%s\n' "$PROJECT_ROOT/.venv/bin:$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        echo 'UMask=0077'
        echo 'KillMode=control-group'
        echo 'Restart=on-failure'
        echo 'RestartSec=5'
        echo
        echo '[Install]'
        echo 'WantedBy=default.target'
    } > "$unit_path"
    chmod 0600 "$unit_path"
}

reconcile_chrome_supervisor() {
    local action="start"
    if [[ "${1:-}" == "--restart" && "$#" -eq 1 ]]; then
        action="restart"
    elif [[ "$#" -ne 0 ]]; then
        fail "usage: service-management.sh chrome-supervisor-reconcile [--restart]"
    fi
    case "$PLATFORM" in
        Darwin)
            exit 0
            ;;
        Linux)
            [[ -x "$PYTHON_BIN" ]] || fail "Python environment is unavailable"
            write_chrome_supervisor_unit
            systemctl --user daemon-reload
            systemctl --user enable "$SERVICE_NAME.service"
            systemctl --user "$action" "$SERVICE_NAME.service"
            systemctl --user is-active --quiet "$SERVICE_NAME.service" \
                || fail "Debug Chrome Supervisor did not become active"
            (
                cd "$PROJECT_ROOT"
                "$PYTHON_BIN" -m app.automations.chrome_maintenance health --wait
            ) || fail "Debug Chrome Supervisor socket did not become ready"
            ;;
        *) fail "unsupported platform: $PLATFORM" ;;
    esac
}

write_chrome_supervisor_unit_only() {
    case "$PLATFORM" in
        Darwin)
            exit 0
            ;;
        Linux)
            [[ -x "$PYTHON_BIN" ]] || fail "Python environment is unavailable"
            write_chrome_supervisor_unit
            ;;
        *) fail "unsupported platform: $PLATFORM" ;;
    esac
}

write_chrome_supervisor_definitions() {
    case "$PLATFORM" in
        Darwin) : ;;
        Linux)
            [[ -x "$PYTHON_BIN" ]] || fail "Python environment is unavailable"
            write_chrome_supervisor_unit
            systemctl --user daemon-reload
            systemctl --user enable "$SERVICE_NAME.service"
            ;;
        *) fail "unsupported platform: $PLATFORM" ;;
    esac
}

stop_chrome_supervisor() {
    case "$PLATFORM" in
        Darwin) : ;;
        Linux) systemctl --user stop "$SERVICE_NAME.service" >/dev/null 2>&1 || true ;;
        *) fail "unsupported platform: $PLATFORM" ;;
    esac
}

chrome_supervisor_state() {
    case "$PLATFORM" in
        Darwin) echo not-managed ;;
        Linux)
            [[ -f "$SYSTEMD_DIR/$SERVICE_NAME.service" ]] || { echo unknown; return 0; }
            if systemctl --user is-active --quiet "$SERVICE_NAME.service"; then
                if (
                    cd "$PROJECT_ROOT"
                    "$PYTHON_BIN" -m app.automations.chrome_maintenance health
                ) >/dev/null 2>&1; then
                    echo running
                else
                    echo unavailable
                fi
            else
                echo stopped
            fi
            ;;
        *) fail "unsupported platform: $PLATFORM" ;;
    esac
}

service_manager_details() {
    local result=0
    case "$PLATFORM" in
        Darwin)
            launchctl print "$(macos_domain)/$MACOS_LABEL" \
                | awk '/^[[:space:]]*(inherited |default )?environment =/{exit} {print}' \
                || result=1
            launchctl print "$(macos_domain)/$WORKER_MACOS_LABEL" \
                | awk '/^[[:space:]]*(inherited |default )?environment =/{exit} {print}' \
                || result=1
            ;;
        Linux)
            systemctl --user show "$CORE_SERVICE_NAME.service" \
                -p LoadState -p ActiveState -p SubState -p MainPID --no-pager || result=1
            systemctl --user show "$WORKER_SERVICE_NAME.service" \
                -p LoadState -p ActiveState -p SubState -p MainPID --no-pager || result=1
            systemctl --user show "$SERVICE_NAME.service" \
                -p LoadState -p ActiveState -p SubState -p MainPID --no-pager || result=1
            ;;
        *) fail "unsupported platform: $PLATFORM" ;;
    esac
    return "$result"
}

uninstall_all_services() {
    case "$PLATFORM" in
        Darwin)
            launchctl bootout "$(macos_domain)/$MACOS_LABEL" >/dev/null 2>&1 || true
            launchctl bootout "$(macos_domain)/$WORKER_MACOS_LABEL" >/dev/null 2>&1 || true
            launchctl bootout "$(macos_domain)/$SYSTEM_UPGRADE_MACOS_LABEL" >/dev/null 2>&1 || true
            rm -f "$(macos_core_plist_path)"
            rm -f "$(macos_worker_plist_path)"
            rm -f "$(macos_system_upgrade_plist_path)"
            ;;
        Linux)
            systemctl --user disable --now "$CORE_SERVICE_NAME.service" >/dev/null 2>&1 || true
            systemctl --user disable --now "$WORKER_SERVICE_NAME.service" >/dev/null 2>&1 || true
            systemctl --user disable --now "$SERVICE_NAME.service" >/dev/null 2>&1 || true
            systemctl --user disable --now "$SYSTEM_UPGRADE_SERVICE_NAME.service" >/dev/null 2>&1 || true
            rm -f "$(systemd_core_unit_path)"
            rm -f "$(systemd_worker_unit_path)"
            rm -f "$SYSTEMD_DIR/$SERVICE_NAME.service"
            rm -f "$(systemd_system_upgrade_unit_path)"
            systemctl --user daemon-reload
            ;;
        *) fail "unsupported platform: $PLATFORM" ;;
    esac
}

[[ "$#" -ge 1 ]] || fail "usage: service-management.sh <core-service-definitions|core-stop|recovery-core-stop|system-upgrade-load|system-upgrade-start|system-upgrade-status|system-upgrade-running|web-start|web-restart|web-restart-async|web-stop|web-status|worker-start|worker-restart|worker-stop|worker-status|worker-definition-exists|chrome-supervisor-reconcile|chrome-supervisor-write-unit|chrome-supervisor-definitions|chrome-supervisor-stop|chrome-supervisor-status|service-details|all-services-uninstall>"
case "$1" in
    core-service-definitions)
        shift
        [[ "$#" -eq 0 ]] || fail "usage: service-management.sh core-service-definitions"
        write_core_service_definitions
        ;;
    core-stop)
        shift
        [[ "$#" -eq 0 ]] || fail "usage: service-management.sh core-stop"
        stop_core_services_if_installed
        ;;
    recovery-core-stop)
        shift
        [[ "$#" -eq 0 ]] || fail "usage: service-management.sh recovery-core-stop"
        stop_recovery_services
        ;;
    system-upgrade-load)
        shift
        [[ "$#" -eq 0 ]] || fail "usage: service-management.sh system-upgrade-load"
        load_system_upgrade_service
        ;;
    system-upgrade-start)
        shift
        [[ "$#" -eq 0 ]] || fail "usage: service-management.sh system-upgrade-start"
        start_system_upgrade_service
        ;;
    system-upgrade-status)
        shift
        [[ "$#" -eq 0 ]] || fail "usage: service-management.sh system-upgrade-status"
        system_upgrade_service_state
        ;;
    system-upgrade-running)
        shift
        [[ "$#" -eq 0 ]] || fail "usage: service-management.sh system-upgrade-running"
        system_upgrade_service_running_state
        ;;
    web-start)
        shift
        [[ "$#" -eq 0 ]] || fail "usage: service-management.sh web-start"
        start_web_service
        ;;
    web-restart)
        shift
        [[ "$#" -eq 0 ]] || fail "usage: service-management.sh web-restart"
        restart_web_service
        ;;
    web-restart-async)
        shift
        [[ "$#" -eq 0 ]] || fail "usage: service-management.sh web-restart-async"
        restart_web_service_async
        ;;
    web-stop)
        shift
        [[ "$#" -eq 0 ]] || fail "usage: service-management.sh web-stop"
        stop_web_service
        ;;
    web-status)
        shift
        [[ "$#" -eq 0 ]] || fail "usage: service-management.sh web-status"
        web_service_state
        ;;
    worker-start)
        shift
        [[ "$#" -eq 0 ]] || fail "usage: service-management.sh worker-start"
        start_worker_service
        ;;
    worker-restart)
        shift
        [[ "$#" -eq 0 ]] || fail "usage: service-management.sh worker-restart"
        restart_worker_service
        ;;
    worker-stop)
        shift
        [[ "$#" -eq 0 ]] || fail "usage: service-management.sh worker-stop"
        stop_worker_service
        ;;
    worker-status)
        shift
        [[ "$#" -eq 0 ]] || fail "usage: service-management.sh worker-status"
        worker_service_state
        ;;
    worker-definition-exists)
        shift
        [[ "$#" -eq 0 ]] || fail "usage: service-management.sh worker-definition-exists"
        worker_service_definition_exists
        ;;
    chrome-supervisor-reconcile)
        shift
        reconcile_chrome_supervisor "$@"
        ;;
    chrome-supervisor-write-unit)
        shift
        [[ "$#" -eq 0 ]] || fail "usage: service-management.sh chrome-supervisor-write-unit"
        write_chrome_supervisor_unit_only
        ;;
    chrome-supervisor-definitions)
        shift
        [[ "$#" -eq 0 ]] || fail "usage: service-management.sh chrome-supervisor-definitions"
        write_chrome_supervisor_definitions
        ;;
    chrome-supervisor-stop)
        shift
        [[ "$#" -eq 0 ]] || fail "usage: service-management.sh chrome-supervisor-stop"
        stop_chrome_supervisor
        ;;
    chrome-supervisor-status)
        shift
        [[ "$#" -eq 0 ]] || fail "usage: service-management.sh chrome-supervisor-status"
        chrome_supervisor_state
        ;;
    service-details)
        shift
        [[ "$#" -eq 0 ]] || fail "usage: service-management.sh service-details"
        service_manager_details
        ;;
    all-services-uninstall)
        shift
        [[ "$#" -eq 0 ]] || fail "usage: service-management.sh all-services-uninstall"
        uninstall_all_services
        ;;
    *) fail "unsupported fixed service operation" ;;
esac
