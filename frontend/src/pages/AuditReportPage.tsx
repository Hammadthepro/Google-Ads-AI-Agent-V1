import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import { useAction } from "../lib/hooks";
import { LiveFindings } from "../components/research/LiveFindings";
import { Badge, Button, Card, cx, dt, EmptyState, ErrorBox, Notice, PageHeader, Spinner, Stat, Tabs } from "../components/ui";

const EVIDENCE: Record<string, { label: string; tone: string; tip: string }> = {
  VERIFIED: { label: "Verified", tone: "green", tip: "Directly confirmed through a reliable source" },
  ESTIMATED: { label: "Estimated", tone: "amber", tip: "Based on research tools or indicators" },
  AI_GENERATED: { label: "AI-generated", tone: "violet", tip: "A suggestion or hypothesis that needs validation" },
  UNAVAILABLE: { label: "Unavailable", tone: "slate", tip: "Data could not be accessed" },
  NOT_APPLICABLE: { label: "Not applicable", tone: "slate", tip: "Does not apply to this business" },
};
const STATUS_TONE: Record<string, string> = { PASS: "green", WARN: "amber", FAIL: "red", INFO: "blue", UNAVAILABLE: "slate", NA: "slate" };
const PRIO_TONE: Record<string, string> = { HIGH: "green", MEDIUM: "blue", LOW: "slate", EXCLUDED: "red" };

export const EvidenceBadge = ({ v }: { v: string }) => <Badge tone={EVIDENCE[v]?.tone ?? "slate"} title={EVIDENCE[v]?.tip}>{EVIDENCE[v]?.label ?? v}</Badge>;

function ScoreCard({ label, s }: { label: string; s: any }) {
  const v = s?.score;
  const tone = v === null || v === undefined ? "text-slate-400" : v >= 75 ? "text-emerald-600" : v >= 55 ? "text-amber-600" : "text-rose-600";
  return (
    <div className="rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-sm">
      <div className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</div>
      <div className={cx("mt-1 text-3xl font-semibold", tone)}>{v ?? "—"}<span className="text-sm font-normal text-slate-400">{v !== null && v !== undefined ? " / 100" : ""}</span></div>
      {s && <div className="text-xs text-slate-500">{s.checks_scored} scored · {s.checks_unavailable} unavailable{s.checks_not_applicable ? ` · ${s.checks_not_applicable} n/a` : ""}</div>}
    </div>
  );
}

function Findings({ items }: { items: any[] }) {
  const [filter, setFilter] = useState("ALL");
  const areas = Array.from(new Set(items.map((i) => i.area)));
  const shown = items.filter((i) => filter === "ALL" || i.status === filter);
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-1.5" role="group" aria-label="Filter findings">
        {["ALL", "FAIL", "WARN", "PASS", "INFO", "UNAVAILABLE", "NA"].map((f) => (
          <button key={f} type="button" aria-pressed={filter === f} onClick={() => setFilter(f)}
            className={cx("rounded-full px-3 py-1 text-xs font-medium ring-1 ring-inset", filter === f ? "bg-brand-600 text-white ring-brand-600" : "bg-white text-slate-600 ring-slate-300 hover:bg-slate-50")}>
            {f === "ALL" ? `All (${items.length})` : `${f === "NA" ? "N/A" : f.charAt(0) + f.slice(1).toLowerCase()} (${items.filter((i) => i.status === f).length})`}</button>))}
      </div>
      {areas.map((a) => {
        const rows = shown.filter((i) => i.area === a);
        if (!rows.length) return null;
        return (
          <div key={a}>
            <h4 className="mb-2 text-sm font-semibold text-slate-800">{a}</h4>
            <ul className="divide-y divide-slate-100 rounded-lg border border-slate-200 bg-white">
              {rows.map((i) => (
                <li key={i.id} className="px-4 py-3 text-sm">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge tone={STATUS_TONE[i.status]}>{i.status === "NA" ? "N/A" : i.status}</Badge>
                    <span className="font-medium text-slate-900">{i.check}</span>
                    <EvidenceBadge v={i.evidence} />
                    {i.simulated && <Badge tone="violet" title="Produced by a test fixture, not a real service">test fixture data</Badge>}
                    {i.severity && <Badge tone={i.severity === "critical" || i.severity === "high" ? "red" : "slate"}>{i.severity}</Badge>}
                  </div>
                  <p className="mt-1 break-words text-slate-600">{i.finding}</p>
                  {i.recommendation && <p className="mt-1 text-slate-800"><b>Do:</b> {i.recommendation}</p>}
                  <p className="mt-1 text-xs text-slate-400">Source: {i.source || "—"} · {i.date}{i.needs ? ` · needs ${i.needs === "client" ? "client clarification" : "manual verification"}` : ""}</p>
                </li>))}
            </ul>
          </div>);
      })}
    </div>
  );
}

