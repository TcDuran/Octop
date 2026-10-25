"""OIDC onboarding reuses the bundled template and never duplicates owned experts."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from octop.config import OctopConfig
from octop.infra.agents.experts.catalog import ExpertCatalog, default_library_root
from octop.infra.auth.sso.default_agent import ensure_default_oidc_agent
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.services import build_shared_services
from octop.infra.server import OctopServer
from octop.infra.utils.paths import PathLayout


@pytest.fixture
def onboarding(tmp_path):
    paths = PathLayout(tmp_path)
    paths.ensure_root()
    db = SqlitePool(paths.db)
    run_migrations(db)
    services = build_shared_services(db=db, paths=paths, config=OctopConfig())
    catalog = ExpertCatalog(default_library_root())
    catalog.refresh()

    async def create(spec):
        await asyncio.sleep(0)
        agent_id = f"test-agent-{spec.user_id}"
        services.agent_repo.create(
            agent_id=agent_id,
            user_id=spec.user_id,
            name=spec.name,
            template_name=spec.template_name,
        )
        return services.agent_repo.get(agent_id)

    registry = SimpleNamespace(create=AsyncMock(side_effect=create))
    server = OctopServer(home=tmp_path)
    server.services = services
    server.app_runtime = SimpleNamespace(agent_registry=registry)
    server.expert_catalog = catalog
    yield server, services, registry, catalog
    db.close()


async def test_new_oidc_user_gets_renamed_original_template(onboarding):
    server, services, registry, catalog = onboarding
    uid = services.user_repo.create(username="new-member", role="user")
    await server._initialize_oidc_agent(uid)
    spec = registry.create.call_args.args[0]
    assert spec.name == "PLAT·通用助手"
    assert spec.template_name == "general-assistant"
    assert spec.user_id == uid
    assert spec.is_shared is False
    assert spec.default_model is None  # Inherit the configured model resolution.
    assert catalog.get(spec.template_name).prompt_files == ["SOUL.md"]


async def test_existing_personal_expert_is_preserved(onboarding):
    server, services, registry, _ = onboarding
    uid = services.user_repo.create(username="existing-member", role="user")
    services.agent_repo.create(agent_id="personal", user_id=uid, name="我的工作助手")
    await server._initialize_oidc_agent(uid)
    registry.create.assert_not_called()
    assert services.agent_repo.get("personal").name == "我的工作助手"


async def test_repeated_and_concurrent_logins_create_only_one(onboarding):
    server, services, registry, _ = onboarding
    uid = services.user_repo.create(username="parallel-member", role="user")
    await asyncio.gather(*(server._initialize_oidc_agent(uid) for _ in range(5)))
    await server._initialize_oidc_agent(uid)
    assert len(services.agent_repo.list_by_user(uid)) == 1
    registry.create.assert_awaited_once()


async def test_another_users_agent_is_not_reused(onboarding):
    server, services, registry, _ = onboarding
    first = services.user_repo.create(username="first-member", role="user")
    second = services.user_repo.create(username="second-member", role="user")
    await server._initialize_oidc_agent(first)
    await server._initialize_oidc_agent(second)
    assert registry.create.await_count == 2
    assert services.agent_repo.list_by_user(first)[0].agent_id != (
        services.agent_repo.list_by_user(second)[0].agent_id
    )


async def test_unavailable_template_does_not_create_an_empty_agent(onboarding):
    _, services, registry, catalog = onboarding
    uid = services.user_repo.create(username="missing-template", role="user")
    catalog._experts.pop("general-assistant")
    with pytest.raises(RuntimeError, match="template is unavailable"):
        await ensure_default_oidc_agent(
            services=services, registry=registry, catalog=catalog, user_id=uid
        )
    registry.create.assert_not_called()
