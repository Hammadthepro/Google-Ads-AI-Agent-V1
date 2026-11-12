import { useMemo } from "react";
import { cx } from "../ui";
import { hostOf, providerLabel, type ResearchModel } from "./model";

/** The live research canvas. A layered SVG scene (no WebGL needed, so it renders on any device): the client's site at the centre,
 *  its crawled pages on the inner orbit, competitors and keyword clusters on the outer arcs, web sources above. Every shape comes from a
 *  real backend event; with nothing recorded yet the scene says so. Motion is CSS-only and switched off under prefers-reduced-motion. */
const W = 760, H = 420, CX = W / 2, CY = H / 2 + 6;

function ring(i: number, n: number, rx: number, ry: number, start = -Math.PI / 2, span = Math.PI * 2) {
  const a = start + (span * (i + 0.5)) / Math.max(n, 1);
  return { x: CX + rx * Math.cos(a), y: CY + ry * Math.sin(a), a };
}
const short = (s: string, n: number) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);

export function ResearchCanvas({ model, live }: { model: ResearchModel; live: boolean }) {
  const pages = model.pages.slice(-18);
  const comps = model.competitors.slice(-8);
  const kws = model.keywords.slice(-8);
  const srcs = model.sources.filter((s) => s.provider === "tavily" || s.kind === "Search").slice(-6);
  const lastSeq = model.last?.seq ?? 0;
  const label = useMemo(() => `Research map: ${model.pages.length} page(s) read, ${model.competitors.length} competitor(s), ${model.keywords.length} keyword discovery step(s), ${model.sources.length} source(s).`, [model]);
  const fresh = (seq: number) => live && lastSeq - seq < 4;
  const empty = !model.started && !model.pages.length;
  return (
    <div className="research-scene relative overflow-hidden rounded-xl bg-[#0b0d17] text-white ring-1 ring-white/10">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_at_50%_55%,rgba(76,95,232,0.28),transparent_60%)]" />
      <div className="research-tilt">
        <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={label} className="block h-auto w-full">
          <defs>
            <radialGradient id="rc-core" cx="50%" cy="40%" r="60%"><stop offset="0%" stopColor="#a3b1fb" /><stop offset="55%" stopColor="#4c5fe8" /><stop offset="100%" stopColor="#1c2260" /></radialGradient>
            <filter id="rc-glow" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="3" result="b" /><feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge></filter>
          </defs>
          {[0.42, 0.7, 0.98].map((k, i) => (
            <ellipse key={i} cx={CX} cy={CY} rx={330 * k} ry={150 * k} fill="none" stroke="rgba(163,177,251,0.14)" strokeDasharray={i === 1 ? "2 6" : undefined} />
          ))}
          {/* pages: inner orbit */}
          {pages.map((p, i) => {
            const pt = ring(i, pages.length, 138, 63);
            const color = p.state === "extracted" ? "#7dd3fc" : p.state === "fetching" ? "#a3b1fb" : p.state === "blocked" ? "#fbbf24" : "#fb7185";
            return (
              <g key={p.url}>
                <line x1={CX} y1={CY} x2={pt.x} y2={pt.y} stroke={color} strokeOpacity={0.35} className={cx(p.state === "fetching" && "rc-flow")} strokeDasharray="3 5" />
                <circle cx={pt.x} cy={pt.y} r={p.state === "fetching" ? 4 : 5} fill={p.state === "fetching" ? "none" : color} stroke={color} strokeWidth={1.5}
                  className={cx(fresh(p.seq) && "rc-pop", p.state === "fetching" && "rc-pulse")}><title>{p.url}</title></circle>
                {i % 2 === 0 && <text x={pt.x + (pt.x >= CX ? 8 : -8)} y={pt.y + 3} fontSize="9" fill="rgba(226,232,255,0.7)" textAnchor={pt.x >= CX ? "start" : "end"}>{short(p.path, 22)}</text>}
              </g>);
          })}
          {/* competitors: right arc */}
          {comps.map((c, i) => {
            const pt = ring(i, comps.length, 300, 140, -Math.PI / 2.6, Math.PI / 1.3);
            return (
              <g key={c.name + c.seq} className={cx(fresh(c.seq) && "rc-pop")}>
                <path d={`M${CX},${CY} Q${(CX + pt.x) / 2},${pt.y - 40} ${pt.x},${pt.y}`} fill="none" stroke="#f0abfc" strokeOpacity={0.3} strokeDasharray="2 6" className="rc-flow" />
                <rect x={pt.x - 4} y={pt.y - 4} width={8} height={8} rx={2} fill="#f0abfc" filter="url(#rc-glow)"><title>{`${c.name}${c.basis ? ` — ${c.basis}` : ""}`}</title></rect>
                <text x={pt.x + 9} y={pt.y + 3} fontSize="10" fill="#fae8ff">{short(c.name, 24)}</text>
              </g>);
          })}
          {/* keyword discovery: left arc */}
          {kws.map((k, i) => {
            const pt = ring(i, kws.length, 300, 140, Math.PI / 1.6, Math.PI / 1.3);
            return (
              <g key={k.seq} className={cx(fresh(k.seq) && "rc-pop")}>
                <path d={`M${CX},${CY} Q${(CX + pt.x) / 2},${pt.y + 30} ${pt.x},${pt.y}`} fill="none" stroke="#86efac" strokeOpacity={0.28} strokeDasharray="2 6" className="rc-flow" />
                <circle cx={pt.x} cy={pt.y} r={3 + Math.min(k.count, 12) / 2.5} fill="#86efac" fillOpacity={0.85}><title>{`${k.seed}: ${k.examples.join(", ")}`}</title></circle>
                <text x={pt.x - 10} y={pt.y + 3} fontSize="10" fill="#dcfce7" textAnchor="end">{short(k.seed, 26)}</text>
              </g>);
          })}
          {/* web sources: top band */}
          {srcs.map((s, i) => {
            const x = CX - 220 + (440 * (i + 0.5)) / Math.max(srcs.length, 1);
            return (
              <g key={s.key} className={cx(fresh(s.seq) && "rc-pop")}>
                <line x1={x} y1={26} x2={CX} y2={CY - 30} stroke="#fde68a" strokeOpacity={0.18} />
                <circle cx={x} cy={26} r={3.5} fill="#fde68a"><title>{s.url ?? s.label}</title></circle>
                <text x={x} y={16} fontSize="9" fill="#fef3c7" textAnchor="middle">{short(s.url ? hostOf(s.url) : s.label, 20)}</text>
              </g>);
          })}
          {/* core: the client's site */}
          <g filter="url(#rc-glow)">
            <circle cx={CX} cy={CY} r={30} fill="url(#rc-core)" className={cx(live && "rc-breathe")} />
            <ellipse cx={CX} cy={CY} rx={30} ry={10} fill="none" stroke="rgba(255,255,255,0.35)" />
            <ellipse cx={CX} cy={CY} rx={12} ry={30} fill="none" stroke="rgba(255,255,255,0.18)" />
          </g>
          <text x={CX} y={CY + 50} fontSize="12" fontWeight={600} fill="#fff" textAnchor="middle">{model.domain || model.business || (empty ? "" : "Client")}</text>
          {empty && <text x={CX} y={CY + 68} fontSize="11" fill="rgba(226,232,255,0.65)" textAnchor="middle">Waiting for the research worker to start…</text>}
        </svg>
      </div>
      <div className="relative flex flex-wrap gap-x-4 gap-y-1 border-t border-white/10 px-4 py-2 text-2xs text-indigo-100/80">
        <span><i className="mr-1 inline-block h-2 w-2 rounded-full bg-sky-300" />Pages read {model.pages.filter((p) => p.state === "extracted").length}</span>
        <span><i className="mr-1 inline-block h-2 w-2 rounded-sm bg-fuchsia-300" />Competitors {model.competitors.length}</span>
        <span><i className="mr-1 inline-block h-2 w-2 rounded-full bg-green-300" />Keyword discoveries {model.keywords.length}</span>
        <span><i className="mr-1 inline-block h-2 w-2 rounded-full bg-amber-200" />Web sources {model.sources.filter((s) => s.provider === "tavily").length}</span>
        {model.locations[0] && <span>Location: {short(model.locations[0].label, 40)}</span>}
        {model.pages.some((p) => p.state === "failed" || p.state === "blocked") && <span className="text-rose-300">Unreachable pages {model.pages.filter((p) => p.state === "failed" || p.state === "blocked").length}</span>}
        {model.competitors.some((c) => c.provider) && <span className="ml-auto">{[...new Set(model.competitors.map((c) => providerLabel(c.provider)).filter(Boolean))].join(" · ")}</span>}
      </div>
    </div>
  );
}

