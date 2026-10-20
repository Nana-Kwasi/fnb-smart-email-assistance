import { useMutation } from "@tanstack/react-query";
import { BrainCircuit, CheckCircle2, FlaskConical, XCircle } from "lucide-react";
import { useState } from "react";
import { api } from "@/api/client";
import type { Check, Citation, DecisionRule, TestResult } from "@/api/types";
import { useAuth } from "@/auth/AuthContext";
import { Badge, Card, ErrorAlert, Field, PageHead, Switch } from "@/components/ui";
import { categoryLabel, LANGUAGE_LABEL } from "@/lib/format";

interface PipelineResult {
  classification: {
    category: string | null; intent: string | null; language: string; urgency: string; risk: string;
    department: string | null; summary: string | null; prompt_injection: string[]; urgent_signals: string[];
    classified_by: string; model_error: string | null;
  };
  decision: { route: "auto" | "review" | "urgent"; send: string | null; reason: string; rules: DecisionRule[] };
  approved_text: string | null;
  draft: string | null;
  draft_error: string | null;
  draft_checks: Check[];
  citations: Citation[];
}

const ROUTE: Record<string, [string, string]> = {
  auto: ["alert-ok", "Would be answered automatically"],
  review: ["alert-warn", "Would go to an officer"],
  urgent: ["alert-danger", "Urgent escalation"],
};

function Pipeline({ result }: { result: PipelineResult }) {
  const c = result.classification;
  return (
    <div className="stack">
      <div className={`alert ${ROUTE[result.decision.route][0]}`}>
        <strong>{ROUTE[result.decision.route][1]}.</strong> {result.decision.reason}
      </div>
      <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
        <Badge tone="badge-brand">{categoryLabel(c.category)}</Badge>
        <Badge>Urgency: {c.urgency}</Badge>
        <Badge>Risk: {c.risk}</Badge>
        <Badge>{LANGUAGE_LABEL[c.language] ?? c.language}</Badge>
        {c.department && <Badge tone="badge-info">{c.department}</Badge>}
        <span className="cell-sub">by {c.classified_by}</span>
      </div>
      {c.model_error && <div className="alert alert-warn">{c.model_error}</div>}
      {c.intent && <div><strong>Wants:</strong> {c.intent}</div>}
      <details>
        <summary className="cell-sub" style={{ cursor: "pointer" }}>Every rule the policy engine checked</summary>
        <ul style={{ margin: "8px 0 0 0", padding: 0, listStyle: "none" }} className="stack">
          {result.decision.rules.map((r) => (
            <li key={r.rule} className="row" style={{ gap: 6, alignItems: "flex-start" }}>
              {r.ok ? <CheckCircle2 size={15} color="var(--ok)" /> : <XCircle size={15} color="var(--danger)" />}
              <span className="cell-sub">{r.detail}</span>
            </li>
          ))}
        </ul>
      </details>
      {result.approved_text && (<><h3>Approved answer</h3><div className="mail-body">{result.approved_text}</div></>)}
      {result.draft && (
        <>
          <h3>AI draft</h3>
          <div className="mail-body">{result.draft}</div>
          {result.citations.map((s) => <span key={s.n} className="cell-sub">[{s.n}] {s.document} v{s.version}: {s.title}</span>)}
          <ul style={{ margin: 0, padding: 0, listStyle: "none" }} className="stack">
            {result.draft_checks.map((k) => (
              <li key={k.check} className="row" style={{ gap: 6, alignItems: "flex-start" }}>
                {k.passed ? <CheckCircle2 size={15} color="var(--ok)" /> : <XCircle size={15} color="var(--danger)" />}
                <span className="cell-sub"><strong>{k.check}</strong> {k.detail}</span>
              </li>
            ))}
          </ul>
        </>
      )}
      {result.draft_error && <div className="cell-sub">No AI draft: {result.draft_error}</div>}
    </div>
  );
}