function KeywordReport({ rep, reportId }: { rep: any; reportId: string }) {
  const [prio, setPrio] = useState("ALL");
  const rows = rep.rows.filter((r: any) => prio === "ALL" || r.priority === prio);
  const cell = (v: any) => (v === null || v === undefined || v === "" ? <span className="text-slate-400">unavailable</span> : String(v));
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        {["ALL", "HIGH", "MEDIUM", "LOW", "EXCLUDED"].map((f) => (
          <button key={f} type="button" aria-pressed={prio === f} onClick={() => setPrio(f)}
            className={cx("rounded-full px-3 py-1 text-xs font-medium ring-1 ring-inset", prio === f ? "bg-brand-600 text-white ring-brand-600" : "bg-white text-slate-600 ring-slate-300")}>
            {f === "ALL" ? `All (${rep.rows.length})` : `${f.charAt(0) + f.slice(1).toLowerCase()} (${rep.counts[f] ?? 0})`}</button>))}
        <a className="ml-auto" href={`/api/v1/audit-reports/${reportId}/keywords.csv`}><Button size="sm">Download CSV</Button></a>
      </div>
      <div className="-mx-5 overflow-x-auto">
        <table className="min-w-full divide-y divide-slate-200 text-xs">
          <thead><tr>{["Keyword", "Service", "Intent", "Monthly searches", "Avg CPC", "Competition", "Top-of-page bid", "Match type", "Landing page", "Priority", "Evidence", "Notes"].map((h) => <th key={h} className="whitespace-nowrap px-3 py-2 text-left font-semibold uppercase tracking-wide text-slate-500">{h}</th>)}</tr></thead>
          <tbody className="divide-y divide-slate-100">
            {rows.slice(0, 300).map((r: any, i: number) => (
              <tr key={i}>
                <td className="px-3 py-2 font-medium text-slate-900">{r.keyword}</td><td className="px-3 py-2">{cell(r.service)}</td><td className="px-3 py-2">{r.intent.replace(/_/g, " ")}</td>
                <td className="px-3 py-2">{cell(r.monthly_searches)}</td><td className="px-3 py-2">{cell(r.avg_cpc)}</td><td className="px-3 py-2">{cell(r.competition)}</td>
                <td className="px-3 py-2">{cell(r.top_of_page_bid)}</td><td className="px-3 py-2">{r.match_type}</td>
                <td className="max-w-[12rem] truncate px-3 py-2" title={r.landing_page}>{cell(r.landing_page)}</td>
                <td className="px-3 py-2"><Badge tone={PRIO_TONE[r.priority]}>{r.priority}</Badge></td><td className="px-3 py-2"><EvidenceBadge v={r.evidence} /></td>
                <td className="max-w-xs px-3 py-2 text-slate-600">{r.notes}</td>
              </tr>))}
          </tbody>
        </table>
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        <div><h4 className="mb-1 text-sm font-semibold">Negative keywords (recommended)</h4>
          <div className="flex flex-wrap gap-1.5">{rep.negatives.map((n: any) => <Badge key={n.term} tone="red" title={n.reason}>{n.term} [{n.match_type}]</Badge>)}</div></div>
        <div><h4 className="mb-1 text-sm font-semibold">Negative conflicts</h4>
          {rep.negative_conflicts.length ? <ul className="list-disc pl-5 text-sm text-rose-700">{rep.negative_conflicts.map((c: any, i: number) => <li key={i}>“{c.negative}” blocks “{c.blocks}”</li>)}</ul> : <p className="text-sm text-slate-500">None found.</p>}</div>
      </div>
    </div>
  );
}

