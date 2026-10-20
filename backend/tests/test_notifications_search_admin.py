from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.core.config import get_settings
from app.models import CustomerEmail, DataSource, NotificationLog
from app.services import notifications
from app.services.app_settings import get_app_settings, get_mailbox_state
from tests.test_email_flow import _poll, _start


def _recipients(db, *addresses):
    cfg = get_app_settings(db)
    cfg.notify_recipients = list(addresses)
    db.commit()


# -- notifications -----------------------------------------------------------------------------


def test_urgent_email_alerts_the_people_in_charge_once(db, mailbox):
    _start(db, mailbox)
    _recipients(db, "lead@fnb.test")
    mailbox.add("u1", subject="FRAUD on my account", body="Someone stole money from my account!")
    _poll(db, mailbox)
    assert len(mailbox.mails) == 1
    to, subject, html = mailbox.mails[0]
    assert to == ["lead@fnb.test"] and "[Urgent]" in subject
    assert "stole money" in html and "https://emailcare.fnb.test/emails/" in html
    email = db.scalar(select(CustomerEmail).where(CustomerEmail.graph_message_id == "u1"))
    notifications.alert_urgent(db, mailbox, email, get_settings())  # a second call sends nothing
    assert len(mailbox.mails) == 1


def test_ordinary_unanswered_emails_do_not_alert_immediately(db, mailbox):
    _start(db, mailbox)
    _recipients(db, "lead@fnb.test")
    mailbox.add("n1", subject="Loan", body="Please call me about a loan.")
    _poll(db, mailbox)
    assert mailbox.mails == []


def test_overdue_emails_are_reminded_once_in_one_message(db, mailbox):
    _start(db, mailbox)
    _recipients(db, "lead@fnb.test")
    old = datetime.now(UTC) - timedelta(hours=5)
    get_mailbox_state(db).process_from = old - timedelta(hours=1)
    db.commit()
    mailbox.add("o1", subject="Loan", body="Please call me.", received=old)
    mailbox.add("o2", subject="Card", body="My card question.", received=old)
    mailbox.add("o3", subject="New", body="Just arrived.")
    _poll(db, mailbox)
    assert notifications.send_overdue(db, mailbox, get_settings()) == 2
    assert len(mailbox.mails) == 1 and "2 customer emails" in mailbox.mails[0][1]
    assert notifications.send_overdue(db, mailbox, get_settings()) == 0  # never twice


def test_daily_digest_is_sent_once_after_the_set_time(db, mailbox):
    _start(db, mailbox)
    _recipients(db, "lead@fnb.test")
    get_app_settings(db).digest_time = "00:00"
    db.commit()
    mailbox.add("d1", subject="Loan", body="Please call me about a mortgage.")
    _poll(db, mailbox)
    assert notifications.send_digest_if_due(db, mailbox, get_settings()) is True
    assert "summary" in mailbox.mails[-1][1].lower()
    assert "No topic for this question" in mailbox.mails[-1][2]
    assert notifications.send_digest_if_due(db, mailbox, get_settings()) is False


def test_alerts_never_go_outside_the_bank(db, mailbox, login):
    _start(db, mailbox)
    _recipients(db, "lead@fnb.test", "someone@gmail.com")
    mailbox.add("x1", subject="FRAUD", body="fraud!")
    _poll(db, mailbox)
    assert mailbox.mails[0][0] == ["lead@fnb.test"]

    admin = login("adam")
    current = admin.get("/api/settings").json()
    body = {
        k: current[k]
        for k in (
            "ack_enabled",
            "auto_reply_enabled",
            "confidence_threshold",
            "ack_template",
            "answer_header",
            "answer_footer",
            "sensitive_keywords",
            "notify_urgent",
            "notify_overdue",
            "overdue_after_hours",
            "notify_digest",
            "digest_time",
            "notify_system",
        )
    }
    resp = admin.put("/api/settings", json={**body, "notify_recipients": ["someone@gmail.com"]})
    assert resp.status_code == 422 and "internal" in resp.json()["detail"]
    resp = admin.put("/api/settings", json={**body, "notify_recipients": ["Lead@FNB.test"]})
    assert resp.status_code == 200 and resp.json()["notify_recipients"] == ["lead@fnb.test"]