/** Nearby businesses plotted around the client by their real coordinates (equirectangular projection around the business). */
export function MarketMap({ model }: { model: ResearchModel }) {
  const center = model.locations.find((l) => l.role === "business") ?? model.locations[0];
  const pts = model.competitors.filter((c) => typeof c.lat === "number" && typeof c.lon === "number");
  if (!center || !pts.length) return null;
  const k = Math.cos((center.lat * Math.PI) / 180);
  const xy = pts.map((p) => ({ p, dx: ((p.lon as number) - center.lon) * k, dy: (p.lat as number) - center.lat }));
  const span = Math.max(0.005, ...xy.map((v) => Math.max(Math.abs(v.dx), Math.abs(v.dy))));
  const S = 90 / span;
  return (
    <svg viewBox="-110 -110 220 220" role="img" aria-label={`${pts.length} nearby business(es) plotted by location`} className="h-auto w-full">
      {[30, 60, 90].map((r) => <circle key={r} r={r} fill="none" stroke="rgb(148 163 184 / 0.35)" strokeDasharray="2 4" />)}
      {xy.map(({ p, dx, dy }) => <circle key={p.name + p.seq} cx={dx * S} cy={-dy * S} r={3.2} fill="#a21caf" fillOpacity={0.75}><title>{`${p.name}${p.distance_km !== undefined ? ` · ${p.distance_km} km` : ""}`}</title></circle>)}
      <circle r={5} fill="#3a48cf" stroke="white" strokeWidth={1.5} />
    </svg>
  );
}
