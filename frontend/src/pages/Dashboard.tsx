import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { Badge, Card, DataSourceBadge, DataStatusNotice, dt, money, Notice, PageHeader, Spinner, Stat, StatusBadge, Table } from "../components/ui";

export default function Dashboard() {
  const { data, isLoading } = useQuery({ queryKey: ["dashboard"], queryFn: () => api.get("/dashboard") });
  if (isLoading || !data) return <Spinner />;
  const totals = data.clients.reduce((a: any, c: any) => ({ cost: a.cost + c.cost_7d, conv: a.conv + c.conversions_7d, target: a.target + c.weekly_target,
    approvals: a.approvals + c.pending_approvals, fixes: a.fixes + c.open_fixes }), { cost: 0, conv: 0, target: 0, approvals: 0, fixes: 0 });
  // Numbers are shown only for clients whose Google Ads data was really retrieved (current or historical); never a fabricated 0.
  const withData = data.clients.filter((c: any) => ["data_available", "historical"].includes(c.data_status?.state));
  const states = Array.from(new Set(data.clients.filter((c: any) => !["data_available"].includes(c.data_status?.state)).map((c: any) => c.data_status?.state)));
  const first = data.clients.find((c: any) => c.data_status && c.data_status.state !== "data_available");
  const hasData = withData.length > 0;
  return (
    <div className="space-y-6">
      <PageHeader title="Overview" subtitle={<>Last 7 days · as of {dt(data.now)}</>}
        actions={hasData ? <DataSourceBadge source={data.data_source} /> : undefined} />
      {data.clients.length === 0 && <Notice tone="blue">Your first real campaign analysis will appear here after connecting Google Ads. Add a client, then configure Google Ads on the <Link className="underline" to="/integrations">Integrations page</Link>.</Notice>}
      {first && states.length === 1 && <DataStatusNotice status={first.data_status} />}
      {first && states.length > 1 && <Notice tone="amber">Some clients have no current Google Ads data — see the Data column below.</Notice>}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        <Stat label="Spend (7d)" value={hasData ? money(totals.cost) : "—"} sub={hasData ? "clients with data" : "No Google Ads data available."} />
        <Stat label="Conversions (7d)" value={hasData ? totals.conv.toFixed(1) : "—"} sub={hasData ? `target ${totals.target}` : ""} />
        <Stat label="Gap to target" value={hasData ? (totals.conv - totals.target).toFixed(1) : "—"} />
        <Stat label="Pending approvals" value={<Link to="/approvals" className="text-brand-700">{totals.approvals}</Link>} />
        <Stat label="Open client fixes" value={totals.fixes} />
      </div>
      <Card title="Clients">
        <Table rows={data.clients} columns={[
          { key: "name", label: "Client", render: (c) => <Link className="font-medium text-brand-700 hover:underline" to={`/clients/${c.client_id}`}>{c.name}</Link> },
          { key: "cost_7d", label: "Spend 7d", render: (c) => ["data_available", "historical"].includes(c.data_status?.state) ? money(c.cost_7d) : "—" },
          { key: "conversions_7d", label: "Conv. 7d", render: (c) => ["data_available", "historical"].includes(c.data_status?.state) ? c.conversions_7d : "—" },
          { key: "weekly_target", label: "Weekly target" },
          { key: "gap", label: "Gap", render: (c) => ["data_available", "historical"].includes(c.data_status?.state) ? <span className={c.gap < 0 ? "text-rose-600" : "text-emerald-600"}>{c.gap}</span> : "—" },
          { key: "pending_approvals", label: "Approvals" },
          { key: "flags", label: "Flags", render: (c) => <div className="flex gap-1">{c.automation_frozen && <Badge tone="red">automation frozen</Badge>}{c.flagged && <Badge tone="amber">flagged</Badge>}</div> },
          { key: "data_status", label: "Data", render: (c) => {
            const st = c.data_status?.state;
            return st === "historical" ? <Badge tone="amber" title={c.data_status.message}>Historical data</Badge>
              : st === "data_available" ? <Badge tone="green">Data available</Badge>
              : st === "not_configured" ? <Badge tone="slate">Not configured</Badge>
              : st === "no_data" ? <Badge tone="blue">Connected but no data</Badge>
              : st === "failed" ? <Badge tone="red">Retrieval failed</Badge> : <Badge tone="slate">No account linked</Badge>;
          } },
        ]} empty="No clients yet." />
      </Card>
      <div className="grid gap-6 lg:grid-cols-2">
        <Card title="Emergency brake (recent)" actions={<Link to="/monitor" className="text-xs text-brand-700">Monitor →</Link>}>
          <Table rows={data.recent_emergency} empty="No emergency events." columns={[
            { key: "rule", label: "Rule", render: (e) => <span className="font-medium">{e.rule.replace(/_/g, " ")}</span> },
            { key: "action", label: "Action", render: (e) => <div className="flex gap-1"><Badge>{e.action}</Badge><StatusBadge value={e.action_mode} /></div> },
            { key: "message", label: "Detail", className: "max-w-sm", render: (e) => <span className="text-xs">{e.message}</span> },
          ]} />
        </Card>
        <Card title="Recent tasks" actions={<Link to="/tasks" className="text-xs text-brand-700">New task →</Link>}>
          <Table rows={data.recent_tasks} empty="No tasks yet." columns={[
            { key: "task_type", label: "Task", render: (t) => <Link to={`/tasks/${t.id}`} className="text-brand-700 hover:underline">{t.task_type.replace(/_/g, " ")}</Link> },
            { key: "status", label: "Status", render: (t) => <StatusBadge value={t.status} /> },
            { key: "created_at", label: "Created", render: (t) => dt(t.created_at) },
          ]} />
        </Card>
      </div>
    </div>
  );
}
