"""Tests for public OIDC callback URL helpers."""

from __future__ import annotations

from types import SimpleNamespace

from starlette.requests import Request

from octop.api.common.public_base import resolve_public_base
from octop.api.routers.invites import _public_base as invite_public_base
from octop.infra.auth.sso.public_base import build_redirect_uri


def _request(
    *,
    scheme: str = "http",
    headers: list[tuple[bytes, bytes]] | None = None,
    public_base_url: str = "",
) -> Request:
    return Request(
        {
            "type": "http",
            "scheme": scheme,
            "server": ("internal.example", 8080),
            "path": "/",
            "headers": headers or [(b"host", b"internal.example:8080")],
            "app": SimpleNamespace(
                state=SimpleNamespace(octop_config=SimpleNamespace(public_base_url=public_base_url))
            ),
        }
    )


def test_resolve_public_base_uses_forwarded_proxy_headers() -> None:
    request = _request(
        headers=[
            (b"host", b"internal.example:8080"),
            (b"x-forwarded-proto", b"https"),
            (b"x-forwarded-host", b"octop.example"),
        ]
    )

    assert resolve_public_base(request) == "https://octop.example"


def test_resolve_public_base_uses_request_url_without_proxy_headers() -> None:
    request = _request(scheme="https", headers=[(b"host", b"octop.example")])

    assert resolve_public_base(request) == "https://octop.example"


def test_resolve_public_base_prefers_configured_value_to_proxy_headers() -> None:
    request = _request(
        headers=[
            (b"host", b"internal.example:8080"),
            (b"x-forwarded-proto", b"https"),
            (b"x-forwarded-host", b"other.example"),
        ],
        public_base_url="http://221.232.69.57:7321/octop",
    )

    assert resolve_public_base(request) == "http://221.232.69.57:7321/octop"


def test_invite_public_base_uses_configured_path_prefix() -> None:
    request = _request(public_base_url="http://221.232.69.57:7321/octop")

    assert invite_public_base(request) == "http://221.232.69.57:7321/octop"


def test_build_redirect_uri_uses_oidc_callback_path() -> None:
    assert (
        build_redirect_uri("https://octop.example/")
        == "https://octop.example/api/auth/oidc/callback"
    )


def test_build_redirect_uri_preserves_public_path_prefix() -> None:
    assert (
        build_redirect_uri("http://221.232.69.57:7321/octop")
        == "http://221.232.69.57:7321/octop/api/auth/oidc/callback"
    )


def test_build_redirect_uri_accepts_oauth_callback_path() -> None:
    assert (
        build_redirect_uri("https://octop.example", "/api/auth/oauth/callback")
        == "https://octop.example/api/auth/oauth/callback"
    )
