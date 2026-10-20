import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Save, X } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { api } from "@/api/client";
import type { AppSettings } from "@/api/types";
import { useAuth } from "@/auth/AuthContext";
import { Badge, Card, ErrorAlert, Field, Loading, PageHead, Switch } from "@/components/ui";
import { formatDateTime } from "@/lib/format";

type Editable = Omit<AppSettings, "updated_at" | "mailbox" | "graph_configured" | "internal_domains">;
type Tab = "automation" | "knowledge" | "escalation" | "wording" | "security";

const TABS: { key: Tab; label: string }[] = [
  { key: "automation", label: "Automation" },
  { key: "knowledge", label: "Knowledge base" },
  { key: "escalation", label: "Escalation" },
  { key: "wording", label: "Email wording" },
  { key: "security", label: "Security & data" },
];

/** A whole number with a unit, e.g. "15 MB". */
function NumberField({ label, hint, value, min, max, unit, onChange }: {
  label: string;
  hint?: ReactNode;
  value: number;
  min: number;
  max: number;
  unit: string;
  onChange: (v: number) => void;
}) {
  return (
    <Field label={label} hint={hint}>
      <div className="row" style={{ gap: 8, flexWrap: "nowrap" }}>
        <input className="input" type="number" style={{ width: 120 }} min={min} max={max} value={value}
          onChange={(e) => onChange(Math.max(min, Math.min(max, Number(e.target.value) || min)))} />
        <span className="cell-sub">{unit}</span>
      </div>
    </Field>
  );
}

/** Both levels on one 0–100% scale, with a band showing what happens at each confidence. */
function Levels({ auto, suggest, onAuto, onSuggest }: {
  auto: number;
  suggest: number;
  onAuto: (v: number) => void;
  onSuggest: (v: number) => void;
}) {
  const a = Math.round(auto * 100);
  const s = Math.round(suggest * 100);
  return (
    <div className="stack" style={{ gap: 14 }}>
      <div className="levels-band" aria-hidden>
        <span className="band-person" style={{ width: `${s}%` }}>{s >= 12 ? "Person" : ""}</span>
        <span className="band-suggest" style={{ width: `${a - s}%` }}>{a - s >= 14 ? "Suggested to staff" : ""}</span>
        <span className="band-auto" style={{ width: `${100 - a}%` }}>{100 - a >= 12 ? "Automatic" : ""}</span>
      </div>
      <div className="legend">
        <span><i className="swatch band-person" /> below {s}%: a person answers, nothing suggested</span>
        <span><i className="swatch band-suggest" /> {s}–{a - 1}%: answer suggested to staff</span>
        <span><i className="swatch band-auto" /> {a}% and above: sent automatically</span>
      </div>
      <Field label={`Answer automatically when at least ${a}% sure`}>
        <input type="range" min={30} max={99} step={1} value={a}
          onChange={(e) => {
            const v = Number(e.target.value);
            onAuto(v / 100);
            if (s > v) onSuggest(v / 100); // the suggest level can never be above the automatic one
          }} />
      </Field>
      <Field label={`Suggest an answer to staff when at least ${s}% sure`}>
        <input type="range" min={20} max={99} step={1} value={s}
          onChange={(e) => onSuggest(Math.min(Number(e.target.value), a) / 100)} />
      </Field>
      <p className="cell-sub">Find the right levels with “Try a customer question” on the Knowledge base page.</p>
    </div>
  );
}

function Chips({ items, onRemove, readOnly, empty }: { items: string[]; onRemove: (v: string) => void; readOnly: boolean; empty: string }) {
  return (
    <div className="chips">
      {items.length === 0 && <span className="cell-sub">{empty}</span>}
      {items.map((v) => (
        <span className="chip" key={v}>
          {v}
          {!readOnly && (
            <button type="button" aria-label={`Remove ${v}`} onClick={() => onRemove(v)}>
              <X size={12} />
            </button>
          )}
        </span>
      ))}
    </div>
  );
}

function AddRow({ placeholder, type = "text", onAdd }: { placeholder: string; type?: string; onAdd: (v: string) => void }) {
  const [value, setValue] = useState("");
  const add = () => {
    if (value.trim()) onAdd(value.trim());
    setValue("");
  };
  return (
    <div className="row">
      <input className="input" type={type} style={{ flex: 1, minWidth: 200 }} value={value} placeholder={placeholder}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            add();
          }
        }} />
      <button type="button" className="btn btn-ghost" onClick={add}>
        <Plus size={16} /> Add
      </button>
    </div>
  );
}

