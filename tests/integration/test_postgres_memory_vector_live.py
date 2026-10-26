"""Local PG SDK diagnostics, not Octop/Harness vector integration acceptance.

Opt in with OCTOP_TEST_PG_VECTORS=1 and a loopback OCTOP_TEST_PG_ADMIN_DSN.
All DDL/DML runs in OID-recorded disposable databases. The vector adapter
and embeddings below are test fixtures, not shipped implementations.
"""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from io import BytesIO
from urllib.error import HTTPError

import psycopg
import pytest
from octop_memory import Memory
from octop_memory.pipeline.recall.multi_source import _gather_vector_atoms
from octop_memory.pipeline.recall.parser import ParsedQuery
from octop_memory.storage.vector.embeddings import OpenAIEmbeddingProvider
from psycopg.types.json import Jsonb

from octop.infra.agents.memory.thread_cleanup import close_memory
from tests.integration.test_database_shared_live import _leases, isolated_postgresql

pytestmark = pytest.mark.skipif(
    os.environ.get("OCTOP_TEST_PG_VECTORS") != "1",
    reason="requires explicit disposable local PG vector test configuration",
)

TEXT_A = "海岚项目报销由林清审批，最高金额为927元。"
TEXT_B = "海岚项目报销由周宁审批，最高金额为1500元。"


class ControlledEmbedding:
    """Deterministic geometry only; makes no claim about semantic quality."""

    vector_size = 3

    def embed(self, text):
        return [0.0, 1.0, 0.0] if text == TEXT_B else [1.0, 0.0, 0.0]


class PgTestIndex:
    """Minimal protocol fixture: namespace-scoped PG SQL, no persistent pool."""

    def __init__(self, dsn, namespace):
        self.dsn = dsn
        self.namespace = namespace

    def ensure_collection(self, vector_size):
        with psycopg.connect(self.dsn) as conn:
            actual = conn.execute(
                "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
                "WHERE attrelid='public.test_memory_vectors'::regclass "
                "AND attname='embedding'"
            ).fetchone()[0]
        if actual != f"vector({vector_size})":
            raise ValueError("vector dimension change requires rebuilding the test collection")

    def upsert(self, atom_id, embedding, payload):
        with psycopg.connect(self.dsn) as conn:
            conn.execute(
                "INSERT INTO public.test_memory_vectors VALUES (%s,%s,%s::vector,%s) "
                "ON CONFLICT (namespace,atom_id) DO UPDATE "
                "SET embedding=excluded.embedding,payload=excluded.payload",
                (self.namespace, atom_id, json.dumps(embedding), Jsonb(payload)),
            )

    def search(self, embedding, *, limit=10, where=None):
        with psycopg.connect(self.dsn) as conn:
            return [
                row[0]
                for row in conn.execute(
                    "SELECT atom_id FROM public.test_memory_vectors "
                    "WHERE namespace=%s AND payload @> %s "
                    "ORDER BY embedding <=> %s::vector,atom_id LIMIT %s",
                    (self.namespace, Jsonb(where or {}), json.dumps(embedding), limit),
                )
            ]

    def delete(self, atom_id):
        with psycopg.connect(self.dsn) as conn:
            conn.execute(
                "DELETE FROM public.test_memory_vectors WHERE namespace=%s AND atom_id=%s",
                (self.namespace, atom_id),
            )


@pytest.fixture
def pg_vectors(tmp_path):
    with isolated_postgresql(tmp_path) as (pool, config):
        dsn = config.postgresql_conninfo()
        with pool.transaction() as conn:
            conn.execute("CREATE EXTENSION vector")
            conn.execute(
                "CREATE TABLE public.test_memory_vectors ("
                "namespace text NOT NULL,atom_id text NOT NULL,embedding vector(3) NOT NULL,"
                "payload jsonb NOT NULL,PRIMARY KEY(namespace,atom_id))"
            )
        with ExitStack() as stack:

            def opened(namespace):
                index = PgTestIndex(dsn, namespace)
                index.ensure_collection(3)
                memory = Memory(
                    namespace,
                    backend="postgres",
                    backend_config={"dsn": dsn},
                    vector_index=index,
                    embedding_provider=ControlledEmbedding(),
                )
                stack.callback(close_memory, memory)
                return memory

            yield opened, dsn
        assert not _leases(config.database), "SDK connection leases leaked"


