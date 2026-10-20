import { useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, ApiError, setCsrfToken, setUnauthorisedHandler } from "@/api/client";
import type { Me, Permission } from "@/api/types";

interface AuthState {
  me: Me | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  reload: () => Promise<void>;  // refresh after settings change what the app shows
  can: (permission: Permission) => boolean;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  const queryClient = useQueryClient();

  const apply = useCallback(
    (next: Me | null) => {
      setCsrfToken(next?.csrf_token ?? "");
      setMe(next);
      if (!next) queryClient.clear(); // never show the previous user's data
    },
    [queryClient],
  );

  useEffect(() => {
    setUnauthorisedHandler(() => apply(null));
    api<Me>("/auth/me")
      .then(apply)
      .catch((err) => {
        if (!(err instanceof ApiError && err.status === 401)) console.error(err);
        apply(null);
      })
      .finally(() => setLoading(false));
  }, [apply]);

  const value = useMemo<AuthState>(
    () => ({
      me,
      loading,
      login: async (username, password) => apply(await api<Me>("/auth/login", { json: { username, password } })),
      logout: async () => {
        await api("/auth/logout", { method: "POST" }).catch(() => undefined);
        apply(null);
      },
      reload: async () => apply(await api<Me>("/auth/me")),
      can: (permission) => Boolean(me?.permissions.includes(permission)),
    }),
    [me, loading, apply],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}
