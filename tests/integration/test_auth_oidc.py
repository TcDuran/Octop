"""OIDC HTTP route integration tests."""

from __future__ import annotations

from typing import Any

import pytest

from octop.api.routers.auth_oidc import _bad_request
from octop.infra.auth.sso.service import RedirectResult, SsoService
from tests.support.app import octop_client
from tests.support.auth import bearer, bootstrap_admin, login


@pytest.fixture
async def client(app_client):
    yield app_client


async def test_oidc_public_routes_and_exchange_return_login_shape(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    c, srv, home = client
    await bootstrap_admin(c, home)
    assert srv.user_manager is not None
    user = srv.user_manager.get("admin")
    assert user is not None

    monkeypatch.setattr(
        SsoService,
        "start_login",
        lambda self, *, redirect_after, public_base: {
            "authorization_url": (
                f"https://idp.example/authorize?redirect={redirect_after}&state=browser-state"
            ),
            "state": "browser-state",
        },
    )

    callback_states: list[str | None] = []

    async def callback(self: SsoService, **_: Any) -> RedirectResult:
        callback_states.append(_["state"])
        return RedirectResult("http://testserver/login/oidc/complete#code=one-time")

    async def exchange(self: SsoService, code: str):
        assert code == "one-time"
        return user

    monkeypatch.setattr(SsoService, "handle_callback", callback)
    monkeypatch.setattr(SsoService, "exchange_login_code", exchange)

    status = await c.get("/api/auth/oidc/status")
    assert status.status_code == 200
    assert status.json() == {"enabled": False, "display_name": ""}

    start = await c.post("/api/auth/oidc/start", json={"redirect_after": "/chat"})
    assert start.status_code == 200
    assert "redirect=/chat" in start.json()["authorization_url"]
    assert start.cookies["octop_sso_state"] == "browser-state"
    set_cookie = start.headers["set-cookie"]
    assert "HttpOnly" in set_cookie
    assert "Max-Age=600" in set_cookie
    assert "Path=/api/auth" in set_cookie
    assert "Path=/api/auth/oidc" not in set_cookie
    assert "SameSite=lax" in set_cookie

    callback_response = await c.get(
        "/api/auth/oidc/callback", params={"code": "provider-code", "state": "attacker-state"}
    )
    assert callback_response.status_code == 302
    assert callback_response.headers["location"] == "http://testserver/login?oidc_error=state"
    assert callback_states == []
    assert "octop_sso_state=" in callback_response.headers["set-cookie"]
    assert "Max-Age=0" in callback_response.headers["set-cookie"]

    start = await c.post("/api/auth/oidc/start", json={"redirect_after": "/chat"})
    callback_response = await c.get(
        "/api/auth/oidc/callback", params={"code": "provider-code", "state": "browser-state"}
    )
    assert callback_response.status_code == 302
    assert callback_response.headers["location"].endswith("#code=one-time")
    assert callback_states == ["browser-state"]

    exchange_response = await c.post("/api/auth/oidc/exchange", json={"code": "one-time"})
    assert exchange_response.status_code == 200
    body = exchange_response.json()
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == srv.services.config.access_token_ttl_seconds
    assert body["user"]["username"] == "admin"
    assert body["access_token"]


async def test_oidc_config_requires_admin_and_returns_public_callback_url(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    c, _srv, home = client
    await bootstrap_admin(c, home)
    token = await login(c)

    assert (await c.get("/api/auth/oidc/config")).status_code == 401

    config = await c.get("/api/auth/oidc/config", headers=bearer(token))
    assert config.status_code == 200
    assert config.json()["redirect_uri"] == "http://testserver/api/auth/oidc/callback"

    update = await c.put(
        "/api/auth/oidc/config",
        headers=bearer(token),
        json={
            "enabled": False,
            "display_name": "Company Login",
            "issuer": "https://issuer.example",
            "client_id": "octop-client",
            "dashboard_origin": "https://dashboard.example",
        },
    )
    assert update.status_code == 200
    assert update.json()["display_name"] == "Company Login"
    assert update.json()["redirect_uri"] == "http://testserver/api/auth/oidc/callback"
    assert "client_secret" not in update.json()

    config = await c.get(
        "/api/auth/oidc/config",
        headers={**bearer(token), "X-Forwarded-Proto": "https", "X-Forwarded-Host": "api.example"},
    )
    assert config.status_code == 200
    assert config.json()["dashboard_origin"] == "https://dashboard.example"
    assert config.json()["redirect_uri"] == "https://api.example/api/auth/oidc/callback"

    monkeypatch.setattr(
        SsoService, "test_connection", lambda self: {"ok": True, "detail": "reachable"}
    )
    tested = await c.post("/api/auth/oidc/config/test", headers=bearer(token))
    assert tested.status_code == 200
    assert tested.json() == {"ok": True, "detail": "reachable"}


async def test_oidc_config_persists_and_validates_trusted_hub_urls(client) -> None:
    c, srv, home = client
    await bootstrap_admin(c, home)
    headers = bearer(await login(c))
    urls = ["https://hub.example/service/*"]
    response = await c.put(
        "/api/auth/oidc/config", headers=headers, json={"extra": {"plat_mcp_urls": urls}}
    )
    assert response.status_code == 200
    provider = srv.services.sso_repo.get_by_kind("oidc")
    assert provider is not None
    assert provider.extra["plat_mcp_urls"] == urls

    response = await c.put(
        "/api/auth/oidc/config",
        headers=headers,
        json={"extra": {"plat_mcp_urls": ["https://*.example/service/*"]}},
    )
    assert response.status_code == 400
    provider = srv.services.sso_repo.get_by_kind("oidc")
    assert provider is not None
    assert provider.extra["plat_mcp_urls"] == urls


def test_oidc_bad_request_keeps_detail_when_localized() -> None:
    error = _bad_request(ValueError("PLAT embedded login is not allowed for this origin"))

    assert error.to_envelope(locale="zh")["error"]["message"] == (
        "OIDC 请求无效：PLAT embedded login is not allowed for this origin"
    )


async def test_public_base_url_reaches_oidc_and_invite_urls(tmp_octop_home, monkeypatch) -> None:
    public_base = "http://221.232.69.57:7321/octop"
    monkeypatch.setenv("OCTOP_PUBLIC_BASE_URL", public_base)

    async with octop_client(tmp_octop_home, patch_llm=False) as (client, server):
        assert server.services.config.public_base_url == public_base
        await bootstrap_admin(client, tmp_octop_home)
        headers = bearer(await login(client))

        oidc = await client.get("/api/auth/oidc/config", headers=headers)
        assert oidc.status_code == 200
        assert oidc.json()["redirect_uri"] == (f"{public_base}/api/auth/oidc/callback")

        observed_public_bases: list[str] = []

        def start_login(
            self: SsoService, *, redirect_after: str | None, public_base: str
        ) -> dict[str, str]:
            observed_public_bases.append(public_base)
            return {
                "authorization_url": "https://idp.example/authorize",
                "state": "configured-state",
            }

        monkeypatch.setattr(SsoService, "start_login", start_login)
        started = await client.post("/api/auth/oidc/start", json={"redirect_after": "/chat"})
        assert started.status_code == 200
        assert "Path=/octop/api/auth" in started.headers["set-cookie"]
        assert observed_public_bases == [public_base]

        updated = await client.put(
            "/api/auth/oidc/config",
            headers=headers,
            json={"dashboard_origin": "https://dashboard.example"},
        )
        assert updated.status_code == 200
        callback = await client.get(
            "/api/auth/oidc/callback",
            params={"state": "configured-state", "error": "access_denied"},
            headers={"cookie": "octop_sso_state=configured-state"},
        )
        assert "Path=/octop/api/auth" in callback.headers["set-cookie"]
        assert callback.headers["location"] == (
            "https://dashboard.example/octop/login?oidc_error=denied"
        )

        invite = await client.post("/api/users/invites", headers=headers, json={})
        assert invite.status_code == 201
        assert invite.json()["invite_url"].startswith(f"{public_base}/invite?code=")
