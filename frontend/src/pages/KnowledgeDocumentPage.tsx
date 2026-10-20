import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Archive, ArrowLeft, Check, Download, FileUp, Search } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, query } from "@/api/client";
import type { KnowledgeDetail, KnowledgeEntry, KnowledgeVersion } from "@/api/types";
import { useAuth } from "@/auth/AuthContext";
import { Badge, Card, ErrorAlert, Loading, PageHead } from "@/components/ui";
import { formatDateTime } from "@/lib/format";
import { AUDIENCE, STATUS_BADGE, UploadDialog } from "./KnowledgePage";

function dateRange(v: KnowledgeVersion): string {
  const fmt = (d: string) => new Date(`${d}T12:00`).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
  if (!v.effective_from && !v.expires_on) return "Always";
  return `${v.effective_from ? fmt(v.effective_from) : "Now"} – ${v.expires_on ? fmt(v.expires_on) : "no end"}`;
}

function Entries({ version }: { version: KnowledgeVersion }) {
  const [text, setText] = useState("");
  const [q, setQ] = useState("");
  useEffect(() => {
    const t = setTimeout(() => setQ(text.trim()), 250);
    return () => clearTimeout(t);
  }, [text]);
  const { data, isLoading, error } = useQuery({
    queryKey: ["knowledge-entries", version.id, q],
    queryFn: () => api<KnowledgeEntry[]>(`/knowledge/versions/${version.id}/entries${query({ q })}`),
    placeholderData: (prev) => prev,
  });
  return (
    <Card title={`What the assistant learned from version ${version.version_label}`}
      sub={`${version.entry_count} answers. Each is sent word for word when it matches a customer’s question.`}>
      <label className="search-box" style={{ boxShadow: "none", marginBottom: 14 }}>
        <Search size={18} aria-hidden />
        <input value={text} placeholder="Search these answers" aria-label="Search answers" onChange={(e) => setText(e.target.value)} />
      </label>
      <ErrorAlert error={error} />
      {isLoading ? (
        <Loading />
      ) : (
        <div className="stack">
          {(data ?? []).map((e) => (
            <details key={e.id} className="entry">
              <summary>
                <strong>{e.title}</strong>
                <Badge tone={e.kind === "qa" ? "badge-brand" : ""}>{e.kind === "qa" ? "Question" : "Section"}</Badge>
              </summary>
              <div className="mail-body" style={{ marginTop: 8 }}>{e.content}</div>
            </details>
          ))}
          {data?.length === 0 && <p className="card-sub">No answers match “{q}”.</p>}
        </div>
      )}
    </Card>
  );
}

