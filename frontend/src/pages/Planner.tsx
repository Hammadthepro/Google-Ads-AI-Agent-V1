import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api/client";
import { Badge, Card, DataSourceBadge, dt, KV, Notice, PageHeader, Spinner, StatusBadge, Table } from "../components/ui";

export function Planner() {
  const nav = useNavigate();
  const q = useQuery({ queryKey: ["plans"], queryFn: () => api.get("/plans") });
  return (
    <div className="space-y-6">
      <PageHeader title="Campaign plans" subtitle="AI drafts, the independent AI challenger, and comparisons with the team's own plan. Start a plan from Tasks."
        actions={<Link to="/tasks" className="text-sm font-medium text-brand-700">New plan → Tasks</Link>} />
      <Card>
        {q.isLoading ? <Spinner /> : (
          <Table rows={q.data ?? []} onRowClick={(p) => nav(`/planner/${p.id}`)} columns={[
            { key: "scope", label: "Scope", render: (p) => <Badge tone="blue">{p.scope}</Badge> },
            { key: "name", label: "Draft", render: (p) => p.draft.campaign_name || p.draft.name || p.draft.headlines?.[0] },
            { key: "challenger", label: "Challenger", render: (p) => <StatusBadge value={p.challenger?.verdict} /> },
            { key: "objections", label: "Objections", render: (p) => p.challenger?.objections?.length ?? 0 },
            { key: "ai", label: "Model", render: (p) => <span className="text-xs">{p.draft_ai?.model} · {p.draft_ai?.prompt}</span> },
            { key: "status", label: "Status", render: (p) => <StatusBadge value={p.status} /> },
            { key: "created_at", label: "Created", render: (p) => dt(p.created_at) },
          ]} />
        )}
      </Card>
    </div>
  );
}

export function AdPreview({ ad, url }: { ad: any; url?: string }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-3">
      <div className="text-[11px] text-slate-500">Ad · {url?.replace(/^https?:\/\//, "")}{ad.path1 ? `/${ad.path1}` : ""}{ad.path2 ? `/${ad.path2}` : ""}</div>
      <div className="mt-0.5 text-base leading-snug text-[#1a0dab]">{ad.headlines.slice(0, 3).join(" | ")}</div>
      <div className="mt-0.5 text-sm text-slate-600">{ad.descriptions.slice(0, 2).join(" ")}</div>
      <details className="mt-2 text-xs"><summary className="cursor-pointer text-slate-500">All {ad.headlines.length} headlines · {ad.descriptions.length} descriptions</summary>
        <ul className="mt-1 grid gap-0.5 sm:grid-cols-2">{ad.headlines.map((h: string) => <li key={h}>{h} <span className="text-slate-400">({h.length})</span></li>)}</ul>
        <ul className="mt-1 space-y-0.5">{ad.descriptions.map((d: string) => <li key={d}>{d} <span className="text-slate-400">({d.length})</span></li>)}</ul></details>
    </div>
  );
}

export function DraftView({ draft }: { draft: any }) {
  const groups = draft.ad_groups ?? (draft.keywords ? [draft] : []);
  return (
    <div className="space-y-4">
      {draft.campaign_name && <KV items={[["Campaign", draft.campaign_name], ["Bidding", draft.bidding_strategy], ["Location", draft.location_note || "—"],
        ["Schedule", draft.ad_schedule_note || "—"], ["Call asset", draft.use_call_asset ? "yes" : "no"], ["Callouts", draft.callouts?.join(", ") || "—"]]} />}
      {draft.rationale && <Notice>{draft.rationale}</Notice>}
      {groups.map((g: any) => (
        <div key={g.name} className="rounded-lg border border-slate-200 p-4">
          <div className="mb-2 flex flex-wrap items-center gap-2"><span className="font-medium">{g.name}</span><span className="text-xs text-slate-500">→ {g.landing_page}</span></div>
          <div className="mb-3 flex flex-wrap gap-1">{g.keywords.map((k: any, i: number) => <Badge key={i} tone={k.match_type === "EXACT" ? "blue" : k.match_type === "BROAD" ? "red" : "slate"}>{k.match_type === "EXACT" ? `[${k.text}]` : k.match_type === "PHRASE" ? `"${k.text}"` : k.text}</Badge>)}</div>
          <div className="grid gap-2 md:grid-cols-2">{g.ads.map((a: any, i: number) => <AdPreview key={i} ad={a} url={g.landing_page} />)}</div>
        </div>
      ))}
      {draft.headlines && !groups.length && <AdPreview ad={draft} />}
      {draft.campaign_negatives?.length > 0 && <div className="text-sm"><b>Campaign negatives:</b> {draft.campaign_negatives.join(", ")}</div>}
    </div>
  );
}

export function ChallengerView({ ch }: { ch: any }) {
  if (!ch?.verdict) return <Notice>No challenger review.</Notice>;
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 text-sm">Verdict <StatusBadge value={ch.verdict} /></div>
      <Table rows={ch.objections} empty="No objections." columns={[{ key: "severity", label: "Severity", render: (o) => <StatusBadge value={o.severity} /> },
        { key: "category", label: "Category" }, { key: "target", label: "Target", className: "max-w-[12rem] truncate" }, { key: "message", label: "Objection" }, { key: "suggestion", label: "Suggestion" }]} />
      {ch.agreements?.length > 0 && <div className="text-sm text-emerald-700">Agrees: {ch.agreements.join(" · ")}</div>}
    </div>
  );
}

