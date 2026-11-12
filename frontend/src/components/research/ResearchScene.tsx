import { lazy, Suspense, useEffect, useMemo, useState } from "react";
import { cx } from "../ui";
import type { ResearchModel } from "./model";
import { buildScene, SCENE_PALETTE as C, sceneCounts, type SceneGraph, type SceneKind, type SceneNode, type StageState } from "./sceneLayout";

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
  const P = (x: number, y: number, z: number) => [x, z * 0.5 - y * 0.9] as const;
  const pad = 1.4;
  const xs = graph.nodes.map((n) => n.x).concat(graph.islands.flatMap((i) => [i.x - i.w / 2, i.x + i.w / 2]), graph.rings.flatMap((r) => [r.x - r.r, r.x + r.r]));
  const zs = graph.nodes.map((n) => n.z * 0.5 - n.y * 0.9).concat(graph.rings.flatMap((r) => [r.z * 0.5 - r.r * 0.5, r.z * 0.5 + r.r * 0.5]), graph.islands.flatMap((i) => [-i.d / 4, i.d / 4]));
  const minX = Math.min(-2, ...xs) - pad, maxX = Math.max(2, ...xs) + pad, minY = Math.min(-2, ...zs) - pad, maxY = Math.max(2, ...zs) + pad;
  const byId = new Map(graph.nodes.map((n) => [n.id, n]));
  const sorted = graph.nodes.slice().sort((a, b) => a.z - b.z);
  const f = focus ? byId.get(focus) : undefined;
  return (
    <svg viewBox={`${minX} ${minY} ${maxX - minX} ${maxY - minY}`} className="absolute inset-0 h-full w-full" preserveAspectRatio="xMidYMid meet" aria-hidden onMouseLeave={() => onFocus(null)}>
      {graph.islands.map((i) => (
        <g key={i.id}>
          <ellipse cx={i.x} cy={0} rx={i.w / 2} ry={i.d / 4} fill="#13202b" stroke={i.state === "running" ? C.working : i.state === "done" ? "#7fae95" : i.state === "failed" ? C.failed : C.gridStrong} strokeWidth={0.04} opacity={i.state === "waiting" ? 0.6 : 1} />
          <text x={i.x} y={i.d / 4 + 0.55} textAnchor="middle" fontSize={0.42} fill="#dce1e4">{i.label}</text>
        </g>))}
      {graph.rings.map((r, i) => <g key={i} opacity={r.dim ? 0.35 : 1}><ellipse cx={r.x} cy={r.z * 0.5} rx={r.r} ry={r.r * 0.5} fill="none" stroke={C.gridStrong} strokeWidth={0.035} strokeDasharray="0.18 0.14" />
        {r.label && <text x={r.x + r.r * 0.72} y={r.r * 0.36 + 0.25} fontSize={0.3} fill={C.edge}>{r.label}</text>}</g>)}
      {graph.edges.map((e, i) => {
        const a = byId.get(e.a), b = byId.get(e.b); if (!a || !b) return null;
        const [x1, y1] = P(a.x, a.y, a.z), [x2, y2] = P(b.x, b.y, b.z);
        return <line key={i} x1={x1} y1={y1} x2={x2} y2={y2} stroke={C.edge} strokeWidth={0.03} opacity={e.faint || e.dim ? 0.4 : 0.85} />;
      })}
      {sorted.map((n) => {
        const [x, y] = P(n.x, n.y + (n.pin ? n.size * 3.2 : 0), n.z);
        const [, gy] = P(n.x, 0, n.z);
        return (
          <g key={n.id} opacity={n.dim ? 0.3 : 1} onMouseEnter={() => onFocus(n.id)} style={{ cursor: "pointer" }}>
            {n.pin && <line x1={x} y1={gy} x2={x} y2={y} stroke={C.edge} strokeWidth={0.03} />}
            <circle cx={x} cy={y} r={Math.max(n.size, 0.07)} fill={nodeColor(n)} />
            {n.id === focus && <circle cx={x} cy={y} r={Math.max(n.size, 0.07) * 1.6} fill="none" stroke="#fff" strokeWidth={0.04} />}
            {n.hub && <text x={x} y={y - n.size - 0.22} textAnchor="middle" fontSize={0.36} fill="#fff">{n.label.length > 28 ? n.label.slice(0, 27) + "…" : n.label}</text>}
          </g>);
      })}
      {f && (() => { const [x, y] = P(f.x, f.y + (f.pin ? f.size * 3.2 : 0), f.z); const t = f.label.length > 40 ? f.label.slice(0, 39) + "…" : f.label; const w = Math.max(t.length * 0.2 + 0.5, 2);
        return <g pointerEvents="none"><rect x={x - w / 2} y={y - f.size - 0.95} width={w} height={0.62} rx={0.1} fill="#fff" /><text x={x} y={y - f.size - 0.54} textAnchor="middle" fontSize={0.32} fill="#15212c">{t}</text></g>; })()}
    </svg>
  );
}

