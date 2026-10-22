"""Per-invocation tool policy for automated analysis, without mutating agents."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from octop.i18n import tr
from octop.infra.cron.analysis.runtime import AnalysisError, current_run

# File generation stays available; arbitrary shell/network and delegation do not.
_LOCAL_TOOLS = frozenset(
    {
        "ls",
        "read_file",
        "write_file",
        "edit_file",
        "glob",
        "grep",
        "write_todos",
        "current_time",
        "search_knowledge",
        "send_file_to_user",
    }
)
_SCAN_TOOLS = frozenset({"sync_analysis_items", "dispatch_analysis_task"})


def permitted(tool: Any, role: str, read_tools: frozenset[str] = frozenset()) -> bool:
    name = str(getattr(tool, "name", ""))
    if name in _LOCAL_TOOLS or (role == "scan" and name in _SCAN_TOOLS):
        return True
    # A server-owned exact allowlist supports legacy MCPs without accurate annotations.
    # Never grant orchestration, delegation or sending through this list.
    if name.startswith("cronjob_") or name in {
        "task",
        "ask_agent",
        "send_to_session",
        *_SCAN_TOOLS,
    }:
        return False
    metadata = getattr(tool, "metadata", None) or {}
    return metadata.get("readOnlyHint") is True or name in read_tools


def failed_result(result: Any) -> bool:
    """Recognize protocol failures as well as explicit business envelopes."""
    if getattr(result, "status", None) == "error":
        return True
    content = getattr(result, "content", result)
    if isinstance(content, list):
        return any(
            failed_result(block.get("text", "")) for block in content if isinstance(block, dict)
        )
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except (ValueError, TypeError):
            return False
        return failed_result(content)
    if isinstance(content, dict):
        return (
            content.get("isError") is True
            or content.get("isSuccess") is False
            or content.get("success") is False
            or bool(content.get("error"))
        )
    return False


class AnalysisToolMiddleware(AgentMiddleware[Any, Any]):
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        run = current_run()
        if run is None or run.role not in {"scan", "analysis"}:
            return await handler(request)
        if run.failed:
            raise AnalysisError("analysis.tool_failed")
        tools = [tool for tool in request.tools if permitted(tool, run.role, run.read_tools)]
        run.allowed_tools.update(str(getattr(tool, "name", "")) for tool in tools)
        return await handler(request.override(tools=tools))

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        run = current_run()
        if run is None or run.role not in {"scan", "analysis"}:
            return await handler(request)
        if run.failed:
            raise AnalysisError("analysis.tool_failed")
        name = str(request.tool_call.get("name", ""))
        if name not in run.allowed_tools:
            run.failed = True
            return ToolMessage(
                content=tr("analysis.tool_blocked", run.locale),
                status="error",
                tool_call_id=str(request.tool_call["id"]),
                name=name,
            )
        try:
            result = await handler(request)
        except Exception:
            run.failed = True
            raise
        if failed_result(result):
            run.failed = True
        return result
