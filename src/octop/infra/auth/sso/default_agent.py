"""Prepare the bundled general assistant for OIDC users without an expert."""

from __future__ import annotations

from octop.infra.agents.experts.catalog import ExpertCatalog, build_create_spec_from_expert
from octop.infra.agents.manager import AgentManager
from octop.infra.db.services import SharedServices
from octop.infra.utils.locale import resolve_user_locale


async def ensure_default_oidc_agent(
    *,
    services: SharedServices,
    registry: AgentManager,
    catalog: ExpertCatalog,
    user_id: int,
) -> None:
    """Reuse owned experts; caller serializes the check/create across logins."""
    if any(row.kind == "expert" for row in services.agent_repo.list_by_user(user_id)):
        return
    expert = catalog.get("general-assistant")
    if expert is None:
        raise RuntimeError("General assistant template is unavailable")
    spec = build_create_spec_from_expert(
        expert_id="general-assistant",
        expert=expert,
        user_id=user_id,
        name=expert.summary.label_zh,
        locale=resolve_user_locale(user_repo=services.user_repo, user_id=user_id),
    )
    await registry.create(spec)
