/** Turns the audit's real research events into what the live view shows. Pure, so it is unit-tested and never invents anything:
 *  every node, source, warning and finding below exists only because the backend recorded an event for it. */
export type ResearchEvent = { seq: number; kind: string; stage: string; level: "info" | "warn" | "error"; title: string; data: Record<string, any>; at: string };

export type PageNode = { url: string; path: string; state: "fetching" | "extracted" | "failed" | "blocked"; title?: string; words?: number; h1?: string; headings?: string[]; status?: number; stage: string; seq: number };
export type CompetitorNode = { name: string; url?: string; basis: string; provider?: string; lat?: number; lon?: number; distance_km?: number; seq: number };
export type KeywordNode = { seed: string; provider: string; count: number; examples: string[]; seq: number };
export type LocationNode = { label: string; lat: number; lon: number; role: string; seq: number };
export type SourceItem = { key: string; label: string; url?: string; provider: string; kind: string; seq: number; at: string };

export type ResearchModel = {
  pages: PageNode[]; competitors: CompetitorNode[]; keywords: KeywordNode[]; locations: LocationNode[]; sources: SourceItem[];
  warnings: ResearchEvent[]; insights: ResearchEvent[]; stageActivity: Record<string, { count: number; last?: ResearchEvent }>;
  domain?: string; business?: string; started: boolean; finished?: ResearchEvent; last?: ResearchEvent;
};

export function hostOf(url?: string): string {
  if (!url) return "";
  try { return new URL(url.includes("//") ? url : `https://${url}`).hostname.replace(/^www\./, ""); } catch { return url; }
}
export function pathOf(url: string): string {
  try { const u = new URL(url); return (u.pathname || "/") + (u.search ? "?…" : ""); } catch { return url; }
}

export const PROVIDER_LABEL: Record<string, string> = {
  google: "Google Autocomplete", duckduckgo: "DuckDuckGo", bing: "Bing", keyword_planner: "Keyword Planner", crawler: "Website crawler",
  tavily: "Tavily web search", openstreetmap: "OpenStreetMap", openstreetmap_overpass: "OpenStreetMap", openstreetmap_nominatim: "OpenStreetMap",
  geoapify: "Geoapify", geoapify_places: "Geoapify", geoapify_geocoding: "Geoapify", places: "Google Places", google_ads: "Google Ads", search_suggestions: "Search suggestions",
};
export const providerLabel = (p?: string) => (p ? PROVIDER_LABEL[p] ?? p : "");

