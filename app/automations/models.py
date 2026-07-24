from __future__ import annotations

from datetime import datetime
from pathlib import Path
from string import Formatter
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictAutomationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BrowserConfig(StrictAutomationModel):
    session: Literal["debug-chrome"] = "debug-chrome"
    start_url: str
    allowed_hosts: list[str] = Field(min_length=1)

    @field_validator("start_url")
    @classmethod
    def validate_start_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("start_url must be an HTTP(S) URL")
        return value

    @field_validator("allowed_hosts")
    @classmethod
    def normalize_hosts(cls, value: list[str]) -> list[str]:
        hosts = []
        for item in value:
            host = item.strip().lower().rstrip(".")
            if not host or "/" in host or ":" in host:
                raise ValueError("allowed_hosts must contain hostnames only")
            hosts.append(host)
        return list(dict.fromkeys(hosts))


class LoginCheckConfig(StrictAutomationModel):
    selector: str = Field(min_length=1)
    timeout_ms: int = Field(default=10_000, ge=100, le=120_000)


class LoginConfig(StrictAutomationModel):
    redirect_hosts: list[str] = Field(default_factory=list)
    check: LoginCheckConfig
    expired_message: str = Field(
        default="登录状态已失效，请重新登录 Debug Chrome",
        min_length=1,
    )

    @field_validator("redirect_hosts")
    @classmethod
    def normalize_redirect_hosts(cls, value: list[str]) -> list[str]:
        hosts = []
        for item in value:
            host = item.strip().lower().rstrip(".")
            if not host or "/" in host or ":" in host:
                raise ValueError("redirect_hosts must contain hostnames only")
            hosts.append(host)
        return list(dict.fromkeys(hosts))


