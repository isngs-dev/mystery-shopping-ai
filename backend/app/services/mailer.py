"""Outgoing e-mail through the organisation's own SMTP server (settings SMTP_*). Never raises: returns False when mail is not set up or sending failed."""
from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage

from ..config import get_settings

log = logging.getLogger("msai.mail")


def configured() -> bool:
    s = get_settings()
    return bool(s.smtp_host and (s.smtp_from or s.smtp_user))


def send(to: str, subject: str, body: str) -> bool:
    s = get_settings()
    if not configured():
        return False
    msg = EmailMessage()
    msg["From"] = s.smtp_from or s.smtp_user
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    try:
        ctx = ssl.create_default_context()
        if s.smtp_ssl:
            server = smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, timeout=20, context=ctx)
        else:
            server = smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=20)
            server.starttls(context=ctx)
        with server:
            if s.smtp_user and s.smtp_password:
                server.login(s.smtp_user, s.smtp_password)
            server.send_message(msg)
        return True
    except Exception as exc:  # noqa: BLE001 - a mail problem must never break the request; the body (a code) is not logged
        log.warning("E-mail to %s failed: %s: %s", to, type(exc).__name__, exc)
        return False
