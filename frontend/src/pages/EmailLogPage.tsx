import { useQuery } from "@tanstack/react-query";
import { Search } from "lucide-react";
import { useState } from "react";
import { api, query } from "@/api/client";
import type { EmailStatus, EmailSummary, Page } from "@/api/types";
import { EmailTable } from "@/components/EmailTable";
import { ExportButtons } from "@/components/ExportButtons";
import { Card, Empty, ErrorAlert, Field, Loading, PageHead, Pager } from "@/components/ui";
import { STATUS_LABEL } from "@/lib/format";

export default function EmailLogPage() {
  const [filters, setFilters] = useState({ q: "", status: "", date_from: "", date_to: "" });
  const [applied, setApplied] = useState(filters);
  const [page, setPage] = useState(1);

  const { data, error, isLoading } = useQuery({
    queryKey: ["email-log", applied, page],
    queryFn: () => api<Page<EmailSummary>>(`/emails${query({ ...applied, page })}`),
  });

  const set = (key: keyof typeof filters) => (e: { target: { value: string } }) =>
    setFilters((f) => ({ ...f, [key]: e.target.value }));

  return (
    <>
      <PageHead
        title="Email Log"
        sub="Every email received and every reply sent."
        actions={<ExportButtons params={{ scope: "log", ...applied }} />}
      />
      <Card>
        <form
          className="form-grid"
          style={{ gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))", alignItems: "end", marginBottom: 16 }}
          onSubmit={(e) => {
            e.preventDefault();
            setApplied(filters);
            setPage(1);
          }}
        >
          <Field label="Search">
            <input className="input" placeholder="Reference, email, name or subject" value={filters.q} onChange={set("q")} maxLength={200} />
          </Field>
          <Field label="Status">
            <select className="select" value={filters.status} onChange={set("status")}>
              <option value="">Any</option>
              {(Object.keys(STATUS_LABEL) as EmailStatus[]).map((s) => (
                <option key={s} value={s}>
                  {STATUS_LABEL[s]}
                </option>
              ))}
            </select>
          </Field>
          <Field label="From date">
            <input className="input" type="date" value={filters.date_from} onChange={set("date_from")} />
          </Field>
          <Field label="To date">
            <input className="input" type="date" value={filters.date_to} onChange={set("date_to")} />
          </Field>
          <button className="btn btn-primary">
            <Search size={16} /> Search
          </button>
        </form>
        <ErrorAlert error={error} />
        {isLoading ? (
          <Loading />
        ) : data && data.items.length > 0 ? (
          <>
            <EmailTable emails={data.items} />
            <Pager page={page} pageSize={data.page_size} total={data.total} onPage={setPage} />
          </>
        ) : (
          <Empty title="No emails found" />
        )}
      </Card>
    </>
  );
}
