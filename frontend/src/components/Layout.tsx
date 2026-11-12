import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { ROLE_LABEL, useAuth } from "../lib/auth";
import { Icon } from "./icons";
import { cx } from "./ui";

type Item = { to: string; label: string; icon: string; perm?: string };
/** Product areas, not workflow steps. The agent is the home; everything else is a view onto the same shared record. */
const NAV: { group: string; items: Item[] }[] = [
  { group: "Workspace", items: [{ to: "/", label: "Agent", icon: "agent" }, { to: "/overview", label: "Overview", icon: "overview" }] },
  { group: "Clients", items: [{ to: "/clients", label: "Clients", icon: "clients" }, { to: "/accounts", label: "Google Ads accounts", icon: "accounts" }] },
  { group: "Campaigns", items: [{ to: "/planner", label: "Campaign plans", icon: "campaigns" }, { to: "/tasks", label: "Work queue", icon: "work" },
    { to: "/approvals", label: "Approvals", icon: "approvals" }] },
  { group: "Intelligence", items: [{ to: "/audits", label: "Research & audits", icon: "research" }, { to: "/monitor", label: "Monitoring", icon: "monitoring" },
    { to: "/weekly", label: "Reports", icon: "reports" }, { to: "/experiments", label: "Experiments", icon: "experiments" }, { to: "/learning", label: "Playbook", icon: "playbook" }] },
  { group: "Admin", items: [{ to: "/integrations", label: "Integrations", icon: "integrations" }, { to: "/users", label: "Users & roles", icon: "users", perm: "users.manage" },
    { to: "/settings", label: "Settings", icon: "settings", perm: "settings.view" }, { to: "/activity", label: "Activity log", icon: "activity" }] },
];

/** Write protection, stated once and quietly. It turns amber only when writes are actually possible. */
export function SafetyBanner() {
  const { data } = useQuery({ queryKey: ["integration-banner"], queryFn: () => api.get("/integrations/mode"), staleTime: 30_000 });
  if (!data) return null;
  const writes = !!data.real_writes_possible;
  const missing = [!data.write_keys?.environment_enabled && "deployment permit", data.mode !== "execution" && "execution mode",
    !data.write_keys?.super_admin_authorized && "Super Admin key"].filter(Boolean) as string[];
  return (
    <NavLink to="/integrations#writes" role="status" title={writes ? data.label : `Missing: ${missing.join(", ")}`}
      className={cx("inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-2xs font-medium ring-1 ring-inset transition hover:ring-slate-300",
        writes ? "bg-amber-50 text-amber-900 ring-amber-300" : "bg-white text-slate-600 ring-slate-200")}>
      <Icon name="shield" className="h-3.5 w-3.5" />
      <span>{writes ? "Writes possible with approval" : `Google Ads writes off · ${missing.length} key${missing.length === 1 ? "" : "s"} missing`}</span>
    </NavLink>
  );
}

function SelectedAccountChip() {
  const { data } = useQuery({ queryKey: ["gads-selection"], queryFn: () => api.get("/google-ads/selection"), staleTime: 15_000, retry: false });
  const s = data?.selected;
  return (
    <NavLink to="/accounts" title="Switch Google Ads account"
      className="mx-3 mb-3 block rounded-md border border-slate-200 bg-white px-3 py-2 text-xs transition hover:border-slate-300">
      <div className="text-2xs font-medium uppercase tracking-wider text-slate-400">Google Ads account</div>
      <div className="mt-0.5 truncate font-medium text-slate-800">{s ? (s.name || s.customer_id) : "None selected"}</div>
      {s && <div className="font-mono text-2xs text-slate-500">{s.customer_id_display ?? s.customer_id}</div>}
    </NavLink>
  );
}