export function buildModel(events: ResearchEvent[]): ResearchModel {
  const pages = new Map<string, PageNode>();
  const competitors = new Map<string, CompetitorNode>();
  const keywords: KeywordNode[] = [];
  const locations: LocationNode[] = [];
  const sources = new Map<string, SourceItem>();
  const warnings: ResearchEvent[] = [];
  const insights: ResearchEvent[] = [];
  const stageActivity: ResearchModel["stageActivity"] = {};
  const m: ResearchModel = { pages: [], competitors: [], keywords, locations, sources: [], warnings, insights, stageActivity, started: false };
  const addSource = (s: SourceItem) => { if (!sources.has(s.key)) sources.set(s.key, s); };
  for (const e of events) {
    m.last = e;
    if (e.stage) { const a = (stageActivity[e.stage] ??= { count: 0 }); a.count += 1; a.last = e; }
    if (e.level !== "info") warnings.push(e);
    const d = e.data ?? {};
    switch (e.kind) {
      case "audit_started": m.started = true; m.domain = hostOf(d.website); m.business = d.business; break;
      case "audit_completed": case "audit_failed": m.finished = e; break;
      case "url_crawling": if (!pages.has(d.url)) pages.set(d.url, { url: d.url, path: pathOf(d.url), state: "fetching", stage: e.stage, seq: e.seq }); break;
      case "page_extracted": {
        const p = pages.get(d.url) ?? { url: d.url, path: pathOf(d.url), stage: e.stage, seq: e.seq, state: "extracted" as const };
        pages.set(d.url, { ...p, state: "extracted", title: d.page_title || e.title, words: d.words, h1: d.h1 || undefined, headings: [d.h1, ...(d.headings ?? [])].filter(Boolean) });
        addSource({ key: d.url, label: d.page_title || pathOf(d.url), url: d.url, provider: "crawler", kind: e.stage === "competitors" ? "Competitor page" : "Website page", seq: e.seq, at: e.at });
        break;
      }
      case "page_failed": case "crawl_blocked": {
        const p = pages.get(d.url) ?? { url: d.url, path: pathOf(d.url), stage: e.stage, seq: e.seq, state: "failed" as const };
        pages.set(d.url, { ...p, state: e.kind === "crawl_blocked" ? "blocked" : "failed", status: typeof d.status === "number" ? d.status : undefined }); break;
      }
      case "competitor_identified": {
        const key = (d.url ? hostOf(d.url) : "") || e.title.toLowerCase();
        if (!competitors.has(key)) competitors.set(key, { name: e.title, url: d.url, basis: d.basis ?? "", provider: d.provider, lat: d.lat, lon: d.lon, distance_km: d.distance_km, seq: e.seq });
        break;
      }
      case "keyword_discovery": keywords.push({ seed: d.seed ?? e.title, provider: d.provider ?? "", count: d.count ?? 0, examples: d.examples ?? [], seq: e.seq }); break;
      case "location_identified": if (typeof d.lat === "number") locations.push({ label: e.title, lat: d.lat, lon: d.lon, role: d.role ?? "", seq: e.seq }); break;
      case "web_result": addSource({ key: d.url, label: e.title, url: d.url, provider: "tavily", kind: "Web result", seq: e.seq, at: e.at }); break;
      case "source_discovered": addSource({ key: `q:${d.query ?? e.title}`, label: e.title, provider: d.provider ?? "", kind: "Search", seq: e.seq, at: e.at }); break;
      case "research_stored": case "evidence_validated": insights.push(e); break;
    }
  }
  m.pages = [...pages.values()];
  m.competitors = [...competitors.values()];
  m.sources = [...sources.values()];
  return m;
}

/** A plain-language line for the activity feed. Technical detail stays in diagnostics. */
export function describe(e: ResearchEvent): string {
  const d = e.data ?? {};
  switch (e.kind) {
    case "url_crawling": return `Opening ${d.url}`;
    case "page_extracted": return `Read “${d.page_title || pathOf(d.url)}”${d.words ? ` · ${d.words} words` : ""}`;
    case "page_failed": return `Could not open ${d.url}${d.status ? ` (HTTP ${d.status})` : ""}`;
    case "crawl_blocked": return `Skipped ${d.url}: the site's robots.txt does not allow it`;
    case "competitor_identified": return `Competitor: ${e.title}${d.basis ? ` — ${d.basis}` : ""}`;
    case "keyword_discovery": return `${e.title}${d.provider ? ` · ${providerLabel(d.provider)}` : ""}`;
    case "location_identified": return `Located: ${e.title}`;
    case "web_result": return `Web result: ${e.title}`;
    case "stage_started": return `Started: ${e.title}`;
    case "stage_completed": return `Finished: ${e.title}${d.summary ? ` — ${d.summary}` : ""}`;
    case "stage_failed": return `Failed: ${e.title}${d.summary ? ` — ${d.summary}` : ""}`;
    default: return e.title;
  }
}

/* ------------------------------------------------------------------------------------------------ live findings stream */
export const STAGE_TITLE: Record<string, string> = {
  client: "Client & business", website: "Website & landing pages", gbp: "Business presence", geo: "Local market",
  competitors: "Competitors", keywords: "Keyword research & search intent", report: "Scoring & recommendations", "": "Research",
};
export type StreamItem = { key: string; type: "activity" | "finding" | "warning" | "note"; text: string; detail?: string; url?: string;
  chips?: string[]; state?: "working" | "done" | "failed"; seq: number; at: string };
export type StreamStage = { stage: string; title: string; state: "running" | "done" | "failed" | "skipped" | "waiting"; summary?: string; items: StreamItem[]; firstSeq: number };