export default function KnowledgeDocumentPage() {
  const { id = "" } = useParams();
  const { me, can } = useAuth();
  const queryClient = useQueryClient();
  const [uploading, setUploading] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const { data: doc, error, isLoading } = useQuery({
    queryKey: ["knowledge-doc", id],
    queryFn: () => api<KnowledgeDetail>(`/knowledge/${id}`),
  });
  const onDone = (d: KnowledgeDetail) => {
    queryClient.setQueryData(["knowledge-doc", id], d);
    void queryClient.invalidateQueries({ queryKey: ["knowledge"] });
  };
  const publish = useMutation({ mutationFn: (vid: string) => api<KnowledgeDetail>(`/knowledge/versions/${vid}/publish`, { method: "POST" }), onSuccess: onDone });
  const archive = useMutation({ mutationFn: (vid: string) => api<KnowledgeDetail>(`/knowledge/versions/${vid}/archive`, { method: "POST" }), onSuccess: onDone });
  const audience = useMutation({
    mutationFn: (value: string) => api<KnowledgeDetail>(`/knowledge/${id}`, { method: "PATCH", json: { audience: value } }),
    onSuccess: onDone,
  });

  if (isLoading) return <Loading />;
  if (!doc) return <ErrorAlert error={error} />;
  const shown =
    doc.versions.find((v) => v.id === selected) ??
    doc.versions.find((v) => v.status === "draft") ??
    doc.versions.find((v) => v.status === "published") ??
    doc.versions[0];

  return (
    <>
      <Link to="/knowledge" className="btn btn-ghost btn-sm" style={{ alignSelf: "flex-start" }}>
        <ArrowLeft size={16} /> Knowledge base
      </Link>
      <PageHead
        title={doc.name}
        sub={
          <span className="row" style={{ gap: 8 }}>
            <Badge tone="badge-brand">{doc.category}</Badge>
            {can("knowledge:edit") ? (
              <select className="select" style={{ width: "auto" }} value={doc.audience} disabled={audience.isPending}
                title={AUDIENCE[doc.audience][2]} aria-label="Who may see it" onChange={(e) => audience.mutate(e.target.value)}>
                {Object.entries(AUDIENCE).map(([k, v]) => <option key={k} value={k}>{v[1]}: {v[2]}</option>)}
              </select>
            ) : (
              <Badge tone={AUDIENCE[doc.audience][0]}>{AUDIENCE[doc.audience][1]}</Badge>
            )}
          </span>
        }
        actions={
          can("knowledge:edit") && (
            <button className="btn btn-ghost" onClick={() => setUploading(true)}>
              <FileUp size={16} /> Upload new version
            </button>
          )
        }
      />
      <ErrorAlert error={publish.error ?? archive.error ?? audience.error} />

      <Card title="Versions" sub="Only the published version is used, and only between its dates. A new version replaces it once published.">
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Version</th>
                <th>Status</th>
                <th>In use</th>
                <th>Uploaded</th>
                <th>Published</th>
                <th>Emails</th>
                <th aria-label="Actions" />
              </tr>
            </thead>
            <tbody>
              {doc.versions.map((v) => {
                const own = v.uploaded_by.id === me?.user.id && Boolean(me?.features.require_second_person);
                return (
                  <tr key={v.id} className="clickable" onClick={() => setSelected(v.id)}
                    style={v.id === shown?.id ? { background: "rgba(0,133,127,0.06)" } : undefined}>
                    <td data-label="Version">
                      <div>
                        <div className="cell-main">v{v.version_label}</div>
                        <div className="cell-sub">{v.filename} · {(v.size_bytes / 1024).toFixed(0)} KB</div>
                      </div>
                    </td>
                    <td data-label="Status"><Badge tone={STATUS_BADGE[v.status][0]}>{STATUS_BADGE[v.status][1]}</Badge></td>
                    <td data-label="In use">
                      <div>
                        <div>{dateRange(v)}</div>
                        {!v.in_date && <div className="cell-sub" style={{ color: "var(--danger)" }}>Outside its dates</div>}
                      </div>
                    </td>
                    <td data-label="Uploaded">
                      <div>
                        <div>{v.uploaded_by.display_name}</div>
                        <div className="cell-sub">{formatDateTime(v.uploaded_at)}</div>
                      </div>
                    </td>
                    <td data-label="Published">
                      {v.published_by ? (
                        <div>
                          <div>{v.published_by.display_name}</div>
                          <div className="cell-sub">{formatDateTime(v.published_at)}</div>
                        </div>
                      ) : "—"}
                    </td>
                    <td data-label="Emails">
                      <div>
                        <div><strong>{v.answered}</strong> answered</div>
                        <div className="cell-sub">{v.suggested} suggested</div>
                      </div>
                    </td>
                    <td data-label="" onClick={(e) => e.stopPropagation()}>
                      <div className="row" style={{ gap: 6, justifyContent: "flex-end" }}>
                        {v.status === "draft" && can("knowledge:publish") && (
                          <button className="btn btn-primary btn-sm" disabled={own || publish.isPending}
                            title={own ? "You uploaded this version, so someone else must publish it" : undefined}
                            onClick={() => publish.mutate(v.id)}>
                            <Check size={14} /> Publish
                          </button>
                        )}
                        {v.status !== "archived" && can("knowledge:edit") && (
                          <button className="btn btn-ghost btn-sm" disabled={archive.isPending} onClick={() => archive.mutate(v.id)}>
                            <Archive size={14} /> Archive
                          </button>
                        )}
                        <a className="icon-btn" href={`/api/knowledge/versions/${v.id}/file`} title="Download the original file" aria-label="Download">
                          <Download size={16} />
                        </a>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {shown?.status === "draft" && shown.uploaded_by.id === me?.user.id && me?.features.require_second_person && (
          <p className="cell-sub" style={{ marginTop: 10 }}>You uploaded the draft, so someone else needs to publish it (Settings: second-person check).</p>
        )}
      </Card>

      {shown && shown.warnings.length > 0 && (
        <div className="alert alert-warn">
          <AlertTriangle size={18} />
          <div>
            <strong>Parts of v{shown.version_label} could not be used:</strong>
            <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
              {shown.warnings.map((w, i) => <li key={i}>{w}</li>)}
            </ul>
          </div>
        </div>
      )}
      {shown && <Entries version={shown} />}
      {uploading && <UploadDialog documentId={doc.id} onClose={() => setUploading(false)} />}
    </>
  );
}
