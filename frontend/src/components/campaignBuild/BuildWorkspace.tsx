import { useEffect, useMemo, useState } from "react";
import { cx } from "../ui";
import { useEventStream } from "../research/LiveFindings";
import { buildModel, buildStream, type ResearchEvent } from "../research/model";
import { ResearchScene } from "../research/ResearchScene";
import type { StageState } from "../research/sceneLayout";
import { buildStages, draftActivity, metric, providerName, waitLeft, type BuildStage } from "./buildModel";

const RESEARCH = new Set(["research", "keywords"]);
const fmt$ = (v: any) => (typeof v === "number" ? `$${v.toLocaleString(undefined, { maximumFractionDigits: v < 100 ? 2 : 0 })}` : "—");
const num = (v: any) => (typeof v === "number" ? v.toLocaleString(undefined, { maximumFractionDigits: 1 }) : "—");

function useNow(on: boolean) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => { if (!on) return; const t = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(t); }, [on]);
  return now;
}

function Dot({ status }: { status: BuildStage["status"] }) {
  if (status === "running") return <span aria-label="in progress" className="inline-block h-3 w-3 shrink-0 rounded-full border-2 border-brand-100 border-t-brand-600 motion-safe:animate-spin" />;
  const cls = { done: "bg-emerald-600", failed: "bg-rose-600", stopped: "bg-amber-500", waiting: "bg-white ring-1 ring-inset ring-slate-300" }[status];
  return <span aria-label={status} className={cx("inline-block h-3 w-3 shrink-0 rounded-full", cls)} />;
}

/** One metric with its label; `estimate` marks a modelled number. */
function Figure({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="min-w-0 rounded-md border border-slate-200 px-3 py-2.5">
      <div className="truncate text-2xs text-slate-500">{label}</div>
      <div className="tabular text-base font-semibold text-slate-900">{value}</div>
      {sub && <div className="text-2xs text-slate-400">{sub}</div>}
    </div>
  );
}

function Waiting({ text }: { text: string }) {
  return <p className="py-8 text-center text-[13px] text-slate-500">{text}</p>;
}

/** Expected leads per week (the estimate's range and point) against the client's weekly target, on one axis. */
function LeadsAxis({ f }: { f: Record<string, any> }) {
  const range: number[] = Array.isArray(f.realistic_weekly_range) ? f.realistic_weekly_range : [];
  const exp = typeof f.expected_weekly_conversions === "number" ? f.expected_weekly_conversions : null;
  const target = typeof f.weekly_target === "number" && f.weekly_target > 0 ? f.weekly_target : null;
  const max = Math.max(...[...range, exp ?? 0, target ?? 0].filter((x) => typeof x === "number"), 1) * 1.15;
  const pct = (v: number) => `${Math.min(100, (v / max) * 100)}%`;
  if (exp === null) return null;
  return (
    <figure className="mt-4" aria-label={`Expected ${exp} leads per week${range.length === 2 ? ` (range ${range[0]} to ${range[1]})` : ""}${target ? `, target ${target}` : ""}`}>
      <figcaption className="mb-2 flex justify-between text-2xs text-slate-500"><span>Leads per week (estimate)</span><span>0 – {num(max)}</span></figcaption>
      <div className="relative h-7 rounded bg-slate-100">
        {range.length === 2 && <div className="absolute inset-y-1.5 rounded-sm bg-brand-200" style={{ left: pct(range[0]), width: `calc(${pct(range[1])} - ${pct(range[0])})` }} />}
        <div className="absolute inset-y-0 w-0.5 bg-brand-700" style={{ left: pct(exp) }} title={`Expected ${exp}`} />
        {target && <div className="absolute -inset-y-1 w-0 border-l-2 border-dashed border-saffron-500" style={{ left: pct(target) }} title={`Target ${target}`} />}
      </div>
      <div className="mt-1.5 flex flex-wrap gap-x-4 text-2xs text-slate-600">
        <span><span className="mr-1 inline-block h-2 w-2 rounded-sm bg-brand-200 align-middle" />realistic range {range.length === 2 ? `${num(range[0])}–${num(range[1])}` : "—"}</span>
        <span><span className="mr-1 inline-block h-2 w-0.5 bg-brand-700 align-middle" />expected {num(exp)}</span>
        {target && <span><span className="mr-1 inline-block h-2 w-0 border-l-2 border-dashed border-saffron-500 align-middle" />target {num(target)}</span>}
      </div>
    </figure>
  );
}

