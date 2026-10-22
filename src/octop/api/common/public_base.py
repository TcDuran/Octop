"""Resolve the externally visible HTTP base URL, including deployment prefix."""

from __future__ import annotations

from starlette.requests import Request

from octop.api.deployment import deployment_prefix


def _first_header_value(value: str | None) -> str | None:
    if value is None:
        return None
    first = value.split(",", 1)[0].strip()
    return first or None


def resolve_public_base(request: Request) -> str:
    """Resolve the externally visible origin and deployment prefix.

    Trusts ``X-Forwarded-Proto`` / ``X-Forwarded-Host`` when present (typical
    behind a reverse proxy). Prefer deploying Octop behind a trusted proxy and
    keeping the dashboard same-origin with the API for OIDC cookies.
    """
    forwarded_proto = _first_header_value(request.headers.get("x-forwarded-proto"))
    forwarded_host = _first_header_value(request.headers.get("x-forwarded-host"))
    prefix = deployment_prefix(request.scope)
    if forwarded_proto:
        host = forwarded_host or request.headers.get("host")
        if host:
            return f"{forwarded_proto}://{host}".rstrip("/") + prefix

    return str(request.url.replace(path="", query="", fragment="")).rstrip("/") + prefix
