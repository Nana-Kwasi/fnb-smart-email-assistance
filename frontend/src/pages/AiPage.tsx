import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Bot, CheckCircle2, CircleDashed, OctagonX, Play, ShieldCheck } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "@/api/client";
import type { AiStatus, AutomationPolicy, CategoryPolicy } from "@/api/types";
import { useAuth } from "@/auth/AuthContext";
import { Badge, Card, Dialog, ErrorAlert, Field, Loading, PageHead, StatCard, Switch } from "@/components/ui";
import { formatDateTime, percent } from "@/lib/format";

const MODE_HELP: Record<string, string> = {
  draft_only: "The assistant drafts answers for officers. Nothing it writes is sent by itself. (Approved text from the knowledge base follows the “automatic answers” switch in Settings.)",
  supervised: "As draft only; drafts that pass every check are marked ready, and officers send them with one click.",
  restricted: "AI answers that pass every check may be sent by themselves, only in low-risk categories marked automatic, up to a daily limit.",
  expanded: "Also medium-risk categories marked automatic, with no daily limit. Only after an approved evaluation and risk review.",
};

function Health({ ok, label, detail }: { ok: boolean | null; label: string; detail: string }) {
  return (
    <div className="health-item">
      {ok === true ? <CheckCircle2 size={18} color="var(--ok)" /> : ok === false ? <AlertTriangle size={18} color="var(--danger)" /> : <CircleDashed size={18} color="var(--ink-faint)" />}
      <div>
        <div style={{ fontWeight: 600 }}>{label}</div>
        <div className="cell-sub">{detail}</div>
      </div>
    </div>
  );
}

function ReasonDialog({ title, confirm, danger, onClose, onConfirm, pending, error }: {
  title: string; confirm: string; danger?: boolean; onClose: () => void; onConfirm: (reason: string) => void; pending: boolean; error: unknown;
}) {
  const [reason, setReason] = useState("");
  return (
    <Dialog title={title} onClose={onClose}>
      <div className="stack">
        <Field label="Reason" hint="Required. Kept in the change history and the audit trail (at least 10 characters).">
          <textarea className="textarea" value={reason} maxLength={500} onChange={(e) => setReason(e.target.value)} placeholder="e.g. Approved by the risk committee, ref RC-12" />
        </Field>
        <ErrorAlert error={error} />
        <div className="row">
          <span className="spacer" />
          <button className="btn btn-ghost" onClick={onClose}>Cancel</button>
          <button className={danger ? "btn btn-danger" : "btn btn-primary"} disabled={pending || reason.trim().length < 10} onClick={() => onConfirm(reason)}>{confirm}</button>
        </div>
      </div>
    </Dialog>
  );
}

