"""Keep openGauss configuration and migration discovery isolated from PostgreSQL."""

import json

import pytest
from psycopg.conninfo import conninfo_to_dict

from octop.config import DatabaseConfig, OctopConfig, load_config
from octop.infra.db import factory
from octop.infra.db.migrate import _discover
from octop.infra.db.opengauss import migrate
from octop.infra.db.pool import OpenGaussPool
from octop.infra.utils.paths import PathLayout


def test_explicit_driver_retained_with_postgres_wire_url(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"database": {"driver": "sqlite"}}))
    monkeypatch.setenv("OCTOP_DATABASE_DRIVER", "opengauss")
    monkeypatch.setenv("OCTOP_DATABASE_URL", "postgresql://tester:abc%23def@db:15434/octop")
    cfg = load_config(path)
    assert cfg.database.is_opengauss
    assert not cfg.database.is_sqlite
    assert not cfg.database.is_postgresql
    params = conninfo_to_dict(cfg.database.postgresql_conninfo())
    assert params["dbname"] == "octop"
    assert params["password"] == "abc#def"


def test_factory_does_not_fall_back_to_sqlite(tmp_path, monkeypatch):
    created = []
    monkeypatch.setattr(OpenGaussPool, "__init__", lambda self, dsn: created.append(dsn))
    cfg = OctopConfig(database=DatabaseConfig(driver="opengauss"))
    paths = PathLayout(tmp_path)
    assert not factory.should_defer_control_plane_db(cfg, paths)
    pool = factory.open_database(cfg, paths)
    assert isinstance(pool, OpenGaussPool)
    assert pool.dialect == "opengauss"
    assert len(created) == 1
    assert not paths.db.exists()


def test_memory_default_uses_opengauss_backend(tmp_path):
    from octop.infra.agents.memory.backend import open_memory_kwargs

    cfg = OctopConfig(database=DatabaseConfig(driver="opengauss"))
    namespace, backend, options = open_memory_kwargs(
        agent_id="sample", cfg={}, octop_config=cfg, workspace_dir=tmp_path
    )
    assert namespace == "agent_sample"
    assert backend == "opengauss"
    assert options == {"dsn": cfg.database.postgresql_conninfo()}


def test_discovery_never_includes_postgres_history(tmp_path, monkeypatch):
    monkeypatch.setattr(migrate, "_MIGRATIONS", tmp_path)
    assert _discover("opengauss") == []
    path = tmp_path / "023_next.sql"
    path.write_text("SELECT 1;")
    assert _discover("opengauss") == [(23, path)]
    assert all(version >= 23 for version, _ in _discover("opengauss"))
    assert _discover("postgresql")[0][0] == 1


@pytest.mark.parametrize(
    "names, message",
    [
        (["024_gap.sql"], "consecutive"),
        (["022_history.sql"], "Invalid or duplicate"),
        (["023_one.sql", "023_two.sql"], "Invalid or duplicate"),
        (["next.sql"], "filename"),
        (["023_next.pg.sql"], None),
    ],
)
def test_discovery_rejects_invalid_version_chains(tmp_path, monkeypatch, names, message):
    monkeypatch.setattr(migrate, "_MIGRATIONS", tmp_path)
    for name in names:
        (tmp_path / name).write_text("SELECT 1;")
    if message:
        with pytest.raises(RuntimeError, match=message):
            migrate.discover_migrations()
    else:
        assert migrate.discover_migrations()[0][0] == 23
