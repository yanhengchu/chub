from __future__ import annotations

import fcntl
import json
import os
import re
import stat
import subprocess
import time
import tomllib
from collections.abc import Callable
from pathlib import Path

from app.ai_runtime import (
    RUNTIME_CAPABILITIES,
    RuntimeActivityEvent,
    RuntimeCapability,
    RuntimeDescriptor,
    RuntimeModelCatalog,
    RuntimeModelInfo,
    RuntimeNativeAction,
    RuntimeNativeSession,
    RuntimeOperationError,
    RuntimeProcessSpec,
    RuntimeReasoningLevel,
    RuntimeSessionDiscoveryResult,
    RuntimeStatus,
    RuntimeTerminalRequest,
)
from app.codex.discovery import CodexSessionDiscovery
from app.codex.model_catalog import CodexModelCatalog
from app.core.config import PROJECT_ROOT, Settings
from app.core.network import is_tailscale_ip


PROFILE_MARKER = "# Managed by Chub Codex PTY"
CODEX_SESSION_ID_PATTERN = r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}"
CODEX_RUNTIME_CAPABILITIES: frozenset[RuntimeCapability] = RUNTIME_CAPABILITIES
MAX_ACTIVITY_EVENT_BYTES = 32 * 1024


def is_valid_codex_session_id(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(
        CODEX_SESSION_ID_PATTERN,
        value,
    ) is not None


class CodexRuntimeAdapter:
    def __init__(
        self,
        settings: Settings,
        *,
        codex_home: Path | None = None,
        executable: str | Path | None = None,
        which: Callable[[str], str | None] | None = None,
        run: Callable[..., subprocess.CompletedProcess] | None = None,
    ) -> None:
        self.settings = settings
        self.codex_home = codex_home or Path(
            os.environ.get("CODEX_HOME", Path.home() / ".codex")
        )
        self.executable = str(executable) if executable is not None else "codex"
        self.discovery = CodexSessionDiscovery(self.codex_home)
        self.model_catalog = CodexModelCatalog(self.codex_home)
        self.hook_dir = settings.codex_pty.runtime_dir / "hooks"
        self._which = which
        self._run = run

    @property
    def descriptor(self) -> RuntimeDescriptor:
        return RuntimeDescriptor(
            runtime_id="codex",
            capabilities=CODEX_RUNTIME_CAPABILITIES,
        )

    @property
    def display_name(self) -> str:
        return "Codex"

    @staticmethod
    def runtime_process_matches(command: tuple[str, ...]) -> bool:
        return any("codex" in Path(part).name.lower() for part in command)

    @staticmethod
    def terminal_backend_matches(
        command: tuple[str, ...],
        session_id: str,
    ) -> bool:
        executable = Path(command[0]).name if command else ""
        return executable == "ttyd" and f"/codex/{session_id}/terminal" in command

    def read_activity_event(self, session_id: str) -> RuntimeActivityEvent | None:
        path = self._activity_event_path(session_id)
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(path, flags)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise RuntimeOperationError(
                "codex_activity_event_unavailable",
                "Codex activity event is unavailable",
            ) from exc
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or stat.S_IMODE(metadata.st_mode) & 0o077
                or metadata.st_size > MAX_ACTIVITY_EVENT_BYTES
            ):
                raise OSError("Codex activity event is unsafe or too large")
            with os.fdopen(descriptor, "rb") as event_file:
                descriptor = -1
                content = event_file.read(MAX_ACTIVITY_EVENT_BYTES + 1)
            if len(content) > MAX_ACTIVITY_EVENT_BYTES:
                raise OSError("Codex activity event is oversized")
        except OSError as exc:
            raise RuntimeOperationError(
                "codex_activity_event_unavailable",
                "Codex activity event is unavailable",
            ) from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        try:
            payload = json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeOperationError(
                "codex_activity_event_invalid",
                "Codex activity event is invalid",
            ) from exc
        if not isinstance(payload, dict):
            raise RuntimeOperationError(
                "codex_activity_event_invalid",
                "Codex activity event is invalid",
            )
        native_session_id = payload.get("codex_session_id")
        if not is_valid_codex_session_id(native_session_id):
            native_session_id = None
        activity = payload.get("activity")
        if activity not in {"working", "idle"}:
            activity = None
        activity_source = payload.get("activity_source", "terminal")
        if activity_source not in {"terminal", "quick"}:
            activity_source = "none"
        elif activity != "working" and activity_source == "terminal":
            activity_source = "none"
        return RuntimeActivityEvent(
            native_session_id=native_session_id,
            activity=activity,
            activity_source=activity_source,
        )

    def clear_activity_event(self, session_id: str) -> None:
        try:
            self._activity_event_path(session_id).unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise RuntimeOperationError(
                "codex_activity_event_unavailable",
                "Codex activity event is unavailable",
            ) from exc

    def rebind_activity_session(self, old_session_id: str, new_session_id: str) -> None:
        """Keep hooks from a live pre-upgrade process correlated after Session rebind."""
        old_path = self._activity_rebind_path(old_session_id)
        self._activity_rebind_path(new_session_id)
        self.hook_dir.mkdir(parents=True, exist_ok=True)
        temporary = self.hook_dir / f".{old_session_id}.{os.getpid()}.rebind.tmp"
        try:
            temporary.write_text(f"{new_session_id}\n", encoding="ascii")
            os.chmod(temporary, 0o600)
            temporary.replace(old_path)
        except OSError as exc:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
            raise RuntimeOperationError(
                "codex_activity_rebind_unavailable",
                "Codex activity Session rebind is unavailable",
            ) from exc

    def _activity_event_path(self, session_id: str) -> Path:
        self._validate_activity_session_id(session_id)
        return self.hook_dir / f"{session_id}.json"

    def _activity_rebind_path(self, session_id: str) -> Path:
        self._validate_activity_session_id(session_id)
        return self.hook_dir / f".{session_id}.rebind"

    @staticmethod
    def _validate_activity_session_id(session_id: str) -> None:
        if not re.fullmatch(
            r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
            r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
            session_id,
        ):
            raise RuntimeOperationError(
                "codex_activity_session_invalid",
                "Codex activity Session ID is invalid",
                kind="invalid_request",
            )

    @property
    def network_available(self) -> bool:
        return is_tailscale_ip(self.settings.server.tailnet_host or "")

    def dependencies(self) -> dict[str, bool]:
        import shutil

        which = self._which or shutil.which
        return {name: which(name) is not None for name in ("codex", "ttyd", "tmux")}

    def status(self) -> RuntimeStatus:
        dependencies = self.dependencies()
        reason = None
        if not self.settings.codex_pty.enabled:
            reason = "Codex PTY is disabled"
        elif not self.network_available:
            reason = "Codex PTY requires a Tailscale listen address"
        else:
            missing = [name for name, found in dependencies.items() if not found]
            if missing:
                reason = f"Missing dependencies: {', '.join(missing)}"
        return RuntimeStatus(
            runtime_id="codex",
            available=reason is None,
            reason=reason,
            dependencies=dependencies,
        )

    def validate_model(self, model: str | None, reasoning_effort: str | None) -> None:
        self.model_catalog.validate(model, reasoning_effort)

    def read_model_catalog(self) -> RuntimeModelCatalog:
        catalog = self.model_catalog.data()
        return RuntimeModelCatalog(
            models=tuple(
                RuntimeModelInfo(
                    id=model.id,
                    name=model.name,
                    description=model.description,
                    default_level=model.default_level,
                    levels=tuple(
                        RuntimeReasoningLevel(
                            id=level.id,
                            description=level.description,
                        )
                        for level in model.levels
                    ),
                )
                for model in catalog.models
            ),
            default_model=catalog.default_model,
            default_reasoning_effort=catalog.default_reasoning_effort,
        )

    @staticmethod
    def validate_native_session_id(native_session_id: str) -> None:
        if not is_valid_codex_session_id(native_session_id):
            raise RuntimeOperationError(
                "codex_session_invalid",
                "Codex Session ID is invalid",
                kind="invalid_request",
            )

    def discover_sessions(self) -> RuntimeSessionDiscoveryResult:
        try:
            discovered = self.discovery.discover()
            archive_states = self.discovery.session_archive_states()
        except OSError as exc:
            raise RuntimeOperationError(
                "codex_session_discovery_unavailable",
                "Unable to discover Codex sessions",
            ) from exc
        sessions: list[RuntimeNativeSession] = []
        for session in discovered:
            native_session_id = session.codex_session_id
            if not is_valid_codex_session_id(native_session_id):
                raise RuntimeOperationError(
                    "codex_session_invalid",
                    "Codex Session ID is invalid",
                )
            sessions.append(
                RuntimeNativeSession(
                    runtime_id="codex",
                    native_session_id=native_session_id,
                    cwd=session.cwd,
                    title=session.title[:500] if session.title is not None else None,
                    active_permission_mode=session.active_permission_mode,
                    active_model=session.active_model,
                    active_reasoning_effort=session.active_reasoning_effort,
                    created_at=session.created_at,
                    updated_at=session.updated_at,
                )
            )
        return RuntimeSessionDiscoveryResult(
            sessions=tuple(sessions),
            archive_states=archive_states,
        )

    def has_active_writer(
        self,
        native_session_id: str | None,
    ) -> bool:
        if not native_session_id:
            return False
        if not is_valid_codex_session_id(native_session_id):
            raise RuntimeOperationError(
                "codex_writer_status_unavailable",
                "Unable to verify Codex session writer state",
            )
        lock_path = self.codex_home / "thread-writer-locks" / f"{native_session_id}.lock"
        flags = os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(lock_path, flags)
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise RuntimeOperationError(
                "codex_writer_status_unavailable",
                "Unable to verify Codex session writer state",
            ) from exc
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise OSError("Codex writer lock is not a regular file")
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            return False
        except OSError as exc:
            raise RuntimeOperationError(
                "codex_writer_status_unavailable",
                "Unable to verify Codex session writer state",
            ) from exc
        finally:
            os.close(descriptor)

    def wait_for_writer_release(
        self,
        native_session_id: str | None,
        *,
        timeout: float = 3.0,
    ) -> bool:
        deadline = time.monotonic() + max(0.0, timeout)
        while self.has_active_writer(native_session_id):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(0.05, remaining))
        return True

    def native_session_available(self, native_session_id: str) -> bool:
        self.validate_native_session_id(native_session_id)
        archive_states = self.discovery.session_archive_states()
        return archive_states is not None and archive_states.get(native_session_id) is False

    def native_session_archive_state(self, native_session_id: str) -> bool | None:
        self.validate_native_session_id(native_session_id)
        archive_states = self.discovery.session_archive_states()
        if archive_states is None:
            return None
        return archive_states.get(native_session_id)

    def native_session_deleted_state(self, native_session_id: str) -> bool | None:
        self.validate_native_session_id(native_session_id)
        archive_states = self.discovery.session_archive_states()
        if archive_states is None:
            return None
        if native_session_id in archive_states:
            return False
        sessions = self.discovery.discover()
        return not any(
            session.codex_session_id == native_session_id
            for session in sessions
        )

    def ensure_profile(self) -> None:
        profile = self.codex_home / "chub.config.toml"
        hook = PROJECT_ROOT / "scripts" / "chub-codex-hook"
        content = (
            f"{PROFILE_MARKER}\n"
            "[[hooks.SessionStart]]\n"
            'matcher = "startup|resume"\n\n'
            "[[hooks.SessionStart.hooks]]\n"
            'type = "command"\n'
            f"command = {json.dumps(str(hook))}\n"
            "timeout = 5\n"
            f"{self.activity_hook_content(hook)}"
        )
        if profile.exists():
            existing = profile.read_text(encoding="utf-8")
            if existing == content or (
                self.profile_has_managed_hook(existing, hook)
                and self.profile_has_activity_hooks(existing, hook)
            ):
                return
            if PROFILE_MARKER not in existing.splitlines() or not self.profile_has_managed_hook(existing, hook):
                raise RuntimeOperationError(
                    "codex_profile_conflict",
                    f"Existing Codex profile is not managed by Chub: {profile}",
                    kind="conflict",
                )
            missing_hooks = "".join(
                self.event_hook_content(event, hook)
                for event in ("UserPromptSubmit", "Stop")
                if not self.profile_has_event_hook(existing, hook, event)
            )
            content = f"{existing.rstrip()}\n{missing_hooks}"
        self.codex_home.mkdir(parents=True, exist_ok=True)
        temporary = profile.with_suffix(".tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(profile)

    @staticmethod
    def profile_has_managed_hook(existing: str, hook: Path) -> bool:
        if PROFILE_MARKER not in existing.splitlines():
            return False
        try:
            profile = tomllib.loads(existing)
            session_start = profile["hooks"]["SessionStart"]
        except (KeyError, TypeError, tomllib.TOMLDecodeError):
            return False
        if not isinstance(session_start, list):
            return False
        return any(
            entry.get("matcher") == "startup|resume"
            and any(
                hook_entry.get("type") == "command"
                and hook_entry.get("command") == str(hook)
                for hook_entry in entry.get("hooks", [])
                if isinstance(hook_entry, dict)
            )
            for entry in session_start
            if isinstance(entry, dict)
        )

    @classmethod
    def activity_hook_content(cls, hook: Path) -> str:
        return "".join(cls.event_hook_content(event, hook) for event in ("UserPromptSubmit", "Stop"))

    @staticmethod
    def event_hook_content(event: str, hook: Path) -> str:
        return (
            f"\n[[hooks.{event}]]\n"
            f"\n[[hooks.{event}.hooks]]\n"
            'type = "command"\n'
            f"command = {json.dumps(str(hook))}\n"
            "timeout = 5\n"
        )

    @classmethod
    def profile_has_activity_hooks(cls, existing: str, hook: Path) -> bool:
        return all(cls.profile_has_event_hook(existing, hook, event) for event in ("UserPromptSubmit", "Stop"))

    @staticmethod
    def profile_has_event_hook(existing: str, hook: Path, event: str) -> bool:
        try:
            profile = tomllib.loads(existing)
            hooks = profile["hooks"]
        except (KeyError, TypeError, tomllib.TOMLDecodeError):
            return False
        entries = hooks.get(event)
        return isinstance(entries, list) and any(
            any(
                hook_entry.get("type") == "command"
                and hook_entry.get("command") == str(hook)
                for hook_entry in entry.get("hooks", [])
                if isinstance(hook_entry, dict)
            )
            for entry in entries
            if isinstance(entry, dict)
        )

    def terminal_command(
        self,
        request: RuntimeTerminalRequest,
        port: int,
    ) -> RuntimeProcessSpec:
        if request.native_session_id is not None:
            self.validate_native_session_id(request.native_session_id)
        launcher = PROJECT_ROOT / "scripts" / "chub-codex-launcher"
        command = [
            "ttyd", "-W", "-O", "-m", "1", "-i", "127.0.0.1", "-p", str(port),
            "-b", f"/codex/{request.session_id}/terminal", str(launcher),
            "--name", f"chub-{request.session_id}", "--cwd", str(request.cwd),
            "--chub-session", request.session_id, "--hook-dir", str(self.hook_dir),
            "--permission-mode", request.permission_mode,
        ]
        if request.native_session_id:
            command.extend(["--codex-session", request.native_session_id])
        if request.model:
            command.extend(["--model", request.model])
        if request.reasoning_effort:
            command.extend(["--reasoning-effort", request.reasoning_effort])
        return RuntimeProcessSpec(argv=tuple(command))

    def run_native_action(
        self,
        action: RuntimeNativeAction,
        native_session_id: str,
    ) -> None:
        if action not in {"archive", "delete"}:
            raise RuntimeOperationError(
                "runtime_action_unsupported",
                "Runtime action is unsupported",
                kind="invalid_request",
            )
        self.validate_native_session_id(native_session_id)
        run = self._run or subprocess.run
        command = [self.executable, action]
        if action == "delete":
            command.append("--force")
        command.append(native_session_id)
        result = run(
            command,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeOperationError(
                f"codex_session_{action}_failed",
                f"Unable to {action} Codex session",
            )
