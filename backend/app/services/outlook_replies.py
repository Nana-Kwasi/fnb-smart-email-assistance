"""Notice when someone replied to a waiting customer email in Outlook, and mark it handled.

People answer customers in Outlook as they always have. For each email still waiting, the
worker looks in the mailbox's Sent Items for messages in the same conversation sent after the
customer's email. Anything there that is not one of the assistant's own replies (compared by
the opening text) means a person has answered.
"""

from __future__ import annotations

import logging
import re
from datetime import timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import as_utc, utcnow
from app.models import AiDraft, Case, CustomerEmail, DraftStatus, EmailStatus, HandledVia
from app.services import audit, cases
from app.services.app_settings import get_mailbox_state

if TYPE_CHECKING:
    from app.services.processor import Mailbox

log = logging.getLogger(__name__)

CHECK_EVERY = timedelta(minutes=5)
MAX_PER_CHECK = 100
_COMPARE_CHARS = 60


def _signature(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())[:_COMPARE_CHARS]


def check_if_due(db: Session, mailbox: Mailbox) -> int:
    state = get_mailbox_state(db)
    now = utcnow()
    if state.last_reply_check_at and now - as_utc(state.last_reply_check_at) < CHECK_EVERY:
        return 0
    state.last_reply_check_at = now
    db.commit()
    return check_waiting(db, mailbox)


def check_waiting(db: Session, mailbox: Mailbox) -> int:
    waiting = list(
        db.scalars(
            select(CustomerEmail)
            .where(CustomerEmail.status == EmailStatus.QUEUED, CustomerEmail.conversation_id.is_not(None))
            .order_by(CustomerEmail.received_at)
            .limit(MAX_PER_CHECK)
        ).unique()
    )
    handled = 0
    for email in waiting:
        try:
            sent = mailbox.sent_in_conversation(email.conversation_id)
        except Exception as exc:  # noqa: BLE001 - try again at the next check
            log.warning("Could not read Sent Items for %s: %s", email.reference, exc)
            continue
        ours = {_signature(r.body_text) for r in email.replies}
        human = [
            item
            for item in sent
            if as_utc(item.sent_at) > as_utc(email.received_at) and _signature(item.preview) not in ours
        ]
        if human:
            first = min(human, key=lambda i: i.sent_at)
            email.status, email.handled_via, email.handled_at = (
                EmailStatus.REPLIED,
                HandledVia.OUTLOOK,
                first.sent_at,
            )
            email.status_reason = "Replied in Outlook"
            for draft in db.scalars(
                select(AiDraft).where(AiDraft.email_id == email.id, AiDraft.status.in_(DraftStatus.OPEN))
            ):
                draft.status = DraftStatus.SUPERSEDED
            case = db.get(Case, email.case_id) if email.case_id else None
            if case is not None:
                cases.add_event(db, case, "answered_in_outlook", email=email)
                cases.sync(db, case)
            audit.record(
                db,
                "email.replied_in_outlook",
                entity_type="email",
                entity_id=email.id,
                detail={"reference": email.reference},
            )
            handled += 1
    db.commit()
    return handled
