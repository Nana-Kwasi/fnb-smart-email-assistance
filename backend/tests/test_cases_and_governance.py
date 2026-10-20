"""Officer review and sending, cases, roles, knowledge access, the audit chain, the quality
monitor, the automation policy API, and the model and Microsoft Graph clients."""

import json
import logging
import uuid
from datetime import date, timedelta

import httpx
import pytest
from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.core.db import utcnow
from app.core.logging import RedactingFilter, redact
from app.models import (
    AuditEvent,
    Case,
    CaseStatus,
    CustomerEmail,
    EmailStatus,
    EvalRun,
    Mode,
    PolicyChange,
    ReplyKind,
)
from app.services import quality
from app.services.audit import verify_chain
from app.services.graph_client import GraphClient, GraphError
from app.services.llm import BREAKER_FAILURES, ModelUnavailable, OpenAICompatibleModel
from app.services.policy import get_policy
from tests.test_ai_pipeline import CLASSIFY_FEES, FEES, QUESTION, FakeModel, _poll
from tests.test_email_flow import _email, _start
from tests.test_knowledge import publish_new


def _queued_with_draft(db, mailbox, msg_id="q1", **model_kw):
    _start(db, mailbox)
    mailbox.add(msg_id, subject="Transfer fee", body=QUESTION)
    _poll(db, mailbox, FakeModel(**model_kw))
    email = _email(db, msg_id)
    assert email.status == EmailStatus.QUEUED
    return email


def _rid():
    return uuid.uuid4().hex


# -- sending from the dashboard ------------------------------------------------------------------


def test_officer_edits_and_sends_the_draft_once(db, mailbox, login, fees_doc):
    email = _queued_with_draft(db, mailbox)
    olivia = login("olivia")
    detail = olivia.get(f"/api/emails/{email.id}").json()
    draft = detail["drafts"][0]
    assert (
        draft["passed"]
        and detail["approval"] == "officer"
        and detail["case"]["reference"].startswith("CASE-")
    )
    body = {
        "text": draft["body_text"] + "\n\nP.S. Our app is free to download.",
        "request_id": _rid(),
        "draft_id": draft["id"],
    }
    first = olivia.post(f"/api/emails/{email.id}/send", json=body)
    assert first.status_code == 200, first.text
    again = olivia.post(f"/api/emails/{email.id}/send", json=body)  # double click / retried request
    assert again.status_code == 200
    officer_sends = [r for r in first.json()["replies"] if r["kind"] == ReplyKind.OFFICER]
    assert len(officer_sends) == 1 and len(mailbox.sent) == 2  # acknowledgement + one reply
    out = again.json()
    assert out["status"] == EmailStatus.REPLIED and out["drafts"][0]["status"] == "sent"
    assert out["drafts"][0]["edited_text"]  # the edit is kept next to the original draft
    action = db.scalar(select(AuditEvent).where(AuditEvent.action == "email.reply_sent"))
    assert action.detail["edited"] is True and action.detail["draft_model"] == "fake-qwen"


def test_reply_asking_for_a_pin_is_refused(db, mailbox, login, fees_doc):
    email = _queued_with_draft(db, mailbox)
    resp = login("olivia").post(
        f"/api/emails/{email.id}/send",
        json={"text": "Please reply with your PIN so we can check.", "request_id": _rid()},
    )
    assert resp.status_code == 422 and mailbox.sent[-1:] != []
    assert len(mailbox.sent) == 1


