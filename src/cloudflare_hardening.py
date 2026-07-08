"""Cloudflare deployment hardening for the NLC Dashboard — DORMANT by default.

This module is *staged, not active*. Your running app is untouched until you
BOTH (a) wire in the one call shown in this folder's README and (b) set the
`NLC_BEHIND_CLOUDFLARE` environment variable on the host. With that env var
unset, `apply()` is a complete no-op — so even wiring it in early cannot affect
local or LAN access.

When enabled (only correct when the app really is behind Cloudflare's HTTPS) it
does two things:

  1. **Secure session cookie** — marks the login cookie `Secure`, so the browser
     only ever sends it over HTTPS. Prevents a cookie from being sniffed on a
     plain-HTTP hop. (This is why it must stay OFF for local http://127.0.0.1
     use — a Secure cookie won't be sent over HTTP, which would break login.)

  2. **Trust the tunnel's forwarded headers** — applies Werkzeug's ProxyFix so
     the app correctly sees the request as HTTPS and can read the real visitor
     IP that `cloudflared` forwards (one hop: Cloudflare edge -> cloudflared ->
     app on localhost).

Why gate on an env var instead of always-on? Trusting `X-Forwarded-For` when the
app is NOT actually behind a trusted proxy lets anyone spoof their IP. Gating
means these only switch on where they're safe: the Cloudflare host.
"""
from __future__ import annotations

import os

_TRUTHY = {"1", "true", "yes", "on"}


def enabled() -> bool:
    """True only when NLC_BEHIND_CLOUDFLARE is set to a truthy value."""
    return os.environ.get("NLC_BEHIND_CLOUDFLARE", "").strip().lower() in _TRUTHY


def apply(app) -> bool:
    """Wire Cloudflare hardening into a Flask app.

    No-op unless `enabled()`. Returns True if hardening was applied, False if it
    was left dormant — safe to call unconditionally at startup.
    """
    if not enabled():
        return False

    # 1) Secure session cookie now that traffic is HTTPS all the way to the user.
    app.config.update(SESSION_COOKIE_SECURE=True)

    # 2) Trust exactly one hop of forwarded headers (cloudflared -> app).
    try:
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)
    except Exception:
        # ProxyFix missing shouldn't take the app down; Secure cookie still helps.
        pass

    return True


def real_client_ip(request) -> str:
    """The visitor's true IP when behind Cloudflare.

    Cloudflare sends the original client IP in `CF-Connecting-IP`. Falls back to
    the first `X-Forwarded-For` entry, then Flask's `remote_addr`. Use this in
    place of `request.remote_addr` anywhere you log or rate-limit by IP.
    """
    return (request.headers.get("CF-Connecting-IP")
            or request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
            or request.remote_addr
            or "")
