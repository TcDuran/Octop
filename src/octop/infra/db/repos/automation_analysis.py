"""SQL-only storage for the most recent attempt on each automation item."""

from __future__ import annotations

from typing import Any

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import now_ts
from octop.infra.utils.ulid import new_ulid


class AutomationAnalysisRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    def get(self, item_id: str) -> dict[str, Any] | None:
        return self._one("item_id", item_id)

    def for_cron(self, cron_id: str) -> dict[str, Any] | None:
        return self._one("analysis_cron_id", cron_id)

    def _one(self, column: str, value: str) -> dict[str, Any] | None:
        with self._db.connect() as conn:
            row = conn.execute(
                f"SELECT * FROM automation_analysis_items WHERE {column} = ?",
                (value,),
            ).fetchone()
        return dict(row) if row else None

    def list_items(
        self,
        parent_cron_id: str,
        source_key: str,
        *,
        offset: int = 0,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM automation_analysis_items WHERE parent_cron_id = ? "
                "AND source_key = ? ORDER BY id LIMIT ? OFFSET ?",
                (parent_cron_id, source_key, limit, offset),
            ).fetchall()
        return [dict(row) for row in rows]

    def observe(
        self,
        *,
        parent_cron_id: str,
        agent_id: str,
        user_id: int,
        source_key: str,
        source_item_key: str,
        business_status: str,
        source_version: str,
        observed_at: int,
    ) -> dict[str, Any]:
        ts = now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO automation_analysis_items "
                "(item_id, parent_cron_id, agent_id, user_id, source_key, source_item_key, "
                "business_status, source_version, observed_at, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(parent_cron_id, source_key, source_item_key) DO UPDATE SET "
                "business_status = excluded.business_status, source_version = excluded.source_version, "
                "observed_at = excluded.observed_at, updated_at = excluded.updated_at "
                "WHERE excluded.observed_at >= automation_analysis_items.observed_at",
                (
                    new_ulid(),
                    parent_cron_id,
                    agent_id,
                    user_id,
                    source_key,
                    source_item_key,
                    business_status,
                    source_version,
                    observed_at,
                    ts,
                    ts,
                ),
            )
            row = conn.execute(
                "SELECT * FROM automation_analysis_items WHERE parent_cron_id = ? "
                "AND source_key = ? AND source_item_key = ?",
                (parent_cron_id, source_key, source_item_key),
            ).fetchone()
        assert row is not None
        return dict(row)

    def reserve(self, item_id: str, version: str, cron_id: str, session_key: str) -> bool:
        with self._db.transaction() as conn:
            result = conn.execute(
                "UPDATE automation_analysis_items SET analysis_cron_id = ?, analysis_version = ?, "
                "session_key = COALESCE(session_key, ?), dispatch_state = 'preparing', updated_at = ? "
                "WHERE item_id = ? AND source_version = ? AND business_status = 'pending' "
                "AND dispatch_state IN ('idle', 'finished') "
                "AND (analysis_version IS NULL OR analysis_version <> ?)",
                (cron_id, version, session_key, now_ts(), item_id, version, version),
            )
        return bool(result.rowcount == 1)

    def bind(self, item_id: str, cron_id: str, thread_id: str) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE automation_analysis_items SET thread_id = ?, updated_at = ? "
                "WHERE item_id = ? AND analysis_cron_id = ? AND dispatch_state = 'preparing'",
                (thread_id, now_ts(), item_id, cron_id),
            )

    def transition(self, cron_id: str, before: str, after: str) -> bool:
        with self._db.transaction() as conn:
            result = conn.execute(
                "UPDATE automation_analysis_items SET dispatch_state = ?, updated_at = ? "
                "WHERE analysis_cron_id = ? AND dispatch_state = ?",
                (after, now_ts(), cron_id, before),
            )
        return bool(result.rowcount == 1)

    def finish(self, cron_id: str, *, success: bool) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE automation_analysis_items SET dispatch_state = 'finished', updated_at = ?, "
                "last_success_version = CASE WHEN ? = 1 AND source_version = analysis_version "
                "AND business_status = 'pending' THEN analysis_version ELSE last_success_version END "
                "WHERE analysis_cron_id = ? AND dispatch_state IN ('queued', 'running')",
                (now_ts(), int(success), cron_id),
            )

    def interrupt_running(self) -> list[str]:
        with self._db.transaction() as conn:
            rows = conn.execute(
                "SELECT analysis_cron_id FROM automation_analysis_items WHERE dispatch_state = 'running'",
            ).fetchall()
            conn.execute(
                "UPDATE automation_analysis_items SET dispatch_state = 'interrupted', updated_at = ? "
                "WHERE dispatch_state = 'running'",
                (now_ts(),),
            )
        return [str(row["analysis_cron_id"]) for row in rows]