export default function TestAreaPage() {
  const { can } = useAuth();
  const canQuery = can("test:run-queries");
  const [form, setForm] = useState({
    subject: "",
    body: "",
    sender_email: "",
    sender_name: "",
    run_query: false,
    verified: true,
  });
  const run = useMutation({ mutationFn: () => api<TestResult>("/test/match", { json: form }) });
  const pipeline = useMutation({
    mutationFn: () =>
      api<PipelineResult>("/ai/try", {
        json: { subject: form.subject, body: form.body || form.subject, sender: form.sender_email || "customer@example.com", sender_verified: form.verified },
      }),
  });
  const set = <K extends keyof typeof form>(key: K, value: (typeof form)[K]) => setForm((f) => ({ ...f, [key]: value }));
  const result = run.data;

  return (
    <>
      <PageHead title="Test Area" sub="Try a customer email and see what the system would do. Nothing is sent." />
      <div className="grid grid-2">
        <Card title="Sample email">
          <form
            className="stack"
            onSubmit={(e) => {
              e.preventDefault();
              run.mutate();
            }}
          >
            <div className="form-grid">
              <Field label="Sender name">
                <input className="input" value={form.sender_name} onChange={(e) => set("sender_name", e.target.value)} />
              </Field>
              <Field label="Sender email">
                <input className="input" type="email" value={form.sender_email} onChange={(e) => set("sender_email", e.target.value)} />
              </Field>
              <Field label="Subject" className="full">
                <input className="input" value={form.subject} onChange={(e) => set("subject", e.target.value)} />
              </Field>
              <Field label="Message" className="full">
                <textarea className="textarea" rows={8} value={form.body} onChange={(e) => set("body", e.target.value)} />
              </Field>
            </div>
            {canQuery && (
              <div className="stack">
                <Switch checked={form.run_query} onChange={(v) => set("run_query", v)} label="Also read the answer from the database (recorded in the audit log)" />
                {form.run_query && (
                  <Switch
                    checked={form.verified}
                    onChange={(v) => set("verified", v)}
                    label="Treat the sender as verified"
                  />
                )}
              </div>
            )}
            <div className="row">
              <button className="btn btn-primary" disabled={run.isPending || !(form.subject || form.body)}>
                <FlaskConical size={16} /> {run.isPending ? "Checking…" : "Check this email"}
              </button>
              <button type="button" className="btn btn-ghost" disabled={pipeline.isPending || (form.subject + form.body).trim().length < 3}
                onClick={() => pipeline.mutate()}>
                <BrainCircuit size={16} /> {pipeline.isPending ? "Running…" : "Run the whole assistant"}
              </button>
            </div>
          </form>
        </Card>

        <Card title="Result">
          <ErrorAlert error={run.error} />
          {!result ? (
            <p className="card-sub">Results appear here.</p>
          ) : (
            <div className="stack">
              <div className={`alert ${result.would_auto_answer ? "alert-ok" : "alert-warn"}`}>{result.decision}</div>
              {result.sensitive_keywords.length > 0 && (
                <div className="row">
                  Sensitive words:
                  {result.sensitive_keywords.map((k) => (
                    <Badge key={k} tone="badge-danger">{k}</Badge>
                  ))}
                </div>
              )}
              {result.ranking.length > 0 && (
                <div className="stack">
                  <h3>Closest topics</h3>
                  {result.ranking.map((r) => (
                    <div key={r.topic_id} className="stack" style={{ gap: 4 }}>
                      <div className="row">
                        <span>{r.title}</span>
                        <span className="spacer" />
                        <strong>{r.score.toFixed(2)}</strong>
                      </div>
                      <div className="meter">
                        <div style={{ width: `${Math.min(100, r.score * 100)}%` }} />
                      </div>
                      <span className="cell-sub">
                        “{r.best_sentence}” ≈ “{r.best_example}”
                      </span>
                    </div>
                  ))}
                </div>
              )}
              <h3>Acknowledgement</h3>
              <div className="mail-body">{result.acknowledgement_preview}</div>
              {form.run_query && (
                <>
                  <h3>Answer from database</h3>
                  {result.answer_preview ? (
                    <div className="mail-body">{result.answer_preview}</div>
                  ) : (
                    <div className="alert alert-warn">{result.answer_reason ?? "No topic matched, so no query was run."}</div>
                  )}
                </>
              )}
            </div>
          )}
        </Card>
      </div>
      {(pipeline.data || pipeline.error) && (
        <Card title="The whole assistant" sub="Safety rules, classification, approved answers, AI draft with its checks, and the policy decision. Nothing is sent or stored.">
          <ErrorAlert error={pipeline.error} />
          {pipeline.data && <Pipeline result={pipeline.data} />}
        </Card>
      )}
    </>
  );
}
