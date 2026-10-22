"""Persistent lifecycle tests with real repositories, registry and Cron delivery."""

from __future__ import annotations

import asyncio
import datetime as dt
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest
from langchain_core.messages import ToolMessage
from langchain_core.tools import StructuredTool

from octop.api.routers.cron import CronPatchBody
from octop.infra.cron.analysis.middleware import AnalysisToolMiddleware, failed_result, permitted
from octop.infra.cron.analysis.runtime import (
    AnalysisError,
    analysis_scope,
    check_execution,
    current_run,
)
from octop.infra.cron.analysis.service import AnalysisItem
from octop.infra.cron.analysis.tools import build_analysis_tools
from octop.infra.cron.delivery import CronDeliveryService
from octop.infra.cron.manager import CronManager
from octop.infra.cron.task_type import CRON_PROMPT_MAX_LEN
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.services import RepoBundle
from octop.infra.gateway.threads import ThreadRegistry


def test_observation_defaults_to_server_time(monkeypatch):
    monkeypatch.setattr("octop.infra.cron.analysis.service.time.time", lambda: 1792557395)
    item = AnalysisItem(key="A", version="v1", status="pending")
    assert item.observed_at == 1792557395
    assert AnalysisItem(key="A", version="v1", status="pending", observed_at=100).observed_at == 100


@pytest.fixture
def env(tmp_path):
    db = SqlitePool(tmp_path / "analysis.db")
    run_migrations(db)
    repos = RepoBundle.from_pool(db)
    uid = repos.user_repo.create(username="analyst", password_hash="x", role="user")
    repos.agent_repo.create(agent_id="agent", user_id=uid, name="Assistant")
    registry = ThreadRegistry(session_repo=repos.session_repo, thread_repo=repos.thread_repo)
    session_key = ThreadRegistry.dashboard_key(agent_id="agent", user_id=uid)
    tid = registry.create_thread(
        agent_id="agent", user_id=uid, channel_type="dashboard", session_key=session_key
    )
    repos.session_repo.upsert(
        session_key=session_key,
        agent_id="agent",
        user_id=uid,
        channel_type="dashboard",
        chat_type="dm",
        thread_id=tid,
        channel_metadata={"keep": "metadata"},
    )
    gateway = MagicMock()
    gateway.thread_registry = registry
    gateway.require_session.side_effect = lambda aid, key: repos.session_repo.get(key)

    async def locked(aid, key, operation):
        await operation()

    gateway.run_in_session = locked
    gateway.push_session_text = AsyncMock()
    gateway.notify_dashboard_push = AsyncMock()
    agents = MagicMock()
    agents.get_row.return_value = repos.agent_repo.get("agent")
    agents.default_mcp_servers.return_value = []
    agents.merge_turn_mcp_servers.return_value = []
    delivery = CronDeliveryService(gateway=gateway, agent_manager=agents, repos=repos)
    manager = CronManager(gateway=gateway, delivery_service=delivery, repos=repos)
    manager._scheduler = MagicMock()
    manager._scheduler.get_job.return_value = None
    repos.cron_repo.create(
        cron_id="scan",
        agent_id="agent",
        user_id=uid,
        prompt="Use sync_analysis_items to scan important work.",
        trigger="interval:600",
        session_key=session_key,
        task_type="agent",
    )
    run = manager.analysis.make_run("scan", tid, session_key)
    yield SimpleNamespace(
        db=db,
        repos=repos,
        registry=registry,
        gateway=gateway,
        agents=agents,
        delivery=delivery,
        manager=manager,
        service=manager.analysis,
        run=run,
        uid=uid,
        tid=tid,
    )
    db.close()


def item(key="A", version="v1", status="pending", observed_at=100):
    return AnalysisItem(key=key, version=version, status=status, observed_at=observed_at)


async def sync(env, items):
    return await env.service.sync("source/company", items, complete=True, offset=0, limit=100)


