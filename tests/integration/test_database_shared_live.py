"""Opt-in PG/openGauss parity using only OID-recorded disposable databases.

OCTOP_TEST_SHARED_DATABASES=1 enables this module. The PG management DSN must
point at a loopback server. Existing provider rows are supplied in a private
file; source application databases are never reset by these tests.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from octop.config import DatabaseConfig, OctopConfig
from octop.infra.db.factory import open_database
from octop.infra.db.migrate import run_migrations
from octop.infra.utils.paths import PathLayout
from tests.integration import test_opengauss_control_plane as contracts
from tests.integration import test_opengauss_scenarios_live as workflows
from tests.integration.test_opengauss_control_plane import isolated_database
from tests.integration.test_opengauss_live import _record
from tests.support.app import write_octop_config

pytestmark = pytest.mark.skipif(
    os.environ.get("OCTOP_TEST_SHARED_DATABASES") != "1",
    reason="requires explicit isolated PG/openGauss parity configuration",
)


@contextmanager
def isolated_postgresql(tmp_path):
    params = conninfo_to_dict(os.environ["OCTOP_TEST_PG_ADMIN_DSN"])
    assert params.get("host") in {"127.0.0.1", "localhost", "::1"}
    params["dbname"] = "postgres"
    name = "octop_parity_" + uuid.uuid4().hex[:16]
    params["application_name"] = name
    params["connect_timeout"] = "8"
    admin_dsn = make_conninfo(**params)
    record = tmp_path / "database.json"
    record.write_text(json.dumps({"database": name, "oid": None}))
    pool = None
    oid = None
    with psycopg.connect(admin_dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(name)))
        oid = admin.execute("SELECT oid FROM pg_database WHERE datname=%s", (name,)).fetchone()[0]
        record.write_text(json.dumps({"database": name, "oid": oid}))
    try:
        config = DatabaseConfig(
            driver="postgresql",
            host=params["host"],
            port=int(params.get("port", 5432)),
            database=name,
            user=params["user"],
            password=params.get("password", ""),
            url=make_conninfo(**{**params, "dbname": name}),
        )
        pool = open_database(OctopConfig(database=config), PathLayout(tmp_path))
        with pool.connect() as conn:
            current = conn.execute(
                "SELECT current_database(), oid FROM pg_database WHERE datname=current_database()"
            ).fetchone()
            assert tuple(current.values()) == (name, oid)
        yield pool, config
    finally:
        if pool is not None:
            pool.close()
        with psycopg.connect(admin_dsn, autocommit=True) as admin:
            found = admin.execute(
                "SELECT oid FROM pg_database WHERE datname=%s", (name,)
            ).fetchone()
            assert oid is not None and found is not None and found[0] == oid
            deadline = time.monotonic() + 5
            while True:
                active = admin.execute(
                    "SELECT count(*) FROM pg_stat_activity WHERE datid=%s", (oid,)
                ).fetchone()[0]
                if active == 0 or time.monotonic() >= deadline:
                    break
                time.sleep(0.1)
            assert active == 0, "unknown connections remain; refusing to terminate sessions"
            admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
            assert (
                admin.execute("SELECT 1 FROM pg_database WHERE datname=%s", (name,)).fetchone()
                is None
            )
        _record("parity_database_cleaned", driver="postgresql", database=name, oid=oid)


@pytest.fixture(params=["postgresql", "opengauss"])
def shared_database(request, tmp_path):
    factory = isolated_postgresql if request.param == "postgresql" else isolated_database
    with factory(tmp_path) as database:
        _record("parity_database_created", driver=request.param, database=database[1].database)
        yield database


def _leases(database):
    from octop_memory.storage.backends.postgres_pool import _registry

    return {
        key[0]: entry.refs
        for key, entry in _registry.items()
        if dict(key[1]).get("dbname") == database
    }


def test_shared_executemany_and_rollback(shared_database):
    from octop.infra.db.repos.care_push import CarePushRepo

    pool, config = shared_database
    run_migrations(pool)
    repo = CarePushRepo(pool)
    episodes = ["中文'引号?", "emoji-🧪", "100%_literal"]
    repo.insert(agent_id="synthetic-a", session_key="session", episode_ids=episodes)
    repo.insert(agent_id="synthetic-b", session_key="session", episode_ids=["other"])
    repo.insert(agent_id="synthetic-a", session_key="empty", episode_ids=[])
    assert repo.list_pushed_episode_ids("synthetic-a") == set(episodes)
    assert repo.list_pushed_episode_ids("synthetic-b") == {"other"}
    with pool.transaction() as conn:
        conn.execute("CREATE TABLE public.parity_batch(id integer PRIMARY KEY, value text)")
    with pytest.raises(psycopg.IntegrityError), pool.transaction() as conn:
        conn.executemany(
            "INSERT INTO public.parity_batch VALUES (?, ?)", [(1, "first"), (1, "bad")]
        )
    with pool.connect() as conn:
        assert conn.execute("SELECT count(*) FROM public.parity_batch").fetchone()[0] == 0
    run_migrations(pool)
    assert repo.list_pushed_episode_ids("synthetic-a") == set(episodes)
    _record("parity_executemany", driver=config.driver, ok=True, rollback=True)


@pytest.mark.parametrize(
    "contract",
    [
        contracts.test_simple_upserts_and_concurrent_secret,
        contracts.test_trajectory_conflict_target,
        contracts.test_exact_sso_link_and_policy_updates,
        contracts.test_automation_observation_and_claim,
        contracts.test_concurrent_one_time_login_and_invite,
        contracts.test_threads_sessions_messages,
    ],
    ids=lambda contract: contract.__name__,
)
def test_shared_repo_conflict_contracts(shared_database, contract):
    contract(shared_database)
    _record(
        "parity_repo_contract",
        driver=shared_database[1].driver,
        contract=contract.__name__,
        ok=True,
    )


def test_shared_real_cache_checkpoint_and_vacuum(shared_database):
    from langgraph.checkpoint.base import empty_checkpoint
    from octop_memory.pipeline.lifecycle.vacuum import check_storage, nudge_vacuum

    from octop.api.common.memory_client import _MemoryCache
    from tests.integration.test_postgres_memory_storage import seed

    _pool, config = shared_database
    backend = "postgres" if config.driver == "postgresql" else "opengauss"
    cache = _MemoryCache(max_size=2)

    def opened(agent, fingerprint):
        return cache.get_or_open(
            agent,
            backend=backend,
            backend_config={"dsn": config.postgresql_conninfo()},
            fingerprint=fingerprint,
        )[0]

    try:
        first = opened("a", "v1")
        seed(first.backend)
        saved = first.put(
            {"configurable": {"thread_id": "parity-checkpoint", "checkpoint_ns": ""}},
            empty_checkpoint(),
            {},
            {},
        )
        before = _leases(config.database)
        assert sum(before.values()) == 2
        assert opened("a", "v1") is first
        replacement = opened("a", "v2")
        assert replacement is not first
        assert replacement.get_tuple(saved) is not None
        assert replacement.backend.get_candidate("referenced") is not None
        assert _leases(config.database) == before
        vacuum = nudge_vacuum(replacement)
        assert {row.table for row in vacuum.tables if row.action == "VACUUM"} == {
            "octop_memory.journal",
            "public.checkpoints",
            "public.checkpoint_blobs",
            "public.checkpoint_writes",
        }
        assert check_storage(replacement).backend == "postgres"
        opened("b", "v1")
        opened("c", "v1")
        assert "a" not in cache._entries
        assert sum(_leases(config.database).values()) == 2
        cache.invalidate("b")
        cache.invalidate("b")
        assert sum(_leases(config.database).values()) == 1
        cache.invalidate()
        cache.invalidate()
        assert _leases(config.database) == {}
        reopened = opened("a", "v3")
        assert reopened.get_tuple(saved) is not None
        assert reopened.backend.get_candidate("referenced") is not None
    finally:
        cache.invalidate()
    assert _leases(config.database) == {}
    _record("parity_memory_cache_vacuum", driver=config.driver, ok=True, pool_leases={})


@pytest.fixture
def scenario(shared_database, tmp_path, monkeypatch):
    pool, config = shared_database
    rows = json.loads(Path(os.environ["OCTOP_LIVE_PROVIDER_FILE"]).read_text(encoding="utf-8"))
    for key in tuple(os.environ):
        if key.startswith("OCTOP_DATABASE_"):
            monkeypatch.delenv(key)
    home = tmp_path / "app-home"
    home.mkdir()
    monkeypatch.setenv("OCTOP_HOME", str(home))
    monkeypatch.delenv("OCTOP_BASE_PATH", raising=False)
    write_octop_config(
        home,
        default_timezone="Asia/Shanghai",
        database=asdict(config),
    )
    home.joinpath("config.json").chmod(0o600)
    state = SimpleNamespace(
        home=home,
        pool=pool,
        database=config.database,
        db_config=config,
        rows=rows,
        ref=f"{os.environ['OCTOP_LIVE_PROVIDER_NAME']}/{os.environ['OCTOP_LIVE_MODEL']}",
        initialized=False,
        agents=[],
        providers={},
    )
    try:
        yield state
    finally:
        from octop.api.common.memory_client import invalidate_cached_memory

        for agent in state.agents:
            invalidate_cached_memory(agent)
        assert _leases(config.database) == {}
        _record("parity_scenario_shutdown", driver=config.driver, pool_leases={})


@pytest.mark.live
async def test_same_remote_embedding_rag_both_databases(scenario):
    await workflows.test_remote_embedding_rag_revision_and_restart(scenario)
    _record("parity_remote_rag", driver=scenario.db_config.driver, ok=True)
