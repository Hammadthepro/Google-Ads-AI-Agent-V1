import { useEffect, useState } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
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

/** Where a route lives in the product, for the header trail. Detail pages name their parent list. */
const TRAIL: [RegExp, string, string?][] = [
  [/^\/overview/, "Overview"], [/^\/clients\/new/, "New client", "/clients"], [/^\/clients\/[^/]+\/edit/, "Edit client", "/clients"], [/^\/clients\/./, "Client", "/clients"], [/^\/clients/, "Clients"],
  [/^\/audit-reports\//, "Client audit", "/audits"], [/^\/businesses\/./, "Business", "/businesses"], [/^\/businesses/, "Businesses"], [/^\/accounts/, "Google Ads accounts"],
  [/^\/tasks\/./, "Task", "/tasks"], [/^\/tasks/, "Work queue"], [/^\/audits\/./, "Audit", "/audits"], [/^\/audits/, "Research & audits"],
  [/^\/planner\/./, "Plan", "/planner"], [/^\/planner/, "Campaign plans"], [/^\/approvals\/./, "Approval", "/approvals"], [/^\/approvals/, "Approvals"],
  [/^\/monitor/, "Monitoring"], [/^\/weekly/, "Reports"], [/^\/experiments/, "Experiments"], [/^\/learning/, "Playbook"], [/^\/users/, "Users & roles"],
  [/^\/integrations/, "Integrations"], [/^\/settings/, "Settings"], [/^\/activity/, "Activity log"],
];
const PARENT_LABEL: Record<string, string> = { "/clients": "Clients", "/audits": "Research & audits", "/businesses": "Businesses", "/tasks": "Work queue",
  "/planner": "Campaign plans", "/approvals": "Approvals" };
const groupOf = (path: string) => NAV.find((g) => g.items.some((i) => i.to !== "/" && path.startsWith(i.to)))?.group;

function Trail() {
  const { pathname } = useLocation();
  if (pathname === "/") return <span className="text-[13px] font-medium text-slate-900">Agent</span>;
  const hit = TRAIL.find(([re]) => re.test(pathname));
  if (!hit) return null;
  const [, label, parent] = hit;
  const group0 = groupOf(parent ?? pathname);
  const group = group0 && group0 !== (parent ? PARENT_LABEL[parent] : label) ? group0 : undefined;
  return (
    <nav aria-label="Breadcrumb" className="flex min-w-0 items-center gap-1.5 text-[13px]">
      {group && <span className="hidden text-slate-400 sm:inline">{group}</span>}
      {group && <Icon name="chevron" className="hidden h-3 w-3 text-slate-300 sm:block" />}
      {parent && <><Link to={parent} className="truncate text-slate-500 hover:text-slate-900">{PARENT_LABEL[parent]}</Link><Icon name="chevron" className="h-3 w-3 shrink-0 text-slate-300" /></>}
      <span className="truncate font-medium text-slate-900" aria-current="page">{label}</span>
    </nav>
  );
}

/** Write protection, stated once and quietly. It turns amber only when writes are actually possible. */
export function SafetyBanner() {
  const { data } = useQuery({ queryKey: ["integration-banner"], queryFn: () => api.get("/integrations/mode"), staleTime: 30_000 });
  if (!data) return null;
  const writes = !!data.real_writes_possible;
  const missing = [!data.write_keys?.environment_enabled && "deployment permit", data.mode !== "execution" && "execution mode",
    !data.write_keys?.super_admin_authorized && "Super Admin key"].filter(Boolean) as string[];
  return (
    <NavLink to="/integrations#writes" role="status" title={writes ? data.label : `Missing: ${missing.join(", ")}`}
      className={cx("inline-flex h-7 items-center gap-1.5 rounded-md px-2.5 text-xs font-medium ring-1 ring-inset transition-colors",
        writes ? "bg-amber-50 text-amber-900 ring-amber-300 hover:bg-amber-100" : "bg-white text-slate-600 ring-slate-200 hover:text-slate-900 hover:ring-slate-300")}>
      <Icon name="shield" className={cx("h-3.5 w-3.5", writes ? "text-amber-600" : "text-slate-400")} />
      <span className="hidden sm:inline">{writes ? "Writes possible with approval" : `Google Ads writes off · ${missing.length} key${missing.length === 1 ? "" : "s"} missing`}</span>
      <span className="sm:hidden">{writes ? "Writes possible" : "Writes off"}</span>
    </NavLink>
  );
}

function SelectedAccountChip({ rail }: { rail?: boolean }) {
  const { data } = useQuery({ queryKey: ["gads-selection"], queryFn: () => api.get("/google-ads/selection"), staleTime: 15_000, retry: false });
  const s = data?.selected;
  if (rail) return (
    <NavLink to="/accounts" title={s ? `Google Ads account: ${s.name || s.customer_id}` : "Google Ads account: none selected"}
      className="relative mx-auto mb-2 flex h-9 w-9 items-center justify-center rounded-md text-ink-muted hover:bg-white/[0.06] hover:text-white">
      <Icon name="accounts" /><span className="sr-only">Switch Google Ads account</span>
      {s && <span aria-hidden className="absolute right-1.5 top-1.5 h-1.5 w-1.5 rounded-full bg-emerald-400" />}
    </NavLink>);
  return (
    <NavLink to="/accounts" title="Switch Google Ads account"
      className="group mx-3 mb-3 block rounded-md border border-ink-line bg-ink-raised px-3 py-2 text-xs transition-colors hover:border-slate-600">
      <div className="flex items-center justify-between text-2xs text-ink-muted"><span>Google Ads account</span><span className="opacity-0 transition-opacity group-hover:opacity-100">Switch</span></div>
      <div className="mt-0.5 flex items-center gap-1.5 truncate font-medium text-slate-100">
        <span aria-hidden className={cx("h-1.5 w-1.5 shrink-0 rounded-full", s ? "bg-emerald-400" : "bg-slate-500")} />
        <span className="truncate">{s ? (s.name || s.customer_id) : "None selected"}</span>
      </div>
      {s && <div className="pl-3 font-mono text-2xs text-ink-muted">{s.customer_id_display ?? s.customer_id}</div>}
    </NavLink>
  );
}

function Nav({ onNavigate, rail }: { onNavigate?: () => void; rail?: boolean }) {
  const { can } = useAuth();
  return (
    <nav aria-label="Main" className={cx("pane-scroll-dark flex-1 overflow-y-auto py-3", rail ? "px-2" : "px-3")}>
      {NAV.map((g, gi) => {
        const items = g.items.filter((i) => !i.perm || can(i.perm));
        if (!items.length) return null;
        return (
          <div key={g.group} className={gi ? (rail ? "mt-3 border-t border-ink-line pt-3" : "mt-5") : ""}>
            {!rail && <div className="px-2 pb-1.5 text-2xs font-medium text-ink-muted">{g.group}</div>}
            <ul className="space-y-px">
              {items.map((n) => (
                <li key={n.to}>
                  <NavLink to={n.to} end={n.to === "/"} onClick={onNavigate} title={rail ? n.label : undefined}
                    className={({ isActive }) => cx("group relative flex items-center rounded-md text-[13px] transition-colors",
                      rail ? "h-9 justify-center" : "gap-2.5 px-2 py-[7px]",
                      isActive ? "bg-white/[0.08] font-medium text-white" : "text-slate-300 hover:bg-white/[0.04] hover:text-white")}>
                    {({ isActive }) => (<>
                      {isActive && <span aria-hidden className={cx("absolute top-1.5 bottom-1.5 w-[2px] rounded-full bg-brand-300", rail ? "left-0" : "-left-3")} />}
                      <Icon name={n.icon} className={cx("h-4 w-4 shrink-0", isActive ? "text-brand-300" : "text-ink-muted group-hover:text-slate-200")} />
                      <span className={rail ? "sr-only" : "truncate"}>{n.label}</span>
                    </>)}
                  </NavLink>
                </li>))}
            </ul>
          </div>);
      })}
    </nav>
  );
}

function Mark() {
  // A search result bar beside a rising bid line: the product's two concerns, intent and spend.
  return (
    <svg aria-hidden viewBox="0 0 24 24" className="h-6 w-6 shrink-0">
      <rect width="24" height="24" rx="6" fill="#136977" />
      <path d="M6 8.5h7M6 12h4.5" stroke="#d3e9ec" strokeWidth="1.8" strokeLinecap="round" />
      <path d="M6 17.2l4-2.6 3 1.6 5-5" stroke="#fff" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" fill="none" />
    </svg>
  );
}

function Brand({ rail, onToggle }: { rail?: boolean; onToggle?: () => void }) {
  return (
    <div className={cx("flex h-[52px] shrink-0 items-center border-b border-ink-line", rail ? "justify-center px-2" : "gap-2.5 px-4")}>
      <Mark />
      {!rail && <div className="min-w-0 flex-1 truncate text-[13px] font-semibold text-white">Ads Agent <span className="ml-1 font-normal text-ink-muted">V1</span></div>}
      {onToggle && !rail && (
        <button type="button" onClick={onToggle} className="rounded p-1 text-ink-muted hover:bg-white/[0.06] hover:text-white" aria-label="Collapse sidebar" title="Collapse sidebar">
          <Icon name="collapse" className="h-4 w-4" />
        </button>)}
    </div>
  );
}

function UserBox({ rail }: { rail?: boolean }) {
  const { user, logout } = useAuth();
  if (!user) return null;
  const initials = (user.name || user.email).slice(0, 2).toUpperCase();
  if (rail) return (
    <div className="flex flex-col items-center gap-1 border-t border-ink-line py-3">
      <div aria-hidden title={`${user.name || user.email} · ${ROLE_LABEL[user.role]}`} className="flex h-7 w-7 items-center justify-center rounded-full bg-ink-line text-2xs font-semibold text-slate-200">{initials}</div>
      <button onClick={logout} className="rounded px-1 py-0.5 text-2xs text-ink-muted hover:text-white">Sign out</button>
    </div>);
  return (
    <div className="flex items-center gap-2.5 border-t border-ink-line px-4 py-3">
      <div aria-hidden className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-ink-line text-2xs font-semibold text-slate-200">{initials}</div>
      <div className="min-w-0 flex-1">
        <div className="truncate text-xs font-medium text-slate-100">{user.name || user.email}</div>
        <div className="truncate text-2xs text-ink-muted">{ROLE_LABEL[user.role]}</div>
      </div>
      <button onClick={logout} className="rounded px-1.5 py-1 text-2xs text-ink-muted hover:bg-white/[0.06] hover:text-white">Sign out</button>
    </div>
  );
}

const RAIL_KEY = "gaa.sidebar.rail";
function readRail() { try { return window.localStorage.getItem(RAIL_KEY) === "1"; } catch { return false; } }

export default function Layout() {
  const [open, setOpen] = useState(false);
  const [rail, setRail] = useState(readRail);
  const loc = useLocation();
  useEffect(() => setOpen(false), [loc.pathname]);
  useEffect(() => { try { window.localStorage.setItem(RAIL_KEY, rail ? "1" : "0"); } catch { /* storage unavailable: keep in memory */ } }, [rail]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  const wide = loc.pathname === "/";  // the agent workspace uses the full width
  return (
    <div className="flex min-h-screen">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded focus:bg-white focus:px-3 focus:py-2 focus:text-sm">Skip to content</a>
      <aside className={cx("sticky top-0 hidden h-screen shrink-0 flex-col bg-ink text-slate-200 transition-[width] duration-200 lg:flex", rail ? "w-[60px]" : "w-[232px]")}>
        <Brand rail={rail} onToggle={() => setRail(true)} />
        <Nav rail={rail} />
        {rail && (
          <button type="button" onClick={() => setRail(false)} className="mx-auto mb-2 flex h-8 w-8 items-center justify-center rounded-md text-ink-muted hover:bg-white/[0.06] hover:text-white" aria-label="Expand sidebar" title="Expand sidebar">
            <Icon name="expand" />
          </button>)}
        <SelectedAccountChip rail={rail} /><UserBox rail={rail} />
      </aside>
      {open && (
        <div className="fixed inset-0 z-40 lg:hidden" role="dialog" aria-modal="true" aria-label="Navigation">
          <div className="absolute inset-0 bg-ink/50" onClick={() => setOpen(false)} />
          <aside className="absolute inset-y-0 left-0 flex w-[272px] animate-fade-in flex-col bg-ink text-slate-200 shadow-pop">
            <Brand /><Nav onNavigate={() => setOpen(false)} /><SelectedAccountChip /><UserBox />
          </aside>
        </div>)}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex h-[52px] shrink-0 items-center gap-3 border-b border-slate-200 bg-white/95 px-4 backdrop-blur supports-[backdrop-filter]:bg-white/85 sm:px-6">
          <button className="-ml-1.5 rounded-md p-1.5 text-slate-600 hover:bg-slate-100 lg:hidden" aria-label="Open navigation" aria-expanded={open} onClick={() => setOpen(true)}><Icon name="menu" /></button>
          <div className="min-w-0 flex-1"><Trail /></div>
          <div className="shrink-0"><SafetyBanner /></div>
        </header>
        <main id="main" className={cx("w-full flex-1", wide ? "" : "mx-auto max-w-content px-4 pb-16 pt-7 sm:px-6 lg:px-10")}><Outlet /></main>
      </div>
    </div>
  );
}