function Nav({ onNavigate }: { onNavigate?: () => void }) {
  const { can } = useAuth();
  return (
    <nav aria-label="Main" className="flex-1 space-y-5 overflow-y-auto px-3 py-3">
      {NAV.map((g) => {
        const items = g.items.filter((i) => !i.perm || can(i.perm));
        if (!items.length) return null;
        return (
          <div key={g.group}>
            <div className="px-2 pb-1 text-2xs font-medium uppercase tracking-wider text-slate-400">{g.group}</div>
            <ul className="space-y-px">
              {items.map((n) => (
                <li key={n.to}>
                  <NavLink to={n.to} end={n.to === "/"} onClick={onNavigate}
                    className={({ isActive }) => cx("group flex items-center gap-2.5 rounded-md px-2 py-1.5 text-[13px] transition-colors",
                      isActive ? "bg-slate-900/[0.06] font-medium text-slate-900" : "text-slate-600 hover:bg-slate-900/[0.04] hover:text-slate-900")}>
                    {({ isActive }) => (<><Icon name={n.icon} className={cx("h-4 w-4 shrink-0", isActive ? "text-brand-600" : "text-slate-400 group-hover:text-slate-600")} />{n.label}</>)}
                  </NavLink>
                </li>))}
            </ul>
          </div>);
      })}
    </nav>
  );
}

function Brand() {
  return (
    <div className="flex h-12 items-center gap-2.5 border-b border-slate-200 px-4">
      <div className="flex h-6 w-6 items-center justify-center rounded-md bg-slate-900 text-white"><Icon name="agent" className="h-3.5 w-3.5" /></div>
      <div className="text-[13px] font-semibold tracking-tight text-slate-900">Ads Agent</div>
    </div>
  );
}

function UserBox() {
  const { user, logout } = useAuth();
  if (!user) return null;
  return (
    <div className="flex items-center gap-2.5 border-t border-slate-200 px-4 py-3">
      <div aria-hidden className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-slate-200 text-2xs font-semibold text-slate-700">{(user.name || user.email).slice(0, 2).toUpperCase()}</div>
      <div className="min-w-0 flex-1">
        <div className="truncate text-xs font-medium text-slate-800">{user.name || user.email}</div>
        <div className="truncate text-2xs text-slate-500">{ROLE_LABEL[user.role]}</div>
      </div>
      <button onClick={logout} className="rounded px-1.5 py-1 text-2xs text-slate-500 hover:bg-slate-100 hover:text-slate-800">Sign out</button>
    </div>
  );
}

export default function Layout() {
  const [open, setOpen] = useState(false);
  const loc = useLocation();
  useEffect(() => setOpen(false), [loc.pathname]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  const wide = loc.pathname === "/";  // the agent workspace uses the full width
  return (
    <div className="flex min-h-screen">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded focus:bg-white focus:px-3 focus:py-2 focus:text-sm">Skip to content</a>
      <aside className="sticky top-0 hidden h-screen w-56 shrink-0 flex-col border-r border-slate-200 bg-[#fbfbfc] lg:flex">
        <Brand /><Nav /><SelectedAccountChip /><UserBox />
      </aside>
      {open && (
        <div className="fixed inset-0 z-40 lg:hidden" role="dialog" aria-modal="true" aria-label="Navigation">
          <div className="absolute inset-0 bg-slate-900/30" onClick={() => setOpen(false)} />
          <aside className="absolute inset-y-0 left-0 flex w-64 animate-fade-in flex-col border-r border-slate-200 bg-[#fbfbfc] shadow-pop">
            <Brand /><Nav onNavigate={() => setOpen(false)} /><SelectedAccountChip /><UserBox />
          </aside>
        </div>)}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex h-12 items-center gap-3 border-b border-slate-200 bg-white/90 px-4 backdrop-blur">
          <button className="-ml-1 rounded p-1.5 text-slate-600 hover:bg-slate-100 lg:hidden" aria-label="Open navigation" aria-expanded={open} onClick={() => setOpen(true)}><Icon name="menu" /></button>
          <div className="text-[13px] font-semibold tracking-tight text-slate-900 lg:hidden">Ads Agent</div>
          <div className="ml-auto"><SafetyBanner /></div>
        </header>
        <main id="main" className={cx("w-full flex-1", wide ? "" : "mx-auto max-w-[1200px] px-4 py-6 sm:px-6 lg:px-8")}><Outlet /></main>
      </div>
    </div>
  );
}