/** A research scene: what is being researched, drawn from the same events as the findings next to it. */
export function ResearchScene({ kind, model, stages, live, focus, onFocus, className }: {
  kind: SceneKind; model: ResearchModel; stages?: StageState; live: boolean; focus: string | null; onFocus: (id: string | null) => void; className?: string;
}) {
  const graph = useMemo(() => buildScene(kind, model, stages), [kind, model, stages]);
  const counts = useMemo(() => sceneCounts(kind, model), [kind, model]);
  const reduced = useReducedMotion();
  const [gl, setGl] = useState(() => canUseWebGL());
  const [flat, setFlat] = useState(false);
  const [resetKey, setResetKey] = useState(0);
  const use3d = gl && !flat;
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
            {live && <span className="inline-flex items-center gap-1.5 rounded bg-brand-500/20 px-1.5 py-px text-2xs font-medium text-brand-200"><span className="h-1.5 w-1.5 rounded-full bg-brand-300 motion-safe:animate-pulse" />live</span>}
          </div>
          <p className="mt-0.5 hidden max-w-sm text-xs leading-4 text-ink-muted sm:block">{SCENE_HINT[kind]}</p>
        </div>
        <div className="pointer-events-auto flex shrink-0 items-center rounded-md bg-white/[0.06] p-0.5 text-2xs ring-1 ring-inset ring-white/10" role="group" aria-label="Scene view">
          {gl && <button type="button" aria-pressed={!flat} onClick={() => setFlat(false)} className={cx("rounded px-2 py-1 font-medium", !flat ? "bg-white/15 text-white" : "text-ink-muted hover:text-white")}>3D</button>}
          <button type="button" aria-pressed={flat || !gl} onClick={() => setFlat(true)} className={cx("rounded px-2 py-1 font-medium", flat || !gl ? "bg-white/15 text-white" : "text-ink-muted hover:text-white")}>Flat</button>
          {use3d && <button type="button" onClick={() => setResetKey((k) => k + 1)} className="rounded px-2 py-1 font-medium text-ink-muted hover:text-white">Reset view</button>}
        </div>
      </div>

      {graph.empty && <div className="pointer-events-none absolute inset-x-0 top-1/2 -translate-y-1/2 px-6 text-center text-[13px] text-slate-400">{graph.empty}</div>}

      <dl className="pointer-events-none absolute inset-x-0 bottom-0 flex flex-wrap items-end gap-x-5 gap-y-1 bg-gradient-to-t from-ink via-ink/80 to-transparent px-4 pb-3 pt-8">
        {counts.map((c) => (
          <div key={c.label} className="flex items-baseline gap-1.5">
            <span aria-hidden className="h-2 w-2 self-center rounded-full" style={{ background: c.tone === "series0" ? C.series[0] : (C as any)[c.tone] }} />
            <dd className="tabular text-sm font-semibold text-white">{c.value}</dd><dt className="text-xs text-ink-muted">{c.label}</dt>
          </div>))}
        {use3d && <span className="ml-auto hidden text-2xs text-ink-muted md:block">Drag to rotate</span>}
      </dl>
    </div>
  );
}
