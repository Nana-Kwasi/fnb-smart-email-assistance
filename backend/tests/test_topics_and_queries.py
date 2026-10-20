from datetime import date
from decimal import Decimal

import pytest

from app.services.datasources import QueryRejected, render_reply, validate_query
from app.services.matcher import Candidate, Matcher
from tests.conftest import BALANCE_SQL_TOPIC, BALANCE_TOPIC, FAQ_TOPIC


def test_topic_needs_a_second_person_to_approve_when_switched_on(db, login):
    from app.services.app_settings import get_app_settings

    get_app_settings(db).require_second_person = True
    db.commit()
    sam, sara = login("sam"), login("sara")
    body = sam.post("/api/topics", json=FAQ_TOPIC).json()
    assert body["live"] is None and body["pending"]["status"] == "pending"

    own = sam.post(f"/api/topics/{body['id']}/revisions/{body['pending']['id']}/approve")
    assert own.status_code == 409
    assert "cannot approve your own" in own.json()["detail"]

    ok = sara.post(f"/api/topics/{body['id']}/revisions/{body['pending']['id']}/approve").json()
    assert ok["live"]["title"] == FAQ_TOPIC["title"] and ok["pending"] is None


def test_editing_a_live_topic_keeps_the_old_version_live_until_approved(login, live_topic):
    topic_id = live_topic(FAQ_TOPIC)
    sam = login("sam")
    edited = sam.put(f"/api/topics/{topic_id}", json={**FAQ_TOPIC, "title": "Opening times"}).json()
    assert edited["live"]["title"] == FAQ_TOPIC["title"]
    assert edited["pending"]["title"] == "Opening times"


def test_rejected_change_records_the_note(login):
    sam, sara = login("sam"), login("sara")
    body = sam.post("/api/topics", json=FAQ_TOPIC).json()
    rejected = sara.post(
        f"/api/topics/{body['id']}/revisions/{body['pending']['id']}/reject",
        json={"note": "Use the new FAQ table"},
    ).json()
    assert rejected["history"][0]["status"] == "rejected"
    assert rejected["history"][0]["review_note"] == "Use the new FAQ table"


@pytest.mark.parametrize(
    "sql, message",
    [
        ("UPDATE accounts SET balance = 0", "SELECT or WITH"),
        ("SELECT 1; DROP TABLE accounts", "one statement"),
        ("SELECT * FROM accounts -- comment", "Comments"),
        ("WITH x AS (DELETE FROM accounts RETURNING *) SELECT * FROM x", "DELETE"),
        ("SELECT * INTO backup FROM accounts", "INTO"),
        ("SELECT * FROM accounts WHERE id = :customer_id", "Unknown parameter"),
    ],
)
def test_unsafe_or_invalid_queries_are_rejected(sql, message):
    with pytest.raises(QueryRejected, match=message):
        validate_query(sql, [])


def test_safe_queries_are_accepted():
    assert (
        validate_query("select answer from faq where code = 'UPDATED';", [])
        == "select answer from faq where code = 'UPDATED'"
    )
    validate_query(BALANCE_SQL_TOPIC["query"], ["account_number"])


def test_topic_api_rejects_unknown_data_source_and_bad_query(login):
    sam = login("sam")
    resp = sam.post("/api/topics", json={**FAQ_TOPIC, "data_source": "nope"})
    assert resp.status_code == 422 and "does not exist" in resp.json()["detail"]
    advanced = {k: v for k, v in FAQ_TOPIC.items() if k != "builder"}
    resp = sam.post("/api/topics", json={**advanced, "query": "DELETE FROM faq"})
    assert resp.status_code == 422


