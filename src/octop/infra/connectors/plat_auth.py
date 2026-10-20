"""Append caller-owned credentials only to explicitly trusted MCP endpoints."""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any
from urllib.parse import unquote, urlsplit

import httpx

from octop.infra.auth.plat_credentials import PlatCredentialStore
from octop.infra.db.repos.secrets import SecretRepo
from octop.infra.db.repos.sso import SsoRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.errors import ErrorCode, OctopError


def _url_parts(url: str, *, rule: bool = False) -> tuple[str, str, int, str]:
    parsed = urlsplit(url)
    path = parsed.path or "/"
    decoded = unquote(path)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or "*" in parsed.netloc
        or any(ord(c) < 33 for c in url)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or (rule and parsed.query)
        or "\\" in decoded
        or re.search(r"%2f|%5c|%25", path, re.I)
        or any(part in {".", ".."} for part in decoded.split("/"))
        or "//" in path
        or ("*" in path and not (rule and path.endswith("/*") and path.count("*") == 1))
    ):
        raise ValueError("invalid trusted MCP URL")
    return (
        parsed.scheme,
        parsed.hostname.lower(),
        parsed.port or (443 if parsed.scheme == "https" else 80),
        path,
    )


def trusted_urls(provider_extra: dict[str, Any]) -> list[str]:
    raw = provider_extra.get("plat_mcp_urls", [])
    if not isinstance(raw, list) or any(not isinstance(v, str) for v in raw):
        raise ValueError("plat_mcp_urls must be a URL list")
    for url in raw:
        _url_parts(url, rule=True)
    return list(raw)


def matches_url(url: str, rule: str) -> bool:
    target = _url_parts(url)
    trusted = _url_parts(rule, rule=True)
    if target[:3] != trusted[:3]:
        return False
    path = trusted[3]
    return target[3].startswith(path[:-1]) if path.endswith("/*") else target[3] == path


_load_auth_failures: ContextVar[list[OctopError] | None] = ContextVar(
    "plat_mcp_load_auth_failures", default=None
)


@contextmanager
def plat_mcp_load_guard() -> Iterator[None]:
    """Preserve authentication errors swallowed by the harness MCP loader."""
    failures: list[OctopError] = []
    token = _load_auth_failures.set(failures)
    try:
        yield
        if failures:
            raise failures[0]
    finally:
        _load_auth_failures.reset(token)


def plat_http_client(
    *,
    headers: dict[str, str] | None = None,
    timeout: httpx.Timeout | None = None,
    auth: httpx.Auth | None = None,
) -> httpx.AsyncClient:
    async def check_authorization(response: httpx.Response) -> None:
        if response.status_code == 401:
            error = OctopError.localized(ErrorCode.PLAT_LOGIN_REQUIRED)
            failures = _load_auth_failures.get()
            if failures is not None:
                failures.append(error)
            raise error

    return httpx.AsyncClient(
        headers=headers,
        timeout=timeout or httpx.Timeout(30, read=300),
        auth=auth,
        follow_redirects=False,
        event_hooks={"response": [check_authorization]},
    )


class PlatMcpAuth:
    def __init__(self, secrets: SecretRepo, providers: SsoRepo, users: UserRepo) -> None:
        self._store = PlatCredentialStore(secrets)
        self._providers = providers
        self._users = users

    def prepare(self, user_id: int, spec: dict[str, Any]) -> dict[str, Any]:
        if spec.get("transport") not in {"http", "streamable_http"}:
            return spec
        providers = [p for p in self._providers.list() if p.kind == "oidc" and p.enabled]
        bindings = [(p, rule) for p in providers for rule in trusted_urls(p.extra)]
        if not bindings:
            return spec
        try:
            matched = {
                p.id: p for p, rule in bindings if matches_url(str(spec.get("url", "")), rule)
            }
        except ValueError as exc:
            raise OctopError.localized(ErrorCode.PLAT_MCP_CONFIG_INVALID) from exc
        if not matched:
            return spec
        if len(matched) != 1:
            raise OctopError.localized(ErrorCode.PLAT_MCP_CONFIG_INVALID)
        provider = next(iter(matched.values()))
        headers = dict(spec.get("headers") or {})
        if any(k.lower() in {"x-plat-token", "authorization", "token"} for k in headers):
            raise OctopError.localized(ErrorCode.PLAT_MCP_CONFIG_INVALID)
        user = self._users.get(user_id)
        identity = next(
            (i for i in self._users.list_sso_identities(user_id) if i.provider_id == provider.id),
            None,
        )
        credential = self._store.get(user_id, provider, identity.subject) if identity else None
        if user is None or user.disabled or credential is None:
            raise OctopError.localized(ErrorCode.PLAT_LOGIN_REQUIRED)
        headers["X-Plat-Token"] = credential.access_token
        return {**spec, "headers": headers, "httpx_client_factory": plat_http_client}
