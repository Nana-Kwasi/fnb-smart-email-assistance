# Smart Email Assistance (FNB)

Reads customer emails from FNB's Outlook (Microsoft 365) customer care mailbox, acknowledges each one,
and, when the answer exists in the bank's own data, replies with that data. Emails it cannot answer
appear under **Needs attention**, and the people in charge are alerted, but only when it matters.

Answers come only from the bank's approved sources. Approved text (knowledge base entries, bank
data lookups) can be sent as it is. Optionally, a **self-hosted AI model (Qwen3.5)** classifies
each email and drafts answers from approved documents, with citations. Independent checks test
every draft, and a central policy decides what may be sent. New installations start in **draft
only** mode: the AI drafts, people send. See [docs/ai-model-setup.md](docs/ai-model-setup.md).

## How an email is handled

1. The worker checks the mailbox every 30 seconds (Microsoft Graph delta query).
2. Auto-replies, bounces and mailing lists are skipped, so the system can never get into an email loop.
   Emails from the bank's own domains are not acknowledged or answered automatically.
3. **Attachments** are checked (type, size) and scanned by ClamAV before anything reads them. Without a
   scanner they are listed for staff and never opened.
4. The email is **classified**: category, intent, urgency, risk, language, department and the evidence for
   it. Rules always run (fraud, account takeover, prompt injection, Twi); the AI model adds the rest when
   switched on. Rules can only make things more careful.
5. It joins a **case** (same conversation, or the same verified sender and category) with a deadline.
6. The customer gets an **acknowledgement** with a reference number. It is retried if Outlook fails, after
   checking Sent Items so it is never sent twice.
7. The **policy engine** (`backend/app/services/policy.py`) decides:
   - **automatic**: approved text, or an AI draft that passed every check in a mode and category that
     allow it;
   - **a person** (Needs attention): with the reason, the AI draft, its sources and its checks;
   - **urgent**: possible fraud or security problems are escalated at once, and nothing is automatic.

Each email is processed once, every reply is stored exactly as sent, and every decision is kept with
the rules it checked. If processing fails part-way, the email goes to a person instead of being lost.

## When the assistant can't answer

People reply **from the dashboard** (check the AI draft and its sources, edit, send) or **in Outlook**, as
before. The worker checks Sent Items every few minutes and marks Outlook replies **Answered in Outlook**.
Complaints, fraud and security replies need a **supervisor**: an officer submits, a different person
approves and sends. Each send carries a request id, so a double click never sends twice. A reply that asks
for a PIN, password or OTP is refused.

The people in charge (Settings → Alerts) are told selectively, not about every email:

| Alert | When |
|---|---|
| **Urgent** | Straight away, for emails with sensitive words |
| **Overdue** | Once per email, when nobody has replied after the set hours (default 4); batched into one message |
| **Daily summary** | Once a day at the set time: what is still waiting, grouped by reason, plus words customers keep using that no topic covers yet (ideas for new topics) |
| **System problem** | When the mailbox or a bank database keeps failing; at most every 6 hours |

Alerts can only go to addresses in `FNB_INTERNAL_EMAIL_DOMAINS`, so customer details never leave the bank.
Every alert is listed under **Users & audit → Alerts sent**.

On the email page, a person can also copy a **suggested answer** built from the bank's data into their
Outlook reply, assign an owner, or mark the email handled (answered by phone, or no reply needed).

## Knowledge base (phase 1)

Upload the bank's documents (PDF, Word, Excel, CSV or text) under **Knowledge base**. Each file is split
into answers: Excel rows with Question/Answer columns, "Q: / A:" pairs, lines ending in "?" followed
by their answer, or headings with the text under them. The page lists exactly what was taken from the
file, and anything that could not be used.

- **Versions:** numbered automatically. Each upload is a draft until published; publishing archives the
  previous version. Optional "in use from/until" dates; the original file is kept. Settings can require
  someone other than the uploader to publish (second-person check, off by default).
- **Categories:** pick one, or add a new one while uploading (new categories start as "suggest only").
- **Size limit:** set in Settings (default 15 MB, up to 50 MB).
- **Try a customer question:** shows which answer would be used, how sure the assistant is, and whether
  it would be sent, suggested to staff, or passed to a person. Drafts are included, so test before publishing.
- **Replies are the document's own words**, with the greeting and sign-off from Settings. Nothing is generated.
- **Settings → AI & automation:** "Answer automatically from the knowledge base" (the on/off switch),
  the level to send automatically, the lower level to suggest to staff, and per category **Automatic**
  or **Suggest only** (e.g. Loans and Policies are suggest only by default). Sensitive words always go to
  a person. Answers over 1,500 characters are only ever suggested.