class AutomationStep(StrictAutomationModel):
    action: Literal["goto", "wait", "hover", "click", "dispatch_event"]
    selector: str | None = None
    url: str | None = None
    event: str | None = None
    expect: Literal["download"] | None = None
    timeout_ms: int = Field(default=30_000, ge=100, le=120_000)

    @model_validator(mode="after")
    def validate_action_fields(self) -> "AutomationStep":
        if self.action == "goto":
            if not self.url or self.selector or self.event or self.expect:
                raise ValueError("goto requires only url")
            parsed = urlparse(self.url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ValueError("goto url must be an HTTP(S) URL")
            return self

        if not self.selector or self.url:
            raise ValueError(f"{self.action} requires selector and does not accept url")
        if self.action == "dispatch_event":
            if not self.event:
                raise ValueError("dispatch_event requires event")
        elif self.event:
            raise ValueError(f"{self.action} does not accept event")
        if self.expect and self.action not in {"click", "dispatch_event"}:
            raise ValueError("expect: download requires click or dispatch_event")
        return self


class OutputConfig(StrictAutomationModel):
    directory: Path
    filename: str = Field(min_length=1)
    conflict: Literal["replace", "skip", "fail"] = "fail"
    timezone: str = Field(min_length=1)

    @field_validator("directory")
    @classmethod
    def validate_relative_directory(cls, value: Path) -> Path:
        if value.is_absolute() or ".." in value.parts:
            raise ValueError("output directory must be a safe relative path")
        return value

    @field_validator("filename")
    @classmethod
    def validate_filename(cls, value: str) -> str:
        if Path(value).name != value or value in {".", ".."}:
            raise ValueError("filename must not contain a path")
        try:
            for _, field_name, _, conversion in Formatter().parse(value):
                if field_name is not None and (field_name != "date" or conversion):
                    raise ValueError
            value.format(date=datetime(2000, 1, 1))
        except (KeyError, ValueError) as exc:
            raise ValueError("filename supports only the date format variable") from exc
        return value


class ValidationConfig(StrictAutomationModel):
    non_empty: bool = True
    extensions: list[str] = Field(min_length=1)
    min_bytes: int = Field(default=1, ge=1)
    signature: Literal["pdf", "zip", "markdown"]

    @field_validator("extensions")
    @classmethod
    def normalize_extensions(cls, value: list[str]) -> list[str]:
        normalized = []
        for item in value:
            extension = item.lower()
            if not extension.startswith(".") or "/" in extension or "\\" in extension:
                raise ValueError("extensions must use values such as .pdf")
            normalized.append(extension)
        return list(dict.fromkeys(normalized))

    @model_validator(mode="after")
    def validate_signature_extension(self) -> "ValidationConfig":
        compatible = {
            "pdf": {".pdf"},
            "zip": {".zip", ".docx", ".xlsx", ".pptx"},
            "markdown": {".md", ".markdown"},
        }[self.signature]
        if not compatible.intersection(self.extensions):
            raise ValueError("signature is incompatible with configured extensions")
        return self


class ExecutionConfig(StrictAutomationModel):
    timeout_ms: int = Field(default=120_000, ge=1_000, le=3_600_000)
    lock_timeout_ms: int = Field(default=1_000, ge=0, le=60_000)
    safe_step_retries: int = Field(default=1, ge=0, le=3)


class AutomationTaskConfig(StrictAutomationModel):
    name: str = Field(min_length=1)
    description: str = ""
    enabled: bool = True
    browser: BrowserConfig
    login: LoginConfig
    steps: list[AutomationStep] = Field(min_length=1)
    output: OutputConfig
    validation: ValidationConfig
    execution: ExecutionConfig = ExecutionConfig()
    extension: Literal["v-weekly-report-linked-documents"] | None = None

    @model_validator(mode="after")
    def validate_download_step(self) -> "AutomationTaskConfig":
        downloads = sum(step.expect == "download" for step in self.steps)
        if downloads != 1:
            raise ValueError("each task must contain exactly one download step")
        start_host = urlparse(self.browser.start_url).hostname
        if start_host and start_host.lower() not in self.browser.allowed_hosts:
            raise ValueError("start_url host must be included in allowed_hosts")
        return self


class AutomationsFile(StrictAutomationModel):
    version: Literal[1] = 1
    tasks: dict[str, AutomationTaskConfig] = Field(default_factory=dict)

    @field_validator("tasks")
    @classmethod
    def validate_task_ids(
        cls, value: dict[str, AutomationTaskConfig]
    ) -> dict[str, AutomationTaskConfig]:
        for task_id in value:
            if not task_id or any(
                character not in "abcdefghijklmnopqrstuvwxyz0123456789-_"
                for character in task_id
            ):
                raise ValueError("task ids may contain lowercase letters, digits, - and _")
        return value


class FeishuDocumentTask(StrictAutomationModel):
    name: str = Field(min_length=1)
    url: str
    enabled: bool = True
    format: Literal["markdown"] = "markdown"
    extension: Literal["v-weekly-report-linked-documents"] | None = None

    @field_validator("url")
    @classmethod
    def validate_feishu_document_url(cls, value: str) -> str:
        parsed = urlparse(value)
        host = (parsed.hostname or "").lower().rstrip(".")
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError("url contains an invalid port") from exc
        if (
            parsed.scheme != "https"
            or not host.endswith(".feishu.cn")
            or not parsed.path.startswith("/wiki/")
            or parsed.username is not None
            or parsed.password is not None
            or port is not None
        ):
            raise ValueError("url must be an HTTPS Feishu Wiki document URL")
        return value


class FeishuDocumentFile(StrictAutomationModel):
    version: Literal[2]
    tasks: dict[str, FeishuDocumentTask] = Field(default_factory=dict)

    @field_validator("tasks")
    @classmethod
    def validate_task_ids(
        cls, value: dict[str, FeishuDocumentTask]
    ) -> dict[str, FeishuDocumentTask]:
        for task_id in value:
            if not task_id or any(
                character not in "abcdefghijklmnopqrstuvwxyz0123456789-_"
                for character in task_id
            ):
                raise ValueError("task ids may contain lowercase letters, digits, - and _")
        return value


class AutomationTemplate(StrictAutomationModel):
    version: Literal[1] = 1
    format: Literal["markdown"]
    task: AutomationTaskConfig


class LinkedDocumentsSourceConfig(StrictAutomationModel):
    section: str = Field(min_length=1)
    link_type: Literal["markdown"] = "markdown"
    allowed_paths: list[str] = Field(min_length=1)
    max_documents: int = Field(default=20, ge=1, le=100)

    @field_validator("allowed_paths")
    @classmethod
    def validate_allowed_paths(cls, value: list[str]) -> list[str]:
        if any(not item.startswith("/") or ".." in item for item in value):
            raise ValueError("allowed_paths must contain safe absolute URL paths")
        return list(dict.fromkeys(value))


class LinkedDocumentsDownloadConfig(StrictAutomationModel):
    template: Literal["feishu-document-download"]
    format: Literal["markdown"] = "markdown"
    continue_on_error: bool = True


class LinkedDocumentsTemplate(StrictAutomationModel):
    version: Literal[1] = 1
    type: Literal["feishu-linked-documents"]
    source: LinkedDocumentsSourceConfig
    download: LinkedDocumentsDownloadConfig


AutomationStatus = Literal["idle", "queued", "running", "success", "failed"]


class LinkedDocumentResult(StrictAutomationModel):
    name: str
    status: Literal["success", "failed"]
    message: str
    output_file: str | None = None


class AutomationState(StrictAutomationModel):
    task_id: str
    status: AutomationStatus = "idle"
    run_id: str | None = None
    trigger: Literal["web", "cli", "schedule"] | None = None
    process_id: int | None = None
    operation_id: str | None = None
    source_ip: str | None = None
    operation_logged: bool = False
    message: str = "尚未执行"
    started_at: datetime | None = None
    finished_at: datetime | None = None
    output_file: str | None = None
    output_bytes: int | None = None
    linked_documents: list[LinkedDocumentResult] = Field(default_factory=list)


class AutomationTaskPublic(StrictAutomationModel):
    id: str
    name: str
    description: str
    enabled: bool
    state: AutomationState


class FeishuEnvironmentState(StrictAutomationModel):
    state: Literal[
        "unchecked",
        "checking",
        "available",
        "login_required",
        "failed",
        "browser_stopped",
    ] = "unchecked"
    message: str = "未检查"
    checked_at: datetime | None = None
    qr_available: bool = False


class BrowserProfilePublic(StrictAutomationModel):
    id: str
    name: str
    initialized: bool
    source_available: bool
    active: bool
    initialization_state: Literal["idle", "running", "failed"] = "idle"
    initialization_message: str | None = None


class AutomationListData(StrictAutomationModel):
    enabled: bool
    browser_state: Literal["running", "stopped", "invalid", "unavailable"]
    browser_message: str
    browser_mode: str | None = None
    browser_profile_id: str | None = None
    browser_profile_name: str | None = None
    browser_profiles: list[BrowserProfilePublic] = Field(default_factory=list)
    browser_profiles_error: str | None = None
    feishu_environment: FeishuEnvironmentState = Field(
        default_factory=FeishuEnvironmentState
    )
    enabled_count: int = 0
    tasks: list[AutomationTaskPublic]


class AutomationRunAccepted(StrictAutomationModel):
    task_id: str
    run_id: str
    status: Literal["queued"] = "queued"


class BrowserControlResult(StrictAutomationModel):
    state: Literal["running", "stopped"]
    mode: str | None = None
    profile_id: str | None = None
    profile_name: str | None = None
    message: str


class BrowserStartRequest(StrictAutomationModel):
    mode: Literal["headed", "headless"] = "headed"
    profile_id: str | None = Field(
        default=None,
        pattern=r"^(Default|Profile [0-9]+)$",
    )


class BrowserInitializationRequest(StrictAutomationModel):
    profile_id: str = Field(pattern=r"^(Default|Profile [0-9]+)$")
    mode: Literal["headed", "headless"] = "headed"


class BrowserInitializationAccepted(StrictAutomationModel):
    profile_id: str
    status: Literal["initializing"] = "initializing"
