import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, query } from "@/api/client";
import type { AdminUser, AuditEvent, NotificationEntry, Page, User } from "@/api/types";
import { useAuth } from "@/auth/AuthContext";
import { Badge, Card, Empty, ErrorAlert, Field, Loading, PageHead, Pager, Switch } from "@/components/ui";
import { formatDateTime } from "@/lib/format";

const ROLES: { value: User["role"]; label: string; hint: string }[] = [
  { value: "officer", label: "Officer", hint: "Handles customer emails, checks and sends drafts" },
  { value: "supervisor", label: "Supervisor", hint: "Also approves replies and actions that need a second person; sees AI quality and analytics" },
  { value: "auditor", label: "Auditor", hint: "Sees everything needed to review decisions, including the audit trail; changes nothing" },
  { value: "admin", label: "Admin", hint: "Also runs the knowledge base, settings, automation policy and users" },
];

function UsersTab() {
  const { me, can } = useAuth();
  const canManage = can("users:manage");
  const queryClient = useQueryClient();
  const { data, error, isLoading } = useQuery({ queryKey: ["admin-users"], queryFn: () => api<AdminUser[]>("/admin/users") });
  const update = useMutation({
    mutationFn: ({ id, change }: { id: string; change: Partial<Pick<AdminUser, "role" | "is_active">> }) =>
      api<AdminUser>(`/admin/users/${id}`, { method: "PATCH", json: change }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["admin-users"] });
      void queryClient.invalidateQueries({ queryKey: ["assignable-users"] });
    },
  });
  if (isLoading) return <Loading />;

  return (
    <Card
      title="Staff"
      sub="Anyone in the Smart Email Assistance AD group can sign in. New people start as officers; change their role here. Changes apply immediately."
    >
      <ErrorAlert error={error ?? update.error} />
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Role</th>
              <th>Access</th>
              <th>Last sign-in</th>
            </tr>
          </thead>
          <tbody>
            {(data ?? []).map((u) => {
              const isMe = u.id === me?.user.id;
              const locked = !canManage || isMe || update.isPending;
              return (
                <tr key={u.id}>
                  <td data-label="Name">
                    <div>
                      <div className="cell-main">
                        {u.display_name || u.username} {isMe && <Badge tone="badge-brand">You</Badge>}
                      </div>
                      <div className="cell-sub">
                        {u.username}
                        {u.email ? ` · ${u.email}` : ""}
                      </div>
                    </div>
                  </td>
                  <td data-label="Role">
                    <select
                      className="select"
                      style={{ minWidth: 150 }}
                      value={u.role}
                      disabled={locked}
                      aria-label={`Role for ${u.display_name || u.username}`}
                      title={isMe ? "You cannot change your own role" : ROLES.find((r) => r.value === u.role)?.hint}
                      onChange={(e) => update.mutate({ id: u.id, change: { role: e.target.value as User["role"] } })}
                    >
                      {ROLES.map((r) => (
                        <option key={r.value} value={r.value}>
                          {r.label}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td data-label="Access">
                    <Switch
                      checked={u.is_active}
                      disabled={locked}
                      onChange={(v) => update.mutate({ id: u.id, change: { is_active: v } })}
                      label={u.is_active ? "Allowed" : "Switched off"}
                    />
                  </td>
                  <td data-label="Last sign-in">{formatDateTime(u.last_login_at)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function AuditTab() {
  const [filters, setFilters] = useState({ action: "", actor: "" });
  const [page, setPage] = useState(1);
  const { data, error, isLoading } = useQuery({
    queryKey: ["audit", filters, page],
    queryFn: () => api<Page<AuditEvent>>(`/admin/audit${query({ ...filters, page })}`),
  });
  return (
    <Card title="Audit trail" sub="Every sign-in, change, view and reply.">
      <div className="form-grid" style={{ marginBottom: 16 }}>
        <Field label="Action starts with" hint="e.g. auth, email, topic, settings">
          <input className="input" value={filters.action} onChange={(e) => { setFilters((f) => ({ ...f, action: e.target.value })); setPage(1); }} />
        </Field>
        <Field label="Username">
          <input className="input" value={filters.actor} onChange={(e) => { setFilters((f) => ({ ...f, actor: e.target.value })); setPage(1); }} />
        </Field>
      </div>
      <ErrorAlert error={error} />
      {isLoading ? (
        <Loading />
      ) : data && data.items.length ? (
        <>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>When</th>
                  <th>Who</th>
                  <th>Action</th>
                  <th>Details</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((a) => (
                  <tr key={a.id}>
                    <td data-label="When">{formatDateTime(a.at)}</td>
                    <td data-label="Who">
                      <div>
                        <div>{a.actor_username}</div>
                        {a.ip_address && <div className="cell-sub">{a.ip_address}</div>}
                      </div>
                    </td>
                    <td data-label="Action"><code>{a.action}</code></td>
                    <td data-label="Details">
                      <span className="cell-sub">
                        {Object.entries(a.detail).map(([k, v]) => `${k}: ${typeof v === "object" ? JSON.stringify(v) : String(v)}`).join(" · ")}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pager page={page} pageSize={data.page_size} total={data.total} onPage={setPage} />
        </>
      ) : (
        <Empty title="No matching events" />
      )}
    </Card>
  );
}

const KIND_LABEL: Record<NotificationEntry["kind"], string> = {
  urgent: "Urgent alert",
  overdue: "Overdue reminder",
  digest: "Daily summary",
  system: "System problem",
};

function NotificationsTab() {
  const [page, setPage] = useState(1);
  const { data, error, isLoading } = useQuery({
    queryKey: ["notifications", page],
    queryFn: () => api<Page<NotificationEntry>>(`/admin/notifications${query({ page })}`),
  });
  return (
    <Card title="Alerts sent" sub="Every alert and daily summary sent to the people in charge.">
      <ErrorAlert error={error} />
      {isLoading ? (
        <Loading />
      ) : data && data.items.length ? (
        <>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>When</th>
                  <th>Kind</th>
                  <th>Subject</th>
                  <th>To</th>
                  <th>Delivered</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((n) => (
                  <tr key={n.id}>
                    <td data-label="When">{formatDateTime(n.sent_at)}</td>
                    <td data-label="Kind"><Badge tone={n.kind === "urgent" || n.kind === "system" ? "badge-danger" : "badge-brand"}>{KIND_LABEL[n.kind]}</Badge></td>
                    <td data-label="Subject">{n.subject}</td>
                    <td data-label="To"><span className="cell-sub">{n.recipients.join(", ")}</span></td>
                    <td data-label="Delivered">
                      {n.succeeded ? <Badge tone="badge-ok">Yes</Badge> : <Badge tone="badge-danger">{n.error ?? "Failed"}</Badge>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pager page={page} pageSize={data.page_size} total={data.total} onPage={setPage} />
        </>
      ) : (
        <Empty title="No alerts sent yet" />
      )}
    </Card>
  );
}

export default function AdminPage() {
  const { can } = useAuth();
  const tabs = [
    { key: "users", label: "Users", show: can("users:view") },
    { key: "audit", label: "Audit trail", show: can("audit:view") },
    { key: "alerts", label: "Alerts sent", show: can("audit:view") },
  ].filter((t) => t.show);
  const [tab, setTab] = useState(tabs[0]?.key ?? "users");

  return (
    <>
      <PageHead
        title="Users & Audit"
        actions={
          <div className="tabs" role="tablist">
            {tabs.map((t) => (
              <button key={t.key} role="tab" aria-selected={tab === t.key} className={tab === t.key ? "active" : ""} onClick={() => setTab(t.key)}>
                {t.label}
              </button>
            ))}
          </div>
        }
      />
      {tab === "users" && <UsersTab />}
      {tab === "audit" && <AuditTab />}
      {tab === "alerts" && <NotificationsTab />}
    </>
  );
}