- **Usage:** each document shows how many emails it answered or was suggested for; each email shows
  which document and version answered it.

Escalation (Settings → Escalation): one **person in charge**, people to **copy**, and a switch to
**always copy every administrator**.

## Phase 1 and phase 2

`FNB_CUSTOMER_DATA_ANSWERS` decides how far automatic answers go:

| | Phase 1 (`false`, default) | Phase 2 (`true`) |
|---|---|---|
| Topics | Same answer for everyone: fees, hours, requirements, how-tos | Also the customer's own records: balances, transactions |
| Data sources | One database | As many as needed |
| Customer-specific emails | Go to a person, reason "needs the customer's own records" | Answered for senders verified by Microsoft 365 |

Everything else (acknowledgements, Needs attention, alerts, Outlook reply tracking, dashboard, search)
works the same in both phases. Turn phase 2 on only after IT and security have signed off.

## Topics: answers from the bank's data

**Everyday way (phase 1):** an admin points the app at the bank's FAQ table once (Data sources →
"Where is the FAQ?": the table, the answer column, and the column that identifies each entry). After
that, creating a topic is two steps:

1. **Pick the answer:** search the bank's FAQ and click the answer customers should receive.
2. **How customers ask it:** a name and a few phrasings.

The answer is read fresh from the FAQ table every time, so when the bank updates its FAQ, replies update too.

**Advanced form** (for technical staff, and for phase 2 customer records):

1. **What customers ask:** a few example phrasings.
2. **Where the answer is:** a database (added by an admin under **Data sources**), a table, and the columns to use.
3. **Which record:** the same answer for everyone (e.g. `code = HOURS`), or, in phase 2, the **customer's own
   record** (e.g. `account_number` = the account number in the email **and** `email` = the customer's email
   address). Customer-specific topics only ever answer senders verified by Microsoft 365 (DMARC).
4. **The reply:** written by clicking column names, e.g. `The balance on your {product} account is {balance:money}.`
5. **Try it** with a sample email before submitting.

The app builds the SELECT itself (SQLAlchemy), so table and column names are quoted correctly for each
database and every value from an email is a bound parameter. Specialists can switch to **Advanced** and
write a single read-only `SELECT`; it is checked before it is saved.

Changes go live only after **a different supervisor approves them** (maker-checker); the previous version stays live until then.

Reply placeholders: `{column}` with optional formats `money`, `number`, `date`, `datetime`, `upper`,
`lower`, `title`; plus `{customer_name}`, `{reference}`, and `{rows}` for a list.

## Data sources

Admins add bank databases on the **Data sources** page (Oracle, SQL Server, PostgreSQL, MySQL). The
connection is tested on save; the password is encrypted with `FNB_ENCRYPTION_KEY` and never shown again.
Use database accounts with **SELECT rights only**, on just the tables the topics need.

## Settings (all in the dashboard)

| Tab | What you set |
|---|---|
| Automation | acknowledgement on/off, automatic answers on/off, the "send automatically" and "suggest" levels (one scale with a coloured band), longest answer sent automatically, sensitive words |
| Knowledge base | categories (Automatic / Suggest only), largest upload, second-person check |
| Escalation | person in charge, people copied, copy every admin, urgent / overdue (hours) / daily summary (time) / system alerts |
| Email wording | acknowledgement, greeting and sign-off around answers, follow-up window (days) |
| Security & data | idle sign-out (minutes), how long to keep emails (months; deleted daily, audit kept), rows per page |

Only server matters (database URL, Active Directory, Microsoft 365, encryption key, email domains) stay in `.env`.

## Exports

Needs attention and the Email log export the list on screen (same filters) as **PDF** (FNB logo,
application name, title, who/when, page numbers) or **Excel** (styled header, frozen headings, filters).

## AI & automation (dashboard page)

- **Operating mode**: draft only (default) → supervised pilot → restricted autonomous → expanded.
  Autonomous modes need a clean evaluation in that mode (`python -m app.evals.run --mode restricted`)
  from the last 30 days, a written reason, and the ai:manage permission. Every change is kept with
  before/after values.
- **Emergency stop**: supervisors can stop all automatic answers; only admins can resume. Reception,
  acknowledgements, cases and the dashboard keep working.
- **Category rules**: acknowledge, automatic, approval (officer/supervisor), risk tier, urgent route,
  deadline, pause.
