"""Trusted invocation scope; never populated from model arguments or HTTP fields."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

from octop.i18n import tr
from octop.infra.errors import ErrorCode, OctopError


@dataclass
class AnalysisRun:
    cron_id: str
    agent_id: str
    user_id: int
    thread_id: str
    session_key: str
    role: str
    guard: Callable[[], None]
    locale: str = "en"
    on_start: Callable[[], None] = lambda: None
    synchronized: dict[str, str] = field(default_factory=dict)
    read_tools: frozenset[str] = frozenset()
    allowed_tools: set[str] = field(default_factory=set)
    dispatched: int = 0
    failed: bool = False


_RUN: ContextVar[AnalysisRun | None] = ContextVar("automation_analysis_run", default=None)


def current_run() -> AnalysisRun | None:
    return _RUN.get()


def require_scan() -> AnalysisRun:
    run = current_run()
    if run is None or run.role != "scan" or run.failed:
        raise AnalysisError("analysis.scan_required")
    run.guard()
    return run


@contextmanager
def analysis_scope(run: AnalysisRun) -> Iterator[None]:
    token = _RUN.set(run)
    try:
        yield
    finally:
        _RUN.reset(token)


def check_execution() -> None:
    run = current_run()
    if run is not None and run.role in {"scan", "analysis"}:
        run.guard()
        run.on_start()


class AnalysisError(OctopError):
    def __init__(self, key: str) -> None:
        run = current_run()
        super().__init__(
            ErrorCode.AUTOMATION_ANALYSIS_CONFLICT,
            tr(key, run.locale if run else "en"),
            details={"reason": key},
        )
