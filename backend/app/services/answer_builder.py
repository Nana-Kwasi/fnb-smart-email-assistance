"""Turn a topic plus a customer email into a reply, using only data from the bank's database.

Returns either the full reply text, or why no reply can be built (with a reason code for the
dashboard and the digest). When there is no data, nothing is sent.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import AppSettings, CustomerEmail, Reason, TopicRevision
from app.services.datasources import (
    DataSources,
    QueryRejected,
    TemplateError,
    builder_is_customer_specific,
    email_value_label,
    extract_params,
    query_is_customer_specific,
    query_params,
    render_reply,
)
from app.services.email_text import customer_greeting_name, fill, strip_quoted_history

log = logging.getLogger(__name__)


@dataclass
class BuiltAnswer:
    text: str | None
    reason: str
    reason_code: str | None = None
    rows_found: int = 0


def sender_is_verified(headers: dict[str, str]) -> bool:
    """True when Microsoft 365 recorded a DMARC pass for this email's From address."""
    for name, value in headers.items():
        if name.lower() == "authentication-results" and re.search(r"\bdmarc=pass\b", value, re.IGNORECASE):
            return True
    return False


def wrap_answer(cfg: AppSettings, email: CustomerEmail, body: str) -> str:
    """The greeting and sign-off from Settings around an answer."""
    name = customer_greeting_name(email.from_name)
    parts = [
        fill(cfg.answer_header, customer_name=name, reference=email.reference),
        body,
        fill(cfg.answer_footer, customer_name=name, reference=email.reference),
    ]
    return "\n\n".join(p.strip() for p in parts if p and p.strip())


def build_answer(
    db: Session, revision: TopicRevision, email: CustomerEmail, cfg: AppSettings, sources: DataSources
) -> BuiltAnswer:
    customer_specific = (
        builder_is_customer_specific(revision.builder)
        if revision.builder
        else query_is_customer_specific(revision.query, revision.extractors)
    )
    if customer_specific and not get_settings().customer_data_answers:
        return BuiltAnswer(
            None,
            "This topic needs the customer's own records, which are not enabled yet (phase 1)",
            Reason.CUSTOMER_DATA_OFF,
        )
    if revision.require_verified_sender and not email.sender_verified:
        return BuiltAnswer(
            None,
            "The sender's address could not be verified, so customer data cannot be sent by email",
            Reason.UNVERIFIED_SENDER,
        )

    question = strip_quoted_history(email.body_text)
    params: dict[str, str | None] = {
        "sender_email": email.from_address.lower(),
        "sender_name": email.from_name,
        "subject": email.subject,
        "reference": email.reference,
        **extract_params(revision.extractors or {}, f"{email.subject}\n{question}"),
    }

    if revision.builder:
        conditions = revision.builder.get("conditions", [])
        missing = [
            email_value_label(c)
            for i, c in enumerate(conditions)
            if c.get("source") == "email_value" and not params.get(f"v{i}")
        ]
    else:
        missing = [p.replace("_", " ") for p in query_params(revision.query or "") if not params.get(p)]
    if missing:
        return BuiltAnswer(None, "The email does not include the " + ", ".join(missing), Reason.MISSING_INFO)

    try:
        ds = sources.get(db, revision.data_source)
        if revision.builder:
            result = sources.run_builder(ds, revision.builder, params, revision.max_rows)
        else:
            needed = query_params(revision.query or "")
            result = sources.run_sql(ds, revision.query, {p: params[p] for p in needed}, revision.max_rows)
    except Exception as exc:  # noqa: BLE001 - database trouble must never reach the customer
        log.error("Reading the answer for topic '%s' failed: %s", revision.title, type(exc).__name__)
        detail = str(exc) if isinstance(exc, QueryRejected) else type(exc).__name__
        return BuiltAnswer(
            None, f"Could not read the answer from '{revision.data_source}' ({detail})", Reason.DATA_ERROR
        )
    if not result.rows:
        return BuiltAnswer(None, "No matching record was found in the database", Reason.NO_DATA)

    name = customer_greeting_name(email.from_name)
    context = {"customer_name": name, "reference": email.reference}
    try:
        body = render_reply(revision.reply_template, revision.row_template, result.rows, context)
    except TemplateError as exc:
        return BuiltAnswer(None, str(exc), Reason.DATA_ERROR, rows_found=len(result.rows))

    text = wrap_answer(cfg, email, body)
    return BuiltAnswer(text, f"Answered from '{revision.data_source}'", rows_found=len(result.rows))