def test_system_alert_when_the_mailbox_keeps_failing(db, mailbox):
    _start(db, mailbox)
    _recipients(db, "lead@fnb.test")
    state = get_mailbox_state(db)
    state.consecutive_failures, state.last_error = 3, "HTTP 401"
    db.commit()
    assert notifications.send_system_alert_if_needed(db, mailbox, get_settings()) is True
    assert notifications.send_system_alert_if_needed(db, mailbox, get_settings()) is False  # not again for 6h
    assert db.scalar(select(NotificationLog.kind)) == "system"


# -- search ------------------------------------------------------------------------------------


def test_search_finds_emails_by_reference_and_respects_roles(db, mailbox, login):
    _start(db, mailbox)
    mailbox.add("s1", subject="Statement", body="Please send my statement.")
    _poll(db, mailbox)
    ref = db.scalar(select(CustomerEmail.reference))

    officer = login("olivia")
    hits = officer.get(f"/api/search?q={ref}").json()["hits"]
    assert hits[0]["kind"] == "email" and ref in hits[0]["title"]

    # Officers do not see admin pages or users; admins do.
    assert not [h for h in officer.get("/api/search?q=users").json()["hits"] if h["kind"] == "page"]
    assert not [h for h in officer.get("/api/search?q=adam").json()["hits"] if h["kind"] == "user"]
    admin = login("adam")
    assert [h for h in admin.get("/api/search?q=users").json()["hits"] if h["kind"] == "page"]
    assert [h for h in admin.get("/api/search?q=adam").json()["hits"] if h["kind"] == "user"]


# -- data sources ------------------------------------------------------------------------------


def test_admin_adds_a_data_source_and_the_password_is_never_shown(db, login):
    from tests.conftest import BANK_DB

    admin = login("adam")
    resp = admin.post(
        "/api/data-sources",
        json={"name": "core_copy", "kind": "sqlite", "database": str(BANK_DB), "password": "s3cret!"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["has_password"] is True and "password" not in body and body["last_check_ok"] is True
    stored = db.scalar(select(DataSource).where(DataSource.name == "core_copy"))
    assert stored.password_encrypted and "s3cret" not in stored.password_encrypted

    assert login("olivia").get("/api/data-sources").status_code == 403  # officers cannot manage them
    assert [d["name"] for d in login("sam").get("/api/data-sources/names").json()] == ["core", "core_copy"]


def test_data_source_used_by_a_topic_cannot_be_deleted(db, login, live_topic):
    from tests.conftest import FAQ_TOPIC

    live_topic(FAQ_TOPIC)
    admin = login("adam")
    core = next(d for d in admin.get("/api/data-sources").json() if d["name"] == "core")
    assert admin.delete(f"/api/data-sources/{core['id']}").status_code == 409


# -- overview ----------------------------------------------------------------------------------


def test_overview_briefing_describes_the_day(db, mailbox, login, live_topic):
    from tests.conftest import FAQ_TOPIC

    live_topic(FAQ_TOPIC)
    _start(db, mailbox)
    mailbox.add("b1", subject="Hours", body="What time do your branches open?")
    mailbox.add("b2", subject="FRAUD", body="Someone stole from me")
    _poll(db, mailbox)
    data = login("olivia").get("/api/overview").json()
    text = " ".join(data["briefing"])
    assert "2 customer emails, 1 answered automatically (50%)" in text
    assert "1 email need a person, 1 urgent" in text
    assert data["received"]["value"] == 2 and len(data["received"]["spark"]) == 7
    assert data["recent"][0]["reference"]
