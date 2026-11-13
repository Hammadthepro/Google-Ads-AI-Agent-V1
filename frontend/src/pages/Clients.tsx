import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import { useAuth } from "../lib/auth";
import { useAction, useClients } from "../lib/hooks";
import { IntelligencePanel } from "../components/IntelligencePanel";
import { Badge, Button, Card, dt, EmptyState, ErrorBox, Field, Input, PageHeader, Spinner, StatusBadge, Table } from "../components/ui";

export function Clients() {
  const { data, isLoading } = useClients();
  const { can } = useAuth();
  const nav = useNavigate();
  return (
    <div className="space-y-6">
      <PageHeader title="Clients" subtitle="Every client has a unique ID. A business with its own Google Ads account is a separate client."
        actions={can("clients.create") && <Link to="/clients/new"><Button variant="primary">New client</Button></Link>} />
      <Card title="All clients">
        {isLoading ? <Spinner /> : (
          <Table rows={data ?? []} onRowClick={(c) => nav(`/clients/${c.id}`)} empty="No clients yet — create the first one." columns={[
            { key: "client_code", label: "Client ID", render: (c) => <code className="text-xs">{c.client_code}</code> },
            { key: "name", label: "Client", render: (c) => <span className="font-medium text-slate-900">{c.name}</span> },
            { key: "businesses", label: "Businesses", render: (c) => c.businesses.map((b: any) => b.name).join(", ") || "—" },
            { key: "owner_contact", label: "Owner" },
            { key: "tag_access_contact", label: "Tag access", className: "max-w-xs truncate" },
            { key: "flags", label: "", render: (c) => c.automation_frozen ? <Badge tone="red">automation frozen</Badge> : null },
          ]} />
        )}
      </Card>
    </div>
  );
}

function AuditReports({ clientId, canRun, highlight }: { clientId: string; canRun: boolean; highlight?: string | null }) {
  const nav = useNavigate();
  const list = useQuery({ queryKey: ["audit-reports", clientId], queryFn: () => api.get(`/clients/${clientId}/audit-reports`), refetchInterval: (q) => ((q.state.data as any[])?.some((r) => r.status === "RUNNING") ? 3000 : false) });
  const run = useAction(() => api.post(`/clients/${clientId}/audit-reports`), [["audit-reports", clientId]]);
  const last = (list.data ?? []).find((r: any) => r.status === "COMPLETED");
  return (
    <Card title="Audits" actions={canRun && <Button size="sm" variant="primary" disabled={run.isPending || (list.data ?? []).some((r: any) => r.status === "RUNNING")}
      onClick={() => run.mutate({}, { onSuccess: (r: any) => nav(`/audit-reports/${r.id}`) })}>{run.isPending ? "Starting…" : "Run new audit"}</Button>}>
      <p className="mb-3 text-sm text-slate-600">{last ? <>Last audit: <b>{dt(last.finished_at)}</b> (v{last.version}).{last.profile_changed_since && <span className="ml-1 text-amber-700"> The profile was edited since — run a new audit to refresh.</span>}</> : "No completed audit yet."} Earlier audits are always kept.</p>
      <ErrorBox error={run.error} />
      <Table rows={list.data ?? []} onRowClick={(r) => nav(`/audit-reports/${r.id}`)} empty="No audits yet." columns={[
        { key: "version", label: "Version", render: (r) => <span className={r.id === highlight ? "font-semibold text-brand-700" : ""}>v{r.version}</span> },
        { key: "status", label: "Status", render: (r) => <StatusBadge value={r.status} /> },
        { key: "started_at", label: "Started", render: (r) => dt(r.started_at) },
        { key: "overall", label: "Overall", render: (r) => r.scores?.overall?.score ?? "—" },
        { key: "website", label: "Website", render: (r) => r.scores?.website?.score ?? "—" },
        { key: "gbp", label: "Profile", render: (r) => r.scores?.gbp?.score ?? "—" },
        { key: "competitors", label: "Competitors", render: (r) => r.scores?.competitors?.score ?? "—" },
        { key: "keywords", label: "Keywords", render: (r) => r.scores?.keywords?.score ?? "—" },
        { key: "stale", label: "", render: (r) => r.profile_changed_since ? <Badge tone="amber">profile edited since</Badge> : null },
      ]} />
    </Card>
  );
}

