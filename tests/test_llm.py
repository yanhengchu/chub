import json
import asyncio
from pathlib import Path

import httpx
import pytest

from app.core.config import LlmConfig
from app.llm import (
    LlmConfigurationError,
    LlmRequestError,
    LlmService,
)
from app.llm.openclaw_config import OpenClawLlmConfigLoader


def write_openclaw_config(
    tmp_path: Path,
    *,
    api: str = "openai-completions",
    base_url: str = "https://llm.example.test/v1",
    api_key: str = "test-secret-key",
    secret_mode: int = 0o600,
) -> tuple[Path, Path]:
    secret_file = tmp_path / "llm-secret.txt"
    secret_file.write_text(f"{api_key}\n", encoding="utf-8")
    secret_file.chmod(secret_mode)
    config_file = tmp_path / "openclaw.json"
    config_file.write_text(
        json.dumps(
            {
                "models": {
                    "providers": {
                        "test-provider": {
                            "baseUrl": base_url,
                            "api": api,
                            "apiKey": {
                                "source": "file",
                                "provider": "test_secret",
                                "id": "value",
                            },
                            "models": [
                                {
                                    "id": "test-model",
                                    "name": "Test Model",
                                }
                            ],
                        }
                    }
                },
                "secrets": {
                    "providers": {
                        "test_secret": {
                            "source": "file",
                            "path": str(secret_file),
                            "mode": "singleValue",
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    config_file.chmod(0o600)
    return config_file, secret_file


def llm_settings(config_file: Path, **overrides: object) -> LlmConfig:
    return LlmConfig(
        openclaw_config_file=config_file,
        timeout_seconds=5,
        max_tokens=64,
        **overrides,
    )


def test_openclaw_llm_config_loads_single_provider_and_file_secret(
    tmp_path: Path,
) -> None:
    config_file, _ = write_openclaw_config(tmp_path)

    resolved = OpenClawLlmConfigLoader(llm_settings(config_file)).load()

    assert resolved.provider == "test-provider"
    assert resolved.model == "test-model"
    assert resolved.api == "openai-completions"
    assert resolved.base_url == "https://llm.example.test/v1"
    assert resolved.api_key.get_secret_value() == "test-secret-key"
    assert "test-secret-key" not in repr(resolved)


def test_openclaw_llm_config_rejects_non_private_secret_file(
    tmp_path: Path,
) -> None:
    config_file, _ = write_openclaw_config(tmp_path, secret_mode=0o644)

    with pytest.raises(
        LlmConfigurationError,
        match="权限必须限制为当前用户",
    ):
        OpenClawLlmConfigLoader(llm_settings(config_file)).load()


def test_openclaw_llm_config_rejects_non_private_config_file(
    tmp_path: Path,
) -> None:
    config_file, _ = write_openclaw_config(tmp_path)
    config_file.chmod(0o644)

    with pytest.raises(
        LlmConfigurationError,
        match="权限必须限制为当前用户",
    ):
        OpenClawLlmConfigLoader(llm_settings(config_file)).load()


def test_openclaw_llm_config_reloads_when_secret_changes(tmp_path: Path) -> None:
    config_file, secret_file = write_openclaw_config(tmp_path)
    loader = OpenClawLlmConfigLoader(llm_settings(config_file))

    first = loader.load()
    cached = loader.load()
    secret_file.write_text("rotated-test-secret-key\n", encoding="utf-8")
    secret_file.chmod(0o600)
    rotated = loader.load()

    assert first is cached
    assert first.api_key.get_secret_value() == "test-secret-key"
    assert rotated.api_key.get_secret_value() == "rotated-test-secret-key"
    assert rotated is not first


def test_openclaw_llm_config_uses_default_model_with_multiple_candidates(
    tmp_path: Path,
) -> None:
    config_file, _ = write_openclaw_config(tmp_path)
    payload = json.loads(config_file.read_text(encoding="utf-8"))
    provider = payload["models"]["providers"]["test-provider"]
    provider["models"].append({"id": "default-model", "name": "Default"})
    payload["agents"] = {
        "defaults": {"model": {"primary": "test-provider/default-model"}}
    }
    config_file.write_text(json.dumps(payload), encoding="utf-8")
    config_file.chmod(0o600)

    resolved = OpenClawLlmConfigLoader(llm_settings(config_file)).load()

    assert resolved.model == "default-model"


def test_openclaw_llm_config_uses_explicit_provider_and_model(
    tmp_path: Path,
) -> None:
    config_file, _ = write_openclaw_config(tmp_path)
    payload = json.loads(config_file.read_text(encoding="utf-8"))
    payload["models"]["providers"]["other-provider"] = {
        "baseUrl": "https://other.example.test/v1",
        "api": "openai-completions",
        "apiKey": {
            "source": "file",
            "provider": "test_secret",
            "id": "value",
        },
        "models": [{"id": "other-model", "name": "Other"}],
    }
    config_file.write_text(json.dumps(payload), encoding="utf-8")
    config_file.chmod(0o600)

    resolved = OpenClawLlmConfigLoader(
        llm_settings(
            config_file,
            provider="other-provider",
            model="other-model",
        )
    ).load()

    assert resolved.provider == "other-provider"
    assert resolved.model == "other-model"


def test_openclaw_llm_config_resolves_relative_secret_path(
    tmp_path: Path,
) -> None:
    config_file, _ = write_openclaw_config(tmp_path)
    payload = json.loads(config_file.read_text(encoding="utf-8"))
    payload["secrets"]["providers"]["test_secret"]["path"] = "llm-secret.txt"
    config_file.write_text(json.dumps(payload), encoding="utf-8")
    config_file.chmod(0o600)

    resolved = OpenClawLlmConfigLoader(llm_settings(config_file)).load()

    assert resolved.api_key.get_secret_value() == "test-secret-key"


def test_openclaw_llm_config_rejects_unsupported_api(
    tmp_path: Path,
) -> None:
    config_file, _ = write_openclaw_config(
        tmp_path,
        api="anthropic-messages",
    )

    with pytest.raises(LlmConfigurationError, match="暂不支持"):
        OpenClawLlmConfigLoader(llm_settings(config_file)).load()


def test_openclaw_llm_config_rejects_unsafe_base_url(
    tmp_path: Path,
) -> None:
    config_file, _ = write_openclaw_config(
        tmp_path,
        base_url="http://remote.example.test/v1",
    )

    with pytest.raises(LlmConfigurationError, match="HTTPS"):
        OpenClawLlmConfigLoader(llm_settings(config_file)).load()


@pytest.mark.anyio
async def test_llm_service_calls_openai_compatible_api_without_leaking_secret(
    tmp_path: Path,
) -> None:
    config_file, _ = write_openclaw_config(tmp_path)
    captured_request: httpx.Request | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_request
        captured_request = request
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "CHUB_LLM_OK",
                        }
                    }
                ]
            },
        )

    service = LlmService(
        llm_settings(config_file),
        transport=httpx.MockTransport(handler),
    )

    result = await service.complete(
        "Return a short status.",
        system_prompt="Reply concisely.",
        max_tokens=16,
    )

    assert result.content == "CHUB_LLM_OK"
    assert result.provider == "test-provider"
    assert result.model == "test-model"
    assert captured_request is not None
    assert captured_request.url == "https://llm.example.test/v1/chat/completions"
    assert captured_request.headers["Authorization"] == "Bearer test-secret-key"
    body = json.loads(captured_request.content)
    assert body == {
        "model": "test-model",
        "messages": [
            {"role": "system", "content": "Reply concisely."},
            {"role": "user", "content": "Return a short status."},
        ],
        "max_tokens": 16,
        "stream": False,
    }
    await service.close()


@pytest.mark.anyio
async def test_llm_service_http_error_does_not_include_response_or_secret(
    tmp_path: Path,
) -> None:
    config_file, _ = write_openclaw_config(tmp_path)
    service = LlmService(
        llm_settings(config_file),
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                401,
                text="test-secret-key must never be surfaced",
            )
        ),
    )

    with pytest.raises(LlmRequestError) as error:
        await service.complete("Hello")

    assert error.value.code == "authentication_failed"
    assert error.value.retryable is False
    assert error.value.status_code == 401
    assert "test-secret-key" not in str(error.value)
    await service.close()


