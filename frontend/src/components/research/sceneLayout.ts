/** Positions for the live research scenes. Pure functions of the research model (which is itself built only from recorded backend
 *  events), so every node on screen corresponds to something the backend actually reported. Coordinates: y is up, the ground is x/z.
 *  Node ids reuse the findings-stream keys (`p:<url>`, `c:<seq>`, `k:<seq>`, `l:<seq>`) so the scene and the findings panel can
 *  point at the same thing. */
import { hostOf, providerLabel, type CompetitorNode, type KeywordNode, type PageNode, type ResearchModel } from "./model";

export type SceneKind = "website" | "local" | "competitors" | "keywords" | "combined";
export type NodeRole = "hub" | "page" | "working" | "failed" | "blocked" | "competitor" | "cluster" | "term" | "business" | "listing" | "host";
export type SceneNode = { id: string; role: NodeRole; x: number; y: number; z: number; size: number; label: string; sub?: string; seq: number; tint?: number; dim?: boolean; pin?: boolean; hub?: boolean };
export type SceneEdge = { a: string; b: string; dim?: boolean; faint?: boolean };
export type SceneRing = { x: number; z: number; r: number; label?: string; dim?: boolean };
export type SceneIsland = { id: string; label: string; x: number; z: number; w: number; d: number; state: "waiting" | "running" | "done" | "failed" | "skipped" };
export type SceneGraph = { kind: SceneKind; nodes: SceneNode[]; edges: SceneEdge[]; rings: SceneRing[]; islands: SceneIsland[]; radius: number; empty: string | null };

/** Series colours for providers in the keyword scene (index into SCENE_PALETTE.series). Stable by first appearance. */
export const SCENE_PALETTE = {
  bg: "#0e1720", grid: "#1b2834", gridStrong: "#243442", edge: "#3b5466", edgeFaint: "#22313d",
  hub: "#f2f3f1", page: "#a9d2d8", working: "#3fb5c4", failed: "#ef6461", blocked: "#d9a441",
  competitor: "#d9a441", business: "#f2f3f1", listing: "#c7a25a", host: "#6f8797",
  series: ["#75b4be", "#d9a441", "#b48cc8", "#9fbf9a", "#e08a6a", "#8fa7d6"],
};

const TAU = Math.PI * 2;
const GOLDEN = Math.PI * (3 - Math.sqrt(5));

/* ------------------------------------------------------------------------------------------------------------- website */
function pathSegments(p: PageNode): string[] {
  return (p.path || "/").split("?")[0].split("/").filter(Boolean);
}

