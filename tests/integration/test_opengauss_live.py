"""Opt-in real LLM regression, using only an OID-recorded disposable database.

Set OCTOP_TEST_OPENGAUSS=1, OCTOP_LIVE_PROVIDER_FILE (private provider rows),
OCTOP_LIVE_PROVIDER_NAME and OCTOP_LIVE_MODEL. Never enables IM channels.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from tests.integration.test_opengauss_control_plane import isolated_database
from tests.support.app import octop_client, write_octop_config
from tests.support.auth import auth_header, bootstrap_admin
from tests.support.http import chat_ws_path, ws_connect

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("OCTOP_TEST_OPENGAUSS") != "1"
        or not os.environ.get("OCTOP_LIVE_PROVIDER_FILE"),
        reason="requires explicit openGauss and private live-provider configuration",
    ),
]


def _record(stage, **details):
    target = os.environ.get("OCTOP_LIVE_RESULT_FILE")
    if target:
        with Path(target).open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"stage": stage, **details}, ensure_ascii=False) + "\n")


async def _turn(client, auth, agent_id, thread_id, text):
    async with ws_connect(client._octop_app, chat_ws_path(agent_id, auth)) as ws:
        await ws.send_json(
            {
                "type": "user_turn",
                "thread_id": thread_id,
                "text": text,
                "skills": [],
                "mcp_servers": [],
                "knowledge_base_ids": [],
                "reasoning_mode": "disabled",
            }
        )
        frames = await asyncio.wait_for(ws.drain_turn(timeout=90), timeout=150)
    if frames[-1]["type"] != "done":
        _record("turn_failure", frames=frames[-3:])
    assert frames[-1]["type"] == "done", "real harness turn did not finish"
    assert not any(frame.get("type") == "error" for frame in frames)
    return frames


async def test_real_llm_chat_memory_extraction_and_restart(tmp_path, monkeypatch):
    rows = json.loads(Path(os.environ["OCTOP_LIVE_PROVIDER_FILE"]).read_text(encoding="utf-8"))
    name = os.environ["OCTOP_LIVE_PROVIDER_NAME"]
    model = os.environ["OCTOP_LIVE_MODEL"]
    source = next(row for row in rows if row["name"] == name and row["enabled"])
    entry = next(item for item in json.loads(source["models_json"]) if item["id"] == model)
    with isolated_database(tmp_path) as (pool, config):
        _record("database_created", database=config.database)
        for key in tuple(os.environ):
            if key.startswith("OCTOP_DATABASE_"):
                monkeypatch.delenv(key)
        home = tmp_path / "app-home"
        home.mkdir()
        monkeypatch.setenv("OCTOP_HOME", str(home))
        monkeypatch.delenv("OCTOP_BASE_PATH", raising=False)
        write_octop_config(
            home,
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
        ref = f"{name}/{model}"
        async with octop_client(home, patch_llm=False) as (client, server):
            assert server.services.db.dialect == "opengauss"
            await bootstrap_admin(client, home)
            auth = await auth_header(client)
            response = await client.post(
                "/api/admin/providers",
                headers=auth,
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
            from octop.infra.agents.settings.tool_catalog import BUILTIN_TOOL_CATALOG

            response = await client.post(
                "/api/agents",
                headers=auth,
                json={
                    "name": "openGauss live validation",
                    "default_model": ref,
                    "max_tokens": 2048,
                    "max_iters": 80,
                    "config": {
                        "backend": {"type": "filesystem", "root_dir": str(home)},
                        "tools_disabled": [
                            tool.name
                            for tool in BUILTIN_TOOL_CATALOG
                            if tool.name not in {"memory_save", "memory_search", "memory_get"}
                        ],
                        "memory": {
                            "memory_enabled": True,
                            "extract_on_session_end": False,
                            "aux_model": ref,
                            "extract_light_timeout_s": 90,
                            "extract_heavy_timeout_s": 90,
                            "extract_max_tokens": 4096,
                            "extract_max_retries": 0,
                            "extract_thinking": "off",
                        },
                    },
                },
            )
            assert response.status_code == 201
            agent_id = response.json()["agent_id"]
            response = await client.post(f"/api/agents/{agent_id}/threads", headers=auth)
            assert response.status_code < 300
            thread_id = response.json()["thread_id"]
            marker = "OG_LIVE_20261023_K7Q9"
            frames = await _turn(
                client,
                auth,
                agent_id,
                thread_id,
                f"这是隔离数据库验收。请记住本次临时会话代号 {marker}。"
                "不要初始化工作区，不要使用工具，只回复这个代号。",
            )
            answer = "".join(str(f.get("content", "")) for f in frames if f.get("type") == "token")
            assert marker in answer
            _record("real_chat", ok=True, frame_count=len(frames))
            agent = server.app_runtime.agent_registry.get_agent(agent_id)
            assert type(agent).__name__ != "FakeHarnessAgent"
            service = agent._memory_runtime.service
            assert service is not None and service._runtime.llm_configured
            memory = agent._memory_runtime.memory
            assert type(memory.backend).__name__ == "OpenGaussMemoryBackend"
            _record(
                "memory_vector_wiring",
                backend=type(memory.backend).__name__,
                vector_index_configured=memory.vector_index is not None,
                embedding_provider_configured=memory.embedding_provider is not None,
            )
            namespace = memory.backend._ns
            assert namespace == f"agent_{agent_id}".lower()
            # A live model may acknowledge the first request without executing it.
            # One explicit reminder is allowed; a text-only promise never passes.
            for attempt in range(2):
                frames = await _turn(
                    client,
                    auth,
                    agent_id,
                    thread_id,
                    "请现在实际执行 memory_save 工具，保存这条合成测试偏好："
                    "验收用户偏好使用中文回答，测试项目代号为海蓝七号。"
                    "仅口头承诺不算完成；不要执行其他工具，执行成功后回复已保存。",
                )
                tool_frames = [f for f in frames if f.get("type") == "tool_call_chunk"]
                tool_names = "".join(str(frame.get("name") or "") for frame in tool_frames)
                _record(
                    "tool_frames", attempt=attempt + 1, names=tool_names, chunks=len(tool_frames)
                )
                if "memory_save" in tool_names:
                    break
            assert "memory_save" in tool_names, "model did not call memory_save"
            with pool.connect() as conn:
                atoms = conn.execute(
                    "SELECT assertion FROM octop_memory.atoms "
                    "WHERE namespace=? AND deprecated_at IS NULL",
                    (namespace,),
                ).fetchall()
            assert any("海蓝七号" in row[0] for row in atoms), "tool did not persist the test atom"
            _record("real_memory_tool", ok=True)
            session = "live-extraction-only"
            captured = await asyncio.to_thread(
                service.capture_turn,
                user="合成测试用户明确要求：长期偏好为中文回复；正在推进海蓝七号验收项目。",
                assistant="已确认，这是用于数据库验收的合成信息。",
                session_id=session,
                thread_id=thread_id,
            )
            _record("capture", result=captured)
            extraction = await asyncio.wait_for(
                asyncio.to_thread(service.extract, session, promote=True, regen_pages=True),
                timeout=240,
            )
            _record("real_extraction", result=extraction)
            assert not extraction.get("failure_reason"), "live extraction reported failure"
            assert extraction.get("candidates", 0) > 0
            assert extraction["pages"]["failed"] == 0
            assert extraction["pages"]["regenerated"] > 0
            with pool.connect() as conn:
                candidate_count = conn.execute(
                    "SELECT count(*) FROM octop_memory.candidates WHERE namespace=? AND session_id=?",
                    (namespace, session),
                ).fetchone()[0]
                page_count = conn.execute(
                    "SELECT count(*) FROM octop_memory.entity_pages "
                    "WHERE namespace=? AND last_regen_at IS NOT NULL AND summary_markdown<>''",
                    (namespace,),
                ).fetchone()[0]
            assert candidate_count > 0 and page_count > 0
            _record("extraction_persistence", candidates=candidate_count, pages=page_count)
            history = await client.get(
                f"/api/agents/{agent_id}/threads/{thread_id}/history", headers=auth
            )
            assert history.status_code == 200 and marker in history.text
        _record("server_stopped", ok=True)
        _record("memory_backend_closed", closed=memory.backend._closed)
        async with octop_client(home, patch_llm=False) as (client, server):
            auth = await auth_header(client)
            history = await client.get(
                f"/api/agents/{agent_id}/threads/{thread_id}/history", headers=auth
            )
            assert history.status_code == 200 and marker in history.text
            frames = await _turn(
                client,
                auth,
                agent_id,
                thread_id,
                "上一条会话里临时会话代号是什么？只回复代号，不要使用工具。",
            )
            answer = "".join(str(f.get("content", "")) for f in frames if f.get("type") == "token")
            assert marker in answer
            counts = await client.get(f"/api/agents/{agent_id}/memory/stats/counts", headers=auth)
            assert counts.status_code == 200
            _record("restart_history_and_chat", ok=True, memory_counts=counts.json())
        from octop_memory.storage.backends.postgres_pool import _registry

        from octop.api.common.memory_client import invalidate_cached_memory

        # The stats RPC owns a separate process-local dashboard cache entry.
        invalidate_cached_memory(agent_id)
        leases = [
            {"purpose": key[0], "references": entry.refs}
            for key, entry in _registry.items()
            if dict(key[1]).get("dbname") == config.database
        ]
        _record(
            "shutdown_pool_leases",
            pools=leases,
        )
        assert not leases, "test database still has memory pool owners"
        with pool.connect() as conn:
            assert conn.execute("SELECT version FROM _schema_version").fetchone()[0] == 25
    _record("database_cleanup", ok=True)
