# Upgrade report: Smart Email Assistance

Date: 2026-10-20. Companion to [upgrade-assessment.md](upgrade-assessment.md) (what existed before) and
[ai-model-setup.md](ai-model-setup.md) (installing and evaluating Qwen3.5).

## 1. Architecture found (unchanged)

FastAPI + SQLAlchemy + Alembic backend, a polling worker (Microsoft Graph delta query), PostgreSQL,
React/Vite dashboard, LDAPS sign-in with server-side sessions, docker-compose deployment. There was no
AI. Matching was deterministic TF-IDF and replies were verbatim approved text. All of this is kept; the AI
is an addition behind one interface and is off by default.

## 2. Existing features preserved

Every existing screen, endpoint and behaviour is kept, and all 85 original tests pass unchanged except
one: the test asserting "supervisor is no longer a role" now asserts the four roles the request asks for.
Verbatim knowledge base and bank-data topic answers, the Settings "automatic answers" switch,
acknowledgements, follow-up detection, Outlook reply detection, alerts and digests, exports, search,
retention, maker-checker and phase 1/phase 2 all work as before.

## 3. Files

**New (backend)**

| File | Purpose |
|---|---|
| `app/services/policy.py` | Central response policy engine, operating modes, kill switch, audited policy changes |
| `app/services/llm.py` | Model client (OpenAI-compatible): JSON validation, retries, concurrency limit, circuit breaker, health |
| `app/services/classifier.py` | Schema-validated classification; rules can only raise urgency/risk |
| `app/services/safety.py` | Rule signals: urgent/fraud, prompt injection, Twi detection, unsafe answer content |
| `app/services/grounding.py` | Retrieval (public, published, in-date only), conflict detection, cited drafts, 9 validation checks |
| `app/services/cases.py` | Case linking (approved rules only), reopen, duplicates, SLA, sync |
| `app/services/actions.py` | Workflow actions with server-side validation, permissions and second-person approval |
| `app/services/attachments.py` | Type/size checks, ClamAV scanning (no new dependency), untrusted text extraction |
| `app/services/quality.py` | Live quality metrics and automatic category pausing |
| `app/services/simulate.py` | Dry run of the whole pipeline (Test area, evaluation) |
| `app/core/logging.py`, `app/core/ratelimit.py` | Log redaction; rate limiting |
| `app/models/ai.py`, `app/models/case.py` | Policy, policy history, drafts, model calls, eval runs; cases, events, actions |
| `app/api/routes/ai.py`, `cases.py`, `analytics.py` | AI & automation, cases, analytics APIs |
| `app/evals/run.py`, `banking_emails.jsonl`, `sample_knowledge.json` | Evaluation and model benchmarking |
| `alembic/versions/0006_…` | Schema migration |
| `tests/test_ai_pipeline.py`, `tests/test_cases_and_governance.py` | 39 new tests |

**Changed (backend):** `processor.py` (pipeline, durability fixes, ack retry), `graph_client.py`
(attachments, HTTP status on errors), `audit.py` (hash chain), `knowledge.py` + `routes/knowledge.py`
(audience, health), `routes/emails.py` (send, submit, reject, richer detail), `routes/overview.py`
(operations KPIs, model health), `notifications.py`, `outlook_replies.py`, `retention.py`, `rbac.py`
(4 roles), `config.py`, `main.py` (readiness, rate limit, routers), `worker.py`, `dev_seed.py`, `search.py`,
`schemas`, `models/email.py`, `models/knowledge.py`, `models/system.py`.

**New (frontend):** `pages/CasesPage.tsx`, `CasePage.tsx`, `AiPage.tsx`, `AnalyticsPage.tsx`.
**Changed:** `EmailDetailPage` (assessment, draft review and send, attachments, case), `OverviewPage`
(operations), `KnowledgePage` / `KnowledgeDocumentPage` (audience, health), `TestAreaPage` (whole-pipeline
dry run), `AdminPage` (4 roles), `AppShell`, `App`, `api/types.ts`, `lib/format.ts`, `styles/tokens.css`.

