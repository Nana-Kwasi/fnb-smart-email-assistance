import { useQuery } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "@/api/client";
import type { TopicSummary } from "@/api/types";
import { useAuth } from "@/auth/AuthContext";
import { Badge, Card, Empty, ErrorAlert, Loading, PageHead } from "@/components/ui";
import { formatDateTime } from "@/lib/format";

export default function TopicsPage() {
  const { can } = useAuth();
  const navigate = useNavigate();
  const { data, error, isLoading } = useQuery({ queryKey: ["topics"], queryFn: () => api<TopicSummary[]>("/topics") });

  return (
    <>
      <PageHead
        title="Topics"
        sub="The questions the assistant can answer, and where in the bank’s data each answer comes from."
        actions={
          can("topics:edit") && (
            <Link className="btn btn-primary" to="/topics/new">
              <Plus size={16} /> New topic
            </Link>
          )
        }
      />
      <Card>
        <ErrorAlert error={error} />
        {isLoading ? (
          <Loading />
        ) : data && data.length > 0 ? (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Topic</th>
                  <th>Data source</th>
                  <th>Status</th>
                  <th>Answered (7 days)</th>
                  <th>Last change</th>
                </tr>
              </thead>
              <tbody>
                {data.map((t) => (
                  <tr
                    key={t.id}
                    className="clickable"
                    tabIndex={0}
                    onClick={() => navigate(`/topics/${t.id}`)}
                    onKeyDown={(e) => e.key === "Enter" && navigate(`/topics/${t.id}`)}
                  >
                    <td data-label="Topic">
                      <div>
                        <div className="cell-main">{t.title}</div>
                        <div className="cell-sub">{t.example_count} example phrasings</div>
                      </div>
                    </td>
                    <td data-label="Data source">
                      <code>{t.data_source}</code>
                    </td>
                    <td data-label="Status">
                      <div className="row" style={{ gap: 4 }}>
                        {!t.is_live ? (
                          <Badge tone="badge-info">Not live yet</Badge>
                        ) : t.is_active ? (
                          <Badge tone="badge-ok">Live</Badge>
                        ) : (
                          <Badge>Switched off</Badge>
                        )}
                        {t.has_pending && <Badge tone="badge-accent">Change awaiting approval</Badge>}
                      </div>
                    </td>
                    <td data-label="Answered (7 days)"><strong>{t.answered_7d}</strong></td>
                    <td data-label="Last change">{formatDateTime(t.updated_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty title="No topics yet">
            Create a topic for each kind of question customers ask, and point it at the table that holds the answer.
          </Empty>
        )}
      </Card>
    </>
  );
}
