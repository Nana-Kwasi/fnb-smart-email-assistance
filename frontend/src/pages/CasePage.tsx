import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, CheckCircle2, MessageSquareText, Play, XCircle } from "lucide-react";
import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "@/api/client";
import type { ActionSpec, CaseDetail, User } from "@/api/types";
import { useAuth } from "@/auth/AuthContext";
import { EmailTable } from "@/components/EmailTable";
import { Badge, Card, ErrorAlert, Field, Loading, PageHead } from "@/components/ui";
import { formatDateTime } from "@/lib/format";
import { CaseStatusBadge } from "./CasesPage";

const EVENT_TITLE: Record<string, string> = {
  opened: "Case opened",
  email_received: "Another email from the customer",
  reopened: "Case reopened: the customer wrote again",
  classified: "Classified",
  acknowledged: "Acknowledgement sent",
  acknowledgement_failed: "Acknowledgement failed",
  draft_created: "Assistant drafted a reply",
  answered_automatically: "Answered automatically",
  sent_to_officer: "Sent to an officer",
  escalated_urgent: "Urgent escalation",
  submitted_for_approval: "Reply submitted for approval",
  reply_sent: "Reply sent from the dashboard",
  answered_in_outlook: "Answered in Outlook",
  email_replied: "Marked answered another way",
  email_closed: "Marked as needing no reply",
  assigned: "Owner changed",
  note: "Internal note",
  resolved: "Case resolved",
  action_requested: "Action requested (needs approval)",
  action_done: "Action done",
  action_failed: "Action could not be done",
  action_rejected: "Action rejected",
};

function detailText(detail: Record<string, unknown>): string {
  const parts: string[] = [];
  for (const key of ["reason", "how", "action", "result", "to", "category", "department"]) {
    const v = detail[key];
    if (v !== undefined && v !== null && v !== "") parts.push(String(v));
  }
  return parts.join(" · ");
}

