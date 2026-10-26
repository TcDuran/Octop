"""Disposable openGauss validation; credentials are supplied only via environment.

Run from the repository root with .venv/bin/python. Never point repository
integration tests at the management database. Only databases created by this
process, with their recorded OIDs, may be written to or dropped.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TypedDict

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
HOST = os.environ["OCTOP_VERIFY_HOST"]
PORT = int(os.environ.get("OCTOP_VERIFY_PORT", "15434"))
USER = os.environ["OCTOP_VERIFY_USER"]
PASSWORD = os.environ["OCTOP_VERIFY_PASSWORD"]
MANAGEMENT = os.environ.get("OCTOP_VERIFY_MANAGEMENT_DB", "plat")
IMAGE = "10.24.1.80:2443/library/opengauss:6.0.5"
TOKEN = secrets.token_hex(4)
NAMES = [f"octop_verify_20261022_{TOKEN}", f"octop_verify_restore_20261022_{TOKEN}"]
CREATED: dict[str, int] = {}
RESULTS: list[dict] = []
CONTAINERS: list[str] = []


def safe(value):
    return str(value).replace(PASSWORD, "<redacted>")


def persist():
    (OUT / os.environ.get("OCTOP_VERIFY_RESULTS_FILE", "results.json")).write_text(
        json.dumps(
            {"run_id": TOKEN, "databases": NAMES, "results": RESULTS},
            indent=2,
            ensure_ascii=False,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )


def record(name, status, evidence=None, **details):
    row = {"name": name, "status": status, "evidence": evidence, **details}
    RESULTS.append(row)
    persist()
    print(json.dumps(row, ensure_ascii=False, default=str), flush=True)


def case(name, fn, *, scope="native"):
    print(json.dumps({"running": name, "scope": scope}), flush=True)
    started = time.monotonic()
    try:
        evidence = fn()
        record(name, "passed", evidence, scope=scope, seconds=round(time.monotonic() - started, 3))
        return True
    except Exception as exc:
        record(
            name,
            "failed",
            safe(getattr(getattr(exc, "diag", None), "message_primary", None) or exc),
            scope=scope,
            sqlstate=getattr(exc, "sqlstate", None),
            error_type=type(exc).__name__,
            seconds=round(time.monotonic() - started, 3),
        )
        return False


def conninfo(database, schema="public"):
    assert database in CREATED and database != MANAGEMENT
    return make_conninfo(
        host=HOST,
        port=PORT,
        dbname=database,
        user=USER,
        password=PASSWORD,
        connect_timeout=8,
        application_name=f"octop_verify_{TOKEN}",
        options=f"-c statement_timeout=10000 -c search_path={schema},public",
    )


def connect(database=NAMES[0], schema="public", **kwargs):
    conn = psycopg.connect(conninfo(database, schema), autocommit=True, **kwargs)
    current, oid = conn.execute(
        "SELECT current_database(), oid FROM pg_database WHERE datname=current_database()"
    ).fetchone()
    assert current == database and oid == CREATED[database]
    return conn


def query(statement, params=None, *, database=NAMES[0]):
    with connect(database) as conn:
        cur = conn.execute(statement, params)
        return cur.fetchall() if cur.description else {"rowcount": cur.rowcount}


def expect_error(statement, expected, params=None):
    try:
        query(statement, params)
    except psycopg.Error as exc:
        assert exc.sqlstate in expected, (exc.sqlstate, exc.diag.message_primary)
        return {"expected_sqlstate": exc.sqlstate, "message": exc.diag.message_primary}
    raise AssertionError("expected a database error")


FUNCTIONS = {
    "to_regclass": """CREATE FUNCTION public.to_regclass(text) RETURNS regclass
        LANGUAGE plpgsql STABLE STRICT AS $$
        DECLARE result regclass; BEGIN
          BEGIN EXECUTE 'SELECT ' || quote_literal($1) || '::regclass' INTO result;
          EXCEPTION WHEN undefined_table OR invalid_schema_name THEN RETURN NULL; END;
          RETURN result;
        END $$""",
    "to_jsonb": """CREATE FUNCTION public.to_jsonb(anyelement) RETURNS jsonb
        LANGUAGE SQL STABLE STRICT AS $$
          SELECT (row_to_json(ROW($1))::jsonb)->'f1'
        $$""",
    "hashtextextended": """CREATE FUNCTION public.hashtextextended(text,bigint) RETURNS bigint
        LANGUAGE SQL IMMUTABLE STRICT AS $$
          SELECT ('x'||substr(md5($2::text||':'||$1),1,16))::bit(64)::bigint
        $$""",
    "jsonb_path_query_array": """CREATE FUNCTION public.jsonb_path_query_array(jsonb,text) RETURNS jsonb
        LANGUAGE plpgsql IMMUTABLE STRICT AS $$ BEGIN
          IF $2 <> '$[*]' THEN RAISE EXCEPTION 'unsupported JSONPath' USING ERRCODE='0A000'; END IF;
          IF jsonb_typeof($1) <> 'array' THEN
            RAISE EXCEPTION 'prototype accepts arrays only' USING ERRCODE='22023';
          END IF;
          RETURN $1;
        END $$""",
}


def ddl_and_dml():
    case("create_schema", lambda: query("CREATE SCHEMA og_probe"))
    case(
        "identity_original_syntax",
        lambda: query(
            "CREATE TABLE og_probe.identity_probe(id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY)"
        ),
    )
    for name, definition in FUNCTIONS.items():
        case(
            "create_function_" + name,
            lambda definition=definition: query(definition),
            scope="prototype",
        )

    def functions():
        assert query("SELECT public.to_regclass('missing_test_relation') IS NULL")[0][0]
        assert query(
            "SELECT public.to_jsonb(42),public.to_jsonb(ARRAY[1,2]),public.to_jsonb(NULL::text)"
        )[0] == (42, [1, 2], None)
        assert query(
            "SELECT public.to_jsonb(t) FROM (SELECT 7 AS id, 'sample'::text AS content) t"
        )[0][0] == {"id": 7, "content": "sample"}
        assert query("SELECT public.jsonb_path_query_array('[1,2]'::jsonb,'$[*]')")[0][0] == [1, 2]
        a, b = query("SELECT public.hashtextextended('key',0),public.hashtextextended('key',1)")[0]
        assert a != b and -(2**63) <= a < 2**63
        return {
            "arrays_scalars_records_sql_null": True,
            "unsupported_path": expect_error(
                "SELECT public.jsonb_path_query_array('[]'::jsonb,'$.unknown')", {"0A000"}
            ),
            "unsupported_scalar": expect_error(
                "SELECT public.jsonb_path_query_array('1'::jsonb,'$[*]')", {"22023"}
            ),
        }

    case("compatibility_function_behavior", functions, scope="prototype")

    def table():
        query("CREATE SEQUENCE og_probe.item_id_seq")
        query("""CREATE TABLE og_probe.items(
            id BIGINT DEFAULT nextval('og_probe.item_id_seq') PRIMARY KEY,
            public_id TEXT NOT NULL UNIQUE,content TEXT NOT NULL,terms JSONB NOT NULL DEFAULT '[]',
            content_tsv TSVECTOR GENERATED ALWAYS AS (to_tsvector('simple',content)||
              to_tsvector('simple',public.jsonb_path_query_array(terms,'$[*]')::text)) STORED
            ) WITH (storage_type=ASTORE)""")
        query("ALTER SEQUENCE og_probe.item_id_seq OWNED BY og_probe.items.id")
        query("CREATE INDEX items_fts ON og_probe.items USING GIN(content_tsv)")
        query("CREATE INDEX CONCURRENTLY items_public_idx ON og_probe.items(public_id)")
        return "explicit sequence, Astore, generated column, GIN, concurrent index created"

    table_ok = case("sequence_generated_fts_and_indexes", table, scope="prototype")

    def crud():
        first = query(
            "INSERT INTO og_probe.items(public_id,content,terms) VALUES ('item-a','hello world','[\"searchtoken\"]') RETURNING id"
        )[0][0]
        assert first > 0
        assert query(
            "SELECT public_id FROM og_probe.items WHERE content_tsv @@ plainto_tsquery('simple','searchtoken')"
        ) == [("item-a",)]
        assert query(
            "UPDATE og_probe.items SET content='updated token' WHERE id=%s RETURNING public_id",
            (first,),
        ) == [("item-a",)]
        assert (
            query(
                "SELECT count(*) FROM og_probe.items WHERE content_tsv @@ plainto_tsquery('simple','updated')"
            )[0][0]
            == 1
        )
        query("INSERT INTO og_probe.items(public_id,content) VALUES ('temporary','remove')")
        assert query(
            "DELETE FROM og_probe.items WHERE public_id='temporary' RETURNING public_id"
        ) == [("temporary",)]
        assert query("SELECT public.to_regclass('og_probe.items') IS NOT NULL")[0][0]
        return {"insert_update_delete_returning": True, "generated_fts_recomputed": True}

    if table_ok:
        case("real_crud_and_generated_fulltext", crud, scope="prototype")

    def deferrable():
        query("CREATE TABLE og_probe.parent(k TEXT PRIMARY KEY) WITH (storage_type=ASTORE)")
        query(
            "CREATE TABLE og_probe.child(k TEXT PRIMARY KEY,p TEXT REFERENCES og_probe.parent(k) DEFERRABLE INITIALLY IMMEDIATE) WITH (storage_type=ASTORE)"
        )
        with connect() as conn, conn.transaction():
            conn.execute("SET CONSTRAINTS ALL DEFERRED")
            conn.execute("INSERT INTO og_probe.child VALUES ('child','parent')")
            conn.execute("INSERT INTO og_probe.parent VALUES ('parent')")
        return {
            "deferred_child_before_parent": True,
            "foreign_key_enforced": expect_error(
                "INSERT INTO og_probe.child VALUES ('bad','missing')", {"23503"}
            ),
        }

    case("deferrable_foreign_key_astore", deferrable)

    query("CREATE TABLE og_probe.kv(k TEXT PRIMARY KEY,v TEXT NOT NULL,observed BIGINT NOT NULL)")
    case(
        "original_on_conflict_real_dml",
        lambda: query(
            "INSERT INTO og_probe.kv VALUES ('original','x',1) ON CONFLICT(k) DO NOTHING"
        ),
    )

    def upsert():
        ins = query(
            "INSERT INTO og_probe.kv VALUES ('a','first',10) ON DUPLICATE KEY UPDATE v=EXCLUDED.v,observed=EXCLUDED.observed"
        )
        upd = query(
            "INSERT INTO og_probe.kv VALUES ('a','newer',20) ON DUPLICATE KEY UPDATE v=EXCLUDED.v,observed=EXCLUDED.observed WHERE EXCLUDED.observed>=kv.observed"
        )
        old = query(
            "INSERT INTO og_probe.kv VALUES ('a','older',5) ON DUPLICATE KEY UPDATE v=EXCLUDED.v,observed=EXCLUDED.observed WHERE EXCLUDED.observed>=kv.observed"
        )
        ignored = query(
            "INSERT INTO og_probe.kv VALUES ('a','ignored',30) ON DUPLICATE KEY UPDATE NOTHING"
        )
        assert query("SELECT v,observed FROM og_probe.kv WHERE k='a'") == [("newer", 20)]
        return {"insert": ins, "update": upd, "older_rejected": old, "duplicate_ignored": ignored}

    case("native_upsert_conditional_ignore_rowcount", upsert)
    case(
        "native_upsert_returning",
        lambda: query(
            "INSERT INTO og_probe.kv VALUES ('a','returning',30) ON DUPLICATE KEY UPDATE v=EXCLUDED.v,observed=EXCLUDED.observed RETURNING k,v"
        ),
    )

    def unique():
        query(
            "CREATE TABLE og_probe.identities(user_id BIGINT,provider_id BIGINT,subject TEXT, UNIQUE(user_id,provider_id),UNIQUE(provider_id,subject))"
        )
        query("INSERT INTO og_probe.identities VALUES (1,1,'subject-a')")
        return query(
            "INSERT INTO og_probe.identities VALUES (1,1,'subject-b') ON DUPLICATE KEY UPDATE subject=EXCLUDED.subject"
        )

    case("native_upsert_unique_field_update", unique)

    def wrong_conflict():
        query(
            "CREATE TABLE og_probe.events(event_id TEXT UNIQUE,thread_id TEXT,seq INT,payload TEXT,UNIQUE(thread_id,seq))"
        )
        query("INSERT INTO og_probe.events VALUES ('event-a','thread',1,'first')")
        query(
            "INSERT INTO og_probe.events VALUES ('event-b','thread',1,'second') ON DUPLICATE KEY UPDATE payload=EXCLUDED.payload"
        )
        rows = query("SELECT event_id,payload FROM og_probe.events")
        assert rows == [("event-a", "second")]
        return {
            "hazard_reproduced": True,
            "rows": rows,
            "meaning": "native upsert updates on another unique constraint; not equivalent to targeted ON CONFLICT",
        }

    case("multiple_unique_conflict_hazard", wrong_conflict)

    def atomic_consume():
        query(
            "CREATE TABLE og_probe.login_codes(k TEXT PRIMARY KEY,consumed BOOLEAN NOT NULL DEFAULT FALSE)"
        )
        query("INSERT INTO og_probe.login_codes(k) VALUES ('once')")

        def consume(_):
            return query(
                "UPDATE og_probe.login_codes SET consumed=TRUE WHERE k='once' AND consumed=FALSE RETURNING k"
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(consume, range(2)))
        assert sorted(map(len, results)) == [0, 1]
        return {"concurrent_success_counts": list(map(len, results))}

    case("atomic_one_time_consume", atomic_consume)

    def skip_locked():
        query("CREATE TABLE og_probe.queue(id INT PRIMARY KEY)")
        query("INSERT INTO og_probe.queue VALUES (1),(2)")
        with connect() as first, connect() as second, first.transaction(), second.transaction():
            assert (
                first.execute("SELECT id FROM og_probe.queue WHERE id=1 FOR UPDATE").fetchone()[0]
                == 1
            )
            rows = second.execute(
                "SELECT id FROM og_probe.queue ORDER BY id FOR UPDATE SKIP LOCKED"
            ).fetchall()
            assert rows == [(2,)]
        return rows

    case("real_skip_locked_two_sessions", skip_locked)

    def advisory():
        with connect() as first, connect() as second, first.transaction(), second.transaction():
            a = first.execute(
                "SELECT pg_try_advisory_xact_lock(public.hashtextextended('same',0))"
            ).fetchone()[0]
            b = second.execute(
                "SELECT pg_try_advisory_xact_lock(public.hashtextextended('same',0))"
            ).fetchone()[0]
            assert a and not b
        return {"first_acquired": a, "second_acquired": b}

    case("prototype_transaction_advisory_lock", advisory, scope="prototype")

    def synthetic_upgrade():
        query("CREATE TABLE og_probe.upgrade_version(v INT PRIMARY KEY)")
        query("INSERT INTO og_probe.upgrade_version VALUES(1)")
        query("CREATE TABLE og_probe.upgrade_items(k TEXT PRIMARY KEY,payload TEXT)")
        query("INSERT INTO og_probe.upgrade_items VALUES('preserved','payload')")
        for _ in range(2):
            with connect() as conn, conn.transaction():
                conn.execute(
                    "ALTER TABLE og_probe.upgrade_items ADD COLUMN IF NOT EXISTS revision INT NOT NULL DEFAULT 1"
                )
                conn.execute("UPDATE og_probe.upgrade_version SET v=2")
        assert query("SELECT * FROM og_probe.upgrade_items") == [("preserved", "payload", 1)]
        return "sample ALTER/backfill/version transaction and repeat apply only; not Octop upgrade acceptance"

    case("synthetic_incremental_upgrade", synthetic_upgrade, scope="prototype")


def application_initialization():
    from octop.infra.db.migrate import run_migrations
    from octop.infra.db.pool import PostgresPool

    query("CREATE SCHEMA control_raw")

    def init():
        pool = PostgresPool(conninfo(NAMES[0], "control_raw"), min_size=1, max_size=2)
        try:
            run_migrations(pool)
            run_migrations(pool)
        finally:
            pool.close()
        return query("SELECT version FROM control_raw._schema_version")

    case("unmodified_control_plane_initialization", init, scope="application")

    def memory():
        from octop_memory.storage.backends.postgres import PostgresMemoryBackend

        backend = PostgresMemoryBackend(namespace="verification", dsn=conninfo(NAMES[0]))
        try:
            return "unmodified memory backend initialized"
        finally:
            backend.close()

    case("unmodified_memory_initialization", memory, scope="application")


def checkpoint_tests():
    from langgraph.checkpoint.postgres import PostgresSaver
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import Command, interrupt

    query("CREATE SCHEMA checkpoint_raw")
    query("CREATE SCHEMA checkpoint_proto")
    raw_dsn = conninfo(NAMES[0], "checkpoint_raw")
    proto_dsn = conninfo(NAMES[0], "checkpoint_proto")

    def raw_setup():
        with PostgresSaver.from_conn_string(raw_dsn) as saver:
            saver.setup()
        return query("SELECT max(v) FROM checkpoint_raw.checkpoint_migrations")

    raw_ok = case("unmodified_checkpoint_migrations", raw_setup, scope="application")

    class State(TypedDict):
        value: int

    def build(saver, pause=False):
        builder = StateGraph(State)

        def step(state):
            if pause:
                accepted = interrupt("approve synthetic step")
                assert accepted is True
            return {"value": state["value"] + 1}

        builder.add_node("step", step)
        builder.add_edge(START, "step")
        builder.add_edge("step", END)
        return builder.compile(checkpointer=saver)

    if raw_ok:

        def raw_write():
            with PostgresSaver.from_conn_string(raw_dsn) as saver:
                return build(saver).invoke({"value": 1}, {"configurable": {"thread_id": "raw"}})

        case("unmodified_checkpoint_real_graph_write", raw_write, scope="application")

    # Exact overrides for these four single-PK tables only; not a general SQL translator.
    class PrototypeSaver(PostgresSaver):
        SELECT_SQL = PostgresSaver.SELECT_SQL
        if os.environ.get("OCTOP_VERIFY_CHECKPOINT_JSON_READ") == "1":
            SELECT_SQL = SELECT_SQL.replace(
                "array_agg(array[bl.channel::bytea, bl.type::bytea, bl.blob])",
                "json_agg(array[encode(bl.channel::bytea,'base64'), encode(bl.type::bytea,'base64'), encode(bl.blob,'base64')])",
            )
            SELECT_SQL = SELECT_SQL.replace(
                "array_agg(array[cw.task_id::text::bytea, cw.channel::bytea, cw.type::bytea, cw.blob] order by cw.task_id, cw.idx)",
                "json_agg(array[encode(cw.task_id::text::bytea,'base64'), encode(cw.channel::bytea,'base64'), encode(cw.type::bytea,'base64'), encode(cw.blob,'base64')] order by cw.task_id, cw.idx)",
            )

        def _load_checkpoint_tuple(self, value):
            if os.environ.get("OCTOP_VERIFY_CHECKPOINT_JSON_READ") == "1":
                value = dict(value)
                for key in ("channel_values", "pending_writes"):
                    if value.get(key) is not None:
                        value[key] = [
                            tuple(
                                base64.b64decode(item) if item is not None else None for item in row
                            )
                            for row in value[key]
                        ]
            return super()._load_checkpoint_tuple(value)

        MIGRATIONS = [
            migration.replace("ADD COLUMN IF NOT EXISTS task_path", "ADD COLUMN task_path")
            for migration in PostgresSaver.MIGRATIONS
        ]
        UPSERT_CHECKPOINT_BLOBS_SQL = PostgresSaver.UPSERT_CHECKPOINT_BLOBS_SQL.replace(
            "ON CONFLICT (thread_id, checkpoint_ns, channel, version) DO NOTHING",
            "ON DUPLICATE KEY UPDATE NOTHING",
        )
        UPSERT_CHECKPOINTS_SQL = PostgresSaver.UPSERT_CHECKPOINTS_SQL.replace(
            "ON CONFLICT (thread_id, checkpoint_ns, checkpoint_id)\n    DO UPDATE SET",
            "ON DUPLICATE KEY UPDATE",
        )
        UPSERT_CHECKPOINT_WRITES_SQL = PostgresSaver.UPSERT_CHECKPOINT_WRITES_SQL.replace(
            "ON CONFLICT (thread_id, checkpoint_ns, checkpoint_id, task_id, idx) DO UPDATE SET",
            "ON DUPLICATE KEY UPDATE",
        )
        INSERT_CHECKPOINT_WRITES_SQL = PostgresSaver.INSERT_CHECKPOINT_WRITES_SQL.replace(
            "ON CONFLICT (thread_id, checkpoint_ns, checkpoint_id, task_id, idx) DO NOTHING",
            "ON DUPLICATE KEY UPDATE NOTHING",
        )

    def prototype():
        config = {"configurable": {"thread_id": "prototype-roundtrip"}}
        with PrototypeSaver.from_conn_string(proto_dsn) as saver:
            saver.setup()
            assert build(saver).invoke({"value": 4}, config)["value"] == 5
            assert saver.get_tuple({"configurable": {"thread_id": "another"}}) is None
            assert (
                saver.get_tuple(
                    {
                        "configurable": {
                            "thread_id": "prototype-roundtrip",
                            "checkpoint_ns": "another",
                        }
                    }
                )
                is None
            )
        with PrototypeSaver.from_conn_string(proto_dsn) as saver:
            saver.setup()
            snapshot = build(saver).get_state(config)
            assert snapshot.values["value"] == 5
            saved = saver.get_tuple(config)
            assert saved is not None
            for _ in range(2):
                saver.put_writes(saved.config, [("__error__", "synthetic error")], "duplicate-task")
            assert list(saver.list(config, limit=10))
        count = query(
            "SELECT count(*) FROM checkpoint_proto.checkpoint_writes WHERE task_id='duplicate-task'"
        )[0][0]
        assert count == 1
        return {
            "real_graph_save": True,
            "new_connection_restore": True,
            "thread_namespace_isolation": True,
            "duplicate_write_count": count,
        }

    case("prototype_checkpoint_graph_restart_idempotency", prototype, scope="prototype")

    def resume():
        config = {"configurable": {"thread_id": "prototype-interrupt"}}
        with PrototypeSaver.from_conn_string(proto_dsn) as saver:
            saver.setup()
            result = build(saver, pause=True).invoke({"value": 8}, config)
            assert result.get("__interrupt__")
        with PrototypeSaver.from_conn_string(proto_dsn) as saver:
            result = build(saver, pause=True).invoke(Command(resume=True), config)
            assert result["value"] == 9
        return {
            "synthetic_human_interrupt": True,
            "new_connection_resume": True,
            "no_llm_calls": True,
        }

    case("prototype_checkpoint_interrupt_resume", resume, scope="prototype")


def backup_tests():
    with tempfile.TemporaryDirectory(prefix="octop-og-archive-") as directory:
        folder = Path(directory)
        folder.chmod(0o777)
        env = dict(os.environ, PGPASSWORD=PASSWORD)

        def tool(program, arguments):
            name = f"octop-og-{TOKEN}-{len(CONTAINERS)}"
            CONTAINERS.append(name)
            cmd = [
                "docker",
                "run",
                "-i",
                "--rm",
                "--pull=never",
                "--platform",
                "linux/amd64",
                "--name",
                name,
                "--label",
                f"octop.verify={TOKEN}",
                "--user",
                "omm",
                "-e",
                "PGPASSWORD",
                "-v",
                f"{folder}:/work",
                "--entrypoint",
                "/bin/bash",
                IMAGE,
                "-c",
                'export LD_LIBRARY_PATH=/usr/local/opengauss/lib; exec "$@"',
                "_",
                f"/usr/local/opengauss/bin/{program}",
                *arguments,
            ]
            result = subprocess.run(
                cmd, input=PASSWORD + "\n", capture_output=True, text=True, env=env, timeout=90
            )
            if result.returncode:
                raise RuntimeError(safe(result.stderr[-2500:] or result.stdout[-2500:]))
            return {
                "returncode": result.returncode,
                "output": safe((result.stdout + result.stderr)[-1500:]),
            }

        case("matching_backup_tool_version", lambda: tool("gs_dump", ["--version"]), scope="backup")
        common = ["-h", HOST, "-p", str(PORT), "-U", USER, "--pipeline"]
        expected = query(
            "SELECT id,public_id,content,terms,content_tsv::text FROM og_probe.items ORDER BY id"
        )
        dump_ok = case(
            "gs_dump_custom_archive",
            lambda: tool("gs_dump", [*common, "-F", "c", "-f", "/work/validation.dump", NAMES[0]]),
            scope="backup",
        )
        restore_ok = False
        if dump_ok:
            restore_ok = case(
                "gs_restore_into_second_empty_database",
                lambda: tool(
                    "gs_restore", [*common, "-d", NAMES[1], "-e", "/work/validation.dump"]
                ),
                scope="backup",
            )
        if restore_ok:

            def verify_restored():
                actual = query(
                    "SELECT id,public_id,content,terms,content_tsv::text FROM og_probe.items ORDER BY id",
                    database=NAMES[1],
                )
                assert actual == expected, (expected, actual)
                query(
                    "INSERT INTO og_probe.items(public_id,content,terms) VALUES ('after-restore','restored content','[\"afterrestore\"]')",
                    database=NAMES[1],
                )
                assert (
                    query(
                        "SELECT count(*) FROM og_probe.items WHERE content_tsv @@ plainto_tsquery('simple','afterrestore')",
                        database=NAMES[1],
                    )[0][0]
                    == 1
                )
                assert (
                    query(
                        "SELECT count(*) FROM pg_indexes WHERE schemaname='og_probe' AND indexname='items_fts'",
                        database=NAMES[1],
                    )[0][0]
                    == 1
                )
                return {
                    "snapshot_equal": True,
                    "generated_column_after_restore": True,
                    "sequence_and_gin": True,
                }

            case("backup_restore_data_and_generated_behavior", verify_restored, scope="backup")


def repository_integration_tests():
    with tempfile.TemporaryDirectory(prefix="octop-og-home-") as home:
        env = dict(os.environ, OCTOP_TEST_DATABASE_URL=conninfo(NAMES[0]), OCTOP_HOME=home)
        env.pop("OCTOP_DATABASE_URL", None)
        assert NAMES[0] in env["OCTOP_TEST_DATABASE_URL"] and NAMES[0] != MANAGEMENT
        result = subprocess.run(
            [
                "uv",
                "run",
                "--no-sync",
                "pytest",
                "tests/integration/test_postgresql_control_plane.py",
                "tests/integration/test_postgresql_checkpoint_history.py",
                "-q",
                "--tb=short",
                "-o",
                "addopts=",
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=150,
        )
        output = safe(result.stdout + result.stderr)
        (OUT / "repository-integration.log").write_text(output, encoding="utf-8")
        record(
            "unmodified_repository_pg_integration",
            "passed" if result.returncode == 0 else "failed",
            {
                "returncode": result.returncode,
                "log": "repository-integration.log",
                "summary": output[-2000:],
            },
            scope="application",
        )


def main(suite=None):
    OUT.mkdir(parents=True, exist_ok=True)
    management = psycopg.connect(
        host=HOST,
        port=PORT,
        dbname=MANAGEMENT,
        user=USER,
        password=PASSWORD,
        connect_timeout=8,
        autocommit=True,
        options="-c default_transaction_read_only=on -c statement_timeout=10000",
        application_name=f"octop_verify_management_{TOKEN}",
    )
    before = management.execute(
        "SELECT oid,datname,datdba FROM pg_database ORDER BY oid"
    ).fetchall()
    try:
        for name in NAMES:
            assert name != MANAGEMENT and not any(row[1] == name for row in before)
            management.execute("SET default_transaction_read_only=off")
            try:
                management.execute(
                    sql.SQL(
                        "CREATE DATABASE {} WITH TEMPLATE template0 DBCOMPATIBILITY 'PG'"
                    ).format(sql.Identifier(name))
                )
            finally:
                management.execute("SET default_transaction_read_only=on")
            oid, owner = management.execute(
                "SELECT oid,pg_get_userbyid(datdba) FROM pg_database WHERE datname=%s", (name,)
            ).fetchone()
            CREATED[name] = oid
            record(
                "create_isolated_database", "passed", {"database": name, "oid": oid, "owner": owner}
            )
        with connect() as conn:
            record(
                "isolated_database_identity",
                "passed",
                conn.execute("SELECT current_database(),current_user,version()").fetchone(),
            )
            record(
                "isolated_database_settings",
                "passed",
                conn.execute(
                    "SELECT name,setting,unit FROM pg_settings WHERE name IN ('sql_compatibility','enable_default_ustore_table','lock_timeout','lockwait_timeout') ORDER BY name"
                ).fetchall(),
            )
        if suite is None:
            ddl_and_dml()
            application_initialization()
            checkpoint_tests()
            backup_tests()
            repository_integration_tests()
        else:
            suite()
    except Exception as exc:
        record("validation_orchestration", "failed", safe(exc), error_type=type(exc).__name__)
    finally:
        for name in CONTAINERS:
            info = subprocess.run(
                ["docker", "inspect", "--format", '{{index .Config.Labels "octop.verify"}}', name],
                capture_output=True,
                text=True,
            )
            if info.returncode == 0 and info.stdout.strip() == TOKEN:
                subprocess.run(["docker", "rm", "-f", name], capture_output=True, text=True)
        for name, oid in reversed(list(CREATED.items())):
            try:
                current = management.execute(
                    "SELECT oid FROM pg_database WHERE datname=%s", (name,)
                ).fetchone()
                assert current == (oid,), (name, current, oid)
                management.execute("SET default_transaction_read_only=off")
                try:
                    management.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
                finally:
                    management.execute("SET default_transaction_read_only=on")
                record("cleanup_database", "passed", {"database": name, "oid": oid})
            except Exception as exc:
                record("cleanup_database", "failed", {"database": name, "error": safe(exc)})
        after = management.execute(
            "SELECT oid,datname,datdba FROM pg_database ORDER BY oid"
        ).fetchall()
        record(
            "cleanup_verification",
            "passed" if before == after else "failed",
            {
                "database_catalog_unchanged": before == after,
                "test_databases_absent": all(row[1] not in CREATED for row in after),
                "management_read_only": management.execute("SHOW transaction_read_only").fetchone()[
                    0
                ],
            },
        )
        management.close()


if __name__ == "__main__":
    main()
