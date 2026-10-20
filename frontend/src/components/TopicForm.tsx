// The topic form. Guided by default: pick a table, which record, and write the reply by clicking
// column names; the server builds the query. "Advanced" lets a specialist write the SQL instead.
import { useMutation, useQuery } from "@tanstack/react-query";
import { Code2, FlaskConical, Plus, Trash2, UserRound, Users, X } from "lucide-react";
import { useRef, useState, type KeyboardEvent } from "react";
import { api, query } from "@/api/client";
import type { Builder, Column, Condition, DataSourceName, TopicDetail, TopicInput } from "@/api/types";
import { useAuth } from "@/auth/AuthContext";
import { Card, ErrorAlert, Field, Switch } from "@/components/ui";

export const EMPTY_TOPIC: TopicInput = {
  title: "",
  examples: [],
  data_source: "",
  builder: { table: "", columns: [], conditions: [], order_by: null, many: false },
  query: null,
  extractors: {},
  max_rows: 10,
  reply_template: "",
  row_template: null,
  require_verified_sender: true,
};

// What a condition can compare a column with, in plain words.
const SOURCES: { value: string; label: string; customer: boolean }[] = [
  { value: "fixed", label: "this exact value", customer: false },
  { value: "sender_email", label: "the customer’s email address", customer: true },
  { value: "email_value:account_number", label: "the account number in the email", customer: true },
  { value: "email_value:phone_number", label: "the phone number in the email", customer: true },
  { value: "email_value:card_last4", label: "the last 4 card digits in the email", customer: true },
  { value: "email_value:custom", label: "text in the email matching a pattern", customer: true },
];

const FORMATS = [
  { value: "", label: "as it is" },
  { value: "money", label: "as money (1,250.00)" },
  { value: "number", label: "as a number (1,250)" },
  { value: "date", label: "as a date (4 October 2026)" },
  { value: "datetime", label: "as date and time" },
  { value: "title", label: "in Title Case" },
];

function sourceKey(c: Condition): string {
  return c.source === "email_value" ? `email_value:${c.value_kind}` : c.source;
}

function fromKey(key: string): Pick<Condition, "source" | "value_kind"> {
  if (key.startsWith("email_value:")) {
    return { source: "email_value", value_kind: key.split(":")[1] as Condition["value_kind"] };
  }
  return { source: key as Condition["source"], value_kind: null };
}

function Step({ n, title, sub, children }: { n: number; title: string; sub: string; children: React.ReactNode }) {
  return (
    <section className="card">
      <div className="step-head">
        <span className="step-num">{n}</span>
        <div>
          <h2>{title}</h2>
          <p className="card-sub">{sub}</p>
        </div>
      </div>
      {children}
    </section>
  );
}

