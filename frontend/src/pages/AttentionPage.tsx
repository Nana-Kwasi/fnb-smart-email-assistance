import { useQuery } from "@tanstack/react-query";
import { BellRing, ExternalLink, Info } from "lucide-react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api, query } from "@/api/client";
import type { Attention } from "@/api/types";
import { ExportButtons } from "@/components/ExportButtons";
import { Badge, Card, Empty, ErrorAlert, Loading, PageHead, Pager, PriorityBadge } from "@/components/ui";
import { formatDateTime, isOverdue, waitingFor } from "@/lib/format";

/** Emails the assistant could not answer. People reply in Outlook; this list clears itself. */
export default function AttentionPage() {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const reason = params.get("reason") ?? "";
  const filter = params.get("filter") ?? "all"; // all | urgent | overdue | mine
  const page = Number(params.get("page") ?? 1);

  const { data, error, isLoading } = useQuery({
    queryKey: ["attention", reason, filter, page],
    queryFn: () =>
      api<Attention>(
        `/emails/attention${query({
          reason: reason || null,
          priority: filter === "urgent" ? "high" : null,
          overdue: filter === "overdue" || null,
          mine: filter === "mine" || null,
          page,
        })}`,
      ),
    refetchInterval: 30_000,
    placeholderData: (prev) => prev,
  });

  function set(next: Record<string, string | null>) {
    const p = new URLSearchParams(params);
    for (const [k, v] of Object.entries(next)) (v ? p.set(k, v) : p.delete(k));
    if (!("page" in next)) p.delete("page");
    setParams(p, { replace: true });
  }

  const total = data?.reasons.reduce((n, r) => n + r.count, 0) ?? 0;

  return (
    <>
      <PageHead
        title="Needs attention"
        sub="Customer emails the assistant could not answer by itself, newest first. Reply in Outlook as usual; this list updates on its own."
        actions={<ExportButtons params={{ scope: "attention", reason: reason || null, filter: filter === "all" ? null : filter }} />}
      />

      <div className="alert" role="note">
        <Info size={18} />
        <span>
          The people in charge get an alert straight away for urgent emails, a reminder when an email waits
          longer than {data?.overdue_after_hours ?? 4} hours, and a daily summary of the rest.
        </span>
      </div>

      <Card>
        <div className="stack" style={{ gap: 14, marginBottom: 16 }}>
          <div className="pills" role="tablist" aria-label="Show">
            {[
              ["all", "All waiting", total],
              ["urgent", "Urgent", null],
              ["overdue", `Over ${data?.overdue_after_hours ?? 4} h`, data?.overdue ?? null],
              ["mine", "Assigned to me", null],
            ].map(([key, label, count]) => (
              <button
                key={key as string}
                role="tab"
                aria-selected={filter === key}
                className={`pill ${filter === key ? "active" : ""}`}
                onClick={() => set({ filter: key === "all" ? null : (key as string) })}
              >
                {label}
                {count !== null && <span className="count">{count}</span>}
              </button>
            ))}
          </div>
          {data && data.reasons.length > 0 && (
            <div className="pills" aria-label="Why a person is needed">
              <button className={`pill ${!reason ? "active" : ""}`} onClick={() => set({ reason: null })}>
                Any reason
              </button>
              {data.reasons.map((r) => (
                <button
                  key={r.code}
                  className={`pill ${reason === r.code ? "active" : ""}`}
                  onClick={() => set({ reason: r.code })}
                >
                  {r.label}
                  <span className="count">{r.count}</span>
                </button>
              ))}
            </div>
          )}
        </div>

        <ErrorAlert error={error} />
        {isLoading && !data ? (
          <Loading />
        ) : data && data.page.items.length > 0 ? (
          <>
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>Waiting</th>
                    <th>Customer</th>
                    <th>Subject</th>
                    <th>Why a person is needed</th>
                    <th>Owner</th>
                    <th aria-label="Alerts" />
                  </tr>
                </thead>
                <tbody>
                  {data.page.items.map((e) => {
                    const late = isOverdue(e.received_at, data.overdue_after_hours);
                    return (
                      <tr
                        key={e.id}
                        className="clickable"
                        tabIndex={0}
                        onClick={() => navigate(`/emails/${e.id}`)}
                        onKeyDown={(ev) => ev.key === "Enter" && navigate(`/emails/${e.id}`)}
                      >
                        <td data-label="Waiting">
                          <div>
                            <div className={`waiting-time ${late ? "overdue" : ""}`}>{waitingFor(e.received_at)}</div>
                            <div className="cell-sub">{formatDateTime(e.received_at)}</div>
                          </div>
                        </td>
                        <td data-label="Customer">
                          <div>
                            <div className="cell-main">{e.from_name || e.from_address}</div>
                            <div className="cell-sub">{e.from_address}</div>
                          </div>
                        </td>
                        <td data-label="Subject">
                          <div>
                            <div className="cell-main">{e.subject || "(no subject)"}</div>
                            <div className="cell-sub">
                              {e.reference}
                              {e.is_follow_up ? " · follow-up" : ""}
                            </div>
                          </div>
                        </td>
                        <td data-label="Why">
                          <div>
                            <div className="row" style={{ gap: 4 }}>
                              <PriorityBadge priority={e.priority} />
                              {late && <Badge tone="badge-danger">Overdue</Badge>}
                            </div>
                            <div className="cell-sub">{e.status_reason}</div>
                          </div>
                        </td>
                        <td data-label="Owner">{e.assigned_to?.display_name ?? <span className="cell-sub">Nobody yet</span>}</td>
                        <td data-label="">
                          {(e.urgent_alert_at || e.overdue_alert_at) && (
                            <span title="The people in charge were alerted" className="cell-sub row" style={{ gap: 4 }}>
                              <BellRing size={14} /> Alerted
                            </span>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <Pager page={page} pageSize={data.page.page_size} total={data.page.total} onPage={(p) => set({ page: String(p) })} />
          </>
        ) : (
          <Empty title="Nothing is waiting">Every customer email has been answered.</Empty>
        )}
      </Card>
      <p className="cell-sub row" style={{ gap: 6 }}>
        <ExternalLink size={14} /> Open an email to see the customer’s message, a suggested answer from the bank’s
        data, and a link straight to it in Outlook.
      </p>
    </>
  );
}