export function ClientDetail() {
  const { id } = useParams();
  const { can } = useAuth();
  const q = useQuery({ queryKey: ["client", id], queryFn: () => api.get(`/clients/${id}`) });
  const structure = useQuery({ queryKey: ["structure", id], queryFn: () => api.get(`/clients/${id}/structure`) });
  const [params] = useSearchParams();
  const unfreeze = useAction(() => api.post(`/clients/${id}/unfreeze`), [["client", id!]]);
  const [tg, setTg] = useState<{ weekly?: string; budget?: string }>({});
  const saveTargets = useAction((v) => api.patch(`/clients/${id}`, v), [["client", id!], ["clients"]]);
  if (q.isLoading || !q.data) return <Spinner />;
  const c = q.data;
  return (
    <div className="space-y-6">
      {params.get("created") && (
        <div className="rounded-xl border border-emerald-200 bg-emerald-50 px-5 py-4" role="status">
          <div className="font-semibold text-emerald-900">Client created · {c.client_code}</div>
          <p className="mt-1 text-sm text-emerald-800">{params.get("report") ? "The analysis has started. Watch it run, or come back to the results here." : "The profile is saved. Run an audit when you are ready."}</p>
          <div className="mt-3 flex flex-wrap gap-2">
            {params.get("report") && <Link to={`/audit-reports/${params.get("report")}`}><Button variant="primary">Watch the analysis live</Button></Link>}
            <Link to={`/?client=${c.id}`}><Button>Ask the agent about this client</Button></Link>
            <Link to="/clients/new"><Button>Add another client</Button></Link>
          </div>
        </div>)}
      {params.get("saved") && <div role="status" className="rounded-xl border border-emerald-200 bg-emerald-50 px-5 py-3 text-sm text-emerald-900">Profile saved. Earlier audits are kept — run a new audit to refresh the findings.</div>}
      <PageHeader title={c.name} subtitle={<>Client ID <code className="rounded bg-slate-100 px-1.5 py-0.5 text-xs">{c.client_code}</code> · created {dt(c.created_at)}</>} actions={<>
        {can("clients.edit") && <Link to={`/clients/${c.id}/edit`}><Button>Edit client information</Button></Link>}
        {c.automation_frozen && <Badge tone="red">automation frozen by emergency brake</Badge>}
        {c.automation_frozen && can("killswitch.client") && <Button size="sm" onClick={() => unfreeze.mutate({})}>Unfreeze automation</Button>}
        <Link to={`/?client=${c.id}`}><Button variant="primary">Ask the agent</Button></Link>
        <Link to={`/tasks?client=${c.id}`}><Button>Run a task</Button></Link></>} />
      <div className="grid items-start gap-8 xl:grid-cols-[minmax(0,1fr)_320px]">
        <div className="min-w-0 space-y-8">
        <Card title="Businesses">
          <Table rows={c.businesses} columns={[
            { key: "name", label: "Business", render: (x) => <Link className="font-medium text-brand-700 hover:underline" to={`/businesses/${x.id}`}>{x.name}</Link> },
            { key: "website_url", label: "Website" },
            { key: "services", label: "Services", render: (x) => x.services.filter((s: any) => !s.is_excluded).map((s: any) => s.name).join(", ") },
            { key: "measurement", label: "Measurement", render: (x) => { const m = c.measurement?.[x.id]; return !m?.evaluated_at ? <span className="text-slate-400">not audited</span> : m.counts.issue ? `${m.counts.issue} problem(s)` : "no problems found"; } },
          ]} />
        </Card>
        <IntelligencePanel clientId={c.id} canRun={can("audits.run")} />
        <AuditReports clientId={c.id} canRun={can("audits.run")} highlight={params.get("report")} />
        <Card title="Campaign structure" actions={structure.data && <Badge tone="violet">{structure.data.source} DATA</Badge>}>
          {structure.isLoading ? <Spinner /> : (
            <Table rows={structure.data?.campaigns ?? []} empty="No campaigns." columns={[
              { key: "name", label: "Campaign", render: (x) => <span className="font-medium">{x.name}</span> },
              { key: "campaign_type", label: "Type", render: (x) => <>{x.campaign_type} {x.read_only && <Badge>read-only in V1</Badge>}</> },
              { key: "status", label: "Status", render: (x) => <StatusBadge value={x.status} /> },
              { key: "daily_budget", label: "Daily budget", render: (x) => `$${x.daily_budget.toFixed(2)}` },
              { key: "weekly_target", label: "Weekly target" },
              { key: "ad_groups", label: "Ad groups", render: (x) => x.ad_groups.length },
              { key: "created_by_app", label: "Created here", render: (x) => x.created_by_app ? <Badge tone="blue">yes</Badge> : "" },
            ]} />
          )}
        </Card>
        </div>
        <aside className="space-y-5 xl:sticky xl:top-[76px]" aria-label="Client facts">
        <Card title="Weekly lead target & budget">
          <p className="mb-3 text-xs text-slate-500">Client-level target for all campaigns combined. Weekly reports compare against this and against each campaign's own target.</p>
          <div className="grid gap-3">
            <Field label="Weekly conversion target (leads)"><Input type="number" disabled={!can("clients.edit")} value={tg.weekly ?? String(c.weekly_conversion_target ?? 0)} onChange={(e) => setTg({ ...tg, weekly: e.target.value })} /></Field>
            <Field label="Agreed monthly ad budget ($)" hint="Optional: leave 0 to use the recommended budget"><Input type="number" disabled={!can("clients.edit")} value={tg.budget ?? String(c.monthly_budget_usd ?? 0)} onChange={(e) => setTg({ ...tg, budget: e.target.value })} /></Field>
          </div>
          {can("clients.edit") && (
            <div className="mt-3 flex items-center gap-3">
              <Button variant="primary" disabled={(tg.weekly === undefined && tg.budget === undefined) || saveTargets.isPending}
                onClick={() => saveTargets.mutate({ ...(tg.weekly !== undefined && { weekly_conversion_target: Number(tg.weekly) }), ...(tg.budget !== undefined && { monthly_budget_usd: Number(tg.budget) }) }, { onSuccess: () => setTg({}) })}>Save targets</Button>
              <ErrorBox error={saveTargets.error} />
            </div>)}
        </Card>
        <Card title="Google Ads accounts" actions={<Link className="font-medium text-brand-700 hover:underline" to="/accounts">Manage →</Link>}>
          {c.accounts?.length ? (
            <ul className="-my-1 divide-y divide-slate-100">{c.accounts.map((a: any) => (
              <li key={a.id ?? a.customer_id} className="py-2.5 text-[13px]">
                <div className="flex items-center justify-between gap-2"><span className="truncate font-medium text-slate-900">{a.descriptive_name || "Unnamed account"}</span><Badge tone="violet">{a.integration_mode}</Badge></div>
                <div className="mt-0.5 flex justify-between gap-2 text-xs text-slate-500"><code className="font-mono">{a.customer_id}</code><span>Auto-tagging {a.auto_tagging_enabled === true ? "on" : a.auto_tagging_enabled === false ? "off" : "unknown"}</span></div>
              </li>))}</ul>
          ) : <EmptyState message="No Google Ads account linked yet — link one on the Google Ads Accounts page." />}
        </Card>
        <Card title="Contacts">
          <dl className="-my-1 divide-y divide-slate-100 text-[13px]">{([["Owner", c.owner_contact], ["Backup", c.backup_contact], ["Website / tag access", c.tag_access_contact]] as [string, string][]).map(([k, v]) => <div key={k} className="flex justify-between gap-4 py-2"><dt className="text-slate-500">{k}</dt><dd className="min-w-0 break-words text-right font-medium text-slate-800">{v || "—"}</dd></div>)}</dl>
        </Card>
        </aside>
      </div>
    </div>
  );
}
