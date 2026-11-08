import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { useAuth } from "../lib/auth";
import { clientOptions, useAction, useClients } from "../lib/hooks";
import { Badge, Button, Card, DataSourceBadge, ErrorBox, money, Notice, PageHeader, Select, Spinner, Stat, StatusBadge, Table } from "../components/ui";

export default function Weekly() {
  const clients = useClients();
  const { can } = useAuth();
  const [clientId, setClientId] = useState("");
  const [reportId, setReportId] = useState("");
  useEffect(() => { if (!clientId && clients.data?.length) setClientId(clients.data[0].id); }, [clients.data]);
  const list = useQuery({ queryKey: ["reports", clientId], enabled: !!clientId, queryFn: () => api.get(`/reports?client_id=${clientId}`) });
  useEffect(() => { setReportId(list.data?.[0]?.id ?? ""); }, [list.data]);
  const rep = useQuery({ queryKey: ["report", reportId], enabled: !!reportId, queryFn: () => api.get(`/reports/${reportId}`) });
  const gen = useAction(() => api.post(`/reports/generate/${clientId}`), [["reports", clientId]]);
  const r = rep.data;
  const t = r?.metrics?.totals;
  const s = r?.sections;
  return (
    <div className="space-y-6">
      <PageHeader title="Weekly Review" subtitle="Rolling four weeks. Numbers come from application calculations; the AI only writes the summary."
        actions={<><div className="w-64"><Select value={clientId} onChange={(e) => setClientId(e.target.value)} options={clientOptions(clients.data)} /></div>
          {list.data?.length > 0 && <div className="w-56"><Select value={reportId} onChange={(e) => setReportId(e.target.value)} options={list.data.map((x: any) => ({ value: x.id, label: `Week ending ${x.week_end}` }))} /></div>}
          {can("monitor.run") && <Button variant="primary" onClick={() => gen.mutate({}, { onSuccess: (x: any) => setReportId(x.id) })} disabled={gen.isPending}>{gen.isPending ? "Generating…" : "Generate now"}</Button>}</>} />
      <ErrorBox error={gen.error} />
      {!r ? (list.isLoading || rep.isLoading ? <Spinner /> : <Notice>No report yet — generate one.</Notice>) : (
        <>
          <div className="flex items-center gap-2 text-sm text-slate-500">Week {r.week_start} → {r.week_end} <DataSourceBadge source={r.source_type} /></div>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-6">
            <Stat label="Campaign targets (sum)" value={t.target} sub={t.client_target ? `Client target ${t.client_target}${t.client_gap !== null && t.client_gap !== undefined ? ` · ${t.client_gap < 0 ? Math.abs(t.client_gap) + " short" : "on target"}` : ""}` : "No client-level target set"} /><Stat label="Actual conversions" value={t.conversions} />
            <Stat label="Gap" value={<span className={t.gap < 0 ? "text-rose-600" : "text-emerald-600"}>{t.gap < 0 ? `${Math.abs(t.gap)} short` : `+${t.gap}`}</span>} />
            <Stat label="Forms / calls" value={`${t.forms} / ${t.calls}`} /><Stat label="Spend" value={money(t.cost)} /><Stat label="Wasted spend (30d)" value={money(t.wasted_spend)} />
          </div>
          <Card title="Summary (AI-written, figures from the app)"><p className="whitespace-pre-wrap text-sm text-slate-700">{r.ai_summary}</p></Card>
          {s.tracking_warnings.length > 0 && <Notice tone="amber"><b>Warnings:</b> {s.tracking_warnings.join(" · ")}</Notice>}
          {r.metrics.campaigns.map((c: any) => (
            <Card key={c.campaign_id} title={<span className="flex items-center gap-2">{c.name} <StatusBadge value={c.tracking_state} /></span>}
              actions={<span className="text-sm">{c.actual} / {c.target} {c.gap !== null && <span className={c.gap < 0 ? "text-rose-600" : "text-emerald-600"}>({c.gap < 0 ? `${Math.abs(c.gap)} short` : "on target"})</span>}</span>}>
              <div className="mb-3 flex flex-wrap items-center gap-4 text-sm text-slate-600">
                <span>4-week trend: {c.trend.join(" → ")}</span><span>Forms {c.forms} · Calls {c.calls}</span><span>CPC {money(c.metrics.cpc)} · CPL {money(c.metrics.cpl)}</span>
              </div>
              <Table rows={c.diagnosis} columns={[{ key: "step", label: "#" }, { key: "question", label: "Diagnosis" },
                { key: "rules", label: "Rules", render: (d) => d.rules ? <Badge tone="amber">yes</Badge> : "—" }, { key: "ai", label: "AI", render: (d) => d.ai ? <Badge tone="amber">yes</Badge> : "—" },
                { key: "recommended", label: "Action", render: (d) => d.recommended ? <Badge tone="red">both agree → recommended</Badge> : "" }]} />
              {c.recommended_actions.length > 0 && <ul className="mt-3 list-disc pl-5 text-sm">{c.recommended_actions.map((a: string) => <li key={a}>{a}</li>)}</ul>}
            </Card>
          ))}
          <div className="grid gap-6 lg:grid-cols-2">
            <Card title="Open client fixes"><Table rows={s.open_fixes} columns={[{ key: "issue", label: "Issue" }, { key: "priority", label: "Priority", render: (f) => <StatusBadge value={f.priority} /> }, { key: "since", label: "Since" }]} /></Card>
            <Card title="Emergency brake actions"><Table rows={s.emergency_actions} empty="None this week." columns={[{ key: "rule", label: "Rule" }, { key: "action", label: "Action" }, { key: "mode", label: "Mode", render: (e) => <StatusBadge value={e.mode} /> }]} /></Card>
            <Card title="Changes needing approval">
              <Table rows={s.recommendations.big_changes_proposed} empty="None." columns={[{ key: "title", label: "Proposal" }, { key: "status", label: "Status", render: (x) => <StatusBadge value={x.status} /> },
                { key: "a", label: "", render: (x) => x.approval_id ? <Link className="text-brand-700" to={`/approvals/${x.approval_id}`}>approve / reject →</Link> : <Link className="text-brand-700" to="/monitor">create approval →</Link> }]} />
            </Card>
            <Card title="Small changes & holds">
              <div className="text-sm"><b>Applied automatically:</b> {s.recommendations.small_changes_made.length || "none"}</div>
              <div className="mt-1 text-sm"><b>Shadow-mode proposals:</b> {s.recommendations.proposed_shadow.length}</div>
              <ul className="mt-2 list-disc pl-5 text-xs text-slate-600">{s.recommendations.held.map((h: any) => <li key={h.title}><b>Held:</b> {h.title} — {h.reason}</li>)}</ul>
            </Card>
            <Card title="Approved & rejected this week">
              <ul className="space-y-1 text-sm">{s.approved_changes.map((x: any) => <li key={x.at}>✓ {x.title}</li>)}{s.rejected_changes.map((x: any) => <li key={x.title}>✕ {x.title} — <span className="text-slate-500">{x.reason}</span></li>)}</ul>
            </Card>
            <Card title="Experiments"><Table rows={s.experiments} empty="None active." columns={[{ key: "name", label: "Experiment" }, { key: "status", label: "Status", render: (x) => <StatusBadge value={x.status} /> }]} /></Card>
          </div>
          <Card title="Business breakdown"><Table rows={Object.entries(s.business_breakdown).map(([k, v]: any) => ({ id: k, business: k, ...v }))}
            columns={[{ key: "business", label: "Business" }, { key: "target", label: "Target" }, { key: "conversions", label: "Conversions" }, { key: "cost", label: "Spend", render: (x) => money(x.cost) }]} /></Card>
        </>
      )}
    </div>
  );
}
