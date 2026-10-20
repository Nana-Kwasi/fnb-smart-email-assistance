"""Fill a local development setup with sample data, so every screen has something to show.

Run after the migrations:  python -m app.dev_seed

Creates:
  * three published knowledge base documents (an FAQ, a card services spreadsheet, loan information),
  * in phase 2 only: a sample "bank" database, its data source, and topics that read from it,
  * a handful of customer emails, processed exactly as real ones would be.

Refuses to run unless FNB_ENV=development, and only ever writes to a SQLite sample database.
"""

from __future__ import annotations

import secrets
import sys
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, select, text

from app.core.config import get_settings
from app.core.db import SessionLocal, utcnow
from app.models import DataSource, DataSourceKind, KnowledgeDocument, StaffUser, Topic
from app.services.app_settings import get_app_settings, get_mailbox_state
from app.services.datasources import DataSources
from app.services.graph_client import InboxMessage
from app.services.knowledge import add_version, publish
from app.services.llm import get_model
from app.services.processor import poll_once
from app.services.topics import TopicDraft, approve, propose

SAMPLE_SOURCE = "sample_bank"
SAMPLE_FILE = "./dev-bank.db"
VERIFIED = {"Authentication-Results": "spf=pass; dkim=pass; dmarc=pass action=none"}

BANK_SQL = """
DROP TABLE IF EXISTS faq;
DROP TABLE IF EXISTS accounts;
DROP TABLE IF EXISTS transactions;
CREATE TABLE faq (code TEXT PRIMARY KEY, question TEXT, answer TEXT);
INSERT INTO faq VALUES
  ('HOURS', 'What are your branch opening hours?', 'Our branches are open from 08:30 to 16:00, Monday to Friday, and from 09:00 to 12:00 on Saturdays.'),
  ('PIN', 'How do I reset my card PIN?', 'You can reset your card PIN at any FNB ATM: insert your card, choose "Change PIN" and follow the steps.'),
  ('OPEN_ACCOUNT', 'What do I need to open an account?', 'Bring your Ghana Card, proof of address (a recent utility bill) and a passport photo to any branch. Opening takes about 20 minutes.'),
  ('STATEMENT', 'How do I get a bank statement?', 'You can download statements in the FNB app under Accounts > Statements, or request a stamped statement at any branch.'),
  ('APP_REGISTER', 'How do I register for the FNB app?', 'Download the FNB Ghana app, choose "Register" and follow the steps using your account number and the phone number registered with us.'),
  ('SWIFT', 'What is your SWIFT code?', '[Sample data] Our SWIFT/BIC code is SAMPLEXX. Include your full account number and name on incoming international transfers.');
CREATE TABLE accounts (account_number TEXT PRIMARY KEY, email TEXT, holder TEXT, product TEXT, balance NUMERIC);
INSERT INTO accounts VALUES
  ('6200123456', 'ama.mensah@example.com', 'Ama Mensah', 'Savings', 1520.50),
  ('6200654321', 'kofi.boateng@example.com', 'Kofi Boateng', 'Cheque', 8930.00);
CREATE TABLE transactions (account_number TEXT, posted_on DATE, description TEXT, amount NUMERIC);
INSERT INTO transactions VALUES
  ('6200123456', '2026-10-01', 'MTN Mobile Money transfer', -250.00),
  ('6200123456', '2026-10-03', 'Salary', 4200.00),
  ('6200123456', '2026-10-04', 'Shoprite Accra Mall', -318.75);
"""

GENERAL_TOPICS = [
    TopicDraft(
        title="Reset card PIN",
        examples=["How do I reset my PIN?", "I forgot my card PIN", "change my ATM pin"],
        data_source=SAMPLE_SOURCE,
        builder={
            "table": "faq",
            "columns": ["answer"],
            "conditions": [{"column": "code", "source": "fixed", "value": "PIN"}],
        },
        reply_template="{answer}",
        require_verified_sender=False,
    ),
    TopicDraft(
        title="Branch opening hours",
        examples=["What time do your branches open?", "branch opening hours", "are you open on Saturday"],
        data_source=SAMPLE_SOURCE,
        builder={
            "table": "faq",
            "columns": ["answer"],
            "conditions": [{"column": "code", "source": "fixed", "value": "HOURS"}],
        },
        reply_template="{answer}",
        require_verified_sender=False,
    ),
]