- **Quality monitor**: hourly; pauses a category when customers keep writing back after automatic answers,
  or when an automatically answered case later turns urgent, and tells the people in charge.
- Model health, latency and error rates, evaluation results, incidents, and an audit-trail integrity check.

## Cases, analytics

- **Cases**: every email about one issue, timeline, internal notes (never sent), owner, deadline,
  reopen and possible-duplicate flags, and workflow actions. Financial actions (e.g. a fee reversal
  request) need a second person, and the system never moves money.
- **Analytics**: categories over time, department workload, backlog, complaint themes, resolution
  times, repeat contact, customer effort, forecast. Patterns that might mean a service problem are
  labelled as possibilities, not findings.

## Dashboard

- **Overview:** today's briefing (plain sentences written from the live numbers by fixed rules, so no AI
  and no data leaving the bank), trends, why people were needed, most answered topics, topic ideas,
  latest emails and system health.
- **Operations** (on Overview): received, acknowledged, AI drafted, answered automatically, waiting,
  urgent, first real answer, time to resolve, automation rate, cases past deadline; each has a fixed
  definition (hover it).
- **Global search** (Ctrl/⌘ K): reference numbers, customers, subjects, topics, people and pages. Each person
  only finds what their role allows.

## Who can sign in, and roles

- **Sign-in:** anyone in the one Smart Email Assistance AD group (`FNB_LDAP_GROUP`) with their normal network username and password.
- **Roles:** **Officer**, **Supervisor**, **Auditor** (read-only, for review and compliance) and **Admin**,
  managed on **Users & audit → Users**. New people start as officer; usernames in `FNB_BOOTSTRAP_ADMINS`
  start as admin so the first admin can get in.
- Admins can switch someone's access off without touching AD. Role and access changes end that person's
  sessions immediately and are recorded in the audit trail. Admins cannot change their own role or access,
  so there is always at least one admin.

| | Officer | Supervisor | Auditor | Admin |
|---|:-:|:-:|:-:|:-:|
| Overview, Needs attention, Email log, Cases, Knowledge base (view), search | ✓ | ✓ | ✓ | ✓ |
| Send replies, assign, notes, case actions, Test area | ✓ | ✓ | | ✓ |
| Approve replies and actions that need a second person; emergency stop | | ✓ | | ✓ |
| AI & automation (view), Analytics, restricted documents, export | | ✓ | ✓ | ✓ |
| Audit trail, users (view) | | | ✓ | ✓ |
| Automation policy, settings, users and roles, documents, topics, data sources | | | | ✓ |

Permissions are defined in one place: `backend/app/security/rbac.py`.

## Security summary

- Sign-in via **LDAPS** against Active Directory; passwords are never stored or logged. Empty passwords and filter injection are refused.
- **Server-side sessions** in an HttpOnly, Secure, SameSite=Strict cookie; 30-minute idle and 8-hour absolute timeouts; logout revokes immediately.
- **CSRF token** required on every change. **Lockout** after 5 failed sign-ins (15 minutes).
- Permission checked on **every API endpoint** (deny by default).
- **Audit trail** of sign-ins, email views, suggested answers viewed, topic changes and approvals, data source
  changes, settings changes, exports, every processing decision, replies and policy changes. It is
  **tamper-evident**: each event's hash covers the previous one (check it on the AI & automation page).
- The AI model must run **on the bank's network** (the app refuses a public model server unless
  `FNB_LLM_ALLOW_EXTERNAL=true`). Prompts and answers are never logged; logs mask email addresses and
  long numbers. Email and attachment text is treated as untrusted data; instructions in it are flagged.
- Knowledge documents are **public** (may be quoted), **internal** (never sent to customers) or
  **restricted** (supervisors, admins, auditors only).
- Rate limits on changes and on sending; `/api/ready` reports database, mailbox and model readiness.
- Bank database passwords are **encrypted at rest**; alerts can only go to internal email domains.
- HTTPS only, strict security headers and CSP, no API docs in production, no stack traces sent to browsers.
- The app refuses to start in production with unsafe settings (short secret, plain `ldap://`, insecure cookies, SQLite).
- Containers run as a non-root user. Secrets live only in `.env` (git-ignored).
- Microsoft Graph needs only `Mail.Read` and `Mail.Send`. **Restrict them to the customer care mailbox** with an
  Exchange application access policy (`New-ApplicationAccessPolicy`), otherwise the app could read every mailbox.

## Try it on your laptop (no LDAP, Outlook or Docker needed)

