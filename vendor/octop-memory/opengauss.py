"""openGauss 6 memory backend, based on octop-memory 1.0.2 (MIT).

Reuse PostgreSQL CRUD, but own DDL, migration watermark and conflict semantics.
Only fresh openGauss installs are supported; no PostgreSQL legacy conversion.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from octop_memory.storage.backends.postgres import PostgresMemoryBackend
from octop_memory.types import ActiveEntity, Alias, DigestRecord, EntityPage
from psycopg import sql
from psycopg.types.json import Jsonb

_MARKER = "octop-memory:opengauss:1"
_TABLES = {
    "meta",
    "memory_nodes",
    "raw_events",
    "candidates",
    "atoms",
    "entities",
    "aliases",
    "entity_pages",
    "thread_active_entities",
    "journal",
    "episodes",
    "digests",
}


class OpenGaussMemoryBackend(PostgresMemoryBackend):
    def _init_schema(self) -> None:
        with self._cursor() as cur:
            cur.execute("SET LOCAL lockwait_timeout = 5000")
            cur.execute("SELECT pg_advisory_xact_lock(%s)", (0x686D656D,))
            cur.execute(
                "SELECT c.relname, obj_description(c.oid, 'pg_class') AS marker "
                "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname='octop_memory' AND c.relkind='r'"
            )
            rows = cur.fetchall()
            if rows:
                names = {r["relname"] for r in rows}
                markers = {r["marker"] for r in rows if r["relname"] == "meta"}
                if markers != {_MARKER} or not _TABLES.issubset(names):
                    raise RuntimeError(
                        "Existing memory schema is not a complete openGauss baseline"
                    )
                cur.execute("SELECT value FROM octop_memory.meta WHERE key='schema_version'")
                if cur.fetchone() != {"value": "1"}:
                    raise RuntimeError("Unsupported openGauss memory schema version")
            else:
                cur.execute("CREATE SCHEMA IF NOT EXISTS octop_memory")
                cur.execute("SET LOCAL search_path TO octop_memory, public")
                cur.execute(
                    Path(__file__).with_name("memory-baseline.sql").read_text(), prepare=False
                )
                cur.execute("COMMENT ON TABLE meta IS 'octop-memory:opengauss:1'")
                cur.execute("INSERT INTO meta(key,value) VALUES ('schema_version','1')")
        self._conn.commit()
        self._conn.execute("SET search_path TO octop_memory, public")
        self._conn.commit()

    def _migrate_legacy_schema(self) -> None:
        # PostgreSQL namespace layouts are deliberately not adopted on openGauss.
        return

    def _upsert_exact(
        self,
        table: str,
        key_columns: tuple[str, ...],
        values: dict[str, Any],
        update_columns: tuple[str, ...],
    ) -> None:
        key = json.dumps([table, *[values[k] for k in key_columns]], ensure_ascii=False)
        lock = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big", signed=True)
        with self._cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(%s)", (lock,))
            where = sql.SQL(" AND ").join(
                sql.SQL("{}=%s").format(sql.Identifier(k)) for k in key_columns
            )
            params = tuple(values[k] for k in key_columns)
            cur.execute(
                sql.SQL("SELECT 1 FROM {} WHERE {} FOR UPDATE").format(
                    sql.Identifier(table), where
                ),
                params,
            )
            if cur.fetchone():
                assignments = sql.SQL(", ").join(
                    sql.SQL("{}=%s").format(sql.Identifier(k)) for k in update_columns
                )
                cur.execute(
                    sql.SQL("UPDATE {} SET {} WHERE {}").format(
                        sql.Identifier(table), assignments, where
                    ),
                    tuple(values[k] for k in update_columns) + params,
                )
            else:
                cur.execute(
                    sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                        sql.Identifier(table),
                        sql.SQL(", ").join(map(sql.Identifier, values)),
                        sql.SQL(", ").join(sql.Placeholder() for _ in values),
                    ),
                    tuple(values.values()),
                )

    def upsert_entity_page(self, page: EntityPage) -> None:
        values = {
            "namespace": self._ns,
            "id": page.id,
            "entity_id": page.entity_id,
            "summary_markdown": page.summary_markdown,
            "headline": page.headline,
            "topics": Jsonb(page.topics),
            "dirty": page.dirty,
            "regen_attempt_count": page.regen_attempt_count,
            "summary_version": page.summary_version,
            "last_regen_at": page.last_regen_at,
            "last_user_edit_at": page.last_user_edit_at,
            "created_at": page.created_at,
            "updated_at": page.updated_at,
        }
        self._upsert_exact(
            "entity_pages",
            ("namespace", "entity_id"),
            values,
            tuple(k for k in values if k not in ("namespace", "id", "entity_id", "created_at")),
        )

    def mark_entity_page_dirty(self, entity_id: str, *, when: datetime) -> None:
        self._upsert_exact(
            "entity_pages",
            ("namespace", "entity_id"),
            {
                "namespace": self._ns,
                "id": f"page_{entity_id}",
                "entity_id": entity_id,
                "dirty": True,
                "created_at": when,
                "updated_at": when,
            },
            ("dirty", "updated_at"),
        )

    def upsert_digest(self, digest: DigestRecord) -> None:
        values = {
            "namespace": self._ns,
            "id": digest.id,
            "period_kind": digest.period_kind,
            "period_key": digest.period_key,
            "period_start": digest.period_start,
            "period_end": digest.period_end,
            "markdown": digest.markdown,
            "episode_ids": Jsonb(digest.episode_ids),
            "llm_version": digest.llm_version,
            "created_at": digest.created_at,
            "updated_at": digest.updated_at,
        }
        self._upsert_exact(
            "digests",
            ("namespace", "period_kind", "period_key"),
            values,
            ("period_start", "period_end", "markdown", "episode_ids", "llm_version", "updated_at"),
        )

    def save_alias(self, alias: Alias) -> None:
        with self._cursor() as cur:
            cur.execute(
                """INSERT INTO aliases
                    (namespace, alias, entity_id, entity_type, created_by, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE NOTHING""",
                (
                    self._ns,
                    alias.alias,
                    alias.entity_id,
                    alias.entity_type,
                    alias.created_by,
                    alias.created_at,
                ),
            )

    def upsert_active_entity(self, record: ActiveEntity) -> None:
        with self._cursor() as cur:
            cur.execute(
                """INSERT INTO thread_active_entities
                    (namespace, thread_id, entity_id, last_seen_at, source)
                    VALUES (%s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        last_seen_at = EXCLUDED.last_seen_at,
                        source = EXCLUDED.source
                """,
                (
                    self._ns,
                    record.thread_id,
                    record.entity_id,
                    record.last_seen_at,
                    record.source,
                ),
            )

    def set_meta(self, key: str, value: str) -> None:
        with self._cursor() as cur:
            cur.execute(
                """INSERT INTO meta (key, value) VALUES (%s, %s)
                   ON DUPLICATE KEY UPDATE value = EXCLUDED.value""",
                (self._meta_key(key), value),
            )