function Bars({ rows, unit }: { rows: [string, number][]; unit: string }) {
  const max = Math.max(...rows.map((r) => r[1]), 1);
  return (
    <ul className="space-y-1.5">
      {rows.map(([k, v]) => (
        <li key={k} className="grid grid-cols-[minmax(0,10rem)_1fr_auto] items-center gap-2 text-xs">
          <span className="truncate text-slate-700" title={k}>{k}</span>
          <span className="h-2.5 rounded-sm bg-slate-100"><span className="block h-full rounded-sm bg-brand-500" style={{ width: `${(v / max) * 100}%` }} /></span>
          <span className="tabular text-slate-600">{v} {unit}</span>
        </li>))}
    </ul>
  );
}

/** The drafting stage: which provider is being asked, waits a provider asked for, failures, and the structure once it is stored. */
function DraftPanel({ events, live, stage }: { events: ResearchEvent[]; live: boolean; stage?: BuildStage }) {
  const { calls, plan, started } = useMemo(() => draftActivity(events), [events]);
  const now = useNow(live && calls.some((c) => c.state === "waiting"));
  if (!started) return <Waiting text="Drafting starts once research, keywords and feasibility are done." />;
  return (
    <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.3fr)]">
      <div>
        <div className="mb-2 text-xs font-medium text-slate-500">AI providers{stage?.attempt ? ` · attempt ${stage.attempt}` : ""}</div>
        {calls.length === 0 ? <p className="text-[13px] text-slate-500">{live ? "Preparing the request…" : "No provider request was recorded."}</p> : (
          <ul className="space-y-2">
            {calls.map((c) => {
              const left = c.state === "waiting" ? waitLeft(c, now) : 0;
              return (
                <li key={c.provider} className={cx("rounded-md border px-3 py-2", c.state === "failed" ? "border-rose-200 bg-rose-50/50" : c.state === "done" ? "border-emerald-200" : "border-slate-200")}>
                  <div className="flex items-center gap-2 text-[13px] font-medium text-slate-900">
                    {c.state === "asking" && live ? <span className="inline-block h-2.5 w-2.5 rounded-full border border-brand-300 border-t-brand-600 motion-safe:animate-spin" aria-hidden />
                      : <span aria-hidden className={cx("h-2 w-2 rounded-full", { done: "bg-emerald-600", failed: "bg-rose-600", waiting: "bg-amber-500", asking: "bg-slate-400" }[c.state])} />}
                    {providerName(c.provider)} {c.model && <span className="font-mono text-2xs font-normal text-slate-500">{c.model}</span>}
                  </div>
                  <div className="mt-0.5 break-words text-xs text-slate-600">{c.text}</div>
                  {c.state === "waiting" && live && <div className="mt-1 text-xs text-amber-800" role="status">Waiting on the provider's rate-limit window{left ? ` · about ${left}s left (as stated by the provider)` : " · retrying now"}</div>}
                </li>);
            })}
          </ul>)}
      </div>
      <div>
        <div className="mb-2 text-xs font-medium text-slate-500">Campaign structure</div>
        {!plan ? <p className="rounded-md border border-dashed border-slate-300 px-3 py-6 text-center text-[13px] text-slate-500">
          {stage?.status === "failed" ? "No draft was stored: no provider returned a valid campaign." : live ? "The structure appears here when a provider returns a valid draft." : "No draft was stored."}</p> : (
          <div className="rounded-md border border-slate-200">
            <div className="border-b border-slate-100 px-3 py-2">
              <div className="truncate text-[13px] font-semibold text-slate-900">{plan.campaign_name ?? "Draft"}</div>
              <div className="text-2xs text-slate-500">{[plan.bidding_strategy?.replace(/_/g, " ").toLowerCase(), plan.campaign_negatives != null && `${plan.campaign_negatives} campaign negatives`, plan.sitelinks != null && `${plan.sitelinks} sitelinks`].filter(Boolean).join(" · ")}</div>
            </div>
            <ul className="divide-y divide-slate-100">
              {(plan.ad_groups ?? []).map((g: any, i: number) => (
                <li key={`${g.name}-${i}`} className="lf-enter flex flex-wrap items-baseline justify-between gap-x-3 px-3 py-1.5 text-xs" style={{ animationDelay: `${Math.min(i, 12) * 60}ms` }}>
                  <span className="min-w-0 truncate font-medium text-slate-800">{g.name}</span>
                  <span className="tabular text-slate-500">{g.keywords} keywords · {g.negatives} negatives · {g.ads} ad{g.ads === 1 ? "" : "s"}</span>
                </li>))}
            </ul>
          </div>)}
      </div>
    </div>
  );
}

