"""Runtime deployment-prefix adapters; business routes keep their original paths."""

from __future__ import annotations

import json
from urllib.parse import urlsplit, urlunsplit

from fastapi import FastAPI
from starlette.datastructures import URL
from starlette.responses import JSONResponse, RedirectResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


def deployment_prefix(scope: Scope) -> str:
    return str(scope.get("app_root_path", scope.get("root_path", ""))).rstrip("/")


def public_path(scope: Scope, path: str) -> str:
    prefix = deployment_prefix(scope)
    if prefix and (path == prefix or path.startswith(prefix + "/")):
        return path
    return prefix + path


def dashboard_html(source: str, base_path: str) -> str:
    """Inject before every resource so even deep links use the deployment root."""
    config = json.dumps({"basePath": base_path})
    boot = f'<base href="{base_path}/" /><script>window.__OCTOP_CONFIG__ = {config};</script>'
    return source.replace('<base href="/" />', "", 1).replace("<head>", f"<head>{boot}", 1)


class InternalApp:
    """Keep authenticated, in-process Bridge calls on canonical /api paths."""

    def __init__(self, app: FastAPI) -> None:
        self.app = app
        self.state = app.state

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self.app({**scope, "octop.internal": True}, receive, send)


class BasePathMiddleware:
    """Strip the public prefix before JWT/setup/Bridge middleware see requests."""

    def __init__(self, app: ASGIApp, base_path: str) -> None:
        self.app = app
        self.base_path = base_path

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"} or scope.get("octop.internal"):
            await self.app(scope, receive, send)
            return
        path = scope["path"]
        if path.startswith("/.well-known/acme-challenge/"):
            await self.app(scope, receive, send)
            return
        if scope["type"] == "http" and path == self.base_path:
            query = scope.get("query_string", b"").decode("latin-1")
            target = f"{self.base_path}/" + (f"?{query}" if query else "")
            await RedirectResponse(target, status_code=307)(scope, receive, send)
            return
        if not path.startswith(f"{self.base_path}/"):
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            else:
                await JSONResponse({"detail": "Not Found"}, status_code=404)(scope, receive, send)
            return

        public_url = URL(scope=scope)
        inner = dict(scope)
        inner["path"] = path[len(self.base_path) :]
        inner["raw_path"] = scope.get("raw_path", path.encode())[len(self.base_path) :]
        # Routing sees canonical paths, including when the prefix itself is /api.
        # Starlette uses app_root_path for base_url/url_for without stripping it
        # from the already-normalized path a second time.
        inner["app_root_path"] = scope.get("root_path", "").rstrip("/") + self.base_path
        inner["root_path"] = ""

        async def send_public(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = []
                for key, value in message.get("headers", []):
                    if key.lower() == b"location":
                        url = urlsplit(value.decode("latin-1"))
                        local = not url.netloc or (
                            url.netloc == public_url.netloc and url.scheme == public_url.scheme
                        )
                        prefixed = url.path == self.base_path or url.path.startswith(
                            f"{self.base_path}/"
                        )
                        if local and url.path.startswith("/") and not prefixed:
                            value = urlunsplit(url._replace(path=self.base_path + url.path)).encode(
                                "latin-1"
                            )
                    headers.append((key, value))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(inner, receive, send_public)
