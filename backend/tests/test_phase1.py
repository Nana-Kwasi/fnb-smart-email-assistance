"""Phase 1: answers from customers' own records are switched off."""

import pytest

from app.core.config import get_settings
from app.models import EmailStatus, Reason
from tests.conftest import BALANCE_SQL_TOPIC, BALANCE_TOPIC, FAQ_TOPIC
from tests.test_email_flow import _email, _poll, _start


@pytest.fixture
def phase1(monkeypatch):
    monkeypatch.setattr(get_settings(), "customer_data_answers", False)


def test_customer_specific_topics_cannot_be_created(phase1, login):
    sam = login("sam")
    for topic in (BALANCE_TOPIC, BALANCE_SQL_TOPIC):
        resp = sam.post("/api/topics", json=topic)
        assert resp.status_code == 422 and "not enabled yet" in resp.json()["detail"]
    assert sam.post("/api/topics", json=FAQ_TOPIC).status_code == 201


def test_existing_customer_topic_hands_the_email_to_a_person(db, mailbox, live_topic, monkeypatch):
    live_topic(BALANCE_TOPIC)  # created while the switch was on
    _start(db, mailbox)
    monkeypatch.setattr(get_settings(), "customer_data_answers", False)
    mailbox.add("p1", subject="Balance", body="What is my account balance? 6200123456")
    _poll(db, mailbox)
    email = _email(db, "p1")
    assert email.status == EmailStatus.QUEUED and email.reason_code == Reason.CUSTOMER_DATA_OFF
    assert all("1,520" not in html for _, html in mailbox.sent)


def test_general_answers_still_work(phase1, db, mailbox, live_topic):
    live_topic(FAQ_TOPIC)
    _start(db, mailbox)
    mailbox.add("g1", subject="Hours", body="What time do your branches open?")
    _poll(db, mailbox)
    assert _email(db, "g1").status == EmailStatus.AUTO_ANSWERED


def test_only_one_data_source_and_features_are_reported(phase1, login):
    from tests.conftest import BANK_DB

    admin = login("adam")
    me = admin.get("/api/auth/me").json()
    assert me["features"]["customer_data_answers"] is False and me["features"]["max_data_sources"] == 1
    resp = admin.post(
        "/api/data-sources", json={"name": "second", "kind": "sqlite", "database": str(BANK_DB)}
    )
    assert resp.status_code == 409 and "Phase 1" in resp.json()["detail"]
