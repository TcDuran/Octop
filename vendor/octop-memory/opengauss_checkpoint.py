"""openGauss saver for the pinned langgraph-checkpoint-postgres 3.1.0 API (MIT)."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from langgraph.checkpoint.postgres import PostgresSaver


class OpenGaussSaver(PostgresSaver):
    SELECT_SQL = "\nselect\n    thread_id,\n    checkpoint,\n    checkpoint_ns,\n    checkpoint_id,\n    parent_checkpoint_id,\n    metadata,\n    (\n        select json_agg(array[encode(bl.channel::bytea,'base64'), encode(bl.type::bytea,'base64'), encode(bl.blob,'base64')])\n        from jsonb_each_text(checkpoint -> 'channel_versions')\n        inner join checkpoint_blobs bl\n            on bl.thread_id = checkpoints.thread_id\n            and bl.checkpoint_ns = checkpoints.checkpoint_ns\n            and bl.channel = jsonb_each_text.key\n            and bl.version = jsonb_each_text.value\n    ) as channel_values,\n    (\n        select\n        json_agg(array[encode(cw.task_id::text::bytea,'base64'), encode(cw.channel::bytea,'base64'), encode(cw.type::bytea,'base64'), encode(cw.blob,'base64')] order by cw.task_id, cw.idx)\n        from checkpoint_writes cw\n        where cw.thread_id = checkpoints.thread_id\n            and cw.checkpoint_ns = checkpoints.checkpoint_ns\n            and cw.checkpoint_id = checkpoints.checkpoint_id\n    ) as pending_writes\nfrom checkpoints "
    UPSERT_CHECKPOINT_BLOBS_SQL = "\n    INSERT INTO checkpoint_blobs (thread_id, checkpoint_ns, channel, version, type, blob)\n    VALUES (%s, %s, %s, %s, %s, %s)\n    ON DUPLICATE KEY UPDATE NOTHING\n"
    UPSERT_CHECKPOINTS_SQL = "\n    INSERT INTO checkpoints (thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id, checkpoint, metadata)\n    VALUES (%s, %s, %s, %s, %s, %s)\n    ON DUPLICATE KEY UPDATE\n        checkpoint = EXCLUDED.checkpoint,\n        metadata = EXCLUDED.metadata;\n"
    UPSERT_CHECKPOINT_WRITES_SQL = "\n    INSERT INTO checkpoint_writes (thread_id, checkpoint_ns, checkpoint_id, task_id, task_path, idx, channel, type, blob)\n    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)\n    ON DUPLICATE KEY UPDATE\n        channel = EXCLUDED.channel,\n        type = EXCLUDED.type,\n        blob = EXCLUDED.blob;\n"
    INSERT_CHECKPOINT_WRITES_SQL = "\n    INSERT INTO checkpoint_writes (thread_id, checkpoint_ns, checkpoint_id, task_id, task_path, idx, channel, type, blob)\n    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)\n    ON DUPLICATE KEY UPDATE NOTHING\n"
    SELECT_PENDING_SENDS_SQL = """
        SELECT checkpoint_id,
            json_agg(array[encode(type::bytea,'base64'),encode(blob,'base64')]
                     ORDER BY task_path,task_id,idx) AS sends
        FROM checkpoint_writes
        WHERE thread_id=%s AND checkpoint_id=ANY(%s) AND channel='__pregel_tasks'
        GROUP BY checkpoint_id
    """

    def __init__(self, conn: Any, **kwargs: Any) -> None:
        super().__init__(conn, **kwargs)
        self.supports_pipeline = False

    def setup(self) -> None:
        # No CONCURRENTLY: the complete fresh baseline is one atomic transaction.
        with self.lock:
            from langgraph.checkpoint.postgres import _internal

            with (
                _internal.get_connection(self.conn) as conn,
                conn.transaction(),
                conn.cursor() as cur,
            ):
                cur.execute("SET LOCAL lockwait_timeout = 5000")
                cur.execute("SELECT pg_advisory_xact_lock(%s)", (0x686D636B,))
                cur.execute(
                    "SELECT c.relname, obj_description(c.oid,'pg_class') AS marker "
                    "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE n.nspname=current_schema() AND c.relkind='r' "
                    "AND c.relname IN ('checkpoint_migrations','checkpoints','checkpoint_blobs','checkpoint_writes')"
                )
                rows = cur.fetchall()
                if rows:
                    markers = {r["marker"] for r in rows if r["relname"] == "checkpoint_migrations"}
                    if len(rows) != 4 or markers != {"octop-checkpoint:opengauss:1"}:
                        raise RuntimeError(
                            "Existing checkpoints are not a complete openGauss baseline"
                        )
                    cur.execute("SELECT v FROM checkpoint_migrations")
                    if cur.fetchall() != [{"v": 1}]:
                        raise RuntimeError("Unsupported openGauss checkpoint version")
                else:
                    cur.execute(
                        Path(__file__).with_name("checkpoint-baseline.sql").read_text(),
                        prepare=False,
                    )
                    cur.execute(
                        "COMMENT ON TABLE checkpoint_migrations IS 'octop-checkpoint:opengauss:1'"
                    )
                    cur.execute("INSERT INTO checkpoint_migrations(v) VALUES (1)")

    @staticmethod
    def _decode(rows: Any) -> list:
        return [
            tuple(base64.b64decode(v) if v is not None else None for v in row)
            for row in (rows or [])
        ]

    def _load_checkpoint_tuple(self, value: Any) -> Any:
        value = dict(value)
        for key in ("channel_values", "pending_writes"):
            value[key] = [
                tuple(base64.b64decode(v) if isinstance(v, str) else v for v in row)
                for row in (value[key] or [])
            ]
        return super()._load_checkpoint_tuple(value)

    def _migrate_pending_sends(
        self, pending_sends: Any, checkpoint: Any, channel_values: Any
    ) -> None:
        super()._migrate_pending_sends(self._decode(pending_sends), checkpoint, channel_values)
