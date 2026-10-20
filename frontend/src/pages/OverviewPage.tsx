import { useQuery } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowDownRight,
  ArrowRight,
  ArrowUpRight,
  Bot,
  CheckCircle2,
  CircleDashed,
  Clock,
  Inbox,
  Lightbulb,
  MailOpen,
  OctagonX,
  Sparkles,
} from "lucide-react";
import { useState, type ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "@/api/client";
import type { Kpi, Operations as Ops, Overview } from "@/api/types";
import { useAuth } from "@/auth/AuthContext";
import { BarList, Legend, Sparkline, TrendChart, type Series } from "@/components/charts";
import { Badge, Card, Empty, ErrorAlert, Loading, PriorityBadge, StatusBadge } from "@/components/ui";
import { formatDuration, greeting, timeAgo } from "@/lib/format";

const SERIES: Series[] = [
  { key: "received", label: "Received", color: "var(--series-1)" },
  { key: "auto", label: "Answered automatically", color: "var(--series-2)" },
  { key: "people", label: "Needed a person", color: "var(--series-3)" },
];

function Trend({ kpi, higherIsBetter, format }: { kpi: Kpi; higherIsBetter: boolean; format: (n: number) => string }) {
  if (kpi.value === null || kpi.previous === null || kpi.previous === 0) {
    return <span>No comparison yet</span>;
  }
  const diff = kpi.value - kpi.previous;
  if (Math.abs(diff) < 1e-9) return <span>Same as yesterday</span>;
  const up = diff > 0;
  const good = up === higherIsBetter;
  return (
    <span>
      <span className={`${up ? "trend-up" : "trend-down"} ${good ? "trend-good" : "trend-bad"}`}>
        {up ? <ArrowUpRight size={15} /> : <ArrowDownRight size={15} />}
        {format(Math.abs(diff))}
      </span>{" "}
      vs yesterday
    </span>
  );
}

function KpiCard({ label, icon, value, foot, spark, color }: {
  label: string;
  icon: ReactNode;
  value: ReactNode;
  foot: ReactNode;
  spark: number[];
  color: string;
}) {
  return (
    <div className="card kpi">
      <span className="kpi-label">
        <span className="stat-icon">{icon}</span>
        {label}
      </span>
      <span className="kpi-value">{value}</span>
      <Sparkline values={spark} color={color} />
      <span className="kpi-foot">{foot}</span>
    </div>
  );
}

/** Period figures with fixed definitions: an acknowledgement, an answer and a resolved case
 * are never counted as the same thing. Hover a figure for its definition. */
function Operations({ ops, days }: { ops: Ops; days: number }) {
  const items: { key: keyof Ops; label: string; value: string | number }[] = [
    { key: "incoming", label: "Received", value: ops.incoming },
    { key: "acknowledged", label: "Acknowledged", value: ops.acknowledged },
    { key: "ai_drafted", label: "AI drafted", value: ops.ai_drafted },
    { key: "auto_resolved", label: "Answered automatically", value: ops.auto_resolved },
    { key: "awaiting_review", label: "Waiting for a person", value: ops.awaiting_review },
    { key: "urgent_escalations", label: "Urgent escalations", value: ops.urgent_escalations },
    { key: "avg_first_response_minutes", label: "First real answer", value: ops.avg_first_response_minutes === null ? "—" : `${ops.avg_first_response_minutes} min` },
    { key: "avg_resolution_hours", label: "Time to resolve", value: ops.avg_resolution_hours === null ? "—" : `${ops.avg_resolution_hours} h` },
    { key: "automation_rate", label: "Automation rate", value: ops.automation_rate === null ? "—" : `${Math.round(ops.automation_rate * 100)}%` },
    { key: "sla_breached", label: "Cases past deadline", value: `${ops.sla_breached} of ${ops.open_cases}` },
  ];
  return (
    <Card title="Operations" sub={`Last ${days} days. Hover a figure to see exactly what it counts.`}>
      <dl className="meta" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", display: "grid", gap: 12 }}>
        {items.map((i) => (
          <div key={i.key} title={ops.definitions[i.key] ?? ""}>
            <dt className="cell-sub">{i.label}</dt>
            <dd style={{ margin: 0, fontSize: 22, fontWeight: 700 }}>{i.value}</dd>
          </div>
        ))}
      </dl>
    </Card>
  );
}

export default function OverviewPage() {
  const { me, can } = useAuth();
  const navigate = useNavigate();
  const [days, setDays] = useState(14);
  const { data, error, isLoading } = useQuery({
    queryKey: ["overview", days],
    queryFn: () => api<Overview>(`/overview?days=${days}`),
    refetchInterval: 30_000,
    placeholderData: (prev) => prev,
  });
  if (isLoading && !data) return <Loading />;
  if (!data) return <ErrorAlert error={error} />;

  const firstName = (me?.user.display_name || me?.user.username || "").split(" ")[0];
  const pct = (n: number) => `${Math.round(n * 100)}%`;

  return (
    <>
      <header className="page-head">
        <div>
          <h1 className="greeting">
            {greeting()}, {firstName}
          </h1>
          <p className="today">
            {new Date().toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long", year: "numeric" })}
          </p>
        </div>
        <div className="page-actions">
          {data.kill_switch ? (
            <Badge tone="badge-danger">
              <OctagonX size={14} /> Emergency stop on
            </Badge>
          ) : (
            <Badge tone={data.auto_reply_enabled ? "badge-ok" : "badge-warn"}>
              <Bot size={14} /> Automatic answers {data.auto_reply_enabled ? "on" : "off"}
            </Badge>
          )}
          {data.automation_mode && (
            <Badge tone="badge-info">AI: {data.automation_mode.replace("_", " ")}</Badge>
          )}
        </div>
      </header>

      <section className="card briefing" aria-label="Today's briefing">
        <span className="briefing-icon" aria-hidden>
          <Sparkles size={20} />
        </span>
        <div>
          <h2>Today’s briefing</h2>
          <ul>
            {data.briefing.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </div>
      </section>

      <div className="grid grid-stats">
        <KpiCard
          label="Received today"
          icon={<MailOpen size={16} />}
          value={data.received.value ?? 0}
          foot={<Trend kpi={data.received} higherIsBetter format={(n) => String(n)} />}
          spark={data.received.spark}
          color="var(--series-1)"
        />
        <KpiCard
          label="Answered automatically"
          icon={<Bot size={16} />}
          value={data.auto_rate.value === null ? "—" : pct(data.auto_rate.value)}
          foot={<Trend kpi={data.auto_rate} higherIsBetter format={(n) => `${Math.round(n * 100)} pts`} />}
          spark={data.auto_rate.spark}
          color="var(--series-2)"
        />
        <KpiCard
          label="Needs a person now"
          icon={<Inbox size={16} />}
          value={data.waiting.value ?? 0}
          foot={
            data.waiting_high_priority || data.overdue ? (
              <span>
                {data.waiting_high_priority} urgent · {data.overdue} over {data.overdue_after_hours} h
              </span>
            ) : (
              <span>Nothing urgent</span>
            )
          }
          spark={data.waiting.spark}
          color="var(--series-3)"
        />
        <KpiCard
          label="Time to acknowledge"
          icon={<Clock size={16} />}
          value={formatDuration(data.avg_ack_seconds.value)}
          foot={<Trend kpi={data.avg_ack_seconds} higherIsBetter={false} format={(n) => formatDuration(n)} />}
          spark={data.avg_ack_seconds.spark}
          color="var(--series-1)"
        />
      </div>

      {data.operations && <Operations ops={data.operations} days={days} />}

      <div className="grid grid-main-side">
        <Card
          title="Customer emails"
          sub="Per day: received, answered automatically, and those that needed a person"
          actions={
            <div className="tabs" role="tablist" aria-label="Range">
              {[7, 14, 30].map((d) => (
                <button key={d} role="tab" aria-selected={days === d} className={days === d ? "active" : ""} onClick={() => setDays(d)}>
                  {d} days
                </button>
              ))}
            </div>
          }
        >
          <TrendChart data={data.trend} series={SERIES} />
          <div style={{ marginTop: 12 }}>
            <Legend series={SERIES} />
          </div>
        </Card>

        <Card
          title="Why a person was needed"
          sub="Last 7 days"
          actions={
            can("queue:view") && (
              <Link className="card-link" to="/attention">
                Open list <ArrowRight size={14} />
              </Link>
            )
          }
        >
          <BarList
            items={data.reasons_7d.map((r) => ({ key: r.code, label: r.label, value: r.count }))}
            color="var(--series-3)"
            onSelect={can("queue:view") ? (code) => navigate(`/attention?reason=${code}`) : undefined}
            empty={<Empty title="The assistant answered everything it could" />}
          />
        </Card>
      </div>

      <div className="grid grid-main-side">
        <Card
          title="Latest emails"
          actions={
            <Link className="card-link" to="/emails">
              View all <ArrowRight size={14} />
            </Link>
          }
        >
          {data.recent.length === 0 ? (
            <Empty title="No emails yet" />
          ) : (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>Reference</th>
                    <th>From</th>
                    <th>Subject</th>
                    <th>Status</th>
                    <th>Received</th>
                  </tr>
                </thead>
                <tbody>
                  {data.recent.map((e) => (
                    <tr key={e.id} className="clickable" tabIndex={0} onClick={() => navigate(`/emails/${e.id}`)}
                      onKeyDown={(ev) => ev.key === "Enter" && navigate(`/emails/${e.id}`)}>
                      <td data-label="Reference"><span className="cell-main">{e.reference}</span></td>
                      <td data-label="From">{e.from_name}</td>
                      <td data-label="Subject"><span className="cell-sub">{e.subject || "(no subject)"}</span></td>
                      <td data-label="Status">
                        <div className="row" style={{ gap: 4 }}>
                          <StatusBadge status={e.status} />
                          <PriorityBadge priority={e.priority} />
                        </div>
                      </td>
                      <td data-label="Received"><span className="cell-sub">{timeAgo(e.received_at)}</span></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        <div className="stack" style={{ gap: "var(--gap)" }}>
          <Card title="Most used answers" sub="Last 7 days">
            <BarList
              items={data.top_topics_7d.map((t) => ({ key: t.title, label: t.title, value: t.count }))}
              color="var(--series-2)"
              empty={<p className="card-sub">No automatic answers yet this week.</p>}
            />
          </Card>

          {data.topic_gaps.length > 0 && (
            <Card title="Missing from the knowledge base" sub="Words customers keep using that no answer covers yet">
              <div className="stack">
                <div className="chips">
                  {data.topic_gaps.map((g) => (
                    <span className="chip" key={g.title} style={{ paddingRight: 12 }}>
                      <Lightbulb size={14} /> {g.title} · {g.count}
                    </span>
                  ))}
                </div>
                {can("knowledge:edit") && (
                  <Link className="card-link" to="/knowledge">
                    Add to the knowledge base <ArrowRight size={14} />
                  </Link>
                )}
              </div>
            </Card>
          )}

          <Card title="System health">
            <div className="health">
              {data.health.map((h) => (
                <div className="health-item" key={h.name}>
                  {h.ok === true ? (
                    <CheckCircle2 size={18} color="var(--ok)" aria-label="Working" />
                  ) : h.ok === false ? (
                    <AlertTriangle size={18} color="var(--danger)" aria-label="Problem" />
                  ) : (
                    <CircleDashed size={18} color="var(--ink-faint)" aria-label="Not set up" />
                  )}
                  <div>
                    <div style={{ fontWeight: 600 }}>{h.name}</div>
                    <div className="cell-sub">{h.detail}</div>
                  </div>
                </div>
              ))}
            </div>
          </Card>
        </div>
      </div>
    </>
  );
}