/** Groups the real events into readable sections: one line per page (opening → read), findings as they arrive, warnings in place. */
export function buildStream(events: ResearchEvent[]): StreamStage[] {
  const stages = new Map<string, StreamStage>();
  const byKey = new Map<string, StreamItem>();   // stage + item key → item: one lookup per page instead of a scan (large crawls)
  const get = (stage: string, seq: number) => {
    const key = stage || "";
    if (!stages.has(key)) stages.set(key, { stage: key, title: STAGE_TITLE[key] ?? key, state: "running", items: [], firstSeq: seq });
    return stages.get(key)!;
  };
  for (const e of events) {
    if (["audit_started", "audit_completed", "audit_failed"].includes(e.kind)) continue;
    const s = get(e.stage, e.seq);
    const d = e.data ?? {};
    const push = (it: Omit<StreamItem, "seq" | "at">) => { const item = { ...it, seq: e.seq, at: e.at }; s.items.push(item); byKey.set(`${s.stage}\u0000${item.key}`, item); };
    const find = (key: string) => byKey.get(`${s.stage}\u0000${key}`);
    switch (e.kind) {
      case "stage_started": s.state = "running"; break;
      case "stage_completed": s.state = "done"; s.summary = d.summary; break;
      case "stage_failed": s.state = "failed"; s.summary = d.summary; break;
      case "stage_skipped": s.state = "skipped"; s.summary = d.summary || e.title; break;
      case "url_crawling": push({ key: `p:${d.url}`, type: "activity", text: `Reading ${hostOf(d.url)}${pathOf(d.url) === "/" ? "" : pathOf(d.url)}`, url: d.url, state: "working" }); break;
      case "page_extracted": {
        const existing = find(`p:${d.url}`);
        const text = `Read “${d.page_title || pathOf(d.url)}”`;
        const detail = [d.h1 && d.h1 !== d.page_title ? `“${d.h1}”` : "", d.words ? `${d.words} words` : ""].filter(Boolean).join(" · ");
        const chips = (d.headings ?? []).filter((h: string) => h && h !== d.h1).slice(0, 5);
        if (existing) Object.assign(existing, { text, detail, chips, state: "done" });
        else push({ key: `p:${d.url}`, type: "activity", text, detail, chips, url: d.url, state: "done" });
        break;
      }
      case "page_failed": case "crawl_blocked": {
        const existing = find(`p:${d.url}`);
        const text = e.kind === "crawl_blocked" ? `Skipped ${pathOf(d.url)}: not allowed by the site's robots.txt` : `Could not open ${pathOf(d.url)}${d.status ? ` (HTTP ${d.status})` : ""}`;
        const detail = hostOf(d.url);   // which site, so a competitor's failed home page is not just "/"
        if (existing) Object.assign(existing, { text, detail, chips: [], state: "failed", type: "warning" });
        else push({ key: `p:${d.url}`, type: "warning", text, detail, url: d.url, state: "failed" });
        break;
      }
      case "competitor_identified": push({ key: `c:${e.seq}`, type: "finding", text: e.title, detail: [d.basis, d.distance_km !== undefined ? `${d.distance_km} km away` : "", providerLabel(d.provider)].filter(Boolean).join(" · "), url: d.url }); break;
      case "keyword_discovery": push({ key: `k:${e.seq}`, type: "finding", text: e.title, detail: providerLabel(d.provider), chips: d.examples ?? [] }); break;
      case "location_identified": push({ key: `l:${e.seq}`, type: "finding", text: `Located: ${e.title}`, detail: providerLabel(d.provider) }); break;
      case "web_result": push({ key: `w:${e.seq}`, type: "activity", text: e.title || hostOf(d.url), detail: hostOf(d.url), url: d.url, state: "done" }); break;
      case "source_discovered": push({ key: `q:${e.seq}`, type: "activity", text: e.title, detail: providerLabel(d.provider), state: "done" }); break;
      case "research_stored": case "evidence_validated": push({ key: `r:${e.seq}`, type: "finding", text: e.title }); break;
      case "provider_unavailable": case "provider_failed": push({ key: `x:${e.seq}`, type: "warning", text: e.title, detail: d.error_kind ? `reason: ${d.error_kind.replace(/_/g, " ")}` : undefined }); break;
      default: push({ key: `n:${e.seq}`, type: e.level === "info" ? "note" : "warning", text: e.title });
    }
  }
  return [...stages.values()].sort((a, b) => a.firstSeq - b.firstSeq);
}