/** Radial site tree: the domain at the centre, each page placed under the closest page whose path is a prefix of its own. */
export function websiteLayout(model: ResearchModel, opts: { scale?: number } = {}): Omit<SceneGraph, "kind" | "islands"> {
  const s = opts.scale ?? 1;
  const pages = model.pages.filter((p) => p.stage !== "competitors");
  const nodes: SceneNode[] = [];
  const edges: SceneEdge[] = [];
  const hubLabel = model.domain || hostOf(pages[0]?.url) || "Website";
  nodes.push({ id: "hub:site", role: "hub", hub: true, x: 0, y: 0.25 * s, z: 0, size: 0.5 * s, label: hubLabel, sub: pages.length ? `${pages.length} page(s) recorded` : "Waiting for the first page", seq: 0 });
  if (!pages.length) return { nodes, edges, rings: [], radius: 4 * s, empty: model.finished ? "No page was opened during this run." : "No page has been opened yet." };

  // the home page is the hub itself when it was crawled
  const byPath = new Map<string, PageNode>();
  for (const p of pages) { const key = "/" + pathSegments(p).join("/"); if (!byPath.has(key)) byPath.set(key, p); }
  const home = byPath.get("/");
  const parentOf = (p: PageNode): string => {
    const seg = pathSegments(p);
    for (let i = seg.length - 1; i >= 1; i--) { const pre = byPath.get("/" + seg.slice(0, i).join("/")); if (pre && pre !== p) return `p:${pre.url}`; }
    return "hub:site";
  };
  const children = new Map<string, PageNode[]>();
  for (const p of pages) {
    if (p === home) continue;
    const par = parentOf(p);
    (children.get(par) ?? children.set(par, []).get(par)!).push(p);
  }
  const weight = (id: string): number => 1 + (children.get(id) ?? []).reduce((a, c) => a + weight(`p:${c.url}`), 0);
  const place = (id: string, a0: number, a1: number, depth: number) => {
    const kids = (children.get(id) ?? []).slice().sort((a, b) => a.seq - b.seq);
    const total = kids.reduce((a, k) => a + weight(`p:${k.url}`), 0) || 1;
    let a = a0;
    // many siblings at one level (flat sites, sitemap fan-outs) alternate across staggered rings instead of crowding one circle
    const bands = Math.max(1, Math.ceil(kids.length / (depth === 0 ? 16 : 10)));
    kids.forEach((k, i) => {
      const w = weight(`p:${k.url}`);
      const span = (a1 - a0) * (w / total);
      const ang = a + span / 2;
      const r = (2.4 + depth * 2.1 + (i % bands) * 1.05) * s;
      const kid = `p:${k.url}`;
      nodes.push(pageNode(k, kid, Math.cos(ang) * r, (-0.35 * depth) * s, Math.sin(ang) * r, s));
      edges.push({ a: id, b: kid, faint: k.state === "fetching" });
      place(kid, a, a + span, depth + 1 + (bands > 1 ? (i % bands) * 0.5 : 0));
      a += span;
    });
  };
  if (home) {
    const hub = nodes[0];
    hub.label = model.domain || hostOf(home.url);
    hub.sub = home.title ? `Home · ${home.title}` : "Home page";
    hub.role = home.state === "failed" ? "failed" : home.state === "blocked" ? "blocked" : "hub";
    hub.seq = home.seq;
    hub.id = `p:${home.url}`;
    for (const [par, list] of children) if (par === "hub:site") { children.delete(par); children.set(hub.id, list); }
  }
  place(nodes[0].id, -Math.PI / 2, -Math.PI / 2 + TAU, 0);
  const radius = Math.max(4, ...nodes.map((n) => Math.hypot(n.x, n.z))) + 0.8 * s;
  return { nodes, edges, rings: [], radius, empty: null };
}

function pageNode(p: PageNode, id: string, x: number, y: number, z: number, s: number): SceneNode {
  const role: NodeRole = p.state === "fetching" ? "working" : p.state === "failed" ? "failed" : p.state === "blocked" ? "blocked" : "page";
  const size = (p.words ? 0.12 + Math.min(Math.log10(Math.max(p.words, 10)) - 1, 3) * 0.035 : 0.12) * s;
  const sub = p.state === "fetching" ? "Opening…" : p.state === "failed" ? "Could not be opened" : p.state === "blocked" ? "Skipped: robots.txt"
    : [p.title && p.title !== p.path ? p.title : "", p.words ? `${p.words} words` : ""].filter(Boolean).join(" · ");
  return { id, role, x, y, z, size, label: p.path || p.url, sub, seq: p.seq };
}

