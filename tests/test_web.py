import httpx
import pytest

from app.application import create_app
from app.core.config import Settings


@pytest.mark.anyio
async def test_home_page_is_public_and_contains_no_token(settings: Settings) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/")

    assert response.status_code == 200
    assert 'type="password"' in response.text
    assert 'src="/static/app.js"' in response.text
    assert 'href="/static/app.css"' in response.text
    assert 'id="connected-bar"' in response.text
    assert "更换凭证" in response.text
    assert "清除凭证" in response.text
    assert "节点任务" in response.text
    assert "查看原始结果" in response.text
    assert 'aria-controls="status-details"' in response.text
    assert 'id="status-details"' in response.text
    assert "展开详情" in response.text
    assert settings.security.token.get_secret_value() not in response.text


@pytest.mark.anyio
async def test_web_assets_are_available(settings: Settings) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        script = await client.get("/static/app.js")
        stylesheet = await client.get("/static/app.css")

    assert script.status_code == 200
    assert stylesheet.status_code == 200
    assert "innerHTML" not in script.text
    assert "sessionStorage" in script.text
    assert "localStorage" in script.text
    assert "Authorization" in script.text
    assert "accessVersion" in script.text
    assert "connectWithToken" in script.text
    assert "新 Token 验证成功后才会替换当前连接" in script.text
    assert "setStatusDetailsExpanded" in script.text
    assert "确定清除此设备保存的 Hub Token 吗？" in script.text
    assert "版本信息" in script.text
    assert "task-button-running" in script.text
    assert "task-button-paused" in script.text


@pytest.mark.anyio
async def test_security_headers_apply_to_page_assets_and_api(
    settings: Settings,
) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        page = await client.get("/")
        asset = await client.get("/static/app.js")
        api = await client.get("/api/health")

    for response in [page, asset, api]:
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["referrer-policy"] == "no-referrer"
    for response in [page, asset]:
        assert "default-src 'self'" in response.headers["content-security-policy"]
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert "content-security-policy" not in api.headers


@pytest.mark.anyio
async def test_page_uses_external_script_only(settings: Settings) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/")

    assert response.text.count("<script") == 1
    assert "<script src=\"/static/app.js\" defer></script>" in response.text


@pytest.mark.anyio
async def test_api_documentation_is_not_public(settings: Settings) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        responses = [
            await client.get("/docs"),
            await client.get("/redoc"),
            await client.get("/openapi.json"),
        ]

    for response in responses:
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"