function CategoryTable({ policy, editable, onSave }: { policy: AutomationPolicy; editable: boolean; onSave: (c: CategoryPolicy[]) => void }) {
  const [rows, setRows] = useState(policy.categories);
  useEffect(() => setRows(policy.categories), [policy.version, policy.categories]);
  const changed = JSON.stringify(rows) !== JSON.stringify(policy.categories);
  const set = (i: number, patch: Partial<CategoryPolicy>) => setRows(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  return (
    <Card title="Categories" sub="What the assistant may do in each kind of email. Urgent categories can never be answered automatically."
      actions={editable && changed && <button className="btn btn-primary btn-sm" onClick={() => onSave(rows)}>Save changes…</button>}>
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr><th>Category</th><th>Department</th><th>Acknowledge</th><th>Automatic</th><th>Approval</th><th>Risk</th><th>Route</th><th>Deadline (h)</th><th>Status</th></tr>
          </thead>
          <tbody>
            {rows.map((c, i) => (
              <tr key={c.code}>
                <td data-label="Category"><div className="cell-main">{c.label}</div><div className="cell-sub">{c.code}</div></td>
                <td data-label="Department">
                  {editable ? (
                    <select className="select" value={c.department} onChange={(e) => set(i, { department: e.target.value })}>
                      {policy.departments.map((d) => <option key={d}>{d}</option>)}
                    </select>
                  ) : c.department}
                </td>
                <td data-label="Acknowledge"><input type="checkbox" checked={c.ack} disabled={!editable} onChange={(e) => set(i, { ack: e.target.checked })} aria-label={`Acknowledge ${c.label}`} /></td>
                <td data-label="Automatic"><input type="checkbox" checked={c.auto_send} disabled={!editable || c.route === "urgent"} onChange={(e) => set(i, { auto_send: e.target.checked })} aria-label={`Automatic answers for ${c.label}`} /></td>
                <td data-label="Approval">
                  {editable ? (
                    <select className="select" value={c.approval} onChange={(e) => set(i, { approval: e.target.value as CategoryPolicy["approval"] })}>
                      <option value="officer">Officer</option><option value="supervisor">Supervisor</option>
                    </select>
                  ) : c.approval}
                </td>
                <td data-label="Risk">
                  {editable ? (
                    <select className="select" value={c.risk_tier} onChange={(e) => set(i, { risk_tier: e.target.value as CategoryPolicy["risk_tier"] })}>
                      <option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option>
                    </select>
                  ) : c.risk_tier}
                </td>
                <td data-label="Route">
                  {editable ? (
                    <select className="select" value={c.route} onChange={(e) => set(i, { route: e.target.value as CategoryPolicy["route"], auto_send: e.target.value === "urgent" ? false : c.auto_send })}>
                      <option value="queue">Queue</option><option value="urgent">Urgent</option>
                    </select>
                  ) : c.route}
                </td>
                <td data-label="Deadline (h)">
                  {editable ? <input className="input" type="number" min={1} max={720} value={c.sla_hours} style={{ width: 80 }} onChange={(e) => set(i, { sla_hours: Number(e.target.value) })} /> : c.sla_hours}
                </td>
                <td data-label="Status">
                  {c.paused ? (
                    <div>
                      <Badge tone="badge-warn">Paused</Badge>
                      {c.paused_reason && <div className="cell-sub">{c.paused_reason}</div>}
                      {editable && <button className="link-btn" onClick={() => set(i, { paused: false, paused_reason: null })}>Resume</button>}
                    </div>
                  ) : (
                    <div>
                      <Badge tone="badge-ok">Active</Badge>
                      {editable && c.auto_send && <button className="link-btn" onClick={() => set(i, { paused: true, paused_reason: "Paused by an administrator" })}>Pause</button>}
                    </div>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

export default function AiPage() {
  const { can } = useAuth();
  const queryClient = useQueryClient();
  const { data, error, isLoading } = useQuery({ queryKey: ["ai-status"], queryFn: () => api<AiStatus>("/ai/status"), refetchInterval: 60_000 });
  const [dialog, setDialog] = useState<null | { kind: "stop" | "resume" } | { kind: "policy"; changes: Record<string, unknown>; title: string }>(null);
  const [mode, setMode] = useState("");
  const refresh = () => void queryClient.invalidateQueries({ queryKey: ["ai-status"] });
  const kill = useMutation({
    mutationFn: ({ on, reason }: { on: boolean; reason: string }) => api(`/ai/kill-switch`, { json: { on, reason } }),
    onSuccess: () => { setDialog(null); refresh(); void queryClient.invalidateQueries({ queryKey: ["overview"] }); },
  });
  const save = useMutation({
    mutationFn: ({ changes, reason }: { changes: Record<string, unknown>; reason: string }) => api(`/ai/policy`, { method: "PUT", json: { ...changes, reason } }),
    onSuccess: () => { setDialog(null); refresh(); },
  });
  const integrity = useMutation({ mutationFn: () => api<{ ok: boolean; checked: number; broken_at: string | null }>("/ai/audit-integrity") });
  useEffect(() => { if (data) setMode(data.policy.mode); }, [data?.policy.mode]);
  if (isLoading) return <Loading />;
  if (!data) return <ErrorAlert error={error} />;
  const p = data.policy;
  const q = data.quality;
  const manage = can("ai:manage");

  return (
    <>
      <PageHead
        title="AI & automation"
        sub="What the assistant may do on its own, how well it is doing, and every change to its rules."
        actions={
          p.kill_switch ? (
            manage && <button className="btn btn-primary" onClick={() => setDialog({ kind: "resume" })}><Play size={16} /> Resume automatic answers</button>
          ) : (
            (can("replies:approve") || manage) && <button className="btn btn-danger" onClick={() => setDialog({ kind: "stop" })}><OctagonX size={16} /> Emergency stop</button>
          )
        }
      />
      {p.kill_switch && (
        <div className="alert alert-danger">
          <strong>Emergency stop is on.</strong> No answer is sent automatically. Emails are still received, acknowledged and recorded, and officers work as normal.
          {p.kill_switch_reason && <> Reason: {p.kill_switch_reason}</>}
        </div>
      )}

      <div className="grid grid-stats">
        <StatCard label="Operating mode" icon={<Bot size={16} />} value={p.mode_label} note={`Policy version ${p.version}`} accent />
        <StatCard label="AI drafts (7 days)" icon={<ShieldCheck size={16} />} value={q.drafts.created} note={`${percent(q.drafts.validation_failure_rate)} failed a check`} />
        <StatCard label="Officers edited drafts" icon={<ShieldCheck size={16} />} value={percent(q.drafts.officer_edit_rate)} note={`${q.drafts.sent_by_officers} sent from drafts`} />
        <StatCard label="AI answers sent by themselves" icon={<Bot size={16} />} value={q.ai_answers_sent} note={`Citations on ${percent(q.citation_coverage)}`} />
      </div>

      <div className="grid grid-2">
        <Card title="Model">
          <div className="health">
            <Health ok={data.model.enabled ? data.model.healthy : null} label={data.model.enabled ? data.model.server ?? "Model server" : "AI model switched off"} detail={data.model.health_detail} />
            <Health ok={data.attachment_scanning ? true : null} label="Attachment scanning" detail={data.attachment_scanning ? "ClamAV" : "No scanner: attachments are never opened"} />
          </div>
          {data.model.enabled && (
            <dl className="meta" style={{ marginTop: 12 }}>
              <dt>Classification</dt><dd>{data.model.fast_model}</dd>
              <dt>Drafting</dt><dd>{data.model.drafting_model}</dd>
              <dt>Limits</dt><dd>{data.model.timeout_seconds} s timeout · {data.model.max_concurrency} at a time</dd>
              <dt>Data location</dt><dd>{data.model.external_allowed ? <Badge tone="badge-warn">Outside provider allowed</Badge> : "Bank network only"}</dd>
            </dl>
          )}
          {Object.keys(q.models).length > 0 && (
            <div className="table-wrap" style={{ marginTop: 12 }}>
              <table className="table">
                <thead><tr><th>Model</th><th>Calls (7 d)</th><th>Errors</th><th>Latency p50 / p95</th></tr></thead>
                <tbody>
                  {Object.entries(q.models).map(([name, m]) => (
                    <tr key={name}>
                      <td data-label="Model">{name}</td>
                      <td data-label="Calls">{m.calls}</td>
                      <td data-label="Errors">{m.errors} ({percent(m.error_rate)})</td>
                      <td data-label="Latency">{m.latency_p50_ms ?? "—"} / {m.latency_p95_ms ?? "—"} ms</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        <Card title="Operating mode" sub="Staged roll-out. New installations start in draft only.">
          <div className="stack">
            {p.modes.map((m) => (
              <label key={m.value} className="choice" style={{ display: "flex", gap: 10, alignItems: "flex-start" }}>
                <input type="radio" name="mode" value={m.value} checked={mode === m.value} disabled={!manage} onChange={() => setMode(m.value)} />
                <span><strong>{m.label}</strong><br /><span className="cell-sub">{MODE_HELP[m.value]}</span></span>
              </label>
            ))}
            {manage && mode !== p.mode && (
              <div className="row">
                <span className="spacer" />
                <button className="btn btn-primary" onClick={() => setDialog({ kind: "policy", changes: { mode }, title: `Switch to ${p.modes.find((m) => m.value === mode)?.label}` })}>Change mode…</button>
              </div>
            )}
            <div className="cell-sub">
              Autonomous modes need a clean evaluation in that mode from the last 30 days: <code>python -m app.evals.run --mode restricted</code>
            </div>
            {manage && (
              <Switch checked={p.auto_pause_enabled} label="Quality monitor may pause a failing category by itself"
                onChange={(v) => setDialog({ kind: "policy", changes: { auto_pause_enabled: v }, title: v ? "Allow automatic pausing" : "Stop automatic pausing" })} />
            )}
          </div>
        </Card>
      </div>

      <CategoryTable policy={p} editable={manage} onSave={(categories) => setDialog({ kind: "policy", changes: { categories }, title: "Save category rules" })} />

      <div className="grid grid-2">
        <Card title="Quality (last 7 days)" sub="Measured from what happened. Accuracy needs labelled emails: see Evaluations.">
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Category</th><th>Emails</th><th>Automatic</th><th>Wrote back after</th><th>Urgent</th></tr></thead>
              <tbody>
                {Object.entries(q.categories).map(([code, m]) => (
                  <tr key={code}>
                    <td data-label="Category">{p.categories.find((c) => c.code === code)?.label ?? code}</td>
                    <td data-label="Emails">{m.emails ?? 0}</td>
                    <td data-label="Automatic">{m.auto ?? 0} ({percent(m.automation_rate ?? null)})</td>
                    <td data-label="Wrote back after">{m.reopened ?? 0} ({percent(m.reopen_rate ?? null)})</td>
                    <td data-label="Urgent">{m.urgent ?? 0}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {q.possible_missed_escalations.length > 0 && (
            <div className="alert alert-danger" style={{ marginTop: 12 }}>
              Possible missed escalations (answered automatically, later urgent): {q.possible_missed_escalations.join(", ")}
            </div>
          )}
          {Object.keys(q.drafts.failed_checks).length > 0 && (
            <p className="cell-sub">Failed checks: {Object.entries(q.drafts.failed_checks).map(([k, n]) => `${k} ${n}`).join(" · ")}</p>
          )}
        </Card>

        <Card title="Evaluations" sub="Fixed, anonymised test emails run through the whole pipeline. Nothing is sent.">
          {data.evaluations.length === 0 ? (
            <p className="card-sub">No evaluation yet. Run <code>python -m app.evals.run</code> on the server.</p>
          ) : (
            <div className="table-wrap">
              <table className="table">
                <thead><tr><th>When</th><th>Mode / models</th><th>Route accuracy</th><th>Unsafe / missed</th></tr></thead>
                <tbody>
                  {data.evaluations.map((r) => (
                    <tr key={r.id}>
                      <td data-label="When">{formatDateTime(r.at)}<div className="cell-sub">{r.dataset} · {r.cases} emails</div></td>
                      <td data-label="Mode">{String(r.metrics.mode ?? "")}<div className="cell-sub">{r.models.fast ?? "rules only"}{r.models.strong && r.models.strong !== r.models.fast ? ` / ${r.models.strong}` : ""}</div></td>
                      <td data-label="Route accuracy">{percent((r.metrics.route_accuracy as number) ?? null)}</td>
                      <td data-label="Unsafe / missed">
                        <Badge tone={r.ok ? "badge-ok" : "badge-danger"}>{String(r.metrics.unsafe_auto_send)} / {String(r.metrics.missed_escalations)}</Badge>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>

      <div className="grid grid-2">
        <Card title="Change history" sub="Every change to the automation rules: who, when, why, before and after.">
          <div className="timeline">
            {data.changes.length === 0 && <p className="card-sub">No changes yet.</p>}
            {data.changes.map((c) => (
              <div className="timeline-item" key={`${c.version}-${c.at}`}>
                <div className="row"><strong>v{c.version} · {c.by}</strong><span className="spacer" /><span className="cell-sub">{formatDateTime(c.at)}</span></div>
                <div className="cell-sub">{c.reason}</div>
                <div className="cell-sub">Changed: {Object.keys(c.changes).join(", ")}</div>
              </div>
            ))}
          </div>
        </Card>
        <Card title="Incidents and audit" sub="Urgent and system alerts, and a check that the audit trail has not been altered.">
          <div className="stack">
            {data.incidents.length === 0 && <p className="card-sub">No incidents recorded.</p>}
            {data.incidents.map((n, i) => (
              <div key={i} className="row" style={{ gap: 6 }}>
                <Badge tone={n.kind === "urgent" ? "badge-danger" : "badge-warn"}>{n.kind}</Badge>
                <span>{n.subject}</span><span className="spacer" /><span className="cell-sub">{formatDateTime(n.at)}</span>
              </div>
            ))}
            {can("audit:view") && (
              <>
                <div className="row">
                  <button className="btn btn-ghost" onClick={() => integrity.mutate()} disabled={integrity.isPending}><ShieldCheck size={16} /> Check audit trail integrity</button>
                </div>
                {integrity.data && (
                  <div className={`alert ${integrity.data.ok ? "alert-ok" : "alert-danger"}`}>
                    {integrity.data.ok ? `Intact: ${integrity.data.checked} events verified.` : `Altered or missing events found (at ${integrity.data.broken_at}).`}
                  </div>
                )}
                <ErrorAlert error={integrity.error} />
              </>
            )}
          </div>
        </Card>
      </div>

      {dialog?.kind === "stop" && (
        <ReasonDialog title="Emergency stop" confirm="Stop automatic answers" danger onClose={() => setDialog(null)} pending={kill.isPending} error={kill.error}
          onConfirm={(reason) => kill.mutate({ on: true, reason })} />
      )}
      {dialog?.kind === "resume" && (
        <ReasonDialog title="Resume automatic answers" confirm="Resume" onClose={() => setDialog(null)} pending={kill.isPending} error={kill.error}
          onConfirm={(reason) => kill.mutate({ on: false, reason })} />
      )}
      {dialog?.kind === "policy" && (
        <ReasonDialog title={dialog.title} confirm="Save" onClose={() => setDialog(null)} pending={save.isPending} error={save.error}
          onConfirm={(reason) => save.mutate({ changes: dialog.changes, reason })} />
      )}
    </>
  );
}
