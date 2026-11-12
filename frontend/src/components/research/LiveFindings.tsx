import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../../api/client";
import { cx, ErrorBox } from "../ui";
import { buildModel, buildStream, describe, hostOf, providerLabel, STAGE_TITLE, type ResearchEvent, type StreamItem, type StreamStage } from "./model";

export type Step = { key: string; name: string; status: string; summary?: string };

/** Follows the persisted event log of a report or of one audit: the whole history first, then only new events (`after`), until done. */
export function useEventStream(url: string, active: boolean) {
  const [events, setEvents] = useState<ResearchEvent[]>([]);
  const [meta, setMeta] = useState<{ steps?: Step[]; status?: string; done: boolean }>({ done: false });
  const [error, setError] = useState<unknown>(null);
  const last = useRef(0);
  useEffect(() => {
    let stop = false, timer: ReturnType<typeof setTimeout> | undefined;
    last.current = 0; setEvents([]); setMeta({ done: false });
    const tick = async () => {
      try {
        const r: any = await api.get(`${url}?after=${last.current}`);
        if (stop) return;
        if (r.events?.length) { last.current = r.last_seq; setEvents((prev) => [...prev, ...r.events]); }
        setMeta({ steps: r.steps, status: r.status, done: !!r.done }); setError(null);
        if (!r.done && active) timer = setTimeout(tick, 1500);
      } catch (e) { if (!stop) { setError(e); timer = setTimeout(tick, 4000); } }
    };
    tick();
    return () => { stop = true; if (timer) clearTimeout(timer); };
  }, [url, active]);
  return { events, ...meta, error };
}

function useNow(active: boolean) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => { if (!active) return; const t = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(t); }, [active]);
  return now;
}

const ago = (now: number, at?: string) => { if (!at) return ""; const s = Math.max(0, Math.round((now - new Date(at).getTime()) / 1000)); return s < 3 ? "just now" : s < 90 ? `${s}s ago` : `${Math.round(s / 60)} min ago`; };

function Dot({ state }: { state: string }) {
  if (state === "running" || state === "working") return <span aria-label="in progress" className="mt-1 inline-block h-3 w-3 shrink-0 rounded-full border-2 border-brand-200 border-t-brand-600 motion-safe:animate-spin" />;
  const map: Record<string, [string, string, string]> = {
    done: ["✓", "bg-emerald-50 text-emerald-700 ring-emerald-200", "done"], failed: ["!", "bg-rose-50 text-rose-700 ring-rose-200", "failed"],
    skipped: ["–", "bg-slate-50 text-slate-500 ring-slate-200", "skipped"], waiting: ["", "bg-white ring-slate-300", "pending"], pending: ["", "bg-white ring-slate-300", "pending"],
  };
  const [mark, cls, label] = map[state] ?? map.waiting;
  return <span aria-label={label} className={cx("mt-0.5 inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full text-[10px] font-bold ring-1", cls)}>{mark}</span>;
}

function Line({ it }: { it: StreamItem }) {
  const tone = it.type === "warning" ? "text-amber-900" : it.type === "finding" ? "text-slate-900" : "text-slate-600";
  return (
    <li className="lf-enter flex gap-2.5 py-1">
      {it.type === "finding" ? <span aria-hidden className="mt-[7px] h-1.5 w-1.5 shrink-0 rounded-full bg-brand-500" />
        : it.type === "warning" ? <span aria-hidden className="mt-[7px] h-1.5 w-1.5 shrink-0 rounded-full bg-amber-500" />
        : it.state === "working" ? <span aria-hidden className="mt-[5px] h-2.5 w-2.5 shrink-0 rounded-full border border-brand-300 border-t-brand-600 motion-safe:animate-spin" />
        : <span aria-hidden className="mt-[7px] h-1.5 w-1.5 shrink-0 rounded-full bg-slate-300" />}
      <div className="min-w-0 flex-1">
        <div className={cx("break-words text-[13px] leading-5", tone, it.type === "finding" && "font-medium")}>
          {it.url ? <a href={it.url} target="_blank" rel="noreferrer noopener" className="hover:underline">{it.text}</a> : it.text}
        </div>
        {it.detail && <div className="text-xs text-slate-500">{it.detail}</div>}
        {!!it.chips?.length && <div className="mt-1 flex flex-wrap gap-1">{it.chips.slice(0, 8).map((c) => <span key={c} className="rounded-full bg-slate-100 px-2 py-0.5 text-2xs text-slate-700">{c}</span>)}</div>}
      </div>
    </li>
  );
}

