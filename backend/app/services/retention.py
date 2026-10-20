"""Data retention: delete customer emails older than the number of months set in Settings.

Runs at most once a day from the worker. 0 months (the default) keeps everything. Each run is
recorded in the audit log with how many emails were removed.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.models import (
    AiDraft,
    Attachment,
    Case,
    CaseAction,
    CaseEvent,
    CustomerEmail,
    EmailReply,
    ModelCall,
)
from app.services import audit
from app.services.app_settings import get_app_settings, get_mailbox_state


def purge_if_due(db: Session) -> int:
    cfg, state = get_app_settings(db), get_mailbox_state(db)
    today = utcnow().date()
    if not cfg.retention_months or state.last_retention_on == today:
        return 0
    cutoff = utcnow() - timedelta(days=30 * cfg.retention_months)
    old = select(CustomerEmail.id).where(CustomerEmail.received_at < cutoff)
    ids = list(db.scalars(old))
    if ids:
        case_ids = set(db.scalars(select(CustomerEmail.case_id).where(CustomerEmail.id.in_(ids))))
        db.execute(delete(EmailReply).where(EmailReply.email_id.in_(ids)))
        db.execute(delete(Attachment).where(Attachment.email_id.in_(ids)))
        db.execute(delete(AiDraft).where(AiDraft.email_id.in_(ids)))
        db.execute(update(ModelCall).where(ModelCall.email_id.in_(ids)).values(email_id=None))
        db.execute(update(CaseEvent).where(CaseEvent.email_id.in_(ids)).values(email_id=None))
        db.execute(delete(CustomerEmail).where(CustomerEmail.id.in_(ids)))
        # Cases with no email left go too, with their notes and actions.
        empty = [
            c
            for c in case_ids - {None}
            if db.scalar(select(CustomerEmail.id).where(CustomerEmail.case_id == c).limit(1)) is None
        ]
        if empty:
            db.execute(
                update(Case)
                .where(Case.possible_duplicate_of_id.in_(empty))
                .values(possible_duplicate_of_id=None)
            )
            db.execute(delete(CaseEvent).where(CaseEvent.case_id.in_(empty)))
            db.execute(delete(CaseAction).where(CaseAction.case_id.in_(empty)))
            db.execute(delete(Case).where(Case.id.in_(empty)))
        audit.record(
            db,
            "retention.purged",
            detail={"emails": len(ids), "cases": len(empty), "older_than_months": cfg.retention_months},
        )
    state.last_retention_on = today
    db.commit()
    return len(ids)
