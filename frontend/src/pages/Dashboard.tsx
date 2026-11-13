import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { useAuth } from "../lib/auth";
import { Badge, Card, cx, DataSourceBadge, DataStatusNotice, dt, EmptyState, MetricStrip, money, Notice, PageHeader, Skeleton, StatusBadge, Table } from "../components/ui";

const HAS = (c: any) => ["data_available", "historical"].includes(c.data_status?.state);

function DataState({ c }: { c: any }) {
  const st = c.data_status?.state;
  return st === "historical" ? <Badge tone="amber" title={c.data_status.message} dot>Historical data</Badge>
    : st === "data_available" ? <Badge tone="green" dot>Data available</Badge>
    : st === "not_configured" ? <Badge tone="slate">Not configured</Badge>
    : st === "no_data" ? <Badge tone="blue">Connected but no data</Badge>
    : st === "failed" ? <Badge tone="red" dot>Retrieval failed</Badge> : <Badge tone="slate">No account linked</Badge>;
}

/** Conversions against the weekly target, drawn from the two numbers the row already has. Nothing is drawn without real data. */
function TargetBar({ c }: { c: any }) {
  if (!HAS(c)) return <span className="text-slate-400">—</span>;
  const target = Number(c.weekly_target) || 0;
  const conv = Number(c.conversions_7d) || 0;
  const share = target > 0 ? Math.min(conv / target, 1) : 0;
  const behind = c.gap < 0;
  return (
    <div className="min-w-[150px]">
      <div className="flex items-baseline justify-between gap-2 tabular">
        <span className="font-medium text-slate-900">{c.conversions_7d}</span>
        <span className="text-xs text-slate-500">of {c.weekly_target}</span>
      </div>
      {target > 0 && (
        <div className="mt-1 h-1 overflow-hidden rounded-full bg-slate-100" aria-hidden>
          <div className={cx("h-full rounded-full", behind ? "bg-amber-500" : "bg-emerald-500")} style={{ width: `${share * 100}%` }} />
        </div>)}
    </div>
  );
}

function Timeline({ items, empty }: { items: { key: string; tone: string; title: React.ReactNode; meta?: React.ReactNode; body?: React.ReactNode }[]; empty: string }) {
  if (!items.length) return <p className="px-5 py-6 text-[13px] text-slate-500">{empty}</p>;
  const dot: Record<string, string> = { red: "bg-rose-500", amber: "bg-amber-500", green: "bg-emerald-500", blue: "bg-brand-500", slate: "bg-slate-300" };
  return (
    <ol className="divide-y divide-slate-100">
      {items.map((i) => (
        <li key={i.key} className="flex gap-3 px-5 py-3">
          <span aria-hidden className={cx("mt-1.5 h-2 w-2 shrink-0 rounded-full", dot[i.tone] ?? dot.slate)} />
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[13px]">{i.title}</div>
            {i.body && <div className="mt-0.5 text-xs leading-5 text-slate-600">{i.body}</div>}
            {i.meta && <div className="mt-0.5 text-2xs text-slate-400">{i.meta}</div>}
          </div>
        </li>))}
    </ol>
  );
}

