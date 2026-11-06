"""Notifier interface. MockNotifier stores messages in the alerts table; EmailNotifier sends via SMTP."""
from __future__ import annotations

import smtplib
from abc import ABC, abstractmethod
from email.message import EmailMessage
import os


class Notifier(ABC):
    name = "notifier"
    source = "MOCK"

    @abstractmethod
    def send(self, *, recipients: list[str], subject: str, body: str, client_id: str | None = None,
             category: str = "general") -> str: ...


class MockNotifier(Notifier):
    name = "MockNotifier"
    source = "MOCK"

    def send(self, *, recipients, subject, body, client_id=None, category="general") -> str:
        from app.db.session import session_scope
        from app.models import Alert
        with session_scope() as db:
            db.add(Alert(client_id=client_id, recipients=recipients, subject=f"[MOCK] {subject}", body=body,
                         delivery_status="MOCK_DELIVERED", category=category, channel="email"))
        return "MOCK_DELIVERED"


class EmailNotifier(Notifier):
    """SMTP delivery (SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASSWORD / SMTP_FROM env vars)."""

    name = "EmailNotifier"
    source = "LIVE"

    def send(self, *, recipients, subject, body, client_id=None, category="general") -> str:
        from app.db.session import session_scope
        from app.models import Alert
        status = "SENT"
        try:
            msg = EmailMessage()
            msg["From"] = os.environ.get("SMTP_FROM", "alerts@localhost")
            msg["To"] = ", ".join(recipients)
            msg["Subject"] = subject
            msg.set_content(body)
            with smtplib.SMTP(os.environ.get("SMTP_HOST", "localhost"), int(os.environ.get("SMTP_PORT", "587")), timeout=15) as s:
                s.starttls()
                if os.environ.get("SMTP_USER"):
                    s.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASSWORD", ""))
                s.send_message(msg)
        except Exception as exc:  # delivery failure is recorded, never hidden
            status = f"FAILED: {type(exc).__name__}"
        with session_scope() as db:
            db.add(Alert(client_id=client_id, recipients=recipients, subject=subject, body=body,
                         delivery_status=status, category=category, channel="email"))
        return status
