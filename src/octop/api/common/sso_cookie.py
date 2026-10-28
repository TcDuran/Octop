"""Shared SSO state cookie for OIDC and OAuth login callbacks."""

from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from octop.api.common.public_base import resolve_public_base

SSO_STATE_COOKIE = "octop_sso_state"
SSO_COOKIE_PATH = "/api/auth"
SSO_STATE_TTL_SECONDS = 600


def request_is_https(request: Request) -> bool:
    forwarded_proto = request.headers.get("x-forwarded-proto")
    if forwarded_proto:
        return forwarded_proto.split(",", 1)[0].strip().lower() == "https"
    return request.url.scheme == "https"


def cookie_path(request: Request) -> str:
    """Keep the SSO cookie visible when Octop is mounted under a URL prefix."""
    prefix = urlsplit(resolve_public_base(request)).path.rstrip("/")
    return f"{prefix}{SSO_COOKIE_PATH}" or SSO_COOKIE_PATH


def set_sso_state_cookie(response: JSONResponse, request: Request, state: str) -> None:
    response.set_cookie(
        SSO_STATE_COOKIE,
        state,
        max_age=SSO_STATE_TTL_SECONDS,
        path=cookie_path(request),
        httponly=True,
        secure=request_is_https(request),
        samesite="lax",
    )


def delete_sso_state_cookie(response: Response, request: Request) -> None:
    response.delete_cookie(
        SSO_STATE_COOKIE, path=cookie_path(request), secure=request_is_https(request)
    )


def cookie_state(request: Request) -> str | None:
    return request.cookies.get(SSO_STATE_COOKIE)
