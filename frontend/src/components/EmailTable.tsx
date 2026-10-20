import { useNavigate } from "react-router-dom";
import type { EmailSummary } from "@/api/types";
import { formatDateTime } from "@/lib/format";
import { PriorityBadge, StatusBadge } from "./ui";

export function EmailTable({ emails, showAssignee = false }: { emails: EmailSummary[]; showAssignee?: boolean }) {
  const navigate = useNavigate();
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th>From</th>
            <th>Subject</th>
            <th>Received</th>
            <th>Status</th>
            {showAssignee && <th>Assigned to</th>}
          </tr>
        </thead>
        <tbody>
          {emails.map((e) => (
            <tr
              key={e.id}
              className="clickable"
              tabIndex={0}
              onClick={() => navigate(`/emails/${e.id}`)}
              onKeyDown={(ev) => ev.key === "Enter" && navigate(`/emails/${e.id}`)}
            >
              <td data-label="From">
                <div>
                  <div className="cell-main">{e.from_name || e.from_address}</div>
                  <div className="cell-sub">{e.from_address}</div>
                </div>
              </td>
              <td data-label="Subject">
                <div>
                  <div className="cell-main">{e.subject || "(no subject)"}</div>
                  <div className="cell-sub">{e.reference}</div>
                </div>
              </td>
              <td data-label="Received">{formatDateTime(e.received_at)}</td>
              <td data-label="Status">
                <div>
                  <div className="row" style={{ gap: 4 }}>
                    <StatusBadge status={e.status} />
                    <PriorityBadge priority={e.priority} />
                  </div>
                  {e.status_reason && <div className="cell-sub">{e.status_reason}</div>}
                </div>
              </td>
              {showAssignee && <td data-label="Assigned to">{e.assigned_to?.display_name ?? "—"}</td>}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
