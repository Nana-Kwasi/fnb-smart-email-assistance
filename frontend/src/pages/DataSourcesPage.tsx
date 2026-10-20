import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Database, KeyRound, Pencil, Plus, RefreshCw, Trash2 } from "lucide-react";
import { useState } from "react";
import { api } from "@/api/client";
import { useAuth } from "@/auth/AuthContext";
import { FaqSetup } from "@/components/FaqSetup";
import type { DataSource } from "@/api/types";
import { Badge, Card, Dialog, Empty, ErrorAlert, Field, Loading, PageHead, Switch } from "@/components/ui";
import { formatDateTime } from "@/lib/format";

const KINDS: { value: DataSource["kind"]; label: string; port: number | null; dbLabel: string }[] = [
  { value: "oracle", label: "Oracle", port: 1521, dbLabel: "Service name" },
  { value: "sqlserver", label: "Microsoft SQL Server", port: 1433, dbLabel: "Database" },
  { value: "postgresql", label: "PostgreSQL", port: 5432, dbLabel: "Database" },
  { value: "mysql", label: "MySQL / MariaDB", port: 3306, dbLabel: "Database" },
  { value: "sqlite", label: "SQLite file (testing only)", port: null, dbLabel: "File path" },
];

interface FormState {
  name: string;
  kind: DataSource["kind"];
  host: string;
  port: string;
  database: string;
  db_schema: string;
  username: string;
  password: string;
  is_active: boolean;
}

const BLANK: FormState = {
  name: "", kind: "oracle", host: "", port: "", database: "", db_schema: "", username: "", password: "", is_active: true,
};

function SourceForm({ existing, onClose }: { existing: DataSource | null; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [f, setF] = useState<FormState>(
    existing
      ? { ...BLANK, ...existing, port: existing.port ? String(existing.port) : "", db_schema: existing.db_schema ?? "", password: "" }
      : BLANK,
  );
  const kind = KINDS.find((k) => k.value === f.kind)!;
  const save = useMutation({
    mutationFn: () => {
      const body = {
        ...f,
        port: f.port ? Number(f.port) : null,
        db_schema: f.db_schema || null,
        password: f.password || null,
      };
      return existing
        ? api<DataSource>(`/data-sources/${existing.id}`, { method: "PUT", json: body })
        : api<DataSource>("/data-sources", { json: body });
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["data-sources"] });
      void queryClient.invalidateQueries({ queryKey: ["ds-names"] });
      onClose();
    },
  });
  const set = (key: keyof FormState) => (e: { target: { value: string } }) => setF({ ...f, [key]: e.target.value });

  return (
    <Dialog title={existing ? `Edit ${existing.name}` : "Add a bank database"} onClose={onClose}>
      <form
        className="stack"
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate();
        }}
      >
        <div className="form-grid">
          <Field label="Name" hint="How topics refer to it, e.g. core_banking">
            <input className="input" required pattern="[A-Za-z0-9_ \-]{2,64}" value={f.name} onChange={set("name")} />
          </Field>
          <Field label="Type">
            <select className="select" value={f.kind} onChange={set("kind")}>
              {KINDS.map((k) => <option key={k.value} value={k.value}>{k.label}</option>)}
            </select>
          </Field>
          {f.kind !== "sqlite" && (
            <>
              <Field label="Server address">
                <input className="input" required value={f.host} onChange={set("host")} placeholder="db01.fnb.local" />
              </Field>
              <Field label="Port">
                <input className="input" inputMode="numeric" value={f.port} onChange={set("port")} placeholder={String(kind.port ?? "")} />
              </Field>
            </>
          )}
          <Field label={kind.dbLabel}>
            <input className="input" required value={f.database} onChange={set("database")} />
          </Field>
          {f.kind !== "sqlite" && (
            <>
              <Field label="Schema (optional)" hint="Where the tables live, if not the account’s default">
                <input className="input" value={f.db_schema} onChange={set("db_schema")} />
              </Field>
              <Field label="Username" hint="Use an account that can only read (SELECT)">
                <input className="input" autoComplete="off" value={f.username} onChange={set("username")} />
              </Field>
              <Field label="Password" hint={existing?.has_password ? "Leave empty to keep the saved password" : "Stored encrypted; never shown again"}>
                <input className="input" type="password" autoComplete="new-password" value={f.password} onChange={set("password")} />
              </Field>
            </>
          )}
        </div>
        <Switch checked={f.is_active} onChange={(v) => setF({ ...f, is_active: v })} label="Available to topics" />
        <ErrorAlert error={save.error} />
        <div className="row">
          <span className="cell-sub">The connection is tested when you save.</span>
          <span className="spacer" />
          <button type="button" className="btn btn-ghost" onClick={onClose}>Cancel</button>
          <button className="btn btn-primary" disabled={save.isPending}>{save.isPending ? "Saving and testing…" : "Save"}</button>
        </div>
      </form>
    </Dialog>
  );
}

