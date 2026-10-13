"""Unit tests for cross-session delivery tools (list_sessions / send_to_session)."""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.config import var_child_runnable_config

from octop.i18n import tr
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.agents import AgentRepo
from octop.infra.db.repos.sessions import SessionRepo
from octop.infra.db.repos.thread_messages import ThreadMessageRepo
from octop.infra.db.repos.threads import ThreadRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.gateway.gateway import Gateway
from octop.infra.gateway.threads import ThreadRegistry
from octop.infra.gateway.tools import build_session_push_tools


@contextmanager
def _configurable(**kwargs: object):
    token = var_child_runnable_config.set({"configurable": kwargs})
    try:
        yield
    finally:
        var_child_runnable_config.reset(token)


def _tool_by_name(tools: list, name: str):
    for tool in tools:
        if tool.name == name:
            return tool
    raise KeyError(name)


def _seed_session(
    registry: ThreadRegistry,
    *,
    session_key: str,
    thread_id: str,
    agent_id: str,
    user_id: int,
    channel_type: str,
    subject_id: str,
    channel_id: str | None = None,
) -> None:
    registry._threads.insert(
        thread_id=thread_id,
        agent_id=agent_id,
        user_id=user_id,
        channel_type=channel_type,
        session_key=session_key,
    )
    registry._sessions.upsert(
        session_key=session_key,
        agent_id=agent_id,
        user_id=user_id,
        channel_type=channel_type,
        chat_type="dm",
        thread_id=thread_id,
        channel_subject_id=subject_id,
        channel_chat_type="dm",
        channel_metadata={"channel_type": channel_type, "user_id": user_id},
        channel_id=channel_id,
    )


class _Env:
    def __init__(self, tmp_path: Path) -> None:
        db = SqlitePool(tmp_path / "octop.db")
        run_migrations(db)
        user_repo = UserRepo(db)
        self.owner_id = user_repo.create(username="owner", password_hash="x", role="user")
        self.other_id = user_repo.create(username="other", password_hash="x", role="user")
        agent_repo = AgentRepo(db)
        agent_repo.create(agent_id="a1", user_id=self.owner_id, name="Agent 1")
        agent_repo.create(agent_id="b1", user_id=self.owner_id, name="Agent 2")

        self.repos = MagicMock()
        self.repos.user_repo = user_repo
        self.repos.agent_repo = agent_repo
        self.repos.session_repo = SessionRepo(db)
        self.repos.thread_repo = ThreadRepo(db)
        self.repos.thread_message_repo = ThreadMessageRepo(db)

        self.gateway = Gateway(agent_manager=MagicMock(), repos=self.repos)
        self.gateway._channel_manager = MagicMock()
        self.gateway._channel_manager.get_channel = MagicMock(return_value=None)
        self.gateway._channel_manager.push_text = AsyncMock()

        async def run_in_session(_channel_id, _session_key, operation):
            await operation()

        self.gateway._channel_manager.run_in_session = run_in_session

        self.appended: list[tuple[str, list[Any]]] = []

        async def aappend_messages(thread_id: str, messages) -> list[Any]:
            kept = list(messages)
            self.appended.append((thread_id, kept))
            return kept

        self.agent_manager = MagicMock()
        self.agent_manager.get_agent = MagicMock(
            return_value=MagicMock(aappend_messages=aappend_messages)
        )

        self.sk_dash = ThreadRegistry.dashboard_key(agent_id="a1", user_id=self.owner_id)
        self.sk_wx = ThreadRegistry.make_key(
            agent_id="a1", channel_type="wechat", channel_subject_id="oWx"
        )
        self.sk_other_agent_wx = ThreadRegistry.make_key(
            agent_id="b1", channel_type="wechat", channel_subject_id="oWx"
        )
        _seed_session(
            self.gateway.thread_registry,
            session_key=self.sk_dash,
            thread_id="thr_dash",
            agent_id="a1",
            user_id=self.owner_id,
            channel_type="dashboard",
            subject_id=str(self.owner_id),
        )
        _seed_session(
            self.gateway.thread_registry,
            session_key=self.sk_wx,
            thread_id="thr_wx",
            agent_id="a1",
            user_id=self.owner_id,
            channel_type="wechat",
            subject_id="oWx",
            channel_id="ch-wx",
        )
        _seed_session(
            self.gateway.thread_registry,
            session_key=self.sk_other_agent_wx,
            thread_id="thr_b_wx",
            agent_id="b1",
            user_id=self.owner_id,
            channel_type="wechat",
            subject_id="oWx",
            channel_id="ch-b",
        )

        self.tools = build_session_push_tools(
            gateway=self.gateway,
            agent_manager=self.agent_manager,
            repos=self.repos,
        )


