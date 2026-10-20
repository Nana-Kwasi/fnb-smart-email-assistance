import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  BellRing,
  Bot,
  Check,
  CheckCircle2,
  Copy,
  Database,
  ExternalLink,
  FileWarning,
  FolderOpen,
  Inbox,
  Library,
  MailCheck,
  Paperclip,
  Send,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  UserCheck,
  XCircle,
} from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "@/api/client";
import type { Draft, EmailDetail, TopicSummary, User } from "@/api/types";
import { useAuth } from "@/auth/AuthContext";
import { Badge, Card, Dialog, ErrorAlert, Field, Loading, PageHead, PriorityBadge, StatusBadge } from "@/components/ui";
import { categoryLabel, formatDateTime, LANGUAGE_LABEL, requestId, waitingFor } from "@/lib/format";

interface Step {
  at: string;
  icon: ReactNode;
  title: string;
  detail?: string;
  body?: string;
  failed?: boolean;
}

function timeline(email: EmailDetail): Step[] {
  const steps: Step[] = [
    { at: email.received_at, icon: <Inbox size={16} />, title: email.is_follow_up ? "Follow-up received" : "Email received" },
  ];
  const titles: Record<string, string> = {
    acknowledgement: "Acknowledgement sent",
    auto_answer: "Answered automatically (approved text)",
    ai_answer: "Answered automatically by the assistant",
  };
  for (const r of email.replies) {
    steps.push({
      at: r.created_at,
      icon: r.kind === "acknowledgement" ? <MailCheck size={16} /> : r.kind === "officer" ? <Send size={16} /> : <Bot size={16} />,
      title: titles[r.kind] ?? `Replied from the dashboard by ${r.sent_by?.display_name ?? "a person"}`,
      detail: r.succeeded ? undefined : `Not delivered: ${r.error ?? "unknown error"}`,
      body: r.body_text,
      failed: !r.succeeded,
    });
  }
  for (const d of email.drafts) {
    if (d.submitted_at)
      steps.push({ at: d.submitted_at, icon: <UserCheck size={16} />, title: `Reply submitted for approval by ${d.submitted_by?.display_name ?? "a person"}` });
    if (d.status === "rejected" && d.decided_at)
      steps.push({ at: d.decided_at, icon: <XCircle size={16} />, title: `Draft rejected by ${d.decided_by?.display_name ?? "a person"}`, detail: d.reject_reason ?? undefined });
  }
  if (email.urgent_alert_at)
    steps.push({ at: email.urgent_alert_at, icon: <BellRing size={16} />, title: "Urgent alert sent to the people in charge" });
  if (email.overdue_alert_at)
    steps.push({ at: email.overdue_alert_at, icon: <BellRing size={16} />, title: "Overdue reminder sent" });
  if (email.handled_at && email.handled_via !== "automatic" && !(email.handled_via === "dashboard" && email.status === "replied" && email.replies.some((r) => r.kind === "officer"))) {
    steps.push({
      at: email.handled_at,
      icon: email.status === "closed" ? <XCircle size={16} /> : <UserCheck size={16} />,
      title:
        email.handled_via === "outlook"
          ? "Answered by a person in Outlook"
          : email.status === "closed"
            ? `Closed by ${email.handled_by?.display_name ?? "a person"}: no reply needed`
            : `Marked as answered by ${email.handled_by?.display_name ?? "a person"}`,
      detail: email.handled_note ?? undefined,
    });
  }
  return steps.sort((a, b) => a.at.localeCompare(b.at));
}

