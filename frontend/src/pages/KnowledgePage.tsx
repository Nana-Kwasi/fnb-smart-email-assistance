import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, FileUp, FlaskConical, HelpCircle, UserRound } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "@/api/client";
import type { AppSettings, KnowledgeDetail, KnowledgeHealth, KnowledgeSummary, TryResult } from "@/api/types";
import { useAuth } from "@/auth/AuthContext";
import { Badge, Card, Dialog, Empty, ErrorAlert, Field, Loading, PageHead } from "@/components/ui";
import { formatDateTime } from "@/lib/format";

const ACCEPT = ".pdf,.docx,.xlsx,.csv,.txt,.md";

export const STATUS_BADGE: Record<string, [string, string]> = {
  published: ["badge-ok", "Published"],
  draft: ["badge-accent", "Draft"],
  archived: ["", "Archived"],
};

const NEW_CATEGORY = "__new__";

export const AUDIENCE: Record<string, [string, string, string]> = {
  public: ["", "Public", "May be quoted to customers"],
  internal: ["badge-info", "Internal", "Staff guidance: never sent to customers"],
  restricted: ["badge-warn", "Restricted", "Internal, and only supervisors, admins and auditors can open it"],
};

/** Upload a new document, or a new version of one (when documentId is given).
 *  Versions are numbered automatically; the size limit comes from Settings. */
export function UploadDialog({ documentId, onClose }: { documentId?: string; onClose: () => void }) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const { me } = useAuth();
  const limitMb = me?.features.max_upload_mb ?? 15;
  const settings = useQuery({ queryKey: ["settings"], queryFn: () => api<AppSettings>("/settings"), enabled: !documentId });
  const [file, setFile] = useState<File | null>(null);
  const [form, setForm] = useState({ name: "", category: "", newCategory: "", effective_from: "", expires_on: "", notes: "", audience: "public" });
  const set = (k: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [k]: e.target.value });
  const tooBig = file !== null && file.size > limitMb * 1024 * 1024;
  const category = form.category === NEW_CATEGORY ? form.newCategory.trim() : form.category;

  const upload = useMutation({
    mutationFn: () => {
      const data = new FormData();
      data.append("file", file!);
      if (!documentId) {
        data.append("name", form.name.trim());
        data.append("category", category);
        data.append("audience", form.audience);
      }
      for (const k of ["effective_from", "expires_on", "notes"] as const) if (form[k].trim()) data.append(k, form[k].trim());
      return api<KnowledgeDetail>(documentId ? `/knowledge/${documentId}/versions` : "/knowledge", { method: "POST", body: data });
    },
    onSuccess: (doc) => {
      void queryClient.invalidateQueries({ queryKey: ["knowledge"] });
      void queryClient.invalidateQueries({ queryKey: ["settings"] });
      queryClient.setQueryData(["knowledge-doc", doc.id], doc);
      onClose();
      if (!documentId) navigate(`/knowledge/${doc.id}`);
    },
  });

  return (
    <Dialog title={documentId ? "Upload a new version" : "Upload a document"} onClose={onClose}>
      <form
        className="stack"
        style={{ gap: 16 }}
        onSubmit={(e) => {
          e.preventDefault();
          upload.mutate();
        }}
      >
        <label className={`file-drop ${file ? "has-file" : ""} ${tooBig ? "too-big" : ""}`}>
          <input type="file" accept={ACCEPT} required onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          <FileUp size={22} aria-hidden />
          {file ? (
            <span>
              <strong>{file.name}</strong>
              <small>{(file.size / 1048576).toFixed(1)} MB{tooBig ? ` · larger than the ${limitMb} MB limit` : " · click to choose another"}</small>
            </span>
          ) : (
            <span>
              <strong>Choose a file</strong>
              <small>PDF, Word, Excel, CSV or text · up to {limitMb} MB</small>
            </span>
          )}
        </label>
        {!documentId && (
          <div className="form-grid">
            <Field label="Name">
              <input className="input" required maxLength={200} value={form.name} onChange={set("name")} placeholder="e.g. Card FAQ" />
            </Field>
            <Field label="Category">
              <select className="select" required value={form.category} onChange={set("category")}>
                <option value="">Choose…</option>
                {(settings.data?.knowledge_categories ?? []).map((c) => (
                  <option key={c.name} value={c.name}>
                    {c.name}{c.auto ? "" : " (suggest only)"}
                  </option>
                ))}
                <option value={NEW_CATEGORY}>+ Add a new category…</option>
              </select>
            </Field>
            {form.category === NEW_CATEGORY && (
              <Field label="New category name" className="full" hint="New categories start as “suggest only”. Change that in Settings if their answers may be sent automatically.">
                <input className="input" required minLength={2} maxLength={60} value={form.newCategory} onChange={set("newCategory")} autoFocus />
              </Field>
            )}
          </div>
        )}
        {!documentId && (
          <Field label="Who may see it" hint={AUDIENCE[form.audience][2]}>
            <select className="select" value={form.audience} onChange={set("audience")}>
              {Object.entries(AUDIENCE).map(([k, v]) => <option key={k} value={k}>{v[1]}</option>)}
            </select>
          </Field>
        )}
        <div className="form-grid">
          <Field label="In use from (optional)">
            <input className="input" type="date" value={form.effective_from} onChange={set("effective_from")} />
          </Field>
          <Field label="Until (optional)">
            <input className="input" type="date" value={form.expires_on} onChange={set("expires_on")} />
          </Field>
          <Field label="Note (optional)" className="full">
            <input className="input" maxLength={500} value={form.notes} onChange={set("notes")} placeholder={documentId ? "What changed in this version?" : "Anything worth knowing about this document"} />
          </Field>
        </div>
        <ErrorAlert error={upload.error} />
        <p className="cell-sub">
          Saved as a draft{documentId ? " with the next version number" : ""}. Test it with “Try a customer question”, then{" "}
          {me?.features.require_second_person ? "someone else publishes it." : "publish it."}
        </p>
        <div className="row">
          <span className="spacer" />
          <button type="button" className="btn btn-ghost" onClick={onClose}>Cancel</button>
          <button className="btn btn-primary" disabled={upload.isPending || !file || tooBig}>
            <FileUp size={16} /> {upload.isPending ? "Reading the file…" : "Upload"}
          </button>
        </div>
      </form>
    </Dialog>
  );
}