export default function Dashboard() {
  const { can } = useAuth();
  const { data, isLoading } = useQuery({ queryKey: ["dashboard"], queryFn: () => api.get("/dashboard") });
  if (isLoading || !data) return <div><PageHeader title="Overview" subtitle="Last 7 days" /><Skeleton lines={6} /></div>;
  const totals = data.clients.reduce((a: any, c: any) => ({ cost: a.cost + c.cost_7d, conv: a.conv + c.conversions_7d, target: a.target + c.weekly_target,
    approvals: a.approvals + c.pending_approvals, fixes: a.fixes + c.open_fixes }), { cost: 0, conv: 0, target: 0, approvals: 0, fixes: 0 });
  // Numbers are shown only for clients whose Google Ads data was really retrieved (current or historical); never a fabricated 0.
  const withData = data.clients.filter(HAS);
  const states = Array.from(new Set(data.clients.filter((c: any) => !["data_available"].includes(c.data_status?.state)).map((c: any) => c.data_status?.state)));
  const first = data.clients.find((c: any) => c.data_status && c.data_status.state !== "data_available");
  const hasData = withData.length > 0;
  const attention = data.clients.filter((c: any) => c.automation_frozen || c.flagged);
  return (
    <div className="space-y-8">
      <PageHeader title="Overview" subtitle={<>Last 7 days · as of {dt(data.now)}</>}
        actions={hasData ? <DataSourceBadge source={data.data_source} /> : undefined} />
      {(data.clients.length === 0 || first) && (
        <div className="space-y-2">
          {data.clients.length === 0 && <Notice tone="blue">Your first real campaign analysis will appear here after connecting Google Ads. Add a client, then configure Google Ads on the <Link className="underline" to="/integrations">Integrations page</Link>.</Notice>}
          {first && states.length === 1 && <DataStatusNotice status={first.data_status} />}
          {first && states.length > 1 && <Notice tone="amber">Some clients have no current Google Ads data — see the Data column below.</Notice>}
        </div>)}

      <MetricStrip items={[
        { label: "Spend (7d)", value: hasData ? money(totals.cost) : "—", sub: hasData ? "clients with data" : "No Google Ads data available.", emphasis: true },
        { label: "Conversions (7d)", value: hasData ? totals.conv.toFixed(1) : "—", sub: hasData ? `target ${totals.target}` : "", emphasis: true },
        { label: "Gap to target", value: hasData ? <span className={totals.conv - totals.target < 0 ? "text-amber-700" : "text-emerald-700"}>{(totals.conv - totals.target).toFixed(1)}</span> : "—" },
        { label: "Pending approvals", value: <Link to="/approvals" className="text-brand-700 hover:underline">{totals.approvals}</Link>, sub: totals.approvals ? "waiting for a reviewer" : "nothing waiting" },
        { label: "Open client fixes", value: totals.fixes },
      ]} />

      <Card title="Clients" description={data.clients.length ? `${data.clients.length} client${data.clients.length === 1 ? "" : "s"} · ${withData.length} with Google Ads data` : undefined}
        actions={<Link to="/clients" className="font-medium text-brand-700 hover:underline">All clients</Link>}>
        {data.clients.length === 0 ? <EmptyState title="No clients yet" message="Clients you add appear here with their spend, conversions and approvals."
          action={can("clients.create") ? <Link to="/clients/new" className="text-[13px] font-medium text-brand-700 hover:underline">Add a client</Link> : undefined} /> :
        <Table rows={data.clients} columns={[
          { key: "name", label: "Client", render: (c) => <Link className="whitespace-nowrap font-medium text-slate-900 hover:text-brand-700 hover:underline" to={`/clients/${c.client_id}`}>{c.name}</Link> },
          { key: "cost_7d", label: "Spend 7d", className: "text-right", render: (c) => <span className="tabular">{HAS(c) ? money(c.cost_7d) : "—"}</span> },
          { key: "conversions_7d", label: "Conversions vs weekly target", render: (c) => <TargetBar c={c} /> },
          { key: "gap", label: "Gap", className: "text-right", render: (c) => HAS(c) ? <span className={cx("tabular font-medium", c.gap < 0 ? "text-amber-700" : "text-emerald-700")}>{c.gap}</span> : "—" },
          { key: "pending_approvals", label: "Approvals", className: "text-right" },
          { key: "flags", label: "Flags", render: (c) => <div className="flex gap-1">{c.automation_frozen && <Badge tone="red">automation frozen</Badge>}{c.flagged && <Badge tone="amber">flagged</Badge>}</div> },
          { key: "data_status", label: "Data", render: (c) => <DataState c={c} /> },
        ]} empty="No clients yet." />}
      </Card>

      <div className="grid gap-6 lg:grid-cols-5">
        <Card className="lg:col-span-3" flush title="Emergency brake (recent)" description="Automatic protections that fired"
          actions={<Link to="/monitor" className="font-medium text-brand-700 hover:underline">Monitor →</Link>}>
          <Timeline empty="No emergency events." items={(data.recent_emergency ?? []).map((e: any, i: number) => ({
            key: e.id ?? `${e.rule}-${i}`, tone: e.action_mode === "SHADOW" ? "slate" : "red",
            title: <><span className="font-medium capitalize text-slate-900">{e.rule.replace(/_/g, " ")}</span><Badge>{e.action}</Badge><StatusBadge value={e.action_mode} /></>,
            body: e.message, meta: e.created_at ? dt(e.created_at) : undefined }))} />
        </Card>
        <Card className="lg:col-span-2" flush title="Recent tasks" actions={<Link to="/tasks" className="font-medium text-brand-700 hover:underline">New task →</Link>}>
          <Timeline empty="No tasks yet." items={(data.recent_tasks ?? []).map((t: any) => ({
            key: t.id, tone: ({ COMPLETED: "green", SUCCEEDED: "green", FAILED: "red", RUNNING: "blue", AWAITING_APPROVAL: "amber" } as Record<string, string>)[t.status] ?? "slate",
            title: <><Link to={`/tasks/${t.id}`} className="font-medium capitalize text-slate-900 hover:text-brand-700 hover:underline">{t.task_type.replace(/_/g, " ").toLowerCase()}</Link><StatusBadge value={t.status} /></>,
            meta: dt(t.created_at) }))} />
        </Card>
      </div>

      {attention.length > 0 && (
        <section aria-label="Clients needing attention">
          <h2 className="mb-2 text-sm font-semibold text-slate-900">Needs attention</h2>
          <ul className="flex flex-wrap gap-2">{attention.map((c: any) => (
            <li key={c.client_id}><Link to={`/clients/${c.client_id}`} className="inline-flex items-center gap-2 rounded-md border border-slate-200 bg-white px-3 py-1.5 text-[13px] hover:border-slate-300">
              <span className="font-medium text-slate-900">{c.name}</span>{c.automation_frozen && <Badge tone="red">automation frozen</Badge>}{c.flagged && <Badge tone="amber">flagged</Badge>}</Link></li>))}</ul>
        </section>)}
    </div>
  );
}
