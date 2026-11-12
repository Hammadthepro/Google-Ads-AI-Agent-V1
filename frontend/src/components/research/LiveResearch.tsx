import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../../api/client";
import { Badge, cx, ErrorBox } from "../ui";
import { buildModel, describe, hostOf, providerLabel, type ResearchEvent } from "./model";
import { MarketMap, ResearchCanvas } from "./ResearchCanvas";

type Step = { key: string; name: string; status: string; summary?: string };
const STAGE_HINT: Record<string, string> = {
  client: "Reading the client profile and services", website: "Crawling the website and extracting services and landing pages",
  gbp: "Business presence and the local market", competitors: "Finding and reading competitors", keywords: "Discovering keywords and search intent",
  report: "Scoring the findings and writing recommendations",
};

/** Polls the persisted event log (`after` = last seq) until the audit is done. The whole history is replayed on load, so a refresh
 *  or a second viewer sees the same research. */
export function useResearchEvents(reportId: string, active: boolean) {
  const [events, setEvents] = useState<ResearchEvent[]>([]);
  const [steps, setSteps] = useState<Step[]>([]);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const last = useRef(0);
  useEffect(() => {
    let stop = false, timer: ReturnType<typeof setTimeout> | undefined;
    last.current = 0; setEvents([]); setDone(false);
    const tick = async () => {
      try {
        const r: any = await api.get(`/audit-reports/${reportId}/events?after=${last.current}`);
        if (stop) return;
        if (r.events?.length) { last.current = r.last_seq; setEvents((prev) => [...prev, ...r.events]); }
        setSteps(r.steps ?? []); setDone(!!r.done); setError(null);
        if (!r.done && active) timer = setTimeout(tick, 1500);
      } catch (e) { if (!stop) { setError(e); timer = setTimeout(tick, 4000); } }
    };
    tick();
    return () => { stop = true; if (timer) clearTimeout(timer); };
  }, [reportId, active]);
  return { events, steps, done, error };
}

function useNow(active: boolean) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => { if (!active) return; const t = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(t); }, [active]);
  return now;
}

const DOT: Record<string, string> = { DONE: "bg-emerald-500", RUNNING: "bg-brand-500", FAILED: "bg-rose-500", SKIPPED: "bg-amber-400", PENDING: "bg-slate-300" };

