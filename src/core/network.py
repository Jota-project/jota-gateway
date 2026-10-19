"""
network.py
~~~~~~~~~~
Resolución de origen de red confiable para /v1/* y /ws/stream.

is_trusted_origin(ip)   -> bool  — ip exenta de auth (loopback y/o TRUSTED_NETWORKS)
resolve_client_ip(conn)  -> str   — IP real a evaluar, respetando X-Real-IP
                                    solo cuando el peer TCP es un proxy confiable.
                                    Compartido por HTTP y WebSocket.
"""

import ipaddress
import logging

from starlette.requests import HTTPConnection

from src.core.config import settings

logger = logging.getLogger(__name__)

_IpNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network

# Not an IP on purpose: is_trusted_origin() rejects it, and it reads well in logs.
NO_REAL_IP = "proxy-without-real-ip"


def _parse_networks(csv: str) -> list[_IpNetwork]:
    networks: list[_IpNetwork] = []
    for raw in csv.split(","):
        raw = raw.strip()
        if not raw:
            continue
        try:
            networks.append(ipaddress.ip_network(raw, strict=False))
        except ValueError:
            logger.warning(f"Ignoring invalid CIDR/IP in network config: {raw!r}")
    return networks


def _ip_in_networks(ip_str: str, networks: list[_IpNetwork]) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    return any(ip in net for net in networks)


def is_trusted_proxy(ip_str: str) -> bool:
    """True if ip_str is allowed to set X-Real-IP (settings.TRUSTED_PROXIES)."""
    return _ip_in_networks(ip_str, _parse_networks(settings.TRUSTED_PROXIES))


def is_trusted_origin(ip_str: str) -> bool:
    """True if ip_str is exempt from /v1/* auth (loopback and/or TRUSTED_NETWORKS)."""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    if settings.TRUST_LOOPBACK and ip.is_loopback:
        return True
    return _ip_in_networks(ip_str, _parse_networks(settings.TRUSTED_NETWORKS))


def warn_if_open_proxy_config() -> None:
    """WARNING at startup for TRUST_LOOPBACK with an empty TRUSTED_PROXIES (#216).

    A same-host reverse proxy would then look like a loopback client for everyone behind
    it (its X-Real-IP is ignored), leaving /v1/* open to the Internet.
    """
    if settings.TRUST_LOOPBACK and not _parse_networks(settings.TRUSTED_PROXIES):
        logger.warning(
            "TRUST_LOOPBACK is on and TRUSTED_PROXIES is empty: a reverse proxy on this "
            "host would make every client look like loopback and /v1/* would be open. "
            "Fine without a proxy; otherwise set TRUSTED_PROXIES to the proxy address "
            "and make it overwrite X-Real-IP."
        )


def resolve_client_ip(connection: HTTPConnection) -> str:
    """
    Returns the IP to evaluate with is_trusted_origin().

    If the immediate TCP peer is a trusted proxy (settings.TRUSTED_PROXIES),
    its X-Real-IP is the client address. A proxy peer WITHOUT a valid X-Real-IP
    fails closed (G-31, #216): returning the peer would turn a same-host proxy
    (loopback is trusted) into an open door for every Internet client, so we
    return a non-IP sentinel that is_trusted_origin() rejects and /v1 asks for
    a Bearer. Otherwise the peer connected directly — use its address as-is and
    ignore any X-Real-IP it sends.
    """
    peer = connection.client.host if connection.client else ""

    if is_trusted_proxy(peer):
        real_ip = (connection.headers.get("x-real-ip") or "").strip()
        try:
            ipaddress.ip_address(real_ip)
        except ValueError:
            return NO_REAL_IP
        return real_ip

    if connection.headers.get("x-real-ip"):
        logger.warning(
            f"Ignoring X-Real-IP header from untrusted peer {peer!r} (possible spoofing attempt)"
        )
    return peer
