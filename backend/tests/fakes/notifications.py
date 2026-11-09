"""TEST FIXTURE ONLY — never imported by the application."""
from __future__ import annotations

from app.adapters.notifications import Notifier


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
