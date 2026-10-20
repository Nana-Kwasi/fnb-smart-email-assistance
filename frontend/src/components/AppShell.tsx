import { useQuery } from "@tanstack/react-query";
import {
  BarChart3,
  BellRing,
  BookOpenCheck,
  BrainCircuit,
  FolderOpen,
  Database,
  FlaskConical,
  LayoutDashboard,
  Library,
  LogOut,
  MailCheck,
  Mails,
  Menu,
  Settings,
  ShieldCheck,
} from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { api } from "@/api/client";
import type { Overview, Permission } from "@/api/types";
import { useAuth } from "@/auth/AuthContext";
import { formatDateTime, initials, ROLE_LABEL } from "@/lib/format";
import { GlobalSearch } from "./GlobalSearch";

interface NavItem {
  to: string;
  label: string;
  icon: ReactNode;
  permission: Permission;
  phase2?: boolean; // only shown once answers from bank databases are switched on
}

const NAV: { section: string; items: NavItem[] }[] = [
  {
    section: "Work",
    items: [
      { to: "/", label: "Overview", icon: <LayoutDashboard size={18} />, permission: "overview:view" },
      { to: "/attention", label: "Needs attention", icon: <BellRing size={18} />, permission: "queue:view" },
      { to: "/cases", label: "Cases", icon: <FolderOpen size={18} />, permission: "cases:view" },
      { to: "/emails", label: "Email log", icon: <Mails size={18} />, permission: "emails:view" },
      { to: "/analytics", label: "Analytics", icon: <BarChart3 size={18} />, permission: "analytics:view" },
    ],
  },
  {
    section: "Assistant",
    items: [
      { to: "/knowledge", label: "Knowledge base", icon: <Library size={18} />, permission: "knowledge:view" },
      { to: "/topics", label: "Topics", icon: <BookOpenCheck size={18} />, permission: "topics:view", phase2: true },
      { to: "/test", label: "Test area", icon: <FlaskConical size={18} />, permission: "test:use" },
      { to: "/ai", label: "AI & automation", icon: <BrainCircuit size={18} />, permission: "ai:view" },
    ],
  },
  {
    section: "Administration",
    items: [
      {
        to: "/data-sources",
        label: "Data sources",
        icon: <Database size={18} />,
        permission: "datasources:manage",
        phase2: true,
      },
      { to: "/settings", label: "Settings", icon: <Settings size={18} />, permission: "settings:view" },
      { to: "/admin", label: "Users & audit", icon: <ShieldCheck size={18} />, permission: "users:view" },
    ],
  },
];

export function AppShell() {
  const { me, can, logout } = useAuth();
  const [open, setOpen] = useState(false);
  const location = useLocation();
  useEffect(() => setOpen(false), [location.pathname]);

  // Live counters in the menu.
  const { data: overview } = useQuery({
    queryKey: ["overview", 14],
    queryFn: () => api<Overview>("/overview?days=14"),
    enabled: can("overview:view"),
    refetchInterval: 60_000,
  });
  const counts: Record<string, number | undefined> = {
    "/attention": overview?.waiting.value ?? undefined,
    "/topics": can("topics:approve") ? overview?.pending_approvals : undefined,
  };

  const name = me?.user.display_name || me?.user.username || "";
  const lastLogin = me?.user.previous_login_at;

  return (
    <div className={`shell ${open ? "drawer-open" : ""}`}>
      <aside className="sidebar" aria-label="Main menu">
        <NavLink to="/" className="app-name">
          <span className="app-icon" aria-hidden>
            <MailCheck size={20} />
          </span>
          <strong>Smart Email Assistance</strong>
        </NavLink>

        <nav className="nav">
          {NAV.map((group) => {
            const phase2 = Boolean(me?.features.customer_data_answers);
            const items = group.items.filter((i) => can(i.permission) && (phase2 || !i.phase2));
            if (!items.length) return null;
            return (
              <div key={group.section}>
                <div className="nav-section">{group.section}</div>
                {items.map((item) => (
                  <NavLink key={item.to} to={item.to} end={item.to === "/"}>
                    {item.icon}
                    <span>{item.label}</span>
                    {Boolean(counts[item.to]) && <span className="nav-count">{counts[item.to]}</span>}
                  </NavLink>
                ))}
              </div>
            );
          })}
        </nav>

        <div className="profile">
          <span className="avatar" aria-hidden>
            {initials(name)}
          </span>
          <div className="profile-text">
            <div className="profile-name" title={name}>
              {name}
            </div>
            <div className="profile-meta">{ROLE_LABEL[me?.user.role ?? ""] ?? me?.user.role}</div>
            <div className="profile-meta" title={lastLogin ? formatDateTime(lastLogin) : undefined}>
              {lastLogin ? `Last login ${formatDateTime(lastLogin)}` : "First sign-in"}
            </div>
          </div>
          <button className="icon-btn" onClick={() => void logout()} aria-label="Sign out" title="Sign out">
            <LogOut size={16} />
          </button>
        </div>
      </aside>
      <div className="backdrop" onClick={() => setOpen(false)} />

      <div className="main">
        <header className="header">
          <button className="icon-btn menu-btn" onClick={() => setOpen(true)} aria-label="Open menu">
            <Menu size={20} />
          </button>
          <GlobalSearch />
        </header>
        <main className="content">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