function SuggestedAnswer({ email }: { email: EmailDetail }) {
  const [topicId, setTopicId] = useState(email.suggested?.topic_id ?? "");
  const [copied, setCopied] = useState(false);
  const topics = useQuery({ queryKey: ["topics"], queryFn: () => api<TopicSummary[]>("/topics") });
  const live = (topics.data ?? []).filter((t) => t.is_live && t.is_active);
  const suggestion = useQuery({
    queryKey: ["suggestion", email.id, topicId],
    queryFn: () => api<{ text: string | null; reason: string }>(`/emails/${email.id}/suggestion/${topicId}`),
    enabled: Boolean(topicId),
  });
  useEffect(() => setCopied(false), [topicId]);

  async function copy() {
    if (!suggestion.data?.text) return;
    await navigator.clipboard.writeText(suggestion.data.text);
    setCopied(true);
  }

  return (
    <Card title="Suggested answer" sub="Built from the bank’s data. Copy it into your reply in Outlook.">
      <div className="stack">
        <Field label="Topic" hint={email.suggested ? `The assistant thinks this is about: ${email.suggested.title}` : undefined}>
          <select className="select" value={topicId} onChange={(e) => setTopicId(e.target.value)}>
            <option value="">Choose a topic…</option>
            {live.map((t) => (
              <option key={t.id} value={t.id}>
                {t.title}
              </option>
            ))}
          </select>
        </Field>
        <ErrorAlert error={suggestion.error} />
        {suggestion.isFetching && <span className="cell-sub">Reading the bank’s data…</span>}
        {suggestion.data &&
          (suggestion.data.text ? (
            <>
              <div className="mail-body">{suggestion.data.text}</div>
              <button className="btn btn-ghost" onClick={() => void copy()}>
                {copied ? <Check size={16} /> : <Copy size={16} />} {copied ? "Copied" : "Copy answer"}
              </button>
            </>
          ) : (
            <div className="alert alert-warn">{suggestion.data.reason}</div>
          ))}
      </div>
    </Card>
  );
}

function KnowledgeAnswer({ email }: { email: EmailDetail }) {
  const [copied, setCopied] = useState(false);
  const k = email.knowledge!;
  const sent = email.status === "auto_answered";
  return (
    <Card
      title={sent ? "Answer sent" : "Suggested answer"}
      sub={
        <>
          From the knowledge base: <Link to={`/knowledge/${k.document_id}`}>{k.document}</Link> (v{k.version}, {k.category})
        </>
      }
    >
      <div className="stack">
        <strong>{k.title}</strong>
        <div className="mail-body">{k.content}</div>
        {!sent && (
          <button className="btn btn-ghost" onClick={() => void navigator.clipboard.writeText(k.content).then(() => setCopied(true))}>
            {copied ? <Check size={16} /> : <Copy size={16} />} {copied ? "Copied" : "Copy answer"}
          </button>
        )}
      </div>
    </Card>
  );
}

const CHECK_LABEL: Record<string, string> = {
  evidence_found: "An approved source covers the question",
  citations_valid: "Cites its sources",
  numbers_supported: "Every figure is in the sources",
  claims_supported: "Every sentence is backed by the sources",
  sources_current: "Sources are published, in date and public",
  no_conflicting_sources: "Sources do not contradict each other",
  safe_content: "Asks for no secrets, makes no promises",
  no_prompt_injection: "The email does not try to instruct the assistant",
  length: "Length",
};

