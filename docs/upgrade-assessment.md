# Upgrade assessment: what exists, what is missing, what to build

Assessed 2026-10-20 against the "Upgrade Existing FNB Ghana AI Email Assistance Platform" work request.
Baseline before any change: 82 backend tests passing, ruff clean, frontend typecheck clean.

## Architecture found

| Layer | Technology |
|---|---|
| Backend API | Python 3.12, FastAPI, SQLAlchemy 2, Alembic (5 migrations), pydantic-settings |
| Worker | `app/worker.py`: one process, polls Microsoft Graph delta query every 30 s |
| Database | PostgreSQL 16 (SQLite for dev/tests) |
| Frontend | React 18, Vite, TypeScript, TanStack Query, hand-written CSS (FNB tokens) |
| Auth | LDAPS against AD, server-side sessions, CSRF, lockout; two roles (officer, admin) |
| AI | **None.** Matching is deterministic TF-IDF (`services/matcher.py`); replies are verbatim DB/document text |
| Deploy | docker-compose: db, api, worker, nginx web |

## Requirement by requirement

Legend: ✅ built · 🟡 partial · ❌ missing · ⏸ recommend later / needs bank input · ⛔ not recommended

### Step 1: Receiving email
| Item | State | Notes |
|---|---|---|
| Detect new mail via Graph | ✅ | Delta query polling, starts from "now" so old mail is never answered |
| Webhook notifications | ⛔ | Needs a public HTTPS endpoint Microsoft can reach; delta polling is reliable and suits an internal server. Keep polling |
| Durable async processing | 🟡→✅ | DB-backed; **two bugs fixed** (see below) |
| Throttling / transient retries | ✅ | 429/503/504 with Retry-After, token refresh on 401 |
| Duplicate prevention | ✅ | Unique Graph message id; each send attempted once |
| Ignore own/auto/bounce mail | ✅ | `automation_check` |
| HTML/plain bodies | ✅ | Graph returns text; outgoing HTML is escaped |
| Attachments (scan, extract) | ❌ | Not fetched at all. Needs a malware scanner (ClamAV) the bank approves |

### Step 2: Acknowledgement
| Item | State | Notes |
|---|---|---|
| Configurable template + reference | ✅ | Settings → Email wording |
| Sent only after durable record | ✅ | Stored and committed first |
| No repeat for same message / thread | ✅ | |
| Skip internal / automated | 🟡 | Automated skipped; internal-domain senders are still acknowledged |
| Provider message id recorded | ❌ | Graph `/reply` returns no id; would need draft-then-send |
| Retry on failure | ❌ | A failed acknowledgement is recorded but never retried |

### Step 3: Classification
| Item | State | Notes |
|---|---|---|
| Categories, intent, urgency, language, sentiment, department | ❌ | Only "matched topic" + keyword-based "sensitive" flag |
| Schema-validated structured output | ❌ | Needs the model layer |

### Step 4: Retrieval (RAG)
| Item | State | Notes |
|---|---|---|
| Versions, approval (draft/publish), effective & expiry dates | ✅ | Knowledge base |
| Superseded/expired filtered out | ✅ | `live_versions` |
| Source version recorded per answer | ✅ | `knowledge_version_id` on each email |
| Retrieval quality | 🟡 | Lexical TF-IDF only; fine for FAQ-style, weak for paraphrase |
| Conflict detection between documents | ❌ | |
| Restricted documents per role | ❌ | Every officer sees every document |
| Expired-document alerts | ❌ | |

### Steps 5–6: Generation, validation, routing
| Item | State | Notes |
|---|---|---|
| Never invent policy | ✅ | Nothing is generated today |
| LLM drafting with citations | ❌ | |
| Independent validation (evidence support, injection, sensitive data) | ❌ | |
| Central decision policy | 🟡 | `_why_not_auto_answer` + checks inline in `process_email` |
| Per-category automation rules | 🟡 | Knowledge categories have Automatic / Suggest only |
| Kill switch | 🟡 | `auto_reply_enabled` works as one, but is the only control |
| Operating modes (draft-only → pilot → restricted → expanded) | ❌ | |
| Urgent escalation | ✅ | Keyword-based, immediate alert to people in charge |
| Officer edit/approve/send in dashboard | ⛔/❓ | Current design: people reply **in Outlook**; dashboard detects it. See open question |

