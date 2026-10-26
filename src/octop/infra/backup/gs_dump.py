"""openGauss custom archives via matching native client tools on PATH.

Password is sent to the tool's stdin pipeline, never an argv or log field.
Restore is atomic; any nonzero status is a failure, including status 1.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path

import psycopg
from psycopg.conninfo import conninfo_to_dict

from octop.infra.errors import ErrorCode, OctopError


def _run(name: str, conninfo: str, args: list[str]) -> None:
    tool = shutil.which(name)
    if tool is None:
        raise OctopError(
            ErrorCode.INTERNAL_ERROR,
            f"{name} not found on PATH; required for openGauss backup/restore",
        )
    params = conninfo_to_dict(conninfo)
    database = str(params.get("dbname") or "")
    if not database:
        raise OctopError(ErrorCode.INTERNAL_ERROR, "openGauss backup requires an explicit database")
    command = [tool]
    for key, flag in (("host", "-h"), ("port", "-p"), ("user", "-U")):
        if params.get(key):
            command.extend([flag, str(params[key])])
    command.append("--pipeline")
    if name == "gs_dump":
        command.extend([*args, database])
    else:
        command.extend(["-d", database, *args])
    password = str(params.get("password") or "")
    result = subprocess.run(
        command, input=password + "\n", capture_output=True, text=True, check=False, timeout=600
    )
    if result.returncode != 0:
        message = "\n".join(part.strip() for part in (result.stderr, result.stdout) if part.strip())
        if password:
            message = message.replace(password, "***")
        raise OctopError(
            ErrorCode.INTERNAL_ERROR, f"{name} failed (status {result.returncode}): {message}"
        )


def dump_opengauss(conninfo: str, dest: Path, *, exclude_table_data: Sequence[str] = ()) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    args = ["-F", "c", "-f", str(dest), "-n", "public", "-n", "octop_memory"]
    for table in exclude_table_data:
        args.extend(["--exclude-table-data", f"public.{table}"])
    _run("gs_dump", conninfo, args)


def restore_opengauss(conninfo: str, dump_file: Path) -> None:
    # Refuse other archive formats before opening a writable restore connection.
    with dump_file.open("rb") as stream:
        if stream.read(5) != b"PGDMP":
            raise OctopError(ErrorCode.INTERNAL_ERROR, "Not an openGauss custom archive")
    with psycopg.connect(conninfo, connect_timeout=8) as conn:
        populated = (
            conn.execute(
                "SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname IN ('public','octop_memory') AND c.relkind='r' LIMIT 1"
            ).fetchone()
            is not None
        )
    args = ["--single-transaction", "--exit-on-error", "--no-owner", "--no-privileges"]
    if populated:
        args.append("--clean")
    _run("gs_restore", conninfo, [*args, str(dump_file)])