def test_create_replace_and_duplicate_contract(pg_vectors):
    opened, _ = pg_vectors
    memory = opened("lifecycle")
    atom, entity, _ = memory.create_atom(TEXT_A, entity_name="海岚报销")
    index = memory.vector_index
    assert index.search([1.0, 0.0, 0.0]) == [atom.id]
    assert index.search([1.0, 0.0, 0.0], where={"entity_id": entity.id}) == [atom.id]
    assert index.search([1.0, 0.0, 0.0], where={"entity_id": "other"}) == []
    with pytest.raises(ValueError, match="duplicate"):
        memory.create_atom(TEXT_A, entity_id=entity.id)
    assert len(memory.list_atoms()) == 1
    assert memory.replace_atom(atom.id, assertion=TEXT_A).id == atom.id
    successor = memory.replace_atom(atom.id, assertion=TEXT_B)
    assert successor is not None and successor.id != atom.id
    assert memory.get_atom(atom.id).superseded_by == successor.id
    assert memory.get_atom(atom.id).deprecated_at is not None
    assert [row.id for row in memory.list_atoms()] == [successor.id]
    assert memory.get_entity(entity.id).atom_count == 1
    assert index.search([0.0, 1.0, 0.0]) == [successor.id]
    assert memory.replace_atom(atom.id, assertion="不应生效") is None


def test_namespace_and_colliding_index_ids(pg_vectors):
    opened, _ = pg_vectors
    first, second = opened("agent-a"), opened("agent-b")
    a = first.store(TEXT_A)
    b = second.store(TEXT_B)
    assert first.get_atom(b.atom_id) is second.get_atom(a.atom_id) is None
    assert first.vector_index.search([1.0, 0.0, 0.0]) == [a.atom_id]
    assert second.vector_index.search([0.0, 1.0, 0.0]) == [b.atom_id]
    # Deliberately collide IDs across namespaces, including a quoted key.
    key = "same'中文?"
    first.vector_index.upsert(key, [1.0, 0.0, 0.0], {})
    second.vector_index.upsert(key, [0.0, 1.0, 0.0], {})
    first.vector_index.delete(key)
    assert key not in first.vector_index.search([1.0, 0.0, 0.0])
    assert key in second.vector_index.search([0.0, 1.0, 0.0])


@pytest.mark.parametrize("failure", ["timeout", "invalid_dimension", "closed_connection"])
def test_vector_failure_preserves_relational_write_and_can_reindex(
    pg_vectors, monkeypatch, caplog, failure
):
    opened, dsn = pg_vectors
    memory = opened(f"fault-{failure}")
    with monkeypatch.context() as patch:
        if failure == "timeout":

            def failed_embed(_):
                raise TimeoutError("controlled remote timeout")

            patch.setattr(memory.embedding_provider, "embed", failed_embed)
        elif failure == "invalid_dimension":
            patch.setattr(memory.embedding_provider, "embed", lambda _: [1.0, 0.0])
        else:
            with psycopg.connect(dsn) as closed:
                pass

            def closed_upsert(*_):
                closed.execute("SELECT 1")

            patch.setattr(memory.vector_index, "upsert", closed_upsert)
        node = memory.store(TEXT_A)
    atom = memory.get_atom(node.atom_id)
    assert atom is not None and atom.assertion == TEXT_A
    assert memory.get(node.id) is not None
    assert memory.vector_index.search([1.0, 0.0, 0.0]) == []
    assert any("failed" in record.message for record in caplog.records)
    # Explicit diagnostic replay, not an implemented Octop recovery feature.
    memory._index_atom_vector(atom)
    memory._index_atom_vector(atom)
    assert memory.vector_index.search([1.0, 0.0, 0.0]) == [atom.id]
    assert len(memory.list_atoms()) == 1


def test_dimension_change_is_rejected_without_data_loss(pg_vectors):
    opened, _ = pg_vectors
    memory = opened("dimension")
    node = memory.store(TEXT_A)
    memory.vector_index.ensure_collection(3)
    with pytest.raises(ValueError, match="rebuilding"):
        memory.vector_index.ensure_collection(1024)
    assert memory.get(node.id).content == TEXT_A
    assert memory.vector_index.search([1.0, 0.0, 0.0]) == [node.atom_id]


def test_reopen_memory_and_checkpoint(pg_vectors):
    from langgraph.checkpoint.base import empty_checkpoint

    opened, _ = pg_vectors
    memory = opened("reopen")
    node = memory.store(TEXT_A)
    saved = memory.put(
        {"configurable": {"thread_id": "pg-vector", "checkpoint_ns": ""}},
        empty_checkpoint(),
        {},
        {},
    )
    close_memory(memory)
    replacement = opened("reopen")
    assert replacement.get(node.id).content == TEXT_A
    assert replacement.vector_index.search([1.0, 0.0, 0.0]) == [node.atom_id]
    assert replacement.get_tuple(saved) is not None