@pytest.fixture
def env(tmp_path: Path) -> _Env:
    return _Env(tmp_path)


@pytest.mark.asyncio
async def test_list_sessions_marks_current(env: _Env) -> None:
    listing = _tool_by_name(env.tools, "list_sessions")
    with _configurable(agent_id="a1", user=str(env.owner_id), session_key=env.sk_dash):
        rows = json.loads(await listing.ainvoke({}))
    by_key = {row["session_key"]: row for row in rows}
    assert set(by_key) == {env.sk_dash, env.sk_wx}
    assert by_key[env.sk_dash]["is_current"] is True
    assert by_key[env.sk_wx]["is_current"] is False
    assert by_key[env.sk_wx]["channel_type"] == "wechat"
    assert by_key[env.sk_wx]["thread_id"] == "thr_wx"
    assert by_key[env.sk_wx]["subject_id"] == "oWx"


@pytest.mark.asyncio
async def test_list_sessions_empty_for_non_owner(env: _Env) -> None:
    listing = _tool_by_name(env.tools, "list_sessions")
    with _configurable(agent_id="a1", user=str(env.other_id), session_key=env.sk_dash):
        rows = json.loads(await listing.ainvoke({}))
    assert rows == []


@pytest.mark.asyncio
async def test_send_to_session_pushes_and_records_history(env: _Env) -> None:
    send = _tool_by_name(env.tools, "send_to_session")
    with _configurable(agent_id="a1", user=str(env.owner_id), session_key=env.sk_dash):
        out = json.loads(await send.ainvoke({"session_key": env.sk_wx, "text": "会议室报告"}))
    assert out == {
        "delivered": True,
        "session_key": env.sk_wx,
        "channel_type": "wechat",
        "thread_id": "thr_wx",
    }

    # History: one [stub human, assistant] pair appended to the target thread.
    assert len(env.appended) == 1
    thread_id, messages = env.appended[0]
    assert thread_id == "thr_wx"
    stub, answer = messages
    assert isinstance(stub, HumanMessage)
    assert isinstance(answer, AIMessage)
    assert answer.content == "会议室报告"
    # IM channels resolve to zh; the stub labels the source channel.
    assert stub.content == tr("gateway.history.pushed", "zh", channel="dashboard")
    assert str(stub.id).startswith("push:")
    assert str(answer.id).startswith("push:")

    # Delivery: pushed verbatim on the target channel id.
    env.gateway._channel_manager.push_text.assert_awaited_once()
    args = env.gateway._channel_manager.push_text.await_args
    assert args.args[0] == "ch-wx"
    assert args.args[2] == "会议室报告"


@pytest.mark.asyncio
async def test_send_to_session_rejects_current_session(env: _Env) -> None:
    send = _tool_by_name(env.tools, "send_to_session")
    with _configurable(agent_id="a1", user=str(env.owner_id), session_key=env.sk_dash):
        out = json.loads(await send.ainvoke({"session_key": env.sk_dash, "text": "hi"}))
    assert "error" in out
    assert env.appended == []
    env.gateway._channel_manager.push_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_to_session_rejects_other_agent_session(env: _Env) -> None:
    send = _tool_by_name(env.tools, "send_to_session")
    with _configurable(agent_id="a1", user=str(env.owner_id), session_key=env.sk_dash):
        out = json.loads(await send.ainvoke({"session_key": env.sk_other_agent_wx, "text": "hi"}))
    assert "error" in out
    assert env.appended == []
    env.gateway._channel_manager.push_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_to_session_rejects_unknown_session(env: _Env) -> None:
    send = _tool_by_name(env.tools, "send_to_session")
    missing = ThreadRegistry.make_key(
        agent_id="a1", channel_type="wechat", channel_subject_id="nobody"
    )
    with _configurable(agent_id="a1", user=str(env.owner_id), session_key=env.sk_dash):
        out = json.loads(await send.ainvoke({"session_key": missing, "text": "hi"}))
    assert "error" in out
    env.gateway._channel_manager.push_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_to_session_rejects_non_owner(env: _Env) -> None:
    send = _tool_by_name(env.tools, "send_to_session")
    with _configurable(agent_id="a1", user=str(env.other_id), session_key=env.sk_dash):
        out = json.loads(await send.ainvoke({"session_key": env.sk_wx, "text": "hi"}))
    assert "error" in out
    env.gateway._channel_manager.push_text.assert_not_awaited()
