import { Fragment, memo, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../../api/client";
import { cx, ErrorBox } from "../ui";
import { buildModel, buildStream, describe, hostOf, providerLabel, STAGE_TITLE, type ResearchEvent, type StreamItem, type StreamStage } from "./model";
import { ResearchScene } from "./ResearchScene";
import { buildDigest, type DigestItem } from "./digest";
import type { SceneKind, StageState } from "./sceneLayout";

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
  if (state === "running" || state === "working") return <span aria-label="in progress" className="mt-0.5 inline-block h-4 w-4 shrink-0 rounded-full border-2 border-brand-100 border-t-brand-600 motion-safe:animate-spin" />;
  const map: Record<string, [string, string, string]> = {
    done: ["✓", "bg-emerald-600 text-white ring-emerald-600", "done"], failed: ["!", "bg-rose-600 text-white ring-rose-600", "failed"],
    skipped: ["–", "bg-slate-100 text-slate-500 ring-slate-200", "skipped"], waiting: ["", "bg-white ring-slate-300", "pending"], pending: ["", "bg-white ring-slate-300", "pending"],
  };
  const [mark, cls, label] = map[state] ?? map.waiting;
  return <span aria-label={label} className={cx("mt-0.5 inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full text-[10px] font-bold ring-1", cls)}>{mark}</span>;
}

const Line = memo(function Line({ it, focused, onHover }: { it: StreamItem; focused?: boolean; onHover?: (k: string | null) => void }) {
  const tone = it.type === "warning" ? "text-amber-900" : it.type === "finding" ? "text-slate-900" : "text-slate-600";
  return (
    <li className={cx("lf-enter -mx-2 flex gap-2.5 rounded-md px-2 py-1.5 transition-colors", focused && "bg-brand-50")} data-key={it.key}
      onMouseEnter={onHover ? () => onHover(it.key) : undefined} onMouseLeave={onHover ? () => onHover(null) : undefined}>
      {it.type === "finding" ? <span aria-hidden className="mt-[7px] h-1.5 w-1.5 shrink-0 rounded-full bg-brand-500" />
        : it.type === "warning" ? <span aria-hidden className="mt-[7px] h-1.5 w-1.5 shrink-0 rounded-full bg-amber-500" />
        : it.state === "working" ? <span aria-hidden className="mt-[5px] h-2.5 w-2.5 shrink-0 rounded-full border border-brand-300 border-t-brand-600 motion-safe:animate-spin" />
        : <span aria-hidden className="mt-[7px] h-1.5 w-1.5 shrink-0 rounded-full bg-slate-300" />}
      <div className="min-w-0 flex-1">
        <div className={cx("break-words text-[13px] leading-5", tone, it.type === "finding" && "font-medium")}>
          {it.url ? <a href={it.url} target="_blank" rel="noreferrer noopener" className="hover:underline">{it.text}</a> : it.text}
        </div>
        {it.detail && <div className="text-xs leading-5 text-slate-500">{it.detail}</div>}
        {!!it.chips?.length && <div className="mt-1 flex flex-wrap gap-1">{it.chips.slice(0, 8).map((c) => <span key={c} className="rounded bg-slate-100 px-1.5 py-px text-2xs text-slate-700">{c}</span>)}</div>}
      </div>
    </li>
  );
});

function Section({ s, defaultOpen, focus, onHover }: { s: StreamStage; defaultOpen: boolean; focus?: string | null; onHover?: (k: string | null) => void }) {
  const [open, setOpen] = useState(defaultOpen);
  useEffect(() => { if (s.state === "running") setOpen(true); }, [s.state]);
  const pages = s.items.filter((i) => i.key.startsWith("p:"));
  const [full, setFull] = useState(false);
  // a long crawl opens on its first lines and the latest one, with the rest one click away
  const CAP = 12;
  const shown = open ? (full || s.items.length <= CAP + 2 ? s.items : [...s.items.slice(0, CAP - 1), ...s.items.slice(-1)]) : s.items.filter((i) => i.type !== "activity").slice(-6);
  const hiddenCount = open && !full && s.items.length > CAP + 2 ? s.items.length - CAP : 0;
  return (
    <section className="py-3.5">
      <button onClick={() => setOpen(!open)} aria-expanded={open} className="flex w-full items-start gap-3 text-left">
        <Dot state={s.state} />
        <div className="min-w-0 flex-1">
          <h3 className="text-sm font-semibold text-slate-900">{s.title}</h3>
          <p className="text-xs text-slate-500">{s.state === "running" ? "In progress" : s.state === "failed" ? "Failed" : s.state === "skipped" ? "Skipped" : "Done"}
            {pages.length ? ` · ${pages.filter((p) => p.state === "done").length} of ${pages.length} page(s) read` : ""}{s.summary ? ` · ${s.summary}` : ""}</p>
        </div>
        {s.items.length > 0 && <span className="shrink-0 pt-0.5 text-2xs text-slate-400 hover:text-slate-700">{open ? "Hide steps" : `Show all ${s.items.length}`}</span>}
      </button>
      {shown.length > 0 && <ul className="ml-[7px] mt-1.5 border-l border-slate-200 pl-[18px]">{shown.map((it, i) => (<Fragment key={it.key}>
        {hiddenCount > 0 && i === shown.length - 1 && <li key="more" className="py-1"><button type="button" onClick={() => setFull(true)} className="text-xs font-medium text-brand-700 hover:underline">Show {hiddenCount} more step{hiddenCount === 1 ? "" : "s"}</button></li>}
        <Line it={it} focused={!!focus && focus === it.key} onHover={onHover} /></Fragment>))}</ul>}
    </section>
  );
}