def test_controlled_parallel_namespaces(pg_vectors):
    opened, _ = pg_vectors
    memories = [opened(f"worker-{i}") for i in range(4)]

    def work(memory):
        ids = {memory.store(f"{TEXT_A} 记录 {i}").atom_id for i in range(12)}
        assert set(memory.vector_index.search([1.0, 0.0, 0.0], limit=100)) == ids
        assert {row.id for row in memory.list_atoms()} == ids
        return ids

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(work, memories))
    assert len(set.union(*results)) == 48


@pytest.mark.parametrize("action", ["deprecate", "delete"])
@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="MEM-VEC-01: SDK soft deletion leaves an index hit and vector source returns it",
)
def test_deprecated_atoms_must_not_reach_vector_source(pg_vectors, action):
    opened, _ = pg_vectors
    memory = opened(f"deleted-{action}")
    node = memory.store(TEXT_A)
    if action == "deprecate":
        assert memory.deprecate_atom(node.atom_id)
        assert not memory.deprecate_atom(node.atom_id)
    else:
        assert memory.delete(node.id)
        assert memory.get(node.id) is None
        assert not memory.delete(node.id)
    assert memory.get_atom(node.atom_id).deprecated_at is not None
    assert memory.list_atoms() == []
    atoms = _gather_vector_atoms(memory, ParsedQuery(text=TEXT_A), limit=10)
    assert atoms == [], "deprecated atom returned by SDK vector recall source"


def test_relational_transaction_failure_does_not_write_vectors(pg_vectors, monkeypatch):
    opened, dsn = pg_vectors
    memory = opened("relational-rollback")

    def counts():
        from psycopg import sql

        with psycopg.connect(dsn) as conn:
            return {
                table: conn.execute(
                    sql.SQL("SELECT count(*) FROM octop_memory.{} WHERE namespace=%s").format(
                        sql.Identifier(table)
                    ),
                    ("relational-rollback",),
                ).fetchone()[0]
                for table in (
                    "raw_events",
                    "candidates",
                    "atoms",
                    "entities",
                    "memory_nodes",
                    "journal",
                )
            }

    before = counts()
    with monkeypatch.context() as patch:

        def fail_journal(_):
            raise RuntimeError("controlled transaction interruption")

        patch.setattr(memory.backend, "append_journal", fail_journal)
        with pytest.raises(RuntimeError, match="interruption"):
            memory.store(TEXT_A)
    assert counts() == before
    assert memory.vector_index.search([1.0, 0.0, 0.0]) == []
    assert memory.store(TEXT_A).atom_id is not None


@pytest.mark.parametrize("failure", ["http502", "malformed_json", "missing_vector"])
def test_remote_provider_errors_degrade_without_losing_memory(
    pg_vectors, monkeypatch, caplog, failure
):
    from urllib import request

    opened, _ = pg_vectors
    memory = opened(f"http-{failure}")
    memory._embedding_provider = OpenAIEmbeddingProvider(
        api_key="test-only", base_url="https://embeddings.invalid"
    )

    def response(*_, **__):
        if failure == "http502":
            raise HTTPError("https://embeddings.invalid/embeddings", 502, "controlled", {}, None)
        body = b"invalid json" if failure == "malformed_json" else b'{"data":[{}]}'
        return BytesIO(body)

    monkeypatch.setattr(request, "urlopen", response)
    node = memory.store(TEXT_A)
    assert memory.get_atom(node.atom_id).assertion == TEXT_A
    assert memory.vector_index.search([1.0, 0.0, 0.0]) == []
    assert _gather_vector_atoms(memory, ParsedQuery(text=TEXT_A), limit=10) == []
    assert any("atom embedding failed" in row.message for row in caplog.records)
    assert any("recall vector embed failed" in row.message for row in caplog.records)


@pytest.mark.xfail(
    strict=True,
    raises=TypeError,
    reason="MEM-VEC-02: SDK does not degrade a remote embedding=null TypeError",
)
def test_null_remote_embedding_should_degrade(pg_vectors, monkeypatch):
    from urllib import request

    opened, _ = pg_vectors
    memory = opened("http-null")
    memory._embedding_provider = OpenAIEmbeddingProvider(
        api_key="test-only", base_url="https://embeddings.invalid"
    )
    monkeypatch.setattr(
        request, "urlopen", lambda *_, **__: BytesIO(b'{"data":[{"embedding":null}]}')
    )
    try:
        memory.store(TEXT_A)
    finally:
        assert len(memory.list_atoms()) == 1  # relational commit predates the TypeError
        assert memory.vector_index.search([1.0, 0.0, 0.0]) == []


