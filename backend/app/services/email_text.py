"""Cleaning incoming email text and building outgoing replies."""

from __future__ import annotations

import re
import secrets
from datetime import datetime
from html import escape

MAX_BODY_CHARS = 20_000

# Where the previous conversation starts in a reply; everything after it is history, not the
# customer's new question.
_QUOTE_MARKERS = [
    re.compile(r"^On .{5,200} wrote:\s*$", re.MULTILINE),
    re.compile(r"^-{2,}\s*Original Message\s*-{2,}", re.MULTILINE | re.IGNORECASE),
    re.compile(r"^From:\s.+$\n^(Sent|Date):\s", re.MULTILINE),
    re.compile(r"^_{10,}\s*$", re.MULTILINE),
]

# Placeholders staff may use in templates. Substitution is plain text replacement of exactly
# these tokens, never str.format, so a template cannot reach anything else.
PLACEHOLDERS = ("{customer_name}", "{reference}")

_NO_REPLY_SENDER = re.compile(r"^(no-?reply|do-?not-?reply|mailer-daemon|postmaster|bounce)", re.IGNORECASE)


def strip_quoted_history(text: str) -> str:
    cut = len(text)
    for marker in _QUOTE_MARKERS:
        m = marker.search(text)
        if m:
            cut = min(cut, m.start())
    lines = [ln for ln in text[:cut].splitlines() if not ln.lstrip().startswith(">")]
    return "\n".join(lines).strip()


def clean_body(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\xa0", " ")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()[:MAX_BODY_CHARS]


def new_reference(received_at: datetime) -> str:
    return f"FNB-{received_at:%y%m%d}-{secrets.token_hex(3).upper()}"


def fill(template: str, *, customer_name: str, reference: str) -> str:
    return template.replace("{customer_name}", customer_name).replace("{reference}", reference)


def customer_greeting_name(from_name: str) -> str:
    name = (from_name or "").strip()
    # Display names that are really email addresses read badly in a greeting.
    if not name or "@" in name:
        return "Valued Customer"
    return name[:100]


def to_html(text: str) -> str:
    """Plain text to simple HTML that Outlook renders reliably. Everything is escaped."""
    paragraphs = [p for p in text.split("\n\n") if p.strip()]
    body = "".join(
        f'<p style="margin:0 0 14px 0">{escape(p).replace(chr(10), "<br>")}</p>' for p in paragraphs
    )
    return (
        '<div style="font-family:Segoe UI,Arial,sans-serif;font-size:14px;line-height:1.5;color:#0F2B2A">'
        f'<div style="border-top:4px solid #00857F;padding-top:14px">{body}</div></div>'
    )


def automation_check(sender: str, headers: dict[str, str], own_mailbox: str) -> tuple[str, bool] | None:
    """Decide whether this email may get automatic replies.

    Replying to auto-replies, bounces or our own mailbox can start an endless email loop.
    Returns None when automatic replies are fine, otherwise ``(reason, ignore)``: ``ignore``
    True means it is not a customer email at all (skip it); False means a person should still
    read it, but nothing automatic may be sent.
    """
    sender_l = sender.lower()
    if own_mailbox and sender_l == own_mailbox.lower():
        return "Sent from our own mailbox", True
    if _NO_REPLY_SENDER.match(sender_l.split("@")[0]):
        return "Sender is a no-reply or system address", True
    h = {k.lower(): v.lower() for k, v in headers.items()}
    if h.get("auto-submitted", "no") != "no":
        return "Automatic message (Auto-Submitted header)", True
    if "x-autoreply" in h or "x-autorespond" in h:
        return "Automatic reply", True
    if h.get("precedence") in ("bulk", "junk", "list", "auto_reply"):
        return "Bulk or mailing-list message", True
    if "list-unsubscribe" in h or "list-id" in h:
        return "Mailing-list message", True
    if "all" in h.get("x-auto-response-suppress", ""):
        return "Sender asked not to receive automatic replies", False
    return None