/* --------------------------------------------------------------------------------------------------------- local market */
/** The geocoded business at the centre and every map listing at its true relative position (km, east/north). */
export function localLayout(model: ResearchModel, opts: { scale?: number } = {}): Omit<SceneGraph, "kind" | "islands"> {
  const s = opts.scale ?? 1;
  const nodes: SceneNode[] = [];
  const centre = model.locations.find((l) => l.role === "business") ?? model.locations[0];
  const listings = model.competitors.filter((c) => typeof c.lat === "number" && typeof c.lon === "number");
  if (!centre) {
    nodes.push({ id: "hub:biz", role: "business", hub: true, x: 0, y: 0, z: 0, size: 0.4 * s, label: model.business || "Business", sub: "Location not recorded yet", seq: 0, pin: true, dim: true });
    return { nodes, edges: [], rings: [], radius: 5 * s, empty: model.started ? "No business location has been recorded." : "Waiting for the business location." };
  }
  const kmX = (lon: number) => (lon - centre.lon) * 111.32 * Math.cos((centre.lat * Math.PI) / 180);
  const kmZ = (lat: number) => -(lat - centre.lat) * 110.57;
  const far = Math.max(1, ...listings.map((c) => c.distance_km ?? Math.hypot(kmX(c.lon!), kmZ(c.lat!))));
  const R = 8.5 * s;
  const k = R / far;
  nodes.push({ id: `l:${centre.seq}`, role: "business", hub: true, x: 0, y: 0, z: 0, size: 0.42 * s, label: model.business || centre.label.split(",")[0], sub: centre.label, seq: centre.seq, pin: true });
  for (const c of listings) {
    nodes.push({ id: `c:${c.seq}`, role: "listing", x: kmX(c.lon!) * k, y: 0, z: kmZ(c.lat!) * k, size: 0.2 * s, label: c.name,
      sub: [c.distance_km !== undefined ? `${c.distance_km} km away` : "", providerLabel(c.provider)].filter(Boolean).join(" · "), seq: c.seq, pin: true });
  }
  const rings: SceneRing[] = [1, 2, 5, 10, 25, 50].filter((km) => km <= far * 1.05).slice(-4).map((km) => ({ x: 0, z: 0, r: km * k, label: `${km} km` }));
  if (!rings.length) rings.push({ x: 0, z: 0, r: R, label: `${Math.round(far * 10) / 10} km` });
  return { nodes, edges: [], rings, radius: R + 1.2 * s, empty: listings.length ? null : "The location is known; no map listing in the same category has been recorded." };
}

/* ---------------------------------------------------------------------------------------------------------- competitors */
/** The business at the centre, each competitor on an orbit (true distance when the event carries one), and the competitor
 *  pages that were actually crawled as small satellites around their competitor. */