# Phase 2 only (FNB_CUSTOMER_DATA_ANSWERS=true): answers from the customer's own records.
CUSTOMER_TOPICS = [
    TopicDraft(
        title="Account balance",
        examples=["What is my account balance?", "how much money is in my account", "check my balance"],
        data_source=SAMPLE_SOURCE,
        builder={
            "table": "accounts",
            "columns": ["holder", "product", "balance"],
            "conditions": [
                {"column": "account_number", "source": "email_value", "value_kind": "account_number"},
                {"column": "email", "source": "sender_email"},
            ],
        },
        reply_template="The available balance on your {product} account is {balance:money}.",
    ),
    TopicDraft(
        title="Recent transactions",
        examples=[
            "Can you send my recent transactions?",
            "last transactions on my account",
            "mini statement",
        ],
        data_source=SAMPLE_SOURCE,
        # An "advanced" topic: a hand-written query joining two tables.
        query="SELECT t.posted_on, t.description, t.amount FROM transactions t "
        "JOIN accounts a ON a.account_number = t.account_number "
        "WHERE a.account_number = :account_number AND lower(a.email) = :sender_email "
        "ORDER BY t.posted_on DESC",
        extractors={"account_number": r"\b(\d{10,16})\b"},
        reply_template="Here are the most recent transactions on your account:\n{rows}",
        row_template="- {posted_on:date}: {description}, {amount:money}",
        max_rows=5,
    ),
]

EMAILS = [
    (
        "PIN",
        "Hi, I forgot my card PIN. How can I reset it?",
        "abena.owusu@example.com",
        "Abena Owusu",
        VERIFIED,
    ),
    (
        "Loan",
        "What is the maximum personal loan I can get?",
        "kojo.mensah@example.com",
        "Kojo Mensah",
        VERIFIED,
    ),
    (
        "Opening hours",
        "Hello, what time do your branches open on Saturday?",
        "kwesi.appiah@example.com",
        "Kwesi Appiah",
        VERIFIED,
    ),
    (
        "Balance enquiry",
        "Good day, what is my account balance? My account number is 6200123456.",
        "ama.mensah@example.com",
        "Ama Mensah",
        VERIFIED,
    ),
    (
        "Transactions",
        "Please can you send my last transactions on account 6200123456?",
        "ama.mensah@example.com",
        "Ama Mensah",
        VERIFIED,
    ),
    (
        "Unauthorised debit - FRAUD",
        "Someone took money from my account without my permission. Please help!",
        "yaw.darko@example.com",
        "Yaw Darko",
        VERIFIED,
    ),
    (
        "Statement request",
        "Please send me my bank statement for the last 3 months for my visa application.",
        "efua.asante@example.com",
        "Efua Asante",
        VERIFIED,
    ),
    (
        "Balance",
        "What is my account balance? 6200123456",
        "someone@spoofed.example",
        "Ama M",
        {"Authentication-Results": "dmarc=fail"},
    ),
    (
        "Out of office",
        "I am out of the office until Monday.",
        "auto@example.com",
        "Auto Reply",
        {"Auto-Submitted": "auto-replied"},
    ),
]


class SampleMailbox:
    """Hands the sample emails to the processor and quietly accepts replies and alerts."""

    def __init__(self, messages: list[InboxMessage]):
        self._messages = {m.id: m for m in messages}

    def inbox_changes(self, delta_link):
        return list(self._messages), "sample-delta"

    def get_message(self, message_id):
        return self._messages[message_id]

    def reply(self, message_id, html):
        return None

    def send_mail(self, to, subject, html, cc=None):
        return None

    def sent_in_conversation(self, conversation_id):
        return []

    def list_attachments(self, message_id, max_bytes):
        return []


SAMPLE_FAQ_MD = """# Customer FAQ (sample data)

Q: What are your branch opening hours?
A: Our branches are open from 08:30 to 16:00, Monday to Friday, and from 09:00 to 12:00 on Saturdays.

Q: How do I get a bank statement?
A: You can download statements in the FNB app under Accounts > Statements, or request a stamped statement at any branch.

Q: What do I need to open an account?
A: Bring your Ghana Card, proof of address (a recent utility bill) and a passport photo to any branch. Opening takes about 20 minutes.

Q: How do I register for the FNB app?
A: Download the FNB Ghana app, choose "Register" and follow the steps using your account number and the phone number registered with us.
"""

SAMPLE_LOANS_MD = """# Loans (sample data)

## Personal loan requirements
You need three months of payslips, your Ghana Card, and an FNB account that your salary is paid into.

Q: What is the maximum personal loan?
A: Up to five times your monthly net salary, subject to an affordability check.
"""

SAMPLE_CARD_ROWS = [
    ("Question", "Answer"),
    (
        "How do I reset my card PIN?",
        'Reset your PIN at any FNB ATM: insert your card, choose "Change PIN" and follow the steps.',
    ),
    (
        "How long does a replacement card take?",
        "Replacement cards are ready at your branch within 5 working days.",
    ),
    (
        "Can I use my card abroad?",
        "Yes. Visa cards work abroad; tell us before you travel through the app under Cards > Travel notice.",
    ),
]