export function TopicForm({ initial, topicId, onDone }: {
  initial: TopicInput;
  topicId?: string;
  onDone: (t: TopicDetail) => void;
}) {
  const { can, me } = useAuth();
  const customerData = Boolean(me?.features.customer_data_answers);
  const [form, setForm] = useState<TopicInput>(initial);
  const [mode, setMode] = useState<"guided" | "advanced">(initial.builder || !initial.query ? "guided" : "advanced");
  const builder: Builder = form.builder ?? EMPTY_TOPIC.builder!;
  const [scope, setScope] = useState<"everyone" | "customer">(
    builder.conditions.some((c) => c.source !== "fixed") ? "customer" : "everyone",
  );
  const [example, setExample] = useState("");
  const [format, setFormat] = useState("");
  const [extractors, setExtractors] = useState(
    Object.entries(initial.extractors).map(([name, pattern]) => ({ name, pattern })),
  );
  const lastFocus = useRef<"reply" | "row">("reply");
  const replyRef = useRef<HTMLTextAreaElement>(null);
  const rowRef = useRef<HTMLInputElement>(null);

  const sources = useQuery({ queryKey: ["ds-names"], queryFn: () => api<DataSourceName[]>("/data-sources/names") });
  const tables = useQuery({
    queryKey: ["tables", form.data_source],
    queryFn: () => api<string[]>(`/data-sources/browse/tables${query({ source: form.data_source })}`),
    enabled: mode === "guided" && Boolean(form.data_source),
  });
  const columns = useQuery({
    queryKey: ["columns", form.data_source, builder.table],
    queryFn: () =>
      api<Column[]>(`/data-sources/browse/columns${query({ source: form.data_source, table: builder.table })}`),
    enabled: mode === "guided" && Boolean(form.data_source && builder.table),
  });

  const set = <K extends keyof TopicInput>(key: K, value: TopicInput[K]) => setForm((f) => ({ ...f, [key]: value }));
  const setB = (patch: Partial<Builder>) => setForm((f) => ({ ...f, builder: { ...(f.builder ?? builder), ...patch } }));

  function payload(): TopicInput {
    if (mode === "guided") {
      return { ...form, query: null, extractors: {}, row_template: builder.many ? form.row_template : null };
    }
    return {
      ...form,
      builder: null,
      extractors: Object.fromEntries(extractors.filter((x) => x.name.trim()).map((x) => [x.name.trim(), x.pattern])),
      row_template: form.row_template?.trim() ? form.row_template : null,
    };
  }

  const save = useMutation({
    mutationFn: () =>
      topicId
        ? api<TopicDetail>(`/topics/${topicId}`, { method: "PUT", json: payload() })
        : api<TopicDetail>("/topics", { json: payload() }),
    onSuccess: onDone,
  });

  // -- examples --
  const addExample = () => {
    const value = example.trim();
    if (value.length >= 3 && !form.examples.includes(value)) set("examples", [...form.examples, value]);
    setExample("");
  };
  const onExampleKey = (e: KeyboardEvent) => {
    if (e.key === "Enter") {
      e.preventDefault();
      addExample();
    }
  };

  // -- which record --
  function chooseScope(next: "everyone" | "customer") {
    setScope(next);
    if (next === "customer" && !builder.conditions.some((c) => c.source !== "fixed")) {
      setB({
        conditions: [
          { column: "", source: "email_value", value_kind: "account_number" },
          { column: "", source: "sender_email" },
        ],
      });
    }
    if (next === "everyone") setB({ conditions: builder.conditions.filter((c) => c.source === "fixed") });
  }
  const updateCondition = (i: number, patch: Partial<Condition>) =>
    setB({ conditions: builder.conditions.map((c, j) => (j === i ? { ...c, ...patch } : c)) });

  // -- reply writing --
  function insert(field: string) {
    const token = field === "rows" || !format ? `{${field}}` : `{${field}:${format}}`;
    const target = lastFocus.current === "row" && builder.many ? rowRef.current : replyRef.current;
    const key = lastFocus.current === "row" && builder.many ? "row_template" : "reply_template";
    const current = (key === "row_template" ? form.row_template : form.reply_template) ?? "";
    const start = target?.selectionStart ?? current.length;
    const end = target?.selectionEnd ?? current.length;
    const next = current.slice(0, start) + token + current.slice(end);
    set(key, next);
    requestAnimationFrame(() => {
      target?.focus();
      target?.setSelectionRange(start + token.length, start + token.length);
    });
  }

  const colNames = (columns.data ?? []).map((c) => c.name);
  const conditionSources = SOURCES.filter((s) => scope === "customer" || !s.customer);

  return (
    <form
      className="steps"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <Step n={1} title="What do customers ask?" sub="The assistant recognises the topic by comparing emails with these phrasings.">
        <div className="form-grid">
          <Field label="Topic name" className="full">
            <input className="input" required maxLength={200} value={form.title} placeholder="e.g. Account balance"
              onChange={(e) => set("title", e.target.value)} />
          </Field>
          <Field label="Ways a customer might ask" hint="Add at least three different phrasings. Press Enter to add." className="full">
            <div className="row">
              <input className="input" style={{ flex: 1, minWidth: 200 }} value={example} maxLength={500}
                placeholder="e.g. How much money is in my account?" onChange={(e) => setExample(e.target.value)} onKeyDown={onExampleKey} />
              <button type="button" className="btn btn-ghost" onClick={addExample}>
                <Plus size={16} /> Add
              </button>
            </div>
          </Field>
          {form.examples.length > 0 && (
            <div className="chips full">
              {form.examples.map((ex) => (
                <span className="chip" key={ex}>
                  {ex}
                  <button type="button" aria-label={`Remove ${ex}`} onClick={() => set("examples", form.examples.filter((x) => x !== ex))}>
                    <X size={12} />
                  </button>
                </span>
              ))}
            </div>
          )}
        </div>
      </Step>

      <Step n={2} title="Where is the answer?" sub="Choose the bank database and the table that holds it.">
        <div className="form-grid">
          <Field label="Database" hint={sources.data?.length === 0 ? "No databases yet: an administrator adds them under Data sources." : undefined}>
            <select className="select" required value={form.data_source}
              onChange={(e) => {
                set("data_source", e.target.value);
                setB({ table: "", columns: [], conditions: [], order_by: null });
              }}>
              <option value="">Choose…</option>
              {(sources.data ?? []).map((s) => (
                <option key={s.name} value={s.name}>{s.name}</option>
              ))}
            </select>
          </Field>
          {mode === "guided" && (
            <Field label="Table" hint={tables.isFetching ? "Reading tables…" : undefined}>
              <input className="input" list="table-list" required value={builder.table} disabled={!form.data_source}
                placeholder={form.data_source ? "Type to search tables" : "Choose a database first"}
                onChange={(e) => setB({ table: e.target.value, columns: [], conditions: builder.conditions.map((c) => ({ ...c, column: "" })), order_by: null })} />
              <datalist id="table-list">
                {(tables.data ?? []).map((t) => <option key={t} value={t} />)}
              </datalist>
            </Field>
          )}
          <ErrorAlert error={tables.error ?? columns.error} />
          {mode === "guided" && builder.table && columns.data && (
            <div className="field full">
              <span>Information to use in the reply</span>
              <div className="column-picks">
                {columns.data.map((c) => (
                  <label className="column-pick" key={c.name}>
                    <input type="checkbox" checked={builder.columns.includes(c.name)}
                      onChange={(e) => setB({ columns: e.target.checked ? [...builder.columns, c.name] : builder.columns.filter((x) => x !== c.name) })} />
                    <span title={c.name}>{c.name}</span>
                    <small>{c.type.toLowerCase().split("(")[0]}</small>
                  </label>
                ))}
              </div>
            </div>
          )}
        </div>
      </Step>

      {mode === "guided" ? (
        <>
          <Step n={3} title="Which record?" sub="Tell the assistant how to find the right row in that table.">
            <div className="stack" style={{ gap: 16 }}>
              <div className="choice-grid">
                <button type="button" className={`choice ${scope === "everyone" ? "active" : ""}`} onClick={() => chooseScope("everyone")}>
                  <Users size={20} />
                  <span>
                    <strong>The same answer for everyone</strong>
                    <small>e.g. branch hours, fees, how to reset a PIN</small>
                  </span>
                </button>
                {customerData ? (
                  <button type="button" className={`choice ${scope === "customer" ? "active" : ""}`} onClick={() => chooseScope("customer")}>
                    <UserRound size={20} />
                    <span>
                      <strong>The customer’s own record</strong>
                      <small>e.g. their balance or recent transactions. Only sent to verified senders.</small>
                    </span>
                  </button>
                ) : (
                  <div className="choice" aria-disabled="true" style={{ opacity: 0.6, cursor: "default" }}>
                    <UserRound size={20} />
                    <span>
                      <strong>The customer’s own record</strong>
                      <small>Coming in phase 2, after IT and security sign-off.</small>
                    </span>
                  </div>
                )}
              </div>

              {builder.conditions.map((c, i) => (
                <div className="condition" key={i}>
                  <select className="select" value={c.column} aria-label="Column" onChange={(e) => updateCondition(i, { column: e.target.value })}>
                    <option value="">Column…</option>
                    {colNames.map((n) => <option key={n} value={n}>{n}</option>)}
                  </select>
                  <span className="equals">equals</span>
                  <select className="select" value={sourceKey(c)} aria-label="Compared with"
                    onChange={(e) => updateCondition(i, { ...fromKey(e.target.value), value: null, pattern: null })}>
                    {conditionSources.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
                  </select>
                  {c.source === "fixed" ? (
                    <input className="input" placeholder="Value" value={c.value ?? ""} onChange={(e) => updateCondition(i, { value: e.target.value })} />
                  ) : c.value_kind === "custom" ? (
                    <input className="input" placeholder="Pattern, e.g. REF-(\d+)" style={{ fontFamily: "var(--mono)" }}
                      value={c.pattern ?? ""} onChange={(e) => updateCondition(i, { pattern: e.target.value })} />
                  ) : (
                    <span />
                  )}
                  <button type="button" className="icon-btn" aria-label="Remove condition"
                    onClick={() => setB({ conditions: builder.conditions.filter((_, j) => j !== i) })}>
                    <Trash2 size={16} />
                  </button>
                </div>
              ))}
              <div className="row">
                <button type="button" className="btn btn-ghost btn-sm" disabled={!builder.table}
                  onClick={() => setB({ conditions: [...builder.conditions, { column: "", source: "fixed", value: "" }] })}>
                  <Plus size={14} /> Add condition
                </button>
                {scope === "customer" && (
                  <span className="cell-sub">Tip: always include “the customer’s email address” so people only receive their own records.</span>
                )}
              </div>

              <div className="form-grid">
                <Field label="Reply with">
                  <select className="select" value={builder.many ? "many" : "one"} onChange={(e) => setB({ many: e.target.value === "many" })}>
                    <option value="one">One record</option>
                    <option value="many">A list of records</option>
                  </select>
                </Field>
                {builder.many && (
                  <Field label="Most records to list">
                    <input className="input" type="number" min={1} max={100} value={form.max_rows}
                      onChange={(e) => set("max_rows", Number(e.target.value))} />
                  </Field>
                )}
                <Field label="Newest / largest first by (optional)">
                  <select className="select" value={builder.order_by?.column ?? ""}
                    onChange={(e) => setB({ order_by: e.target.value ? { column: e.target.value, direction: "desc" } : null })}>
                    <option value="">No particular order</option>
                    {colNames.map((n) => <option key={n} value={n}>{n}</option>)}
                  </select>
                </Field>
              </div>
            </div>
          </Step>

          <Step n={4} title="Write the reply" sub="Click a field to insert it where your cursor is. The greeting and sign-off are added from Settings.">
            <div className="stack">
              <div className="row">
                <span className="cell-sub">Insert</span>
                <select className="select" style={{ width: "auto" }} value={format} aria-label="Format" onChange={(e) => setFormat(e.target.value)}>
                  {FORMATS.map((f) => <option key={f.value} value={f.value}>{f.label}</option>)}
                </select>
              </div>
              <div className="insert-chips">
                {builder.columns.map((c) => (
                  <button type="button" key={c} className="insert-chip" onClick={() => insert(c)}>{c}</button>
                ))}
                <button type="button" className="insert-chip" onClick={() => insert("customer_name")}>customer_name</button>
                <button type="button" className="insert-chip" onClick={() => insert("reference")}>reference</button>
                {builder.many && <button type="button" className="insert-chip" onClick={() => insert("rows")}>rows (the list)</button>}
              </div>
              <Field label="Reply">
                <textarea ref={replyRef} className="textarea" rows={5} required value={form.reply_template}
                  onFocus={() => (lastFocus.current = "reply")} onChange={(e) => set("reply_template", e.target.value)}
                  placeholder="e.g. The available balance on your {product} account is {balance:money}." />
              </Field>
              {builder.many && (
                <Field label="Each line of the list" hint="Repeated once per record, where {rows} appears in the reply.">
                  <input ref={rowRef} className="input" value={form.row_template ?? ""} onFocus={() => (lastFocus.current = "row")}
                    onChange={(e) => set("row_template", e.target.value)} placeholder="e.g. - {posted_on:date}: {description}, {amount:money}" />
                </Field>
              )}
            </div>
          </Step>
        </>
      ) : (
        <AdvancedFields form={form} set={set} extractors={extractors} setExtractors={setExtractors} />
      )}

      {can("test:run-queries") && <TryIt payload={payload} />}

      <ErrorAlert error={save.error} />
      <div className="row">
        <button type="button" className="link-btn" onClick={() => setMode(mode === "guided" ? "advanced" : "guided")}>
          <Code2 size={14} style={{ verticalAlign: -2 }} />{" "}
          {mode === "guided" ? "Write a SQL query instead (advanced)" : "Back to the guided form"}
        </button>
        <span className="spacer" />
        <button className="btn btn-primary" disabled={save.isPending || form.examples.length === 0}>
          {save.isPending ? "Submitting…" : "Submit for approval"}
        </button>
      </div>
    </form>
  );
}

function TryIt({ payload }: { payload: () => TopicInput }) {
  const [sample, setSample] = useState({ subject: "", body: "", sender_email: "", sender_name: "", verified: true });
  const run = useMutation({
    mutationFn: () => api<{ text: string | null; reason: string }>("/topics/try", { json: { topic: payload(), sample } }),
  });
  return (
    <Step n={5} title="Try it" sub="Check the reply with a sample email before submitting. Nothing is saved or sent.">
      <div className="form-grid">
        <Field label="Customer’s email address">
          <input className="input" type="email" value={sample.sender_email} onChange={(e) => setSample({ ...sample, sender_email: e.target.value })} />
        </Field>
        <Field label="Customer’s name">
          <input className="input" value={sample.sender_name} onChange={(e) => setSample({ ...sample, sender_name: e.target.value })} />
        </Field>
        <Field label="Their message" className="full">
          <textarea className="textarea" rows={3} value={sample.body} placeholder="e.g. What is my balance? My account is 6200123456"
            onChange={(e) => setSample({ ...sample, body: e.target.value })} />
        </Field>
        <div className="row full">
          <button type="button" className="btn btn-ghost" disabled={run.isPending} onClick={() => run.mutate()}>
            <FlaskConical size={16} /> {run.isPending ? "Reading the bank’s data…" : "Try it"}
          </button>
          <span className="cell-sub">Recorded in the audit trail.</span>
        </div>
      </div>
      <div style={{ marginTop: 12 }}>
        <ErrorAlert error={run.error} />
        {run.data &&
          (run.data.text ? <div className="mail-body">{run.data.text}</div> : <div className="alert alert-warn">{run.data.reason}</div>)}
      </div>
    </Step>
  );
}

function AdvancedFields({ form, set, extractors, setExtractors }: {
  form: TopicInput;
  set: <K extends keyof TopicInput>(key: K, value: TopicInput[K]) => void;
  extractors: { name: string; pattern: string }[];
  setExtractors: React.Dispatch<React.SetStateAction<{ name: string; pattern: string }[]>>;
}) {
  const builtins = useQuery({ queryKey: ["parameters"], queryFn: () => api<string[]>("/topics/parameters") });
  const params = [...(builtins.data ?? []), ...extractors.map((x) => x.name).filter(Boolean)];
  return (
    <Card title="Advanced: write the query" sub="For specialists. A single read-only SELECT; values from the email are always passed safely as :parameters.">
      <div className="form-grid">
        <div className="field full">
          <span>Values taken from the email</span>
          <small>Each becomes a <code>:name</code> parameter. The first bracketed group of the pattern is used.</small>
          {extractors.map((x, i) => (
            <div className="row" key={i}>
              <input className="input" style={{ flex: "1 1 160px" }} placeholder="name" value={x.name}
                onChange={(e) => setExtractors((all) => all.map((y, j) => (j === i ? { ...y, name: e.target.value } : y)))} />
              <input className="input" style={{ flex: "2 1 220px", fontFamily: "var(--mono)" }} placeholder="pattern" value={x.pattern}
                onChange={(e) => setExtractors((all) => all.map((y, j) => (j === i ? { ...y, pattern: e.target.value } : y)))} />
              <button type="button" className="icon-btn" aria-label="Remove" onClick={() => setExtractors((all) => all.filter((_, j) => j !== i))}>
                <Trash2 size={16} />
              </button>
            </div>
          ))}
          <div>
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => setExtractors((all) => [...all, { name: "", pattern: "" }])}>
              <Plus size={14} /> Add value
            </button>
          </div>
        </div>
        <Field label="Query" className="full" hint={<>Parameters: {params.map((p) => <code key={p}>:{p} </code>)}</>}>
          <textarea className="textarea code" rows={6} required value={form.query ?? ""} onChange={(e) => set("query", e.target.value)} />
        </Field>
        <Field label="Reply" className="full" hint="Use {column}, {column:money}, {customer_name}, {reference}, and {rows} for a list.">
          <textarea className="textarea" rows={4} required value={form.reply_template} onChange={(e) => set("reply_template", e.target.value)} />
        </Field>
        <Field label="Each line of the list (only with {rows})" className="full">
          <input className="input" value={form.row_template ?? ""} onChange={(e) => set("row_template", e.target.value)} />
        </Field>
        <Field label="Most records to read">
          <input className="input" type="number" min={1} max={100} value={form.max_rows} onChange={(e) => set("max_rows", Number(e.target.value))} />
        </Field>
        <div className="full">
          <Switch checked={form.require_verified_sender} onChange={(v) => set("require_verified_sender", v)}
            label="Only answer verified senders (keep on for any customer-specific data)" />
        </div>
      </div>
    </Card>
  );
}
