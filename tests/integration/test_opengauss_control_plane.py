"""Real openGauss tests: create and drop ONLY databases owned by this fixture.

Opt in with OCTOP_TEST_OPENGAUSS=1 and OCTOP_DATABASE_HOST/PORT/USER/PASSWORD.
No existing database schema is reset. Management connection is only used for
CREATE DATABASE, catalog reads and DROP of the recorded database OID.
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from octop.config import DatabaseConfig, OctopConfig
from octop.infra.db.factory import open_database
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import OpenGaussPool
from octop.infra.db.repos.agents import AgentRepo
from octop.infra.db.repos.settings import SettingsRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.utils.paths import PathLayout

pytestmark = pytest.mark.skipif(
    os.environ.get("OCTOP_TEST_OPENGAUSS") != "1", reason="set OCTOP_TEST_OPENGAUSS=1 to opt in"
)


@contextmanager
def isolated_database(tmp_path):
    name = "octop_verify_" + uuid.uuid4().hex[:16]
    params = {
        "host": os.environ["OCTOP_DATABASE_HOST"],
        "port": os.environ["OCTOP_DATABASE_PORT"],
        "user": os.environ["OCTOP_DATABASE_USER"],
        "password": os.environ["OCTOP_DATABASE_PASSWORD"],
        "connect_timeout": 8,
        "application_name": name,
    }
    pool = None
    oid = None
    admin_dsn = make_conninfo(
        dbname=os.environ.get("OCTOP_TEST_OPENGAUSS_MANAGEMENT_DB", "plat"), **params
    )
    # Record intent before CREATE; openGauss can close the creating connection
    # after successful database creation, so read the assigned OID on a new one.
    record = tmp_path / "database.json"
    record.write_text(json.dumps({"database": name, "oid": None}))
    with psycopg.connect(admin_dsn, autocommit=True) as admin:
        admin.execute(
            sql.SQL("CREATE DATABASE {} TEMPLATE template0 DBCOMPATIBILITY 'PG'").format(
                sql.Identifier(name)
            )
        )
    with psycopg.connect(admin_dsn, autocommit=True) as admin:
        oid = admin.execute("SELECT oid FROM pg_database WHERE datname=%s", (name,)).fetchone()[0]
        record.write_text(json.dumps({"database": name, "oid": oid}))
    try:
        config = DatabaseConfig(
            driver="opengauss",
            host=params["host"],
            port=int(params["port"]),
            database=name,
            user=params["user"],
            password=params["password"],
            url=make_conninfo(
                dbname=name,
                options="-c statement_timeout=45000 -c lockwait_timeout=5000",
                **params,
            ),
        )
        pool = open_database(OctopConfig(database=config), PathLayout(tmp_path))
        assert isinstance(pool, OpenGaussPool)
        with pool.connect() as conn:
            current = conn.execute(
                "SELECT current_database(), oid FROM pg_database WHERE datname=current_database()"
            ).fetchone()
            assert tuple(current.values()) == (name, oid)
            conn.execute("SET statement_timeout = 45000")
            conn.execute("SET lockwait_timeout = 5000")
        yield pool, config
    finally:
        if pool is not None:
            pool.close()
        with psycopg.connect(
            make_conninfo(
                dbname=os.environ.get("OCTOP_TEST_OPENGAUSS_MANAGEMENT_DB", "plat"), **params
            ),
            autocommit=True,
        ) as admin:
            found = admin.execute(
                "SELECT oid FROM pg_database WHERE datname=%s", (name,)
            ).fetchone()
            assert oid is not None and found is not None and found[0] == oid, (
                "database ownership changed; refusing cleanup"
            )
            # The server may still be processing a pooled socket's close.
            deadline = time.monotonic() + 5
            while True:
                active = admin.execute(
                    "SELECT count(*) FROM pg_stat_activity WHERE datid=%s", (oid,)
                ).fetchone()[0]
                if active == 0 or time.monotonic() >= deadline:
                    break
                time.sleep(0.1)
            if active:
                sessions = admin.execute(
                    "SELECT application_name,state FROM pg_stat_activity WHERE datid=%s", (oid,)
                ).fetchall()
                raise AssertionError(
                    f"unknown connections {sessions!r}; refusing to terminate sessions"
                )
            admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
        with psycopg.connect(
            make_conninfo(
                dbname=os.environ.get("OCTOP_TEST_OPENGAUSS_MANAGEMENT_DB", "plat"), **params
            ),
            autocommit=True,
        ) as admin:
            assert (
                admin.execute("SELECT 1 FROM pg_database WHERE datname=%s", (name,)).fetchone()
                is None
            )
        print(f"cleaned {name} oid={oid}")


@pytest.fixture
def og(tmp_path):
    with isolated_database(tmp_path) as database:
        yield database


@pytest.fixture
def og_target(tmp_path):
    target = tmp_path / "restore-target"
    target.mkdir()
    with isolated_database(target) as database:
        yield database


def seed(pool):
    run_migrations(pool)
    uid = UserRepo(pool).create(username="tester", password_hash="x", role="admin")
    AgentRepo(pool).create(agent_id="agent", user_id=uid, name="bot")
    return uid


def test_baseline_and_repeated_init(og):
    pool, _ = og
    uid = seed(pool)
    SettingsRepo(pool).set("keep", "custom")
    run_migrations(pool)
    assert SettingsRepo(pool).get("keep") == "custom"
    assert UserRepo(pool).get(uid).username == "tester"
    assert UserRepo(pool).create(username="next", role="user") > uid
    with pool.connect() as conn:
        assert conn.execute("SELECT version FROM _schema_version").fetchone()[0] == 25
        assert (
            conn.execute(
                "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'"
            ).fetchone()[0]
            == 36
        )
        assert conn.execute("SELECT count(*) FROM user_role").fetchone()[0] == 2
        assert (
            conn.execute("SELECT value FROM settings WHERE key='active_tts_provider'").fetchone()[0]
            == "browser"
        )


def test_failed_upgrade_rolls_back(og, tmp_path, monkeypatch):
    from octop.infra.db.opengauss import migrate

    pool, _ = og
    seed(pool)
    for _version, migration in migrate.discover_migrations():
        (tmp_path / migration.name).write_text(migration.read_text(encoding="utf-8"))
    monkeypatch.setattr(migrate, "_MIGRATIONS", tmp_path)
    path = tmp_path / "026_test.sql"
    path.write_text(
        "CREATE TABLE public.upgrade_canary(id integer); INSERT INTO no_such_table VALUES (1);"
    )
    with pytest.raises(psycopg.Error):
        run_migrations(pool)
    with pool.connect() as conn:
        assert conn.execute("SELECT version FROM _schema_version").fetchone()[0] == 25
        assert not conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name='upgrade_canary'"
        ).fetchone()
    path.write_text(
        "CREATE TABLE public.upgrade_canary(id integer); INSERT INTO settings VALUES ('upgrade', 'retained');"
    )
    run_migrations(pool)
    run_migrations(pool)
    assert SettingsRepo(pool).get("upgrade") == "retained"
    assert UserRepo(pool).get_by_username("tester") is not None
    with pool.transaction() as conn:
        conn.execute("UPDATE _schema_version SET version=999")
    with pytest.raises(RuntimeError, match="Unsupported"):
        run_migrations(pool)


def test_rejects_foreign_or_incomplete_schema(og):
    pool, _ = og
    with pool.transaction() as conn:
        conn.execute("CREATE TABLE public._schema_version(version integer)")
        conn.execute("INSERT INTO public._schema_version VALUES (22)")
    with pytest.raises(RuntimeError, match="not initialized"):
        run_migrations(pool)
    with pool.transaction() as conn:
        conn.execute("DROP TABLE public._schema_version")
    run_migrations(pool)
    with pool.transaction() as conn:
        conn.execute("DROP TABLE public.audit_log")
    with pytest.raises(RuntimeError, match="missing tables.*audit_log"):
        run_migrations(pool)


def test_simple_upserts_and_concurrent_secret(og):
    from octop.infra.db.repos.proactive_care_config import (
        ProactiveCareConfig,
        ProactiveCareConfigRepo,
    )
    from octop.infra.db.repos.secrets import SecretRepo

    pool, _ = og
    seed(pool)
    settings = SettingsRepo(pool)
    settings.set("k", "one")
    settings.set("k", "two")
    assert settings.get("k") == "two"
    secrets = SecretRepo(pool)
    secrets.put("binary", b"\x00\xff")
    secrets.put("binary", b"\xff\x00")
    assert secrets.get("binary") == b"\xff\x00"
    with ThreadPoolExecutor(max_workers=4) as executor:
        values = list(
            executor.map(
                lambda _: secrets.get_or_create("shared", lambda: uuid.uuid4().bytes), range(8)
            )
        )
    assert len(set(values)) == 1
    care = ProactiveCareConfigRepo(pool)
    care.upsert(ProactiveCareConfig(agent_id="agent", enabled=True))
    care.upsert(ProactiveCareConfig(agent_id="agent", enabled=False))
    assert care.get("agent").enabled is False
    settings.delete("k")
    assert settings.get("k") is None


def test_trajectory_conflict_target(og):
    from octop.infra.db.repos.trajectory_events import TrajectoryEventRepo
    from octop.infra.history.trajectory.types import TrajectoryEvent

    pool, _ = og
    uid = seed(pool)
    from octop.infra.db.repos.threads import ThreadRepo

    ThreadRepo(pool).insert(
        thread_id="t", agent_id="agent", user_id=uid, channel_type="dashboard", session_key="s"
    )
    repo = TrajectoryEventRepo(pool)
    event = TrajectoryEvent(
        event_id="a",
        agent_id="agent",
        thread_id="t",
        seq=1,
        ts=1.0,
        kind="user",
        summary="original",
        payload={"binary": "data"},
        turn_id=None,
        request_seq=None,
        is_error=False,
    )
    assert repo.append(event)
    assert not repo.append(event)
    other = replace(event, event_id="b", summary="wrong")
    assert not repo.append(other)
    with pytest.raises(psycopg.IntegrityError):
        repo.upsert(other)
    assert repo.get("a").summary == "original"
    assert repo.upsert(replace(event, summary="updated", seq=2))
    assert repo.get("a").summary == "updated"
    assert repo.get("a").seq == 1
    repo.delete_for_thread("t")
    assert repo.get("a") is None


def test_exact_sso_link_and_policy_updates(og):
    from octop.infra.db.repos.user_policies import UserPolicyRepo

    pool, _ = og
    uid = seed(pool)
    other = UserRepo(pool).create(username="other", role="user")
    from octop.infra.db.repos.sso import SsoRepo

    provider = (
        SsoRepo(pool)
        .upsert_by_kind(
            "oidc",
            enabled=True,
            display_name="test",
            issuer="https://test.invalid",
            client_id="test",
            client_secret_enc=None,
            scopes="openid",
            dashboard_origin=None,
        )
        .id
    )
    users = UserRepo(pool)
    users.upsert_sso_identity(uid, provider_id=provider, subject="first")
    users.upsert_sso_identity(uid, provider_id=provider, subject="changed")
    with pytest.raises(psycopg.IntegrityError):
        users.upsert_sso_identity(other, provider_id=provider, subject="changed")
    assert users.get_by_sso(provider, "changed").id == uid
    assert users.get(other).sso_provider_id is None
    assert users.remove_sso_identity(uid, provider_id=provider)
    assert users.get(uid).sso_provider_id is None
    policies = UserPolicyRepo(pool)
    policies.set(uid, "p", "one")
    policies.set(uid, "p", "two")
    assert policies.enabled_values(uid) == {"p": "two"}
    policies.set(uid, "p", None)
    assert policies.enabled_values(uid) == {}


def test_automation_observation_and_claim(og):
    from octop.infra.db.repos.automation_analysis import AutomationAnalysisRepo
    from octop.infra.db.repos.cron import CronJobRepo

    pool, _ = og
    uid = seed(pool)
    CronJobRepo(pool).create(
        cron_id="cron",
        agent_id="agent",
        user_id=uid,
        trigger="0 9 * * *",
        prompt="ping",
        session_key="s",
    )
    repo = AutomationAnalysisRepo(pool)
    params = {
        "parent_cron_id": "cron",
        "agent_id": "agent",
        "user_id": uid,
        "source_key": "source",
        "source_item_key": "item",
        "business_status": "pending",
    }
    with ThreadPoolExecutor(max_workers=4) as executor:
        rows = list(
            executor.map(
                lambda n: repo.observe(**params, source_version=str(n), observed_at=n), range(8)
            )
        )
    assert len({row["item_id"] for row in rows}) == 1
    latest = repo.observe(**params, source_version="old", observed_at=0)
    assert latest["source_version"] == "7"
    with ThreadPoolExecutor(max_workers=4) as executor:
        claims = list(
            executor.map(
                lambda _: repo.reserve(latest["item_id"], "7", "analysis", "session"), range(8)
            )
        )
    assert sum(claims) == 1


def test_concurrent_one_time_login_and_invite(og):
    from octop.infra.db.repos.invites import InviteRepo
    from octop.infra.db.repos.sso import SsoRepo
    from octop.infra.db.repos.user_policies import UserPolicyRepo

    pool, _ = og
    uid = seed(pool)
    sso = SsoRepo(pool)

    def configure(_):
        return sso.upsert_by_kind(
            "oidc",
            enabled=True,
            display_name="test",
            issuer="https://test.invalid",
            client_id="test",
            client_secret_enc=None,
            scopes="openid",
            dashboard_origin=None,
        ).id

    with ThreadPoolExecutor(max_workers=4) as executor:
        providers = list(executor.map(configure, range(4)))
    assert len(set(providers)) == 1
    expires = int(time.time()) + 300
    sso.create_login_state(
        state="state",
        provider_id=providers[0],
        nonce="nonce",
        code_verifier="code",
        redirect_after="/",
        expires_at=expires,
    )
    with ThreadPoolExecutor(max_workers=4) as executor:
        claims = list(executor.map(lambda _: sso.take_login_state("state"), range(4)))
    assert sum(row is not None for row in claims) == 1
    sso.attach_login_code("state", "login", uid, expires)
    with ThreadPoolExecutor(max_workers=4) as executor:
        claims = list(executor.map(lambda _: sso.consume_login_code("login"), range(4)))
    assert [row for row in claims if row is not None] == [{"user_id": uid}]
    invites = InviteRepo(pool)
    invites.create(code="invite", created_by=uid, expires_at=expires)

    def redeem(n):
        try:
            return invites.redeem_creating_user(
                code="invite",
                username=f"invited{n}",
                password_hash="x",
                display_name=None,
                locale="zh",
                policies=[("p", "first"), ("p", "second")],
            )[0]
        except ValueError as exc:
            assert str(exc) == "used"
            return None

    with ThreadPoolExecutor(max_workers=4) as executor:
        users = list(executor.map(redeem, range(4)))
    winners = [user for user in users if user is not None]
    assert len(winners) == 1
    assert UserPolicyRepo(pool).enabled_values(winners[0]) == {"p": "second"}
    with pool.connect() as conn:
        assert (
            conn.execute("SELECT count(*) FROM users WHERE username LIKE 'invited%'").fetchone()[0]
            == 1
        )


def test_threads_sessions_messages(og):
    from octop.infra.db.repos.sessions import SessionRepo
    from octop.infra.db.repos.thread_messages import ThreadMessageInput, ThreadMessageRepo
    from octop.infra.db.repos.threads import ThreadRepo

    pool, _ = og
    uid = seed(pool)
    ThreadRepo(pool).insert(
        thread_id="t", agent_id="agent", user_id=uid, channel_type="dashboard", session_key="s"
    )
    sessions = SessionRepo(pool)
    params = {
        "session_key": "s",
        "agent_id": "agent",
        "user_id": uid,
        "channel_type": "dashboard",
        "chat_type": "dm",
        "thread_id": "t",
    }
    sessions.upsert(**params, channel_id="channel", channel_metadata={"v": 1})
    sessions.upsert(**params, channel_metadata={"v": 2})
    assert sessions.get("s").channel_id == "channel"
    assert sessions.get("s").channel_metadata["v"] == 2
    messages = ThreadMessageRepo(pool)
    messages.mark_projection("t", "pending")
    messages.mark_projection("t", "ready")
    assert messages.projection_status("t") == "ready"
    batch = [
        ThreadMessageInput(
            message_id="m", role="user", message_json='{"text":"你好"}', created_at=1
        )
    ]
    assert messages.append_if_ready("t", batch) == 1
    assert messages.append_if_ready("t", batch) == 0


def test_schema_matches_postgresql_baseline(og):
    dsn = os.environ.get("OCTOP_TEST_BASELINE_POSTGRES_URL")
    if not dsn:
        pytest.skip("set OCTOP_TEST_BASELINE_POSTGRES_URL to a freshly migrated reference DB")
    pool, _ = og
    run_migrations(pool)
    columns = "SELECT table_name,column_name,data_type,is_nullable FROM information_schema.columns WHERE table_schema='public'"
    with psycopg.connect(dsn) as reference, pool.connect() as actual:
        expected = sorted(reference.execute(columns).fetchall())
        found = sorted(tuple(row.values()) for row in actual.execute(columns).fetchall())
        assert found == expected
        for table, column, *_ in expected:
            if column != "id":
                continue
            # This also verifies integer vs bigint widths for every surrogate key.
            assert actual.execute(
                "SELECT data_type FROM information_schema.columns WHERE table_schema='public' AND table_name=? AND column_name=?",
                (table, column),
            ).fetchone()[0] == next(row[2] for row in expected if row[:2] == (table, column))
        # PostgreSQL 18 also catalogs NOT NULL as constraints; openGauss does not.
        # Column nullability is compared above via information_schema.columns.
        constraints = "SELECT t.relname,c.conname,c.contype,c.conkey,f.relname AS foreign_table,c.confkey,c.confupdtype,c.confdeltype,c.condeferrable,c.condeferred FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid JOIN pg_namespace n ON n.oid=t.relnamespace LEFT JOIN pg_class f ON f.oid=c.confrelid WHERE n.nspname='public' AND c.contype <> 'n'"
        assert sorted(
            tuple(row.values()) for row in actual.execute(constraints).fetchall()
        ) == sorted(reference.execute(constraints).fetchall())
        indexes = "SELECT tablename,indexname,indexdef FROM pg_indexes WHERE schemaname='public'"

        def normalized_indexes(rows):
            return sorted(
                (
                    table,
                    name,
                    re.sub(r" TABLESPACE pg_default", "", definition.replace("ON public.", "ON ")),
                )
                for table, name, definition in rows
            )

        assert normalized_indexes(
            tuple(row.values()) for row in actual.execute(indexes).fetchall()
        ) == normalized_indexes(reference.execute(indexes).fetchall())
        defaults = "SELECT table_name,column_name,column_default FROM information_schema.columns WHERE table_schema='public' AND column_name <> 'id'"
        assert sorted(tuple(row.values()) for row in actual.execute(defaults).fetchall()) == sorted(
            reference.execute(defaults).fetchall()
        )
        for table, column, datatype, *_ in expected:
            if column != "id":
                continue
            name = actual.execute(
                "SELECT pg_get_serial_sequence(?, ?)", (f"public.{table}", column)
            ).fetchone()[0]
            expected_sequence = reference.execute(
                "SELECT pg_get_serial_sequence(%s,%s)", (f"public.{table}", column)
            ).fetchone()[0]
            if expected_sequence is None:
                assert name is None, table
                continue
            assert name is not None, table
            with actual.cursor() as cursor:
                bounds = cursor.execute(
                    sql.SQL("SELECT min_value,max_value,increment_by FROM {}").format(
                        sql.Identifier(*name.split("."))
                    )
                ).fetchone()
            assert tuple(bounds.values()) == (
                1,
                2147483647 if datatype == "integer" else 9223372036854775807,
                1,
            )
        for query in (
            "SELECT key,value FROM settings",
            "SELECT user_role_id,user_role_name,permissions,policies FROM user_role",
        ):
            actual_rows = [tuple(row.values()) for row in actual.execute(query).fetchall()]
            reference_rows = reference.execute(query).fetchall()
            if "FROM user_role" in query:
                # Permissions are sets; upgrades preserve existing JSON member order.
                for rows in (actual_rows, reference_rows):
                    for index, row in enumerate(rows):
                        rows[index] = (*row[:2], json.dumps(sorted(json.loads(row[2]))), row[3])
            assert sorted(actual_rows) == sorted(reference_rows)


def test_memory_backend_crud_and_namespace_isolation(og):
    from datetime import UTC, datetime

    from octop_memory import Memory
    from octop_memory.types import (
        ActiveEntity,
        Alias,
        AtomCard,
        DigestRecord,
        EntityPage,
        MemoryNode,
    )

    pool, config = og
    memories = [
        Memory(
            namespace=n, backend="opengauss", backend_config={"dsn": config.postgresql_conninfo()}
        )
        for n in ("agent_one", "agent_two")
    ]
    try:
        first, second = [m.backend for m in memories]
        now = datetime.now(UTC)
        first.save_node(
            MemoryNode(
                id="root",
                level="root",
                content="alpha searchable 中文",
                parent_id=None,
                topic=None,
                conversation_id=None,
                created_at=now,
                updated_at=now,
            )
        )
        assert first.get_node("root").content == "alpha searchable 中文"
        assert second.get_node("root") is None
        first.save_atom(
            AtomCard(
                id="atom",
                entity_id="e",
                candidate_id="c",
                raw_event_ids=["raw"],
                assertion="alpha searchable 中文",
                verbatim_quote="中文",
                quote_event_id="raw",
                search_terms=["searchable", "中文"],
                occurred_at=now,
                confidence="high",
                importance="high",
                created_at=now,
            )
        )
        first.save_node(
            MemoryNode(
                id="leaf",
                parent_id="root",
                level="leaf",
                content="",
                topic=None,
                conversation_id=None,
                created_at=now,
                updated_at=now,
                atom_id="atom",
            )
        )
        assert first.search_memories("searchable")
        assert not second.search_memories("searchable")
        alias = Alias(
            alias="name", entity_id="e", entity_type="person", created_by="test", created_at=now
        )
        first.save_alias(alias)
        first.save_alias(alias)
        first.upsert_active_entity(ActiveEntity(thread_id="t", entity_id="e", last_seen_at=now))
        first.upsert_active_entity(ActiveEntity(thread_id="t", entity_id="e", last_seen_at=now))
        first.set_meta("sample", "old")
        first.set_meta("sample", "new")
        assert first.get_meta("sample") == "new"
        page = EntityPage(
            id="p",
            entity_id="e",
            summary_markdown="first",
            headline="",
            topics=[],
            dirty=True,
            regen_attempt_count=0,
            summary_version=0,
            created_at=now,
            updated_at=now,
        )
        first.upsert_entity_page(page)
        first.upsert_entity_page(replace(page, id="ignored", summary_markdown="second"))
        assert first.get_entity_page("e").id == "p"
        assert first.get_entity_page("e").summary_markdown == "second"
        with pytest.raises(psycopg.errors.UniqueViolation):
            first.upsert_entity_page(replace(page, entity_id="other"))
        first.mark_entity_page_dirty("e", when=now)
        first.mark_entity_page_dirty("new", when=now)
        assert first.get_entity_page("e").dirty
        assert first.get_entity_page("new").dirty
        with pytest.raises(RuntimeError), first.transaction():
            first.set_meta("rollback", "bad")
            raise RuntimeError("rollback")
        assert first.get_meta("rollback") is None
        digest = DigestRecord(
            id="digest",
            period_kind="daily",
            period_key="2026-10-22",
            period_start=now,
            period_end=now,
            markdown="first",
            episode_ids=[],
            llm_version="test",
            created_at=now,
            updated_at=now,
        )
        first.upsert_digest(digest)
        first.upsert_digest(replace(digest, id="ignored", markdown="second"))
        assert first.get_digest("daily", "2026-10-22").markdown == "second"
        with pytest.raises(psycopg.errors.UniqueViolation):
            first.upsert_digest(replace(digest, period_key="2026-10-23"))
    finally:
        for memory in memories:
            memory.backend.close()
            if memory._checkpointer_pool is not None:
                memory._checkpointer_pool.close()


def test_checkpoint_graph_interrupt_and_reopen(og):
    from typing import TypedDict

    from langgraph.checkpoint.base import empty_checkpoint
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import Command, interrupt
    from octop_memory import Memory

    from octop.infra.agents.memory.thread_cleanup import close_memory

    _, config = og

    class State(TypedDict):
        value: int
        approved: bool

    def approve(state):
        accepted = interrupt({"question": "approve", "value": state["value"]})
        return {"value": state["value"] + 1, "approved": bool(accepted)}

    builder = StateGraph(State)
    builder.add_node("approve", approve)
    builder.add_edge(START, "approve")
    builder.add_edge("approve", END)

    def open_memory():
        return Memory(
            namespace="agent",
            backend="opengauss",
            backend_config={
                "dsn": config.postgresql_conninfo(),
            },
        )

    memory = open_memory()
    graph_config = {"configurable": {"thread_id": "graph"}}
    try:
        graph = builder.compile(checkpointer=memory)
        result = graph.invoke({"value": 1, "approved": False}, graph_config)
        assert result["__interrupt__"]
        snapshot = graph.get_state(graph_config)
        assert snapshot.next == ("approve",)
        assert memory.get_tuple(graph_config).pending_writes
    finally:
        close_memory(memory)

    memory = open_memory()
    try:
        graph = builder.compile(checkpointer=memory)
        assert graph.invoke(Command(resume=True), graph_config) == {"value": 2, "approved": True}
        history = list(graph.get_state_history(graph_config))
        assert len(history) >= 3
        checkpoint = empty_checkpoint()
        checkpoint["channel_values"] = {"binary": b"\x00\xff\n", "json": ["中文", {"ok": True}]}
        checkpoint["channel_versions"] = {"binary": "1", "json": "1"}
        saved = memory.put(
            {"configurable": {"thread_id": "binary", "checkpoint_ns": ""}},
            checkpoint,
            {"source": "input", "step": 0},
            checkpoint["channel_versions"],
        )
        memory.put_writes(saved, [("result", b"\x00\xff"), ("other", {"a": 1})], "task", "path")
        loaded = memory.get_tuple(saved)
        assert loaded.checkpoint["channel_values"] == checkpoint["channel_values"]
        assert loaded.pending_writes == [
            ("task", "result", b"\x00\xff"),
            ("task", "other", {"a": 1}),
        ]
        assert len(list(memory.list(saved, filter={"source": "input"}))) == 1
        memory._checkpointer.setup()
        memory.delete_thread("binary")
        assert memory.get_tuple(saved) is None
    finally:
        close_memory(memory)


def test_checkpoint_legacy_sends_and_delta_history(og):
    from langgraph.checkpoint.base import empty_checkpoint
    from langgraph.checkpoint.serde.types import _DeltaSnapshot
    from octop_memory import Memory

    from octop.infra.agents.memory.thread_cleanup import close_memory

    _, config = og
    memory = Memory(
        namespace="history",
        backend="opengauss",
        backend_config={"dsn": config.postgresql_conninfo()},
    )
    try:
        memory.get_tuple({"configurable": {"thread_id": "initialize"}})
        saver = memory._checkpointer
        parent = empty_checkpoint()
        parent["v"] = 3
        saved = saver.put(
            {"configurable": {"thread_id": "legacy", "checkpoint_ns": ""}},
            parent,
            {},
            {},
        )
        saver.put_writes(saved, [("__pregel_tasks", {"payload": b"\x00\xff"})], "send")
        child = empty_checkpoint()
        child["v"] = 3
        child_saved = saver.put(saved, child, {}, {})
        assert saver.get_tuple(child_saved).checkpoint["channel_values"]["__pregel_tasks"] == [
            {"payload": b"\x00\xff"}
        ]
        assert list(saver.list(child_saved))[0].checkpoint["channel_values"]["__pregel_tasks"]
        snapshot = empty_checkpoint()
        snapshot["channel_values"] = {"delta": _DeltaSnapshot(["initial"])}
        snapshot["channel_versions"] = {"delta": "1"}
        saved = saver.put(
            {"configurable": {"thread_id": "delta", "checkpoint_ns": ""}},
            snapshot,
            {},
            snapshot["channel_versions"],
        )
        saver.put_writes(saved, [("delta", ["next"])], "delta-task")
        child = empty_checkpoint()
        child["channel_versions"] = {"delta": "1"}
        saved = saver.put(saved, child, {}, {})
        history = saver.get_delta_channel_history(config=saved, channels=["delta", "missing"])
        assert history["delta"]["seed"] == _DeltaSnapshot(["initial"])
        assert ("delta-task", "delta", ["next"]) in history["delta"]["writes"]
        assert history["missing"] == {"writes": []}
    finally:
        close_memory(memory)


def test_native_dump_restore_empty_and_populated(og, og_target, tmp_path):
    import shutil

    from langgraph.checkpoint.base import empty_checkpoint
    from octop_memory import Memory

    from octop.infra.agents.memory.thread_cleanup import close_memory
    from octop.infra.backup.gs_dump import dump_opengauss, restore_opengauss

    if not shutil.which("gs_dump") or not shutil.which("gs_restore"):
        pytest.skip("matching native openGauss clients required")
    source, source_config = og
    target, target_config = og_target
    seed(source)
    SettingsRepo(source).set("restore", "中文 preserved")
    memory = Memory(
        namespace="backup",
        backend="opengauss",
        backend_config={
            "dsn": source_config.postgresql_conninfo(),
        },
    )
    try:
        memory.backend.set_meta("backup", "memory retained")
        checkpoint = empty_checkpoint()
        checkpoint["channel_values"] = {"blob": b"\x00\xff"}
        checkpoint["channel_versions"] = {"blob": "1"}
        memory.put(
            {"configurable": {"thread_id": "backup", "checkpoint_ns": ""}},
            checkpoint,
            {},
            checkpoint["channel_versions"],
        )
    finally:
        close_memory(memory)
    with source.transaction() as conn:
        conn.execute(
            "CREATE FUNCTION public.validation_add(integer) RETURNS integer "
            "LANGUAGE SQL IMMUTABLE AS 'SELECT $1 + 1'"
        )
        conn.execute("CREATE INDEX validation_user_name ON users(lower(username))")
    archive = tmp_path / "native.dump"
    dump_opengauss(source_config.postgresql_conninfo(), archive)
    assert archive.stat().st_size > 1000
    for populated in (False, True):
        if populated:
            SettingsRepo(target).set("restore", "changed")
        restore_opengauss(target_config.postgresql_conninfo(), archive)
        run_migrations(target)
        assert SettingsRepo(target).get("restore") == "中文 preserved"
        assert UserRepo(target).get_by_username("tester") is not None
        with target.connect() as conn:
            assert conn.execute("SELECT validation_add(41)").fetchone()[0] == 42
            assert conn.execute(
                "SELECT 1 FROM pg_indexes WHERE indexname='validation_user_name'"
            ).fetchone()
        reopened = Memory(
            namespace="backup",
            backend="opengauss",
            backend_config={
                "dsn": target_config.postgresql_conninfo(),
            },
        )
        try:
            assert reopened.backend.get_meta("backup") == "memory retained"
            assert (
                reopened.get_tuple({"configurable": {"thread_id": "backup"}}).checkpoint[
                    "channel_values"
                ]["blob"]
                == b"\x00\xff"
            )
        finally:
            close_memory(reopened)


def test_memory_maintenance_and_agent_cleanup(og, tmp_path):
    from langgraph.checkpoint.base import empty_checkpoint
    from octop_memory import Memory
    from octop_memory.pipeline.lifecycle.vacuum import nudge_vacuum

    from octop.infra.agents.memory import postgres_storage as storage
    from octop.infra.agents.memory.backend import open_memory_kwargs
    from octop.infra.agents.memory.thread_cleanup import (
        close_memory,
        delete_agent_memory_and_checkpoints,
    )
    from tests.integration.test_postgres_memory_storage import seed as seed_memory

    pool, config = og
    seed(pool)
    octop_config = OctopConfig(database=config)
    namespace, backend, options = open_memory_kwargs(
        agent_id="agent", cfg={}, octop_config=octop_config, workspace_dir=tmp_path
    )
    first = Memory(namespace=namespace, backend=backend, backend_config=options)
    second = Memory(namespace="other_agent", backend=backend, backend_config=options)
    try:
        seed_memory(first.backend)
        seed_memory(second.backend)
        before = storage.inspect_postgres_storage(first.backend)
        assert before["backend"] == "opengauss"
        assert before["cleanup_preview"]["total"] == 2
        backup = tmp_path / "cleanup.jsonl"
        result = storage.maintain_postgres_storage(
            first.backend, backup=backup, progress=lambda _: None
        )
        assert result["candidates_deleted"] == result["journal_deleted"] == 1
        lines = [json.loads(line) for line in backup.read_text().splitlines()]
        assert lines[0]["namespace"] == namespace and lines[0]["rows"] == 2
        assert all(line["row"]["namespace"] == namespace for line in lines[1:])
        assert storage.inspect_postgres_storage(second.backend)["cleanup_preview"]["total"] == 2
        checkpoint = empty_checkpoint()
        saved = first.put(
            {"configurable": {"thread_id": "cleanup", "checkpoint_ns": ""}},
            checkpoint,
            {},
            {},
        )
        vacuum = nudge_vacuum(first)
        assert any(table.action == "VACUUM" for table in vacuum.tables)
        assert first.get_tuple(saved) is not None
        assert second.backend.get_candidate("referenced") is not None
        delete_agent_memory_and_checkpoints(
            agent_id="agent",
            thread_ids={"cleanup"},
            cfg={},
            octop_config=octop_config,
            workspace_dir=tmp_path,
        )
        assert first.get_tuple(saved) is None
        assert first.backend.get_candidate("referenced") is None
        assert second.backend.get_candidate("referenced") is not None
    finally:
        close_memory(first)
        close_memory(second)


@pytest.mark.parametrize("include_chats", [False, True])
def test_system_archive_roundtrip(og, tmp_path, monkeypatch, include_chats):
    import shutil
    import tarfile
    import tempfile

    from octop.infra.backup.system_archive import create_system_backup, restore_system_backup
    from octop.infra.db.repos.secrets import SecretRepo
    from octop.infra.db.repos.threads import ThreadRepo

    if not shutil.which("gs_dump") or not shutil.which("gs_restore"):
        pytest.skip("matching native openGauss clients required")
    # Local validation clients run in Docker, which can access this test tree.
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    pool, config = og
    uid = seed(pool)
    paths = PathLayout(tmp_path / "home")
    paths.root.mkdir()
    paths.config.write_text('{"port":18088}')
    threads = ThreadRepo(pool)
    threads.insert(
        thread_id="archived",
        agent_id="agent",
        user_id=uid,
        channel_type="dashboard",
        session_key="archived",
    )
    SettingsRepo(pool).set("archive", "original 中文")
    archive = tmp_path / "system.tar.gz"
    create_system_backup(
        paths=paths,
        agent_rows=AgentRepo(pool).list_all(),
        pool=pool,
        db_config=config,
        dest=archive,
        include_chats=include_chats,
    )
    with tarfile.open(archive) as tar:
        manifest = json.load(tar.extractfile("manifest.json"))
    assert manifest["database_driver"] == "opengauss"
    assert manifest["database_dump_format"] == "og_custom"
    live_uid = UserRepo(pool).create(username="live", password_hash="live", role="admin")
    jwt = SecretRepo(pool).get_or_create("jwt", lambda: b"preserve-jwt")
    threads.insert(
        thread_id="live",
        agent_id="agent",
        user_id=uid,
        channel_type="dashboard",
        session_key="live",
    )
    SettingsRepo(pool).set("archive", "changed")
    result = restore_system_backup(
        archive,
        paths=paths,
        pool=pool,
        db_config=config,
        restore_config=False,
        preserve_users=True,
    )
    assert result["database_driver"] == "opengauss"
    assert result["chats_restored"] == include_chats
    assert SettingsRepo(pool).get("archive") == "original 中文"
    assert UserRepo(pool).get(live_uid).username == "live"
    assert SecretRepo(pool).get("jwt") == jwt
    assert threads.get("archived") is not None
    assert (threads.get("live") is None) == include_chats
    assert UserRepo(pool).create(username="after", role="user") > live_uid
    threads.insert(
        thread_id="after",
        agent_id="agent",
        user_id=uid,
        channel_type="dashboard",
        session_key="after",
    )


async def test_http_setup_chat_and_restart(og, tmp_path, monkeypatch):
    from octop.infra.agents.memory.backend import open_memory_kwargs
    from octop.infra.agents.memory.thread_cleanup import close_memory
    from tests.support.app import octop_client, write_octop_config
    from tests.support.auth import auth_header, bootstrap_admin, create_agent, seed_openai_provider
    from tests.support.fakes import FakeHarnessAgent
    from tests.support.http import chat_ws_path, ws_connect

    pool, config = og
    home = tmp_path / "app-home"
    home.mkdir()
    for key in tuple(os.environ):
        if key.startswith("OCTOP_DATABASE_"):
            monkeypatch.delenv(key)
    write_octop_config(
        home,
        enable_api_docs=True,
        database={
            "driver": "opengauss",
            "host": config.host,
            "port": config.port,
            "database": config.database,
            "user": config.user,
            "password": config.password,
        },
    )
    fake = FakeHarnessAgent(chunks=[{"type": "token", "node": "agent", "content": "hello 中文"}])
    async with octop_client(home, fake_agent=fake) as (client, server):
        assert server.services.db.dialect == "opengauss"
        await bootstrap_admin(client, home)
        auth = await auth_header(client)
        await seed_openai_provider(client, auth)
        agent_id = await create_agent(client, auth)
        response = await client.post(f"/api/agents/{agent_id}/threads", headers=auth)
        response.raise_for_status()
        thread_id = response.json()["thread_id"]
        async with ws_connect(client._octop_app, chat_ws_path(agent_id, auth)) as ws:
            await ws.send_json({"type": "user_turn", "text": "hello", "thread_id": thread_id})
            frames = await ws.drain_turn()
        assert any(f.get("content") == "hello 中文" for f in frames)
        assert frames[-1]["type"] == "done"
        assert (
            await client.get(f"/api/agents/{agent_id}/threads", headers=auth)
        ).status_code == 200
        namespace, backend, options = open_memory_kwargs(
            agent_id=agent_id,
            cfg={},
            octop_config=server.config,
            workspace_dir=home,
        )
        from octop_memory import Memory

        memory = Memory(namespace=namespace, backend=backend, backend_config=options)
        try:
            memory.backend.set_meta("http-smoke", "retained")
        finally:
            close_memory(memory)
    assert UserRepo(pool).get_by_username("admin") is not None
    async with octop_client(home, fake_agent=fake) as (client, _server):
        auth = await auth_header(client)
        response = await client.get(f"/api/agents/{agent_id}/threads", headers=auth)
        response.raise_for_status()
        assert thread_id in str(response.json())
        assert (await client.get("/api/openapi.json", headers=auth)).status_code == 200