function Actions({ data, onDone }: { data: CaseDetail; onDone: (c: CaseDetail) => void }) {
  const { can, me } = useAuth();
  const catalog = useQuery({ queryKey: ["action-catalog"], queryFn: () => api<ActionSpec[]>("/cases/actions/catalog") });
  const [name, setName] = useState("");
  const [params, setParams] = useState<Record<string, string>>({});
  const run = useMutation({
    mutationFn: () => {
      const p: Record<string, unknown> = { ...params };
      if (name === "request_documents") p.documents = (params.documents ?? "").split("\n").map((d) => d.trim()).filter(Boolean);
      if (name === "fee_reversal_request") p.amount = Number(params.amount);
      return api<CaseDetail>(`/cases/${data.id}/actions`, { json: { action: name, params: p } });
    },
    onSuccess: (c) => { onDone(c); setName(""); setParams({}); },
  });
  const decide = useMutation({
    mutationFn: ({ id, approve }: { id: string; approve: boolean }) => api<CaseDetail>(`/cases/actions/${id}/decide`, { json: { approve } }),
    onSuccess: onDone,
  });
  const spec = catalog.data?.find((a) => a.name === name);
  const field = (key: string, label: string, el: "input" | "textarea" | "select" = "input", opts: string[] = [], type = "text") => (
    <Field label={label} key={key}>
      {el === "select" ? (
        <select className="select" value={params[key] ?? ""} onChange={(e) => setParams({ ...params, [key]: e.target.value })}>
          <option value="">Choose…</option>
          {opts.map((o) => <option key={o} value={o}>{o}</option>)}
        </select>
      ) : el === "textarea" ? (
        <textarea className="textarea" value={params[key] ?? ""} onChange={(e) => setParams({ ...params, [key]: e.target.value })} />
      ) : (
        <input className="input" type={type} value={params[key] ?? ""} onChange={(e) => setParams({ ...params, [key]: e.target.value })} />
      )}
    </Field>
  );

  return (
    <Card title="Actions" sub="Done by staff, checked by the server. The assistant can only suggest; it cannot run any of these.">
      <div className="stack">
        {data.actions.map((a) => (
          <div key={a.id} className="stack" style={{ gap: 4 }}>
            <div className="row" style={{ gap: 6 }}>
              <strong>{a.label}</strong>
              <Badge tone={{ done: "badge-ok", pending_approval: "badge-warn", failed: "badge-danger", rejected: "" }[a.status]}>
                {a.status.replace("_", " ")}
              </Badge>
              <span className="spacer" />
              <span className="cell-sub">{a.requested_by.display_name}</span>
            </div>
            {a.result && <span className="cell-sub">{a.result}</span>}
            {a.status === "pending_approval" && can("actions:approve") && a.requested_by.id !== me?.user.id && (
              <div className="row">
                <button className="btn btn-sm btn-primary" onClick={() => decide.mutate({ id: a.id, approve: true })}><CheckCircle2 size={14} /> Approve</button>
                <button className="btn btn-sm btn-ghost" onClick={() => decide.mutate({ id: a.id, approve: false })}><XCircle size={14} /> Reject</button>
              </div>
            )}
          </div>
        ))}
        <ErrorAlert error={decide.error} />
        {can("cases:handle") && (
          <>
            <Field label="New action">
              <select className="select" value={name} onChange={(e) => { setName(e.target.value); setParams({}); }}>
                <option value="">Choose an action…</option>
                {(catalog.data ?? []).map((a) => (
                  <option key={a.name} value={a.name}>{a.label}{a.needs_approval ? " (needs approval)" : ""}</option>
                ))}
              </select>
            </Field>
            {spec && <p className="card-sub" style={{ margin: 0 }}>{spec.description}</p>}
            {name === "assign_department" && field("department", "Department", "select", data.departments)}
            {name === "schedule_callback" && [field("when", "When", "input", [], "datetime-local"), field("note", "Note (optional)")]}
            {name === "request_documents" && field("documents", "Documents to ask for (one per line)", "textarea")}
            {name === "set_status" && [field("status", "Status", "select", ["open", "closed"]), field("reason", "Reason")]}
            {name === "create_ticket" && field("summary", "Summary")}
            {name === "check_service_status" && field("service", "Service")}
            {name === "fee_reversal_request" && [field("amount", "Amount (GHS)", "input", [], "number"), field("reason", "Reason")]}
            <ErrorAlert error={run.error} />
            {name && (
              <div className="row">
                <span className="spacer" />
                <button className="btn btn-primary" disabled={run.isPending} onClick={() => run.mutate()}>
                  <Play size={16} /> {spec?.needs_approval ? "Request" : "Do it"}
                </button>
              </div>
            )}
          </>
        )}
      </div>
    </Card>
  );
}

