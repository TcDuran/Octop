"""Identity and lifetime boundaries for private business credentials."""

from __future__ import annotations

import time
from dataclasses import replace

import pytest

from octop.config import OctopConfig
from octop.infra.auth.plat_credentials import PlatCredential, PlatCredentialStore
from octop.infra.auth.sso.service import SsoService
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.services import build_shared_services
from octop.infra.users.manager import UserManager
from octop.infra.utils.paths import PathLayout


@pytest.fixture
def services(tmp_path, monkeypatch):
    monkeypatch.setenv("OCTOP_HOME", str(tmp_path))
    paths = PathLayout(tmp_path)
    paths.ensure_root()
    db = SqlitePool(paths.db)
    run_migrations(db)
    shared = build_shared_services(db=db, paths=paths, config=OctopConfig())
    yield shared
    db.close()


def provider(services):
    return services.sso_repo.upsert_provider(
        enabled=True,
        display_name="PLAT",
        issuer="https://plat.example",
        client_id="assistant",
        client_secret_enc=None,
        scopes="openid profile",
        dashboard_origin=None,
    )


def test_credentials_are_encrypted_scoped_and_bound_to_identity(services):
    p = provider(services)
    store = PlatCredentialStore(services.secret_repo)
    cred = PlatCredential(
        "test-secret-A", int(time.time()) + 100, p.issuer, "person-A", p.client_id
    )
    store.save(1, p.id, cred)
    assert b"test-secret-A" not in services.secret_repo.get(store._key(1, p.id))
    assert store.get(1, p, "person-A") == cred
    assert store.get(2, p, "person-A") is None
    assert store.get(1, p, "person-B") is None
    assert store.get(1, replace(p, issuer="https://another.example"), "person-A") is None
    assert store.get(1, replace(p, client_id="another"), "person-A") is None
    store.save(1, p.id, replace(cred, access_token="test-secret-B"))
    assert store.get(1, p, "person-A").access_token == "test-secret-B"
    store.save(1, p.id, replace(cred, expires_at=1))
    assert store.get(1, p, "person-A") is None
    store.delete(1, p.id)
    assert store.get(1, p, "person-A") is None
    assert "test-secret-A" not in repr(cred)


@pytest.mark.parametrize(
    "changes",
    [
        {"access_token": ""},
        {"access_token": "bad\r\nheader"},
        {"token_type": "Basic"},
        {"expires_in": 0},
        {"expires_in": True},
        {"expires_in": "100"},
    ],
)
def test_rejects_invalid_exchange_response(services, changes):
    response = {"access_token": "test", "token_type": "Bearer", "expires_in": 100, **changes}
    with pytest.raises(ValueError):
        PlatCredential.from_response(response, provider(services), "person", 123)


@pytest.mark.asyncio
async def test_integrated_callback_saves_then_awaits_reload(services, monkeypatch):
    p = provider(services)
    services.sso_repo.upsert_by_kind(
        "oidc",
        enabled=True,
        display_name=p.display_name,
        issuer=p.issuer,
        client_id=p.client_id,
        client_secret_enc=None,
        scopes=p.scopes,
        dashboard_origin=None,
        extra={"plat_mcp_urls": ["https://hub.example/service/*"]},
    )
    calls = []
    store = PlatCredentialStore(services.secret_repo)

    async def reload(user_id):
        assert store.get(user_id, p, "person").access_token == "test-token"
        calls.append(user_id)

    sso = SsoService(services, UserManager(services), on_plat_login=reload)
    services.sso_repo.create_login_state(
        state="state",
        provider_id=p.id,
        nonce="nonce",
        code_verifier="verifier",
        redirect_after="/",
        expires_at=int(time.time()) + 600,
    )
    credential = PlatCredential(
        "test-token", int(time.time()) + 1000, p.issuer, "person", p.client_id
    )
    monkeypatch.setattr(
        sso._adapters["oidc"],
        "complete_plat_login",
        lambda *a, **kw: ("person", {"sub": "person"}, credential),
    )
    result = await sso.handle_callback(
        code="code", state="state", error=None, public_base="https://octop.example"
    )
    assert calls
    assert "/login/oidc/complete#code=" in result.url