function StagePanel({ k, events, stage, live, stages, model, subject }: { k: string; events: ResearchEvent[]; stage?: BuildStage; live: boolean; stages: StageState;
  model: ReturnType<typeof buildModel>; subject?: { website?: string; business?: string } }) {
  const pending = !stage || stage.status === "waiting";
  if (RESEARCH.has(k)) {
    const kw = metric(events, "keywords");
    return (
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
        <ResearchScene kind="combined" model={model} stages={stages} live={live && (stage?.status === "running" || stages.keywords === "running")} focus={null} onFocus={() => {}}
          className="h-[300px] sm:h-[380px]" />
        <div className="min-w-0">
          <div className="mb-2 text-xs font-medium text-slate-500">Keyword clusters</div>
          {kw && Object.keys(kw.clusters ?? {}).length ? (<>
            <Bars rows={Object.entries(kw.clusters as Record<string, number>).sort((a, b) => b[1] - a[1]).slice(0, 12)} unit="kw" />
            <p className="mt-3 text-xs text-slate-500">{kw.selected} selected of {kw.candidates} candidates · {kw.negatives} negative ideas · {kw.overlaps} overlap(s)</p>
          </>) : <p className="text-[13px] text-slate-500">{stages.keywords === "running" ? "Clustering the researched keywords…" : stages.keywords === "failed" ? "Keyword research failed." : "Clusters appear when keyword research finishes."}</p>}
          {subject?.website && <p className="mt-3 truncate text-2xs text-slate-400">Researching {subject.website}</p>}
        </div>
      </div>);
  }
  if (k === "draft" || k === "challenge") return <DraftPanel events={events} live={live} stage={stage} />;
  const m = metric(events, k === "account" ? "account" : k);
  if (k === "account") return m ? (
    <div className="grid gap-3 sm:grid-cols-3">
      <Figure label="Google Ads account" value={m.linked ? m.customer_id : "Not linked"} sub={m.linked ? "read-only sync" : "drafting continues; deployment needs an account"} />
      {m.linked && <Figure label="Campaigns read" value={num(m.campaigns)} />}
    </div>) : <Waiting text={pending ? "Not started." : "Reading the account…"} />;
  if (k === "measurement") {
    if (!m) return <Waiting text={pending ? "Not started." : "Checking conversion tracking…"} />;
    const total = Math.max(m.checks, 1);
    return (
      <div>
        <div className="flex h-3 overflow-hidden rounded-sm bg-slate-100" role="img" aria-label={`${m.ok} passed, ${m.issues} issues, ${m.not_assessable} not assessable of ${m.checks} checks`}>
          <span className="bg-emerald-500" style={{ width: `${(m.ok / total) * 100}%` }} /><span className="bg-rose-500" style={{ width: `${(m.issues / total) * 100}%` }} />
          <span className="bg-slate-300" style={{ width: `${(m.not_assessable / total) * 100}%` }} />
        </div>
        <p className="mt-2 text-xs text-slate-600">{m.checks} checks · <span className="text-emerald-700">{m.ok} passed</span> · <span className="text-rose-700">{m.issues} issue(s)</span> · {m.not_assessable} could not be assessed</p>
        {!!m.issue_titles?.length && <ul className="mt-2 list-disc pl-4 text-xs text-slate-700">{m.issue_titles.map((t: string) => <li key={t}>{t}</li>)}</ul>}
      </div>);
  }
  if (k === "overlap") return m ? (
    <div><p className="text-[13px] text-slate-800"><span className="tabular text-lg font-semibold">{m.count}</span> keyword overlap(s) with existing campaigns</p>
      {!!m.keywords?.length && <div className="mt-2 flex flex-wrap gap-1">{m.keywords.map((x: string) => <span key={x} className="rounded bg-slate-100 px-1.5 py-px text-2xs text-slate-700">{x}</span>)}</div>}</div>)
    : <Waiting text={pending ? "Not started." : "Comparing with existing keywords…"} />;
  if (k === "feasibility") return m ? (
    <div>
      <div className="grid gap-2 sm:grid-cols-4">
        <Figure label="Monthly budget" value={fmt$(m.monthly_budget)} sub={m.recommended_monthly_budget ? `recommended ${fmt$(m.recommended_monthly_budget)}` : undefined} />
        <Figure label="Daily budget" value={fmt$(m.daily_budget)} />
        <Figure label="Allowed cost per lead" value={fmt$(m.allowed_cost_per_lead)} />
        <Figure label="Status" value={String(m.status ?? "—").replace(/_/g, " ").toLowerCase()} sub="estimate" />
      </div>
      <LeadsAxis f={m} />
    </div>) : <Waiting text={pending ? "Not started." : "Estimating leads and cost per lead…"} />;
  if (k === "intelligence") return m ? (
    <div className="grid gap-3 sm:grid-cols-3">
      <Figure label="Readiness" value={m.label} /><Figure label="Modules refreshed" value={num(m.ran)} /><Figure label="Modules reused" value={num(m.reused)} />
    </div>) : <Waiting text={pending ? "Not started." : "Updating the intelligence record…"} />;
  return stage?.summary ? <p className="whitespace-pre-wrap break-words text-[13px] text-slate-700">{stage.summary}</p> : <Waiting text={pending ? "Not started." : "Working…"} />;
}