export function HumanComparison({ hc }: { hc: any }) {
  if (!hc || !Object.keys(hc).length) return <Notice>No human plan was provided.</Notice>;
  const col = (title: string, items: string[], tone: string) => (
    <div><div className="mb-1 text-xs font-semibold uppercase text-slate-500">{title} ({items.length})</div>
      <div className="flex flex-wrap gap-1">{items.map((x) => <Badge key={x} tone={tone}>{x}</Badge>)}</div></div>);
  return (
    <div className="space-y-4">
      <div className="grid gap-4 md:grid-cols-3">{col("Agreed", hc.agreed, "green")}{col("AI only", hc.ai_only, "blue")}{col("Human only", hc.human_only, "violet")}</div>
      <Table rows={hc.disagreements} empty="No disagreements." columns={[{ key: "topic", label: "Topic" }, { key: "human", label: "Human" }, { key: "ai", label: "AI" }, { key: "recommended_resolution", label: "Recommended resolution" }]} />
      {hc.applied_human_decisions?.length > 0 && <Notice tone="green">Human decisions applied (AI never silently overrides them): {hc.applied_human_decisions.join("; ")}</Notice>}
    </div>
  );
}

export function PlanDetail() {
  const { id } = useParams();
  const q = useQuery({ queryKey: ["plan", id], queryFn: () => api.get(`/plans/${id}`) });
  if (q.isLoading || !q.data) return <Spinner />;
  const p = q.data;
  return (
    <div className="space-y-6">
      <PageHeader title={p.draft.campaign_name || p.draft.name || "Ad draft"} subtitle={<>Plan v{p.version} · <Link className="text-brand-700" to={`/tasks/${p.task_id}`}>task</Link></>}
        actions={<><StatusBadge value={p.status} /><DataSourceBadge source={p.source_type} /></>} />
      <Card title="AI provenance"><KV items={[["Drafting AI", `${p.draft_ai?.provider} · ${p.draft_ai?.model}`], ["Draft prompt", p.draft_ai?.prompt],
        ["Challenger AI", `${p.challenger_ai?.provider ?? "—"} · ${p.challenger_ai?.model ?? ""}`], ["Challenger prompt", p.challenger_ai?.prompt ?? "—"],
        ["Playbook rules applied", p.playbook_rule_ids.length], ["Version (after AI fixes)", p.version]]} /></Card>
      <Card title="AI draft"><DraftView draft={p.draft} /></Card>
      <Card title="AI challenger (independent review)"><ChallengerView ch={p.challenger} /></Card>
      <Card title="Human plan comparison"><HumanComparison hc={p.human_comparison} /></Card>
    </div>
  );
}
