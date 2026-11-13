/** "What was found": the discoveries of an audit in plain language, above the step-by-step log.
 *  Two inputs only: the audit's stored result (once it exists) and the recorded research events. Pure and unit-tested: every line
 *  restates a value that is in one of those two and says which (`basis`); a capability that produced nothing says so.
 *  Groups: "finding" (what was discovered), "gap" (a discovered problem), "limit" (what the research could not establish).
 *  `ref` points at the same id the scene uses, so hovering a line highlights the thing it is about. */
import { hostOf, providerLabel, type PageNode, type ResearchModel } from "./model";
import type { SceneKind } from "./sceneLayout";

export type DigestTone = "finding" | "gap" | "limit";
export type DigestItem = { key: string; tone: DigestTone; title: string; body?: string; points?: string[]; chips?: string[]; basis: string; ref?: string };
export type DigestContext = { status?: string; error?: string; simulated?: boolean };

const list = (xs: string[], max = 4) => (xs.length <= max ? xs.join(", ") : `${xs.slice(0, max).join(", ")} and ${xs.length - max} more`);
const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
const arr = (v: any): any[] => (Array.isArray(v) ? v : []);
const RESULT = "Stored audit result";

/** A URL that is a machine-readable file (sitemap, robots, feed, data), not a page a visitor reads. Decided by the address alone. */
export const isFile = (p: PageNode) => /\.(xml|txt|json|rss|atom|csv|pdf)(\.gz)?$/i.test(p.path.split("?")[0]);

/** Section headings that recur across several read pages: the site's own navigation and themes, counted, not inferred. */
export function recurringHeadings(pages: PageNode[], minPages = 2): { heading: string; pages: number }[] {
  const seen = new Map<string, { heading: string; urls: Set<string> }>();
  for (const p of pages) for (const h of p.headings ?? []) {
    const k = h.trim().toLowerCase();
    if (!k || k.length > 60) continue;
    (seen.get(k) ?? seen.set(k, { heading: h.trim(), urls: new Set() }).get(k)!).urls.add(p.url);
  }
  return [...seen.values()].filter((x) => x.urls.size >= minPages).map((x) => ({ heading: x.heading, pages: x.urls.size }))
    .sort((a, b) => b.pages - a.pages || a.heading.localeCompare(b.heading));
}