/** The campaign build as it happens: the workflow rail and, for the stage in view, a picture drawn only from what the backend recorded. */
export function BuildWorkspace({ task, active, subject }: { task: any; active: boolean; subject?: { website?: string; business?: string } }) {
  const { events, error, done } = useEventStream(`/tasks/${task.id}/events`, active);
  const stages = useMemo(() => buildStages(task.task_type, task.steps), [task.task_type, task.steps]);
  const model = useMemo(() => {
    const m = buildModel(events);
    if (subject?.business && !m.business) m.business = subject.business;
    return m;
  }, [events, subject?.business]);
  const sceneStages = useMemo<StageState>(() => {
    const st: StageState = {};
    for (const s of buildStream(events)) if (["website", "gbp", "geo", "competitors", "keywords"].includes(s.stage)) st[s.stage] = s.state;
    return st;
  }, [events]);
  const running = stages.find((s) => s.status === "running");
  const stopped = [...stages].reverse().find((s) => s.status === "failed" || s.status === "stopped");
  const lastDone = [...stages].reverse().find((s) => s.status === "done");
  const auto = (running ?? stopped ?? lastDone ?? stages[0])?.key;
  const [pick, setPick] = useState<string | null>(null);
  useEffect(() => { setPick(null); }, [task.id]);
  const view = pick ?? auto;
  const live = active && !done;
  const last = events[events.length - 1];
  const now = useNow(live);
  const quiet = live && last && now - new Date(last.at).getTime() > 20000;
  const conn = error ? { t: "Connection lost · retrying", c: "text-rose-700" } : live ? { t: last ? (quiet ? "Live · waiting on an external service" : "Live") : "Connected · waiting for the first step", c: quiet ? "text-amber-700" : "text-brand-700" }
    : { t: events.length ? `Finished · ${events.length} recorded events` : "No live events were recorded for this run", c: "text-slate-500" };
  return (
    <section aria-label="Build workspace" className="overflow-hidden rounded-lg border border-slate-200 bg-white">
      <header className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-100 px-5 py-3">
        <h2 className="text-[13px] font-semibold text-slate-900">Build workspace</h2>
        <span role="status" aria-live="polite" className={cx("flex items-center gap-1.5 text-xs", conn.c)}>
          {live && !error && <span className="h-1.5 w-1.5 rounded-full bg-current motion-safe:animate-pulse" aria-hidden />}{conn.t}</span>
      </header>
      <nav aria-label="Workflow stages" className="pane-scroll overflow-x-auto border-b border-slate-100 px-3 py-2">
        <ol className="flex min-w-max gap-1">
          {stages.map((s) => (
            <li key={s.key}>
              <button type="button" onClick={() => setPick(s.key)} aria-current={view === s.key ? "step" : undefined}
                className={cx("flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs", view === s.key ? "bg-brand-50 font-medium text-brand-800 ring-1 ring-inset ring-brand-200" : "text-slate-600 hover:bg-slate-50", s.status === "waiting" && "text-slate-400")}>
                <Dot status={s.status} />{s.title}{s.attempt ? <span className="text-2xs text-slate-400">#{s.attempt}</span> : null}
              </button>
            </li>))}
        </ol>
      </nav>
      <div className="p-5">
        {(() => { const s = stages.find((x) => x.key === view); return s && (s.status === "failed" || s.status === "stopped") && s.summary ? (
          <p className={cx("mb-4 break-words rounded-md px-3 py-2 text-[13px]", s.status === "failed" ? "bg-rose-50 text-rose-900" : "bg-amber-50 text-amber-950")}>{s.summary}</p>) : null; })()}
        <StagePanel k={view} events={events} stage={stages.find((s) => s.key === view)} live={live} stages={sceneStages} model={model} subject={subject} />
      </div>
    </section>
  );
}
