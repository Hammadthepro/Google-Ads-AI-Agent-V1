import { lazy, Suspense, useEffect, useMemo, useState } from "react";
import { cx } from "../ui";
import type { ResearchModel } from "./model";
import { buildScene, nameOrder, SCENE_PALETTE as C, sceneCounts, type SceneGraph, type SceneKind, type SceneNode, type StageState } from "./sceneLayout";

const Scene3D = lazy(() => import("./Scene3D"));

export const SCENE_TITLE: Record<SceneKind, string> = {
  website: "Site map", local: "Local market", competitors: "Competitive field", keywords: "Keyword landscape", combined: "Research streams",
};
const SCENE_HINT: Record<SceneKind, string> = {
  website: "Each point is a page the crawler opened, placed under the page whose address contains it.",
  local: "The business at the centre; map listings at their real distance and direction.",
  competitors: "Competitors on orbits around the business; their pages that were read sit beside them.",
  keywords: "One cluster per keyword source that answered, with the example terms it returned.",
  combined: "One island per research stream. An island lights up when its stage records its first step.",
};

const FINISHED_EMPTY: Record<SceneKind, string> = {
  website: "No page was opened during this run.", local: "No location or map listing was recorded in this run.",
  competitors: "No competitor was recorded in this run.", keywords: "No keyword source answered in this run.", combined: "Nothing was recorded in this run.",
};

let webglCache: boolean | null = null;
export function canUseWebGL(): boolean {
  if (webglCache !== null) return webglCache;
  try {
    if (typeof window === "undefined" || !("WebGLRenderingContext" in window)) return (webglCache = false);
    const c = document.createElement("canvas");
    webglCache = !!(c.getContext("webgl2") || c.getContext("webgl"));
  } catch { webglCache = false; }
  return webglCache;
}

export function useReducedMotion() {
  const q = typeof window !== "undefined" && window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : null;
  const [r, setR] = useState(!!q?.matches);
  useEffect(() => {
    if (!q) return;
    const on = () => setR(q.matches);
    q.addEventListener?.("change", on);
    return () => q.removeEventListener?.("change", on);
  }, [q]);
  return r;
}

const nodeColor = (n: SceneNode) => n.role === "cluster" || n.role === "term" ? C.series[(n.tint ?? 0) % C.series.length]
  : (({ hub: C.hub, page: C.page, working: C.working, failed: C.failed, blocked: C.blocked, competitor: C.competitor, business: C.business, listing: C.listing, host: C.host } as Record<string, string>)[n.role] ?? C.page);