@pytest.mark.anyio
async def test_llm_service_rejects_unrecognized_success_response(
    tmp_path: Path,
) -> None:
    config_file, _ = write_openclaw_config(tmp_path)
    service = LlmService(
        llm_settings(config_file),
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json={"unexpected": True})
        ),
    )

    with pytest.raises(LlmRequestError, match="无法识别"):
        await service.complete("Hello")
    await service.close()


@pytest.mark.anyio
async def test_llm_service_rejects_empty_response(tmp_path: Path) -> None:
    config_file, _ = write_openclaw_config(tmp_path)
    service = LlmService(
        llm_settings(config_file),
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json={"choices": [{"message": {"content": "  "}}]},
            )
        ),
    )

    with pytest.raises(LlmRequestError) as error:
        await service.complete("Hello")

    assert error.value.code == "empty_response"
    await service.close()


@pytest.mark.anyio
async def test_llm_service_rejects_zero_max_tokens(tmp_path: Path) -> None:
    config_file, _ = write_openclaw_config(tmp_path)
    service = LlmService(
        llm_settings(config_file),
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json={})
        ),
    )

    with pytest.raises(ValueError, match="max_tokens"):
        await service.complete("Hello", max_tokens=0)
    await service.close()


@pytest.mark.anyio
async def test_llm_service_reuses_client_and_closes_it(tmp_path: Path) -> None:
    config_file, _ = write_openclaw_config(tmp_path)
    request_count = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}}]},
        )

    service = LlmService(
        llm_settings(config_file),
        transport=httpx.MockTransport(handler),
    )

    await service.complete("one")
    client = service.protocol._client
    await service.complete("two")

    assert request_count == 2
    assert service.protocol._client is client
    assert client is not None and client.is_closed is False
    await service.close()
    assert client.is_closed is True


