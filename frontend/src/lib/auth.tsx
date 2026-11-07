import { createContext, ReactNode, useContext } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";

export type User = { id: string; email: string; name: string; role: string; permissions: string[]; client_ids?: string[] };
type Ctx = { user: User | null; loading: boolean; can: (p: string) => boolean; refresh: () => void; logout: () => Promise<void> };

const AuthCtx = createContext<Ctx>({ user: null, loading: true, can: () => false, refresh: () => {}, logout: async () => {} });

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const me = useQuery({ queryKey: ["me"], queryFn: () => api.get("/auth/me"), retry: false });
  const user: User | null = me.data?.user ?? null;
  const value: Ctx = {
    user,
    loading: me.isLoading,
    can: (p) => !!user?.permissions.includes(p),
    refresh: () => qc.invalidateQueries({ queryKey: ["me"] }),
    logout: async () => {
      await api.post("/auth/logout");
      qc.clear();
      window.location.href = "/login";
    },
  };
  return <AuthCtx.Provider value={value}>{children}</AuthCtx.Provider>;
}

export const useAuth = () => useContext(AuthCtx);

export const ROLE_LABEL: Record<string, string> = {
  SUPER_ADMIN: "Super Admin", ADMIN: "Admin", CAMPAIGN_MANAGER: "Campaign Manager", ANALYST: "Analyst",
};