export default function CasePage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const { can } = useAuth();
  const queryClient = useQueryClient();
  const [note, setNote] = useState("");
  const { data, error, isLoading } = useQuery({ queryKey: ["case", id], queryFn: () => api<CaseDetail>(`/cases/${id}`) });
  const users = useQuery({ queryKey: ["assignable-users"], queryFn: () => api<User[]>("/emails/assignable-users"), enabled: can("cases:handle") });
  const onDone = (c: CaseDetail) => {
    queryClient.setQueryData(["case", id], c);
    void queryClient.invalidateQueries({ queryKey: ["cases"] });
  };
  const addNote = useMutation({
    mutationFn: () => api<CaseDetail>(`/cases/${id}/notes`, { json: { text: note } }),
    onSuccess: (c) => { onDone(c); setNote(""); },
  });
  const assign = useMutation({
    mutationFn: (userId: string) => api<CaseDetail>(`/cases/${id}/assign`, { json: { user_id: userId || null } }),
    onSuccess: onDone,
  });
  if (isLoading) return <Loading />;
  if (!data) return <ErrorAlert error={error} />;

  return (
    <>
      <button className="btn btn-ghost btn-sm" style={{ alignSelf: "flex-start" }} onClick={() => navigate(-1)}>
        <ArrowLeft size={16} /> Back
      </button>
      <PageHead
        title={data.reference}
        sub={
          <span className="row" style={{ gap: 8, flexWrap: "wrap" }}>
            <CaseStatusBadge status={data.status} />
            {data.priority === "high" && <Badge tone="badge-danger">Urgent</Badge>}
            <span className="cell-sub">{data.category_label ?? "Not classified"} · {data.department ?? "No department"}</span>
          </span>
        }
      />
      {data.possible_duplicate_of && (
        <div className="alert">Possibly the same issue as {data.possible_duplicate_of} (same address and category). Not merged: check before replying twice.</div>
      )}
      <div className="grid grid-main-side">
        <div className="stack" style={{ gap: "var(--gap)" }}>
          <Card title="Emails in this case">
            <EmailTable emails={data.email_list} />
          </Card>
          <Card title="Timeline">
            <div className="timeline">
              {[...data.events].reverse().map((e) => (
                <div key={e.id} className={`timeline-item ${e.kind.includes("failed") ? "failed" : ""}`}>
                  <div className="row">
                    {e.kind === "note" && <MessageSquareText size={16} color="var(--fnb-teal)" />}
                    <strong>{EVENT_TITLE[e.kind] ?? e.kind}</strong>
                    <span className="spacer" />
                    <span className="cell-sub">{e.actor_name} · {formatDateTime(e.at)}</span>
                  </div>
                  {e.text && <div className="mail-body" style={{ marginTop: 6 }}>{e.text}</div>}
                  {detailText(e.detail) && <div className="cell-sub" style={{ marginTop: 4 }}>{detailText(e.detail)}</div>}
                </div>
              ))}
            </div>
          </Card>
        </div>
        <div className="stack" style={{ gap: "var(--gap)" }}>
          <Card title="Details">
            <dl className="meta">
              <dt>Customer</dt>
              <dd>
                {data.customer_name && <div>{data.customer_name}</div>}
                <div className="cell-sub">{data.customer_address}{data.sender_verified ? " · verified by Microsoft 365" : " · not verified"}</div>
              </dd>
              <dt>Opened</dt>
              <dd>{formatDateTime(data.opened_at)}</dd>
              <dt>Deadline</dt>
              <dd className={data.sla_breached ? "trend-bad" : ""}>{formatDateTime(data.sla_due_at)}{data.sla_breached && " (missed)"}</dd>
              <dt>First answer</dt>
              <dd>{formatDateTime(data.first_response_at)}</dd>
              <dt>Resolved</dt>
              <dd>{formatDateTime(data.resolved_at)}</dd>
              <dt>Reopened</dt>
              <dd>{data.reopened_count}×</dd>
              <dt>Owner</dt>
              <dd>
                {can("cases:handle") ? (
                  <select className="select" value={data.assigned_to?.id ?? ""} disabled={assign.isPending} onChange={(e) => assign.mutate(e.target.value)}>
                    <option value="">Nobody yet</option>
                    {(users.data ?? []).map((u) => <option key={u.id} value={u.id}>{u.display_name}</option>)}
                  </select>
                ) : (
                  data.assigned_to?.display_name ?? "Nobody yet"
                )}
              </dd>
            </dl>
          </Card>
          {can("cases:handle") && (
            <Card title="Internal note" sub="For staff only. Never sent to the customer.">
              <div className="stack">
                <textarea className="textarea" value={note} maxLength={4000} onChange={(e) => setNote(e.target.value)} placeholder="e.g. Called the customer; waiting for their ID." />
                <ErrorAlert error={addNote.error} />
                <div className="row">
                  <span className="spacer" />
                  <button className="btn btn-primary" disabled={!note.trim() || addNote.isPending} onClick={() => addNote.mutate()}>Add note</button>
                </div>
              </div>
            </Card>
          )}
          <Actions data={data} onDone={onDone} />
        </div>
      </div>
    </>
  );
}