@pytest.mark.asyncio
async def test_dispatch_limit_leaves_remaining_items_for_next_scan(env):
    observations = [item(key=f"work-{index}") for index in range(6)]
    with analysis_scope(env.run):
        records = await sync(env, observations)
        for record in records[:5]:
            assert (await dispatch(env, record))["status"] == "created"
        blocked = await dispatch(env, records[5])
        assert blocked["reason_code"] == "analysis.limit"
        assert blocked["cron_id"] is None

    parent = env.manager.get("scan")
    session = env.repos.session_repo.get(parent.session_key)
    next_run = env.service.make_run("scan", session.thread_id, parent.session_key)
    with analysis_scope(next_run):
        records = await sync(env, observations)
        for record in records[:5]:
            assert (await dispatch(env, record))["status"] == "waiting"
        assert next_run.dispatched == 0
        assert (await dispatch(env, records[5]))["status"] == "created"
        assert next_run.dispatched == 1


async def dispatch(env, record):
    return await env.service.dispatch(
        record["item_id"],
        record["version"],
        "Analysis " + record["key"],
        "Read details and generate a checklist.",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("unit", ["中", '"', "\\", "\n"])
async def test_dispatch_checks_complete_prompt_before_reserving_and_keeps_it_editable(env, unit):
    with analysis_scope(env.run):
        record = (await sync(env, [item()]))[0]
        previous = await dispatch(env, record)
        previous_job = env.manager.get(previous["cron_id"])
        env.repos.cron_repo.set_run_status(previous["cron_id"], ts=101, status="ok", error=None)
        env.repos.analysis_repo.finish(previous["cron_id"], success=True)
        record = (await sync(env, [item(version="v2", observed_at=200)]))[0]
        stored = env.repos.analysis_repo.get(record["item_id"])
        scheduled_count = env.manager._scheduler.add_job.call_count
        dispatched_count = env.run.dispatched

        payload = json.loads(previous_job.prompt)
        payload.update(version="v2", observed_at=200, instructions="")
        budget = CRON_PROMPT_MAX_LEN - len(json.dumps(payload, ensure_ascii=False))
        width = len(json.dumps(unit, ensure_ascii=False)) - 2
        instructions = unit * (budget // width) + "中" * (budget % width)
        tool = next(
            t for t in build_analysis_tools(env.service) if t.name == "dispatch_analysis_task"
        )
        assert tool.args["instructions"]["maxLength"] == CRON_PROMPT_MAX_LEN
        args = {
            "item_id": record["item_id"],
            "expected_version": record["version"],
            "title": "Analysis A",
        }
        for oversized in (instructions + "中", "中" * CRON_PROMPT_MAX_LEN):
            blocked = json.loads(await tool.ainvoke({**args, "instructions": oversized}))
            assert blocked["status"] == "blocked"
            assert blocked["reason_code"] == "analysis.prompt_too_long"
            assert str(CRON_PROMPT_MAX_LEN) in blocked["reason"]
            assert env.repos.analysis_repo.get(record["item_id"]) == stored
            assert env.run.dispatched == dispatched_count
            assert env.manager._scheduler.add_job.call_count == scheduled_count
            assert len(env.manager.list_by_agent("agent")) == 2

        accepted = json.loads(await tool.ainvoke({**args, "instructions": instructions}))
        assert accepted["status"] == "created"
        assert accepted["thread_url"] == previous["thread_url"]
        job = env.manager.get(accepted["cron_id"])
        assert len(job.prompt) == CRON_PROMPT_MAX_LEN
        assert json.loads(job.prompt)["instructions"] == instructions
        assert CronPatchBody(prompt=job.prompt).prompt == job.prompt


@pytest.mark.asyncio
@pytest.mark.parametrize("timezone", ["Asia/Shanghai", "America/New_York"])
async def test_dispatch_persists_schedule_in_server_timezone(env, timezone):
    env.manager._timezone = timezone
    with analysis_scope(env.run):
        record = (await sync(env, [item()]))[0]
        before = dt.datetime.now(dt.UTC)
        await dispatch(env, record)
        after = dt.datetime.now(dt.UTC)
    stored = env.repos.analysis_repo.get(record["item_id"])
    job = env.repos.cron_repo.get(stored["analysis_cron_id"])
    run_at = dt.datetime.fromisoformat(job.trigger.removeprefix("date:"))
    assert run_at.utcoffset() == before.astimezone(ZoneInfo(timezone)).utcoffset()
    assert before + dt.timedelta(seconds=3) <= run_at <= after + dt.timedelta(seconds=3)
    scheduled = env.manager._scheduler.add_job.call_args.kwargs["trigger"]
    assert scheduled.run_date.isoformat() == run_at.isoformat()


@pytest.mark.asyncio
async def test_two_items_deduplicate_keep_conversation_and_handle_changes(env):
    with analysis_scope(env.run):
        records = await sync(env, [item(), item("B")])
        results = await asyncio.gather(*(dispatch(env, record) for record in records))
        assert [r["status"] for r in results] == ["created", "created"]
        assert len({r["thread_url"] for r in results}) == 2
        duplicate = await dispatch(env, records[0])
        assert duplicate["status"] == "waiting"
        assert len(env.manager.list_by_agent("agent")) == 3
        a = env.repos.analysis_repo.get(records[0]["item_id"])
        env.repos.cron_repo.set_run_status(a["analysis_cron_id"], ts=101, status="ok", error=None)
        env.repos.analysis_repo.finish(a["analysis_cron_id"], success=True)
        assert (await dispatch(env, records[0]))["status"] == "existing"
        original_thread = env.repos.thread_repo.get(a["thread_id"])
        env.registry.set_title_if_null(a["thread_id"], "Unchanged")
        updated = await sync(
            env, [item(version="v2", observed_at=200), item("B", status="closed", observed_at=200)]
        )
        new_a = await dispatch(env, updated[0])
        assert new_a["status"] == "created"
        assert new_a["thread_url"] == results[0]["thread_url"]
        assert env.repos.thread_repo.get(a["thread_id"]).title == original_thread.title
        assert (await dispatch(env, updated[1]))["status"] == "blocked"
        # An older child is still identifiable after the latest reference changes.
        old = env.manager.analysis.make_run(a["analysis_cron_id"], a["thread_id"], a["session_key"])
        assert old.role == "analysis"
        with pytest.raises(AnalysisError):
            env.service.guard(old)


@pytest.mark.asyncio
async def test_read_only_empty_and_failed_observations_do_not_close_history(env):
    with analysis_scope(env.run):
        assert (
            await env.service.sync("source/company", None, complete=False, offset=0, limit=100)
            == []
        )
        assert env.manager.get("scan").session_key == env.run.session_key
        await sync(env, [])
        fixed = env.repos.session_repo.get(env.run.session_key)
        assert fixed.channel_metadata["keep"] == "metadata"
        assert "analysis-overview" in fixed.session_key
        records = await sync(env, [item()])
        await sync(env, [])
        assert (await sync(env, None))[0]["status"] == "pending"
        with pytest.raises(AnalysisError):
            await env.service.sync(
                "source/company", [item(status="closed")], complete=False, offset=0, limit=100
            )
        env.run.failed = True
        with pytest.raises(AnalysisError):
            await sync(env, [item(status="closed")])
    assert env.repos.analysis_repo.get(records[0]["item_id"])["business_status"] == "pending"
    with pytest.raises(AnalysisError):
        await sync(env, [])


@pytest.mark.asyncio
async def test_version_changes_while_running_never_claim_new_version_success(env):
    with analysis_scope(env.run):
        record = (await sync(env, [item()]))[0]
        result = await dispatch(env, record)
        row = env.repos.analysis_repo.get(record["item_id"])
        child = env.service.make_run(result["cron_id"], row["thread_id"], row["session_key"])
        with analysis_scope(child):
            check_execution()
        updated = (await sync(env, [item(version="v2", observed_at=200)]))[0]
        assert (await dispatch(env, updated))["status"] == "waiting"
        env.repos.analysis_repo.finish(result["cron_id"], success=True)
        latest = env.repos.analysis_repo.get(record["item_id"])
        assert latest["analysis_version"] == "v1"
        assert latest["last_success_version"] is None
        assert (await dispatch(env, updated))["status"] == "created"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["parent_disabled", "child_disabled", "closed", "thread_deleted", "target_changed"]
)
async def test_guard_after_wait_rejects_changed_lifecycle(env, change):
    with analysis_scope(env.run):
        record = (await sync(env, [item()]))[0]
        result = await dispatch(env, record)
    row = env.repos.analysis_repo.get(record["item_id"])
    child = env.service.make_run(result["cron_id"], row["thread_id"], row["session_key"])
    child.guard()  # Valid before waiting for the existing conversation lock.
    if change == "parent_disabled":
        env.repos.cron_repo.update("scan", enabled=False)
    elif change == "child_disabled":
        env.repos.cron_repo.update(result["cron_id"], enabled=False)
    elif change == "closed":
        with analysis_scope(env.run):
            await sync(env, [item(status="closed", observed_at=200)])
    elif change == "thread_deleted":
        env.registry.delete_thread(row["thread_id"])
    else:
        env.repos.cron_repo.update(result["cron_id"], session_key=env.run.session_key)
    with analysis_scope(child), pytest.raises(AnalysisError):
        check_execution()
    assert env.repos.analysis_repo.get(record["item_id"])["dispatch_state"] == "queued"


@pytest.mark.asyncio
async def test_failure_retry_is_reserved_and_interrupted_run_not_replayed(env):
    with analysis_scope(env.run):
        record = (await sync(env, [item()]))[0]
        result = await dispatch(env, record)
    cron_id = result["cron_id"]
    env.repos.cron_repo.set_run_status(cron_id, ts=101, status="error", error="source offline")
    env.repos.analysis_repo.finish(cron_id, success=False)
    env.repos.cron_repo.update(cron_id, enabled=False)
    with analysis_scope(env.run):
        assert (await dispatch(env, record))["status"] == "existing"
    env.service.retry(cron_id)
    assert env.repos.analysis_repo.for_cron(cron_id)["dispatch_state"] == "queued"
    with pytest.raises(AnalysisError):
        env.service.retry(cron_id)
    assert (
        env.service.present(env.repos.analysis_repo.for_cron(cron_id))["dispatch_state"] == "queued"
    )
    env.repos.analysis_repo.transition(cron_id, "queued", "running")
    env.service.recover()
    assert not env.manager.get(cron_id).enabled
    assert env.repos.analysis_repo.for_cron(cron_id)["dispatch_state"] == "interrupted"


@pytest.mark.asyncio
async def test_deletion_and_failed_creation_preserve_diagnostic_state(env, monkeypatch):
    with analysis_scope(env.run):
        record = (await sync(env, [item()]))[0]
        monkeypatch.setattr(env.manager, "create", AsyncMock(side_effect=RuntimeError("disk full")))
        with pytest.raises(RuntimeError):
            await dispatch(env, record)
        assert env.repos.analysis_repo.get(record["item_id"])["dispatch_state"] == "preparing"
        assert (await dispatch(env, record))["status"] == "waiting"
    env.repos.cron_repo.delete("scan")
    assert env.repos.analysis_repo.get(record["item_id"])["parent_cron_id"] is None
    env.repos.user_repo.delete(env.uid)
    assert env.repos.analysis_repo.get(record["item_id"]) is None


def test_fingerprint_ignores_volatile_time_but_detects_business_changes():
    def fingerprint(hours, node):
        return AnalysisItem(
            key="OPEN_123",
            status="pending",
            observed_at=100,
            fields={"taskId": "OPEN_123", "node": node, "pendingHours": hours},
        ).fingerprint()

    assert fingerprint(1, "review") == fingerprint(2, "review")
    assert fingerprint(1, "review") != fingerprint(1, "done")


@pytest.mark.asyncio
async def test_tool_policy_is_per_invocation_and_cannot_be_bypassed(env):
    tool = StructuredTool.from_function(
        func=lambda: "ok", name="source_query", description="Query", metadata={"readOnlyHint": True}
    )
    assert permitted(tool, "analysis")
    assert not permitted(tool.model_copy(update={"metadata": {}}), "analysis")
    assert not permitted(SimpleNamespace(name="dispatch_analysis_task"), "analysis")
    assert not permitted(SimpleNamespace(name="cronjob_create"), "scan")
    middleware = AnalysisToolMiddleware()
    request = SimpleNamespace(tool_call={"id": "call1", "name": "source_approve"})
    handler = AsyncMock(return_value=ToolMessage(content="approved", tool_call_id="call1"))
    with analysis_scope(env.run):
        result = await middleware.awrap_tool_call(request, handler)
        assert result.status == "error"
        assert env.run.failed
        handler.assert_not_awaited()
    await middleware.awrap_tool_call(request, handler)
    handler.assert_awaited_once()
    assert current_run() is None


@pytest.mark.asyncio
async def test_failed_analysis_cannot_generate_or_deliver_success(env):
    middleware = AnalysisToolMiddleware()
    env.run.failed = True
    env.run.allowed_tools.add("send_file_to_user")
    model_handler = AsyncMock()
    tool_handler = AsyncMock()
    with analysis_scope(env.run):
        with pytest.raises(AnalysisError, match="failed|失败"):
            await middleware.awrap_model_call(SimpleNamespace(), model_handler)
        with pytest.raises(AnalysisError, match="failed|失败"):
            await middleware.awrap_tool_call(
                SimpleNamespace(tool_call={"id": "file", "name": "send_file_to_user"}),
                tool_handler,
            )
    model_handler.assert_not_awaited()
    tool_handler.assert_not_awaited()
    # The failed background invocation must not poison later user interaction.
    await middleware.awrap_model_call(SimpleNamespace(), model_handler)
    model_handler.assert_awaited_once()


@pytest.mark.parametrize(
    "content",
    [
        '{"isSuccess":false}',
        '{"isError":true}',
        '{"error":"timeout"}',
        '{"success":false}',
        '[{"type":"text","text":"{\\"isSuccess\\":false}"}]',
    ],
)
def test_business_and_protocol_failure_envelopes(content):
    assert failed_result(ToolMessage(content=content, tool_call_id="x"))
    assert not failed_result(ToolMessage(content='{"isSuccess":true,"data":[]}', tool_call_id="x"))


@pytest.mark.asyncio
async def test_tools_accept_real_structured_contract(env):
    tools = {tool.name: tool for tool in build_analysis_tools(env.service)}
    with analysis_scope(env.run):
        output = await tools["sync_analysis_items"].ainvoke(
            {"source_key": "source/company", "items": [item().model_dump()], "complete": True}
        )
    assert "item_id" in output


@pytest.mark.asyncio
async def test_real_thread_lock_checks_after_user_turn_not_before_only(env, tmp_path):
    from octop.infra.agents.manager import AgentManager
    from octop.infra.utils.paths import PathLayout

    agents = AgentManager(repos=env.repos, paths=PathLayout(tmp_path))
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = []

    async def stream(aid, request):
        calls.append(request["thread_id"])
        entered.set()
        await release.wait()
        yield {"type": "token", "content": "user reply"}

    agents._harness_manager = SimpleNamespace(stream=stream)
    with analysis_scope(env.run):
        record = (await sync(env, [item()]))[0]
        result = await dispatch(env, record)
    row = env.repos.analysis_repo.get(record["item_id"])

    async def user_turn():
        return [chunk async for chunk in agents.stream("agent", {"thread_id": row["thread_id"]})]

    user = asyncio.create_task(user_turn())
    await asyncio.wait_for(entered.wait(), 2)
    child = env.service.make_run(result["cron_id"], row["thread_id"], row["session_key"])

    async def background():
        with analysis_scope(child):
            return [
                chunk async for chunk in agents.stream("agent", {"thread_id": row["thread_id"]})
            ]

    task = asyncio.create_task(background())
    await asyncio.sleep(0)
    env.repos.cron_repo.update("scan", enabled=False)
    release.set()
    await user
    with pytest.raises(AnalysisError):
        await task
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, "tool", "push", "error_chunk"])
async def test_cron_execution_saves_final_status_before_item_completion(env, failure):
    with analysis_scope(env.run):
        record = (await sync(env, [item()]))[0]
        result = await dispatch(env, record)
    cron_id = result["cron_id"]
    env.agents.default_knowledge_base_ids.return_value = []

    async def stream(aid, request):
        check_execution()
        assert env.repos.analysis_repo.for_cron(cron_id)["dispatch_state"] == "running"
        if failure == "tool":
            yield {"type": "tool_call_chunk", "name": "query"}
            yield {"type": "tool_result", "name": "query", "status": "error", "content": "timeout"}
        if failure == "error_chunk":
            yield {"type": "error", "message": "model unavailable"}
        yield {"type": "token", "content": "Analysis and [checklist](workspace/checklist.md)"}

    env.agents.stream = stream
    if failure == "push":
        env.gateway.push_session_text.side_effect = RuntimeError("push unavailable")
    original = env.repos.analysis_repo.finish

    def finish(cid, *, success):
        assert env.manager.get(cid).last_status == ("ok" if success else "error")
        original(cid, success=success)

    env.repos.analysis_repo.finish = finish
    await env.manager._run_user_job(cron_id)
    row = env.repos.analysis_repo.for_cron(cron_id)
    assert row["dispatch_state"] == "finished"
    assert row["last_success_version"] == ("v1" if failure is None else None)
    assert env.manager.get(cron_id).last_status == ("ok" if failure is None else "error")
    assert not env.manager.get(cron_id).enabled
    if failure in {"tool", "error_chunk"}:
        env.gateway.push_session_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_concurrent_dispatch_same_item_creates_one_job(env):
    with analysis_scope(env.run):
        record = (await sync(env, [item()]))[0]
        results = await asyncio.gather(dispatch(env, record), dispatch(env, record))
    assert sorted(r["status"] for r in results) == ["created", "waiting"]
    assert len(env.manager.list_by_agent("agent")) == 2