def test_email_text_cannot_inject_sql(db):
    """Values from emails are bound parameters; a quote in the email is just data."""
    from app.core.config import get_settings
    from app.services.datasources import DataSources

    sources = DataSources(get_settings())
    ds = sources.get(db, "core")
    attack = {"sender_email": "x' OR '1'='1"}
    assert (
        sources.run_sql(ds, "SELECT holder FROM accounts WHERE lower(email) = :sender_email", attack, 10).rows
        == []
    )
    spec = {
        "table": "accounts",
        "columns": ["holder"],
        "conditions": [{"column": "email", "source": "sender_email"}],
    }
    assert sources.run_builder(ds, spec, attack, 10).rows == []


def test_guided_topic_checks_table_columns_and_reply_fields(login):
    sam = login("sam")
    bad_column = {**FAQ_TOPIC, "builder": {**FAQ_TOPIC["builder"], "columns": ["nonsense"]}}
    resp = sam.post("/api/topics", json=bad_column)
    assert resp.status_code == 422 and "not found" in resp.json()["detail"]
    bad_field = {**FAQ_TOPIC, "reply_template": "{answer} {secret}"}
    resp = sam.post("/api/topics", json=bad_field)
    assert resp.status_code == 422 and "{secret}" in resp.json()["detail"]


def test_customer_specific_guided_topic_always_requires_a_verified_sender(login):
    body = login("sam").post("/api/topics", json={**BALANCE_TOPIC, "require_verified_sender": False}).json()
    assert body["pending"]["require_verified_sender"] is True


def test_try_a_topic_before_saving_it(login):
    sam = login("sam")
    sample = {
        "subject": "Balance",
        "body": "My balance please, account 6200123456",
        "sender_email": "ama@example.com",
        "sender_name": "Ama",
        "verified": True,
    }
    out = sam.post("/api/topics/try", json={"topic": BALANCE_TOPIC, "sample": sample}).json()
    assert "1,520.50" in out["text"]
    out = sam.post(
        "/api/topics/try", json={"topic": BALANCE_TOPIC, "sample": {**sample, "body": "balance?"}}
    ).json()
    assert out["text"] is None and "account number" in out["reason"]
    assert (
        login("olivia").post("/api/topics/try", json={"topic": BALANCE_TOPIC, "sample": sample}).status_code
        == 403
    )


def test_browse_tables_and_columns_for_the_guided_form(login):
    sam = login("sam")
    assert {"accounts", "faq"} <= set(sam.get("/api/data-sources/browse/tables?source=core").json())
    cols = [c["name"] for c in sam.get("/api/data-sources/browse/columns?source=core&table=accounts").json()]
    assert "balance" in cols


def test_reply_layout_formats_values_and_lists_rows():
    rows = [
        {"date": date(2026, 10, 1), "amount": Decimal("1250.5"), "merchant": "shoprite"},
        {"date": date(2026, 10, 3), "amount": Decimal("80"), "merchant": "total"},
    ]
    text = render_reply(
        "Hello {customer_name}, your last payments:\n{rows}",
        "- {date:date}: {amount:money} at {merchant:title}",
        rows,
        {"customer_name": "Ama"},
    )
    assert text == (
        "Hello Ama, your last payments:\n- 01 October 2026: 1,250.50 at Shoprite\n"
        "- 03 October 2026: 80.00 at Total"
    )


def test_matcher_tolerates_typos_and_refuses_ambiguity():
    import uuid

    a, b = uuid.uuid4(), uuid.uuid4()
    matcher = Matcher(
        [
            Candidate(a, uuid.uuid4(), "Reset PIN", ["How do I reset my card PIN?", "forgot my pin"]),
            Candidate(b, uuid.uuid4(), "Branch hours", ["What time do branches open?"]),
        ]
    )
    result = matcher.match("", "Hi, I forgot my pinn, how can I resett it?", 0.4)
    assert result.matched and result.matched.topic_id == a
    assert matcher.match("", "Please send me a statement for March.", 0.4).matched is None


def test_date_format_also_handles_dates_stored_as_text():
    assert render_reply("{d:date}", None, [{"d": "2026-10-04"}], {}) == "04 October 2026"
    assert render_reply("{d:date}", None, [{"d": "not a date"}], {}) == "not a date"