export function competitorLayout(model: ResearchModel, opts: { scale?: number; excludeMapListings?: boolean } = {}): Omit<SceneGraph, "kind" | "islands"> {
  const s = opts.scale ?? 1;
  const nodes: SceneNode[] = [{ id: "hub:biz", role: "hub", hub: true, x: 0, y: 0.2 * s, z: 0, size: 0.45 * s, label: model.business || model.domain || "Your business", sub: model.domain, seq: 0 }];
  const edges: SceneEdge[] = [];
  const comps = model.competitors.filter((c) => !(opts.excludeMapListings && typeof c.lat === "number")).sort((a, b) => a.seq - b.seq);
  const withDist = comps.filter((c) => typeof c.distance_km === "number");
  const far = Math.max(1, ...withDist.map((c) => c.distance_km!));
  const placed = new Map<string, SceneNode>();
  comps.forEach((c: CompetitorNode, i) => {
    const ang = -Math.PI / 2 + i * GOLDEN;
    const r = (typeof c.distance_km === "number" ? 2.6 + (c.distance_km / far) * 5.4 : 3.4 + (i % 3) * 1.5) * s;
    const n: SceneNode = { id: `c:${c.seq}`, role: "competitor", x: Math.cos(ang) * r, y: 0, z: Math.sin(ang) * r, size: 0.34 * s, label: c.name,
      sub: [c.basis, c.distance_km !== undefined ? `${c.distance_km} km` : "", c.url ? hostOf(c.url) : "", providerLabel(c.provider)].filter(Boolean).join(" · "), seq: c.seq };
    nodes.push(n);
    edges.push({ a: "hub:biz", b: n.id });
    if (c.url) placed.set(hostOf(c.url), n);
  });
  // crawled competitor pages orbit their competitor; a host nobody named becomes its own small node so nothing is dropped or misattributed
  const pages = model.pages.filter((p) => p.stage === "competitors");
  const perHost = new Map<string, number>();
  for (const p of pages) {
    const h = hostOf(p.url);
    let parent = placed.get(h);
    if (!parent) {
      const i = placed.size;
      const ang = Math.PI / 2 + i * GOLDEN;
      parent = { id: `h:${h}`, role: "host", x: Math.cos(ang) * 6.2 * s, y: 0, z: Math.sin(ang) * 6.2 * s, size: 0.22 * s, label: h, sub: "Crawled site", seq: p.seq };
      nodes.push(parent); edges.push({ a: "hub:biz", b: parent.id, faint: true }); placed.set(h, parent);
    }
    const n = perHost.get(h) ?? 0; perHost.set(h, n + 1);
    // pages form a small ring above their site, outward from the centre, opening a new ring every eight pages
    const ring = Math.floor(n / 8), slot = n % 8;
    const out = Math.atan2(parent.z, parent.x);
    const ang = out + (slot - 3.5) * 0.42;
    const rr = (1.05 + ring * 0.55) * s;
    const node = pageNode(p, `p:${p.url}`, parent.x + Math.cos(ang) * rr, (0.9 + ring * 0.35) * s, parent.z + Math.sin(ang) * rr, s * 0.75);
    nodes.push(node); edges.push({ a: parent.id, b: node.id, faint: true });
  }
  const rings: SceneRing[] = withDist.length ? [0.25, 0.5, 1].map((f) => ({ x: 0, z: 0, r: (2.6 + f * 5.4) * s, label: `${Math.round(far * f * 10) / 10} km` })) : [];
  const radius = Math.max(5 * s, ...nodes.map((n) => Math.hypot(n.x, n.z))) + 1 * s;
  return { nodes, edges, rings, radius, empty: comps.length || pages.length ? null : "No competitor has been identified yet." };
}

