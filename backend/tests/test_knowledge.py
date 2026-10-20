"""Knowledge base: upload, publish (maker-checker), answer emails, and the automation settings."""

from datetime import date, timedelta

from sqlalchemy import select

from app.core.config import get_settings
from app.models import CustomerEmail, EmailStatus, Reason
from app.services import notifications
from app.services.app_settings import get_app_settings
from tests.test_email_flow import _email, _poll, _start

FAQ = b"""# Customer FAQ

Q: What are your branch opening hours?
A: Branches open 08:30 to 16:00 on weekdays and 09:00 to 12:00 on Saturdays.

Q: How do I get a bank statement?
A: Download it in the app under Accounts > Statements, or ask at any branch.
"""

LOANS = b"""Q: What is the maximum personal loan?
A: Up to five times your monthly net salary, subject to an affordability check.
"""


def upload(api, name="Customer FAQ", category="General", data=FAQ, filename="faq.md", **form):
    return api.post(
        "/api/knowledge",
        files={"file": (filename, data, "text/markdown")},
        data={"name": name, "category": category, **form},
    )


def publish_new(login, **kw) -> dict:
    """Upload as one supervisor and publish as another, as the bank would."""
    doc = upload(login("sam"), **kw).json()
    draft = doc["versions"][0]
    resp = login("sara").post(f"/api/knowledge/versions/{draft['id']}/publish")
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_upload_reads_entries_and_needs_a_second_person_to_publish_when_switched_on(db, login):
    get_app_settings(db).require_second_person = True
    db.commit()
    sam, sara, olivia = login("sam"), login("sara"), login("olivia")
    assert upload(olivia).status_code == 403  # officers can view, not upload

    resp = upload(sam)
    assert resp.status_code == 201, resp.text
    version = resp.json()["versions"][0]
    assert version["status"] == "draft" and version["entry_count"] == 2

    own = sam.post(f"/api/knowledge/versions/{version['id']}/publish")
    assert own.status_code == 409 and "someone else must publish" in own.json()["detail"]
    assert (
        sara.post(f"/api/knowledge/versions/{version['id']}/publish").json()["versions"][0]["status"]
        == "published"
    )

    entries = olivia.get(f"/api/knowledge/versions/{version['id']}/entries").json()
    assert entries[0]["title"] == "What are your branch opening hours?"


def test_bad_files_are_refused_with_a_clear_reason(login):
    sam = login("sam")
    resp = upload(sam, data=b"just some words without any structure at all", filename="notes.exe")
    assert resp.status_code == 422 and "PDF, Word" in resp.json()["detail"]
    resp = upload(sam, data=b"%PDF-broken", filename="x.pdf")
    assert resp.status_code == 422


def test_published_answer_is_sent_word_for_word_and_counted(db, mailbox, login):
    doc = publish_new(login)
    _start(db, mailbox)
    mailbox.add("k1", subject="Hours", body="Hello, what are your branch opening hours on Saturday?")
    _poll(db, mailbox)
    email = _email(db, "k1")
    assert email.status == EmailStatus.AUTO_ANSWERED
    assert "09:00 to 12:00 on Saturdays" in mailbox.sent[-1][1]
    assert email.knowledge_version_id is not None

    detail = login("olivia").get(f"/api/knowledge/{doc['id']}").json()
    assert detail["versions"][0]["answered"] == 1
    used = login("olivia").get(f"/api/emails/{email.id}").json()["knowledge"]
    assert used["document"] == "Customer FAQ" and used["version"] == "1"


def test_drafts_are_never_used_for_real_emails(db, mailbox, login):
    upload(login("sam"))  # not published
    _start(db, mailbox)
    mailbox.add("k2", subject="Hours", body="What are your branch opening hours?")
    _poll(db, mailbox)
    assert _email(db, "k2").status == EmailStatus.QUEUED


def test_suggest_only_category_goes_to_a_person_with_the_answer_ready(db, mailbox, login):
    publish_new(login, name="Loans", category="Loans", data=LOANS, filename="loans.md")
    _start(db, mailbox)
    mailbox.add("k3", subject="Loan", body="What is the maximum personal loan?")
    _poll(db, mailbox)
    email = _email(db, "k3")
    assert email.status == EmailStatus.QUEUED and email.reason_code == Reason.SUGGEST_ONLY
    suggestion = login("olivia").get(f"/api/emails/{email.id}").json()["knowledge"]
    assert "five times your monthly net salary" in suggestion["content"]


def test_below_the_automatic_level_is_suggested_not_sent(db, mailbox, login):
    publish_new(login)
    _start(db, mailbox)
    get_app_settings(db).confidence_threshold = 0.99
    db.commit()
    mailbox.add("k4", subject="Statement", body="Could you tell me how I get a bank statement please")
    _poll(db, mailbox)
    email = _email(db, "k4")
    assert email.status == EmailStatus.QUEUED and email.reason_code == Reason.BELOW_AUTO
    assert email.knowledge_entry_id is not None  # the suggestion is kept for staff
    assert len(mailbox.sent) == 1  # acknowledgement only


def test_switching_automatic_answers_off_still_suggests(db, mailbox, login):
    publish_new(login)
    _start(db, mailbox, auto_reply=False)
    mailbox.add("k5", subject="Hours", body="What are your branch opening hours?")
    _poll(db, mailbox)
    email = _email(db, "k5")
    assert email.reason_code == Reason.AUTO_OFF and email.knowledge_entry_id is not None


