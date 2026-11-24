"""Middleware de confiance des reverse proxys (X-Forwarded-For / X-Forwarded-Proto)."""

from __future__ import annotations

import ipaddress

from starlette.types import ASGIApp, Receive, Scope, Send

IpNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network


class TrustedProxies:
    """Proxys de confiance : adresses IP, réseaux (« 172.16.0.0/12 ») ou noms (« testclient » en test)."""

    def __init__(self, entries: set[str] | list[str]):
        self.networks: list[IpNetwork] = []
        self.names: set[str] = set()
        for entry in entries:
            entry = entry.strip()
            if not entry:
                continue
            try:
                self.networks.append(ipaddress.ip_network(entry, strict=False))
            except ValueError:
                self.names.add(entry)

    def __contains__(self, host: str) -> bool:
        if host in self.names:
            return True
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            return False
        return any(ip in net for net in self.networks)


def client_from_forwarded_for(header: str, trusted: TrustedProxies) -> str | None:
    """Adresse du visiteur dans X-Forwarded-For : la plus à droite qui n'est pas un proxy de confiance.

    Chaque proxy ajoute à droite l'adresse qu'il voit ; ce qu'un visiteur écrit lui-même dans l'en-tête reste à
    gauche et ne peut donc pas remplacer sa vraie adresse.
    """
    hops = [h.strip() for h in header.split(",") if h.strip()]
    for hop in reversed(hops):
        if hop not in trusted:
            return hop
    return hops[0] if hops else None


class TrustedProxyMiddleware:
    """Ne croit les en-têtes X-Forwarded-* que si le pair TCP est un proxy de confiance (TRUSTED_PROXY)."""

    def __init__(self, app: ASGIApp, trusted_proxies: set[str]):
        self.app = app
        self.trusted = TrustedProxies(trusted_proxies)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            client = scope.get("client")
            peer_ip = client[0] if client else ""
            if peer_ip in self.trusted:
                headers = dict(scope.get("headers") or [])
                xff = headers.get(b"x-forwarded-for")
                if xff:
                    real_ip = client_from_forwarded_for(xff.decode("latin1"), self.trusted)
                    if real_ip:
                        scope["client"] = (real_ip, client[1] if client else 0)
                xfp = headers.get(b"x-forwarded-proto")
                if xfp:
                    proto = xfp.decode("latin1").split(",")[0].strip().lower()
                    if proto in ("http", "https", "ws", "wss"):
                        scope["scheme"] = proto
        await self.app(scope, receive, send)