function Assessment({ email }: { email: EmailDetail }) {
  const c = email.classification;
  const urgentTone = email.urgency === "critical" || email.urgency === "high" ? "badge-danger" : "";
  return (
    <Card title="Assistant’s assessment" sub={email.classified_by === "rules" ? "From the safety rules only (AI model not used)" : `Classified by ${email.classified_by ?? "—"}`}>
      <div className="stack">
        <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
          <Badge tone="badge-brand">{categoryLabel(email.category)}</Badge>
          {email.urgency && <Badge tone={urgentTone}>Urgency: {email.urgency}</Badge>}
          {email.risk && <Badge tone={email.risk === "high" ? "badge-warn" : ""}>Risk: {email.risk}</Badge>}
          {email.language && <Badge>{LANGUAGE_LABEL[email.language] ?? email.language}</Badge>}
          {email.sentiment && <Badge>{email.sentiment}</Badge>}
          {email.department && <Badge tone="badge-info">{email.department}</Badge>}
        </div>
        {email.intent && <div><strong>Wants:</strong> {email.intent}</div>}
        {email.ai_summary && <p className="card-sub" style={{ margin: 0 }}>{email.ai_summary}</p>}
        {c && c.prompt_injection.length > 0 && (
          <div className="alert alert-danger">The email tries to instruct the assistant (“{c.prompt_injection[0]}”). Treat it with care.</div>
        )}
        {c && c.urgent_signals.length > 0 && <div className="alert alert-danger">Urgent signs: {c.urgent_signals.join(", ")}</div>}
        {c?.model_error && <div className="alert alert-warn">{c.model_error}</div>}
        {c && c.subcategory_evidence.length > 0 && (
          <div>
            <div className="cell-sub">Why (quoted from the email)</div>
            <ul style={{ margin: "4px 0 0 18px", padding: 0 }}>
              {c.subcategory_evidence.map((q) => <li key={q}>“{q}”</li>)}
            </ul>
          </div>
        )}
        {c && c.next_steps.length > 0 && (
          <div>
            <div className="cell-sub">Suggested next steps (suggestions only; nothing is done automatically)</div>
            <ul style={{ margin: "4px 0 0 18px", padding: 0 }}>
              {c.next_steps.map((n) => <li key={n}>{n}</li>)}
            </ul>
          </div>
        )}
        {email.decision && (
          <details>
            <summary className="cell-sub" style={{ cursor: "pointer" }}>
              How it was routed (policy v{email.decision.policy_version}, {email.decision.mode.replace("_", " ")} mode)
            </summary>
            <ul style={{ margin: "8px 0 0 0", padding: 0, listStyle: "none" }} className="stack">
              {email.decision.rules.map((r) => (
                <li key={r.rule} className="row" style={{ gap: 6, alignItems: "flex-start" }}>
                  {r.ok ? <CheckCircle2 size={15} color="var(--ok)" /> : <XCircle size={15} color="var(--danger)" />}
                  <span className="cell-sub">{r.detail}</span>
                </li>
              ))}
            </ul>
          </details>
        )}
      </div>
    </Card>
  );
}

function Attachments({ email }: { email: EmailDetail }) {
  const tone: Record<string, string> = { clean: "badge-ok", infected: "badge-danger", rejected: "badge-danger", error: "badge-warn", not_scanned: "badge-warn" };
  const word: Record<string, string> = { clean: "Scanned: clean", infected: "Malware found", rejected: "Not accepted", error: "Scan failed", not_scanned: "Not scanned" };
  return (
    <Card title="Attachments" sub="Open the originals in Outlook. Text read from them is the customer’s, not the bank’s.">
      <div className="stack">
        {email.attachments.map((a) => (
          <div key={a.id} className="stack" style={{ gap: 4 }}>
            <div className="row" style={{ gap: 6 }}>
              <Paperclip size={14} /> <strong>{a.name}</strong>
              <span className="cell-sub">{Math.max(1, Math.round(a.size_bytes / 1024))} KB</span>
              <span className="spacer" />
              <Badge tone={tone[a.scan_status]}>{word[a.scan_status]}</Badge>
            </div>
            {a.scan_detail && a.scan_status !== "clean" && <span className="cell-sub">{a.scan_detail}</span>}
            {a.problems.map((p) => <span key={p} className="cell-sub"><FileWarning size={13} /> {p}</span>)}
            {a.extracted_text && (
              <details>
                <summary className="cell-sub" style={{ cursor: "pointer" }}>Show the text read from it</summary>
                <div className="mail-body" style={{ marginTop: 6, maxHeight: 240, overflow: "auto" }}>{a.extracted_text}</div>
              </details>
            )}
          </div>
        ))}
      </div>
    </Card>
  );
}

