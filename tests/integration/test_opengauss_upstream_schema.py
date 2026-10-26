"""Opt-in current-upstream schema contracts using OID-recorded disposable databases."""

import json
import os

import pytest

from octop.infra.db.migrate import run_migrations
from octop.infra.db.repos.resource_group_grants import ResourceGroupGrantRepo
from octop.infra.db.repos.settings import SettingsRepo
from octop.infra.db.repos.user_group_members import UserGroupMemberRepo
from octop.infra.db.repos.user_groups import UserGroupRepo
from octop.infra.db.repos.users import UserRepo
from tests.integration.test_database_shared_live import isolated_postgresql
from tests.integration.test_opengauss_control_plane import isolated_database

pytestmark = pytest.mark.skipif(
    os.environ.get("OCTOP_TEST_OPENGAUSS") != "1", reason="requires isolated database opt-in"
)


@pytest.fixture
def og(tmp_path):
    with isolated_database(tmp_path) as database:
        yield database


@pytest.fixture(params=["postgresql", "opengauss"])
def shared_database(request, tmp_path):
    factory = isolated_postgresql if request.param == "postgresql" else isolated_database
    with factory(tmp_path) as database:
        yield database


def test_retained_baseline_upgrades_to_current_schema(og):
    from octop.infra.db.opengauss import migrate

    pool, _config = og
    with pool.transaction() as conn, conn.cursor() as cursor:
        cursor.execute(migrate._BASELINE.read_text(encoding="utf-8"), prepare=False)
    uid = UserRepo(pool).create(username="upgrade-retained", role="admin")
    SettingsRepo(pool).set("upgrade-canary", "retained")
    with pool.connect() as conn:
        assert conn.execute("SELECT version FROM _schema_version").fetchone()[0] == 22
    run_migrations(pool)
    run_migrations(pool)
    assert UserRepo(pool).get(uid).username == "upgrade-retained"
    assert SettingsRepo(pool).get("upgrade-canary") == "retained"
    with pool.connect() as conn:
        assert conn.execute("SELECT version FROM _schema_version").fetchone()[0] == 25
        columns = {
            (row[0], row[1])
            for row in conn.execute(
                "SELECT table_name,column_name FROM information_schema.columns "
                "WHERE table_schema='public'"
            ).fetchall()
        }
    assert SettingsRepo(pool).get("granted_bridge_permission") == "1"
    assert SettingsRepo(pool).get("expanded_page_tab_permissions_v1") == "1"
    with pool.connect() as conn:
        permissions = json.loads(
            conn.execute("SELECT permissions FROM user_role WHERE user_role_id='user'").fetchone()[
                0
            ]
        )
    assert {"bridge", "skill_builtin", "skill_market", "connector_enabled"} <= set(permissions)
    with pool.transaction() as conn:
        conn.execute(
            "UPDATE user_role SET permissions=? WHERE user_role_id='user'", ('["knowledge_bases"]',)
        )
    run_migrations(pool)
    with pool.connect() as conn:
        assert json.loads(
            conn.execute("SELECT permissions FROM user_role WHERE user_role_id='user'").fetchone()[
                0
            ]
        ) == ["knowledge_bases"]
    assert {
        ("skill_packages", "shared"),
        ("knowledge_bases", "icon_url"),
        ("bridge_connections", "icon_url"),
        ("bridge_connections", "shared"),
        ("user_group", "enabled"),
        ("user_group", "avatar_icon"),
    } <= columns


def test_group_membership_and_resource_grants(shared_database):
    pool, _config = shared_database
    run_migrations(pool)
    users = UserRepo(pool)
    uid = users.create(username="group-member", role="user")
    groups = UserGroupRepo(pool)
    members = UserGroupMemberRepo(pool)
    grants = ResourceGroupGrantRepo(pool)
    group = groups.create(name="数据库验证组", description="合成测试")
    assert group.enabled and group.avatar_icon is None
    members.add(group.user_group_id, uid)
    members.add(group.user_group_id, uid)
    assert members.group_ids_for_user(uid) == {group.user_group_id}
    assert members.user_ids_for_group(group.user_group_id) == [uid]
    grants.replace("knowledge_bases", "synthetic-kb", [group.user_group_id])
    assert grants.group_ids("knowledge_bases", "synthetic-kb") == {group.user_group_id}
    groups.update(group.user_group_id, enabled=False, avatar_icon="team", update_avatar_icon=True)
    updated = groups.get(group.user_group_id)
    assert not updated.enabled and updated.avatar_icon == "team"
    run_migrations(pool)
    assert members.counts_by_group() == {group.user_group_id: 1}
    assert groups.delete(group.user_group_id)
    assert members.group_ids_for_user(uid) == set()
    assert grants.group_ids("knowledge_bases", "synthetic-kb") == set()
    assert users.get(uid).username == "group-member"


def test_current_schema_matches_postgresql(og, tmp_path, monkeypatch):
    from tests.integration.test_opengauss_control_plane import (
        test_schema_matches_postgresql_baseline,
    )

    reference = tmp_path / "pg-schema-reference"
    reference.mkdir()
    with isolated_postgresql(reference) as (pool, config):
        run_migrations(pool)
        monkeypatch.setenv("OCTOP_TEST_BASELINE_POSTGRES_URL", config.postgresql_conninfo())
        test_schema_matches_postgresql_baseline(og)