def test_new_version_replaces_the_old_one_and_expired_versions_are_not_used(db, mailbox, login):
    doc = publish_new(login)
    sam, sara = login("sam"), login("sara")
    new = sam.post(
        f"/api/knowledge/{doc['id']}/versions",
        files={"file": ("faq.md", FAQ.replace(b"08:30", b"09:00"), "text/markdown")},
        data={"version_label": "2", "expires_on": str(date.today() - timedelta(days=1))},
    ).json()
    v2 = next(v for v in new["versions"] if v["version_label"] == "2")
    assert v2["in_date"] is False
    after = sara.post(f"/api/knowledge/versions/{v2['id']}/publish").json()
    statuses = {v["version_label"]: v["status"] for v in after["versions"]}
    assert statuses == {"1": "archived", "2": "published"}

    _start(db, mailbox)
    mailbox.add("k6", subject="Hours", body="What are your branch opening hours?")
    _poll(db, mailbox)
    assert _email(db, "k6").status == EmailStatus.QUEUED  # the only published version has expired


def test_try_a_question_explains_what_would_happen(db, login):
    sam = login("sam")
    upload(sam)  # a draft is enough to test
    out = sam.post("/api/knowledge/try", json={"question": "what are your opening hours?"}).json()
    assert out["hits"][0]["title"] == "What are your branch opening hours?"
    assert "draft" in out["explanation"]
    out = sam.post("/api/knowledge/try", json={"question": "Someone stole money, this is fraud"}).json()
    assert out["outcome"] == "person"
    out = sam.post(
        "/api/knowledge/try", json={"question": "Do you sell car insurance?", "include_drafts": True}
    ).json()
    assert out["outcome"] == "person"


def test_escalation_goes_to_the_person_in_charge_and_copies_admins(db, mailbox, login):
    login("adam")  # an admin who has signed in (their email is adam@fnb.test)
    _start(db, mailbox)
    cfg = get_app_settings(db)
    cfg.escalation_to, cfg.notify_recipients, cfg.escalation_copy_admins = (
        "lead@fnb.test",
        ["deputy@fnb.test"],
        True,
    )
    db.commit()
    mailbox.add("e1", subject="FRAUD", body="Someone stole money from me")
    _poll(db, mailbox)
    assert mailbox.mails[0][0] == ["lead@fnb.test"]
    assert sorted(mailbox.mail_cc[0]) == ["adam@fnb.test", "deputy@fnb.test"]
    to, cc = notifications.escalation_recipients(db, get_settings())
    assert to == ["lead@fnb.test"]


def test_settings_keep_categories_and_check_the_two_levels(login):
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
            "notify_recipients",
        )
    }
    resp = admin.put("/api/settings", json={**body, "suggest_threshold": 0.9, "confidence_threshold": 0.6})
    assert resp.status_code == 422
    resp = admin.put("/api/settings", json={**body, "escalation_to": "Lead@fnb.test"})
    assert resp.status_code == 200
    saved = resp.json()
    assert saved["escalation_to"] == "lead@fnb.test" and len(saved["knowledge_categories"]) == 8
    cats = [{**c, "auto": False} if c["name"] == "Cards" else c for c in saved["knowledge_categories"]]
    resp = admin.put("/api/settings", json={**body, "knowledge_categories": cats})
    assert next(c for c in resp.json()["knowledge_categories"] if c["name"] == "Cards")["auto"] is False


def test_search_finds_documents(login):
    publish_new(login)
    hits = login("olivia").get("/api/search?q=customer faq").json()["hits"]
    assert any(h["kind"] == "document" and h["title"] == "Customer FAQ" for h in hits)


def test_overview_counts_knowledge_answers(db, mailbox, login):
    publish_new(login)
    _start(db, mailbox)
    mailbox.add("o1", subject="Hours", body="What are your branch opening hours?")
    _poll(db, mailbox)
    data = login("olivia").get("/api/overview").json()
    assert data["top_topics_7d"][0]["title"] == "What are your branch opening hours?"
    assert any(h["name"] == "Knowledge base" and h["ok"] for h in data["health"])
    assert db.scalar(select(CustomerEmail.knowledge_entry_id)) is not None


def test_without_the_second_person_check_the_uploader_can_publish(login):
    sam = login("sam")
    draft = upload(sam).json()["versions"][0]
    assert (
        sam.post(f"/api/knowledge/versions/{draft['id']}/publish").json()["versions"][0]["status"]
        == "published"
    )


def test_versions_are_numbered_automatically_and_new_categories_can_be_added(login):
    sam = login("sam")
    doc = upload(sam, category="Mortgages").json()
    assert doc["versions"][0]["version_label"] == "1" and doc["category"] == "Mortgages"
    cats = sam.get("/api/settings").json()["knowledge_categories"]
    assert {"name": "Mortgages", "auto": False} in cats  # new categories start as suggest only
    second = sam.post(
        f"/api/knowledge/{doc['id']}/versions", files={"file": ("faq.md", FAQ, "text/markdown")}
    ).json()
    assert [v["version_label"] for v in second["versions"]] == ["2", "1"]


def test_upload_size_limit_comes_from_settings(db, login):
    get_app_settings(db).max_upload_mb = 1
    db.commit()
    big = b"Q: big?\nA: " + b"x" * (1024 * 1024 + 10)
    resp = upload(login("sam"), data=big)
    assert resp.status_code == 413 and "1 MB limit" in resp.json()["detail"]