function ReplyComposer({ email }: { email: EmailDetail }) {
  const { can, me } = useAuth();
  const queryClient = useQueryClient();
  const open = email.drafts.find((d) => ["proposed", "edited", "pending_approval"].includes(d.status));
  const [text, setText] = useState(open ? open.edited_text ?? open.body_text : "");
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  const key = useRef(requestId());
  useEffect(() => setText(open ? open.edited_text ?? open.body_text : ""), [open?.id]);

  const done = (updated: EmailDetail) => {
    queryClient.setQueryData(["email", email.id], updated);
    void queryClient.invalidateQueries({ queryKey: ["attention"] });
    void queryClient.invalidateQueries({ queryKey: ["overview"] });
    key.current = requestId();
  };
  const send = useMutation({
    mutationFn: () => api<EmailDetail>(`/emails/${email.id}/send`, { json: { text, request_id: key.current, draft_id: open?.id ?? null } }),
    onSuccess: done,
  });
  const submit = useMutation({
    mutationFn: () => api<EmailDetail>(`/emails/${email.id}/submit`, { json: { text, draft_id: open?.id ?? null } }),
    onSuccess: done,
  });
  const reject = useMutation({
    mutationFn: () => api<EmailDetail>(`/emails/${email.id}/drafts/${open!.id}/reject`, { json: { reason } }),
    onSuccess: (u) => { done(u); setRejecting(false); },
  });

  const needsSupervisor = email.approval === "supervisor" || open?.status === "pending_approval";
  const ownSubmission = open?.submitted_by?.id === me?.user.id;
  const canSend = can("replies:send") && (!needsSupervisor || (can("replies:approve") && !ownSubmission));
  const edited = open ? text.trim() !== open.body_text.trim() : false;
  const busy = send.isPending || submit.isPending;

  return (
    <Card
      title={open ? (open.status === "pending_approval" ? "Reply waiting for approval" : "Draft reply") : "Write a reply"}
      sub={
        open
          ? open.model.startsWith("template") || open.model.includes("staff")
            ? `Prepared ${open.model.startsWith("template") ? "from a template" : `by ${open.submitted_by?.display_name ?? "staff"}`}. Check it before sending.`
            : `Drafted by ${open.model} from the bank’s approved documents. Check it, edit if needed, then send.`
          : "Sent in the customer’s Outlook thread. You can also reply in Outlook as usual."
      }
    >
      <div className="stack">
        {open && !open.passed && !open.model.startsWith("template") && !open.model.includes("staff") && (
          <div className="alert alert-warn">This draft failed a safety check. Read it carefully against the sources before using any of it.</div>
        )}
        {open?.status === "pending_approval" && (
          <div className="alert">Submitted by {open.submitted_by?.display_name ?? "an officer"}{ownSubmission ? " (you): someone else must approve it." : "."}</div>
        )}
        {needsSupervisor && open?.status !== "pending_approval" && (
          <div className="alert">This category needs a supervisor’s approval before anything is sent.</div>
        )}
        <textarea className="textarea" rows={12} value={text} maxLength={10000} onChange={(e) => setText(e.target.value)}
          aria-label="Reply text" placeholder="Write your reply to the customer…" />
        {open && open.citations.length > 0 && (
          <div>
            <div className="cell-sub">Sources used</div>
            <ul style={{ margin: "4px 0 0 18px", padding: 0 }}>
              {open.citations.map((c) => (
                <li key={c.n}>
                  [{c.n}] {c.document_id ? <Link to={`/knowledge/${c.document_id}`}>{c.document}</Link> : c.document} v{c.version}: {c.title}
                </li>
              ))}
            </ul>
          </div>
        )}
        {open && open.validation.length > 0 && (
          <details open={!open.passed}>
            <summary className="cell-sub" style={{ cursor: "pointer" }}>
              Safety checks: {open.validation.filter((c) => c.passed).length} of {open.validation.length} passed
            </summary>
            <ul style={{ margin: "8px 0 0 0", padding: 0, listStyle: "none" }} className="stack">
              {open.validation.map((c) => (
                <li key={c.check} className="row" style={{ gap: 6, alignItems: "flex-start" }}>
                  {c.passed ? <CheckCircle2 size={15} color="var(--ok)" /> : <XCircle size={15} color="var(--danger)" />}
                  <span><strong>{CHECK_LABEL[c.check] ?? c.check}</strong> <span className="cell-sub">{c.detail}</span></span>
                </li>
              ))}
            </ul>
          </details>
        )}
        <ErrorAlert error={send.error || submit.error || reject.error} />
        <div className="row" style={{ flexWrap: "wrap" }}>
          {open && can("replies:send") && (open.status !== "pending_approval" || can("replies:approve")) && (
            <button className="btn btn-ghost" onClick={() => setRejecting(true)}>
              <XCircle size={16} /> Reject draft
            </button>
          )}
          <span className="spacer" />
          {edited && <span className="cell-sub">Edited</span>}
          {needsSupervisor && can("replies:send") && !canSend && open?.status !== "pending_approval" && (
            <button className="btn btn-primary" disabled={busy || text.trim().length < 2} onClick={() => submit.mutate()}>
              <UserCheck size={16} /> Submit for approval
            </button>
          )}
          {canSend && (
            <button className="btn btn-primary" disabled={busy || text.trim().length < 2} onClick={() => send.mutate()}>
              <Send size={16} /> {needsSupervisor ? "Approve and send" : "Send to customer"}
            </button>
          )}
        </div>
      </div>
      {rejecting && (
        <Dialog title="Reject this draft" onClose={() => setRejecting(false)}>
          <div className="stack">
            <Field label="Why?" hint="Kept with the draft, and counted in AI quality.">
              <textarea className="textarea" value={reason} maxLength={500} onChange={(e) => setReason(e.target.value)} placeholder="e.g. Fee figure is out of date" />
            </Field>
            <div className="row">
              <span className="spacer" />
              <button className="btn btn-ghost" onClick={() => setRejecting(false)}>Cancel</button>
              <button className="btn btn-danger" disabled={reject.isPending || reason.trim().length < 3} onClick={() => reject.mutate()}>Reject</button>
            </div>
          </div>
        </Dialog>
      )}
    </Card>
  );
}