def _sample_xlsx() -> bytes:
    import io

    from openpyxl import Workbook

    wb = Workbook()
    for row in SAMPLE_CARD_ROWS:
        wb.active.append(row)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def _sample_people(db) -> tuple[StaffUser, StaffUser]:
    """Two placeholder people, so the maker-checker rule is followed even for sample data."""
    people = []
    for username, label in (("sample.author", "author"), ("sample.approver", "approver")):
        user = db.scalar(select(StaffUser).where(StaffUser.username == username))
        if user is None:
            user = StaffUser(
                username=username, display_name=f"Sample data ({label})", role="admin", is_active=False
            )
            db.add(user)
            db.flush()
        people.append(user)
    return people[0], people[1]


def _seed_knowledge(db) -> None:
    if db.scalar(select(KnowledgeDocument.id).limit(1)) is not None:
        return
    author, approver = _sample_people(db)
    samples = [
        ("Customer FAQ", "General", "customer-faq.md", SAMPLE_FAQ_MD.encode()),
        ("Card services", "Cards", "card-services.xlsx", _sample_xlsx()),
        ("Loan information", "Loans", "loans.md", SAMPLE_LOANS_MD.encode()),
    ]
    for name, category, filename, data in samples:
        doc = KnowledgeDocument(name=name, category=category, created_by_id=author.id)
        db.add(doc)
        version = add_version(
            db,
            author,
            doc,
            filename=filename,
            data=data,
            version_label=None,
            effective_from=None,
            expires_on=None,
            notes="Sample data",
        )
        publish(db, approver, version)
    db.commit()
    print(f"Published {len(samples)} sample knowledge base documents.")


def _seed_phase2(db, settings, sources) -> None:
    """Phase 2 only: a sample bank database, its data source, the FAQ table and topics."""
    bank = create_engine(f"sqlite:///{SAMPLE_FILE}")
    with bank.begin() as conn:
        for statement in filter(str.strip, BANK_SQL.split(";")):
            conn.execute(text(statement))
    print(f"Sample bank database ready ({SAMPLE_FILE}).")
    if db.scalar(select(DataSource.id).where(DataSource.name == SAMPLE_SOURCE)) is None:
        sample = DataSource(name=SAMPLE_SOURCE, kind=DataSourceKind.SQLITE, database=SAMPLE_FILE)
        db.add(sample)
        db.flush()
        sample.last_check_ok, sample.last_check_at = sources.check(sample) is None, utcnow()
        db.commit()
        print(f"Added the '{SAMPLE_SOURCE}' data source.")
    if db.scalar(select(Topic.id).limit(1)) is None:
        author, approver = _sample_people(db)
        drafts = GENERAL_TOPICS + CUSTOMER_TOPICS
        for draft in drafts:
            revision = propose(db, author, draft, sources)
            approve(db, approver, revision.topic, revision.id)
        print(f"Created {len(drafts)} approved topics.")
    cfg = get_app_settings(db)
    if not cfg.faq_table:
        cfg.faq_data_source, cfg.faq_table, cfg.faq_key_column = SAMPLE_SOURCE, "faq", "code"
        cfg.faq_question_column, cfg.faq_answer_column = "question", "answer"
    db.commit()


def main() -> None:
    settings = get_settings()
    if settings.env != "development":
        sys.exit("dev_seed only runs when FNB_ENV=development.")
    sources = DataSources(settings)
    with SessionLocal() as db:
        _seed_knowledge(db)
        if settings.customer_data_answers:
            _seed_phase2(db, settings, sources)

        cfg = get_app_settings(db)
        cfg.auto_reply_enabled = True  # sample data only: shows automatic answers working
        state = get_mailbox_state(db)
        state.delta_link = state.delta_link or "sample-delta"
        state.process_from = datetime.now(UTC) - timedelta(days=1)
        db.commit()

        now = datetime.now(UTC)
        messages = [
            InboxMessage(
                id=f"sample-{secrets.token_hex(6)}",
                subject=subject,
                from_address=sender,
                from_name=name,
                received_at=now - timedelta(minutes=15 * (i + 1)),
                conversation_id=None,
                body_text=body,
                headers=headers,
            )
            for i, (subject, body, sender, name, headers) in enumerate(EMAILS)
        ]
        # The AI model is used only if FNB_LLM_ENABLED is set; otherwise rules and approved text only.
        count = poll_once(db, SampleMailbox(messages), sources, settings, model=get_model(settings))
        print(f"Processed {count} sample customer emails (no real emails were sent).")


if __name__ == "__main__":
    main()