/** The same layout drawn flat (oblique projection). Used without WebGL, while the 3D view loads, and when the user prefers it. */
export function SceneSvg({ graph, focus, onFocus }: { graph: SceneGraph; focus: string | null; onFocus: (id: string | null) => void }) {
  // a gentle top-down projection: depth folds into the vertical axis, height lifts a little
  const P = (x: number, y: number, z: number) => [x, z * 0.55 - y * 0.35] as const;
  const xs = graph.nodes.map((n) => n.x).concat(graph.islands.flatMap((i) => [i.x - i.w / 2, i.x + i.w / 2]), graph.rings.flatMap((r) => [r.x - r.r, r.x + r.r]));
  const ys = graph.nodes.map((n) => n.z * 0.55 - n.y * 0.35).concat(graph.rings.flatMap((r) => [r.z * 0.55 - r.r * 0.55, r.z * 0.55 + r.r * 0.55]), graph.islands.flatMap((i) => [-i.d * 0.28, i.d * 0.28]));
  const pad = Math.max(1.2, (Math.max(...xs, 2) - Math.min(...xs, -2)) * 0.08);
  const minX = Math.min(-2, ...xs) - pad, maxX = Math.max(2, ...xs) + pad, minY = Math.min(-2, ...ys) - pad * 1.6, maxY = Math.max(2, ...ys) + pad * 1.6;
  // text and strokes are sized against the drawing's width, so they read the same whether the scene is small or large
  const u = (maxX - minX) / 100;
  const byId = new Map(graph.nodes.map((n) => [n.id, n]));
  const sorted = graph.nodes.slice().sort((a, b) => a.z - b.z);
  const f = focus ? byId.get(focus) : undefined;
  const r = (n: SceneNode) => Math.max(n.size, 0.55 * u);
  const lift = (n: SceneNode) => (n.pin ? n.size * 3.2 : 0);
  const clip = (t: string, n: number) => (t.length > n ? t.slice(0, n - 1) + "…" : t);
  // same rule as the 3D view: the things a scene is about are named when there are few enough to read
  const named = new Set(nameOrder(graph.nodes, 8).map((n) => n.id));
  return (
    <svg viewBox={`${minX} ${minY} ${maxX - minX} ${maxY - minY}`} className="absolute inset-0 h-full w-full" preserveAspectRatio="xMidYMid meet" aria-hidden onMouseLeave={() => onFocus(null)}>
      {graph.islands.map((i) => (
        <g key={i.id}>
          <ellipse cx={i.x} cy={0} rx={i.w / 2} ry={i.d * 0.28} fill="#13202b" stroke={i.state === "running" ? C.working : i.state === "done" ? "#7fae95" : i.state === "failed" ? C.failed : C.gridStrong} strokeWidth={0.25 * u} opacity={i.state === "waiting" ? 0.6 : 1} />
          <text x={i.x} y={i.d * 0.28 + 3.2 * u} textAnchor="middle" fontSize={2.4 * u} fill="#dce1e4">{i.label}</text>
          <text x={i.x} y={i.d * 0.28 + 5.6 * u} textAnchor="middle" fontSize={1.9 * u} fill={C.host}>{i.state === "running" ? "researching" : i.state === "waiting" ? "not started" : i.state}</text>
        </g>))}
      {graph.rings.map((g, i) => <g key={i} opacity={g.dim ? 0.35 : 1}><ellipse cx={g.x} cy={g.z * 0.55} rx={g.r} ry={g.r * 0.55} fill="none" stroke={C.gridStrong} strokeWidth={0.2 * u} strokeDasharray={`${u} ${0.8 * u}`} />
        {g.label && <text x={g.x + g.r * 0.72} y={g.r * 0.4 + 2 * u} fontSize={1.8 * u} fill={C.edge}>{g.label}</text>}</g>)}
      {graph.edges.map((e, i) => {
        const a = byId.get(e.a), b = byId.get(e.b); if (!a || !b) return null;
        const [x1, y1] = P(a.x, a.y + lift(a), a.z), [x2, y2] = P(b.x, b.y + lift(b), b.z);
        return <line key={i} x1={x1} y1={y1} x2={x2} y2={y2} stroke={C.edge} strokeWidth={0.18 * u} opacity={e.faint || e.dim ? 0.4 : 0.85} />;
      })}
      {sorted.map((n) => {
        const [x, y] = P(n.x, n.y + lift(n), n.z);
        const [, gy] = P(n.x, 0, n.z);
        return (
          <g key={n.id} opacity={n.dim ? 0.3 : 1} onMouseEnter={() => onFocus(n.id)} style={{ cursor: "pointer" }}>
            {n.pin && <line x1={x} y1={gy} x2={x} y2={y} stroke={C.edge} strokeWidth={0.18 * u} />}
            <circle cx={x} cy={y} r={r(n)} fill={nodeColor(n)} />
            {n.id === focus && <circle cx={x} cy={y} r={r(n) + 0.9 * u} fill="none" stroke="#fff" strokeWidth={0.3 * u} />}
            {n.hub && <text x={x} y={y - r(n) - 1.4 * u} textAnchor="middle" fontSize={2.3 * u} fontWeight={500} fill="#fff">{clip(n.label, 30)}</text>}
            {named.has(n.id) && n.id !== focus && <text x={x} y={y + r(n) + 2.6 * u} textAnchor="middle" fontSize={1.9 * u} fill="#bcc5cb">{clip(n.label, 26)}</text>}
          </g>);
      })}
      {f && (() => { const [x, y] = P(f.x, f.y + lift(f), f.z); const t = clip(f.label, 44); const w = Math.max(t.length * 1.15 * u + 3 * u, 12 * u);
        const bx = Math.min(Math.max(x - w / 2, minX + u), maxX - w - u), by = y - r(f) - 6.2 * u;
        return <g pointerEvents="none"><rect x={bx} y={by} width={w} height={4.4 * u} rx={0.7 * u} fill="#fff" /><text x={bx + w / 2} y={by + 2.95 * u} textAnchor="middle" fontSize={2.05 * u} fill="#15212c">{t}</text></g>; })()}
    </svg>
  );
}

