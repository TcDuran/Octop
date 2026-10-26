"""Fresh-install baseline and subsequent openGauss-only migrations.

Historical PostgreSQL migrations and repair helpers must not run on openGauss.
Each SQL file is executed intact so dollar-quoted function bodies remain valid.
"""

from __future__ import annotations

import re
from pathlib import Path

from octop.infra.db.pool import DatabasePool

BASELINE_VERSION = 22
_ROOT = Path(__file__).parent
_MIGRATIONS = _ROOT / "migrations"
_BASELINE = _ROOT / "022_baseline.sql"
_SCHEMA_MARKER = "octop:opengauss:baseline22"
_BASELINE_TABLES = frozenset(
    [
        "_schema_version",
        "agents",
        "audit_log",
        "automation_analysis_items",
        "bridge_connections",
        "care_push_records",
        "channels",
        "connector_oauth_states",
        "connectors",
        "cron_jobs",
        "knowledge_bases",
        "knowledge_documents",
        "proactive_care_config",
        "providers",
        "published_experts",
        "secrets",
        "sessions",
        "settings",
        "skill_packages",
        "sso_login_states",
        "sso_providers",
        "storage_backends",
        "thread_history_projection",
        "thread_messages",
        "threads",
        "trajectory_events",
        "usage_log",
        "user_invites",
        "user_policies",
        "user_role",
        "user_sso_identities",
        "users",
        "voice_providers",
    ]
)


def discover_migrations() -> list[tuple[int, Path]]:
    found: dict[int, Path] = {}
    for path in sorted(_MIGRATIONS.glob("*.sql")):
        match = re.fullmatch(r"(\d{3})_.+\.sql", path.name)
        if match is None:
            raise RuntimeError(f"Invalid openGauss migration filename: {path.name}")
        version = int(match[1])
        if version <= BASELINE_VERSION or version in found:
            raise RuntimeError(f"Invalid or duplicate openGauss migration version: {version}")
        found[version] = path
    versions = sorted(found)
    if versions != list(range(BASELINE_VERSION + 1, BASELINE_VERSION + 1 + len(versions))):
        raise RuntimeError("openGauss migrations must have consecutive versions after baseline 22")
    return [(version, found[version]) for version in versions]


def run_opengauss_migrations(db: DatabasePool) -> None:
    migrations = discover_migrations()
    latest = migrations[-1][0] if migrations else BASELINE_VERSION
    with db.transaction() as conn:
        # Serialize first installs and upgrades across processes, including absent tables.
        conn.execute("SELECT pg_advisory_xact_lock(7873836140333222)")
        conn.execute("SET LOCAL search_path = public, pg_catalog")
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
            ).fetchall()
        }
        if "_schema_version" not in tables:
            if tables:
                raise RuntimeError("openGauss initialization requires an empty public schema")
            with conn.cursor() as cursor:
                cursor.execute(_BASELINE.read_text(encoding="utf-8"), prepare=False)
            version = BASELINE_VERSION
        else:
            rows = conn.execute("SELECT version FROM public._schema_version").fetchall()
            if len(rows) != 1:
                raise RuntimeError("Invalid openGauss schema version metadata")
            version = int(rows[0][0])
        if not BASELINE_VERSION <= version <= latest:
            raise RuntimeError(
                f"Unsupported openGauss schema version {version}; supported 22–{latest}"
            )
        marker = conn.execute(
            "SELECT obj_description('public._schema_version'::regclass, 'pg_class')"
        ).fetchone()[0]
        if marker != _SCHEMA_MARKER:
            raise RuntimeError("Database was not initialized with the openGauss baseline")
        if version == BASELINE_VERSION and "_schema_version" in tables:
            missing = _BASELINE_TABLES - tables
            if missing:
                raise RuntimeError(
                    f"Incomplete openGauss baseline; missing tables: {sorted(missing)}"
                )
        for next_version, path in migrations:
            if next_version <= version:
                continue
            with conn.cursor() as cursor:
                cursor.execute(path.read_text(encoding="utf-8"), prepare=False)
            conn.execute("UPDATE public._schema_version SET version = ?", (next_version,))