/* ------------------------------------------------------------------------------------------------------------- keywords */
/** One cluster per recorded discovery (its seed), the example terms the event carried around it, one lane per provider. */
export function keywordLayout(model: ResearchModel, opts: { scale?: number } = {}): Omit<SceneGraph, "kind" | "islands"> {
  const s = opts.scale ?? 1;
  const nodes: SceneNode[] = [{ id: "hub:kw", role: "hub", hub: true, x: 0, y: 0.2 * s, z: 0, size: 0.45 * s, label: model.business || "Services", sub: "Keyword research", seq: 0 }];
  const edges: SceneEdge[] = [];
  const providers: string[] = [];
  for (const k of model.keywords) if (!providers.includes(k.provider)) providers.push(k.provider);
  const lane = (p: string) => providers.indexOf(p);
  const sector = TAU / Math.max(providers.length, 1);
  const inLane = new Map<string, number>();
  const sorted: KeywordNode[] = model.keywords.slice().sort((a, b) => a.seq - b.seq);
  for (const k of sorted) {
    const li = lane(k.provider);
    const n = inLane.get(k.provider) ?? 0; inLane.set(k.provider, n + 1);
    const cols = 3;
    const ang = -Math.PI / 2 + li * sector + sector * ((n % cols) + 0.5) / cols;
    const r = (3 + Math.floor(n / cols) * 1.9) * s;
    const id = `k:${k.seq}`;
    const size = (0.2 + Math.min(Math.log10(Math.max(k.count, 1) + 1), 2.5) * 0.11) * s;
    nodes.push({ id, role: "cluster", x: Math.cos(ang) * r, y: 0.1 * s, z: Math.sin(ang) * r, size, label: k.seed, sub: `${k.count} term(s) · ${providerLabel(k.provider) || "source"}`, seq: k.seq, tint: li });
    edges.push({ a: "hub:kw", b: id, faint: true });
    k.examples.slice(0, 6).forEach((t, j, arr) => {
      const a2 = ang + ((j - (arr.length - 1) / 2) * 0.55);
      const tr = (0.8 + (j % 2) * 0.25) * s;
      const tid = `${id}:t${j}`;
      nodes.push({ id: tid, role: "term", x: Math.cos(ang) * r + Math.cos(a2) * tr, y: (0.35 + (j % 3) * 0.18) * s, z: Math.sin(ang) * r + Math.sin(a2) * tr, size: 0.085 * s, label: t, sub: `from “${k.seed}”`, seq: k.seq, tint: li });
      edges.push({ a: id, b: tid, faint: true });
    });
  }
  // sources that were asked but did not answer are part of the picture too: an outer ring, one node per recorded failure
  const misses = model.warnings.filter((w) => (w.kind === "provider_failed" || w.kind === "provider_unavailable") && (w.stage === "keywords" || !w.stage));
  misses.forEach((w, i) => {
    const ang = -Math.PI / 2 + (i + 0.5) * (TAU / Math.max(misses.length, 1)) + 0.3;
    const r = Math.max(5.2 * s, ...nodes.map((n) => Math.hypot(n.x, n.z) + 1.4 * s));
    const id = `x:${w.seq}`;
    nodes.push({ id, role: w.kind === "provider_unavailable" ? "blocked" : "failed", x: Math.cos(ang) * r, y: 0, z: Math.sin(ang) * r, size: 0.22 * s,
      label: providerLabel(w.data?.provider) || w.title, sub: w.kind === "provider_unavailable" ? "Not available" : `Did not answer${w.data?.error_kind ? `: ${String(w.data.error_kind).replace(/_/g, " ")}` : ""}`, seq: w.seq });
    edges.push({ a: "hub:kw", b: id, faint: true });
  });
  const radius = Math.max(5 * s, ...nodes.map((n) => Math.hypot(n.x, n.z))) + 1 * s;
  return { nodes, edges, rings: [], radius, empty: model.keywords.length || misses.length ? null : model.last ? "No keyword source returned any terms." : "No keyword source has answered yet." };
}

/* ------------------------------------------------------------------------------------------------------------- combined */
export type StageState = Record<string, "waiting" | "running" | "done" | "failed" | "skipped">;

/** Separate islands for each research stream of a combined audit. An island is only lit once its stage has recorded something. */
export function combinedLayout(model: ResearchModel, stages: StageState): Omit<SceneGraph, "kind"> {
  const islands: SceneIsland[] = [];
  const nodes: SceneNode[] = [];
  const edges: SceneEdge[] = [];
  const rings: SceneRing[] = [];
  const parts: { id: string; label: string; stages: string[]; build: () => Omit<SceneGraph, "kind" | "islands"> }[] = [
    { id: "website", label: "Website", stages: ["website"], build: () => websiteLayout(model, { scale: 0.5 }) },
    { id: "local", label: "Local market", stages: ["geo", "gbp"], build: () => localLayout(model, { scale: 0.5 }) },
    { id: "competitors", label: "Competitors", stages: ["competitors"], build: () => competitorLayout(model, { scale: 0.5, excludeMapListings: true }) },
  ];
  if (model.keywords.length || stages.keywords) parts.push({ id: "keywords", label: "Keywords", stages: ["keywords"], build: () => keywordLayout(model, { scale: 0.5 }) });
  const gap = 0.9;
  const built = parts.map((p) => ({ ...p, g: p.build() }));
  const widths = built.map((b) => Math.max(b.g.radius, 2.2) * 2);
  const total = widths.reduce((a, w) => a + w, 0) + gap * (built.length - 1);
  let x = -total / 2;
  built.forEach((b, i) => {
    const w = widths[i];
    const cx = x + w / 2;
    x += w + gap;
    const st = b.stages.map((k) => stages[k]).filter(Boolean);
    const state = st.includes("failed") ? "failed" : st.includes("running") ? "running" : st.length && st.every((v) => v === "done" || v === "skipped") ? (st.every((v) => v === "skipped") ? "skipped" : "done") : "waiting";
    const dim = state === "waiting" || state === "skipped";
    islands.push({ id: b.id, label: b.label, x: cx, z: 0, w, d: w, state });
    const pre = (id: string) => `${b.id}|${id}`;
    // the island carries the name; hubs inside islands stay unlabelled so the business name is not repeated four times
    for (const n of b.g.nodes) nodes.push({ ...n, hub: false, id: n.id.startsWith("hub:") ? pre(n.id) : n.id, x: n.x + cx, dim: n.dim || dim });
    for (const e of b.g.edges) edges.push({ a: e.a.startsWith("hub:") ? pre(e.a) : e.a, b: e.b.startsWith("hub:") ? pre(e.b) : e.b, faint: e.faint, dim });
    for (const r of b.g.rings) rings.push({ ...r, x: r.x + cx, dim });
  });
  return { nodes, edges, rings, islands, radius: total / 2 + 0.6, empty: model.started || nodes.length > built.length ? null : "Waiting for the research worker to record its first step." };
}

