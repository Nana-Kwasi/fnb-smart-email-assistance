import { useQuery } from "@tanstack/react-query";
import { Search } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, query } from "@/api/client";
import type { CaseSummary, Page } from "@/api/types";
import { Badge, Card, Empty, ErrorAlert, Field, Loading, PageHead, Pager } from "@/components/ui";
import { CATEGORY_LABEL, formatDateTime, timeAgo } from "@/lib/format";

const FILTERS = [
  { key: "", label: "All" },
  { key: "mine", label: "Mine" },
  { key: "sla", label: "Past deadline" },
  { key: "urgent", label: "Urgent" },
  { key: "reopened", label: "Reopened" },
  { key: "duplicates", label: "Possible duplicates" },
];

export function CaseStatusBadge({ status }: { status: CaseSummary["status"] }) {
  const tone = { open: "badge-warn", resolved: "badge-ok", closed: "" }[status];
  return <Badge tone={tone}>{status[0].toUpperCase() + status.slice(1)}</Badge>;
}

export default function CasesPage() {
  const navigate = useNavigate();
  const [form, setForm] = useState({ q: "", status: "open", category: "" });
  const [applied, setApplied] = useState(form);
  const [filter, setFilter] = useState("");
  const [page, setPage] = useState(1);
  const { data, error, isLoading } = useQuery({
    queryKey: ["cases", applied, filter, page],
    queryFn: () => api<Page<CaseSummary>>(`/cases${query({ ...applied, filter, page })}`),
    placeholderData: (prev) => prev,
  });

  return (
    <>
      <PageHead title="Cases" sub="Each customer issue with every email about it, its deadline, notes and actions." />
      <Card
        actions={
          <div className="tabs" role="tablist" aria-label="Show">
            {FILTERS.map((f) => (
              <button key={f.key} role="tab" aria-selected={filter === f.key} className={filter === f.key ? "active" : ""}
                onClick={() => { setFilter(f.key); setPage(1); }}>
                {f.label}
              </button>
            ))}
          </div>
        }
      >
        <form className="form-grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))", alignItems: "end", marginBottom: 16 }}
          onSubmit={(e) => { e.preventDefault(); setApplied(form); setPage(1); }}>
          <Field label="Search">
            <input className="input" placeholder="Case reference, email or name" value={form.q} maxLength={200}
              onChange={(e) => setForm({ ...form, q: e.target.value })} />
          </Field>
          <Field label="Status">
            <select className="select" value={form.status} onChange={(e) => setForm({ ...form, status: e.target.value })}>
              <option value="">Any</option>
              <option value="open">Open</option>
              <option value="resolved">Resolved</option>
              <option value="closed">Closed</option>
            </select>
          </Field>
          <Field label="Category">
            <select className="select" value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })}>
              <option value="">Any</option>
              {Object.entries(CATEGORY_LABEL).filter(([k]) => k !== "unclassified").map(([k, v]) => (
                <option key={k} value={k}>{v}</option>
              ))}
            </select>
          </Field>
          <button className="btn btn-primary"><Search size={16} /> Search</button>
        </form>
        <ErrorAlert error={error} />
        {isLoading && !data ? (
          <Loading />
        ) : data && data.items.length > 0 ? (
          <>
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>Case</th>
                    <th>Customer</th>
                    <th>Category</th>
                    <th>Status</th>
                    <th>Deadline</th>
                    <th>Owner</th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((c) => (
                    <tr key={c.id} className="clickable" tabIndex={0} onClick={() => navigate(`/cases/${c.id}`)}
                      onKeyDown={(ev) => ev.key === "Enter" && navigate(`/cases/${c.id}`)}>
                      <td data-label="Case">
                        <div className="cell-main">{c.reference}</div>
                        <div className="cell-sub">{c.emails} email{c.emails === 1 ? "" : "s"} · {timeAgo(c.last_activity_at)}</div>
                      </td>
                      <td data-label="Customer">
                        <div className="cell-main">{c.customer_name || c.customer_address}</div>
                        <div className="cell-sub">{c.customer_address}{c.sender_verified ? " · verified" : ""}</div>
                      </td>
                      <td data-label="Category">
                        <div>{c.category_label ?? "Not classified"}</div>
                        <div className="cell-sub">{c.department ?? ""}</div>
                      </td>
                      <td data-label="Status">
                        <div className="row" style={{ gap: 4, flexWrap: "wrap" }}>
                          <CaseStatusBadge status={c.status} />
                          {c.priority === "high" && <Badge tone="badge-danger">Urgent</Badge>}
                          {c.reopened_count > 0 && <Badge tone="badge-info">Reopened</Badge>}
                          {c.possible_duplicate_of && <Badge>Duplicate?</Badge>}
                        </div>
                      </td>
                      <td data-label="Deadline">
                        {c.sla_due_at ? (
                          <span className={c.sla_breached ? "trend-bad" : ""}>{formatDateTime(c.sla_due_at)}</span>
                        ) : "—"}
                        {c.first_response_at && <div className="cell-sub">Answered {timeAgo(c.first_response_at)}</div>}
                      </td>
                      <td data-label="Owner">{c.assigned_to?.display_name ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Pager page={page} pageSize={data.page_size} total={data.total} onPage={setPage} />
          </>
        ) : (
          <Empty title="No cases here" />
        )}
      </Card>
    </>
  );
}