function ManagePanel({ email }: { email: EmailDetail }) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [resolving, setResolving] = useState<"replied" | "closed" | null>(null);
  const [note, setNote] = useState("");
  const users = useQuery({ queryKey: ["assignable-users"], queryFn: () => api<User[]>("/emails/assignable-users") });

  const onDone = (updated: EmailDetail) => {
    queryClient.setQueryData(["email", email.id], updated);
    void queryClient.invalidateQueries({ queryKey: ["attention"] });
    void queryClient.invalidateQueries({ queryKey: ["overview"] });
  };
  const assign = useMutation({
    mutationFn: (userId: string) => api<EmailDetail>(`/emails/${email.id}/assign`, { json: { user_id: userId || null } }),
    onSuccess: onDone,
  });
  const resolve = useMutation({
    mutationFn: () => api<EmailDetail>(`/emails/${email.id}/resolve`, { json: { outcome: resolving, note } }),
    onSuccess: (updated) => {
      onDone(updated);
      setResolving(null);
      navigate("/attention");
    },
  });
  const open = email.status === "queued" || email.status === "received";

  return (
    <Card title="Owner and status">
      <div className="stack">
        <Field label="Owner">
          <select className="select" value={email.assigned_to?.id ?? ""} disabled={assign.isPending}
            onChange={(e) => assign.mutate(e.target.value)}>
            <option value="">Nobody yet</option>
            {(users.data ?? []).map((u) => (
              <option key={u.id} value={u.id}>
                {u.display_name}
              </option>
            ))}
          </select>
        </Field>
        <ErrorAlert error={assign.error} />
        {open && (
          <>
            <p className="card-sub">
              Replied in Outlook instead? You don’t need to do anything: this updates on its own within a few minutes.
            </p>
            <div className="row">
              <button className="btn btn-ghost" onClick={() => setResolving("replied")}>
                <CheckCircle2 size={16} /> Answered another way
              </button>
              <button className="btn btn-danger" onClick={() => setResolving("closed")}>
                <XCircle size={16} /> No reply needed
              </button>
            </div>
          </>
        )}
      </div>
      {resolving && (
        <Dialog title={resolving === "replied" ? "Answered another way" : "No reply needed"} onClose={() => setResolving(null)}>
          <div className="stack">
            <Field
              label={resolving === "replied" ? "How was it answered? (optional)" : "Why is no reply needed?"}
              hint="Kept in the audit trail."
            >
              <textarea className="textarea" value={note} maxLength={500} onChange={(e) => setNote(e.target.value)}
                placeholder={resolving === "replied" ? "e.g. Called the customer" : "e.g. Duplicate of FNB-…"} />
            </Field>
            <ErrorAlert error={resolve.error} />
            <div className="row">
              <span className="spacer" />
              <button className="btn btn-ghost" onClick={() => setResolving(null)}>Cancel</button>
              <button
                className={resolving === "replied" ? "btn btn-primary" : "btn btn-danger"}
                disabled={resolve.isPending || (resolving === "closed" && note.trim().length < 3)}
                onClick={() => resolve.mutate()}
              >
                Confirm
              </button>
            </div>
          </div>
        </Dialog>
      )}
    </Card>
  );
}