export default function SettingsPage() {
  const { can, reload } = useAuth();
  const readOnly = !can("settings:edit");
  const queryClient = useQueryClient();
  const [tab, setTab] = useState<Tab>("automation");
  const { data, error, isLoading } = useQuery({ queryKey: ["settings"], queryFn: () => api<AppSettings>("/settings") });
  const [form, setForm] = useState<Editable | null>(null);

  useEffect(() => {
    if (data) {
      const { updated_at: _u, mailbox: _m, graph_configured: _g, internal_domains: _d, ...rest } = data;
      setForm(rest);
    }
  }, [data]);

  const save = useMutation({
    mutationFn: () => api<AppSettings>("/settings", { method: "PUT", json: form }),
    onSuccess: (saved) => {
      queryClient.setQueryData(["settings"], saved);
      void queryClient.invalidateQueries({ queryKey: ["overview"] });
      void reload(); // upload limit and second-person check are shown elsewhere in the app
    },
  });

  if (isLoading || !form || !data) return isLoading ? <Loading /> : <ErrorAlert error={error} />;
  const set = <K extends keyof Editable>(key: K, value: Editable[K]) => setForm((f) => (f ? { ...f, [key]: value } : f));
  const domains = data.internal_domains.length ? data.internal_domains.map((d) => "@" + d).join(", ") : null;

  return (
    <>
      <PageHead
        title="Settings"
        sub={`Last changed ${formatDateTime(data.updated_at)}`}
        actions={
          !readOnly && (
            <button className="btn btn-primary" disabled={save.isPending} onClick={() => save.mutate()}>
              <Save size={16} /> {save.isPending ? "Saving…" : "Save changes"}
            </button>
          )
        }
      />
      {readOnly && <div className="alert">You can view these settings. Only administrators can change them.</div>}
      <ErrorAlert error={save.error} />
      {save.isSuccess && <div className="alert alert-ok">Settings saved.</div>}

      <div className="tabs" role="tablist" aria-label="Settings sections" style={{ alignSelf: "flex-start" }}>
        {TABS.map((t) => (
          <button key={t.key} role="tab" aria-selected={tab === t.key} className={tab === t.key ? "active" : ""} onClick={() => setTab(t.key)}>
            {t.label}
          </button>
        ))}
      </div>

      <fieldset disabled={readOnly} style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }} className="stack">
        {tab === "automation" && (
          <>
            <Card title="Main switches" sub="Turn automatic answers off to stop them immediately.">
              <div className="stack">
                <Switch checked={form.ack_enabled} onChange={(v) => set("ack_enabled", v)} label="Send an acknowledgement to every customer email" />
                <Switch checked={form.auto_reply_enabled} onChange={(v) => set("auto_reply_enabled", v)}
                  label="Answer automatically from the knowledge base" />
              </div>
            </Card>
            <Card title="How sure the assistant must be">
              <Levels auto={form.confidence_threshold} suggest={form.suggest_threshold}
                onAuto={(v) => set("confidence_threshold", v)} onSuggest={(v) => set("suggest_threshold", v)} />
            </Card>
            <Card title="Safety limits">
              <div className="form-grid">
                <NumberField label="Longest answer sent automatically" unit="characters" min={200} max={10000}
                  value={form.max_auto_answer_chars} onChange={(v) => set("max_auto_answer_chars", v)}
                  hint="Longer answers are only ever suggested to staff." />
              </div>
            </Card>
            <Card title="Sensitive words" sub="Emails with these always go to a person as urgent, and are never answered automatically.">
              <div className="stack">
                {!readOnly && (
                  <AddRow placeholder="Add a word or phrase"
                    onAdd={(v) => !form.sensitive_keywords.includes(v.toLowerCase()) && set("sensitive_keywords", [...form.sensitive_keywords, v.toLowerCase()])} />
                )}
                <Chips items={form.sensitive_keywords} readOnly={readOnly} empty="No sensitive words."
                  onRemove={(v) => set("sensitive_keywords", form.sensitive_keywords.filter((x) => x !== v))} />
              </div>
            </Card>
          </>
        )}

        {tab === "knowledge" && (
          <>
            <Card title="Categories" sub="Which kinds of answers may be sent by themselves. “Suggest only” answers always go to a person first.">
              <div className="stack">
                {form.knowledge_categories.map((c, i) => (
                  <div className="row" key={i}>
                    <input className="input" style={{ flex: 1, minWidth: 160 }} value={c.name} maxLength={60} aria-label="Category name"
                      onChange={(e) => set("knowledge_categories", form.knowledge_categories.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))} />
                    <div className="tabs" role="radiogroup" aria-label={`${c.name} answers`}>
                      {[true, false].map((auto) => (
                        <button type="button" key={String(auto)} role="radio" aria-checked={c.auto === auto} className={c.auto === auto ? "active" : ""}
                          onClick={() => set("knowledge_categories", form.knowledge_categories.map((x, j) => (j === i ? { ...x, auto } : x)))}>
                          {auto ? "Automatic" : "Suggest only"}
                        </button>
                      ))}
                    </div>
                    {!readOnly && (
                      <button type="button" className="icon-btn" aria-label={`Remove ${c.name}`}
                        onClick={() => set("knowledge_categories", form.knowledge_categories.filter((_, j) => j !== i))}>
                        <X size={16} />
                      </button>
                    )}
                  </div>
                ))}
                {!readOnly && (
                  <div>
                    <button type="button" className="btn btn-ghost btn-sm"
                      onClick={() => set("knowledge_categories", [...form.knowledge_categories, { name: "", auto: false }])}>
                      <Plus size={14} /> Add category
                    </button>
                  </div>
                )}
              </div>
            </Card>
            <Card title="Uploads">
              <div className="stack">
                <div className="form-grid">
                  <NumberField label="Largest file allowed" unit="MB" min={1} max={50} value={form.max_upload_mb}
                    onChange={(v) => set("max_upload_mb", v)} />
                </div>
                <Switch checked={form.require_second_person} onChange={(v) => set("require_second_person", v)}
                  label="Someone other than the uploader must publish a document (second-person check)" />
              </div>
            </Card>
          </>
        )}

        {tab === "escalation" && (
          <Card title="Who is told, and when" sub="When the assistant cannot answer, the person in charge is told. Not for every email: only what needs them.">
            <div className="stack" style={{ gap: 16 }}>
              <Field label="Person in charge" hint={domains ? `Internal addresses only (${domains}).` : "Internal addresses only."}>
                <input className="input" type="email" value={form.escalation_to ?? ""} placeholder="customercare.lead@fnb…"
                  onChange={(e) => set("escalation_to", e.target.value || null)} />
              </Field>
              <Field label="Also copy">
                {!readOnly && (
                  <AddRow placeholder="colleague@fnb…" type="email"
                    onAdd={(v) => v.includes("@") && !form.notify_recipients.includes(v.toLowerCase()) && set("notify_recipients", [...form.notify_recipients, v.toLowerCase()])} />
                )}
              </Field>
              <Chips items={form.notify_recipients} readOnly={readOnly} empty="Nobody copied."
                onRemove={(v) => set("notify_recipients", form.notify_recipients.filter((x) => x !== v))} />
              <Switch checked={form.escalation_copy_admins} onChange={(v) => set("escalation_copy_admins", v)} label="Always copy every administrator" />
              <hr className="divider" />
              <Switch checked={form.notify_urgent} onChange={(v) => set("notify_urgent", v)} label="Urgent emails (sensitive words): alert straight away" />
              <div className="row">
                <Switch checked={form.notify_overdue} onChange={(v) => set("notify_overdue", v)} label="Remind when nobody has replied after" />
                <input className="input" type="number" min={1} max={72} style={{ width: 90 }} value={form.overdue_after_hours} aria-label="Hours"
                  onChange={(e) => set("overdue_after_hours", Math.max(1, Math.min(72, Number(e.target.value) || 1)))} />
                <span className="cell-sub">hours</span>
              </div>
              <div className="row">
                <Switch checked={form.notify_digest} onChange={(v) => set("notify_digest", v)} label="Daily summary at" />
                <input className="input" type="time" style={{ width: 130 }} value={form.digest_time} aria-label="Time"
                  onChange={(e) => set("digest_time", e.target.value)} />
              </div>
              <Switch checked={form.notify_system} onChange={(v) => set("notify_system", v)} label="System problems (mailbox or a database not reachable)" />
            </div>
          </Card>
        )}

        {tab === "wording" && (
          <>
            <Card title="Email wording" sub="Use {customer_name} and {reference} anywhere in these texts.">
              <div className="form-grid">
                <Field label="Acknowledgement email" className="full">
                  <textarea className="textarea" rows={8} value={form.ack_template} onChange={(e) => set("ack_template", e.target.value)} />
                </Field>
                <Field label="Before every answer (greeting)">
                  <textarea className="textarea" rows={4} value={form.answer_header} onChange={(e) => set("answer_header", e.target.value)} />
                </Field>
                <Field label="After every answer (sign-off)">
                  <textarea className="textarea" rows={4} value={form.answer_footer} onChange={(e) => set("answer_footer", e.target.value)} />
                </Field>
              </div>
            </Card>
            <Card title="Replies in the same thread">
              <div className="form-grid">
                <NumberField label="Don’t acknowledge again within" unit="days" min={0} max={30} value={form.follow_up_days}
                  onChange={(v) => set("follow_up_days", v)} hint="A customer replying in the same thread within this time gets no second acknowledgement. 0 = always acknowledge." />
              </div>
            </Card>
          </>
        )}

        {tab === "security" && (
          <>
            <Card title="Security & data">
              <div className="form-grid">
                <NumberField label="Sign people out after" unit="minutes idle" min={5} max={480} value={form.session_idle_minutes}
                  onChange={(v) => set("session_idle_minutes", v)} />
                <NumberField label="Keep customer emails for" unit="months (0 = forever)" min={0} max={120} value={form.retention_months}
                  onChange={(v) => set("retention_months", v)} hint="Older emails and their replies are deleted once a day. The audit log is kept." />
                <NumberField label="Rows per page in lists" unit="rows" min={5} max={100} value={form.page_size}
                  onChange={(v) => set("page_size", v)} />
              </div>
            </Card>
            <Card title="Mailbox" sub="Set by IT in the server configuration.">
              <dl className="meta">
                <dt>Customer care mailbox</dt>
                <dd>{data.mailbox || "Not set"}</dd>
                <dt>Microsoft 365 connection</dt>
                <dd>{data.graph_configured ? <Badge tone="badge-ok">Configured</Badge> : <Badge tone="badge-warn">Not configured</Badge>}</dd>
              </dl>
            </Card>
          </>
        )}
      </fieldset>
    </>
  );
}