export function AuditReportPage() {
  const { id } = useParams();
  const q = useQuery({ queryKey: ["audit-report", id], queryFn: () => api.get(`/audit-reports/${id}`),
    refetchInterval: (query) => ((query.state.data as any)?.status === "RUNNING" ? 1200 : false) });
  const rerun = useAction(() => api.post(`/clients/${q.data.client_id}/audit-reports`), [["audit-reports"]]);
  if (q.isLoading || !q.data) return q.error ? <ErrorBox error={q.error} /> : <Spinner />;
  const r = q.data;
  const running = r.status === "RUNNING";
  return (
    <div className="space-y-6">
      <PageHeader title={`Audit v${r.version}`} subtitle={<>{running ? "Analysing live — results appear as each audit finishes." : <>Finished {dt(r.finished_at)}</>} · client <Link className="text-brand-700" to={`/clients/${r.client_id}`}>profile</Link></>}
        actions={<><Badge tone={running ? "blue" : r.status === "FAILED" ? "red" : "green"}>{r.status}</Badge>{!running && <Button variant="primary" disabled={rerun.isPending}
          onClick={() => rerun.mutate({}, { onSuccess: (n: any) => { window.location.href = `/audit-reports/${n.id}`; } })}>Run a new audit</Button>}</>} />
      <ErrorBox error={rerun.error} />
      {r.profile_changed_since && <Notice tone="amber">The client profile was edited after this audit. These findings reflect the earlier information — run a new audit to refresh them.</Notice>}
      {r.status === "FAILED" && <Notice tone="red">{r.error}</Notice>}
      {(running || r.status === "FAILED") && <LiveFindings url={`/audit-reports/${r.id}/events`} active={running} steps={r.steps} title={running ? "Live findings" : "Research activity"} />}
      {r.status === "COMPLETED" && (
        <details className="rounded-lg border border-slate-200 bg-white">
          <summary className="cursor-pointer px-4 py-2.5 text-sm font-medium text-slate-800">How this research was done: every step, source and limitation</summary>
          <div className="p-3"><LiveFindings url={`/audit-reports/${r.id}/events`} active={false} steps={r.steps} /></div>
        </details>)}
      {r.status === "COMPLETED" && (<>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-6">
          <ScoreCard label="Overall client readiness" s={r.scores.overall} />
          <ScoreCard label="Client & business" s={r.scores.client} /><ScoreCard label="Website readiness" s={r.scores.website} />
          <ScoreCard label="Business Profile" s={r.scores.gbp} /><ScoreCard label="Competitor positioning" s={r.scores.competitors} /><ScoreCard label="Keyword readiness" s={r.scores.keywords} />
        </div>
        <Notice tone={r.scores.overall.score >= 75 ? "green" : r.scores.overall.score >= 55 ? "amber" : "red"}><b>{r.scores.overall.label}.</b> <span className="opacity-80">{r.scores.overall.criteria}</span></Notice>
        {r.source_type !== "LIVE" && <Notice tone="amber">Some checks could not use live data (integration not configured or a request failed) — they are listed as <b>unavailable</b> and do not count toward the score. Check the <a className="underline" href="/integrations">Integrations page</a>.</Notice>}
        {r.changes?.previous_version && (
          <Card title={`Compared with audit v${r.changes.previous_version} (${dt(r.changes.previous_date)})`}>
            <div className="grid gap-4 text-sm md:grid-cols-4">
              <Stat label="Resolved / improved" value={r.changes.resolved.length} /><Stat label="Worse" value={r.changes.worse.length} /><Stat label="New issues" value={r.changes.new.length} /><Stat label="Unchanged" value={r.changes.unchanged} />
            </div>
            <ul className="mt-3 space-y-1 text-sm">
              {r.changes.resolved.slice(0, 8).map((c: any) => <li key={c.id} className="text-emerald-700">▲ {c.check} ({c.from} → {c.to})</li>)}
              {r.changes.worse.slice(0, 8).map((c: any) => <li key={c.id} className="text-rose-700">▼ {c.check} ({c.from} → {c.to})</li>)}
              {r.changes.new.slice(0, 8).map((c: any) => <li key={c.id} className="text-amber-700">＋ New: {c.check} ({c.status})</li>)}
            </ul>
          </Card>)}
        <Card title="Final audit summary">
          <dl className="space-y-4 text-sm">
            {Object.entries(r.summary).filter(([k]) => k !== "method_note").sort(([a], [b]) => parseInt(a) - parseInt(b)).map(([k, v]: any) => (
              <div key={k}><dt className="font-semibold text-slate-800">{parseInt(k)}. {k.replace(/^\d+_/, "").replace(/_/g, " ").replace(/^./, (c: string) => c.toUpperCase())}</dt>
                <dd className="mt-1 text-slate-600">{Array.isArray(v) ? (v.length ? <ul className="list-disc pl-5">{v.map((x: string, i: number) => <li key={i}>{x}</li>)}</ul> : "None") : v}</dd></div>))}
          </dl>
        </Card>
        <Card title="Recommendations">
          <div className="space-y-4 text-sm">
            {(["critical", "high", "medium", "low"] as const).map((sev) => (
              <div key={sev}><h4 className="mb-1 font-semibold capitalize text-slate-800">{sev === "low" ? "Low-priority opportunities" : `${sev === "critical" ? "Critical issues that must be addressed" : sev === "high" ? "High-priority improvements" : "Medium-priority improvements"}`} ({r.recommendations[sev].length})</h4>
                {r.recommendations[sev].length ? <ul className="list-disc space-y-1 pl-5 text-slate-600">{r.recommendations[sev].map((x: any, i: number) => <li key={i}>{x.text} <span className="text-xs text-slate-400">— {x.because.slice(0, 90)}</span> <EvidenceBadge v={x.evidence} /></li>)}</ul> : <p className="text-slate-400">None</p>}</div>))}
            <div className="grid gap-4 md:grid-cols-2">
              <div><h4 className="mb-1 font-semibold">Needs client clarification ({r.recommendations.needs_client_clarification.length})</h4>
                <ul className="list-disc space-y-1 pl-5 text-slate-600">{r.recommendations.needs_client_clarification.map((x: any, i: number) => <li key={i}>{x.text}</li>)}</ul></div>
              <div><h4 className="mb-1 font-semibold">Requires manual verification ({r.recommendations.manual_verification.length})</h4>
                <ul className="list-disc space-y-1 pl-5 text-slate-600">{r.recommendations.manual_verification.map((x: any, i: number) => <li key={i}>{x.text}</li>)}</ul></div>
            </div>
            <div><h4 className="mb-1 font-semibold">Recommended next steps</h4><ol className="list-decimal space-y-1 pl-5 text-slate-600">{r.recommendations.next_steps.map((x: string, i: number) => <li key={i}>{x}</li>)}</ol></div>
          </div>
        </Card>
        <Card title="Detailed findings">
          <Tabs tabs={[
            ...(["client", "website", "gbp", "competitors", "keywords"] as const).map((k) => ({ key: k, label: `${r.sections[k].title}${r.sections[k].score !== null ? ` · ${r.sections[k].score}` : ""}`,
              content: <div className="space-y-4">
                {k === "competitors" && r.sections.competitors.suggested_competitors?.length > 0 && <Notice>Other local listings found (not yet analysed): {r.sections.competitors.suggested_competitors.map((s: any) => s.name).join(", ")}</Notice>}
                {k === "keywords" && <KeywordReport rep={r.sections.keywords.report} reportId={r.id} />}
                {r.sections[k].findings.length ? <Findings items={r.sections[k].findings} /> : <EmptyState message="No findings." />}</div> })),
            { key: "snapshot", label: "Client info used", content: <pre className="whitespace-pre-wrap text-xs text-slate-600">{JSON.stringify(r.input_snapshot, null, 2)}</pre> },
          ]} />
        </Card>
      </>)}
    </div>
  );
}