function SideLists({ steps, warnings, sources, columns }: { steps: Step[]; warnings: ResearchEvent[]; sources: ReturnType<typeof buildModel>["sources"]; columns?: boolean }) {
  return (
    <div className={cx("text-[13px]", columns ? "grid gap-x-6 gap-y-5 sm:grid-cols-2 xl:grid-cols-3" : "space-y-5")}>
      {steps.length > 0 && (
        <div>
          <div className="mb-2 text-xs font-medium text-slate-500">Tasks</div>
          <ol className="space-y-1.5">{steps.map((s) => (
            <li key={s.key} className="flex items-start gap-2">
              <Dot state={s.status === "DONE" || s.status === "COMPLETED" ? "done" : s.status === "RUNNING" ? "running" : s.status === "FAILED" ? "failed" : s.status === "SKIPPED" ? "skipped" : "pending"} />
              <span className={cx("min-w-0", s.status === "PENDING" || s.status === "QUEUED" ? "text-slate-500" : "text-slate-800")}>{s.name.replace(/^\d+ · /, "")}</span>
            </li>))}</ol>
        </div>)}
      {warnings.length > 0 && (
        <div>
          <div className="mb-2 text-xs font-medium text-slate-500">Limitations & failures</div>
          <ul className="space-y-1.5 text-xs leading-5 text-amber-900">{warnings.slice(-8).map((w) => <li key={w.seq} className="break-words border-l-2 border-amber-300 pl-2">{describe(w)}</li>)}</ul>
        </div>)}
      {sources.length > 0 && (
        <div>
          <div className="mb-2 text-xs font-medium text-slate-500">Sources</div>
          <ol className="space-y-1.5">{sources.slice(0, 12).map((s, i) => (
            <li key={s.key} className="flex gap-2 text-xs"><span className="tabular mt-px w-4 shrink-0 text-right text-slate-400">{i + 1}</span>
              <div className="min-w-0">{s.url ? <a href={s.url} target="_blank" rel="noreferrer noopener" className="block truncate font-medium text-slate-800 hover:text-brand-700 hover:underline" title={s.url}>{s.label || hostOf(s.url)}</a>
                : <span className="block truncate font-medium text-slate-800">{s.label}</span>}
                <span className="text-2xs text-slate-400">{s.url ? hostOf(s.url) : providerLabel(s.provider)} · {s.kind}</span></div></li>))}
          </ol>
          {sources.length > 12 && <p className="mt-1 text-2xs text-slate-400">and {sources.length - 12} more</p>}
        </div>)}
    </div>
  );
}

const TONE_MARK: Record<DigestItem["tone"], [string, string]> = {
  finding: ["bg-brand-600", "Finding"], gap: ["bg-amber-500", "Gap to fix"], limit: ["bg-slate-400", "Limitation"],
};