Uses SQLite, development sign-in (**any username and password work**) and sample data.
Requirements: Python 3.12 and Node 22.

**1. Backend** (terminal 1)
```bash
cd backend
python -m venv .venv
.venv/bin/pip install -r requirements.txt        # Windows: .venv\Scripts\pip install -r requirements.txt
cp .env.dev.example .env                          # Windows: copy .env.dev.example .env
.venv/bin/alembic upgrade head                    # Windows: .venv\Scripts\alembic upgrade head
.venv/bin/python -m app.dev_seed                  # sample bank data, topics and customer emails
.venv/bin/uvicorn app.main:app --port 8010
```

**2. Frontend** (terminal 2)
```bash
cd frontend
npm ci
npm run dev
```

**3.** Open http://localhost:5173 and sign in:
- **`admin`** with any password: administrator (set by `FNB_BOOTSTRAP_ADMINS` in `backend/.env`).
- **Any other name**, e.g. your F Number: starts as officer. Change their role as admin under **Users & Audit**.
- To try approving topics, sign in as a second user, make them supervisor, and approve the first user's change.

To start again from scratch, delete `backend/dev-app.db` and `backend/dev-bank.db` and repeat the
`alembic` and `dev_seed` steps. Development sign-in is refused unless `FNB_ENV=development`, so these
settings can't be used on a server by accident.

## Setting up on the office machine

### 1. Microsoft 365 app registration (Entra ID)
1. Register an app; note the **Tenant ID** and **Client ID**, and create a **client secret**.
2. API permissions → Microsoft Graph → **Application**: `Mail.Read`, `Mail.Send` → grant admin consent.
3. Restrict it to the customer care mailbox (Exchange Online PowerShell):
   ```powershell
   New-ApplicationAccessPolicy -AppId <CLIENT_ID> -PolicyScopeGroupId customercare@fnb.example `
     -AccessRight RestrictAccess -Description "Smart Email Assistance: customer care mailbox only"
   ```

### 2. Active Directory
Create **one** group for everyone who uses the app (e.g. `SmartEmail-Users`) and a read-only service
account for the LDAP lookup. Put your own username in `FNB_BOOTSTRAP_ADMINS`, sign in, then set
everyone else's role on the Users screen.

### 3. Configure and run (Docker)
```bash
cp .env.example .env          # fill in every value
# put fnb-care.crt / fnb-care.key (HTTPS) and ad-ca.pem (LDAPS CA) in deploy/certs/
docker compose up -d --build
```
Open `https://<server-name>/` and sign in with your network account.

### 4. Go live safely
1. Leave **automatic answers off** (the default). Only acknowledgements are sent; matched topics are suggested to officers.
2. Create topics, get them approved, and try real emails in the **Test Area**.
3. Check officers are happy with the suggestions, then switch automatic answers on in **Settings**.

## Running without Docker

Requirements: Python 3.12, Node 22, PostgreSQL 16.

```bash
# backend
cd backend
python -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-drivers.txt   # Windows: .venv\Scripts\pip
cp ../.env.example .env        # edit; set FNB_DATABASE_URL
.venv/bin/alembic upgrade head
.venv/bin/uvicorn app.main:app --port 8010      # the API
.venv/bin/python -m app.worker                  # the mailbox worker (separate terminal)

# frontend
cd frontend
npm ci
npm run dev        # development at http://localhost:5173 (proxies /api to :8010)
npm run build      # production files in dist/, serve with frontend/nginx.conf
```
For local development over http, set `FNB_ENV=development` and `FNB_COOKIE_SECURE=false`.

## Project layout

```
backend/
  app/
    core/        settings (all from environment) and database
    models/      tables: staff, topics, emails, settings, audit
    security/    LDAP sign-in, sessions, roles and permissions
    services/    email processing, policy engine, classifier, grounding (retrieval, drafts, checks),
                 llm (model client), cases, actions, attachments, quality, Outlook client, audit
    evals/       evaluation set and runner (python -m app.evals.run)
    api/         HTTP routes, one file per dashboard area
    main.py      the API          worker.py  the mailbox worker
  alembic/       database migrations
  tests/         pytest suite (fake Outlook, fake AD, SQLite bank database)
frontend/
  src/
    api/         API client and types
    auth/        sign-in state and permission guards
    components/  shell, cards, tables, form controls
    pages/       one file per screen
    styles/      FNB colour tokens, layout, components (glass design)
```

## Checks

```bash
cd backend && .venv/bin/pytest && .venv/bin/ruff check .
cd frontend && npm run typecheck && npm run build
```
