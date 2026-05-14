"""Resolve the *real* client IP across the dev / prod proxy chain.

The deployment topology is:

    [browser] ──┬─→ Cloudflare ─→ overseas relay ─→ frp tunnel ─→ backend
                │
                └─→ LAN direct ─→ backend                       (no proxy)

That means a request reaching the FastAPI app can sit at one of three
levels of indirection, and each one needs a different signal to recover
the "true" client IP:

1. **Cloudflare-fronted**: ``request.client.host`` is the frp container's
   address (e.g. ``172.23.0.1``) — useless. The right value lives in
   the ``CF-Connecting-IP`` header which Cloudflare sets to the original
   visitor IP. We trust it whenever it's present because the only paths
   into this backend that *can* produce that header are the CF tunnel
   itself; LAN clients won't set it.

2. **Direct via frp (rare; no CF)**: there is no ``CF-Connecting-IP``.
   ``X-Forwarded-For`` may be present (frp's HTTP mode adds it). We
   take the leftmost entry, which is the originator.

3. **LAN direct**: no proxy headers at all; ``request.client.host`` is
   already the real IP (e.g. ``192.168.1.16``).

The fallback order ``CF → XFF → peer`` makes all three cases work
without configuration.

We deliberately don't validate the inbound headers against a whitelist
of Cloudflare IPs here — the backend is not directly exposed to the
internet (CF + frp / LAN only), so spoofing the header would require
being already on the trusted path. If that assumption ever changes the
right place to add the check is in front of ``client_ip_from``, not in
every caller.
"""

from __future__ import annotations

from typing import Literal

from fastapi import Request

# A lightweight tag describing where the IP came from. Useful in the
# access log for operators trying to spot "all requests look like
# 172.23.0.1" symptoms quickly.
IpSource = Literal["cf", "xff", "peer", "unknown"]


CF_HEADER = "cf-connecting-ip"
XFF_HEADER = "x-forwarded-for"


def client_ip_from(request: Request) -> str | None:
    """Best-effort real-IP resolution. Convenience wrapper for callers
    that don't care about the source tag.
    """
    ip, _ = client_ip_with_source(request)
    return ip


def client_ip_with_source(request: Request) -> tuple[str | None, IpSource]:
    """Resolve the client IP and report which header / signal it came from.

    The order of precedence is the one documented in the module
    docstring: ``CF-Connecting-IP`` → leftmost ``X-Forwarded-For`` →
    socket peer.

    Returns ``(None, "unknown")`` only when ``request.client`` is also
    absent (rare — happens for ASGI test transports without a client
    tuple).
    """
    cf = request.headers.get(CF_HEADER)
    if cf:
        # CF sends a single IP, never a list. Strip whitespace defensively.
        cleaned = cf.strip()
        if cleaned:
            return cleaned, "cf"

    fwd = request.headers.get(XFF_HEADER)
    if fwd:
        # XFF is a comma-separated list of hops; the leftmost is the
        # originator (each hop appends, never prepends). Edge cases:
        # empty entries, IPv6 with brackets, port suffixes — strip what
        # we can but don't try to validate; the log consumer can.
        first = fwd.split(",", 1)[0].strip()
        if first:
            return first, "xff"

    peer = request.client.host if request.client else None
    if peer:
        return peer, "peer"
    return None, "unknown"


__all__ = (
    "IpSource",
    "CF_HEADER",
    "XFF_HEADER",
    "client_ip_from",
    "client_ip_with_source",
)