/** The discoveries, in plain language, ahead of the step log. Hovering a line lights the matching point in the scene. */
function Digest({ items, live, focus, onHover }: { items: DigestItem[]; live: boolean; focus: string | null; onHover: (k: string | null) => void }) {
  const [all, setAll] = useState(false);
  const shown = all ? items : items.slice(0, 9);
  return (
    <section aria-label="What was found" className="border-b border-slate-200 px-5 pb-4 pt-4">
      <div className="mb-2.5 flex items-baseline justify-between gap-3">
        <h3 className="text-sm font-semibold text-slate-900">{live ? "Found so far" : "What was found"}</h3>
        <span className="text-2xs text-slate-400">from recorded research{live ? ", updating" : ""}</span>
      </div>
      <ul className="space-y-0.5">
        {shown.map((it) => (
          <li key={it.key} onMouseEnter={it.ref ? () => onHover(it.ref!) : undefined} onMouseLeave={it.ref ? () => onHover(null) : undefined}
            className={cx("lf-enter -mx-2 flex gap-2.5 rounded-md px-2 py-1.5", it.ref && "cursor-default", it.ref && focus === it.ref && "bg-brand-50")}>
            <span aria-hidden className={cx("mt-[7px] h-1.5 w-1.5 shrink-0 rounded-sm", TONE_MARK[it.tone][0])} />
            <div className="min-w-0">
              <div className="text-[13px] font-medium leading-5 text-slate-900"><span className="sr-only">{TONE_MARK[it.tone][1]}: </span>{it.title}</div>
              {it.body && <div className="text-xs leading-5 text-slate-600">{it.body}</div>}
              {!!it.chips?.length && <div className="mt-1 flex flex-wrap gap-1">{it.chips.map((c) => <span key={c} className="rounded bg-slate-100 px-1.5 py-px text-2xs text-slate-700">{c}</span>)}</div>}
            </div>
          </li>))}
      </ul>
      {items.length > 9 && <button type="button" onClick={() => setAll(!all)} className="mt-1.5 text-xs font-medium text-brand-700 hover:underline">{all ? "Show fewer" : `Show all ${items.length}`}</button>}
      <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-2xs text-slate-500" aria-hidden>
        {(["finding", "gap", "limit"] as const).filter((t) => items.some((i) => i.tone === t)).map((t) => <span key={t} className="inline-flex items-center gap-1.5"><span className={cx("h-1.5 w-1.5 rounded-sm", TONE_MARK[t][0])} />{TONE_MARK[t][1]}</span>)}
      </div>
    </section>
  );
}

/** Live research: what is happening now, the tasks, readable findings as they arrive, numbered sources, and every limitation or
 *  failure in place. Built only from recorded backend events; when nothing is running it says so.
 *  With `kind`, a scene of what is being researched sits beside the findings. Both read the same single event stream, and hovering
 *  an item in one highlights it in the other. */
