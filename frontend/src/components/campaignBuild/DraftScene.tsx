import { lazy, Suspense, useMemo, useState } from "react";
import { cx } from "../ui";
import type { ResearchEvent } from "../research/model";
import { canUseWebGL, useReducedMotion } from "../research/ResearchScene";
import { draftScene, type Phase } from "./draftScene";
import { providerName } from "./buildModel";

const DraftScene3D = lazy(() => import("./DraftScene3D"));
const DOT: Record<string, string> = { done: "bg-emerald-400", running: "bg-brand-300 motion-safe:animate-pulse", failed: "bg-rose-400", skipped: "bg-amber-400", waiting: "bg-white/20" };
const WORD: Record<string, string> = { done: "done", running: "in progress", failed: "failed", skipped: "skipped", waiting: "not started" };

function Phases({ title, phases }: { title: string; phases: Phase[] }) {
  return (
    <div>
      <div className="mb-1 text-2xs font-medium uppercase tracking-wide text-ink-muted">{title}</div>
      <ol className="space-y-0.5">{phases.map((p) => (
        <li key={p.key} className="flex items-center gap-1.5 text-xs">
          <span aria-hidden className={cx("h-1.5 w-1.5 shrink-0 rounded-full", DOT[p.state])} />
          <span className={p.state === "waiting" ? "text-ink-muted" : "text-white"}>{p.label}</span>
          <span className="text-2xs text-ink-muted">{p.detail ?? WORD[p.state]}</span>
        </li>))}</ol>
    </div>
  );
}

/** The drafting and independent review as they happen, from recorded events only. */
export function DraftScene({ events, live, className }: { events: ResearchEvent[]; live: boolean; className?: string }) {
  const m = useMemo(() => draftScene(events), [events]);
  const reduced = useReducedMotion();
  const [gl, setGl] = useState(() => canUseWebGL());
  const [flat, setFlat] = useState(false);
  const use3d = gl && !flat;
  const tok = (a: number, b: number) => (a || b ? `${a.toLocaleString()} in · ${b.toLocaleString()} out` : "no tokens reported yet");
  const c = m.challenger;
  const chalLine = c.state === "skipped" ? `Not performed: ${c.reason}` : c.state === "failed" ? `Review failed: ${c.reason}` : c.state === "done" ? `Verdict ${c.verdict} · ${c.objections} concern(s)` :
    c.state === "reviewing" ? "Reviewing the draft against its evidence" : "Starts after the draft is assembled";
  const summary = `AI drafting: ${m.phases.map((p) => `${p.label} ${WORD[p.state]}`).join(", ")}. Challenger: ${chalLine}.`;
  return (
    <div className={cx("relative overflow-hidden rounded-lg bg-ink text-slate-200", className)} role="img" aria-label={summary}>
      <div className="absolute inset-0" style={{ background: "radial-gradient(120% 90% at 50% 30%, #13222e 0%, #0b131b 72%)" }} aria-hidden />
      {use3d ? <Suspense fallback={null}><DraftScene3D model={m} live={live} reduced={reduced} onFail={() => setGl(false)} /></Suspense> : (
        <div className="absolute inset-0 flex items-center justify-center gap-6 px-6" aria-hidden>
          {m.groups.map((g) => <div key={g.name} title={g.name} className={cx("w-6 rounded-sm", g.state === "written" ? "bg-brand-200" : g.state === "writing" ? "bg-brand-400" : "bg-white/15")}
            style={{ height: `${g.state === "written" ? 16 + Math.log2(1 + g.keywords) * 14 : 12}px` }} />)}
        </div>)}
      <div className="pointer-events-none absolute inset-x-0 top-0 flex items-start justify-between gap-3 p-4">
        <div className="min-w-0 max-w-[46%]">
          <div className="flex items-center gap-2 text-[13px] font-semibold text-white"><span className="h-2 w-2 rounded-full bg-brand-300" />Main AI
            {m.main.state === "requesting" && live && <span className="rounded bg-brand-500/25 px-1.5 py-px text-2xs text-brand-100">request in flight</span>}
            {m.main.state === "waiting" && <span className="rounded bg-amber-500/25 px-1.5 py-px text-2xs text-amber-100">waiting on provider{m.main.waitSeconds ? ` · ${Math.round(m.main.waitSeconds)}s stated` : ""}</span>}</div>
          <div className="truncate text-2xs text-ink-muted">{m.main.provider ? `${providerName(m.main.provider)}${m.main.model ? ` · ${m.main.model}` : ""}` : "provider not called yet"}</div>
          <div className="tabular text-2xs text-ink-muted">{m.main.requests} drafting request(s) · {tok(m.main.tokensIn, m.main.tokensOut)}</div>
        </div>
        <div className="min-w-0 max-w-[46%] text-right">
          <div className="flex items-center justify-end gap-2 text-[13px] font-semibold text-white">Challenger AI<span className="h-2 w-2 rounded-full bg-plum-300" /></div>
          <div className="truncate text-2xs text-ink-muted">{c.provider ? `${c.provider}${c.model ? ` · ${c.model}` : ""}` : "not called yet"}</div>
          <div className={cx("text-2xs", c.state === "skipped" || c.state === "failed" ? "text-amber-200" : "text-ink-muted")}>{chalLine}</div>
        </div>
      </div>
      <div className="pointer-events-auto absolute right-3 top-[4.6rem] flex rounded-md bg-white/[0.06] p-0.5 text-2xs ring-1 ring-inset ring-white/10" role="group" aria-label="Scene view">
        {gl && <button type="button" aria-pressed={!flat} onClick={() => setFlat(false)} className={cx("rounded px-2 py-1 font-medium", !flat ? "bg-white/15 text-white" : "text-ink-muted")}>3D</button>}
        <button type="button" aria-pressed={flat || !gl} onClick={() => setFlat(true)} className={cx("rounded px-2 py-1 font-medium", flat || !gl ? "bg-white/15 text-white" : "text-ink-muted")}>Flat</button>
      </div>
      {!m.started && <div className="pointer-events-none absolute inset-x-0 top-1/2 -translate-y-1/2 text-center text-[13px] text-slate-400">Drafting starts once research, keywords and feasibility are done.</div>}
      <div className="pointer-events-none absolute inset-x-0 bottom-0 grid grid-cols-2 gap-4 bg-gradient-to-t from-ink via-ink/85 to-transparent px-4 pb-3 pt-10">
        <Phases title="Drafting" phases={m.phases} />
        <div className="justify-self-end"><Phases title="Independent review" phases={c.phases} />
          {(c.tokensIn > 0 || c.tokensOut > 0) && <div className="tabular mt-0.5 text-2xs text-ink-muted">{tok(c.tokensIn, c.tokensOut)}</div>}</div>
      </div>
    </div>
  );
}
