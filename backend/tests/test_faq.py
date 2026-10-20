"""The simple topic flow: an admin points at the FAQ table once; topics pick an entry."""

from app.models import EmailStatus
from tests.test_email_flow import _email, _poll, _start

CONFIG = {
    "data_source": "core",
    "table": "faq",
    "key_column": "code",
    "question_column": "question",
    "answer_column": "answer",
}


def test_admin_sets_up_the_faq_and_supervisors_browse_it(login):
    admin, sam = login("adam"), login("sam")
    assert sam.get("/api/faq/config").json() == {"configured": False, "config": None}
    assert login("olivia").put("/api/faq/config", json=CONFIG).status_code == 403  # admins only
    assert admin.put("/api/faq/config", json={**CONFIG, "answer_column": "nope"}).status_code == 422
    assert admin.put("/api/faq/config", json=CONFIG).json()["configured"] is True

    entries = sam.get("/api/faq/entries").json()
    assert entries == [
        {
            "key": "HOURS",
            "question": "Branch hours",
            "answer": "Our branches are open 08:30 to 16:00, Monday to Friday.",
        }
    ]
    assert sam.get("/api/faq/entries?q=branches").json()[0]["key"] == "HOURS"
    assert sam.get("/api/faq/entries?q=mortgage").json() == []
    assert login("olivia").get("/api/faq/entries").status_code == 403


def test_topic_made_from_an_faq_entry_answers_customers(db, mailbox, login, live_topic):
    login("adam").put("/api/faq/config", json=CONFIG)
    # This is exactly what the simple form submits.
    live_topic(
        {
            "title": "Branch hours",
            "examples": ["What time do you open?", "branch opening hours", "are you open on Saturday"],
            "data_source": "core",
            "builder": {
                "table": "faq",
                "columns": ["answer"],
                "conditions": [{"column": "code", "source": "fixed", "value": "HOURS"}],
            },
            "reply_template": "{answer}",
            "require_verified_sender": False,
        }
    )
    _start(db, mailbox)
    mailbox.add("f1", subject="Opening", body="What time do you open on Saturday?")
    _poll(db, mailbox)
    assert _email(db, "f1").status == EmailStatus.AUTO_ANSWERED
    assert "08:30 to 16:00" in mailbox.sent[-1][1]
