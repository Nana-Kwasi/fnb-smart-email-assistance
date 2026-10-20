"""Test setup: a throwaway app database, a fake 'bank' database, a fake Outlook mailbox."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from datetime import UTC, datetime
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="fnb-care-tests-"))
BANK_DB = _TMP / "bank.db"
os.environ.update(
    {
        "FNB_ENV": "test",
        "FNB_AUTH_MODE": "ldap",  # never pick up a developer's backend/.env sign-in mode
        "FNB_DATABASE_URL": f"sqlite:///{_TMP / 'app.db'}",
        "FNB_SESSION_SECRET": "test-secret-" + "x" * 32,
        "FNB_COOKIE_SECURE": "false",
        "FNB_GRAPH_MAILBOX": "care@fnb.test",
        "FNB_INTERNAL_EMAIL_DOMAINS": "fnb.test",
        # Most tests cover the full (phase 2) behaviour; test_phase1.py switches it off.
        "FNB_CUSTOMER_DATA_ANSWERS": "true",
        "FNB_APP_BASE_URL": "https://emailcare.fnb.test",
        "FNB_LDAP_GROUP": "CN=EmailCare-Users,OU=Groups,DC=fnb,DC=test",
        "FNB_BOOTSTRAP_ADMINS": "adam",
    }
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.api.services import get_mailbox  # noqa: E402
from app.core.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import DataSource, DataSourceKind, StaffUser  # noqa: E402
from app.security import ldap_auth  # noqa: E402
from app.security.rbac import Role  # noqa: E402
from app.services.graph_client import InboxMessage, SentItem  # noqa: E402

DMARC_PASS = {
    "Authentication-Results": "spf=pass smtp.mailfrom=example.com; dkim=pass; dmarc=pass action=none"
}


def _seed_bank() -> None:
    BANK_DB.unlink(missing_ok=True)
    con = sqlite3.connect(BANK_DB)
    con.executescript("""
        CREATE TABLE faq (code TEXT, question TEXT, answer TEXT);
        INSERT INTO faq VALUES ('HOURS', 'Branch hours', 'Our branches are open 08:30 to 16:00, Monday to Friday.');
        CREATE TABLE accounts (account_number TEXT, email TEXT, holder TEXT, balance NUMERIC, product TEXT);
        INSERT INTO accounts VALUES ('6200123456', 'ama@example.com', 'Ama Mensah', 1520.5, 'Savings');
        INSERT INTO accounts VALUES ('6200999999', 'kofi@example.com', 'Kofi Boateng', 99, 'Cheque');
    """)
    con.commit()
    con.close()


class FakeMailbox:
    def __init__(self):
        self.messages: dict[str, InboxMessage] = {}
        self.new_ids: list[str] = []
        self.sent: list[tuple[str, str]] = []  # replies to customers: (message id, html)
        self.mails: list[tuple[list[str], str, str]] = []  # alerts: (to, subject, html)
        self.mail_cc: list[list[str]] = []  # who was copied on each alert
        self.sent_items: dict[str, list[SentItem]] = {}  # conversation id -> what Outlook shows as sent
        self.fail_replies = False
        self.attachments: dict[str, list] = {}  # message id -> RawAttachment list

    def add(
        self,
        msg_id: str,
        *,
        subject: str,
        body: str,
        sender: str = "ama@example.com",
        name: str = "Ama Mensah",
        headers: dict | None = None,
        received: datetime | None = None,
        conversation: str | None = None,
        attachments: list | None = None,
    ):
        self.messages[msg_id] = InboxMessage(
            id=msg_id,
            subject=subject,
            from_address=sender,
            from_name=name,
            received_at=received or datetime.now(UTC),
            conversation_id=conversation or "conv-" + msg_id,
            body_text=body,
            headers=DMARC_PASS if headers is None else headers,
            has_attachments=bool(attachments),
        )
        if attachments:
            self.attachments[msg_id] = attachments
        self.new_ids.append(msg_id)

    def inbox_changes(self, delta_link):
        ids, self.new_ids = self.new_ids, []
        return ids, "delta-next"

    def get_message(self, message_id):
        return self.messages[message_id]

    def reply(self, message_id, html):
        if self.fail_replies:
            raise RuntimeError("Outlook is down")
        self.sent.append((message_id, html))

    def send_mail(self, to, subject, html, cc=None):
        self.mails.append((to, subject, html))
        self.mail_cc.append(cc or [])

    def sent_in_conversation(self, conversation_id):
        return self.sent_items.get(conversation_id, [])

    def list_attachments(self, message_id, max_bytes):
        return self.attachments.get(message_id, [])


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    _seed_bank()
    with SessionLocal() as session:
        session.add(DataSource(name="core", kind=DataSourceKind.SQLITE, database=str(BANK_DB)))
        session.commit()
    yield


@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session


@pytest.fixture
def mailbox():
    box = FakeMailbox()
    app.dependency_overrides[get_mailbox] = lambda: box
    yield box
    app.dependency_overrides.pop(get_mailbox, None)


ROLE_BY_USER = {
    "olivia": Role.OFFICER,
    "otto": Role.OFFICER,
    "vera": Role.SUPERVISOR,
    "victor": Role.SUPERVISOR,
    "audrey": Role.AUDITOR,
    "sam": Role.ADMIN,
    "sara": Role.ADMIN,
    "adam": Role.ADMIN,
}


@pytest.fixture(autouse=True)
def fake_directory(monkeypatch):
    """Stand-in for Active Directory: every ROLE_BY_USER name is in the group; password is 'pw'."""

    def authenticate(username, password, settings, server=None):
        if username not in ROLE_BY_USER or password != "pw":
            raise ldap_auth.AuthError(ldap_auth.INVALID_CREDENTIALS)
        return ldap_auth.DirectoryUser(username, username.title(), f"{username}@fnb.test")

    monkeypatch.setattr(ldap_auth, "authenticate", authenticate)


class Api:
    """A signed-in browser: keeps the session cookie and sends the CSRF header."""

    def __init__(self, client: TestClient, csrf: str):
        self.c, self.csrf = client, csrf

    def get(self, url, **kw):
        return self.c.get(url, **kw)

    def _send(self, method, url, **kw):
        return self.c.request(method, url, headers={"X-CSRF-Token": self.csrf}, **kw)

    def post(self, url, **kw):
        return self._send("POST", url, **kw)

    def put(self, url, **kw):
        return self._send("PUT", url, **kw)

    def patch(self, url, **kw):
        return self._send("PATCH", url, **kw)

    def delete(self, url, **kw):
        return self._send("DELETE", url, **kw)


@pytest.fixture
def login():
    def _login(username: str) -> Api:
        # Roles live in the app database, as if an admin had already set them on the Users screen.
        with SessionLocal() as session:
            if not session.scalar(select(StaffUser).where(StaffUser.username == username)):
                session.add(StaffUser(username=username, role=ROLE_BY_USER[username].value))
                session.commit()
        client = TestClient(app)
        resp = client.post("/api/auth/login", json={"username": username, "password": "pw"})
        assert resp.status_code == 200, resp.text
        return Api(client, resp.json()["csrf_token"])

    return _login


# A guided topic: the same answer for everyone.
FAQ_TOPIC = {
    "title": "Branch opening hours",
    "examples": ["What time do your branches open?", "branch opening hours", "when are you open on Saturday"],
    "data_source": "core",
    "builder": {
        "table": "faq",
        "columns": ["answer"],
        "conditions": [{"column": "code", "source": "fixed", "value": "HOURS"}],
    },
    "reply_template": "{answer}",
    "require_verified_sender": False,
}

# A guided topic for the customer's own record (account number from the email + their address).
BALANCE_TOPIC = {
    "title": "Account balance",
    "examples": ["What is my account balance?", "how much money is in my account", "check my balance"],
    "data_source": "core",
    "builder": {
        "table": "accounts",
        "columns": ["holder", "balance", "product"],
        "conditions": [
            {"column": "account_number", "source": "email_value", "value_kind": "account_number"},
            {"column": "email", "source": "sender_email"},
        ],
    },
    "reply_template": "The available balance on your {product} account is {balance:money}.",
}

# The same balance topic written as an advanced (hand-written) query.
BALANCE_SQL_TOPIC = {
    "title": "Account balance (SQL)",
    "examples": ["What is my account balance?", "how much money is in my account", "check my balance"],
    "data_source": "core",
    "query": "SELECT holder, balance, product FROM accounts WHERE account_number = :account_number "
    "AND lower(email) = :sender_email",
    "extractors": {"account_number": r"\b(\d{10})\b"},
    "reply_template": "The available balance on your {product} account is {balance:money}.",
    "require_verified_sender": True,
}


@pytest.fixture
def live_topic(login):
    """Create a topic as one supervisor and approve it as another. Returns the topic id."""

    def _make(payload: dict) -> str:
        sam, sara = login("sam"), login("sara")
        created = sam.post("/api/topics", json=payload)
        assert created.status_code == 201, created.text
        body = created.json()
        approved = sara.post(f"/api/topics/{body['id']}/revisions/{body['pending']['id']}/approve")
        assert approved.status_code == 200, approved.text
        return body["id"]

    return _make


FEES_FAQ = b"""Q: How much is a transfer to another bank?
A: A transfer to another bank in the app costs GHS 5 per transfer.
"""


@pytest.fixture
def fees_doc(login):
    """A published fee document in "Loans", a suggest-only category, so the approved text is not
    sent by itself and the AI path is what decides."""
    from tests.test_knowledge import publish_new

    return publish_new(login, name="Fees", category="Loans", data=FEES_FAQ, filename="fees.md")
