import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { useAuth } from "../lib/auth";
import { clientOptions, useAction, useClients } from "../lib/hooks";
import { Badge, Button, Card, DataSourceBadge, DataStatusNotice, dt, ErrorBox, money, Notice, PageHeader, pct, Select, Spinner, MetricStrip, StatusBadge, Table, Tabs } from "../components/ui";

function Spark({ points }: { points: number[] }) {
  if (points.length < 2) return null;
  const max = Math.max(...points, 1);
  const d = points.map((p, i) => `${(i / (points.length - 1)) * 100},${30 - (p / max) * 28}`).join(" ");
  return <svg viewBox="0 0 100 30" className="h-6 w-28"><polyline fill="none" stroke="#4f6ef7" strokeWidth="1.5" points={d} /></svg>;
}

function CampaignSeries({ id }: { id: string }) {
  const q = useQuery({ queryKey: ["series", id], queryFn: () => api.get(`/monitor/campaign/${id}/series`) });
  return <Spark points={(q.data ?? []).map((r: any) => r.cost)} />;
}

export default function Monitor() {
  const clients = useClients();
  const { can } = useAuth();
  const nav = useNavigate();
  const [clientId, setClientId] = useState("");
  useEffect(() => { if (!clientId && clients.data?.length) setClientId(clients.data[0].id); }, [clients.data]);
  const m = useQuery({ queryKey: ["monitor", clientId], enabled: !!clientId, queryFn: () => api.get(`/monitor/${clientId}`) });
  const terms = useQuery({ queryKey: ["terms", clientId], enabled: !!clientId, queryFn: () => api.get(`/monitor/${clientId}/search-terms`) });
  const recs = useQuery({ queryKey: ["recs", clientId], enabled: !!clientId, queryFn: () => api.get(`/recommendations?client_id=${clientId}`) });
  const events = useQuery({ queryKey: ["emergency"], queryFn: () => api.get("/emergency/events") });
  const sync = useAction(() => api.post(`/monitor/${clientId}/sync`), [["monitor", clientId], ["terms", clientId]]);
  const opt = useAction(() => api.post(`/recommendations/run/${clientId}`), [["recs", clientId]]);
  const brake = useAction(() => api.post(`/emergency/run`), [["emergency"]]);
  const toApproval = useAction((rid) => api.post(`/recommendations/${rid}/approval`), [["recs", clientId]]);
  const data = m.data;
  const totals = (data?.campaigns ?? []).reduce((a: any, c: any) => ({ cost: a.cost + c.cost, conv: a.conv + c.conversions, clicks: a.clicks + c.clicks }), { cost: 0, conv: 0, clicks: 0 });
  return (
    <div className="space-y-6">
      <PageHeader title="Monitoring" subtitle="Daily sync re-downloads the last 30 days because leads are recorded days after the click."
        actions={<>
          <div className="w-64"><Select value={clientId} onChange={(e) => setClientId(e.target.value)} options={clientOptions(clients.data)} /></div>
          {can("monitor.run") && <Button onClick={() => sync.mutate({})} disabled={sync.isPending}>{sync.isPending ? "Syncing…" : "Sync now"}</Button>}
          {can("monitor.run") && <Button onClick={() => opt.mutate({})} disabled={opt.isPending}>Run optimisation</Button>}
          {can("monitor.run") && <Button onClick={() => brake.mutate({})} disabled={brake.isPending}>Run emergency check</Button>}
          {data && <DataSourceBadge source={data.data_source} />}</>} />
      <ErrorBox error={sync.error || opt.error || brake.error || toApproval.error} />
      {opt.data && <Notice tone="violet">Optimisation ({opt.data.mode} mode): {opt.data.negatives_proposed} negatives proposed, {opt.data.negatives_applied} applied, {opt.data.negatives_held} held; {opt.data.big_changes} big-change proposals, {opt.data.held} held.</Notice>}
      {!data ? <Spinner /> : (
        <>
          <DataStatusNotice status={data.data_status} />
          <MetricStrip items={[
            { label: "Spend (30d)", value: money(totals.cost), emphasis: true },
            { label: "Conversions (30d)", value: totals.conv.toFixed(1), emphasis: true },
            { label: "Wasted spend (30d)", value: money(data.wasted_spend.total), sub: `terms with ≥${data.wasted_spend.min_clicks} clicks, 0 conv.` },
            { label: "Calls (30d)", value: data.calls.calls, sub: `${data.calls.missed} missed · ${data.calls.short} under ${data.calls.min_seconds}s` },
          ]} />
          <Card title="Campaigns (30 days)">
            <Table rows={data.campaigns} columns={[
              { key: "name", label: "Campaign", render: (c) => <span className="font-medium">{c.name} {c.read_only && <Badge>read-only</Badge>}</span> },
              { key: "status", label: "Status", render: (c) => <StatusBadge value={c.status} /> },
              { key: "trend", label: "Spend trend", render: (c) => <CampaignSeries id={c.campaign_id} /> },
              { key: "cost", label: "Cost", render: (c) => money(c.cost) }, { key: "clicks", label: "Clicks" }, { key: "conversions", label: "Conv." },
              { key: "cost_per_conversion", label: "CPA", render: (c) => money(c.cost_per_conversion) }, { key: "ctr", label: "CTR", render: (c) => pct(c.ctr) },
              { key: "search_impression_share", label: "Impr. share", render: (c) => pct(c.search_impression_share) },
              { key: "budget_lost_is", label: "Lost (budget)", render: (c) => pct(c.budget_lost_is) }, { key: "rank_lost_is", label: "Lost (rank)", render: (c) => pct(c.rank_lost_is) },
              { key: "source_type", label: "Data", render: (c) => <DataSourceBadge source={c.source_type} /> },
            ]} />
          </Card>
          <Tabs tabs={[
            { key: "recs", label: `Recommendations (${recs.data?.length ?? 0})`, content: (
              <Card>
                <Table rows={recs.data ?? []} columns={[
                  { key: "title", label: "Recommendation", className: "max-w-md" }, { key: "size", label: "Size", render: (r) => <Badge tone={r.size === "SMALL" ? "slate" : "blue"}>{r.size === "SMALL" ? "small (auto-eligible)" : "big (approval)"}</Badge> },
                  { key: "decision", label: "Enough data", render: (r) => <StatusBadge value={r.decision} /> },
                  { key: "status", label: "Status", render: (r) => <StatusBadge value={r.status} /> },
                  { key: "hold_reason", label: "Held because", className: "max-w-xs text-xs" },
                  { key: "act", label: "", render: (r) => r.approval_id ? <Button size="sm" onClick={() => nav(`/approvals/${r.approval_id}`)}>Open approval</Button>
                    : (r.decision === "GO" || r.category === "NEGATIVE_KEYWORD") && ["OPEN", "SHADOW"].includes(r.status) && can("approvals.submit") && !["RANK", "REPLACE_AD"].includes(r.category)
                      ? <Button size="sm" onClick={() => toApproval.mutate(r.id)}>Create approval</Button> : null },
                ]} />
              </Card>) },
            { key: "waste", label: "Wasted spend", content: <Card><Table rows={data.wasted_spend.terms} columns={[{ key: "term", label: "Search term" }, { key: "campaign", label: "Campaign" }, { key: "clicks", label: "Clicks" }, { key: "cost", label: "Cost", render: (t) => money(t.cost) }, { key: "conversions_90d", label: "Conv. 90d" }]} /></Card> },
            { key: "terms", label: "Search terms", content: <Card><Table rows={terms.data ?? []} columns={[{ key: "term", label: "Search term" }, { key: "matched_keyword", label: "Keyword" }, { key: "campaign", label: "Campaign" }, { key: "clicks", label: "Clicks" }, { key: "cost", label: "Cost", render: (t) => money(t.cost) }, { key: "conversions", label: "Conv." }]} /></Card> },
            { key: "changes", label: "Change history", content: <Card><Table rows={data.change_events} columns={[{ key: "changed_at", label: "When", render: (c) => dt(c.changed_at) }, { key: "user", label: "User" }, { key: "client_type", label: "Via" }, { key: "change", label: "Change" }, { key: "resource", label: "Resource", className: "text-xs" }, { key: "made_outside_app", label: "", render: (c) => c.made_outside_app ? <Badge tone="amber">manual change outside app</Badge> : <Badge>this app</Badge> }]} /></Card> },
            { key: "brake", label: "Emergency brake", content: <Card><Notice tone="violet">Deterministic hourly rules, no AI. By default the brake only freezes automation inside the app; changing real campaigns requires EMERGENCY_BRAKE_WRITE_MODE=real.</Notice><div className="mt-3"><Table rows={(events.data ?? []).filter((e: any) => e.client_id === clientId)} empty="No events for this client." columns={[{ key: "created_at", label: "When", render: (e) => dt(e.created_at) }, { key: "rule", label: "Rule" }, { key: "severity", label: "Severity", render: (e) => <StatusBadge value={e.severity} /> }, { key: "action", label: "Action" }, { key: "action_mode", label: "Mode", render: (e) => <StatusBadge value={e.action_mode} /> }, { key: "message", label: "Detail", className: "max-w-md text-xs" }]} /></div></Card> },
          ]} />
        </>
      )}
    </div>
  );
}
