import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Check, Pencil, X } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "@/api/client";
import type { Condition, FaqConfig, Revision, TopicDetail, TopicInput } from "@/api/types";
import { useAuth } from "@/auth/AuthContext";
import { SimpleTopicForm } from "@/components/SimpleTopicForm";
import { EMPTY_TOPIC, TopicForm } from "@/components/TopicForm";
import { faqKeyOf } from "@/lib/faq";
import { Badge, Card, Dialog, ErrorAlert, Field, Loading, PageHead, Switch } from "@/components/ui";
import { formatDateTime } from "@/lib/format";

function fromRevision(r: Revision): TopicInput {
  const { title, examples, data_source, builder, query, extractors, max_rows, reply_template, row_template, require_verified_sender } = r;
  return { title, examples, data_source, builder, query, extractors, max_rows, reply_template, row_template, require_verified_sender };
}

const SOURCE_WORDS: Record<string, string> = {
  sender_email: "the customer’s email address",
  account_number: "the account number in the email",
  phone_number: "the phone number in the email",
  card_last4: "the last 4 card digits in the email",
  custom: "text in the email matching a pattern",
};

function describe(c: Condition): string {
  if (c.source === "fixed") return `${c.column} is “${c.value}”`;
  return `${c.column} matches ${SOURCE_WORDS[c.source === "sender_email" ? "sender_email" : c.value_kind ?? "custom"]}`;
}

function RevisionView({ revision, faqKey }: { revision: Revision; faqKey?: string | null }) {
  const b = revision.builder;
  return (
    <div className="stack">
      <div className="chips">
        {revision.examples.map((e) => (
          <span className="chip" key={e} style={{ paddingRight: 12 }}>{e}</span>
        ))}
      </div>
      {faqKey ? (
        <p>
          Replies with the bank’s FAQ answer <strong>“{faqKey}”</strong>, read fresh from the database each time.
        </p>
      ) : b ? (
        <p>
          Reads <strong>{b.columns.join(", ")}</strong> from <code>{b.table}</code> in <strong>{revision.data_source}</strong>
          {b.conditions.length > 0 && <> where {b.conditions.map(describe).join(" and ")}</>}
          {b.many ? `, up to ${revision.max_rows} records` : ""}
          {b.order_by ? `, newest first by ${b.order_by.column}` : ""}.
        </p>
      ) : (
        <div className="mail-body"><code>{revision.query}</code></div>
      )}
      {!faqKey && (
        <div className="mail-body">
          {revision.reply_template}
          {revision.row_template ? `\n\nEach line: ${revision.row_template}` : ""}
        </div>
      )}
      <dl className="meta">
        <dt>Verified sender</dt>
        <dd>{revision.require_verified_sender ? "Required" : "Not required"}</dd>
        <dt>Submitted</dt>
        <dd>{revision.submitted_by.display_name}, {formatDateTime(revision.submitted_at)}</dd>
      </dl>
    </div>
  );
}

