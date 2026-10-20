from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import AppSettings, MailboxState


def get_app_settings(db: Session) -> AppSettings:
    row = db.get(AppSettings, 1)
    if row is None:
        row = AppSettings(id=1)
        db.add(row)
        db.flush()
    return row


def get_mailbox_state(db: Session) -> MailboxState:
    row = db.get(MailboxState, 1)
    if row is None:
        row = MailboxState(id=1)
        db.add(row)
        db.flush()
    return row