/** A page title when the site gave one; its path when the "title" is only the address. */
const pageName = (p: PageNode) => (p.title && p.title !== p.path && !/^https?:\/\//.test(p.title) ? p.title : p.path);

function crawlItems(pages: PageNode[], who: string, keyPrefix: string): DigestItem[] {
  const out: DigestItem[] = [];
  if (!pages.length) return out;
  const read = pages.filter((p) => p.state === "extracted");
  const html = read.filter((p) => !isFile(p));
  const files = read.filter(isFile);
  const failed = pages.filter((p) => p.state === "failed");
  const blocked = pages.filter((p) => p.state === "blocked");
  const hosts = [...new Set(read.map((p) => hostOf(p.url)))];
  const basis = `Recorded crawl: ${plural(pages.length, "address", "addresses")} opened`;
  out.push(read.length
    ? { key: `${keyPrefix}read`, tone: "finding", basis,
        title: files.length ? `Read ${plural(html.length, "web page")} and ${plural(files.length, "sitemap or data file")} on ${list(hosts, 3)}` : `Read ${plural(html.length, "page")} on ${list(hosts, 3)}`,
        body: files.length && html.length <= 2 ? "Most addresses the crawler reached were sitemap files, so little page content was available to read." : undefined }
    : { key: `${keyPrefix}read`, tone: "gap", basis, title: `No page of ${who} could be read` });
  if (failed.length) {
    const st = [...new Set(failed.map((p) => p.status).filter(Boolean))];
    out.push({ key: `${keyPrefix}failed`, tone: "gap", basis, title: `${plural(failed.length, "address", "addresses")} could not be opened${st.length ? ` (HTTP ${st.join(", ")})` : ""}`,
      body: list(failed.map((p) => `${hostOf(p.url)}${p.path === "/" ? "" : p.path}`), 3), ref: `p:${failed[0].url}` });
  }
  if (blocked.length) out.push({ key: `${keyPrefix}blocked`, tone: "limit", basis, title: `${plural(blocked.length, "address", "addresses")} skipped because the site's robots.txt disallows them` });
  const home = html.find((p) => p.path === "/") ?? html[0];
  if (home?.h1) out.push({ key: `${keyPrefix}home`, tone: "finding", basis: `Page read: ${hostOf(home.url)}${home.path}`,
    title: `${home.path === "/" ? "Home page" : `“${pageName(home)}”`} leads with “${home.h1}”`, chips: (home.headings ?? []).filter((h) => h !== home.h1).slice(0, 6), ref: `p:${home.url}` });
  const deep = html.filter((p) => (p.words ?? 0) > 0).sort((a, b) => (b.words ?? 0) - (a.words ?? 0)).slice(0, 3);
  if (html.length > 2 && deep.length) out.push({ key: `${keyPrefix}deep`, tone: "finding", basis: "Word counts of the pages read",
    title: "Pages with the most content", points: deep.map((p) => `${pageName(p)} — ${p.words} words`), ref: `p:${deep[0].url}` });
  const rec = recurringHeadings(html).slice(0, 8);
  if (rec.length) out.push({ key: `${keyPrefix}rec`, tone: "finding", basis: "Headings of the pages read, counted",
    title: "Sections that repeat across pages", body: "Usually the site's navigation and main themes.", chips: rec.map((r) => `${r.heading} · ${r.pages} pages`) });
  return out;
}

/** Providers that were asked and did not answer, one line each: what the research could not establish. */
function providerGaps(m: ResearchModel, stages: string[]): DigestItem[] {
  const seen = new Set<string>();
  const out: DigestItem[] = [];
  for (const w of m.warnings) {
    if (w.kind !== "provider_failed" && w.kind !== "provider_unavailable") continue;
    if (stages.length && w.stage && !stages.includes(w.stage)) continue;
    const who = providerLabel(w.data?.provider) || w.data?.provider || "A source";
    if (seen.has(who)) continue;
    seen.add(who);
    out.push({ key: `pv-${w.seq}`, tone: "limit", basis: "Recorded source response",
      title: w.kind === "provider_unavailable" ? `${who} was not available` : `${who} did not answer`,
      body: w.data?.reason || (w.data?.error_kind ? `Reason: ${String(w.data.error_kind).replace(/_/g, " ")}` : undefined), ref: `x:${w.seq}` });
  }
  return out;
}

function websiteFromResult(r: any): DigestItem[] {
  const out: DigestItem[] = [];
  const services = arr(r.service_inventory).map((s) => s?.name).filter(Boolean);
  out.push(services.length ? { key: "w-svc", tone: "finding", basis: RESULT, title: `${plural(services.length, "service")} found on the site`, chips: services.slice(0, 10) }
    : { key: "w-svc", tone: "gap", basis: RESULT, title: "No services could be identified from the site's pages" });
  const noPage = arr(r.services_without_page);
  if (noPage.length) out.push({ key: "w-nopage", tone: "gap", basis: RESULT, title: `${plural(noPage.length, "profile service")} with no dedicated page`, body: "Ads for these services have no matching landing page.", chips: noPage.slice(0, 8) });
  const notInProfile = arr(r.services_not_in_profile);
  if (notInProfile.length) out.push({ key: "w-notprof", tone: "gap", basis: RESULT, title: "Offered on the site but missing from the client profile", chips: notInProfile.slice(0, 8) });
  const issues = arr(r.seo_issues);
  out.push(issues.length ? { key: "w-seo", tone: "gap", basis: RESULT, title: plural(issues.length, "technical or SEO issue"),
    points: issues.slice(0, 4).map((i) => `${i.issue}${i.url ? ` — ${(() => { try { return new URL(i.url).pathname; } catch { return i.url; } })()}` : ""}`) }
    : { key: "w-seo", tone: "finding", basis: RESULT, title: "No technical or SEO issues were flagged" });
  const scores = arr(r.content_scores).filter((c) => typeof c?.score === "number").sort((a, b) => a.score - b.score);
  if (scores.length) out.push({ key: "w-score", tone: scores[0].score < 60 ? "gap" : "finding", basis: `${RESULT} (content score, 0–100)`,
    title: `Weakest landing page: ${scores[0].service} (${scores[0].score}/100)`, body: arr(scores[0].notes).join("; ") || undefined });
  const tags = [...arr(r.google_tags_detected), ...arr(r.gtm_detected).map((g: string) => `GTM ${g}`)];
  out.push({ key: "w-tags", tone: tags.length ? "finding" : "gap", basis: RESULT,
    title: tags.length ? `Google tags on the site: ${list(tags, 4)}` : "No Google tag or Tag Manager container was detected",
    body: tags.length ? undefined : "Conversions cannot be measured from this site until a tag is installed." });
  const claims = arr(r.claims_not_in_verified_list);
  if (claims.length) out.push({ key: "w-claims", tone: "gap", basis: RESULT, title: `${plural(claims.length, "claim")} on the site are not in the verified list`, body: "Ads cannot use them until they are verified." });
  return out;
}

function competitorItems(m: ResearchModel, r: any): DigestItem[] {
  const out: DigestItem[] = [];
  const byName = new Map(arr(r?.competitors).map((c) => [String(c?.name ?? "").toLowerCase(), c]));
  for (const c of m.competitors.filter((x) => typeof x.lat !== "number" || x.url)) {
    const host = c.url ? hostOf(c.url) : "";
    const pages = m.pages.filter((p) => p.stage === "competitors" && host && hostOf(p.url) === host);
    const read = pages.filter((p) => p.state === "extracted");
    const html = read.filter((p) => !isFile(p));
    const failed = pages.filter((p) => p.state === "failed");
    const home = html.find((p) => p.path === "/") ?? html[0];
    const res = byName.get(c.name.toLowerCase());
    const services = arr(res?.services), lacks = arr(res?.services_client_lacks), offers = arr(res?.offers_on_site);
    const files = read.length - html.length;
    const points = [
      read.length ? `${plural(read.length, "address", "addresses")} read${files ? ` (${plural(files, "sitemap or data file")})` : ""}${home?.h1 ? `; their site leads with “${home.h1}”` : ""}`
        : failed.length ? `Their site could not be opened${failed[0].status ? ` (HTTP ${failed[0].status})` : ""}` : c.url ? "Their site has not been read yet" : "",
      res ? (services.length ? `Services: ${list(services, 5)}` : "No services could be extracted from their pages") : "",
      lacks.length ? `They offer what the client does not: ${list(lacks, 4)}` : "",
      offers.length ? `Offers on their site: ${list(offers, 2)}` : "",
      res?.keyword_ideas?.length ? `Keyword ideas (their site's topics, not their bids): ${list(res.keyword_ideas, 4)}` : res?.keyword_error ? "Keyword ideas unavailable: Google Ads is not connected" : "",
    ].filter(Boolean);
    out.push({ key: `cmp-${c.seq}`, tone: !read.length && failed.length ? "gap" : "finding", basis: res ? `${RESULT} and recorded crawl` : "Recorded competitor and crawl events",
      title: `${c.name}${c.basis ? ` (${c.basis})` : ""}`, points, ref: `c:${c.seq}` });
  }
  if (r?.skipped) out.push({ key: "cmp-skip", tone: "limit", basis: RESULT, title: "No competitor was audited", body: r.reason });
  else if (!out.length && r) out.push({ key: "cmp-none", tone: "gap", basis: RESULT, title: "No competitor was analysed" });
  for (const w of arr(r?.web_candidates).slice(0, 4)) out.push({ key: `cmp-web-${w.website_url}`, tone: "finding", basis: `${RESULT} (web search)`, title: `Suggested by web search: ${w.name}`, body: "Not verified as a competitor." });
  if (r?.web_discovery && r.web_discovery.used === false && r.web_discovery.reason) out.push({ key: "cmp-webx", tone: "limit", basis: RESULT, title: "Competitors were not searched for on the web", body: r.web_discovery.reason });
  for (const s of arr(r?.missing_sources)) out.push({ key: `cmp-miss-${s}`, tone: "limit", basis: RESULT, title: s });
  return out;
}

function keywordItems(m: ResearchModel, r: any): DigestItem[] {
  const out: DigestItem[] = [];
  if (r && Array.isArray(r.keywords)) {
    const kws = arr(r.keywords);
    const sel = kws.filter((k) => k?.selected);
    const clusters = Object.entries((r.clusters ?? {}) as Record<string, string[]>);
    out.push({ key: "kw-total", tone: kws.length ? "finding" : "gap", basis: RESULT,
      title: kws.length ? `${plural(kws.length, "keyword")} researched, ${sel.length} selected for ads` : "No keywords were produced",
      body: arr(r.sources_used).length ? `Built from: ${arr(r.sources_used).map((s: string) => s.replace(/_/g, " ")).join(", ")}` : undefined });
    for (const [name, terms] of clusters.slice(0, 6)) out.push({ key: `kw-c-${name}`, tone: "finding", basis: RESULT, title: `Proposed ad group “${name}”`, chips: arr(terms).slice(0, 8) });
    const observed = kws.filter((k) => typeof k?.avg_monthly_searches === "number").length;
    out.push({ key: "kw-vol", tone: observed ? "finding" : "limit", basis: RESULT,
      title: observed ? `${observed} of ${kws.length} keywords have observed search volume` : "Search volume could not be verified for any keyword",
      body: !observed && r.keyword_planner?.available === false ? r.keyword_planner.reason : undefined });
    const neg = arr(r.negative_ideas);
    out.push({ key: "kw-neg", tone: neg.length ? "finding" : "gap", basis: RESULT, title: neg.length ? plural(neg.length, "negative keyword idea") : "No negative keyword ideas were produced", chips: neg.slice(0, 8) });
    if (arr(r.overlaps).length) out.push({ key: "kw-ovl", tone: "gap", basis: RESULT, title: `${plural(arr(r.overlaps).length, "keyword")} overlap between ad groups` });
    const inputs = Object.entries((r.inputs ?? {}) as Record<string, any>).filter(([, v]) => !v)
      .map(([k]) => ({ WEBSITE: "website", GBP: "business presence", COMPETITOR: "competitor" } as Record<string, string>)[k] ?? k.toLowerCase());
    if (inputs.length) out.push({ key: "kw-inputs", tone: "limit", basis: RESULT, title: `Built without ${list(inputs.map((x) => `${x} research`), 3)}`, body: "No usable result of those audits was available." });
    return out;
  }
  for (const k of m.keywords.slice(0, 8)) out.push({ key: `kw-d-${k.seq}`, tone: "finding", basis: "Recorded keyword source response", title: `${k.seed}: ${plural(k.count, "term")} from ${providerLabel(k.provider) || "a source"}`, chips: k.examples.slice(0, 6), ref: `k:${k.seq}` });
  for (const i of m.insights) out.push({ key: `kw-i-${i.seq}`, tone: "finding", basis: "Recorded research step", title: i.title });
  return out;
}

function localItems(m: ResearchModel, r: any, simulated: boolean): DigestItem[] {
  const out: DigestItem[] = [];
  const fx = simulated ? " — test fixture, not real businesses" : "";
  const c = r?.client;
  if (c && c.name) out.push({ key: "gbp-client", tone: "finding", basis: `${RESULT}${c.simulated ? " — test fixture, not Google Places" : ""}`,
    title: `${c.name}${c.primary_category ? ` · ${c.primary_category}` : ""}`,
    body: [typeof c.rating === "number" ? `${c.rating}★` : "", typeof c.user_rating_count === "number" ? plural(c.user_rating_count, "review") : ""].filter(Boolean).join(" · ") || undefined });
  const rivals = arr(r?.competitors).filter(Boolean);
  if (rivals.length) {
    const rated = rivals.filter((x) => typeof x.rating === "number").sort((a, b) => b.rating - a.rating);
    out.push({ key: "gbp-rivals", tone: "finding", basis: RESULT + fx, title: `${plural(rivals.length, "competing listing")} compared`,
      body: rated.length ? `Highest rated: ${rated[0].name} (${rated[0].rating}★${typeof rated[0].user_rating_count === "number" ? `, ${rated[0].user_rating_count} reviews` : ""})` : undefined });
  }
  // the backend's own findings: OpenStreetMap presence is a discovery, its absence is explicitly not proof of anything, the rest are problems
  for (const f of arr(r?.findings).slice(0, 5).map(String)) out.push({ key: `gbp-f-${f}`, basis: RESULT + fx, title: f,
    tone: /^Listed on OpenStreetMap/.test(f) ? "finding" : /^Not found on OpenStreetMap/.test(f) ? "limit" : "gap" });
  const loc = m.locations.find((l) => l.role === "business") ?? m.locations[0];
  if (loc) out.push({ key: "gbp-loc", tone: "finding", basis: "Recorded geocoding result" + fx, title: `Located at ${loc.label}`, ref: `l:${loc.seq}` });
  const listings = m.competitors.filter((x) => typeof x.distance_km === "number").sort((a, b) => a.distance_km! - b.distance_km!);
  if (listings.length) out.push({ key: "gbp-near", tone: "finding", basis: `Recorded map listings (${listings.length})` + fx,
    title: `${plural(listings.length, "same-category business", "same-category businesses")} on the map nearby`,
    points: listings.slice(0, 3).map((x) => `${x.name} — ${x.distance_km} km`), ref: `c:${listings[0].seq}` });
  const geo = r?.geo;
  if (!listings.length && geo?.available && typeof geo.density?.total === "number") out.push({ key: "gbp-density", tone: "finding", basis: RESULT + fx,
    title: `${plural(geo.density.total, "same-category business", "same-category businesses")} within ${Math.round((geo.radius_m ?? 0) / 1609)} miles${geo.center?.display_name ? ` of ${geo.center.display_name}` : ""}` });
  if (geo && geo.available === false && geo.reason) out.push({ key: "gbp-geo", tone: "limit", basis: RESULT, title: "Local market data was not available", body: geo.reason });
  const placesEvent = m.warnings.some((w) => w.data?.provider === "places");
  if (r && r.places_available === false && !placesEvent) out.push({ key: "gbp-places", tone: "limit", basis: RESULT, title: "Google Places was not available", body: "Ratings, reviews and hours could not be checked." });
  return out;
}

/** The analysis behind an audit that failed did not run: say what is missing, from the recorded failure itself. */
function failedAudit(kind: SceneKind, ctx: DigestContext): DigestItem[] {
  if (ctx.status !== "FAILED") return [];
  const missing: Record<string, string> = {
    website: "The service inventory, landing-page scores, SEO checks and tag detection were not produced.",
    local: "Listing details, ratings and the competitor comparison were not produced.",
    competitors: "The competitor comparison was not produced.",
    keywords: "No keyword list was produced.",
  };
  return [{ key: "failed", tone: "limit", basis: "Recorded failure of this audit", title: "The analysis did not finish", body: [ctx.error, missing[kind]].filter(Boolean).join(" ") }];
}

const ORDER: Record<DigestTone, number> = { finding: 0, gap: 1, limit: 2 };

/** The digest for one audit type (or the combined view). `result` is the stored audit result; pass nothing while it runs. */
export function buildDigest(kind: SceneKind, m: ResearchModel, result?: any, ctx: DigestContext = {}): DigestItem[] {
  const r = result && typeof result === "object" && Object.keys(result).length ? result : undefined;
  const site = m.pages.filter((p) => p.stage !== "competitors");
  let items: DigestItem[];
  switch (kind) {
    case "website": items = [...crawlItems(site, "the website", "ws-"), ...(r ? websiteFromResult(r) : []), ...providerGaps(m, ["website"])]; break;
    case "competitors": items = [...competitorItems(m, r), ...providerGaps(m, ["competitors"])]; break;
    case "keywords": items = [...keywordItems(m, r), ...providerGaps(m, ["keywords"])]; break;
    case "local": items = [...localItems(m, r, !!ctx.simulated), ...providerGaps(m, ["gbp", "geo"])]; break;
    default: {
      items = [...crawlItems(site, "the website", "ws-")];
      const comps = m.competitors.filter((c) => c.url);
      if (comps.length) items.push({ key: "all-cmp", tone: "finding", basis: "Recorded competitor events", title: `${plural(comps.length, "competitor")} identified`, body: list(comps.map((c) => c.name), 6), ref: `c:${comps[0].seq}` });
      items.push(...localItems(m, undefined, !!ctx.simulated));
      if (m.keywords.length) items.push({ key: "all-kw", tone: "finding", basis: "Recorded keyword source responses", title: `${plural(m.keywords.length, "keyword source")} answered`, body: `${m.keywords.reduce((a, k) => a + (k.count || 0), 0)} terms reported` });
      items.push(...m.insights.map((i) => ({ key: `all-i-${i.seq}`, tone: "finding" as const, basis: "Recorded research step", title: i.title })));
      items.push(...providerGaps(m, []));
    }
  }
  items.push(...failedAudit(kind, ctx));
  return items.map((it, i) => ({ it, i })).sort((a, b) => ORDER[a.it.tone] - ORDER[b.it.tone] || a.i - b.i).map((x) => x.it);
}
