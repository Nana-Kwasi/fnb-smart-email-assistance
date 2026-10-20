import { useQuery } from "@tanstack/react-query";
import { Lightbulb, MailOpen, Repeat, Timer } from "lucide-react";
import { useState } from "react";
import { api } from "@/api/client";
import type { QualityMetrics } from "@/api/types";
import { BarList, Legend, TrendChart, type Series } from "@/components/charts";
import { Badge, Card, Empty, ErrorAlert, Loading, PageHead, StatCard } from "@/components/ui";
import { percent } from "@/lib/format";

interface Analytics {
  days: number;
  facts: {
    emails: number;
    by_category: { category: string; label: string; emails: number }[];
    daily_by_category: ({ date: string } & Record<string, number | string>)[];
    top_categories: { category: string; label: string }[];
    department_workload: { department: string; open_cases: number }[];
    complaints: number;
    complaint_themes: { theme: string; emails: number }[];
    backlog: { date: string; waiting: number }[];
    avg_resolution_hours: { category: string; label: string; hours: number; cases: number }[];
    cases_opened: number;
    reopened_cases: number;
    repeat_contact_rate: number | null;
    avg_emails_per_case: number | null;
    avg_handoffs_per_case: number | null;
    possible_duplicates: number;
    ai_drafted: number;
    quality: QualityMetrics;
  };
  signals: { category: string; label: string; detail: string; themes: { theme: string; emails: number }[] }[];
  forecast: { date: string; expected: number }[] | null;
  forecast_note: string | null;
  recommendations: { text: string; evidence: string }[];
}

const COLORS = ["var(--series-1)", "var(--series-2)", "var(--series-3)", "var(--series-4)", "var(--series-5)", "var(--series-6)"];

export default function AnalyticsPage() {
  const [days, setDays] = useState(30);
  const { data, error, isLoading } = useQuery({
    queryKey: ["analytics", days],
    queryFn: () => api<Analytics>(`/analytics?days=${days}`),
    placeholderData: (prev) => prev,
  });
  if (isLoading && !data) return <Loading />;
  if (!data) return <ErrorAlert error={error} />;
  const f = data.facts;
  const series: Series[] = f.top_categories.map((c, i) => ({ key: c.category, label: c.label, color: COLORS[i % COLORS.length] }));

  return (
    <>
      <PageHead
        title="Analytics"
        sub="Counted from records. Anything marked “possible” is a pattern worth checking, not a finding."
        actions={
          <div className="tabs" role="tablist" aria-label="Range">
            {[7, 30, 90].map((d) => (
              <button key={d} role="tab" aria-selected={days === d} className={days === d ? "active" : ""} onClick={() => setDays(d)}>{d} days</button>
            ))}
          </div>
        }
      />

      {data.signals.map((s) => (
        <div key={s.category} className="alert alert-warn">
          <strong>Possible recurring issue: {s.label}.</strong> {s.detail}
          {s.themes.length > 0 && <> Common words: {s.themes.map((t) => t.theme).join(", ")}.</>}
        </div>
      ))}

      <div className="grid grid-stats">
        <StatCard label="Customer emails" icon={<MailOpen size={16} />} value={f.emails} note={`${f.cases_opened} cases opened`} />
        <StatCard label="Repeat contact" icon={<Repeat size={16} />} value={percent(f.repeat_contact_rate)} note={`${f.reopened_cases} cases reopened`} />
        <StatCard label="Customer effort" icon={<Timer size={16} />} value={f.avg_emails_per_case ?? "—"} note={`emails per case · ${f.avg_handoffs_per_case ?? "—"} hand-offs`} />
        <StatCard label="Complaints" icon={<Lightbulb size={16} />} value={f.complaints} note={`${f.possible_duplicates} possible duplicate cases`} />
      </div>

      <Card title="Emails by category" sub="Per day, the six largest categories">
        {series.length ? (
          <>
            <TrendChart data={f.daily_by_category} series={series} />
            <div style={{ marginTop: 12 }}><Legend series={series} /></div>
          </>
        ) : <Empty title="No emails in this period" />}
      </Card>

      <div className="grid grid-2">
        <Card title="Backlog" sub="Emails waiting for a person at the end of each day">
          <TrendChart data={f.backlog} series={[{ key: "waiting", label: "Waiting", color: "var(--series-3)" }]} height={200} />
        </Card>
        <Card title="Open cases by department">
          <BarList items={f.department_workload.map((d) => ({ key: d.department, label: d.department, value: d.open_cases }))}
            color="var(--series-2)" empty={<p className="card-sub">No open cases.</p>} />
        </Card>
      </div>

      <div className="grid grid-2">
        <Card title="Complaint themes" sub="Most frequent word pairs in complaints and missing-transaction emails">
          <BarList items={f.complaint_themes.map((t) => ({ key: t.theme, label: t.theme, value: t.emails }))}
            color="var(--series-3)" empty={<p className="card-sub">Not enough complaints to show themes.</p>} />
        </Card>
        <Card title="Time to resolve" sub="Average hours from case opened to resolved">
          <BarList items={f.avg_resolution_hours.map((r) => ({ key: r.category, label: `${r.label} (${r.cases})`, value: r.hours }))}
            color="var(--series-1)" empty={<p className="card-sub">No resolved cases yet.</p>} />
        </Card>
      </div>

      <div className="grid grid-2">
        <Card title="Workload forecast" sub="Average for the same weekday over the period">
          {data.forecast ? (
            <BarList items={data.forecast.map((d) => ({ key: d.date, label: new Date(`${d.date}T12:00:00`).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" }), value: d.expected }))} />
          ) : <p className="card-sub">{data.forecast_note}</p>}
        </Card>
        <Card title="Suggestions" sub="Hypotheses from the numbers, with the evidence">
          {data.recommendations.length === 0 ? <p className="card-sub">Nothing stands out.</p> : (
            <div className="stack">
              {data.recommendations.map((r) => (
                <div key={r.text}>
                  <Badge tone="badge-info">Possible</Badge> {r.text}
                  <div className="cell-sub">Evidence: {r.evidence}</div>
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>

      <Card title="AI answer quality" sub="Last 30 days at most">
        <div className="grid grid-stats">
          <StatCard label="Drafts" icon={<Lightbulb size={16} />} value={f.quality.drafts.created} note={`${percent(f.quality.drafts.validation_failure_rate)} failed a check`} />
          <StatCard label="Edited by officers" icon={<Lightbulb size={16} />} value={percent(f.quality.drafts.officer_edit_rate)} note={`${f.quality.drafts.rejected} rejected`} />
          <StatCard label="Sent by the AI" icon={<Lightbulb size={16} />} value={f.quality.ai_answers_sent} note={`Citations ${percent(f.quality.citation_coverage)}`} />
          <StatCard label="Possible missed escalations" icon={<Lightbulb size={16} />} value={f.quality.possible_missed_escalations.length} note="Answered automatically, later urgent" />
        </div>
      </Card>
    </>
  );
}
