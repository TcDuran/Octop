"""Shared definitions never inherit their owner's business identity."""

from __future__ import annotations

import copy
import time

import httpx
import pytest
from tests.unit.auth import test_plat_credentials

from octop.infra.auth.plat_credentials import PlatCredential, PlatCredentialStore
from octop.infra.connectors.plat_auth import PlatMcpAuth, matches_url, trusted_urls
from octop.infra.connectors.service import ConnectorService
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.users.manager import UserManager

services = test_plat_credentials.services


@pytest.mark.parametrize(
    ("url", "rule", "expected"),
    [
        ("https://hub.example/service/oa/mcp", "https://hub.example/service/*", True),
        ("https://hub.example/service2/oa/mcp", "https://hub.example/service/*", False),
        ("https://hub.example/service", "https://hub.example/service/*", False),
        (
            "https://HUB.example:443/service/oa/mcp?foo=bar",
            "https://hub.example/service/oa/mcp",
            True,
        ),
        ("http://hub.example/service/oa/mcp", "https://hub.example/service/*", False),
        ("https://hub.example:444/service/oa/mcp", "https://hub.example/service/*", False),
        ("http://127.0.0.1/mcp", "http://localhost/mcp", False),
        ("https://hub.example.evil/service/mcp", "https://hub.example/*", False),
    ],
)
def test_url_boundaries(url, rule, expected):
    assert matches_url(url, rule) is expected


@pytest.mark.parametrize(
    "rule",
    [
        "https://user:password@hub.example/mcp",
        "https://hub.example/mcp#fragment",
        "https://hub.example/mcp?query=1",
        "https://hub.example/service/../mcp",
        "https://hub.example/service/%2fsecret",
        "https://hub.example/service/%252fsecret",
        "https://hub.example/service/\\secret",
        "https://hub.example/*/mcp",
        "https://*.example/mcp",
    ],
)
def test_invalid_rules(rule):
    with pytest.raises(ValueError):
        trusted_urls({"plat_mcp_urls": [rule]})


@pytest.mark.asyncio
async def test_shared_definition_uses_each_callers_token_and_preserves_headers(services):
    p = services.sso_repo.upsert_by_kind(
        "oidc",
        enabled=True,
        display_name="PLAT",
        issuer="https://plat.example",
        client_id="assistant",
        client_secret_enc=None,
        scopes="openid profile",
        dashboard_origin=None,
        extra={"plat_mcp_urls": ["https://hub.example/service/*"]},
    )
    manager = UserManager(services)
    owner = await manager.create(username="owner", password="Long-test-password-42!", role="admin")
    a = await manager.resolve_or_create_sso_user(
        provider_id=p.id, subject="person-a", claims={"sub": "person-a"}
    )
    b = await manager.resolve_or_create_sso_user(
        provider_id=p.id, subject="person-b", claims={"sub": "person-b"}
    )
    auth = PlatMcpAuth(services.secret_repo, services.sso_repo, services.user_repo)
    svc = ConnectorService(
        repo=services.repos.connector_repo,
        secret_repo=services.secret_repo,
        settings_repo=services.settings_repo,
        config=services.config,
        plat_auth=auth,
    )
    original = {
        "transport": "streamable_http",
        "url": "https://hub.example/service/oa/mcp",
        "headers": {"X-App-Id": "oa-assistant"},
        "shared": True,
        "enabled": True,
    }
    svc.put_custom_servers(owner.id, {"oa": copy.deepcopy(original)})
    store = PlatCredentialStore(services.secret_repo)
    for user, subject, token in [(a, "person-a", "test-token-A"), (b, "person-b", "test-token-B")]:
        store.save(
            user.id,
            p.id,
            PlatCredential(token, int(time.time()) + 1000, p.issuer, subject, p.client_id),
        )
    configs_a = svc.custom_harness_configs(a.id)
    configs_b = svc.custom_harness_configs(b.id)
    spec_a = next(iter(configs_a.values()))
    spec_b = next(iter(configs_b.values()))
    prepared_a = svc.prepare_mcp_spec(a.id, spec_a)
    prepared_b = svc.prepare_mcp_spec(b.id, spec_b)
    assert prepared_a["headers"]["X-Plat-Token"] == "test-token-A"
    assert prepared_b["headers"]["X-Plat-Token"] == "test-token-B"
    assert prepared_a["headers"]["X-App-Id"] == prepared_b["headers"]["X-App-Id"] == "oa-assistant"
    assert "X-Plat-Token" not in spec_a["headers"]
    assert "X-Plat-Token" not in svc.get_custom_servers(owner.id)["oa"]["headers"]
    with pytest.raises(OctopError) as failure:
        svc.prepare_mcp_spec(owner.id, spec_a)
    assert failure.value.code is ErrorCode.PLAT_LOGIN_REQUIRED
    for name in ["X-Plat-Token", "x-plat-token", "Authorization", "token"]:
        with pytest.raises(OctopError) as failure:
            svc.prepare_mcp_spec(a.id, {**spec_a, "headers": {name: "another"}})
        assert failure.value.code is ErrorCode.PLAT_MCP_CONFIG_INVALID
    untouched = {**spec_a, "url": "https://other.example/mcp"}
    assert svc.prepare_mcp_spec(owner.id, untouched) is untouched
    async with prepared_a["httpx_client_factory"]() as client:
        assert client.follow_redirects is False
    assert isinstance(client, httpx.AsyncClient)