function DraftHistory({ draft }: { draft: Draft }) {
  return (
    <div className="stack">
      <div className="cell-sub">
        {draft.model} · {draft.passed ? "passed every check" : "failed a check"}
        {draft.decided_by && ` · sent by ${draft.decided_by.display_name}`}
        {draft.edited_text ? " · edited before sending" : ""}
      </div>
      {draft.citations.map((c) => (
        <span key={c.n} className="cell-sub">
          <Sparkles size={13} /> [{c.n}] {c.document} v{c.version}: {c.title}
        </span>
      ))}
      <details>
        <summary className="cell-sub" style={{ cursor: "pointer" }}>Show the original draft</summary>
        <div className="mail-body" style={{ marginTop: 8 }}>{draft.body_text}</div>
      </details>
    </div>
  );
}

export default function EmailDetailPage() {
  const { id = "" } = useParams();
  const { can, me } = useAuth();
  const navigate = useNavigate();
  const { data: email, error, isLoading } = useQuery({
    queryKey: ["email", id],
    queryFn: () => api<EmailDetail>(`/emails/${id}`),
  });
  if (isLoading) return <Loading />;
  if (!email) return <ErrorAlert error={error} />;

  const canHandle = can("queue:handle") && email.status !== "skipped";

  return (
    <>
      <button className="btn btn-ghost btn-sm" style={{ alignSelf: "flex-start" }} onClick={() => navigate(-1)}>
        <ArrowLeft size={16} /> Back
      </button>
      <PageHead
        title={email.subject || "(no subject)"}
        sub={
          <span className="row" style={{ gap: 8 }}>
            <Badge tone="badge-brand">{email.reference}</Badge>
            <StatusBadge status={email.status} />
            <PriorityBadge priority={email.priority} />
            {email.status === "queued" && <span className="cell-sub">waiting {waitingFor(email.received_at)}</span>}
          </span>
        }
        actions={
          // The link comes from Microsoft 365; only ever follow an https link.
          email.web_link?.startsWith("https://") && (
            <a className="btn btn-primary" href={email.web_link} target="_blank" rel="noopener noreferrer">
              <ExternalLink size={16} /> Open in Outlook
            </a>
          )
        }
      />
      {email.status_reason && (
        <div className={`alert ${email.status === "queued" ? "alert-warn" : ""}`}>{email.status_reason}</div>
      )}

      <div className="grid grid-main-side">
        <div className="stack" style={{ gap: "var(--gap)" }}>
          <Card title="Customer’s email">
            <div className="mail-body">{email.body_text || "(empty)"}</div>
          </Card>
          {email.status === "queued" && can("replies:send") && <ReplyComposer email={email} />}
          {email.status !== "queued" && email.drafts.some((d) => d.status === "sent") && (
            <Card title="Assistant’s draft" sub="What the assistant proposed, and what was sent">
              <DraftHistory draft={email.drafts.find((d) => d.status === "sent")!} />
            </Card>
          )}
          <Card title="What happened">
            <div className="timeline">
              {timeline(email).map((s, i) => (
                <div key={i} className={`timeline-item ${s.failed ? "failed" : ""}`}>
                  <div className="row">
                    <span style={{ color: "var(--fnb-teal)", display: "inline-flex" }}>{s.icon}</span>
                    <strong>{s.title}</strong>
                    <span className="spacer" />
                    <span className="cell-sub">{formatDateTime(s.at)}</span>
                  </div>
                  {s.detail && <div className="cell-sub" style={{ marginTop: 4 }}>{s.detail}</div>}
                  {s.body && (
                    <details style={{ marginTop: 6 }}>
                      <summary className="cell-sub" style={{ cursor: "pointer" }}>Show what was sent</summary>
                      <div className="mail-body" style={{ marginTop: 8 }}>{s.body}</div>
                    </details>
                  )}
                </div>
              ))}
            </div>
          </Card>
        </div>

        <div className="stack" style={{ gap: "var(--gap)" }}>
          {(email.category || email.classified_by) && <Assessment email={email} />}
          {email.attachments.length > 0 && <Attachments email={email} />}
          <Card title="Details">
            <dl className="meta">
              <dt>From</dt>
              <dd>
                {email.from_name && <div>{email.from_name}</div>}
                <div className="cell-sub">{email.from_address}</div>
              </dd>
              <dt>Sender check</dt>
              <dd>
                {email.sender_verified ? (
                  <Badge tone="badge-ok"><ShieldCheck size={13} /> Verified by Microsoft 365</Badge>
                ) : (
                  <Badge tone="badge-warn"><ShieldAlert size={13} /> Not verified</Badge>
                )}
              </dd>
              <dt>Received</dt>
              <dd>{formatDateTime(email.received_at)}</dd>
              {email.case && (
                <>
                  <dt>Case</dt>
                  <dd>
                    {can("cases:view") ? (
                      <Link to={`/cases/${email.case.id}`} className="row" style={{ gap: 6 }}>
                        <FolderOpen size={14} /> {email.case.reference}
                      </Link>
                    ) : (
                      email.case.reference
                    )}
                    <div className="cell-sub">
                      {email.case.emails} email{email.case.emails === 1 ? "" : "s"} · {email.case.status}
                      {email.case.reopened_count > 0 && ` · reopened ${email.case.reopened_count}×`}
                    </div>
                    {email.case.possible_duplicate_of && (
                      <div className="cell-sub">Possible duplicate of {email.case.possible_duplicate_of}</div>
                    )}
                  </dd>
                </>
              )}
              <dt>Answer source</dt>
              <dd>
                {email.knowledge ? (
                  <span className="row" style={{ gap: 6 }}>
                    <Library size={14} /> {email.knowledge.document} v{email.knowledge.version}
                  </span>
                ) : email.suggested ? (
                  <span className="row" style={{ gap: 6 }}>
                    <Database size={14} /> {email.suggested.title}
                  </span>
                ) : (
                  "None"
                )}
                {email.match_score !== null && <div className="cell-sub">score {email.match_score.toFixed(2)}</div>}
              </dd>
            </dl>
          </Card>
          {email.knowledge && <KnowledgeAnswer email={email} />}
          {canHandle && email.status !== "auto_answered" && !email.knowledge && (me?.features.customer_data_answers || email.suggested) && (
            <SuggestedAnswer email={email} />
          )}
          {canHandle && <ManagePanel email={email} />}
        </div>
      </div>
    </>
  );
}
