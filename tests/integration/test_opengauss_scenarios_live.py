"""Opt-in synthetic business workflows with real Harness, LLM and openGauss.

Requires the same private provider file as test_opengauss_live, with a chat
provider and a remote embedding provider. Never connects an IM channel.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import uuid
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.integration.test_opengauss_control_plane import isolated_database
from tests.integration.test_opengauss_live import _record
from tests.support.app import octop_client, write_octop_config
from tests.support.auth import auth_header, bootstrap_admin, create_user
from tests.support.http import chat_ws_path, ws_connect

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("OCTOP_TEST_OPENGAUSS") != "1"
        or not os.environ.get("OCTOP_LIVE_PROVIDER_FILE"),
        reason="requires explicit openGauss and private live-provider configuration",
    ),
]


@pytest.fixture
def scenario(tmp_path, monkeypatch):
    rows = json.loads(Path(os.environ["OCTOP_LIVE_PROVIDER_FILE"]).read_text(encoding="utf-8"))
    with isolated_database(tmp_path) as (pool, config):
        _record("scenario_database_created", database=config.database)
        for key in tuple(os.environ):
            if key.startswith("OCTOP_DATABASE_"):
                monkeypatch.delenv(key)
        home = tmp_path / "app-home"
        home.mkdir()
        monkeypatch.setenv("OCTOP_HOME", str(home))
        monkeypatch.delenv("OCTOP_BASE_PATH", raising=False)
        write_octop_config(
            home,
            default_timezone="Asia/Shanghai",
            database={
                "driver": "opengauss",
                "host": config.host,
                "port": config.port,
                "database": config.database,
                "user": config.user,
                "password": config.password,
            },
        )
        home.joinpath("config.json").chmod(0o600)
        state = SimpleNamespace(
            home=home,
            pool=pool,
            database=config.database,
            db_config=config,
            rows=rows,
            ref=f"{os.environ['OCTOP_LIVE_PROVIDER_NAME']}/{os.environ['OCTOP_LIVE_MODEL']}",
            initialized=False,
            agents=[],
            providers={},
        )
        try:
            yield state
        finally:
            from octop_memory.storage.backends.postgres_pool import _registry

            from octop.api.common.memory_client import invalidate_cached_memory

            for agent_id in state.agents:
                invalidate_cached_memory(agent_id)
            leases = [
                {"purpose": key[0], "references": entry.refs}
                for key, entry in _registry.items()
                if dict(key[1]).get("dbname") == config.database
            ]
            _record("scenario_shutdown", database=config.database, pool_leases=leases)
            assert leases == [], "scenario left memory or checkpoint pool leases"
    _record("scenario_database_cleaned", database=state.database)


@asynccontextmanager
async def _start(state):
    async with octop_client(state.home, patch_llm=False) as (client, server):
        assert server.services.db.dialect == state.db_config.driver
        if not state.initialized:
            await bootstrap_admin(client, state.home)
        admin = await auth_header(client)
        if not state.initialized:
            selections = {
                os.environ["OCTOP_LIVE_PROVIDER_NAME"]: os.environ["OCTOP_LIVE_MODEL"],
                os.environ.get("OCTOP_LIVE_EMBED_PROVIDER_NAME", "集团ai"): os.environ.get(
                    "OCTOP_LIVE_EMBED_MODEL", "bge-m3"
                ),
            }
            for name, model in selections.items():
                source = next(row for row in state.rows if row["name"] == name and row["enabled"])
                entry = next(m for m in json.loads(source["models_json"]) if m["id"] == model)
                response = await client.post(
                    "/api/admin/providers",
                    headers=admin,
                    json={
                        "name": name,
                        "kind": source["kind"],
                        "base_url": source["base_url"],
                        "api_key": source["api_key"],
                        "extra_json": source["extra_json"],
                        "models": [{**entry, "enabled": True}],
                    },
                )
                assert response.status_code == 201
                state.providers[name] = response.json()["id"]
            state.initialized = True
        yield client, server, admin


async def _agent(state, client, auth, name, tools=()):
    from octop.infra.agents.settings.tool_catalog import BUILTIN_TOOL_CATALOG

    root = state.home / "workspaces" / uuid.uuid4().hex
    response = await client.post(
        "/api/agents",
        headers=auth,
        json={
            "name": name,
            "default_model": state.ref,
            "max_tokens": 2048,
            "max_iters": 80,
            "config": {
                "backend": {"type": "filesystem", "root_dir": str(root)},
                "tools_disabled": [
                    tool.name for tool in BUILTIN_TOOL_CATALOG if tool.name not in tools
                ],
                "memory": {"memory_enabled": False, "extract_on_session_end": False},
            },
        },
    )
    assert response.status_code == 201, response.text
    agent_id = response.json()["agent_id"]
    state.agents.append(agent_id)
    return agent_id


async def _thread(client, auth, agent_id):
    response = await client.post(f"/api/agents/{agent_id}/threads", headers=auth)
    assert response.status_code < 300, response.text
    return response.json()["thread_id"]


async def _chat(client, auth, agent_id, thread_id, prompt, kb_ids=()):
    async with ws_connect(client._octop_app, chat_ws_path(agent_id, auth)) as ws:
        await ws.send_json(
            {
                "type": "user_turn",
                "thread_id": thread_id,
                "text": prompt,
                "skills": [],
                "mcp_servers": [],
                "knowledge_base_ids": list(kb_ids),
                "reasoning_mode": "disabled",
                "conversation_mode": "craft",
            }
        )
        frames = await asyncio.wait_for(ws.drain_turn(timeout=120), timeout=180)
    assert frames[-1]["type"] == "done", "real Harness turn did not finish"
    assert not any(frame.get("type") == "error" for frame in frames)
    answer = "".join(str(f.get("content", "")) for f in frames if f.get("type") == "token")
    tools = " ".join(str(f.get("name") or "") for f in frames if f.get("type") == "tool_call_chunk")
    return answer, tools


async def test_users_concurrent_threads_and_lifecycle(scenario):
    state = scenario
    async with _start(state) as (client, server, admin):
        alice = await create_user(client, admin, username="scenario_alice")
        bob = await create_user(client, admin, username="scenario_bob")
        aid = await _agent(state, client, alice, "Alice 项目助理")
        bid = await _agent(state, client, bob, "Bob 项目助理")
        first = await _thread(client, alice, aid)
        second = await _thread(client, alice, aid)
        third = await _thread(client, bob, bid)
        cases = [
            (alice, aid, first, "OG_ALICE_RED_731"),
            (alice, aid, second, "OG_ALICE_BLUE_942"),
            (bob, bid, third, "OG_BOB_GREEN_586"),
        ]
        replies = await asyncio.gather(
            *(
                _chat(
                    client,
                    auth,
                    agent,
                    thread,
                    f"本会话项目代号为 {marker}。只回复该代号，不要调用工具。",
                )
                for auth, agent, thread, marker in cases
            )
        )
        for (auth, agent, thread, marker), (answer, _tools) in zip(cases, replies, strict=True):
            assert marker in answer
            history = await client.get(
                f"/api/agents/{agent}/threads/{thread}/history", headers=auth
            )
            assert history.status_code == 200 and marker in history.text
            assert all(other[3] not in history.text for other in cases if other[3] != marker)
            assert (
                type(server.app_runtime.agent_registry.get_agent(agent)).__name__
                != "FakeHarnessAgent"
            )
        for path in (
            f"/api/agents/{aid}",
            f"/api/agents/{aid}/threads/{first}/history",
            f"/api/agents/{aid}/workspace/tree",
        ):
            denied = await client.get(path, headers=bob)
            assert denied.status_code in {403, 404}, path
        denied = await client.patch(
            f"/api/agents/{aid}/threads/{first}", headers=bob, json={"title": "越权修改"}
        )
        assert denied.status_code in {403, 404}
        visible = await client.get("/api/agents", headers=bob)
        assert visible.status_code == 200 and aid not in visible.text
        denied = await client.post("/api/admin/providers", headers=bob, json={"name": "forbidden"})
        assert denied.status_code == 403
        renamed = await client.patch(
            f"/api/agents/{aid}/threads/{first}",
            headers=alice,
            json={"title": "红队验收📋", "pinned": True},
        )
        assert renamed.status_code == 200
        _record("S01_S02", ok=True, concurrent_turns=3, separate_users=2)
    async with _start(state) as (client, _server, _admin):
        alice = await auth_header(client, username="scenario_alice")
        listing = await client.get(f"/api/agents/{aid}/threads", headers=alice)
        saved = next(row for row in listing.json() if row["thread_id"] == first)
        assert saved["title"] == "红队验收📋" and saved["pinned"] is True
        deleted = await client.delete(f"/api/agents/{aid}/threads/{second}", headers=alice)
        assert deleted.status_code == 204
        gone = await client.get(f"/api/agents/{aid}/threads/{second}/history", headers=alice)
        assert gone.status_code == 404
        retained = await client.get(f"/api/agents/{aid}/threads/{first}/history", headers=alice)
        assert retained.status_code == 200 and "OG_ALICE_RED_731" in retained.text
        with state.pool.connect() as conn:
            assert (
                conn.execute(
                    "SELECT count(*) FROM threads WHERE thread_id=?", (second,)
                ).fetchone()[0]
                == 0
            )
            assert (
                conn.execute("SELECT count(*) FROM threads WHERE thread_id=?", (first,)).fetchone()[
                    0
                ]
                == 1
            )
        _record("S05", ok=True, metadata_survives_restart=True, deleted_thread_absent=True)


async def test_attachment_tools_archive_and_restart(scenario):
    state = scenario
    async with _start(state) as (client, _server, admin):
        aid = await _agent(state, client, admin, "附件核算助理", tools={"read_file", "write_file"})
        thread = await _thread(client, admin, aid)
        uploads = []
        for content in (
            "项目,金额\n合成交通,12.50\n合成文具,7.25\n",
            "独立附件，请勿覆盖第一份。\n",
        ):
            response = await client.post(
                f"/api/agents/{aid}/upload",
                headers=admin,
                files={"file": ("费用明细.csv", content.encode(), "text/csv")},
            )
            assert response.status_code == 200, response.text
            item = response.json()
            uploads.append(item)
            downloaded = await client.get(item["access_url"], headers=admin)
            assert downloaded.status_code == 200 and downloaded.content == content.encode()
            await asyncio.sleep(0.01)
        assert uploads[0]["workspace_path"] != uploads[1]["workspace_path"]
        prompt = (
            f"请实际使用 read_file 读取 {uploads[0]['workspace_path']}，计算金额合计。"
            "然后实际使用 write_file 写入 outbound/核算结果.txt，内容包含合计金额及代号 OG_FILES_482。"
            "不要初始化工作区，不要调用其他工具。"
        )
        answer, tools = await _chat(client, admin, aid, thread, prompt)
        assert "read_file" in tools and "write_file" in tools, tools
        output_url = f"/api/agents/{aid}/workspace/download"
        output = await client.get(
            output_url, headers=admin, params={"path": "outbound/核算结果.txt"}
        )
        assert output.status_code == 200, output.text
        assert "19.75" in output.text and "OG_FILES_482" in output.text
        original = output.content
        archive = await client.get(f"/api/agents/{aid}/workspace/archive", headers=admin)
        assert archive.status_code == 200
        with zipfile.ZipFile(io.BytesIO(archive.content)) as zipped:
            assert zipped.read("outbound/核算结果.txt") == original
        removed = await client.delete(
            f"/api/agents/{aid}/workspace/file",
            headers=admin,
            params={"path": "outbound/核算结果.txt"},
        )
        assert removed.status_code in {200, 204}
        missing = await client.get(
            output_url, headers=admin, params={"path": "outbound/核算结果.txt"}
        )
        assert missing.status_code == 404
        restored = await client.post(
            f"/api/agents/{aid}/workspace/archive?mode=merge",
            headers=admin,
            files={"file": ("synthetic-workspace.zip", archive.content, "application/zip")},
        )
        assert restored.status_code == 200, restored.text
        output = await client.get(
            output_url, headers=admin, params={"path": "outbound/核算结果.txt"}
        )
        assert output.status_code == 200 and output.content == original
        _record(
            "S03_S04",
            ok=True,
            tools=tools,
            same_name_attachments=2,
            workspace_archive_roundtrip=True,
        )
    async with _start(state) as (client, _server, admin):
        saved = await client.get(
            output_url, headers=admin, params={"path": "outbound/核算结果.txt"}
        )
        assert saved.status_code == 200 and saved.content == original
        history = await client.get(f"/api/agents/{aid}/threads/{thread}/history", headers=admin)
        assert history.status_code == 200 and "核算结果" in history.text
        answer, _tools = await _chat(
            client,
            admin,
            aid,
            thread,
            "刚才核算的费用合计是多少，报告代号是什么？从会话历史回答，不要使用工具。",
        )
        assert "19.75" in answer and "OG_FILES_482" in answer
        _record("S06", ok=True, real_chat_after_restart=True, workspace_bytes_preserved=True)


async def _wait_job(client, auth, agent_id, cron_id):
    async with asyncio.timeout(150):
        while True:
            response = await client.get(f"/api/agents/{agent_id}/cron/{cron_id}", headers=auth)
            assert response.status_code == 200
            row = response.json()
            if row["last_status"] in {"ok", "error"}:
                assert row["last_status"] == "ok", row.get("last_error")
                assert row["last_run_at"] is not None
                return row
            await asyncio.sleep(0.2)


async def test_cron_real_agent_and_restart(scenario):
    state = scenario
    async with _start(state) as (client, server, admin):
        aid = await _agent(state, client, admin, "定时日报助理")
        created = await client.post(
            f"/api/agents/{aid}/cron",
            headers=admin,
            json={
                "name": "合成日报任务",
                "trigger": "interval:86400",
                "task_type": "agent",
                "prompt": "这是合成定时日报验收。只回复 OG_CRON_863，不要使用工具。",
                "enabled": False,
            },
        )
        assert created.status_code == 201, created.text
        job = created.json()
        cron_id = job["id"]
        invalid = await client.patch(
            f"/api/agents/{aid}/cron/{cron_id}", headers=admin, json={"trigger": "broken-trigger"}
        )
        assert invalid.status_code == 400
        started = await client.post(f"/api/agents/{aid}/cron/{cron_id}/run-now", headers=admin)
        assert started.status_code == 204
        finished = await _wait_job(client, admin, aid, cron_id)
        assert finished["trigger"] == "interval:86400" and finished["enabled"] is False
        registry = server.app_runtime.gateway.thread_registry
        thread = registry.get_bound_thread_id(job["session_key"])
        assert thread
        history = await client.get(f"/api/agents/{aid}/threads/{thread}/history", headers=admin)
        assert history.status_code == 200
        messages = (
            history.json().get("messages", [])
            if isinstance(history.json(), dict)
            else history.json()
        )
        assert any(
            m.get("role") == "assistant" and "OG_CRON_863" in json.dumps(m) for m in messages
        )
        _record(
            "S07",
            ok=True,
            task_type="agent",
            status=finished["last_status"],
            real_assistant_history=True,
        )
    async with _start(state) as (client, _server, admin):
        response = await client.get(f"/api/agents/{aid}/cron/{cron_id}", headers=admin)
        assert response.status_code == 200
        assert response.json()["enabled"] is False and response.json()["last_status"] == "ok"
        timezone = await client.get("/api/settings/timezone", headers=admin)
        assert timezone.status_code == 200 and timezone.json()["timezone"] == "Asia/Shanghai"
        deleted = await client.delete(f"/api/agents/{aid}/cron/{cron_id}", headers=admin)
        assert deleted.status_code == 204
        _record("S08", ok=True, paused_job_survives_restart=True, timezone="Asia/Shanghai")


async def _wait_document(client, auth, kb_id, doc_id):
    async with asyncio.timeout(90):
        while True:
            response = await client.get(f"/api/knowledge-bases/{kb_id}/documents", headers=auth)
            assert response.status_code == 200
            row = next(row for row in response.json() if row["id"] == doc_id)
            assert row["status"] != "failed", row.get("error_message")
            if row["status"] == "ready":
                assert row["chunk_count"] > 0
                return row
            await asyncio.sleep(0.2)


def _record_knowledge_vector_destination(state, kb_id, doc_id, stage, dimension):
    """Inspect actual persisted vectors; never substitute an index or provider."""
    import sqlite3

    from octop.infra.knowledge.index import KnowledgeIndex

    index = KnowledgeIndex(kb_id)
    assert index.path.is_relative_to(state.home)
    with sqlite3.connect(index.path) as conn:
        header = conn.execute("PRAGMA integrity_check").fetchone()[0]
        rows = conn.execute(
            "SELECT length(embedding) FROM chunks WHERE doc_id=? ORDER BY ordinal", (doc_id,)
        ).fetchall()
    assert header == "ok" and rows
    assert dimension > 0 and all(row[0] == dimension * 4 for row in rows)
    with state.pool.connect() as conn:
        columns = conn.execute(
            "SELECT n.nspname,c.relname,a.attname,t.typname FROM pg_attribute a "
            "JOIN pg_class c ON c.oid=a.attrelid "
            "JOIN pg_namespace n ON n.oid=c.relnamespace "
            "JOIN pg_type t ON t.oid=a.atttypid "
            "WHERE a.attnum>0 AND NOT a.attisdropped AND c.relkind IN ('r','p') "
            "AND n.nspname NOT IN ('pg_catalog','information_schema') "
            "AND t.typname IN ('vector','sparsevec','halfvec') "
            "ORDER BY n.nspname,c.relname,a.attname"
        ).fetchall()
    _record(
        "knowledge_vector_destination",
        checkpoint=stage,
        driver=state.db_config.driver,
        database=state.database,
        actual_index_type=type(index).__name__,
        vector_storage="sqlite_sidecar",
        index_path=str(index.path.relative_to(state.home)),
        document_chunks=len(rows),
        embedding_dim=dimension,
        sqlite_integrity=header,
        database_vector_columns=[list(row.values()) for row in columns],
        database_vector_integration_accepted=False,
    )


async def test_remote_embedding_rag_revision_and_restart(scenario):
    from octop.infra.knowledge.retrieve import retrieve_context

    state = scenario
    async with _start(state) as (client, server, admin):
        embed_name = os.environ.get("OCTOP_LIVE_EMBED_PROVIDER_NAME", "集团ai")
        feature = await client.put(
            "/api/knowledge-bases/feature",
            headers=admin,
            json={
                "enabled": True,
                "backend": "remote",
                "provider_id": str(state.providers[embed_name]),
                "model": os.environ.get("OCTOP_LIVE_EMBED_MODEL", "bge-m3"),
            },
        )
        assert feature.status_code == 200 and feature.json()["usable"] is True
        owner = await create_user(client, admin, username="kb_scenario_owner")
        peer = await create_user(client, admin, username="kb_scenario_peer")
        aid = await _agent(state, client, owner, "制度问答助理", tools={"search_knowledge"})
        created = await client.post(
            "/api/knowledge-bases",
            headers=owner,
            json={
                "name": "海蓝七号合成差旅制度",
                "description": "合成项目差旅住宿上限",
                "shared": False,
            },
        )
        assert created.status_code == 201, created.text
        kb_id = created.json()["id"]
        source = "# 海蓝七号合成差旅制度\n本制度仅用于自动验收。海蓝七号项目每晚住宿报销上限为 683 元。\n"
        uploaded = await client.post(
            f"/api/knowledge-bases/{kb_id}/documents",
            headers=owner,
            files={"upload": ("差旅制度.md", source.encode(), "text/markdown")},
        )
        assert uploaded.status_code == 201, uploaded.text
        doc_id = uploaded.json()["id"]
        await _wait_document(client, owner, kb_id, doc_id)
        base = await client.get(f"/api/knowledge-bases/{kb_id}", headers=owner)
        assert base.status_code == 200 and base.json()["embedding_dim"] > 0
        dimension = base.json()["embedding_dim"]
        await asyncio.get_running_loop().run_in_executor(
            None, _record_knowledge_vector_destination, state, kb_id, doc_id, "uploaded", dimension
        )
        owner_id = server.services.user_repo.get_by_username("kb_scenario_owner").id
        peer_id = server.services.user_repo.get_by_username("kb_scenario_peer").id
        context = await retrieve_context(
            server.services,
            user_id=owner_id,
            is_admin=False,
            query="海蓝七号住宿报销上限",
            knowledge_base_ids=[kb_id],
        )
        assert "683" in context and "octop-kb-citations" in context and doc_id in context
        denied = await client.put(
            f"/api/knowledge-bases/{kb_id}/documents/{doc_id}/content",
            headers=peer,
            json={"content": "未经授权修改"},
        )
        assert denied.status_code in {403, 404}
        hidden = await retrieve_context(
            server.services,
            user_id=peer_id,
            is_admin=False,
            query="住宿上限",
            knowledge_base_ids=[kb_id],
        )
        assert hidden == ""
        thread = await _thread(client, owner, aid)
        answer, tools = await _chat(
            client,
            owner,
            aid,
            thread,
            "请实际调用 search_knowledge 检索海蓝七号制度，住宿报销上限是多少元？只依据检索结果回答并注明文档。",
            kb_ids=[kb_id],
        )
        assert "search_knowledge" in tools and "683" in answer, (answer, tools)
        history = await client.get(f"/api/agents/{aid}/threads/{thread}/history", headers=owner)
        assert history.status_code == 200 and doc_id in history.text
        _record(
            "S09_S10",
            ok=True,
            embedding_dim=base.json()["embedding_dim"],
            tools=tools,
            citations=True,
        )
        revised = source.replace("683", "927")
        changed = await client.put(
            f"/api/knowledge-bases/{kb_id}/documents/{doc_id}/content",
            headers=owner,
            json={"content": revised},
        )
        assert changed.status_code == 200, changed.text
        await _wait_document(client, owner, kb_id, doc_id)
        context = await retrieve_context(
            server.services,
            user_id=owner_id,
            is_admin=False,
            query="住宿上限",
            knowledge_base_ids=[kb_id],
        )
        assert "927" in context and "683" not in context
    async with _start(state) as (client, server, _admin):
        owner = await auth_header(client, username="kb_scenario_owner")
        await asyncio.get_running_loop().run_in_executor(
            None,
            _record_knowledge_vector_destination,
            state,
            kb_id,
            doc_id,
            "revised_and_restarted",
            dimension,
        )
        new_thread = await _thread(client, owner, aid)
        answer, tools = await _chat(
            client,
            owner,
            aid,
            new_thread,
            "请实际调用 search_knowledge 查询海蓝七号项目当前住宿报销上限是多少元？依据最新制度回答。",
            kb_ids=[kb_id],
        )
        assert "search_knowledge" in tools and "927" in answer and "683" not in answer, (
            answer,
            tools,
        )
        removed = await client.delete(
            f"/api/knowledge-bases/{kb_id}/documents/{doc_id}", headers=owner
        )
        assert removed.status_code in {200, 204}
        context = await retrieve_context(
            server.services,
            user_id=owner_id,
            is_admin=False,
            query="住宿上限",
            knowledge_base_ids=[kb_id],
        )
        assert context == "", "deleted document remained retrievable"
        _record(
            "S11_S12",
            ok=True,
            revision_survives_restart=True,
            private_kb_isolation=True,
            deleted_not_retrieved=True,
        )


async def test_knowledge_sidecar_and_metadata_backup_restore(scenario, tmp_path, monkeypatch):
    """Restore DB metadata and the original SQLite vectors as one system backup."""
    import shutil
    import tempfile

    from octop.infra.backup.system_archive import create_system_backup, restore_system_backup
    from octop.infra.knowledge.retrieve import retrieve_context
    from octop.infra.utils.paths import PathLayout

    if not shutil.which("gs_dump") or not shutil.which("gs_restore"):
        pytest.skip("matching native openGauss clients required")
    state = scenario
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    async with _start(state) as (client, server, admin):
        feature = await client.put(
            "/api/knowledge-bases/feature",
            headers=admin,
            json={
                "enabled": True,
                "backend": "remote",
                "provider_id": str(state.providers[os.environ["OCTOP_LIVE_EMBED_PROVIDER_NAME"]]),
                "model": os.environ["OCTOP_LIVE_EMBED_MODEL"],
            },
        )
        assert feature.status_code == 200 and feature.json()["usable"] is True
        created = await client.post(
            "/api/knowledge-bases", headers=admin, json={"name": "知识库混合存储灾备验收"}
        )
        assert created.status_code == 201
        kb_id = created.json()["id"]
        uploaded = await client.post(
            f"/api/knowledge-bases/{kb_id}/documents",
            headers=admin,
            files={
                "upload": ("policy.md", "海蓝七号每晚住宿上限为683元。".encode(), "text/markdown")
            },
        )
        assert uploaded.status_code == 201
        doc_id = uploaded.json()["id"]
        await _wait_document(client, admin, kb_id, doc_id)
        dimension = server.services.knowledge_repo.get_base(kb_id).embedding_dim
        user_id = server.services.user_repo.get_by_username("admin").id
    archive = tmp_path / "knowledge-system.tar.gz"
    await asyncio.to_thread(
        create_system_backup,
        paths=PathLayout(state.home),
        agent_rows=[],
        pool=state.pool,
        db_config=state.db_config,
        dest=archive,
    )
    archive.chmod(0o600)
    async with _start(state) as (client, server, admin):
        removed = await client.delete(
            f"/api/knowledge-bases/{kb_id}/documents/{doc_id}", headers=admin
        )
        assert removed.status_code in {200, 204}
        assert server.services.knowledge_repo.get_document(doc_id) is None
        empty = await retrieve_context(
            server.services,
            user_id=user_id,
            is_admin=True,
            query="海蓝七号住宿上限",
            knowledge_base_ids=[kb_id],
        )
        assert empty == ""
    result = await asyncio.to_thread(
        restore_system_backup,
        archive,
        paths=PathLayout(state.home),
        pool=state.pool,
        db_config=state.db_config,
        restore_config=False,
        preserve_users=False,
    )
    assert result["database_driver"] == "opengauss" and result["knowledge_files"] > 0
    async with _start(state) as (_client, server, _admin):
        document = server.services.knowledge_repo.get_document(doc_id)
        assert document is not None and document.status == "ready"
        await asyncio.to_thread(
            _record_knowledge_vector_destination, state, kb_id, doc_id, "backup_restored", dimension
        )
        restored = await retrieve_context(
            server.services,
            user_id=user_id,
            is_admin=True,
            query="海蓝七号住宿上限",
            knowledge_base_ids=[kb_id],
        )
        assert "683" in restored and doc_id in restored
        _record(
            "knowledge_sidecar_backup_restore",
            ok=True,
            metadata_restored=True,
            sqlite_vectors_restored=True,
            third_party_embedding=True,
        )


async def test_system_backup_restore_then_real_chat(scenario, tmp_path, monkeypatch):
    import shutil
    import tempfile

    from octop.infra.backup.system_archive import create_system_backup, restore_system_backup
    from octop.infra.db.repos.agents import AgentRepo
    from octop.infra.utils.paths import PathLayout

    if not shutil.which("gs_dump") or not shutil.which("gs_restore"):
        pytest.skip("matching native openGauss clients required")
    state = scenario
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    params = {"path": "交付结果.txt"}
    async with _start(state) as (client, _server, admin):
        aid = await _agent(state, client, admin, "灾备验收助理")
        thread = await _thread(client, admin, aid)
        answer, _tools = await _chat(
            client,
            admin,
            aid,
            thread,
            "本会话的灾备代号是 OG_RESTORE_254。只回复该代号，不要使用工具。",
        )
        assert "OG_RESTORE_254" in answer
        file_url = f"/api/agents/{aid}/workspace/file"
        written = await client.put(
            file_url,
            headers=admin,
            params=params,
            json={"content": "备份前的合成交付结果 OG_RESTORE_254"},
        )
        assert written.status_code == 200
    paths = PathLayout(state.home)
    archive = tmp_path / "synthetic-system.tar.gz"
    await asyncio.to_thread(
        create_system_backup,
        paths=paths,
        agent_rows=AgentRepo(state.pool).list_all(),
        pool=state.pool,
        db_config=state.db_config,
        dest=archive,
        include_chats=True,
    )
    archive.chmod(0o600)
    async with _start(state) as (client, _server, admin):
        changed = await client.put(
            file_url, headers=admin, params=params, json={"content": "备份后的变更必须被还原"}
        )
        assert changed.status_code == 200
        after_snapshot = await _thread(client, admin, aid)
    result = await asyncio.to_thread(
        restore_system_backup,
        archive,
        paths=paths,
        pool=state.pool,
        db_config=state.db_config,
        restore_config=False,
        preserve_users=False,
    )
    assert result["database_driver"] == "opengauss" and result["chats_restored"] is True
    async with _start(state) as (client, _server, admin):
        saved = await client.get(file_url, headers=admin, params=params)
        assert saved.status_code == 200 and "OG_RESTORE_254" in saved.json()["content"]
        assert "备份后的变更" not in saved.json()["content"]
        absent = await client.get(
            f"/api/agents/{aid}/threads/{after_snapshot}/history", headers=admin
        )
        assert absent.status_code == 404
        history = await client.get(f"/api/agents/{aid}/threads/{thread}/history", headers=admin)
        assert history.status_code == 200 and "OG_RESTORE_254" in history.text
        answer, _tools = await _chat(
            client, admin, aid, thread, "这段会话的灾备代号是什么？只回复代号，不要使用工具。"
        )
        assert "OG_RESTORE_254" in answer
        _record(
            "S14",
            ok=True,
            native_dump_restore=True,
            chat_restored=True,
            workspace_restored=True,
            post_snapshot_thread_absent=True,
            real_chat_after_restore=True,
        )
