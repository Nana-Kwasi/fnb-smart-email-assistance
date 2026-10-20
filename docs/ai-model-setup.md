# AI model setup (Qwen3.5, self-hosted)

The assistant talks to the model through one OpenAI-compatible endpoint
(`backend/app/services/llm.py`). Changing the model is an `.env` change; nothing in email
processing, the database or the dashboard depends on which model it is.

With `FNB_LLM_ENABLED=false` (the default) the system works exactly as before: safety rules,
approved knowledge base text and bank-data topics, and no AI.

## 1. Before you download: verify the model

Check these on the **official model card of the exact checkpoint** (Hugging Face `Qwen/…` or
Qwen's own site), not a blog. Licences can differ between checkpoints in one family.

| Check | Why |
|---|---|
| Exact release name and revision (commit hash) | So the evaluation, the audit trail and production use the same weights |
| Licence text and commercial-use terms | Third-party guides report Apache 2.0 for Qwen3.5; confirm it for the checkpoint you pick |
| Languages supported (English; any Twi/Akan) | Twi is not promised: it is routed to staff until it passes evaluation |
| JSON / structured output behaviour, "thinking" mode | The client strips `<think>` blocks and validates JSON, but disabling thinking is faster |
| Hardware needs (VRAM at your context length) | Sizing the server |

Sizes reported for the family include small dense models (around 9B and 27B) and a large
mixture-of-experts flagship. A reasonable start, **subject to your own evaluation**:

- `FNB_LLM_FAST_MODEL`: the smallest checkpoint that classifies the evaluation set well
- `FNB_LLM_STRONG_MODEL`: a mid-size checkpoint for drafting (or the same model)

Do not choose on public benchmark rankings. Choose on `python -m app.evals.run` results (section 4).

## 2. Serve it (on the bank's network)

The config refuses a model server outside the bank's network (public host name or address)
unless `FNB_LLM_ALLOW_EXTERNAL=true`, which needs written approval.

**Production: vLLM on a GPU server**
```bash
# docker-compose.yml has an optional "llm" service (profile "ai"):
VLLM_VERSION=<approved version> FNB_LLM_MODEL_ID=Qwen/<exact checkpoint> FNB_LLM_FAST_MODEL=qwen \
  docker compose --profile ai up -d llm
```
`.env`:
```
FNB_LLM_ENABLED=true
FNB_LLM_BASE_URL=http://llm:8000/v1
FNB_LLM_FAST_MODEL=qwen                 # the --served-model-name
FNB_LLM_EXTRA_BODY={"chat_template_kwargs": {"enable_thinking": false}}
```

**Pilot or laptop: Ollama**
```bash
ollama pull <qwen3.5 tag>      # check the tag on ollama.com/library
ollama serve
```
```
FNB_LLM_ENABLED=true
FNB_LLM_BASE_URL=http://localhost:11434/v1
FNB_LLM_FAST_MODEL=<qwen3.5 tag>
```

Check it: the AI & automation page shows "Serving <model>", and `GET /api/ready` includes
`"model": true`.

## 3. How the model is used, and what protects the bank

| Task | Model setting | Output | Checked by |
|---|---|---|---|
| Classify (category, intent, urgency, risk, language, department, evidence) | fast | JSON | Pydantic schema, allowed values only; quotes must exist in the email; rules can only raise urgency/risk |
| Draft an answer | strong | JSON with citations | 9 independent checks (`services/grounding.py`): evidence found, citations valid, every figure in the sources, every sentence supported, sources current and public, no conflicting sources, no secrets asked or promises made, no prompt injection, length |

- The model never sees restricted or internal documents and cannot run anything: no database,
  no email sending, no actions. Sending is decided by `services/policy.py`.
- Timeouts, one retry, a limit on parallel requests, and a circuit breaker (3 failures pause
  calls for 2 minutes). When the model is down, emails are still acknowledged, recorded and
  sent to people with the reason "AI model unavailable".
- Prompts and answers are not logged. `model_calls` keeps only task, model, latency, ok/error.

## 4. Evaluate before every model or knowledge change

```bash
cd backend
# Compare candidates on sample knowledge, without touching the bank's knowledge base:
.venv/bin/python -m app.evals.run --isolated --fast-model <small> --strong-model <mid> --no-save
# The run that unlocks an autonomous mode: bank's knowledge base and policy, in that mode:
.venv/bin/python -m app.evals.run --mode restricted
# Replay recent real emails (aggregates only, nothing stored about the emails):
.venv/bin/python -m app.evals.run --replay-days 30 --mode restricted --no-save
```
Exit code 0 means no unsafe automatic answers and no missed escalations; 2 means there were some.
The dataset `app/evals/banking_emails.jsonl` is synthetic. Extend it with **anonymised**
real cases, especially Twi and mixed-language ones, before relying on the numbers.

## 5. Measured so far

Rules only (no model), sample knowledge, in restricted mode, 30 synthetic emails: route accuracy
100%, 0 unsafe automatic answers, 0 missed escalations, 3 of 3 injection attempts blocked.
Three urgent cases were missed on the first run; the rules were extended, so this set is no
longer a blind test.

Integration check with a real model through Ollama (gemma3:4b, already on the development
laptop; Qwen3.5 was not downloaded): 11 of 11 calls returned valid JSON, fraud was escalated,
2 of 3 drafts passed every check, and the third was correctly held back for weak evidence.
Latency on the development laptop was 60–90 s per call, so production needs a GPU server.
