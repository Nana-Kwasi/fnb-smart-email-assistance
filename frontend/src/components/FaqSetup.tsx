// One-time setup: where the bank keeps its general answers. Topics then just pick an entry.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BookOpen, Save } from "lucide-react";
import { useEffect, useState } from "react";
import { api, query } from "@/api/client";
import type { Column, DataSource, FaqConfig } from "@/api/types";
import { Badge, Card, ErrorAlert, Field } from "@/components/ui";

const BLANK: FaqConfig = { data_source: "", table: "", key_column: "", question_column: null, answer_column: "" };

export function FaqSetup({ sources }: { sources: DataSource[] }) {
  const queryClient = useQueryClient();
  const current = useQuery({
    queryKey: ["faq-config"],
    queryFn: () => api<{ configured: boolean; config: FaqConfig | null }>("/faq/config"),
  });
  const [f, setF] = useState<FaqConfig>(BLANK);
  useEffect(() => {
    if (current.data?.config) setF(current.data.config);
  }, [current.data]);

  const tables = useQuery({
    queryKey: ["tables", f.data_source],
    queryFn: () => api<string[]>(`/data-sources/browse/tables${query({ source: f.data_source })}`),
    enabled: Boolean(f.data_source),
  });
  const columns = useQuery({
    queryKey: ["columns", f.data_source, f.table],
    queryFn: () => api<Column[]>(`/data-sources/browse/columns${query({ source: f.data_source, table: f.table })}`),
    enabled: Boolean(f.data_source && f.table),
  });
  const save = useMutation({
    mutationFn: () => api("/faq/config", { method: "PUT", json: f }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["faq-config"] });
      void queryClient.invalidateQueries({ queryKey: ["faq-entries"] });
    },
  });
  const cols = (columns.data ?? []).map((c) => c.name);
  const colSelect = (key: "key_column" | "question_column" | "answer_column", optional = false) => (
    <select className="select" value={f[key] ?? ""} disabled={!cols.length} required={!optional}
      onChange={(e) => setF({ ...f, [key]: e.target.value || (optional ? null : "") })}>
      <option value="">{optional ? "None" : "Choose…"}</option>
      {cols.map((c) => <option key={c} value={c}>{c}</option>)}
    </select>
  );

  return (
    <Card
      title={<span className="row" style={{ gap: 8 }}><BookOpen size={18} color="var(--fnb-teal)" /> Where is the FAQ?</span>}
      sub="Point to the table that holds the bank’s general answers. Topics are then created by simply picking an answer from it."
      actions={current.data?.configured ? <Badge tone="badge-ok">Set up</Badge> : <Badge tone="badge-warn">Not set up</Badge>}
    >
      <form
        className="stack"
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate();
        }}
      >
        <div className="form-grid">
          <Field label="Database">
            <select className="select" required value={f.data_source}
              onChange={(e) => setF({ ...BLANK, data_source: e.target.value })}>
              <option value="">Choose…</option>
              {sources.filter((s) => s.is_active).map((s) => <option key={s.id} value={s.name}>{s.name}</option>)}
            </select>
          </Field>
          <Field label="FAQ table">
            <select className="select" required value={f.table} disabled={!tables.data}
              onChange={(e) => setF({ ...f, table: e.target.value, key_column: "", question_column: null, answer_column: "" })}>
              <option value="">Choose…</option>
              {(tables.data ?? []).map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </Field>
          <Field label="Column with the answer text">{colSelect("answer_column")}</Field>
          <Field label="Column with the question (optional)" hint="Shown when picking answers">{colSelect("question_column", true)}</Field>
          <Field label="Column that identifies each entry" hint="e.g. an ID or code that never changes">{colSelect("key_column")}</Field>
        </div>
        <ErrorAlert error={tables.error ?? columns.error ?? save.error} />
        {save.isSuccess && <div className="alert alert-ok">Saved. Topics can now be created from the FAQ.</div>}
        <div className="row">
          <span className="spacer" />
          <button className="btn btn-primary" disabled={save.isPending || !f.answer_column || !f.key_column}>
            <Save size={16} /> {save.isPending ? "Checking…" : "Save"}
          </button>
        </div>
      </form>
    </Card>
  );
}