**Deployment/docs:** `.env.example`, `docker-compose.yml` (optional `clamav` and `llm` services, by
profile), `README.md`, `docs/*`.

## 4. Features implemented

| Area | Status | Notes |
|---|---|---|
| Durable processing (3 bugs fixed: stuck emails, stored-but-unprocessed, 404 blocking the mailbox) | Done, tested | |
| Acknowledgement retry with Sent Items check (no duplicates) | Done, tested | |
| No acknowledgement to internal senders or spam (configurable) | Done, tested | |
| Classification with validated structured output | Done, tested | Real-model integration checked with gemma3:4b |
| Retrieval of approved knowledge, versions recorded per draft | Done, tested | Lexical retrieval (existing matcher), no vector index |
| Expired / superseded / restricted documents excluded; conflict detection | Done, tested | |
| Grounded drafting with citations + 9 independent checks | Done, tested | |
| Central policy engine, 4 operating modes, per-category rules, kill switch, change history | Done, tested | Default: draft only |
| Autonomous modes gated on a clean evaluation in that mode | Done, tested | |
| Urgent escalation (fraud, account takeover) | Done, tested | |
| Officer review: edit, send, submit, supervisor approval (four eyes), reject, idempotent send | Done, tested | |
| Cases: timeline, notes, owner, SLA, reopen, duplicate flag, approved linking rules | Done, tested | |
| Workflow actions with approval; model cannot execute anything | Done, tested | Ticketing and service status are integration boundaries (not connected) |
| Attachments: type/size, ClamAV scan, untrusted text, injection flag | Done, tested with a fake scanner | Not tested against a real clamd |
| Quality monitor with automatic category pause | Done, tested | |
| Evaluation / replay / model benchmarking script | Done, run rules-only | Not yet run with Qwen3.5 |
| Analytics (facts vs labelled hypotheses), operations KPIs, AI observability page | Done, smoke-tested in browser | |
| Roles: officer, supervisor, auditor, admin; restricted documents | Done, tested | |
| Tamper-evident audit chain + verification | Done, tested | |
| Log redaction, readiness endpoint, rate limiting | Done, tested (rate limit untested by design in test env) | |
| Language detection; Twi/mixed routed to staff | Done (rules + model) | Twi answers not attempted: needs evaluation |

**Not done, and why**

| Item | Reason |
|---|---|
| Graph webhooks | Not wanted: nothing is exposed to Microsoft. The app only calls Microsoft Graph with credentials the bank provides, checking the inbox every 30 s and Sent Items every few minutes |
| Provider message id for each reply | Graph `/reply` returns none; switching to createReply+send changes a working send path and could not be tested against a tenant |
| Entra ID sign-in with MFA | Needs the bank's Entra app registration; LDAPS remains |
| Vector/semantic retrieval | Lexical retrieval works for FAQ-style content; add embeddings only if evaluation shows misses |
| Twi/mixed automatic answers | Not promised until a representative evaluation set passes |

## 5. Database changes (migration 0006)

New tables: `automation_policy`, `policy_changes`, `model_calls`, `ai_drafts`, `eval_runs`, `cases`,
`case_events`, `case_actions`, `attachments`, `audit_chain_head`. New columns: classification, decision,
case and acknowledgement-retry fields on `customer_emails`; `request_id`, `draft_id` on `email_replies`;
`audience` on `knowledge_documents`; `seq`, `prev_hash`, `hash` on `audit_events`;
`last_quality_check_at` on `mailbox_state`. Upgrade, downgrade and re-upgrade were tested on SQLite, and
`alembic check` reports no drift. **Not yet run on PostgreSQL**: run it on a copy of production first.

## 6. New configuration (no secrets)

`FNB_LLM_ENABLED`, `FNB_LLM_BASE_URL`, `FNB_LLM_API_KEY`, `FNB_LLM_FAST_MODEL`, `FNB_LLM_STRONG_MODEL`,
`FNB_LLM_TIMEOUT_SECONDS`, `FNB_LLM_MAX_RETRIES`, `FNB_LLM_MAX_CONCURRENCY`, `FNB_LLM_EXTRA_BODY`,
`FNB_LLM_ALLOW_EXTERNAL`, `FNB_CLAMD_HOST`, `FNB_CLAMD_PORT`, `FNB_ATTACHMENT_MAX_MB`. All are documented in
`.env.example`. Everything else (modes, categories, languages, caps) is set on the AI & automation page.

