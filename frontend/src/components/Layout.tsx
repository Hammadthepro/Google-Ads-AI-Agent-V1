import { NavLink, Outlet } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { ROLE_LABEL, useAuth } from "../lib/auth";
import { cx } from "./ui";

const NAV: { to: string; label: string; icon: string }[] = [
  { to: "/", label: "Dashboard", icon: "◧" },
  { to: "/clients", label: "Clients", icon: "◎" },
  { to: "/businesses", label: "Businesses", icon: "▣" },
  { to: "/accounts", label: "Google Ads Accounts", icon: "◈" },
  { to: "/tasks", label: "Tasks", icon: "▶" },
  { to: "/audits", label: "Audits", icon: "⌕" },
  { to: "/planner", label: "Campaign Planner", icon: "✎" },
  { to: "/approvals", label: "Approvals", icon: "✓" },
  { to: "/monitor", label: "Campaign Monitor", icon: "∿" },
  { to: "/weekly", label: "Weekly Review", icon: "▤" },
  { to: "/experiments", label: "Experiments", icon: "⚗" },
  { to: "/learning", label: "Learning / Playbook", icon: "❖" },
  { to: "/users", label: "Users & Roles", icon: "☺" },
  { to: "/integrations", label: "Integrations", icon: "⇄" },
  { to: "/settings", label: "System Settings", icon: "⚙" },
  { to: "/activity", label: "Activity Logs", icon: "≣" },
];

export function SafetyBanner() {
  const { data } = useQuery({ queryKey: ["integration-banner"], queryFn: () => api.get("/integrations/mode"), staleTime: 30_000 });
  if (!data) return null;
  const tone = data.mode === "read_only" ? "bg-sky-600 text-white" : "bg-amber-500 text-amber-950";
  return (
    <div role="status" className={cx("flex flex-wrap items-center gap-x-4 gap-y-1 px-6 py-1.5 text-xs font-medium", tone)}>
      <span className="font-semibold uppercase tracking-wide">{data.label}</span>
      <span>{data.mode === "read_only" ? "Real Google Ads data is read. Writes are impossible." : "Real reads and approved writes."}</span>
      <span className="opacity-90">Real campaign writes: {data.real_writes_possible ? "POSSIBLE (approval required)" : "DISABLED"}</span>
    </div>
  );
}

export default function Layout() {
  const { user, logout } = useAuth();
  return (
    <div className="flex min-h-screen flex-col">
      <SafetyBanner />
      <div className="flex flex-1">
        <aside className="hidden w-60 shrink-0 flex-col border-r border-slate-200 bg-white lg:flex">
          <div className="flex items-center gap-2 px-5 py-4">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand-600 text-sm font-bold text-white">GA</div>
            <div>
              <div className="text-sm font-semibold leading-tight">Ads AI Agent</div>
              <div className="text-[11px] text-slate-500">V1 · safety-first</div>
            </div>
          </div>
          <nav className="flex-1 space-y-0.5 overflow-y-auto px-3 pb-4">
            {NAV.map((n) => (
              <NavLink key={n.to} to={n.to} end={n.to === "/"}
                className={({ isActive }) => cx("flex items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-sm",
                  isActive ? "bg-brand-50 font-medium text-brand-700" : "text-slate-600 hover:bg-slate-50 hover:text-slate-900")}>
                <span className="w-4 text-center text-xs opacity-70">{n.icon}</span>{n.label}
              </NavLink>
            ))}
          </nav>
          {user && (
            <div className="border-t border-slate-100 px-4 py-3 text-xs">
              <div className="truncate font-medium text-slate-800">{user.name || user.email}</div>
              <div className="truncate text-slate-500">{user.email}</div>
              <div className="mt-1 flex items-center justify-between">
                <span className="rounded bg-slate-100 px-1.5 py-0.5 text-slate-600">{ROLE_LABEL[user.role]}</span>
                <button onClick={logout} className="text-slate-500 hover:text-slate-800">Sign out</button>
              </div>
            </div>
          )}
        </aside>
        <div className="flex min-w-0 flex-1 flex-col">
          <nav className="flex gap-1 overflow-x-auto border-b border-slate-200 bg-white px-4 py-2 lg:hidden">
            {NAV.map((n) => (
              <NavLink key={n.to} to={n.to} end={n.to === "/"}
                className={({ isActive }) => cx("whitespace-nowrap rounded-md px-2 py-1 text-xs", isActive ? "bg-brand-50 text-brand-700" : "text-slate-600")}>
                {n.label}
              </NavLink>
            ))}
          </nav>
          <main className="mx-auto w-full max-w-7xl flex-1 px-4 py-6 sm:px-6"><Outlet /></main>
        </div>
      </div>
    </div>
  );
}