@pytest.mark.asyncio
async def test_missed_job_retry_does_not_consume_old_terminal_status(env):
    with analysis_scope(env.run):
        record = (await sync(env, [item()]))[0]
        result = await dispatch(env, record)
    env.repos.cron_repo.mark_missed(result["cron_id"])
    assert (
        env.service.present(env.repos.analysis_repo.get(record["item_id"]))["dispatch_state"]
        == "finished"
    )
    env.service.retry(result["cron_id"])
    assert (
        env.service.present(env.repos.analysis_repo.get(record["item_id"]))["dispatch_state"]
        == "queued"
    )


@pytest.mark.asyncio
async def test_failed_new_version_retains_previous_success_and_deleted_chat_stays_deleted(env):
    with analysis_scope(env.run):
        record = (await sync(env, [item()]))[0]
        first = await dispatch(env, record)
        env.repos.analysis_repo.finish(first["cron_id"], success=True)
        updated = (await sync(env, [item(version="v2", observed_at=200)]))[0]
        second = await dispatch(env, updated)
        env.repos.analysis_repo.finish(second["cron_id"], success=False)
        row = env.repos.analysis_repo.get(record["item_id"])
        assert row["last_success_version"] == "v1"
        env.registry.delete_thread(row["thread_id"])
        updated = (await sync(env, [item(version="v3", observed_at=300)]))[0]
        assert (await dispatch(env, updated))["status"] == "blocked"
        assert env.repos.analysis_repo.get(record["item_id"])["thread_id"] is None


def test_legacy_mcp_whitelist_is_exact_and_never_grants_recursive_dispatch():
    query = SimpleNamespace(name="legacy_query", metadata={"readOnlyHint": False})
    assert permitted(query, "analysis", frozenset({"legacy_query"}))
    assert not permitted(query, "analysis", frozenset({"legacy_*"}))
    for name in ("cronjob_create", "task", "dispatch_analysis_task", "send_to_session"):
        assert not permitted(SimpleNamespace(name=name, metadata={}), "analysis", frozenset({name}))


@pytest.mark.asyncio
async def test_deleted_completed_cron_is_not_recreated_for_new_version(env):
    with analysis_scope(env.run):
        record = (await sync(env, [item()]))[0]
        first = await dispatch(env, record)
        env.repos.analysis_repo.finish(first["cron_id"], success=True)
        await env.manager.delete(first["cron_id"])
        updated = (await sync(env, [item(version="v2", observed_at=200)]))[0]
        outcome = await dispatch(env, updated)
        assert outcome["status"] == "blocked"
        assert outcome["needs_review"] is True
        assert len(env.manager.list_by_agent("agent")) == 1
