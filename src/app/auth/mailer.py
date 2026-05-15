"""Send email via SMTP (optional)."""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage

from app.auth.config import AuthConfig


def smtp_configured(cfg: AuthConfig) -> bool:
    return bool(cfg.smtp_host and cfg.smtp_from)


def send_email(
    cfg: AuthConfig,
    *,
    to_addrs: list[str],
    subject: str,
    body_text: str,
    body_html: str | None = None,
) -> None:
    if not cfg.smtp_host or not cfg.smtp_from:
        raise RuntimeError("SMTP is not configured (BLINK_SMTP_HOST / BLINK_SMTP_FROM).")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg.smtp_from
    msg["To"] = ", ".join(to_addrs)
    msg.set_content(body_text)
    if body_html:
        msg.add_alternative(body_html, subtype="html")
    if cfg.smtp_use_tls:
        context = ssl.create_default_context()
        with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=30) as smtp:
            smtp.starttls(context=context)
            if cfg.smtp_user and cfg.smtp_password is not None:
                smtp.login(cfg.smtp_user, cfg.smtp_password)
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=30) as smtp:
            if cfg.smtp_user and cfg.smtp_password is not None:
                smtp.login(cfg.smtp_user, cfg.smtp_password)
            smtp.send_message(msg)