export function LiveFindings({ url, active, steps: givenSteps, title = "Research activity", kind, subject, result }: { url: string; active: boolean; steps?: Step[]; title?: string; kind?: SceneKind; subject?: { website?: string; business?: string }; result?: any }) {
  const { events, steps: streamSteps, done, error } = useEventStream(url, active);
  const stream = useMemo(() => buildStream(events), [events]);
  const model = useMemo(() => {
    const m = buildModel(events);
    // the audit record names the website even before its first event does; only used to label the centre of the scene
    if (!m.domain && subject?.website) m.domain = hostOf(subject.website);
    if (!m.business && subject?.business) m.business = subject.business;
    return m;
  }, [events, subject?.website, subject?.business]);
  const live = active && !done;
  const now = useNow(live);
  const steps = givenSteps ?? streamSteps ?? [];
  const current = steps.find((s) => s.status === "RUNNING");
  const last = events[events.length - 1];
  const sources = model.sources;
  const warnings = model.warnings;
  const [focus, setFocus] = useState<string | null>(null);
  const digest = useMemo(() => (kind ? buildDigest(kind, model, result) : []), [kind, model, result]);
  const stages = useMemo<StageState>(() => {
    const st: StageState = {};
    for (const s of stream) st[s.stage] = s.state;
    for (const s of steps) if (!st[s.key]) st[s.key] = s.status === "RUNNING" ? "running" : s.status === "DONE" || s.status === "COMPLETED" ? "done" : s.status === "FAILED" ? "failed" : s.status === "SKIPPED" ? "skipped" : "waiting";
    return st;
  }, [stream, steps]);

  // keep the newest finding in view while research is live, unless the reader has scrolled up to read something
  const scroller = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);
  useEffect(() => {
    const el = scroller.current;
    if (el && kind && live && pinned.current) el.scrollTop = el.scrollHeight;
  }, [events.length, kind, live]);

  const statusLine = (
    <div role="status" aria-live="polite" className="flex min-w-0 items-center gap-2 text-[13px] text-slate-600">
      {live ? (<>
        <span className="relative flex h-2 w-2 shrink-0"><span className="absolute inline-flex h-full w-full rounded-full bg-brand-500 opacity-60 motion-safe:animate-ping" /><span className="relative inline-flex h-2 w-2 rounded-full bg-brand-600" /></span>
        <span className="truncate">{last ? `${current ? (STAGE_TITLE[current.key] ?? current.name.replace(/^\d+ · /, "")) + " · " : ""}last update ${ago(now, last.at)}` : "Waiting for the research worker to record its first step"}</span>
        {last && (now - new Date(last.at).getTime()) > 20000 && <span className="shrink-0 text-amber-700">· waiting for a slow source to answer</span>}
      </>) : <span>{model.finished?.kind === "audit_failed" ? "Stopped." : events.length ? `Finished · ${events.length} recorded step(s)` : "No activity was recorded for this run."}</span>}
    </div>
  );

  const streamBody = stream.length === 0 ? (
    <p className="py-6 text-[13px] text-slate-500">{live ? "Nothing has been recorded yet. Activity appears here the moment the first source is contacted." : "There is no recorded activity for this audit."}</p>
  ) : stream.map((s) => <Section key={s.stage} s={s} defaultOpen={live ? s.state === "running" : s.state === "failed"} focus={focus} onHover={kind ? setFocus : undefined} />);

  const technical = (
    <details className="group border-t border-slate-100 px-5 py-2.5 text-xs">
      <summary className="cursor-pointer text-slate-500 hover:text-slate-800">Technical details ({events.length} events)</summary>
      <div className="pane-scroll mt-2 max-h-72 overflow-auto rounded-md bg-slate-50 p-2 font-mono text-2xs leading-4 text-slate-600">{events.map((e) => <div key={e.seq}>#{e.seq} {e.at} [{e.level}] {e.stage || "-"} {e.kind} — {e.title}</div>)}</div>
    </details>
  );

  if (kind) return (
    <section aria-label="Live findings" className="overflow-hidden rounded-lg border border-slate-200 bg-white">
      <header className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-slate-100 px-5 py-3">
        <h2 className="text-sm font-semibold text-slate-900">{title}</h2>
        {statusLine}
        {sources.length > 0 && <span className="ml-auto rounded bg-slate-100 px-1.5 py-px text-2xs font-medium text-slate-600">{sources.length} source{sources.length === 1 ? "" : "s"}</span>}
      </header>
      <ErrorBox error={error} />
      <div className="grid xl:grid-cols-[minmax(0,1.15fr)_minmax(380px,1fr)]">
        <div className="min-w-0 border-b border-slate-100 p-3 xl:border-b-0 xl:border-r">
          <div className="xl:sticky xl:top-[64px]">
            <ResearchScene kind={kind} model={model} stages={stages} live={live} focus={focus} onFocus={setFocus} className="h-[340px] sm:h-[440px] xl:h-[min(520px,calc(100vh-150px))]" />
            {sources.length > 0 && <div className="pane-scroll hidden max-h-[calc(100vh-700px)] min-h-[140px] overflow-y-auto px-2 pb-2 pt-5 xl:block"><SideLists steps={[]} warnings={[]} sources={sources} /></div>}
          </div>
        </div>
        <div className="min-w-0">
          {digest.length > 0 && <Digest items={digest} live={live} focus={focus} onHover={setFocus} />}
          <div className="flex items-center justify-between px-5 pt-4 text-xs text-slate-500">
            <span className="font-medium text-slate-700">Step by step</span>
            <span>{events.length} recorded step(s) · {warnings.length} limitation(s)</span>
          </div>
          <div ref={scroller} onScroll={(e) => { const el = e.currentTarget; pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80; }}
            className="pane-scroll divide-y divide-slate-100 px-5 xl:max-h-[640px] xl:overflow-y-auto">{streamBody}</div>
          <div className="border-t border-slate-100 bg-slate-50/50 px-5 py-4">
            <SideLists steps={steps} warnings={warnings} sources={[]} />
            {sources.length > 0 && <div className="mt-5 xl:hidden"><SideLists steps={[]} warnings={[]} sources={sources} /></div>}
          </div>
        </div>
      </div>
      {technical}
    </section>
  );

  return (
    <section aria-label="Live findings" className="overflow-hidden rounded-lg border border-slate-200 bg-white">
      <header className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-slate-100 px-5 py-3">
        <h2 className="text-sm font-semibold text-slate-900">{title}</h2>
        {statusLine}
        {sources.length > 0 && <span className="ml-auto rounded bg-slate-100 px-1.5 py-px text-2xs font-medium text-slate-600">{sources.length} source{sources.length === 1 ? "" : "s"}</span>}
      </header>
      <ErrorBox error={error} />
      <div className="grid lg:grid-cols-[minmax(0,1fr)_280px]">
        <div className="divide-y divide-slate-100 px-5">{streamBody}</div>
        <aside className="border-t border-slate-100 bg-slate-50/50 px-5 py-4 lg:border-l lg:border-t-0"><SideLists steps={steps} warnings={warnings} sources={sources} /></aside>
      </div>
      {technical}
    </section>
  );
}
