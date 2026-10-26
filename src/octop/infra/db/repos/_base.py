"""Helpers shared across repo implementations."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping, Sequence
from typing import Any, Protocol, TypeVar

R_co = TypeVar("R_co", covariant=True)

UNSET = object()

# Row mapping returned by the DB driver (sqlite3.Row today; asyncpg.Record later).
DbRow = Mapping[str, Any]


class _FromRow(Protocol[R_co]):
    @classmethod
    def from_row(cls, row: DbRow) -> R_co: ...


def now_ts() -> int:
    return int(time.time())


def bool_int(value: bool) -> int:
    return 1 if value else 0


def map_rows[R_co](rows: Sequence[DbRow], cls: type[_FromRow[R_co]]) -> list[R_co]:
    return [cls.from_row(r) for r in rows]


def partial_updates(pairs: list[tuple[str, object | None]]) -> tuple[list[str], list[object]]:
    """Build SET clauses for optional fields (None = skip)."""
    clauses: list[str] = []
    params: list[object] = []
    for col, val in pairs:
        if val is None:
            continue
        clauses.append(f"{col} = ?")
        params.append(val)
    return clauses, params


def optional_updates(
    pairs: list[tuple[str, object]],
    *,
    sentinel: object = UNSET,
) -> tuple[list[str], list[object]]:
    """Build SET clauses; sentinel means the field was omitted from the patch."""
    clauses: list[str] = []
    params: list[object] = []
    for col, val in pairs:
        if val is sentinel:
            continue
        clauses.append(f"{col} = ?")
        params.append(val)
    return clauses, params


def sql_in_placeholders(count: int) -> str:
    """``?, ?, ?`` for IN (...) clauses; swap ``?`` → ``%s`` when adding PostgreSQL."""
    return ", ".join("?" * count)


def sql_unix_day_bucket(column: str, *, dialect: str = "sqlite") -> str:
    """Expression that buckets a unix-epoch integer column into YYYY-MM-DD."""
    if dialect in {"postgresql", "opengauss"}:
        return f"to_char(to_timestamp({column}), 'YYYY-MM-DD')"
    return f"date({column}, 'unixepoch')"


def insert_returning_id(conn: Any, sql: str, params: Sequence[object]) -> int:
    """Run INSERT … RETURNING id (SQLite 3.35+ and PostgreSQL)."""
    row = conn.execute(f"{sql} RETURNING id", params).fetchone()
    if row is None:
        raise RuntimeError("INSERT RETURNING id returned no row")
    if isinstance(row, Mapping):
        return int(row["id"])
    return int(row[0])


def sql_upsert(dialect: str, target: str, assignments: str = "") -> str:
    """Conflict clause for tables with one supplied unique key.

    openGauss cannot select a conflict target. Multi-unique tables must use
    ``opengauss_upsert_by_key`` instead. All arguments are internal SQL literals.
    """
    if dialect == "opengauss":
        return " ON DUPLICATE KEY UPDATE " + (assignments or "NOTHING")
    clause = f" ON CONFLICT({target})" if target else " ON CONFLICT"
    return clause + (f" DO UPDATE SET {assignments}" if assignments else " DO NOTHING")


def opengauss_key_lock(conn: Any, table: str, values: Sequence[object]) -> None:
    """Transaction-scoped lock for a possibly absent logical row.

    A stable signed 64-bit digest avoids relying on PostgreSQL-only hash
    functions. Hash collisions only serialize unrelated writers.
    """
    raw = json.dumps([table, *values], ensure_ascii=False, separators=(",", ":"))
    key = int.from_bytes(hashlib.sha256(raw.encode()).digest()[:8], "big", signed=True)
    conn.execute("SELECT pg_advisory_xact_lock(?)", (key,))


def opengauss_upsert_by_key(
    conn: Any,
    table: str,
    values: Mapping[str, object],
    key_columns: Sequence[str],
    update_columns: Sequence[str],
    *,
    condition: str = "",
    condition_params: Sequence[object] = (),
) -> Any:
    """Preserve an exact conflict target on a table with multiple unique keys.

    Must run inside a transaction; all upsert writers of this logical key must
    use the same lock. Other unique violations are intentionally propagated.
    Null key parts never conflict under ordinary UNIQUE semantics.
    """
    key_values = [values[column] for column in key_columns]
    opengauss_key_lock(conn, table, key_values)
    where = " AND ".join(f"{column} = ?" for column in key_columns)
    existing = conn.execute(
        f"SELECT 1 FROM {table} WHERE {where} FOR UPDATE", key_values
    ).fetchone()
    if existing is not None:
        assignment = ", ".join(f"{column} = ?" for column in update_columns)
        params = [values[column] for column in update_columns] + key_values + list(condition_params)
        suffix = f" AND ({condition})" if condition else ""
        return conn.execute(f"UPDATE {table} SET {assignment} WHERE {where}{suffix}", params)
    columns = ", ".join(values)
    placeholders = sql_in_placeholders(len(values))
    return conn.execute(
        f"INSERT INTO {table}({columns}) VALUES ({placeholders})", list(values.values())
    )