function Section({ s, defaultOpen }: { s: StreamStage; defaultOpen: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  useEffect(() => { if (s.state === "running") setOpen(true); }, [s.state]);
  const pages = s.items.filter((i) => i.key.startsWith("p:"));
  const shown = open ? s.items : s.items.filter((i) => i.type !== "activity").slice(-6);
  return (
    <section className="py-3">
      <button onClick={() => setOpen(!open)} aria-expanded={open} className="flex w-full items-start gap-2.5 text-left">
        <Dot state={s.state} />
        <div className="min-w-0 flex-1">
          <h3 className="text-sm font-semibold text-slate-900">{s.title}</h3>
          <p className="text-xs text-slate-500">{s.state === "running" ? "In progress" : s.state === "failed" ? "Failed" : s.state === "skipped" ? "Skipped" : "Done"}
            {pages.length ? ` · ${pages.filter((p) => p.state === "done").length} of ${pages.length} page(s) read` : ""}{s.summary ? ` · ${s.summary}` : ""}</p>
        </div>
        <span className="text-2xs text-slate-400">{open ? "Hide steps" : `Show all ${s.items.length}`}</span>
      </button>
      {shown.length > 0 && <ul className="ml-[26px] mt-1 border-l border-slate-100 pl-3">{shown.map((it) => <Line key={it.key} it={it} />)}</ul>}
    </section>
  );
}

/** ChatGPT-style live findings: what is happening now, the tasks, readable findings as they arrive, numbered sources, and every
 *  limitation or failure in place. Built only from recorded backend events; when nothing is running it says so. */
export function LiveFindings({ url, active, steps: givenSteps, title = "Research activity" }: { url: string; active: boolean; steps?: Step[]; title?: string }) {
  const { events, steps: streamSteps, done, error } = useEventStream(url, active);
  const stream = useMemo(() => buildStream(events), [events]);
  const model = useMemo(() => buildModel(events), [events]);
  const live = active && !done;
  const now = useNow(live);
  const steps = givenSteps ?? streamSteps ?? [];
  const current = steps.find((s) => s.status === "RUNNING");
  const last = events[events.length - 1];
  const sources = model.sources;
  const warnings = model.warnings;
  return (
    <section aria-label="Live findings" className="rounded-xl border border-slate-200 bg-white">
      <header className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-slate-100 px-5 py-3">
        <h2 className="text-sm font-semibold text-slate-900">{title}</h2>
        <div role="status" aria-live="polite" className="flex min-w-0 items-center gap-2 text-[13px] text-slate-600">
          {live ? (<>
            <span className="relative flex h-2 w-2"><span className="absolute inline-flex h-full w-full rounded-full bg-brand-500 opacity-60 motion-safe:animate-ping" /><span className="relative inline-flex h-2 w-2 rounded-full bg-brand-600" /></span>
            <span className="truncate">{last ? `${current ? (STAGE_TITLE[current.key] ?? current.name.replace(/^\d+ · /, "")) + " · " : ""}last update ${ago(now, last.at)}` : "Waiting for the research worker to record its first step"}</span>
            {last && (now - new Date(last.at).getTime()) > 20000 && <span className="text-amber-700">· waiting for a slow source to answer</span>}
          </>) : <span>{model.finished?.kind === "audit_failed" ? "Stopped." : events.length ? `Finished · ${events.length} recorded step(s)` : "No activity was recorded for this run."}</span>}
        </div>
        {sources.length > 0 && <span className="ml-auto rounded-full bg-slate-100 px-2 py-0.5 text-2xs font-medium text-slate-600">{sources.length} source{sources.length === 1 ? "" : "s"}</span>}
      </header>
      <ErrorBox error={error} />
      <div className="grid lg:grid-cols-[minmax(0,1fr)_260px]">
        <div className="divide-y divide-slate-100 px-5">
          {stream.length === 0 ? (
            <p className="py-6 text-sm text-slate-500">{live ? "Nothing has been recorded yet. Activity appears here the moment the first source is contacted." : "There is no recorded activity for this audit."}</p>
          ) : stream.map((s) => <Section key={s.stage} s={s} defaultOpen={live ? s.state === "running" : s.state === "failed"} />)}
        </div>
        <aside className="space-y-4 border-t border-slate-100 px-5 py-4 lg:border-l lg:border-t-0">
          {steps.length > 0 && (
            <div>
              <div className="mb-1.5 text-2xs font-semibold uppercase tracking-wider text-slate-400">Tasks</div>
              <ol className="space-y-1.5">{steps.map((s) => (
                <li key={s.key} className="flex items-start gap-2 text-[13px]">
                  <Dot state={s.status === "DONE" || s.status === "COMPLETED" ? "done" : s.status === "RUNNING" ? "running" : s.status === "FAILED" ? "failed" : s.status === "SKIPPED" ? "skipped" : "pending"} />
                  <span className={cx("min-w-0", s.status === "PENDING" || s.status === "QUEUED" ? "text-slate-500" : "text-slate-800")}>{s.name.replace(/^\d+ · /, "")}</span>
                </li>))}</ol>
            </div>)}
          {warnings.length > 0 && (
            <div>
              <div className="mb-1.5 text-2xs font-semibold uppercase tracking-wider text-slate-400">Limitations & failures</div>
              <ul className="space-y-1 text-xs text-amber-900">{warnings.slice(-8).map((w) => <li key={w.seq} className="break-words">{describe(w)}</li>)}</ul>
            </div>)}
          {sources.length > 0 && (
            <div>
              <div className="mb-1.5 text-2xs font-semibold uppercase tracking-wider text-slate-400">Sources</div>
              <ol className="space-y-1.5">{sources.slice(0, 12).map((s, i) => (
                <li key={s.key} className="flex gap-2 text-xs"><span className="mt-px text-slate-400">{i + 1}</span>
                  <div className="min-w-0">{s.url ? <a href={s.url} target="_blank" rel="noreferrer noopener" className="block truncate font-medium text-slate-800 hover:underline" title={s.url}>{s.label || hostOf(s.url)}</a>
                    : <span className="block truncate font-medium text-slate-800">{s.label}</span>}
                    <span className="text-2xs text-slate-400">{s.url ? hostOf(s.url) : providerLabel(s.provider)} · {s.kind}</span></div></li>))}
              </ol>
              {sources.length > 12 && <p className="mt-1 text-2xs text-slate-400">and {sources.length - 12} more</p>}
            </div>)}
        </aside>
      </div>
      <details className="border-t border-slate-100 px-5 py-2 text-xs">
        <summary className="cursor-pointer text-slate-500">Technical details ({events.length} events)</summary>
        <div className="mt-2 max-h-72 overflow-auto font-mono text-2xs text-slate-600">{events.map((e) => <div key={e.seq}>#{e.seq} {e.at} [{e.level}] {e.stage || "-"} {e.kind} — {e.title}</div>)}</div>
      </details>
    </section>
  );
}
