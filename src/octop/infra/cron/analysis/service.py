"""Item lifecycle orchestration using existing conversations and Cron scheduling."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import time
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, model_validator

from octop.i18n import tr
from octop.infra.cron.analysis.runtime import AnalysisError, AnalysisRun, require_scan
from octop.infra.cron.task_type import CRON_PROMPT_MAX_LEN
from octop.infra.utils.locale import resolve_user_locale
from octop.infra.utils.ulid import new_cron_id

if TYPE_CHECKING:
    from octop.infra.cron.manager import CronManager
    from octop.infra.db.repos.cron import CronJobRow
    from octop.infra.db.services import RepoBundle


class AnalysisItem(BaseModel):
    key: str = Field(min_length=1, max_length=500)
    status: str = Field(pattern="^(pending|handled|closed|unknown)$")
    observed_at: int = Field(
        default_factory=lambda: int(time.time()),
        gt=0,
        description="Source observation time, Unix seconds. Omit to use current server time; "
        "do not invoke shell commands to calculate it.",
    )
    version: str | None = Field(default=None, min_length=1, max_length=500)
    fields: dict[str, Any] | None = Field(
        default=None,
        description="Fixed business fields for fingerprinting when no source version exists. "
        "Exclude observation time, elapsed hours, model summaries and user messages.",
    )

    @model_validator(mode="after")
    def validate_version(self) -> AnalysisItem:
        if not self.version and not self.fields:
            raise ValueError("version or fixed business fields required")
        return self

    def fingerprint(self) -> str:
        if self.version:
            return self.version
        fields = dict(self.fields or {})
        for key in ("pendingHours", "observed_at", "query_time", "summary"):
            fields.pop(key, None)
        if not fields:
            raise ValueError("stable fingerprint fields required")
        raw = json.dumps(fields, sort_keys=True, ensure_ascii=False, allow_nan=False)
        return "sha256:" + hashlib.sha256(raw.encode()).hexdigest()


class AnalysisService:
    def __init__(self, manager: CronManager) -> None:
        self.manager = manager

    @property
    def repos(self) -> RepoBundle:
        return self.manager._repos

    def guard(self, run: AnalysisRun) -> None:
        row = self.manager.get(run.cron_id)
        if row is None or row.agent_id != run.agent_id or row.user_id != run.user_id:
            raise AnalysisError("analysis.owner_changed")
        if not row.enabled:
            raise AnalysisError("analysis.parent_disabled")
        if row.fresh_thread or row.task_type != "agent":
            raise AnalysisError("analysis.fixed_target_required")
        agent = self.repos.agent_repo.get(run.agent_id)
        if agent is None or agent.user_id != run.user_id:
            raise AnalysisError("analysis.owner_changed")
        session = self.repos.session_repo.get(row.session_key)
        thread = self.repos.thread_repo.get(run.thread_id)
        if (
            session is None
            or session.agent_id != run.agent_id
            or session.user_id != run.user_id
            or session.thread_id != run.thread_id
            or thread is None
            or thread.agent_id != run.agent_id
            or thread.user_id != run.user_id
        ):
            raise AnalysisError("analysis.target_changed")
        item = self.repos.analysis_repo.for_cron(run.cron_id)
        if run.role == "analysis":
            if item is None:
                raise AnalysisError("analysis.obsolete")
            parent = self.manager.get(item["parent_cron_id"]) if item["parent_cron_id"] else None
            if (
                parent is None
                or not parent.enabled
                or parent.agent_id != run.agent_id
                or parent.user_id != run.user_id
                or item["business_status"] != "pending"
                or item["thread_id"] != run.thread_id
                or item["session_key"] != row.session_key
                or item["source_version"] != item["analysis_version"]
                or item["dispatch_state"] not in {"queued", "running"}
                or not row.trigger.startswith("date:")
            ):
                raise AnalysisError("analysis.obsolete")
        else:
            fixed = self.repos.session_repo.get(
                self.summary_key(run.agent_id, run.user_id, run.cron_id)
            )
            if fixed is not None and (
                row.session_key != fixed.session_key or fixed.thread_id != run.thread_id
            ):
                raise AnalysisError("analysis.target_changed")

    def make_run(self, cron_id: str, thread_id: str, session_key: str) -> AnalysisRun:
        row = self.manager.get(cron_id)
        if row is None:
            raise AnalysisError("analysis.obsolete")
        item = self.repos.analysis_repo.for_cron(cron_id)
        summary_key = self.summary_key(row.agent_id, row.user_id, cron_id)
        session = self.repos.session_repo.get(session_key)
        metadata = session.channel_metadata if session else {}
        role = (
            "analysis"
            if item
            or (metadata or {}).get("analysis_item")
            or row.session_key.startswith(f"{row.agent_id}:dashboard:{row.user_id}:analysis-item-")
            else "scan"
            if (
                row.session_key == summary_key
                or self.repos.session_repo.get(summary_key) is not None
                or "sync_analysis_items" in row.prompt
                or "dispatch_analysis_task" in row.prompt
            )
            else "cron"
        )
        run = AnalysisRun(
            cron_id, row.agent_id, row.user_id, thread_id, session_key, role, lambda: None
        )
        run.locale = resolve_user_locale(
            user_repo=self.repos.user_repo, user_id=row.user_id, channel_type="dashboard"
        )
        agent = self.repos.agent_repo.get(row.agent_id)
        config = json.loads(agent.config_json or "{}") if agent else {}
        read_tools = config.get("automation_analysis_read_tools", [])
        if isinstance(read_tools, list):
            run.read_tools = frozenset(name for name in read_tools if isinstance(name, str))
        run.guard = lambda: self.guard(run)
        if role == "analysis":

            def start() -> None:
                if not self.repos.analysis_repo.transition(cron_id, "queued", "running"):
                    raise AnalysisError("analysis.already_running")

            run.on_start = start
        return run

    @staticmethod
    def summary_key(agent_id: str, user_id: int, cron_id: str) -> str:
        return f"{agent_id}:dashboard:{user_id}:analysis-overview-{cron_id}"

    async def initialize_summary(self, run: AnalysisRun) -> None:
        row = self.manager.get(run.cron_id)
        assert row is not None
        key = self.summary_key(run.agent_id, run.user_id, run.cron_id)
        session = self.repos.session_repo.get(key)
        if session is not None:
            if session.thread_id != run.thread_id:
                raise AnalysisError("analysis.target_changed")
            return
        original = self.repos.session_repo.get(run.session_key)
        assert original is not None
        if original.channel_type != "dashboard":
            raise AnalysisError("analysis.dashboard_required")
        self.repos.session_repo.upsert(
            session_key=key,
            agent_id=run.agent_id,
            user_id=run.user_id,
            channel_type="dashboard",
            chat_type=original.chat_type,
            thread_id=run.thread_id,
            channel_subject_id=original.channel_subject_id,
            channel_chat_type=original.channel_chat_type,
            channel_metadata={**(original.channel_metadata or {}), "analysis_parent": run.cron_id},
        )
        await self.manager.update(run.cron_id, session_key=key)
        run.session_key = key

    def present(self, item: dict[str, Any]) -> dict[str, Any]:
        cron = self.manager.get(item["analysis_cron_id"]) if item["analysis_cron_id"] else None
        # Only queued jobs which have never started can consume scheduler terminal state.
        if (
            cron
            and not cron.enabled
            and item["dispatch_state"] == "queued"
            and cron.last_status == "missed"
        ):
            self.repos.analysis_repo.finish(cron.cron_id, success=False)
            refreshed = self.repos.analysis_repo.get(item["item_id"])
            assert refreshed is not None
            item = refreshed
        thread_id = item["thread_id"]
        return {
            "item_id": item["item_id"],
            "key": item["source_item_key"],
            "status": item["business_status"],
            "observed_at": item["observed_at"],
            "version": item["source_version"],
            "analysis_version": item["analysis_version"],
            "last_success_version": item["last_success_version"],
            "dispatch_state": item["dispatch_state"],
            "cron_id": item["analysis_cron_id"],
            "cron_status": cron.last_status if cron else None,
            "thread_url": f"/chat/{item['agent_id']}/{thread_id}" if thread_id else None,
            "needs_review": bool(item["analysis_cron_id"] and not cron)
            or bool(item["session_key"] and not thread_id),
        }

    async def sync(
        self,
        source_key: str,
        items: list[AnalysisItem] | None,
        *,
        complete: bool,
        offset: int,
        limit: int,
    ) -> list[dict[str, Any]]:
        run = require_scan()
        if items is not None:
            if not complete:
                raise AnalysisError("analysis.incomplete")
            # Validate the whole batch before changing the observation snapshot.
            versions = [(item, item.fingerprint()) for item in items]
            await self.initialize_summary(run)
            for item, version in versions:
                row = self.repos.analysis_repo.observe(
                    parent_cron_id=run.cron_id,
                    agent_id=run.agent_id,
                    user_id=run.user_id,
                    source_key=source_key,
                    source_item_key=item.key,
                    business_status=item.status,
                    source_version=version,
                    observed_at=item.observed_at,
                )
                if row["source_version"] == version and row["business_status"] == "pending":
                    run.synchronized[row["item_id"]] = version
                else:
                    run.synchronized.pop(row["item_id"], None)
        return [
            self.present(row)
            for row in self.repos.analysis_repo.list_items(
                run.cron_id,
                source_key,
                offset=offset,
                limit=limit,
            )
        ]

    async def dispatch(
        self,
        item_id: str,
        expected_version: str,
        title: str,
        instructions: str,
    ) -> dict[str, Any]:
        from octop.infra.cron.manager import CronCreateSpec

        run = require_scan()
        item = self.repos.analysis_repo.get(item_id)
        if (
            item is None
            or item["parent_cron_id"] != run.cron_id
            or item["user_id"] != run.user_id
            or item["agent_id"] != run.agent_id
        ):
            raise AnalysisError("analysis.owner_changed")

        def result(status: str, reason: str) -> dict[str, Any]:
            current = self.repos.analysis_repo.get(item_id)
            assert current is not None
            return {
                **self.present(current),
                "status": status,
                "reason_code": reason,
                "reason": tr(reason, run.locale, limit=CRON_PROMPT_MAX_LEN),
            }

        if (
            run.synchronized.get(item_id) != expected_version
            or item["source_version"] != expected_version
            or item["business_status"] != "pending"
        ):
            return result("blocked", "analysis.sync_required")
        if item["session_key"] and not item["thread_id"]:
            return result("blocked", "analysis.target_changed")
        if item["dispatch_state"] in {"preparing", "queued", "running", "interrupted"}:
            return result("waiting", "analysis.active_or_uncertain")
        if item["analysis_cron_id"] and self.manager.get(item["analysis_cron_id"]) is None:
            return result("blocked", "analysis.active_or_uncertain")
        if item["analysis_version"] == expected_version:
            return result("existing", "analysis.already_attempted")
        if run.dispatched >= 5:
            return result("blocked", "analysis.limit")
        prompt = json.dumps(
            {
                "source": item["source_key"],
                "key": item["source_item_key"],
                "version": expected_version,
                "observed_at": item["observed_at"],
                "instructions": instructions,
                "constraints": "Query and analyze only. Recheck current source data. "
                "State material coverage and uncertainty. Do not approve, send business "
                "messages or schedule. Keep results and generated workspace files in this "
                "conversation. Use send_file_to_user to deliver generated files here.",
            },
            ensure_ascii=False,
        )
        if len(prompt) > CRON_PROMPT_MAX_LEN:
            return result("blocked", "analysis.prompt_too_long")
        parent = self.manager.get(run.cron_id)
        assert parent is not None
        cron_id = new_cron_id()
        session_key = (
            item["session_key"] or f"{run.agent_id}:dashboard:{run.user_id}:analysis-item-{item_id}"
        )
        if not self.repos.analysis_repo.reserve(item_id, expected_version, cron_id, session_key):
            return result("waiting", "analysis.active_or_uncertain")
        run.dispatched += 1
        thread_id = item["thread_id"]
        if thread_id is None:
            thread_id = self.manager._gateway.thread_registry.create_thread(
                agent_id=run.agent_id,
                user_id=run.user_id,
                channel_type="dashboard",
                session_key=session_key,
                title=title,
            )
            self.repos.session_repo.upsert(
                session_key=session_key,
                agent_id=run.agent_id,
                user_id=run.user_id,
                channel_type="dashboard",
                chat_type="private",
                thread_id=thread_id,
                channel_metadata={"analysis_item": item_id},
            )
            self.repos.analysis_repo.bind(item_id, cron_id, thread_id)
        else:
            bound = self.repos.session_repo.get(session_key)
            if bound is None or bound.thread_id != thread_id:
                raise AnalysisError("analysis.target_changed")
        when = dt.datetime.now(ZoneInfo(self.manager._timezone)) + dt.timedelta(seconds=3)
        await self.manager.create(
            CronCreateSpec(
                cron_id=cron_id,
                agent_id=run.agent_id,
                user_id=run.user_id,
                name=title[:80],
                prompt=prompt,
                trigger=f"date:{when.isoformat()}",
                session_key=session_key,
                task_type="agent",
                fresh_thread=False,
                enabled=False,
                model=parent.model,
                mcp_servers=list(parent.mcp_servers),
            )
        )
        self.repos.analysis_repo.transition(cron_id, "preparing", "queued")
        row = await self.manager.update(cron_id, enabled=1)
        if not row.enabled:
            raise AnalysisError("analysis.not_scheduled")
        return result("created", "analysis.queued")

    def retry(self, cron_id: str) -> None:
        item = self.repos.analysis_repo.for_cron(cron_id)
        if item is None:
            cron = self.manager.get(cron_id)
            if cron and ":analysis-item-" in cron.session_key:
                raise AnalysisError("analysis.obsolete")
            return
        cron = self.manager.get(cron_id)
        assert cron is not None
        if item["dispatch_state"] not in {"finished", "interrupted"} or (
            cron.last_status not in {"error", "missed"} and item["dispatch_state"] != "interrupted"
        ):
            raise AnalysisError("analysis.retry_not_allowed")
        old_state = item["dispatch_state"]
        if not self.repos.analysis_repo.transition(cron_id, old_state, "queued"):
            raise AnalysisError("analysis.retry_not_allowed")
        self.repos.cron_repo.update(cron_id, enabled=True)
        try:
            run = self.make_run(cron_id, item["thread_id"] or "", cron.session_key)
            self.guard(run)
        except Exception:
            self.repos.analysis_repo.transition(cron_id, "queued", old_state)
            self.repos.cron_repo.update(cron_id, enabled=bool(cron.enabled))
            raise

    def validate_update(
        self,
        row: CronJobRow,
        *,
        session_key: str | None,
        fresh_thread: bool | None,
        task_type: str | None,
        trigger: str | None,
        enabled: bool | None,
    ) -> None:
        item = self.repos.analysis_repo.for_cron(row.cron_id)
        child = item is not None or ":analysis-item-" in row.session_key
        overview = self.repos.session_repo.get(
            self.summary_key(row.agent_id, row.user_id, row.cron_id)
        )
        if not child and overview is None:
            return
        original = self.repos.session_repo.get(row.session_key)
        initializing = (
            not child
            and overview is not None
            and original is not None
            and session_key == overview.session_key
            and original.thread_id == overview.thread_id
        )
        if (
            session_key is not None
            and session_key != row.session_key
            and not initializing
            or fresh_thread is True
            or task_type not in {None, "agent"}
            or child
            and trigger is not None
            and trigger != row.trigger
        ):
            raise AnalysisError("analysis.fixed_target_required")
        if (
            child
            and enabled is True
            and (item is None or item["dispatch_state"] != "queued" or row.last_status is not None)
        ):
            raise AnalysisError("analysis.retry_not_allowed")

    def recover(self) -> None:
        for cron_id in self.repos.analysis_repo.interrupt_running():
            if self.manager.get(cron_id):
                self.repos.cron_repo.update(cron_id, enabled=False)