export default function TopicPage() {
  const { id = "new" } = useParams();
  const isNew = id === "new";
  const { me, can } = useAuth();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(isNew);
  const [advancedChoice, setAdvancedChoice] = useState<boolean | null>(null);
  const faq = useQuery({ queryKey: ["faq-config"], queryFn: () => api<{ configured: boolean; config: FaqConfig | null }>("/faq/config") });
  const [rejecting, setRejecting] = useState(false);
  const [note, setNote] = useState("");

  const { data: topic, error, isLoading } = useQuery({
    queryKey: ["topic", id],
    queryFn: () => api<TopicDetail>(`/topics/${id}`),
    enabled: !isNew,
  });

  const done = (t: TopicDetail) => {
    queryClient.setQueryData(["topic", t.id], t);
    void queryClient.invalidateQueries({ queryKey: ["topics"] });
    void queryClient.invalidateQueries({ queryKey: ["overview"] });
    setEditing(false);
    setRejecting(false);
    if (isNew) navigate(`/topics/${t.id}`, { replace: true });
  };
  const review = useMutation({
    mutationFn: ({ action }: { action: "approve" | "reject" }) =>
      api<TopicDetail>(`/topics/${id}/revisions/${topic!.pending!.id}/${action}`, action === "reject" ? { json: { note } } : { method: "POST" }),
    onSuccess: done,
  });
  const toggle = useMutation({
    mutationFn: (active: boolean) => api<TopicDetail>(`/topics/${id}/active`, { json: { active } }),
    onSuccess: done,
  });

  const current = topic ? (topic.pending ?? topic.live ?? topic.history[0]) : null;
  const currentKey = faqKeyOf(current, faq.data?.config);
  // Simple form unless this topic was built with the advanced form (or the person switched).
  const advanced = advancedChoice ?? (current !== null && currentKey === null);
  const setAdvanced = (v: boolean) => setAdvancedChoice(v);

  if (!isNew && isLoading) return <Loading />;
  if (!isNew && !topic) return <ErrorAlert error={error} />;

  const title = isNew ? "New topic" : (topic!.pending ?? topic!.live)!.title;
  const ownPending = topic?.pending?.submitted_by.id === me?.user.id && Boolean(me?.features.require_second_person);

  return (
    <>
      <Link to="/topics" className="btn btn-ghost btn-sm" style={{ alignSelf: "flex-start" }}>
        <ArrowLeft size={16} /> All topics
      </Link>
      <PageHead
        title={title}
        sub={isNew ? "Teach the assistant a new question. It goes live once it is approved." : undefined}
        actions={
          !isNew && (
            <>
              {can("topics:approve") && topic!.live && (
                <Switch checked={topic!.is_active} disabled={toggle.isPending} onChange={(v) => toggle.mutate(v)} label="Live" />
              )}
              {can("topics:edit") && !editing && (
                <button className="btn btn-ghost" onClick={() => setEditing(true)}>
                  <Pencil size={16} /> Edit
                </button>
              )}
            </>
          )
        }
      />
      <ErrorAlert error={toggle.error} />

      {editing ? (
        <>
          {!advanced && faq.data?.configured && faq.data.config ? (
            <SimpleTopicForm
              config={faq.data.config}
              topicId={isNew ? undefined : id}
              initial={
                current && currentKey !== null
                  ? { key: currentKey, title: current.title, examples: current.examples }
                  : undefined
              }
              onDone={done}
            />
          ) : !advanced && faq.isLoading ? (
            <Loading />
          ) : !advanced ? (
            <Card title="The FAQ table isn’t set up yet">
              <p className="card-sub">
                Topics normally just pick an answer from the bank’s FAQ. An administrator sets this up once on the
                Data sources page. Until then you can use the advanced form.
              </p>
            </Card>
          ) : (
            <TopicForm initial={current ? fromRevision(current) : EMPTY_TOPIC} topicId={isNew ? undefined : id} onDone={done} />
          )}
          <button type="button" className="link-btn" style={{ alignSelf: "flex-start" }} onClick={() => setAdvanced(!advanced)}>
            {advanced ? "Back to the simple form" : "Advanced form (choose tables and columns yourself)"}
          </button>
        </>
      ) : (
        <>
          {topic!.pending && (
            <Card
              title="Change awaiting approval"
              actions={<Badge tone="badge-accent">Pending</Badge>}
              sub={ownPending ? "You submitted this change, so someone else must approve it." : undefined}
            >
              <RevisionView revision={topic!.pending} faqKey={faqKeyOf(topic!.pending, faq.data?.config)} />
              {can("topics:approve") && !ownPending && (
                <div className="row" style={{ marginTop: 16 }}>
                  <ErrorAlert error={review.error} />
                  <span className="spacer" />
                  <button className="btn btn-danger" onClick={() => setRejecting(true)}>
                    <X size={16} /> Reject
                  </button>
                  <button className="btn btn-primary" disabled={review.isPending} onClick={() => review.mutate({ action: "approve" })}>
                    <Check size={16} /> Approve and make live
                  </button>
                </div>
              )}
            </Card>
          )}
          {topic!.live ? (
            <Card title="Live version" actions={<Badge tone="badge-ok">In use</Badge>}>
              <RevisionView revision={topic!.live} faqKey={faqKeyOf(topic!.live, faq.data?.config)} />
            </Card>
          ) : (
            !topic!.pending && <div className="alert">This topic has no approved version yet.</div>
          )}
          <Card title="History">
            <div className="timeline">
              {topic!.history.map((r) => (
                <div key={r.id} className="timeline-item">
                  <div className="row">
                    <strong>{r.title}</strong>
                    <Badge tone={r.status === "approved" ? "badge-ok" : r.status === "rejected" ? "badge-danger" : r.status === "pending" ? "badge-accent" : ""}>
                      {r.status}
                    </Badge>
                  </div>
                  <div className="cell-sub">
                    Submitted by {r.submitted_by.display_name}, {formatDateTime(r.submitted_at)}
                    {r.reviewed_by && ` · reviewed by ${r.reviewed_by.display_name}, ${formatDateTime(r.reviewed_at)}`}
                    {r.review_note && ` · “${r.review_note}”`}
                  </div>
                </div>
              ))}
            </div>
          </Card>
        </>
      )}

      {rejecting && (
        <Dialog title="Reject this change" onClose={() => setRejecting(false)}>
          <div className="stack">
            <Field label="Reason (shown to the author)">
              <textarea className="textarea" value={note} maxLength={500} onChange={(e) => setNote(e.target.value)} />
            </Field>
            <ErrorAlert error={review.error} />
            <div className="row">
              <span className="spacer" />
              <button className="btn btn-ghost" onClick={() => setRejecting(false)}>Cancel</button>
              <button className="btn btn-danger" disabled={review.isPending} onClick={() => review.mutate({ action: "reject" })}>
                Reject change
              </button>
            </div>
          </div>
        </Dialog>
      )}
    </>
  );
}
