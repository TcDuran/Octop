"""Built-in LangChain tools for cross-session message delivery."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Annotated, Any

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import StructuredTool
from langgraph.config import get_config
from pydantic import Field

from octop.i18n import tr
from octop.infra.gateway.threads import ThreadRegistry
from octop.infra.history.projection import message_inputs
from octop.infra.utils.locale import resolve_user_locale
from octop.infra.utils.ulid import new_ulid

if TYPE_CHECKING:
    from octop.infra.agents.manager import AgentManager
    from octop.infra.db.repos.sessions import SessionRow
    from octop.infra.db.repos.thread_messages import ThreadMessageInput
    from octop.infra.db.services import RepoBundle
    from octop.infra.gateway.gateway import Gateway

logger = logging.getLogger(__name__)

_LIST_SESSIONS_DESC = (
    "List every chat session bound to the current agent across channels "
    "(WeChat/QQ/Feishu/Discord/dashboard/CLI/…): session_key, channel, chat type, "
    "subject id and thread id. The session the conversation is running in is flagged "
    "with is_current=true. Use it to find the target session_key before send_to_session."
)

_SEND_TO_SESSION_DESC = (
    "Deliver a message to ANOTHER session of the same agent — e.g. push a result "
    "from a dashboard chat to the user's WeChat, or forward a summary from an IM "
    "chat to the dashboard. session_key must come from list_sessions and must not "
    "be the current session (reply there directly instead). The text is delivered "
    "verbatim to the target channel and recorded in that conversation's history; "
    "it does not start a model turn there. IM channels render plain text only — "
    "no markdown tables or headers."
)


def _tool_ctx() -> tuple[str, int, str]:
    cfg = get_config().get("configurable") or {}
    agent_id = cfg.get("agent_id")
    user_raw = cfg.get("user")
    if not agent_id:
        raise ValueError("missing configurable.agent_id")
    if user_raw is None:
        raise ValueError("missing configurable.user")
    user_id = int(user_raw)
    session_key = cfg.get("session_key")
    if not session_key:
        session_key = ThreadRegistry.dashboard_key(agent_id=str(agent_id), user_id=user_id)
    return str(agent_id), user_id, str(session_key)


def _ok(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def _err(exc: Exception) -> str:
    return json.dumps({"error": str(exc)}, ensure_ascii=False)


def _channel_segment(session_key: str) -> str:
    parts = session_key.split(":", 3)
    return parts[1] if len(parts) > 1 else session_key


def _project_best_effort(
    repos: RepoBundle, thread_id: str, messages: list[ThreadMessageInput]
) -> None:
    if not messages:
        return
    try:
        repos.thread_message_repo.append_if_ready(thread_id, messages)
    except Exception:
        logger.warning(
            "failed to append cross-session push projection for thread=%s",
            thread_id,
            exc_info=True,
        )


async def _record_pushed_turn(
    *,
    agent_manager: AgentManager,
    repos: RepoBundle,
    session: SessionRow,
    source_channel: str,
    text: str,
) -> None:
    """Persist the pushed text into the target thread (checkpoint + projection)."""
    locale = resolve_user_locale(
        user_repo=repos.user_repo,
        user_id=session.user_id,
        channel_type=session.channel_type,
    )
    label = tr("gateway.history.pushed", locale, channel=source_channel)
    push_id = new_ulid()
    canonical = [
        HumanMessage(content=label, id=f"push:{push_id}:human"),
        AIMessage(content=text, id=f"push:{push_id}:assistant"),
    ]
    harness = agent_manager.get_agent(session.agent_id)
    appended = await harness.aappend_messages(session.thread_id, canonical)
    _project_best_effort(
        repos, session.thread_id, message_inputs(appended, dedupe_missing_ids=True)
    )


def build_session_push_tools(
    *,
    gateway: Gateway,
    agent_manager: AgentManager,
    repos: RepoBundle,
) -> list[StructuredTool]:
    """Return cross-session delivery tools (not MCP — wired via HarnessAgentConfig.tools)."""

    def _user_owns_agent(agent_id: str, user_id: int) -> bool:
        row = repos.agent_repo.get(agent_id)
        return row is not None and row.user_id == user_id

    async def list_sessions() -> str:
        try:
            agent_id, user_id, current_key = _tool_ctx()
            if not _user_owns_agent(agent_id, user_id):
                return _ok([])
            rows = repos.session_repo.list_by_agent(agent_id, limit=100)
            return _ok(
                [
                    {
                        "session_key": row.session_key,
                        "channel_type": row.channel_type,
                        "chat_type": row.channel_chat_type or row.chat_type,
                        "subject_id": row.channel_subject_id,
                        "thread_id": row.thread_id,
                        "is_current": row.session_key == current_key,
                    }
                    for row in rows
                ]
            )
        except Exception as exc:
            return _err(exc)

    async def send_to_session(
        session_key: Annotated[
            str,
            Field(
                description=(
                    "Target session_key from list_sessions. Must belong to the same "
                    "agent and differ from the current session."
                ),
            ),
        ],
        text: Annotated[
            str,
            Field(
                description=(
                    "Message to deliver verbatim. Final-user-ready plain text: "
                    "no internal jargon; IM channels do not render markdown."
                ),
            ),
        ],
    ) -> str:
        try:
            agent_id, user_id, current_key = _tool_ctx()
            if not _user_owns_agent(agent_id, user_id):
                raise ValueError("cross-session push is only available to the agent owner")
            if session_key == current_key:
                raise ValueError(
                    "refusing to send to the current session — reply to the user directly instead"
                )
            session = gateway.require_session(agent_id, session_key)
            await _record_pushed_turn(
                agent_manager=agent_manager,
                repos=repos,
                session=session,
                source_channel=_channel_segment(current_key),
                text=text,
            )
            await gateway.push_text_from_session(agent_id, session_key, text)
            return _ok(
                {
                    "delivered": True,
                    "session_key": session_key,
                    "channel_type": session.channel_type,
                    "thread_id": session.thread_id,
                }
            )
        except Exception as exc:
            return _err(exc)

    return [
        StructuredTool.from_function(
            coroutine=list_sessions,
            name="list_sessions",
            description=_LIST_SESSIONS_DESC,
        ),
        StructuredTool.from_function(
            coroutine=send_to_session,
            name="send_to_session",
            description=_SEND_TO_SESSION_DESC,
        ),
    ]