@pytest.mark.parametrize(
    "contract",
    [
        "test_cleanup_is_scoped_backed_up_and_other_expert_remains_writable",
        "test_backup_failure_deletes_nothing",
        "test_sql_failure_rolls_back_both_tables_and_retains_backup",
        "test_namespace_lock_and_busy_rows_do_not_block_other_experts",
        "test_each_pass_is_bounded",
    ],
)
def test_existing_pg_maintenance_contract(pg_vectors, tmp_path, monkeypatch, contract):
    from tests.integration import test_postgres_memory_storage as maintenance

    opened, _ = pg_vectors
    stores = (opened("maintenance-a").backend, opened("maintenance-b").backend)
    for backend in stores:
        maintenance.seed(backend)
    kwargs = {"stores": stores, "tmp_path": tmp_path}
    if contract in {"test_backup_failure_deletes_nothing", "test_each_pass_is_bounded"}:
        kwargs["monkeypatch"] = monkeypatch
    getattr(maintenance, contract)(**kwargs)


async def test_pg_memory_http_ownership_and_restart(pg_vectors, tmp_path, monkeypatch):
    """Actual PG-backed HTTP CRUD/auth, with no vector or LLM hookup claimed."""
    from octop.api.common.memory_client import invalidate_cached_memory
    from tests.support.app import octop_client, write_octop_config
    from tests.support.auth import auth_header, bootstrap_admin, create_agent, create_user

    _, dsn = pg_vectors
    home = tmp_path / "app-home"
    home.mkdir()
    monkeypatch.setenv("OCTOP_HOME", str(home))
    monkeypatch.delenv("OCTOP_BASE_PATH", raising=False)
    for key in tuple(os.environ):
        if key.startswith("OCTOP_DATABASE_"):
            monkeypatch.delenv(key)
    write_octop_config(home, database={"driver": "postgresql", "url": dsn})
    home.joinpath("config.json").chmod(0o600)
    agents = []
    atoms = []
    try:
        for restart in (False, True):
            async with octop_client(home) as (client, server):
                assert server.services.db.dialect == "postgresql"
                if not restart:
                    await bootstrap_admin(client, home)
                    admin = await auth_header(client)
                    alice = await create_user(client, admin, username="vector_alice")
                    bob = await create_user(client, admin, username="vector_bob")
                    agents.extend(
                        [
                            await create_agent(client, alice, name="Alice PG"),
                            await create_agent(client, bob, name="Bob PG"),
                        ]
                    )
                    for agent, auth, text in zip(
                        agents, (alice, bob), (TEXT_A, TEXT_B), strict=True
                    ):
                        response = await client.post(
                            f"/api/agents/{agent}/memory/atoms",
                            headers=auth,
                            json={"assertion": text, "entity_name": "报销"},
                        )
                        assert response.status_code == 200, response.text
                        atoms.append(response.json()["atom"]["id"])
                else:
                    alice = await auth_header(client, username="vector_alice")
                    bob = await auth_header(client, username="vector_bob")
                for i, auth in enumerate((alice, bob)):
                    base = f"/api/agents/{agents[i]}/memory/atoms"
                    own = await client.post(base + "/list", headers=auth, json={})
                    assert own.status_code == 200, own.text
                    assert [row["id"] for row in own.json()["items"]] == [atoms[i]]
                    missing = await client.get(base + "/" + atoms[1 - i], headers=auth)
                    assert missing.status_code == 404
                    foreign = f"/api/agents/{agents[1 - i]}/memory/atoms"
                    for method, path, body in (
                        ("POST", foreign + "/list", {}),
                        ("GET", foreign + "/" + atoms[1 - i], None),
                        (
                            "POST",
                            foreign + "/" + atoms[1 - i] + ":replace",
                            {"assertion": "禁止修改"},
                        ),
                        ("POST", foreign + "/" + atoms[1 - i] + ":deprecate", {}),
                    ):
                        denied = await client.request(method, path, headers=auth, json=body)
                        assert denied.status_code == 403, denied.text
                with psycopg.connect(dsn) as conn:
                    rows = conn.execute(
                        "SELECT namespace,id,assertion FROM octop_memory.atoms "
                        "WHERE id=ANY(%s) ORDER BY namespace",
                        (atoms,),
                    ).fetchall()
                    assert {row[0] for row in rows} == {
                        f"agent_{agent}".lower() for agent in agents
                    }
                    assert {row[2] for row in rows} == {TEXT_A, TEXT_B}
                    assert (
                        conn.execute("SELECT count(*) FROM test_memory_vectors").fetchone()[0] == 0
                    )
            for agent in agents:
                invalidate_cached_memory(agent)
    finally:
        for agent in agents:
            invalidate_cached_memory(agent)