def test_supervisor_category_needs_a_second_person(db, mailbox, login, fees_doc):
    email = _queued_with_draft(
        db, mailbox, classify={**CLASSIFY_FEES, "category": "complaint", "risk": "high"}
    )
    olivia, vera, victor = login("olivia"), login("vera"), login("victor")
    text = "Dear Ama Mensah,\n\nWe are sorry. A transfer to another bank costs GHS 5.\n\nKind regards"
    blocked = olivia.post(f"/api/emails/{email.id}/send", json={"text": text, "request_id": _rid()})
    assert blocked.status_code == 403
    submitted = olivia.post(f"/api/emails/{email.id}/submit", json={"text": text})
    assert submitted.status_code == 200
    draft = next(d for d in submitted.json()["drafts"] if d["status"] == "pending_approval")
    # A supervisor who submitted it cannot also approve it.
    own = vera.post(f"/api/emails/{email.id}/submit", json={"text": text + " ", "draft_id": draft["id"]})
    assert own.status_code == 200
    self_approve = vera.post(
        f"/api/emails/{email.id}/send", json={"text": text, "request_id": _rid(), "draft_id": draft["id"]}
    )
    assert self_approve.status_code == 403
    ok = victor.post(
        f"/api/emails/{email.id}/send", json={"text": text, "request_id": _rid(), "draft_id": draft["id"]}
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == EmailStatus.REPLIED


def test_auditor_sees_everything_and_changes_nothing(db, mailbox, login, fees_doc):
    email = _queued_with_draft(db, mailbox)
    audrey = login("audrey")
    assert audrey.get(f"/api/emails/{email.id}").status_code == 200
    assert audrey.get("/api/ai/status").status_code == 200
    assert audrey.get("/api/analytics").status_code == 200
    assert audrey.get("/api/ai/audit-integrity").status_code == 200
    assert (
        audrey.post(
            f"/api/emails/{email.id}/send", json={"text": "Hello there", "request_id": _rid()}
        ).status_code
        == 403
    )
    assert audrey.post(f"/api/cases/{email.case_id}/notes", json={"text": "x"}).status_code == 403
    assert login("olivia").get("/api/ai/status").status_code == 403


# -- cases -----------------------------------------------------------------------------------------


def test_emails_in_one_conversation_share_a_case_which_reopens(db, mailbox, login):
    _start(db, mailbox)
    mailbox.add("t1", subject="Card", body="My card does not work at ATMs.", conversation="conv-x")
    _poll(db, mailbox)
    first = _email(db, "t1")
    login("olivia").post(f"/api/emails/{first.id}/resolve", json={"outcome": "replied", "note": "Called"})
    case = db.get(Case, first.case_id)
    db.refresh(case)
    assert case.status == CaseStatus.RESOLVED
    mailbox.add("t2", subject="RE: Card", body="It still does not work.", conversation="conv-x")
    _poll(db, mailbox)
    second = _email(db, "t2")
    db.refresh(case)
    assert second.case_id == first.case_id and case.status == CaseStatus.OPEN and case.reopened_count == 1


def test_only_verified_senders_are_linked_across_conversations(db, mailbox):
    _start(db, mailbox)
    model = FakeModel(classify={**CLASSIFY_FEES, "category": "cards"})
    mailbox.add("v1", subject="Card", body="Card question one.")
    mailbox.add("v2", subject="Card again", body="Card question two.")
    mailbox.add("n1", subject="Card", body="Card question.", sender="kwame@example.com", headers={})
    mailbox.add("n2", subject="Card again", body="Card question.", sender="kwame@example.com", headers={})
    _poll(db, mailbox, model)
    v1, v2, n1, n2 = (_email(db, m) for m in ("v1", "v2", "n1", "n2"))
    assert v1.case_id == v2.case_id  # verified by Microsoft 365: same customer, same issue
    assert n1.case_id != n2.case_id  # unverified: never merged...
    assert db.get(Case, n2.case_id).possible_duplicate_of_id == n1.case_id  # ...only flagged


def test_case_notes_are_internal_and_financial_actions_need_approval(db, mailbox, login):
    _start(db, mailbox)
    mailbox.add("x1", subject="Fee", body="You charged me twice.")
    _poll(db, mailbox)
    case_id = _email(db, "x1").case_id
    olivia, otto, vera = login("olivia"), login("otto"), login("vera")
    note = olivia.post(f"/api/cases/{case_id}/notes", json={"text": "Customer is a pensioner; call first."})
    assert note.status_code == 200
    assert any(e["kind"] == "note" for e in note.json()["events"])
    assert mailbox.sent[-1][1].find("pensioner") == -1  # notes never reach the customer

    req = olivia.post(
        f"/api/cases/{case_id}/actions",
        json={"action": "fee_reversal_request", "params": {"amount": 25, "reason": "Duplicate fee"}},
    )
    action = req.json()["actions"][0]
    assert action["status"] == "pending_approval"
    assert otto.post(f"/api/cases/actions/{action['id']}/decide", json={"approve": True}).status_code == 403
    done = vera.post(f"/api/cases/actions/{action['id']}/decide", json={"approve": True})
    assert done.json()["actions"][0]["status"] == "done"
    assert "does not move money" in done.json()["actions"][0]["result"]

    bad = olivia.post(
        f"/api/cases/{case_id}/actions",
        json={"action": "assign_department", "params": {"department": "Nowhere"}},
    )
    assert bad.status_code == 422
    unknown = olivia.post(f"/api/cases/{case_id}/actions", json={"action": "transfer_money", "params": {}})
    assert unknown.status_code == 422
    listing = olivia.get("/api/cases?filter=duplicates")
    assert listing.status_code == 200


# -- knowledge access, expiry and conflicts --------------------------------------------------------


def test_restricted_documents_are_hidden_and_internal_ones_never_answer(db, login):
    publish_new(
        login,
        name="Fraud playbook",
        category="General",
        audience="restricted",
        data=b"Q: How do we freeze?\nA: Use the core banking freeze screen.\n",
        filename="p.md",
    )
    publish_new(
        login,
        name="Staff notes",
        category="General",
        audience="internal",
        data=b"Q: What are your branch opening hours?\nA: Staff note: open early.\n",
        filename="s.md",
    )
    names = {d["name"] for d in login("olivia").get("/api/knowledge").json()}
    assert "Fraud playbook" not in names and "Staff notes" in names
    assert "Fraud playbook" in {d["name"] for d in login("vera").get("/api/knowledge").json()}
    from app.services.knowledge import knowledge_candidates

    assert knowledge_candidates(db) == []  # neither may be sent to a customer


def test_expired_and_conflicting_documents_are_reported(db, login):
    publish_new(
        login,
        name="Fees 2025",
        category="Fees and charges",
        data=FEES,
        filename="a.md",
        expires_on=(date.today() - timedelta(days=1)).isoformat(),
    )
    publish_new(login, name="Fees A", category="Fees and charges", data=FEES, filename="b.md")
    publish_new(
        login,
        name="Fees B",
        category="Fees and charges",
        data=FEES.replace(b"GHS 5", b"GHS 8"),
        filename="c.md",
    )
    health = login("olivia").get("/api/knowledge/health").json()
    assert [d["name"] for d in health["expired"]] == ["Fees 2025"]
    assert health["conflicts"] and "Fees A" in health["conflicts"][0] and "Fees B" in health["conflicts"][0]
    from app.services.grounding import retrieve

    used = {e.document for e in retrieve(db, "", "transfer to another bank fee")}
    assert "Fees 2025" not in used


# -- policy, kill switch, quality monitor ----------------------------------------------------------


def test_autonomous_mode_needs_a_clean_evaluation_in_that_mode(db, login):
    adam = login("adam")
    body = {"mode": "restricted", "reason": "Pilot approved by risk committee, ref RC-12"}
    assert adam.put("/api/ai/policy", json=body).status_code == 409
    db.add(
        EvalRun(
            dataset="banking_emails.jsonl",
            models={},
            policy_version=1,
            cases=30,
            run_by="test",
            metrics={"mode": "draft_only", "isolated": False, "unsafe_auto_send": 0, "missed_escalations": 0},
        )
    )
    db.commit()
    assert adam.put("/api/ai/policy", json=body).status_code == 409  # evaluated in the wrong mode
    db.add(
        EvalRun(
            dataset="banking_emails.jsonl",
            models={},
            policy_version=1,
            cases=30,
            run_by="test",
            metrics={"mode": "restricted", "isolated": False, "unsafe_auto_send": 0, "missed_escalations": 0},
        )
    )
    db.commit()
    ok = adam.put("/api/ai/policy", json=body)
    assert ok.status_code == 200, ok.text
    change = db.scalar(select(PolicyChange).order_by(PolicyChange.at.desc()))
    assert (
        change.changes["mode"] == {"before": "draft_only", "after": "restricted"} and "RC-12" in change.reason
    )
    assert login("vera").put("/api/ai/policy", json=body).status_code == 403
    short = adam.put("/api/ai/policy", json={"mode": "draft_only", "reason": "why"})
    assert short.status_code == 422  # a reason is required


def test_supervisors_can_stop_automation_but_only_admins_resume(db, login):
    vera, adam = login("vera"), login("adam")
    stop = vera.post("/api/ai/kill-switch", json={"on": True, "reason": "Wrong fee answers reported"})
    assert stop.status_code == 200 and stop.json()["kill_switch"] is True
    assert (
        vera.post("/api/ai/kill-switch", json={"on": False, "reason": "Looks fine again now"}).status_code
        == 403
    )
    assert (
        adam.post("/api/ai/kill-switch", json={"on": False, "reason": "Fixed the fee document"}).status_code
        == 200
    )
    assert get_policy(db).kill_switch is False


def test_quality_monitor_pauses_a_category_customers_keep_writing_back_about(db, mailbox):
    _start(db, mailbox)
    p = get_policy(db)
    p.auto_pause_min_samples = 3
    db.commit()
    now = utcnow()
    for i in range(3):
        case = Case(
            reference=f"CASE-T-{i}",
            customer_address=f"c{i}@example.com",
            category="fees_rates",
            opened_at=now,
            last_activity_at=now,
        )
        db.add(case)
        db.flush()
        for j, status in enumerate((EmailStatus.AUTO_ANSWERED, EmailStatus.QUEUED)):
            db.add(
                CustomerEmail(
                    reference=f"FNB-T-{i}{j}",
                    graph_message_id=f"g{i}{j}",
                    from_address=case.customer_address,
                    received_at=now - timedelta(hours=5 - j),
                    handled_at=now - timedelta(hours=5),
                    status=status,
                    category="fees_rates",
                    case_id=case.id,
                )
            )
    db.commit()
    assert quality.enforce(db, mailbox, get_settings()) == ["fees_rates"]
    cat = next(c for c in get_policy(db).categories if c["code"] == "fees_rates")
    assert cat["paused"] and "quality monitor" in cat["paused_reason"].lower()
    change = db.scalar(select(PolicyChange).order_by(PolicyChange.at.desc()))
    assert change.actor_username == "system"


# -- audit trail -------------------------------------------------------------------------------------


def test_audit_trail_is_tamper_evident(db, login):
    adam = login("adam")
    adam.get("/api/settings")
    adam.post("/api/ai/kill-switch", json={"on": True, "reason": "Testing the audit chain"})
    assert verify_chain(db)["ok"]
    event = db.scalar(select(AuditEvent).where(AuditEvent.action == "policy.changed"))
    event.detail = {**event.detail, "reason": "something else"}
    db.commit()
    result = verify_chain(db)
    assert not result["ok"] and result["broken_at"] == str(event.id)


# -- the model client, Microsoft Graph, configuration, logs --------------------------------------------


def _model(handler, **overrides) -> OpenAICompatibleModel:
    settings = Settings(
        **{
            **get_settings().model_dump(),
            "llm_enabled": True,
            "llm_base_url": "http://llm:8000/v1",
            "llm_fast_model": "qwen-test",
            **overrides,
        }
    )
    return OpenAICompatibleModel(settings, http=httpx.Client(transport=httpx.MockTransport(handler)))


def _chat(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def test_model_output_is_parsed_strictly_and_thinking_is_ignored():
    from app.services.classifier import ModelClassification

    good = "<think>the customer asks about fees</think>\n```json\n" + json.dumps(CLASSIFY_FEES) + "\n```"
    answer = _model(lambda r: _chat(good)).ask(None, "classify", "s", "u", ModelClassification)
    assert answer.data.category == "fees_rates" and answer.model == "qwen-test"
    bad = json.dumps({**CLASSIFY_FEES, "category": "transfer_money_now"})
    with pytest.raises(ModelUnavailable, match="invalid_output"):
        _model(lambda r: _chat(bad)).ask(None, "classify", "s", "u", ModelClassification)


def test_model_timeouts_open_the_circuit_breaker():
    from app.services.classifier import ModelClassification

    calls = []

    def slow(request):
        calls.append(1)
        raise httpx.ReadTimeout("slow", request=request)

    model = _model(slow, llm_max_retries=0)
    for _ in range(BREAKER_FAILURES):
        with pytest.raises(ModelUnavailable, match="timeout"):
            model.ask(None, "classify", "s", "u", ModelClassification)
    with pytest.raises(ModelUnavailable, match="paused"):
        model.ask(None, "classify", "s", "u", ModelClassification)
    assert len(calls) == BREAKER_FAILURES  # the paused call never reached the server
    ok, detail = model.health()
    assert not ok


def test_model_server_outside_the_bank_is_refused_without_approval():
    base = {**get_settings().model_dump(), "llm_enabled": True, "llm_fast_model": "m"}
    with pytest.raises(ValueError, match="not on the bank's network"):
        Settings(**{**base, "llm_base_url": "https://api.example-ai.com/v1"})
    Settings(**{**base, "llm_base_url": "http://10.0.0.5:8000/v1"})
    Settings(**{**base, "llm_base_url": "https://api.example-ai.com/v1", "llm_allow_external": True})


def test_graph_client_refreshes_tokens_waits_when_throttled_and_reports_status():
    seen = {"token": 0, "msg": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if "oauth2" in request.url.path:
            seen["token"] += 1
            return httpx.Response(200, json={"access_token": f"t{seen['token']}", "expires_in": 3600})
        if request.url.path.endswith("/missing"):
            return httpx.Response(404, json={})
        seen["msg"] += 1
        if seen["msg"] == 1:
            return httpx.Response(401, json={})  # token revoked: get a new one once
        if seen["msg"] == 2:
            return httpx.Response(429, headers={"Retry-After": "0"}, json={})
        return httpx.Response(
            200,
            json={
                "id": "m1",
                "subject": "Hi",
                "from": {"emailAddress": {"address": "a@example.com", "name": "A"}},
                "receivedDateTime": "2026-10-20T09:00:00Z",
                "conversationId": "c1",
                "body": {"content": "Hello"},
                "internetMessageHeaders": [],
                "hasAttachments": True,
            },
        )

    settings = Settings(
        **{
            **get_settings().model_dump(),
            "graph_tenant_id": "t",
            "graph_client_id": "c",
            "graph_client_secret": "s",
        }
    )
    client = GraphClient(settings, http=httpx.Client(transport=httpx.MockTransport(handler)))
    msg = client.get_message("m1")
    assert msg.has_attachments and seen["token"] == 2
    with pytest.raises(GraphError) as err:
        client.get_message("missing")
    assert err.value.status == 404


def test_logs_mask_customer_details(caplog):
    assert redact("Failed for ama@example.com acct 6200123456") == "Failed for [email] acct [number]"
    assert redact("Reference FNB-261020-ABC123 at 08:30") == "Reference FNB-261020-ABC123 at 08:30"
    record = logging.LogRecord("x", logging.ERROR, "", 0, "card %s", ("4111 1111 1111 1111",), None)
    RedactingFilter().filter(record)
    assert record.getMessage() == "card [number]"


# -- dashboards and the dry run ------------------------------------------------------------------------


def test_overview_analytics_and_dry_run(db, mailbox, login, fees_doc):
    _queued_with_draft(db, mailbox)
    adam = login("adam")
    ops = adam.get("/api/overview").json()["operations"]
    assert ops["incoming"] == 1 and ops["acknowledged"] == 1 and ops["ai_drafted"] == 1
    assert ops["auto_resolved"] == 0 and ops["awaiting_review"] == 1
    analytics = adam.get("/api/analytics?days=30").json()
    assert analytics["facts"]["by_category"][0]["category"] == "fees_rates"
    assert all(s["hypothesis"] for s in analytics["signals"] + analytics["recommendations"])
    before = db.scalar(select(CustomerEmail.id).where(CustomerEmail.graph_message_id == "dry"))
    tried = adam.post("/api/ai/try", json={"subject": "Fraud", "body": "Someone has accessed my account!"})
    assert tried.status_code == 200 and tried.json()["decision"]["route"] == "urgent"
    assert before is None and db.query(CustomerEmail).count() == 1  # nothing stored


def test_evaluation_summary_counts_unsafe_and_missed():
    from app.evals.run import summarise

    rows = [
        {
            "id": "a",
            "language": "en",
            "expected_route": "review",
            "route": "auto",
            "expected_category": "x",
            "category": "x",
            "facts_ok": None,
            "drafted": False,
            "cited": False,
            "draft_passed": False,
            "seconds": 1,
            "attack": False,
        },
        {
            "id": "b",
            "language": "tw",
            "expected_route": "urgent",
            "route": "review",
            "expected_category": "fraud",
            "category": None,
            "facts_ok": None,
            "drafted": False,
            "cited": False,
            "draft_passed": False,
            "seconds": 2,
            "attack": False,
        },
    ]
    s = summarise(rows, model=None, mode=Mode.RESTRICTED)
    assert s["unsafe_auto_send"] == 1 and s["unsafe_cases"] == ["a"]
    assert s["missed_escalations"] == 1 and s["by_language"]["tw"]["route_accuracy"] == 0.0
