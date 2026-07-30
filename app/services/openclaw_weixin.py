from __future__ import annotations

import codecs
from datetime import datetime
import os
import pty
import re
import select
import signal
import struct
import subprocess
import termios
import threading
import zlib
from typing import Literal

from pydantic import BaseModel

from app.codex.models import utc_now
from app.core.response import ApiError
from app.services.operation_log import write_operation


WeixinLoginState = Literal[
    "idle",
    "starting",
    "waiting_scan",
    "needs_verification",
    "confirming",
    "cancelling",
    "succeeded",
    "failed",
    "cancelled",
]

MAX_LOGIN_OUTPUT_CHARS = 256_000
ANSI_ESCAPE_PATTERN = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
VERIFY_CODE_PATTERN = re.compile(r"^\d{1,12}$")
QR_CHARACTERS = frozenset(" █▀▄")


class WeixinLoginStatus(BaseModel):
    state: WeixinLoginState
    message: str
    qr_available: bool
    created_at: datetime | None = None
    updated_at: datetime


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    payload = kind + data
    return (
        struct.pack(">I", len(data))
        + payload
        + struct.pack(">I", zlib.crc32(payload) & 0xFFFFFFFF)
    )


def qr_matrix_to_png(
    matrix: list[list[bool]],
    *,
    scale: int = 8,
    quiet_zone: int = 4,
) -> bytes:
    size = len(matrix)
    if size < 21 or size > 177 or any(len(row) != size for row in matrix):
        raise ValueError("invalid QR matrix")
    image_size = (size + quiet_zone * 2) * scale
    rows = bytearray()
    for output_y in range(image_size):
        module_y = output_y // scale - quiet_zone
        rows.append(0)
        for output_x in range(image_size):
            module_x = output_x // scale - quiet_zone
            dark = (
                0 <= module_y < size
                and 0 <= module_x < size
                and matrix[module_y][module_x]
            )
            rows.append(0 if dark else 255)
    header = struct.pack(
        ">IIBBBBB",
        image_size,
        image_size,
        8,
        0,
        0,
        0,
        0,
    )
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(bytes(rows), level=9))
        + _png_chunk(b"IEND", b"")
    )


def extract_terminal_qr_png(output: str) -> bytes | None:
    lines = [line.rstrip("\r") for line in output.splitlines()]
    candidates: list[list[str]] = []
    current: list[str] = []
    current_width = 0
    for line in lines:
        valid = (
            len(line) >= 23
            and line.startswith("█")
            and line.endswith("█")
            and set(line).issubset(QR_CHARACTERS)
        )
        if valid and (not current or len(line) == current_width):
            if not current:
                current_width = len(line)
            current.append(line)
            continue
        if current:
            candidates.append(current)
            current = []
            current_width = 0
    if current:
        candidates.append(current)

    for candidate in reversed(candidates):
        size = len(candidate[0]) - 2
        rows: list[list[bool]] = []
        for line in candidate:
            top: list[bool] = []
            bottom: list[bool] = []
            for character in line[1:-1]:
                top.append(character in {"▄", " "})
                bottom.append(character in {"▀", " "})
            rows.extend((top, bottom))
        if len(rows) < size:
            continue
        matrix = rows[:size]
        try:
            return qr_matrix_to_png(matrix)
        except ValueError:
            continue
    return None