export function buildScene(kind: SceneKind, model: ResearchModel, stages: StageState = {}): SceneGraph {
  if (kind === "combined") return { kind, ...combinedLayout(model, stages) };
  const g = kind === "website" ? websiteLayout(model) : kind === "local" ? localLayout(model) : kind === "competitors" ? competitorLayout(model) : keywordLayout(model);
  return { kind, islands: [], ...g };
}

/** Plain-language counts for the scene legend. Every number is a count of recorded events. */
export function sceneCounts(kind: SceneKind, model: ResearchModel): { label: string; value: number; tone: keyof typeof SCENE_PALETTE | "series0" }[] {
  const site = model.pages.filter((p) => p.stage !== "competitors");
  const count = (list: PageNode[], st: PageNode["state"]) => list.filter((p) => p.state === st).length;
  const pageCounts = (list: PageNode[]) => [
    { label: "read", value: count(list, "extracted"), tone: "page" as const }, { label: "opening", value: count(list, "fetching"), tone: "working" as const },
    { label: "failed", value: count(list, "failed"), tone: "failed" as const }, { label: "skipped", value: count(list, "blocked"), tone: "blocked" as const },
  ];
  switch (kind) {
    case "website": return pageCounts(site).filter((c) => c.value || c.label === "read").map((c) => ({ ...c, label: `page${c.value === 1 ? "" : "s"} ${c.label}` }));
    case "local": return [{ label: "map listings", value: model.competitors.filter((c) => typeof c.lat === "number").length, tone: "listing" }, { label: "location", value: model.locations.length, tone: "business" }];
    case "competitors": return [{ label: "competitors", value: model.competitors.length, tone: "competitor" }, { label: "competitor pages read", value: count(model.pages.filter((p) => p.stage === "competitors"), "extracted"), tone: "page" }];
    case "keywords": return [{ label: "discoveries", value: model.keywords.length, tone: "series0" }, { label: "terms reported", value: model.keywords.reduce((a, k) => a + (k.count || 0), 0), tone: "series0" },
      ...((n) => (n ? [{ label: n === 1 ? "source did not answer" : "sources did not answer", value: n, tone: "failed" as const }] : []))(model.warnings.filter((w) => w.kind === "provider_failed" || w.kind === "provider_unavailable").length)];
    default: return [
      { label: "pages read", value: count(site, "extracted"), tone: "page" }, { label: "competitors", value: model.competitors.length, tone: "competitor" },
      { label: "keyword discoveries", value: model.keywords.length, tone: "series0" }, { label: "sources", value: model.sources.length, tone: "hub" },
    ];
  }
}