const OUTCOME: Record<TryResult["outcome"], [string, string, JSX.Element]> = {
  automatic: ["alert-ok", "Would be answered automatically", <CheckCircle2 size={18} key="a" />],
  suggested: ["alert-warn", "Would be suggested to staff", <HelpCircle size={18} key="s" />],
  person: ["alert", "Would go to a person", <UserRound size={18} key="p" />],
};

function TryBox() {
  const [question, setQuestion] = useState("");
  const run = useMutation({
    mutationFn: () => api<TryResult>("/knowledge/try", { json: { question, include_drafts: true } }),
  });
  const r = run.data;
  return (
    <Card title={<span className="row" style={{ gap: 8 }}><FlaskConical size={18} color="var(--fnb-teal)" /> Try a customer question</span>}
      sub="See which answer the assistant would use, how sure it is, and what would happen. Drafts are included, so you can test before publishing. Nothing is sent.">
      <form
        className="row"
        onSubmit={(e) => {
          e.preventDefault();
          run.mutate();
        }}
      >
        <input className="input" style={{ flex: 1, minWidth: 220 }} value={question} maxLength={5000}
          placeholder="e.g. How do I reset my card PIN?" onChange={(e) => setQuestion(e.target.value)} />
        <button className="btn btn-primary" disabled={run.isPending || question.trim().length < 3}>
          {run.isPending ? "Checking…" : "Try"}
        </button>
      </form>
      <ErrorAlert error={run.error} />
      {r && (
        <div className="stack" style={{ marginTop: 14 }}>
          <div className={`alert ${OUTCOME[r.outcome][0]}`}>
            {OUTCOME[r.outcome][2]}
            <span><strong>{OUTCOME[r.outcome][1]}.</strong> {r.explanation}</span>
          </div>
          {r.hits.map((h, i) => (
            <div key={i} className="stack" style={{ gap: 6 }}>
              <div className="row">
                <strong>{h.title}</strong>
                <span className="spacer" />
                <span className="cell-sub">{h.document} · v{h.version}{h.version_status === "draft" ? " (draft)" : ""}</span>
                <strong>{Math.round(h.score * 100)}%</strong>
              </div>
              <div className="meter"><div style={{ width: `${Math.min(100, h.score * 100)}%` }} /></div>
              {i === 0 && <div className="mail-body">{h.content}</div>}
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

function HealthCard() {
  const navigate = useNavigate();
  const { data } = useQuery({ queryKey: ["knowledge-health"], queryFn: () => api<KnowledgeHealth>("/knowledge/health") });
  if (!data) return null;
  const items = [...data.expired, ...data.expiring_soon, ...data.drafts_waiting];
  return (
    <Card title="Health" sub={`${data.documents_in_use} documents and ${data.entries_in_use} answers in use. ${data.index}`}>
      {items.length === 0 && data.conflicts.length === 0 ? (
        <p className="card-sub" style={{ margin: 0 }}><CheckCircle2 size={14} color="var(--ok)" /> No expired documents, nothing waiting, no contradictions found.</p>
      ) : (
        <div className="stack">
          {data.conflicts.map((c) => (
            <div key={c} className="alert alert-danger"><AlertTriangle size={16} /> Contradiction: {c}. Drafts citing these are never sent automatically.</div>
          ))}
          {items.map((d) => (
            <button key={`${d.id}-${d.problem}`} className="link-btn row" style={{ gap: 6, textAlign: "left" }} onClick={() => navigate(`/knowledge/${d.id}`)}>
              <Badge tone={d.problem.startsWith("Expired") ? "badge-danger" : d.problem.startsWith("Draft") ? "badge-accent" : "badge-warn"}>{d.problem}</Badge>
              {d.name}{d.version ? ` v${d.version}` : ""}{d.expires_on ? ` · ${d.expires_on}` : ""}
            </button>
          ))}
        </div>
      )}
    </Card>
  );
}

export default function KnowledgePage() {
  const { can } = useAuth();
  const navigate = useNavigate();
  const [uploading, setUploading] = useState(false);
  const { data, error, isLoading } = useQuery({ queryKey: ["knowledge"], queryFn: () => api<KnowledgeSummary[]>("/knowledge") });

  return (
    <>
      <PageHead
        title="Knowledge base"
        sub="The bank’s documents the assistant answers general questions from. Replies are the document’s own words."
        actions={
          can("knowledge:edit") && (
            <button className="btn btn-primary" onClick={() => setUploading(true)}>
              <FileUp size={16} /> Upload document
            </button>
          )
        }
      />
      <TryBox />
      <HealthCard />
      <Card title="Documents">
        <ErrorAlert error={error} />
        {isLoading ? (
          <Loading />
        ) : data && data.length ? (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Document</th>
                  <th>Category</th>
                  <th>Status</th>
                  <th>Answers in it</th>
                  <th>Emails answered</th>
                  <th>Last upload</th>
                </tr>
              </thead>
              <tbody>
                {data.map((d) => (
                  <tr key={d.id} className="clickable" tabIndex={0} onClick={() => navigate(`/knowledge/${d.id}`)}
                    onKeyDown={(e) => e.key === "Enter" && navigate(`/knowledge/${d.id}`)}>
                    <td data-label="Document">
                      <span className="cell-main">{d.name}</span>
                      {d.audience !== "public" && <> <Badge tone={AUDIENCE[d.audience][0]}>{AUDIENCE[d.audience][1]}</Badge></>}
                    </td>
                    <td data-label="Category">{d.category}</td>
                    <td data-label="Status">
                      <div className="row" style={{ gap: 4 }}>
                        <Badge tone={STATUS_BADGE[d.status][0]}>
                          {STATUS_BADGE[d.status][1]}{d.live_version ? ` · v${d.live_version}` : ""}
                        </Badge>
                        {d.has_draft && d.status === "published" && <Badge tone="badge-accent">New draft</Badge>}
                      </div>
                    </td>
                    <td data-label="Answers in it">{d.entry_count}</td>
                    <td data-label="Emails answered"><strong>{d.answered}</strong></td>
                    <td data-label="Last upload"><span className="cell-sub">{formatDateTime(d.updated_at)}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty title="No documents yet">Upload the bank’s FAQs, product sheets and procedures to get started.</Empty>
        )}
      </Card>
      {uploading && <UploadDialog onClose={() => setUploading(false)} />}
    </>
  );
}