/** A research scene: what is being researched, drawn from the same events as the findings next to it. */
export function ResearchScene({ kind, model, stages, live, focus, onFocus, className, fixture, queued }: {
  kind: SceneKind; model: ResearchModel; stages?: StageState; live: boolean; focus: string | null; onFocus: (id: string | null) => void; className?: string;
  /** The backend marked this data as a test fixture: every point is labelled as such, never as a real business. */ fixture?: boolean;
  /** Accepted but not started: nothing is live yet. */ queued?: boolean;
}) {
  const graph = useMemo(() => buildScene(kind, model, stages), [kind, model, stages]);
  const counts = useMemo(() => sceneCounts(kind, model), [kind, model]);
  const reduced = useReducedMotion();
  const [gl, setGl] = useState(() => canUseWebGL());
  const [flat, setFlat] = useState(false);
  const [resetKey, setResetKey] = useState(0);
  const use3d = gl && !flat;
  const coarse = typeof window !== "undefined" && !!window.matchMedia?.("(pointer: coarse)").matches;
  // once the run is over, an empty scene says nothing was recorded rather than "not yet"
  const emptyText = !graph.empty ? null : live ? graph.empty : FINISHED_EMPTY[kind];
  const summary = `${SCENE_TITLE[kind]}: ${counts.map((c) => `${c.value} ${c.label}`).join(", ")}.`;
  return (
    <div className={cx("relative overflow-hidden rounded-lg bg-ink text-slate-200", className)} role="img" aria-label={summary}>
      <div className="absolute inset-0" style={{ background: "radial-gradient(120% 90% at 50% 35%, #142330 0%, #0e1720 70%)" }} aria-hidden />
      {use3d ? (
        <Suspense fallback={<SceneSvg graph={graph} focus={focus} onFocus={onFocus} />}>
          <Scene3D graph={graph} focus={focus} onFocus={onFocus} live={live} reduced={reduced} onFail={() => setGl(false)} resetKey={resetKey} />
        </Suspense>
      ) : <SceneSvg graph={graph} focus={focus} onFocus={onFocus} />}

      <div className="pointer-events-none absolute inset-x-0 top-0 flex items-start justify-between gap-3 p-4">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-[13px] font-semibold text-white">
            {SCENE_TITLE[kind]}
            {fixture && <span className="rounded bg-plum-500/30 px-1.5 py-px text-2xs font-medium text-plum-50">Test fixture — not real businesses</span>}
            {live && queued && <span className="rounded bg-white/10 px-1.5 py-px text-2xs font-medium text-slate-300">waiting to start</span>}
            {live && !queued && <span className="inline-flex items-center gap-1.5 rounded bg-brand-500/20 px-1.5 py-px text-2xs font-medium text-brand-200"><span className="h-1.5 w-1.5 rounded-full bg-brand-300 motion-safe:animate-pulse" />live</span>}
          </div>
          <p className="mt-0.5 hidden max-w-sm text-xs leading-4 text-ink-muted sm:block">{SCENE_HINT[kind]}</p>
        </div>
        <div className="pointer-events-auto flex shrink-0 items-center rounded-md bg-white/[0.06] p-0.5 text-2xs ring-1 ring-inset ring-white/10" role="group" aria-label="Scene view">
          {gl && <button type="button" aria-pressed={!flat} onClick={() => setFlat(false)} className={cx("rounded px-2 py-1 font-medium", !flat ? "bg-white/15 text-white" : "text-ink-muted hover:text-white")}>3D</button>}
          <button type="button" aria-pressed={flat || !gl} onClick={() => setFlat(true)} className={cx("rounded px-2 py-1 font-medium", flat || !gl ? "bg-white/15 text-white" : "text-ink-muted hover:text-white")}>Flat</button>
          {use3d && <button type="button" onClick={() => setResetKey((k) => k + 1)} className="rounded px-2 py-1 font-medium text-ink-muted hover:text-white">Reset view</button>}
        </div>
      </div>

      {emptyText && <div className="pointer-events-none absolute inset-x-0 top-1/2 -translate-y-1/2 px-6 text-center text-[13px] text-slate-400">{emptyText}</div>}

      <dl className="pointer-events-none absolute inset-x-0 bottom-0 flex flex-wrap items-end gap-x-5 gap-y-1 bg-gradient-to-t from-ink via-ink/80 to-transparent px-4 pb-3 pt-8">
        {counts.map((c) => (
          <div key={c.label} className="flex items-baseline gap-1.5">
            <span aria-hidden className="h-2 w-2 self-center rounded-full" style={{ background: c.tone === "series0" ? C.series[0] : (C as any)[c.tone] }} />
            <dd className="tabular text-sm font-semibold text-white">{c.value}</dd><dt className="text-xs text-ink-muted">{c.label}</dt>
          </div>))}
        {use3d && <span className="ml-auto text-2xs text-ink-muted">{coarse ? "Two fingers to rotate" : "Drag to rotate"}</span>}
      </dl>
    </div>
  );
}
