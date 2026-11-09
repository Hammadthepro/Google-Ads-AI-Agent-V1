"""Notifier interface. InAppNotifier records alerts in the app only (no email); EmailNotifier sends via SMTP."""
from __future__ import annotations

import smtplib
from abc import ABC, abstractmethod
from email.message import EmailMessage

from app.core.config import get_settings


class Notifier(ABC):
    name = "notifier"
    source = "LIVE"

    @abstractmethod
    def send(self, *, recipients: list[str], subject: str, body: str, client_id: str | None = None,
             category: str = "general") -> str: ...




class InAppNotifier(Notifier):
    """Used when SMTP is not configured. The alert is stored and shown in the app; it is NOT emailed, and says so."""

    name = "InAppNotifier"
    source = "LIVE"

    def send(self, *, recipients, subject, body, client_id=None, category="general") -> str:
        from app.db.session import session_scope
        from app.models import Alert
        with session_scope() as db:
            db.add(Alert(client_id=client_id, recipients=recipients, subject=subject, body=body,
                         delivery_status="NOT_EMAILED_SMTP_NOT_CONFIGURED", category=category, channel="in_app"))
        return "NOT_EMAILED_SMTP_NOT_CONFIGURED"


class EmailNotifier(Notifier):
    """SMTP delivery (SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASSWORD / SMTP_FROM, loaded through Settings)."""

    name = "EmailNotifier"
    source = "LIVE"

    def send(self, *, recipients, subject, body, client_id=None, category="general") -> str:
        from app.db.session import session_scope
        from app.models import Alert
        status = "SENT"
        try:
            msg = EmailMessage()
            cfg = get_settings()
            msg["From"] = cfg.SMTP_FROM
            msg["To"] = ", ".join(recipients)
            msg["Subject"] = subject
            msg.set_content(body)
            with smtplib.SMTP(cfg.SMTP_HOST or "localhost", cfg.SMTP_PORT, timeout=15) as s:
                s.starttls()
                if cfg.SMTP_USER:
                    s.login(cfg.SMTP_USER, cfg.SMTP_PASSWORD)
                s.send_message(msg)
        except Exception as exc:  # delivery failure is recorded, never hidden
            status = f"FAILED: {type(exc).__name__}"
        with session_scope() as db:
            db.add(Alert(client_id=client_id, recipients=recipients, subject=subject, body=body,
                         delivery_status=status, category=category, channel="email"))
        return status