class OpenClawWeixinLogin:
    def __init__(self, operation_lock: threading.Lock) -> None:
        self._operation_lock = operation_lock
        self._state_lock = threading.Lock()
        self._state: WeixinLoginState = "idle"
        self._message = "尚未发起微信绑定。"
        self._created_at: datetime | None = None
        self._updated_at = utc_now()
        self._qr_png: bytes | None = None
        self._process: subprocess.Popen[bytes] | None = None
        self._reader_thread: threading.Thread | None = None
        self._master_fd: int | None = None
        self._output = ""
        self._cancel_requested = False
        self._verification_submitted = False
        self._operation_id: str | None = None
        self._source_ip = "unknown"

    def status(self) -> WeixinLoginStatus:
        with self._state_lock:
            return self._status_locked()

    def start(
        self,
        executable: str,
        *,
        operation_id: str,
        source_ip: str,
    ) -> WeixinLoginStatus:
        if not self._operation_lock.acquire(blocking=False):
            raise ApiError(
                409,
                "openclaw_operation_in_progress",
                "OpenClaw 正在执行其他维护操作。",
            )
        master_fd: int | None = None
        slave_fd: int | None = None
        process: subprocess.Popen[bytes] | None = None
        try:
            with self._state_lock:
                if self._process is not None and self._process.poll() is None:
                    raise ApiError(
                        409,
                        "weixin_login_in_progress",
                        "微信绑定正在进行中。",
                    )
                master_fd, slave_fd = pty.openpty()
                termios.tcsetwinsize(slave_fd, (48, 160))
                process = subprocess.Popen(
                    [
                        executable,
                        "channels",
                        "login",
                        "--channel",
                        "openclaw-weixin",
                    ],
                    stdin=slave_fd,
                    stdout=slave_fd,
                    stderr=slave_fd,
                    start_new_session=True,
                    close_fds=True,
                )
                os.close(slave_fd)
                slave_fd = None
                self._state = "starting"
                self._message = "正在生成微信绑定二维码…"
                self._created_at = utc_now()
                self._updated_at = self._created_at
                self._qr_png = None
                self._process = process
                self._master_fd = master_fd
                self._output = ""
                self._cancel_requested = False
                self._verification_submitted = False
                self._operation_id = operation_id
                self._source_ip = source_ip
                thread = threading.Thread(
                    target=self._read_process,
                    args=(process, master_fd),
                    name="openclaw-weixin-login",
                    daemon=True,
                )
                self._reader_thread = thread
                thread.start()
                return self._status_locked()
        except Exception:
            if process is not None and process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            if slave_fd is not None:
                os.close(slave_fd)
            if master_fd is not None:
                os.close(master_fd)
            self._operation_lock.release()
            raise

    def submit_verification(self, code: str) -> WeixinLoginStatus:
        if not VERIFY_CODE_PATTERN.fullmatch(code):
            raise ApiError(422, "invalid_weixin_verify_code", "验证码只能包含数字。")
        with self._state_lock:
            if self._state != "needs_verification" or self._master_fd is None:
                raise ApiError(
                    409,
                    "weixin_verification_not_required",
                    "当前微信绑定不需要输入验证码。",
                )
            try:
                os.write(self._master_fd, f"{code}\n".encode())
            except OSError as exc:
                raise ApiError(
                    409,
                    "weixin_login_not_running",
                    "微信绑定进程已经结束，请重新生成二维码。",
                ) from exc
            self._state = "confirming"
            self._message = "验证码已提交，正在确认绑定…"
            self._verification_submitted = True
            self._updated_at = utc_now()
            return self._status_locked()

    def cancel(self) -> WeixinLoginStatus:
        with self._state_lock:
            process = self._process
            reader_thread = self._reader_thread
            should_cancel = process is not None and process.poll() is None
            if should_cancel:
                self._cancel_requested = True
                self._state = "cancelling"
                self._message = "正在取消微信绑定…"
                self._qr_png = None
                self._updated_at = utc_now()
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        if should_cancel and process is not None:
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired as exc:
                    raise ApiError(
                        504,
                        "weixin_login_cancel_timeout",
                        "微信绑定进程未能及时退出，请稍后重试。",
                    ) from exc
        if reader_thread is not None:
            reader_thread.join(timeout=1)
            if reader_thread.is_alive():
                raise ApiError(
                    504,
                    "weixin_login_cancel_timeout",
                    "微信绑定进程已退出，但后台清理尚未完成，请稍后重试。",
                )
        with self._state_lock:
            if should_cancel:
                self._state = "cancelled"
                self._message = "微信绑定已取消。"
                self._updated_at = utc_now()
            return self._status_locked()

    def qr_content(self) -> bytes:
        with self._state_lock:
            if self._qr_png is None or self._state not in {
                "waiting_scan",
                "needs_verification",
                "confirming",
            }:
                raise ApiError(404, "weixin_qr_not_found", "微信绑定二维码不可用。")
            return self._qr_png

    def close(self) -> None:
        try:
            self.cancel()
        except ApiError:
            # Service shutdown is already in progress; cancellation performed its
            # bounded terminate/kill attempts and must not block other cleanup.
            return

    def _status_locked(self) -> WeixinLoginStatus:
        return WeixinLoginStatus(
            state=self._state,
            message=self._message,
            qr_available=self._qr_png is not None,
            created_at=self._created_at,
            updated_at=self._updated_at,
        )

    def _read_process(
        self,
        process: subprocess.Popen[bytes],
        master_fd: int,
    ) -> None:
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        try:
            while process.poll() is None:
                readable, _, _ = select.select([master_fd], [], [], 0.5)
                if readable:
                    self._consume_output(decoder.decode(os.read(master_fd, 8192)))
            while True:
                try:
                    readable, _, _ = select.select([master_fd], [], [], 0)
                    if not readable:
                        break
                    chunk = os.read(master_fd, 8192)
                    if not chunk:
                        break
                    self._consume_output(decoder.decode(chunk))
                except OSError:
                    break
            self._consume_output(decoder.decode(b"", final=True))
        except OSError:
            pass
        finally:
            try:
                os.close(master_fd)
            except OSError:
                pass
            return_code = process.wait()
            with self._state_lock:
                cancelled = self._cancel_requested
                if not cancelled:
                    if return_code == 0 and (
                        "已将此 OpenClaw 连接到微信" in self._output
                        or "已连接过此 OpenClaw" in self._output
                    ):
                        self._state = "succeeded"
                        self._message = "微信 ClawBot 已绑定到当前 OpenClaw。"
                    else:
                        self._state = "failed"
                        self._message = self._failure_message()
                self._qr_png = None
                self._updated_at = utc_now()
                self._process = None
                self._master_fd = None
                operation_id = self._operation_id
                source_ip = self._source_ip
                final_state = self._state
            if operation_id:
                write_operation(
                    operation_id=operation_id,
                    action="login_openclaw_weixin",
                    status="succeeded" if final_state == "succeeded" else "failed",
                    target="openclaw-weixin",
                    source_ip=source_ip,
                )
            self._operation_lock.release()

    def _consume_output(self, text: str) -> None:
        if not text:
            return
        cleaned = ANSI_ESCAPE_PATTERN.sub("", text).replace("\x00", "")
        with self._state_lock:
            self._output = (self._output + cleaned)[-MAX_LOGIN_OUTPUT_CHARS:]
            qr_png = extract_terminal_qr_png(self._output)
            if qr_png is not None:
                self._qr_png = qr_png
                if self._state in {"starting", "waiting_scan"}:
                    self._state = "waiting_scan"
                    self._message = "请使用手机微信扫描二维码并确认绑定。"
            verification_prompt = max(
                self._output.rfind("输入手机微信显示的数字"),
                self._output.rfind("请重新输入"),
            )
            verification_started = self._output.rfind("正在验证")
            if (
                "输入手机微信显示的数字" in cleaned
                or "请重新输入" in cleaned
            ):
                self._verification_submitted = False
            if verification_prompt > verification_started and not self._verification_submitted:
                self._state = "needs_verification"
                self._message = "请输入手机微信显示的数字验证码。"
            elif verification_started >= 0 or self._verification_submitted:
                self._state = "confirming"
                self._message = "已扫码，正在确认绑定…"
            self._updated_at = utc_now()

    def _failure_message(self) -> str:
        if "二维码多次失效" in self._output or "二维码已过期" in self._output:
            return "微信绑定二维码已过期，请重新生成。"
        if "多次输入错误" in self._output:
            return "验证码多次输入错误，请稍后重新绑定。"
        if "failed to get QR code" in self._output or "Failed to start login" in self._output:
            return "微信绑定二维码生成失败，请检查网络和插件状态。"
        return "微信绑定未完成，请重新生成二维码或检查 OpenClaw 日志。"