export default function DataSourcesPage() {
  const queryClient = useQueryClient();
  const { me } = useAuth();
  const [editing, setEditing] = useState<DataSource | "new" | null>(null);
  const [confirmRemove, setConfirmRemove] = useState<string | null>(null);
  const { data, error, isLoading } = useQuery({ queryKey: ["data-sources"], queryFn: () => api<DataSource[]>("/data-sources") });
  const refresh = () => void queryClient.invalidateQueries({ queryKey: ["data-sources"] });
  const check = useMutation({ mutationFn: (id: string) => api<DataSource>(`/data-sources/${id}/check`, { method: "POST" }), onSuccess: refresh });
  const remove = useMutation({ mutationFn: (id: string) => api(`/data-sources/${id}`, { method: "DELETE" }), onSuccess: refresh });
  const limit = me?.features.max_data_sources ?? null;
  const atLimit = limit !== null && (data?.length ?? 0) >= limit;

  return (
    <>
      <PageHead
        title="Data sources"
        sub="The bank databases the assistant reads answers from. Passwords are stored encrypted and never shown."
        actions={
          !atLimit && (
            <button className="btn btn-primary" onClick={() => setEditing("new")}>
              <Plus size={16} /> Add database
            </button>
          )
        }
      />
      {limit !== null && (
        <div className="alert" role="note">
          Phase 1: answers come from {limit === 1 ? "one database" : `up to ${limit} databases`} and are the same for
          every customer (e.g. fees, hours, how-tos). Answers from customers’ own records come in phase 2.
        </div>
      )}
      <ErrorAlert error={error ?? check.error ?? remove.error} />
      {isLoading ? (
        <Loading />
      ) : !data?.length ? (
        <Card>
          <Empty title="No databases yet">Add the bank database that holds the answers customers ask for.</Empty>
        </Card>
      ) : (
        <div className="grid grid-2">
          {data.map((d) => (
            <Card
              key={d.id}
              title={
                <span className="row" style={{ gap: 8 }}>
                  <Database size={18} color="var(--fnb-teal)" /> {d.name}
                </span>
              }
              sub={KINDS.find((k) => k.value === d.kind)?.label}
              actions={
                d.last_check_ok === null ? <Badge>Not tested</Badge> : d.last_check_ok ? <Badge tone="badge-ok">Connected</Badge> : <Badge tone="badge-danger">Not connecting</Badge>
              }
            >
              <dl className="meta">
                {d.kind !== "sqlite" && (
                  <>
                    <dt>Server</dt>
                    <dd>{d.host}{d.port ? `:${d.port}` : ""}</dd>
                  </>
                )}
                <dt>{KINDS.find((k) => k.value === d.kind)?.dbLabel}</dt>
                <dd>{d.database}{d.db_schema ? ` · schema ${d.db_schema}` : ""}</dd>
                {d.kind !== "sqlite" && (
                  <>
                    <dt>Account</dt>
                    <dd className="row" style={{ gap: 6 }}>
                      {d.username || "—"} {d.has_password && <KeyRound size={14} aria-label="Password saved" />}
                    </dd>
                  </>
                )}
                <dt>Last checked</dt>
                <dd>{formatDateTime(d.last_check_at)}</dd>
              </dl>
              {d.last_error && <div className="alert alert-danger" style={{ marginTop: 12 }}>{d.last_error}</div>}
              {!d.is_active && <div className="alert" style={{ marginTop: 12 }}>Switched off: topics cannot use it.</div>}
              <div className="row" style={{ marginTop: 16 }}>
                <button className="btn btn-ghost btn-sm" disabled={check.isPending} onClick={() => check.mutate(d.id)}>
                  <RefreshCw size={14} /> Test connection
                </button>
                <button className="btn btn-ghost btn-sm" onClick={() => setEditing(d)}>
                  <Pencil size={14} /> Edit
                </button>
                <span className="spacer" />
                <button className="btn btn-danger btn-sm" disabled={remove.isPending}
                  title="Only possible when no topic uses it"
                  onClick={() => (confirmRemove === d.id ? remove.mutate(d.id) : setConfirmRemove(d.id))}
                  onBlur={() => setConfirmRemove(null)}>
                  <Trash2 size={14} /> {confirmRemove === d.id ? "Click again to remove" : "Remove"}
                </button>
              </div>
            </Card>
          ))}
        </div>
      )}
      {data && data.length > 0 && <FaqSetup sources={data} />}
      {editing && <SourceForm existing={editing === "new" ? null : editing} onClose={() => setEditing(null)} />}
    </>
  );
}
