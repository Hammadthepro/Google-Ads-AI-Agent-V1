import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import { useAction } from "../lib/hooks";
import { LiveFindings } from "../components/research/LiveFindings";
import { Badge, Button, Card, cx, dt, EmptyState, ErrorBox, MetricStrip, Notice, PageHeader, Skeleton, Tabs } from "../components/ui";

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

const scoreTone = (v?: number | null) => (v === null || v === undefined ? "slate" : v >= 75 ? "green" : v >= 55 ? "amber" : "red");
const TONE_TEXT: Record<string, string> = { green: "text-emerald-700", amber: "text-amber-700", red: "text-rose-700", slate: "text-slate-400" };
const TONE_BAR: Record<string, string> = { green: "bg-emerald-500", amber: "bg-amber-500", red: "bg-rose-500", slate: "bg-slate-200" };

/** One section score as a labelled bar on the 0–100 scale the report already uses; "—" when nothing could be scored. */
function ScoreRow({ label, s }: { label: string; s: any }) {
  const v = s?.score;
  const tone = scoreTone(v);
  return (
    <div className="grid grid-cols-[minmax(0,1fr)_48px] items-center gap-x-4 gap-y-1 py-2.5 sm:grid-cols-[180px_minmax(0,1fr)_48px]">
      <div className="text-[13px] font-medium text-slate-800">{label}</div>
      <div className="col-span-2 row-start-2 h-1.5 overflow-hidden rounded-full bg-slate-100 sm:col-span-1 sm:row-start-auto" aria-hidden>
        {v !== null && v !== undefined && <div className={cx("h-full rounded-full", TONE_BAR[tone])} style={{ width: `${Math.max(0, Math.min(v, 100))}%` }} />}
      </div>
      <div className={cx("tabular text-right text-sm font-semibold", TONE_TEXT[tone])}>{v ?? "—"}</div>
      {s && <div className="col-span-2 text-2xs text-slate-500 sm:col-span-3 sm:pl-[196px]">{s.checks_scored} scored · {s.checks_unavailable} unavailable{s.checks_not_applicable ? ` · ${s.checks_not_applicable} n/a` : ""}</div>}
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
            className={cx("rounded-md px-2.5 py-1 text-xs font-medium ring-1 ring-inset transition-colors", filter === f ? "bg-slate-900 text-white ring-slate-900" : "bg-white text-slate-600 ring-slate-200 hover:bg-slate-50 hover:ring-slate-300")}>
            {f === "ALL" ? `All (${items.length})` : `${f === "NA" ? "N/A" : f.charAt(0) + f.slice(1).toLowerCase()} (${items.filter((i) => i.status === f).length})`}</button>))}
      </div>
      {areas.map((a) => {
        const rows = shown.filter((i) => i.area === a);
        if (!rows.length) return null;
        return (
          <div key={a}>
            <h4 className="mb-2 text-[13px] font-semibold text-slate-900">{a}</h4>
            <ul className="divide-y divide-slate-100 rounded-lg border border-slate-200 bg-white">
              {rows.map((i) => (
                <li key={i.id} className="px-4 py-3.5 text-[13px]">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge tone={STATUS_TONE[i.status]}>{i.status === "NA" ? "N/A" : i.status}</Badge>
                    <span className="font-medium text-slate-900">{i.check}</span>
                    <EvidenceBadge v={i.evidence} />
                    {i.simulated && <Badge tone="violet" title="Produced by a test fixture, not a real service">test fixture data</Badge>}
                    {i.severity && <Badge tone={i.severity === "critical" || i.severity === "high" ? "red" : "slate"}>{i.severity}</Badge>}
                  </div>
                  <p className="mt-1.5 max-w-read break-words leading-5 text-slate-600">{i.finding}</p>
                  {i.recommendation && <p className="mt-1.5 max-w-read leading-5 text-slate-800"><b className="font-medium">Do:</b> {i.recommendation}</p>}
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
            className={cx("rounded-md px-2.5 py-1 text-xs font-medium ring-1 ring-inset transition-colors", prio === f ? "bg-slate-900 text-white ring-slate-900" : "bg-white text-slate-600 ring-slate-200 hover:bg-slate-50 hover:ring-slate-300")}>
            {f === "ALL" ? `All (${rep.rows.length})` : `${f.charAt(0) + f.slice(1).toLowerCase()} (${rep.counts[f] ?? 0})`}</button>))}
        <a className="ml-auto inline-flex h-7 items-center rounded-md bg-white px-2.5 text-xs font-medium text-slate-800 ring-1 ring-inset ring-slate-300 hover:bg-slate-50" href={`/api/v1/audit-reports/${reportId}/keywords.csv`}>Download CSV</a>
      </div>
      <div className="-mx-5 overflow-x-auto">
        <table className="min-w-full divide-y divide-slate-200 text-xs">
          <thead className="bg-slate-50/70"><tr>{["Keyword", "Service", "Intent", "Monthly searches", "Avg CPC", "Competition", "Top-of-page bid", "Match type", "Landing page", "Priority", "Evidence", "Notes"].map((h) => <th key={h} scope="col" className="whitespace-nowrap px-3 py-2 text-left font-medium text-slate-500">{h}</th>)}</tr></thead>
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
        <div><h4 className="mb-2 text-[13px] font-semibold text-slate-900">Negative keywords (recommended)</h4>
          <div className="flex flex-wrap gap-1.5">{rep.negatives.map((n: any) => <Badge key={n.term} tone="red" title={n.reason}>{n.term} [{n.match_type}]</Badge>)}</div></div>
        <div><h4 className="mb-2 text-[13px] font-semibold text-slate-900">Negative conflicts</h4>
          {rep.negative_conflicts.length ? <ul className="list-disc pl-5 text-sm text-rose-700">{rep.negative_conflicts.map((c: any, i: number) => <li key={i}>“{c.negative}” blocks “{c.blocks}”</li>)}</ul> : <p className="text-sm text-slate-500">None found.</p>}</div>
      </div>
    </div>
  );
}

const SEVERITY: { key: "critical" | "high" | "medium" | "low"; title: string; bar: string }[] = [
  { key: "critical", title: "Critical issues that must be addressed", bar: "border-rose-600" },
  { key: "high", title: "High-priority improvements", bar: "border-amber-500" },
  { key: "medium", title: "Medium-priority improvements", bar: "border-brand-500" },
  { key: "low", title: "Low-priority opportunities", bar: "border-slate-300" },
];

export function AuditReportPage() {
  const { id } = useParams();
  const q = useQuery({ queryKey: ["audit-report", id], queryFn: () => api.get(`/audit-reports/${id}`),
    refetchInterval: (query) => ((query.state.data as any)?.status === "RUNNING" ? 1200 : false) });
  const rerun = useAction(() => api.post(`/clients/${q.data.client_id}/audit-reports`), [["audit-reports"]]);
  if (q.isLoading || !q.data) return q.error ? <ErrorBox error={q.error} /> : <div><Skeleton lines={3} /><div className="mt-6 h-[440px] rounded-lg bg-ink" /></div>;
  const r = q.data;
  const running = r.status === "RUNNING";
  const overall = r.scores?.overall;
  return (
    <div className="space-y-8">
      <PageHeader title={`Audit v${r.version}`} subtitle={<>{running ? "Analysing live — results appear as each audit finishes." : <>Finished {dt(r.finished_at)}</>} · client <Link className="font-medium text-brand-700 hover:underline" to={`/clients/${r.client_id}`}>profile</Link></>}
        actions={<><Badge tone={running ? "blue" : r.status === "FAILED" ? "red" : "green"} dot>{r.status}</Badge>{!running && <Button variant="primary" disabled={rerun.isPending}
          onClick={() => rerun.mutate({}, { onSuccess: (n: any) => { window.location.href = `/audit-reports/${n.id}`; } })}>Run a new audit</Button>}</>} />
      <ErrorBox error={rerun.error} />
      {r.profile_changed_since && <Notice tone="amber">The client profile was edited after this audit. These findings reflect the earlier information — run a new audit to refresh them.</Notice>}
      {r.status === "FAILED" && <Notice tone="red">{r.error}</Notice>}
      {(running || r.status === "FAILED") && <LiveFindings url={`/audit-reports/${r.id}/events`} active={running} steps={r.steps} title={running ? "Live findings" : "Research activity"} kind="combined" />}

      {r.status === "COMPLETED" && (<>
        <section aria-label="Scores" className="grid overflow-hidden rounded-lg border border-slate-200 bg-white lg:grid-cols-[320px_minmax(0,1fr)]">
          <div className="border-b border-slate-100 p-6 lg:border-b-0 lg:border-r">
            <div className="text-xs text-slate-500">Overall client readiness</div>
            <div className={cx("tabular mt-2 text-[52px] font-semibold leading-none tracking-tight", TONE_TEXT[scoreTone(overall?.score)])}>{overall?.score ?? "—"}<span className="ml-1 text-base font-normal text-slate-400">{overall?.score !== null && overall?.score !== undefined ? "/ 100" : ""}</span></div>
            <p className="mt-3 text-sm font-medium text-slate-900">{overall?.label}</p>
            {overall?.criteria && <details className="mt-3 text-xs leading-5 text-slate-500"><summary className="cursor-pointer font-medium text-slate-600 hover:text-slate-900">How the score is calculated</summary><p className="mt-1">{overall.criteria}</p></details>}
            {overall?.checks_scored !== undefined && <p className="mt-3 text-2xs text-slate-500">{overall.checks_scored} scored · {overall.checks_unavailable} unavailable{overall.checks_not_applicable ? ` · ${overall.checks_not_applicable} n/a` : ""}</p>}
          </div>
          <div className="divide-y divide-slate-100 px-6 py-2">
            <ScoreRow label="Client & business" s={r.scores.client} /><ScoreRow label="Website readiness" s={r.scores.website} />
            <ScoreRow label="Business Profile" s={r.scores.gbp} /><ScoreRow label="Competitor positioning" s={r.scores.competitors} /><ScoreRow label="Keyword readiness" s={r.scores.keywords} />
          </div>
        </section>
        {r.source_type !== "LIVE" && <Notice tone="amber">Some checks could not use live data (integration not configured or a request failed) — they are listed as <b>unavailable</b> and do not count toward the score. Check the <a className="underline" href="/integrations">Integrations page</a>.</Notice>}
        {r.changes?.previous_version && (
          <Card title={`Compared with audit v${r.changes.previous_version} (${dt(r.changes.previous_date)})`}>
            <MetricStrip items={[{ label: "Resolved / improved", value: <span className="text-emerald-700">{r.changes.resolved.length}</span> }, { label: "Worse", value: <span className="text-rose-700">{r.changes.worse.length}</span> },
              { label: "New issues", value: <span className="text-amber-700">{r.changes.new.length}</span> }, { label: "Unchanged", value: r.changes.unchanged }]} />
            <ul className="mt-4 space-y-1 text-[13px]">
              {r.changes.resolved.slice(0, 8).map((c: any) => <li key={c.id} className="text-emerald-800">▲ {c.check} ({c.from} → {c.to})</li>)}
              {r.changes.worse.slice(0, 8).map((c: any) => <li key={c.id} className="text-rose-800">▼ {c.check} ({c.from} → {c.to})</li>)}
              {r.changes.new.slice(0, 8).map((c: any) => <li key={c.id} className="text-amber-800">＋ New: {c.check} ({c.status})</li>)}
            </ul>
          </Card>)}

        <div className="grid gap-8 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
          <section aria-labelledby="sum-h">
            <h2 id="sum-h" className="mb-4 border-b border-slate-200 pb-2 text-base font-semibold text-slate-900">Final audit summary</h2>
            <dl className="space-y-5">
              {Object.entries(r.summary).filter(([k]) => k !== "method_note").sort(([a], [b]) => parseInt(a) - parseInt(b)).map(([k, v]: any) => (
                <div key={k} className="grid grid-cols-[28px_minmax(0,1fr)]">
                  <span className="tabular pt-px text-[13px] font-medium text-slate-400">{parseInt(k)}.</span>
                  <div><dt className="text-[13px] font-semibold text-slate-900">{k.replace(/^\d+_/, "").replace(/_/g, " ").replace(/^./, (c: string) => c.toUpperCase())}</dt>
                    <dd className="prose-read mt-1">{Array.isArray(v) ? (v.length ? <ul>{v.map((x: string, i: number) => <li key={i}>{x}</li>)}</ul> : "None") : v}</dd></div>
                </div>))}
            </dl>
          </section>
          <section aria-labelledby="rec-h">
            <h2 id="rec-h" className="mb-4 border-b border-slate-200 pb-2 text-base font-semibold text-slate-900">Recommendations</h2>
            <div className="space-y-5 text-[13px]">
              {SEVERITY.map(({ key: sev, title, bar }) => (
                <div key={sev} className={cx("border-l-2 pl-4", bar)}>
                  <h3 className="font-semibold text-slate-900">{title} <span className="tabular font-normal text-slate-400">({r.recommendations[sev].length})</span></h3>
                  {r.recommendations[sev].length ? <ul className="mt-1.5 space-y-2 text-slate-700">{r.recommendations[sev].map((x: any, i: number) => <li key={i} className="leading-5">{x.text} <EvidenceBadge v={x.evidence} /><span className="block text-xs text-slate-400">{x.because.slice(0, 90)}</span></li>)}</ul> : <p className="mt-1 text-slate-400">None</p>}
                </div>))}
              <div className="grid gap-5 border-t border-slate-100 pt-5 md:grid-cols-2">
                <div><h3 className="font-semibold text-slate-900">Needs client clarification ({r.recommendations.needs_client_clarification.length})</h3>
                  <ul className="mt-1.5 list-disc space-y-1 pl-5 text-slate-600">{r.recommendations.needs_client_clarification.map((x: any, i: number) => <li key={i}>{x.text}</li>)}</ul></div>
                <div><h3 className="font-semibold text-slate-900">Requires manual verification ({r.recommendations.manual_verification.length})</h3>
                  <ul className="mt-1.5 list-disc space-y-1 pl-5 text-slate-600">{r.recommendations.manual_verification.map((x: any, i: number) => <li key={i}>{x.text}</li>)}</ul></div>
              </div>
              <div className="rounded-lg bg-slate-900 px-5 py-4 text-slate-100"><h3 className="font-semibold text-white">Recommended next steps</h3>
                <ol className="mt-2 list-decimal space-y-1 pl-5 text-slate-300">{r.recommendations.next_steps.map((x: string, i: number) => <li key={i}>{x}</li>)}</ol></div>
            </div>
          </section>
        </div>

        <Card title="Detailed findings">
          <Tabs tabs={[
            ...(["client", "website", "gbp", "competitors", "keywords"] as const).map((k) => ({ key: k, label: `${r.sections[k].title}${r.sections[k].score !== null ? ` · ${r.sections[k].score}` : ""}`,
              content: <div className="space-y-4">
                {k === "competitors" && r.sections.competitors.suggested_competitors?.length > 0 && <Notice>Other local listings found (not yet analysed): {r.sections.competitors.suggested_competitors.map((s: any) => s.name).join(", ")}</Notice>}
                {k === "keywords" && <KeywordReport rep={r.sections.keywords.report} reportId={r.id} />}
                {r.sections[k].findings.length ? <Findings items={r.sections[k].findings} /> : <EmptyState message="No findings." />}</div> })),
            { key: "snapshot", label: "Client info used", content: <pre className="whitespace-pre-wrap rounded-md bg-slate-50 p-3 font-mono text-xs text-slate-600">{JSON.stringify(r.input_snapshot, null, 2)}</pre> },
          ]} />
        </Card>

        <details className="group rounded-lg border border-slate-200 bg-white">
          <summary className="flex cursor-pointer items-center justify-between gap-3 px-5 py-3.5 text-[13px] font-medium text-slate-800 hover:bg-slate-50/60">
            How this research was done: every step, source and limitation<span className="text-xs font-normal text-slate-500 group-open:hidden">Show the research streams</span></summary>
          <div className="border-t border-slate-100 p-3"><LiveFindings url={`/audit-reports/${r.id}/events`} active={false} steps={r.steps} kind="combined" /></div>
        </details>
      </>)}
    </div>
  );
}