export function LiveResearch({ reportId, running }: { reportId: string; running: boolean }) {
  const { events, steps, done, error } = useResearchEvents(reportId, running);
  const model = useMemo(() => buildModel(events), [events]);
  const live = running && !done;
  const now = useNow(live);
  const [open, setOpen] = useState<ResearchEvent | null>(null);
  const idle = model.last ? Math.round((now - new Date(model.last.at).getTime()) / 1000) : null;
  const current = steps.find((s) => s.status === "RUNNING");
  const feed = events.filter((e) => !["stage_started"].includes(e.kind)).slice(-40).reverse();
  return (
    <section aria-label="Live research" className="space-y-4">
      <ErrorBox error={error} />
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div className="space-y-3">
          <ResearchCanvas model={model} live={live} />
          <div className="flex flex-wrap items-center gap-2 text-sm" role="status" aria-live="polite">
            {live ? (<>
              <span className="relative flex h-2.5 w-2.5"><span className="absolute inline-flex h-full w-full rounded-full bg-brand-500 opacity-60 motion-safe:animate-ping" /><span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-brand-600" /></span>
              <span className="font-medium text-slate-900">{current ? STAGE_HINT[current.key] ?? current.name : "Preparing the research"}</span>
              {model.last ? <span className="text-slate-500">· last activity {idle !== null && idle > 2 ? `${idle}s ago` : "just now"}{idle !== null && idle > 20 ? " — waiting for a slow source to answer" : ""}</span>
                : <span className="text-slate-500">· waiting for the research worker to record its first step</span>}
            </>) : model.finished ? <span className="text-slate-600">{model.finished.kind === "audit_failed" ? "The research stopped." : "Research complete."} {events.length} recorded step(s).</span>
              : <span className="text-slate-500">No research events were recorded for this audit.</span>}
          </div>
        </div>
        <aside className="space-y-3">
          <div className="rounded-lg border border-slate-200 bg-white">
            <div className="border-b border-slate-100 px-3 py-2 text-xs font-semibold text-slate-700">Research modules</div>
            <ol className="divide-y divide-slate-100">
              {steps.map((s) => {
                const act = model.stageActivity[s.key];
                return (
                  <li key={s.key} className="flex items-start gap-2.5 px-3 py-2">
                    <span className={cx("mt-1.5 h-2 w-2 shrink-0 rounded-full", DOT[s.status] ?? "bg-slate-300", s.status === "RUNNING" && "motion-safe:animate-pulse")} />
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center justify-between gap-2 text-[13px]"><span className="font-medium text-slate-800">{s.name.replace(/^\d+ · /, "")}</span>
                        <span className="text-2xs text-slate-400">{s.status === "PENDING" ? "pending" : s.status === "RUNNING" ? "running" : s.status.toLowerCase()}</span></div>
                      {(s.summary || act?.last) && <div className="truncate text-2xs text-slate-500" title={s.summary || act?.last?.title}>{s.summary || (act?.last ? describe(act.last) : "")}</div>}
                    </div>
                  </li>);
              })}
            </ol>
          </div>
          {model.warnings.length > 0 && (
            <div className="rounded-lg border border-amber-200 bg-amber-50/60">
              <div className="border-b border-amber-100 px-3 py-2 text-xs font-semibold text-amber-900">Limitations and failures ({model.warnings.length})</div>
              <ul className="max-h-48 space-y-1 overflow-y-auto px-3 py-2 text-2xs text-amber-900">
                {model.warnings.slice(-12).map((w) => <li key={w.seq} className={cx(w.level === "error" && "text-rose-800")}>{describe(w)}</li>)}
              </ul>
            </div>)}
          {model.locations.length > 0 && model.competitors.some((c) => typeof c.lat === "number") && (
            <div className="rounded-lg border border-slate-200 bg-white p-3">
              <div className="mb-1 text-xs font-semibold text-slate-700">Local market</div>
              <MarketMap model={model} />
              <p className="text-2xs text-slate-500">Nearby same-category businesses from map data. Coverage of small businesses is incomplete.</p>
            </div>)}
        </aside>
      </div>
      <div className="grid gap-4 lg:grid-cols-3">
        <div className="rounded-lg border border-slate-200 bg-white lg:col-span-1">
          <div className="border-b border-slate-100 px-3 py-2 text-xs font-semibold text-slate-700">Activity</div>
          {feed.length === 0 ? <p className="px-3 py-4 text-sm text-slate-500">{live ? "Nothing recorded yet." : "No activity recorded."}</p> : (
            <ul className="max-h-80 divide-y divide-slate-50 overflow-y-auto" aria-label="Research activity">
              {feed.map((e) => (
                <li key={e.seq}><button onClick={() => setOpen(e)} className="flex w-full items-start gap-2 px-3 py-1.5 text-left text-[12px] hover:bg-slate-50">
                  <span className={cx("mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full", e.level === "error" ? "bg-rose-500" : e.level === "warn" ? "bg-amber-400" : "bg-slate-300")} />
                  <span className="min-w-0 flex-1 break-words text-slate-700">{describe(e)}</span>
                  <time className="shrink-0 text-2xs text-slate-400">{new Date(e.at).toLocaleTimeString()}</time></button></li>))}
            </ul>)}
        </div>
        <div className="rounded-lg border border-slate-200 bg-white">
          <div className="border-b border-slate-100 px-3 py-2 text-xs font-semibold text-slate-700">Sources and evidence ({model.sources.length})</div>
          {model.sources.length === 0 ? <p className="px-3 py-4 text-sm text-slate-500">Sources appear here as they are read.</p> : (
            <ul className="max-h-80 divide-y divide-slate-50 overflow-y-auto">
              {model.sources.slice().reverse().slice(0, 60).map((s) => (
                <li key={s.key} className="px-3 py-1.5 text-[12px]">
                  <div className="flex items-center gap-1.5"><Badge>{s.kind}</Badge><span className="text-2xs text-slate-400">{providerLabel(s.provider)}</span></div>
                  {s.url ? <a href={s.url} target="_blank" rel="noreferrer noopener" className="mt-0.5 block truncate text-brand-700 hover:underline" title={s.url}>{s.label || s.url}</a>
                    : <div className="mt-0.5 truncate text-slate-700">{s.label}</div>}
                  {s.url && <div className="truncate text-2xs text-slate-400">{hostOf(s.url)}</div>}
                </li>))}
            </ul>)}
        </div>
        <div className="rounded-lg border border-slate-200 bg-white">
          <div className="border-b border-slate-100 px-3 py-2 text-xs font-semibold text-slate-700">Findings so far</div>
          <ul className="space-y-2 px-3 py-2 text-[12px] text-slate-700">
            {model.competitors.length > 0 && <li><b>{model.competitors.length}</b> competitor(s): {model.competitors.slice(0, 5).map((c) => c.name).join(", ")}{model.competitors.length > 5 ? "…" : ""}</li>}
            {model.keywords.length > 0 && <li><b>{model.keywords.reduce((n, k) => n + k.count, 0)}</b> keyword candidate(s) from {[...new Set(model.keywords.map((k) => providerLabel(k.provider)).filter(Boolean))].join(", ")}</li>}
            {model.insights.map((e) => <li key={e.seq}>{e.title}</li>)}
            {model.pages.length > 0 && <li><b>{model.pages.filter((p) => p.state === "extracted").length}</b> page(s) read across {new Set(model.pages.map((p) => hostOf(p.url))).size} site(s)</li>}
            {!model.competitors.length && !model.keywords.length && !model.insights.length && !model.pages.length && <li className="text-slate-500">Findings appear as each module stores its results.</li>}
          </ul>
        </div>
      </div>
      <details className="rounded-lg border border-slate-200 bg-white text-[12px]">
        <summary className="cursor-pointer px-3 py-2 text-xs font-semibold text-slate-700">Diagnostics: raw research events ({events.length})</summary>
        <div className="max-h-96 overflow-auto px-3 pb-3 font-mono text-2xs text-slate-600">
          {events.map((e) => <div key={e.seq}>#{e.seq} {e.at} [{e.level}] {e.stage || "-"} {e.kind} — {e.title}</div>)}
        </div>
      </details>
      {open && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4" role="dialog" aria-modal="true" aria-label="Event detail" onClick={() => setOpen(null)}>
          <div className="w-full max-w-lg rounded-lg bg-white p-4 shadow-pop" onClick={(ev) => ev.stopPropagation()}>
            <div className="flex items-start justify-between gap-2"><h3 className="text-sm font-semibold text-slate-900">{describe(open)}</h3><button className="text-xs text-slate-500" onClick={() => setOpen(null)}>Close</button></div>
            <p className="mt-1 text-2xs text-slate-500">{open.kind} · {open.stage || "audit"} · {new Date(open.at).toLocaleString()}</p>
            <pre className="mt-2 max-h-80 overflow-auto rounded bg-slate-50 p-2 text-2xs text-slate-700">{JSON.stringify(open.data, null, 2)}</pre>
          </div>
        </div>)}
    </section>
  );
}
