"""Targeted sandbox follow-up; uses the same credential environment as verify.py."""

from __future__ import annotations

import os
from contextlib import contextmanager

import verify as v


def fixtures():
    v.query("CREATE SCHEMA og_probe")
    for definition in v.FUNCTIONS.values():
        v.query(definition)
    v.query("CREATE SEQUENCE og_probe.item_id_seq")
    v.query("""CREATE TABLE og_probe.items(
        id BIGINT DEFAULT nextval('og_probe.item_id_seq') PRIMARY KEY,
        public_id TEXT NOT NULL UNIQUE,content TEXT NOT NULL,terms JSONB NOT NULL DEFAULT '[]',
        content_tsv TSVECTOR GENERATED ALWAYS AS (to_tsvector('simple',content)||
            to_tsvector('simple',public.jsonb_path_query_array(terms,'$[*]')::text)) STORED
        ) WITH (storage_type=ASTORE)""")
    v.query("ALTER SEQUENCE og_probe.item_id_seq OWNED BY og_probe.items.id")
    v.query("CREATE INDEX items_fts ON og_probe.items USING GIN(content_tsv)")
    v.query(
        "INSERT INTO og_probe.items(public_id,content,terms) VALUES ('item-a','hello world','[\"sample\"]')"
    )
    return "test fixtures only; no application initialization claim"


def upgrade():
    v.query("CREATE TABLE og_probe.upgrade_version(v INT PRIMARY KEY)")
    v.query("INSERT INTO og_probe.upgrade_version VALUES(1)")
    v.query("CREATE TABLE og_probe.upgrade_items(k TEXT PRIMARY KEY,payload TEXT)")
    v.query("INSERT INTO og_probe.upgrade_items VALUES('preserved','payload')")
    rejected = v.expect_error(
        "ALTER TABLE og_probe.upgrade_items ADD COLUMN IF NOT EXISTS revision INT DEFAULT 1",
        {"42601"},
    )
    for _ in range(2):
        with v.connect() as conn, conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(987612345)")
            exists = conn.execute(
                "SELECT 1 FROM information_schema.columns WHERE table_schema='og_probe' AND table_name='upgrade_items' AND column_name='revision'"
            ).fetchone()
            if not exists:
                conn.execute(
                    "ALTER TABLE og_probe.upgrade_items ADD COLUMN revision INT NOT NULL DEFAULT 1"
                )
            conn.execute("UPDATE og_probe.upgrade_version SET v=2")
    assert v.query("SELECT * FROM og_probe.upgrade_items") == [("preserved", "payload", 1)]
    assert v.query("SELECT v FROM og_probe.upgrade_version") == [(2,)]
    return {
        "original_add_column_rejected": rejected,
        "metadata_checked_alter_repeat_passed": True,
        "scope": "sample incremental migration only; no Octop openGauss migration exists yet",
    }


def memory_prototype():
    from octop_memory.storage.backends.postgres import PostgresMemoryBackend

    adapted = []

    class Cursor:
        def __init__(self, cur):
            self.cur = cur

        def execute(self, statement, params=None, **kwargs):
            if isinstance(statement, str):
                if statement.strip().startswith("SET LOCAL lock_timeout"):
                    statement = "SET LOCAL lockwait_timeout = 5000"
                    adapted.append("lockwait_timeout")
                target = "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
                if target in statement:
                    statement = statement.replace(
                        target, "ON DUPLICATE KEY UPDATE value = EXCLUDED.value"
                    )
                    adapted.append("meta_single_primary_key_upsert")
            try:
                self.cur.execute(statement, params, **kwargs)
            except Exception as exc:
                v.record(
                    "memory_prototype_failing_statement",
                    "failed",
                    " ".join(str(statement).split())[:1500],
                    scope="prototype",
                    sqlstate=getattr(exc, "sqlstate", None),
                )
                raise
            return self

        def __getattr__(self, name):
            return getattr(self.cur, name)

        def __iter__(self):
            return iter(self.cur)

    class Backend(PostgresMemoryBackend):
        @contextmanager
        def _cursor(self):
            with super()._cursor() as cur:
                yield Cursor(cur)

    backend = Backend(namespace="prototype_memory", dsn=v.conninfo(v.NAMES[0]))
    try:
        rows = v.query(
            "SELECT tablename FROM pg_tables WHERE schemaname='octop_memory' ORDER BY tablename"
        )
        assert len(rows) > 5
        return {
            "tables": rows,
            "adaptations": sorted(set(adapted)),
            "scope": "DDL bootstrap only; not full memory business regression",
        }
    finally:
        backend.close()


def suite():
    if not v.case("followup_fixture_setup", fixtures, scope="fixture"):
        return
    v.case("metadata_checked_incremental_upgrade", upgrade, scope="prototype")
    v.case("memory_bootstrap_with_two_exact_adaptations", memory_prototype, scope="prototype")
    v.checkpoint_tests()
    v.backup_tests()


if __name__ == "__main__":
    os.environ["OCTOP_VERIFY_RESULTS_FILE"] = "followup-results.json"
    v.main(suite)