@pytest.mark.anyio
async def test_llm_service_limits_concurrency(tmp_path: Path) -> None:
    config_file, _ = write_openclaw_config(tmp_path)
    active = 0
    maximum_active = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        await asyncio.sleep(0.01)
        active -= 1
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}}]},
        )

    service = LlmService(
        llm_settings(config_file, max_concurrency=2),
        transport=httpx.MockTransport(handler),
    )

    await asyncio.gather(*(service.complete(str(index)) for index in range(4)))

    assert maximum_active == 2
    await service.close()


@pytest.mark.anyio
async def test_llm_service_rejects_oversized_response(tmp_path: Path) -> None:
    config_file, _ = write_openclaw_config(tmp_path)
    service = LlmService(
        llm_settings(config_file, max_response_bytes=1024),
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, content=b"x" * 1025)
        ),
    )

    with pytest.raises(LlmRequestError) as error:
        await service.complete("Hello")

    assert error.value.code == "response_too_large"
    assert error.value.retryable is False
    await service.close()


@pytest.mark.anyio
async def test_llm_service_classifies_timeout(tmp_path: Path) -> None:
    config_file, _ = write_openclaw_config(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    service = LlmService(
        llm_settings(config_file),
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(LlmRequestError) as error:
        await service.complete("Hello")

    assert error.value.code == "timeout"
    assert error.value.retryable is True
    await service.close()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status_code", "code", "retryable"),
    [
        (429, "rate_limited", True),
        (503, "provider_unavailable", True),
        (400, "request_failed", False),
    ],
)
async def test_llm_service_classifies_provider_errors(
    tmp_path: Path,
    status_code: int,
    code: str,
    retryable: bool,
) -> None:
    config_file, _ = write_openclaw_config(tmp_path)
    service = LlmService(
        llm_settings(config_file),
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(status_code)
        ),
    )

    with pytest.raises(LlmRequestError) as error:
        await service.complete("Hello")

    assert error.value.code == code
    assert error.value.retryable is retryable
    assert error.value.status_code == status_code
    await service.close()
