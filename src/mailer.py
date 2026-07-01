"""Outbound email via SMTP (defaults tuned for Microsoft 365 / Outlook).

Config is a plain dict (persisted in the settings table under "mail_config"):
    host, port, sender, username, password, use_tls

Kept deliberately small: stdlib smtplib only, no third-party dependency. Every
send returns (ok: bool, error: str) so callers can show a friendly message
instead of crashing on a network/auth failure.
"""
from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage

DEFAULTS = {
    "host": "smtp.office365.com",
    "port": 587,
    "sender": "",
    "username": "",
    "password": "",
    "use_tls": True,
}


def merged_config(saved: dict | None) -> dict:
    cfg = dict(DEFAULTS)
    if saved:
        cfg.update({k: v for k, v in saved.items() if v is not None})
    return cfg


def is_configured(cfg: dict) -> bool:
    """Enough set to attempt a send? Only a host + from-address are required;
    a username/password is optional (blank = send without signing in, e.g.
    Microsoft 365 'direct send' or an internal relay)."""
    return bool(cfg.get("host") and cfg.get("sender"))


def send_email(cfg: dict, to: str, subject: str, body: str) -> tuple[bool, str]:
    """Send a plain-text email. Returns (ok, error_message).

    Authentication is optional: if no username/password is set, the message is
    sent without logging in (so no app password is needed). If a username and
    password are given, it authenticates with them.
    """
    cfg = merged_config(cfg)
    if not is_configured(cfg):
        return False, "Email is not configured. An administrator must set up mail settings first."
    if not to:
        return False, "No recipient address."

    msg = EmailMessage()
    msg["From"] = cfg["sender"]
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    host = str(cfg.get("host") or DEFAULTS["host"])
    try:
        port = int(cfg.get("port") or DEFAULTS["port"])
    except (TypeError, ValueError):
        port = DEFAULTS["port"]

    try:
        with smtplib.SMTP(host, port, timeout=20) as server:
            server.ehlo()
            if cfg.get("use_tls", True):
                try:
                    server.starttls(context=ssl.create_default_context())
                    server.ehlo()
                except smtplib.SMTPNotSupportedError:
                    pass  # server offers no STARTTLS (e.g. plain direct-send relay)
            if cfg.get("username") and cfg.get("password"):
                server.login(cfg["username"], cfg["password"])
            server.send_message(msg)
        return True, ""
    except smtplib.SMTPAuthenticationError:
        return False, ("The mail server rejected the username/password. Leave them blank to send "
                       "without signing in (direct send), or check the credentials.")
    except (smtplib.SMTPException, OSError) as e:
        return False, f"Could not send email: {e}"


def send_verification_code(cfg: dict, to_email: str, name: str, code: str) -> tuple[bool, str]:
    body = (
        f"Hi {name or 'there'},\n\n"
        "Someone requested access to the NLC Financial Dashboard with this email address.\n\n"
        f"Your verification code is:  {code}\n\n"
        "Enter this code on the verification screen to confirm your email. After that, an "
        "administrator will approve your account before you can sign in.\n\n"
        "This code expires in 24 hours. If you didn't request this, you can ignore this email.\n"
    )
    return send_email(cfg, to_email, "Your NLC Financial Dashboard verification code", body)


def send_test(cfg: dict, to_email: str) -> tuple[bool, str]:
    body = ("This is a test message from the NLC Financial Dashboard.\n\n"
            "If you received this, outbound email is working correctly.\n")
    return send_email(cfg, to_email, "NLC Financial Dashboard — test email", body)
