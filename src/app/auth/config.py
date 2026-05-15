"""Auth configuration from environment."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _truthy(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class AuthConfig:
    password_enabled: bool
    google_enabled: bool
    session_secret: str | None
    session_https_only: bool
    public_base_url: str
    google_client_id: str | None
    google_client_secret: str | None
    google_allowed_hd: str | None
    smtp_host: str | None
    smtp_port: int
    smtp_user: str | None
    smtp_password: str | None
    smtp_from: str | None
    smtp_use_tls: bool
    login_max_attempts: int
    login_window_seconds: int

    @property
    def any_enabled(self) -> bool:
        return self.password_enabled or self.google_enabled

    @classmethod
    def from_env(cls) -> AuthConfig:
        port_raw = os.getenv("BLINK_SMTP_PORT", "587").strip()
        try:
            smtp_port = int(port_raw)
        except ValueError:
            smtp_port = 587
        max_att = os.getenv("BLINK_AUTH_LOGIN_MAX_ATTEMPTS", "8").strip()
        try:
            login_max_attempts = max(1, int(max_att))
        except ValueError:
            login_max_attempts = 8
        win = os.getenv("BLINK_AUTH_LOGIN_WINDOW_SECONDS", "900").strip()
        try:
            login_window_seconds = max(60, int(win))
        except ValueError:
            login_window_seconds = 900
        return cls(
            password_enabled=_truthy("BLINK_AUTH_PASSWORD"),
            google_enabled=_truthy("BLINK_AUTH_GOOGLE"),
            session_secret=os.getenv("BLINK_SESSION_SECRET", "").strip() or None,
            session_https_only=_truthy("BLINK_SESSION_HTTPS_ONLY"),
            public_base_url=os.getenv("BLINK_PUBLIC_BASE_URL", "http://127.0.0.1:8080").rstrip("/"),
            google_client_id=os.getenv("BLINK_GOOGLE_CLIENT_ID", "").strip() or None,
            google_client_secret=os.getenv("BLINK_GOOGLE_CLIENT_SECRET", "").strip() or None,
            google_allowed_hd=os.getenv("BLINK_GOOGLE_ALLOWED_HD", "").strip() or None,
            smtp_host=os.getenv("BLINK_SMTP_HOST", "").strip() or None,
            smtp_port=smtp_port,
            smtp_user=os.getenv("BLINK_SMTP_USER", "").strip() or None,
            smtp_password=os.getenv("BLINK_SMTP_PASSWORD", "").strip() or None,
            smtp_from=os.getenv("BLINK_SMTP_FROM", "").strip() or None,
            smtp_use_tls=not _truthy("BLINK_SMTP_DISABLE_TLS"),
            login_max_attempts=login_max_attempts,
            login_window_seconds=login_window_seconds,
        )

    def validate_for_startup(self) -> None:
        if not self.any_enabled:
            return
        if not self.session_secret:
            raise ValueError(
                "BLINK_SESSION_SECRET is required when BLINK_AUTH_PASSWORD or BLINK_AUTH_GOOGLE is enabled."
            )
        if self.google_enabled:
            if not self.google_client_id or not self.google_client_secret:
                raise ValueError("BLINK_GOOGLE_CLIENT_ID and BLINK_GOOGLE_CLIENT_SECRET are required when Google auth is enabled.")