### Step 7 and governance
| Item | State | Notes |
|---|---|---|
| Audit trail | ✅ | Sign-ins, views, changes, processing decisions |
| Tamper-evident audit | ❌ | No hash chain |
| Retention | ✅ | Configurable months, daily purge |
| Encryption at rest of DB passwords | ✅ | Fernet |
| Entra ID sign-in + MFA | ⏸ | Today LDAPS (no MFA). Needs bank's Entra app registration for OIDC |
| Roles: officer, supervisor, admin, auditor | 🟡 | Only officer and admin |
| Rate limiting | 🟡 | Login lockout only |
| Structured logs with redaction | 🟡 | Logs avoid bodies by convention; no redaction filter |
| Health / readiness | 🟡 | `/api/health` only checks the app DB |

### Advanced features (section 6)
| Item | State |
|---|---|
| Case management (cases spanning emails, timeline, notes, SLA, reopen) | ❌ (email-level only; conversation id links threads) |
| Complaint / friction analytics | 🟡 Overview has trends, reasons and "topic ideas" |
| Quality evaluation, replay testing | 🟡 Test area tries one email at a time |
| Attachment intelligence | ❌ (blocked on scanner) |
| Multilingual (English / Twi) | ❌ |
| Controlled action framework | ❌ |
| Model benchmarking script | ❌ |

### Dashboard (section 7)
Overview, Needs attention, Email log, Email detail, Knowledge base, Topics, Data sources,
Test area, Settings, Users & audit, global search, PDF/Excel export all exist and work. Missing:
AI observability page, case view/timeline, analytics page, kill switch/operating mode controls.

## Bugs found and fixed in this pass

1. **Emails could get stuck invisible.** If anything raised while processing an email, the batch
   aborted, the email stayed in "received" (shown as "Processing", not in Needs attention), and
   because it was already stored it was never processed again. `recover_interrupted` only ran at
   worker start-up. Fixed: each email is processed in isolation (`_process_safely`); a failure moves
   it to Needs attention with "check Outlook before replying"; recovery runs every cycle.
2. **Stored-but-unprocessed emails were lost.** A crash between storing and processing left the
   email unprocessed forever (Graph would not report it again). Fixed: emails never started are
   picked up on the next cycle (safe: nothing was sent yet).
3. **A deleted message blocked the mailbox.** A 404 on one message aborted every poll forever.
   Fixed: `GraphError` now carries the HTTP status; 404 is skipped.

Tests added in `tests/test_email_flow.py` for all three.

## Recommended build order (with Qwen3.5 served locally)

The model is reached through an **OpenAI-compatible HTTP endpoint** (vLLM, Ollama or llama.cpp all
provide one), so any Qwen3.5 checkpoint can be swapped by configuration and no new Python
dependency is needed (`httpx` is already used). Verify the licence of the exact checkpoint chosen.

1. **Stabilisation (remaining):** acknowledgement retry, readiness check (DB + Graph + model), log
   redaction filter, skip acknowledgements to internal domains (configurable).
2. **Model layer:** `services/llm/` client with timeouts, retries, concurrency limit, health check,
   separate "fast" and "strong" model settings; if the model is down everything falls back to
   today's behaviour and emails go to people.
3. **Classification:** category, intent, urgency, language, sentiment, department, evidence; JSON
   validated against a server-side schema and the allowed category list. New columns via migration.
4. **Central policy engine:** one module deciding auto-send / human review / urgent escalation,
   per-category rules, operating mode (default **draft-only**), kill switch, audited history.
5. **Grounded drafting:** retrieve approved entries, Qwen drafts with citations, independent
   validator (every claim traceable to cited text, no account data, injection patterns, length,
   tone). Drafts go to officers; auto-send only in restricted mode and allowed categories.
6. **Cases and roles:** case table linking emails, timeline, internal notes, SLA deadlines,
   reopen/duplicate detection; add supervisor and auditor roles.
7. **Quality and analytics:** evaluation set + replay script + model benchmark script; AI
   observability page; complaint themes (facts vs hypotheses clearly labelled).
8. **Needs bank input:** attachments (ClamAV), Entra ID/MFA sign-in, multilingual (Twi) evaluation.
