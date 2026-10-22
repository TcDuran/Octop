"""Generic item registration and one-shot analysis tools."""

from __future__ import annotations

import json
from typing import Annotated

from langchain_core.tools import StructuredTool
from pydantic import Field

from octop.infra.cron.analysis.service import AnalysisItem, AnalysisService
from octop.infra.cron.task_type import CRON_PROMPT_MAX_LEN


def build_analysis_tools(service: AnalysisService) -> list[StructuredTool]:
    async def sync_analysis_items(
        source_key: Annotated[str, Field(min_length=1, max_length=500)],
        items: list[AnalysisItem] | None = None,
        complete: Annotated[
            bool,
            Field(
                description="Required true when submitting items (including []). "
                "Confirms the selected source query succeeded and all pages were read. "
                "Not required for items=null (read existing records)."
            ),
        ] = False,
        offset: Annotated[int, Field(ge=0)] = 0,
        limit: Annotated[int, Field(ge=1, le=200)] = 100,
    ) -> str:
        result = await service.sync(
            source_key, items, complete=complete, offset=offset, limit=limit
        )
        return json.dumps(result, ensure_ascii=False)

    async def dispatch_analysis_task(
        item_id: str,
        expected_version: str,
        title: Annotated[str, Field(min_length=1, max_length=80)],
        instructions: Annotated[
            str,
            Field(
                min_length=1,
                max_length=CRON_PROMPT_MAX_LEN,
                description="Instructions plus serialized source metadata and constraints must fit "
                "the Cron prompt limit. Leave room for this additional content.",
            ),
        ],
    ) -> str:
        result = await service.dispatch(item_id, expected_version, title, instructions)
        return json.dumps(result, ensure_ascii=False)

    return [
        StructuredTool.from_function(
            coroutine=sync_analysis_items,
            name="sync_analysis_items",
            description="In a periodic agent Cron, register/query generic work items. "
            "The scanning Cron prompt must name sync_analysis_items to opt in. "
            "Query source first; set complete=true when submitting successful observations. "
            "items=null reads existing records; [] means successful empty query and never closes missing items. "
            "Use stable source scope and string item keys. Query time/elapsed hours are not versions. "
            "Returns persistent item IDs, versions, real Cron states and conversation links. "
            "Does not start analysis. MCP tools must declare readOnlyHint=true or be explicitly authorized in the agent automation_analysis_read_tools configuration.",
        ),
        StructuredTool.from_function(
            coroutine=dispatch_analysis_task,
            name="dispatch_analysis_task",
            description="Arrange independent analysis for a pending item successfully synchronized "
            "in this scanning turn. Prompt decides importance. Pass returned item_id/version, "
            "initial title and source-specific analysis instructions. Reuses ordinary item "
            "conversation; creates one date Cron per changed version. created means queued, "
            "not completed. Same-version failures require manual retry; never recursively dispatch.",
        ),
    ]