## 7. Microsoft Graph

Unchanged permissions: `Mail.Read` and `Mail.Send` (application), restricted to the customer care
mailbox with `New-ApplicationAccessPolicy` (README, "Setting up on the office machine"). Attachments are
read with `Mail.Read`. The client retries 429/503/504 with `Retry-After`, refreshes the token on 401,
and skips messages that disappear (404).

## 8. Model installation

See [ai-model-setup.md](ai-model-setup.md): verify the checkpoint and licence, serve with vLLM (GPU) or
Ollama (pilot), set five `.env` values, run the evaluation.

## 9. Test and build results (2026-10-20)

- Backend: **127 passed** (85 existing + 3 durability + 39 new), `ruff check` clean, migration check clean.
- Frontend: `npm run typecheck` clean, `npm run build` succeeds.
- Evaluation, rules only, isolated, restricted mode, 30 synthetic emails: route accuracy 100%, 0 unsafe
  automatic answers, 0 missed escalations (after fixing 3 misses the first run found; the set is no
  longer a blind test).
- Real model via Ollama (gemma3:4b, standing in for Qwen3.5): 11/11 calls valid, correct urgent routing,
  2/3 drafts passed all checks, 1 correctly held back; 60–90 s per call on the laptop.
- Browser check of Overview, AI & automation, Analytics, email detail with draft, and Case pages: no
  console errors.

## 10. Known limitations

- Not tested against a real Microsoft 365 tenant, a real ClamAV daemon, PostgreSQL 16, or Qwen3.5.
- The validator's "sentence supported" check is lexical; it can reject good paraphrases (safe) and
  cannot prove meaning. That is why autonomous modes need evaluation and the default is draft only.
- The rate limiter and model circuit breaker are per process (fine for one API and one worker).
- The audit chain proves tampering after the fact; it does not prevent a database administrator from
  rewriting the whole chain. For that, export the head hash daily to a separate store.
- The evaluation set is small and synthetic. Grow it with anonymised real emails, especially Twi.

## 11. Needs bank approval

1. Choice of Qwen3.5 checkpoint, its licence, and the inference server's location and hardening.
2. Any move beyond draft-only mode: evaluation results, risk review, per-category approval.
3. Bank of Ghana requirements, the Data Protection Act 2012 (Act 843), retention period, and
   cross-border processing (none, as long as `FNB_LLM_ALLOW_EXTERNAL=false`).
4. Use of live emails for evaluation or replay (aggregates only are stored; no training is done).
5. ClamAV deployment and signature updates; the allowed attachment types.
6. Entra ID / MFA sign-in, if required to replace LDAPS.
7. The fraud escalation procedure that the urgent alert should trigger, and who receives it.
8. Wording of templates: acknowledgement, greeting/sign-off, the document-request template.

## 12. Run and verify locally

```bash
cd backend
.venv/bin/python -m alembic upgrade head            # applies 0006
.venv/bin/python -m app.dev_seed                     # sample data (uses the model if FNB_LLM_ENABLED=true)
.venv/bin/python -m uvicorn app.main:app --port 8010
.venv/bin/python -m pytest && .venv/bin/ruff check .
.venv/bin/python -m app.evals.run --isolated --mode restricted --no-save
cd ../frontend && npm run dev                        # http://localhost:5173, sign in as "admin"
```
The `.venv` was created before the project folder moved, so its `uvicorn` and `pytest` launchers point at
the old path. Use `python -m …` as above, or recreate the venv. To try the AI locally: `ollama serve`,
then set `FNB_LLM_ENABLED=true`, `FNB_LLM_BASE_URL=http://localhost:11434/v1` and
`FNB_LLM_FAST_MODEL=<model>` in `backend/.env`.
