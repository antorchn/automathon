"""Middleware de confiance du reverse proxy (X-Forwarded-For / X-Forwarded-Proto)."""

from __future__ import annotations

from starlette.types import ASGIApp, Receive, Scope, Send


class TrustedProxyMiddleware:
    """Ne fait confiance aux en-têtes X-Forwarded-* que si le pair TCP est dans `trusted_proxies`."""

    def __init__(self, app: ASGIApp, trusted_proxies: set[str]):
        self.app = app
        self.trusted_proxies = trusted_proxies

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            client = scope.get("client")
            peer_ip = client[0] if client else ""
            if peer_ip in self.trusted_proxies:
                headers = dict(scope.get("headers") or [])
                xff = headers.get(b"x-forwarded-for")
                if xff:
                    first_ip = xff.decode("latin1").split(",")[0].strip()
                    if first_ip:
                        scope["client"] = (first_ip, client[1] if client else 0)
                xfp = headers.get(b"x-forwarded-proto")
                if xfp:
                    proto = xfp.decode("latin1").split(",")[0].strip().lower()
                    if proto in ("http", "https", "ws", "wss"):
                        scope["scheme"] = proto
        await self.app(scope, receive, send)
