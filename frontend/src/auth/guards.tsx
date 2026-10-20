import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import type { Permission } from "@/api/types";
import { Empty, Loading } from "@/components/ui";
import { useAuth } from "./AuthContext";

/** Pages behind sign-in. The server checks every request too; this only keeps the UI tidy. */
export function RequireAuth({ children }: { children: ReactNode }) {
  const { me, loading } = useAuth();
  const location = useLocation();
  if (loading) return <Loading />;
  if (!me) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  return <>{children}</>;
}

export function RequirePermission({ permission, children }: { permission: Permission; children: ReactNode }) {
  const { can } = useAuth();
  if (!can(permission)) {
    return <Empty title="You do not have access to this page">Ask an administrator if you need it.</Empty>;
  }
  return <>{children}</>;
}
