/** "What was found": the discoveries of an audit in plain language, above the step-by-step log.
 *  Two inputs only: the audit's stored result (once it exists) and the recorded research events. Pure and unit-tested: every line
 *  restates a value that is in one of those two, and a capability that produced nothing says so instead of being left out.
 *  `ref` points at the same id the scene uses, so hovering a line highlights the thing it is about. */
import { hostOf, providerLabel, type PageNode, type ResearchModel } from "./model";
import type { SceneKind } from "./sceneLayout";

export type DigestTone = "finding" | "gap" | "limit";
export type DigestItem = { key: string; tone: DigestTone; title: string; body?: string; chips?: string[]; ref?: string };

const list = (xs: string[], max = 4) => (xs.length <= max ? xs.join(", ") : `${xs.slice(0, max).join(", ")} and ${xs.length - max} more`);
const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
const arr = (v: any): any[] => (Array.isArray(v) ? v : []);

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
  const read = pages.filter((p) => p.state === "extracted");
  const failed = pages.filter((p) => p.state === "failed");
  const blocked = pages.filter((p) => p.state === "blocked");
  if (!pages.length) return out;
  const hosts = [...new Set(read.map((p) => hostOf(p.url)))];
  out.push({ key: `${keyPrefix}read`, tone: read.length ? "finding" : "gap",
    title: read.length ? `Read ${plural(read.length, "page")} on ${list(hosts, 3)}` : `No page of ${who} could be read`,
    body: [failed.length ? `${plural(failed.length, "page")} could not be opened${(() => { const st = [...new Set(failed.map((p) => p.status).filter(Boolean))]; return st.length ? ` (HTTP ${st.join(", ")})` : ""; })()}` : "",
      blocked.length ? `${plural(blocked.length, "page")} skipped because robots.txt disallows them` : ""].filter(Boolean).join("; ") || undefined });
  const home = read.find((p) => p.path === "/") ?? read[0];
  if (home?.h1) out.push({ key: `${keyPrefix}home`, tone: "finding", title: `${home.path === "/" ? "Home page" : `“${pageName(home)}”`} leads with “${home.h1}”`, ref: `p:${home.url}` });
  const deep = read.filter((p) => (p.words ?? 0) > 0).sort((a, b) => (b.words ?? 0) - (a.words ?? 0)).slice(0, 3);
  if (read.length > 2 && deep.length) out.push({ key: `${keyPrefix}deep`, tone: "finding", title: "Most substantial pages",
    body: deep.map((p) => `${pageName(p)} (${p.words} words)`).join(" · "), ref: `p:${deep[0].url}` });
  const rec = recurringHeadings(read).slice(0, 8);
  if (rec.length) out.push({ key: `${keyPrefix}rec`, tone: "finding", title: `Sections repeated across pages`, chips: rec.map((r) => `${r.heading} · ${r.pages}`) });
  return out;
}

function websiteFromResult(r: any): DigestItem[] {
  const out: DigestItem[] = [];
  const services = arr(r.service_inventory).map((s) => s?.name).filter(Boolean);
  out.push(services.length ? { key: "w-svc", tone: "finding", title: `${plural(services.length, "service")} found on the site`, chips: services.slice(0, 10) }
    : { key: "w-svc", tone: "gap", title: "No services could be identified from the site's pages" });
  const noPage = arr(r.services_without_page);
  if (noPage.length) out.push({ key: "w-nopage", tone: "gap", title: `${plural(noPage.length, "profile service")} with no dedicated page`, body: list(noPage, 6) });
  const notInProfile = arr(r.services_not_in_profile);
  if (notInProfile.length) out.push({ key: "w-notprof", tone: "gap", title: "On the site but missing from the client profile", body: list(notInProfile, 6) });
  const issues = arr(r.seo_issues);
  out.push(issues.length ? { key: "w-seo", tone: "gap", title: `${plural(issues.length, "technical or SEO issue")}`,
    body: issues.slice(0, 3).map((i) => `${i.issue}${i.url ? ` (${(() => { try { return new URL(i.url).pathname; } catch { return i.url; } })()})` : ""}`).join(" · ") }
    : { key: "w-seo", tone: "finding", title: "No technical or SEO issues were flagged" });
  const scores = arr(r.content_scores).filter((c) => typeof c?.score === "number").sort((a, b) => a.score - b.score);
  if (scores.length) out.push({ key: "w-score", tone: scores[0].score < 60 ? "gap" : "finding", title: `Weakest landing page: ${scores[0].service} (${scores[0].score}/100)`, body: arr(scores[0].notes).join("; ") || undefined });
  const tags = [...arr(r.google_tags_detected), ...arr(r.gtm_detected).map((g: string) => `GTM ${g}`)];
  out.push({ key: "w-tags", tone: tags.length ? "finding" : "gap", title: tags.length ? `Google tags on the site: ${list(tags, 4)}` : "No Google tag or Tag Manager container was detected" });
  const claims = arr(r.claims_not_in_verified_list);
  if (claims.length) out.push({ key: "w-claims", tone: "gap", title: `${plural(claims.length, "claim")} on the site are not in the verified list`, body: "Ads cannot use them until they are verified." });
  return out;
}

function competitorItems(m: ResearchModel, r: any): DigestItem[] {
  const out: DigestItem[] = [];
  const byName = new Map(arr(r?.competitors).map((c) => [String(c?.name ?? "").toLowerCase(), c]));
  for (const c of m.competitors.filter((x) => typeof x.lat !== "number" || x.url)) {
    const host = c.url ? hostOf(c.url) : "";
    const pages = m.pages.filter((p) => p.stage === "competitors" && host && hostOf(p.url) === host);
    const read = pages.filter((p) => p.state === "extracted");
    const failed = pages.filter((p) => p.state === "failed");
    const home = read.find((p) => p.path === "/") ?? read[0];
    const res = byName.get(c.name.toLowerCase());
    const services = arr(res?.services);
    const lacks = arr(res?.services_client_lacks);
    const offers = arr(res?.offers_on_site);
    const lines = [
      read.length ? `${plural(read.length, "page")} read${home?.h1 ? `; leads with “${home.h1}”` : ""}` : failed.length ? `Site could not be opened${failed[0].status ? ` (HTTP ${failed[0].status})` : ""}` : c.url ? "Site not read yet" : "",
      res ? (services.length ? `Services: ${list(services, 5)}` : "No services could be extracted") : "",
      lacks.length ? `Offers that the client does not: ${list(lacks, 4)}` : "",
      offers.length ? `Offers on their site: ${list(offers, 2)}` : "",
      res?.keyword_ideas?.length ? `Keyword ideas: ${list(res.keyword_ideas, 4)}` : res?.keyword_error ? "Keyword ideas unavailable (Google Ads not connected)" : "",
    ].filter(Boolean);
    out.push({ key: `cmp-${c.seq}`, tone: !read.length && failed.length ? "gap" : "finding", title: `${c.name}${c.basis ? ` — ${c.basis}` : ""}`, body: lines.join(" · ") || undefined, ref: `c:${c.seq}` });
  }
  if (!out.length && r) out.push({ key: "cmp-none", tone: "gap", title: "No competitor was analysed" });
  for (const w of arr(r?.web_candidates).slice(0, 4)) out.push({ key: `cmp-web-${w.website_url}`, tone: "finding", title: `Suggested by web search: ${w.name}`, body: "Not verified as a competitor." });
  if (r?.web_discovery && r.web_discovery.used === false && r.web_discovery.reason) out.push({ key: "cmp-webx", tone: "limit", title: `Competitor discovery by web search was not used`, body: r.web_discovery.reason });
  for (const s of arr(r?.missing_sources)) out.push({ key: `cmp-miss-${s}`, tone: "limit", title: s });
  return out;
}

function keywordItems(m: ResearchModel, r: any): DigestItem[] {
  const out: DigestItem[] = [];
  if (r && Array.isArray(r.keywords)) {
    const kws = arr(r.keywords);
    const sel = kws.filter((k) => k?.selected);
    const clusters = Object.entries((r.clusters ?? {}) as Record<string, string[]>);
    out.push({ key: "kw-total", tone: kws.length ? "finding" : "gap", title: kws.length ? `${plural(kws.length, "keyword")} researched, ${sel.length} selected` : "No keywords were produced",
      body: arr(r.sources_used).length ? `From: ${arr(r.sources_used).map((s: string) => s.replace(/_/g, " ")).join(", ")}` : undefined });
    for (const [name, terms] of clusters.slice(0, 6)) out.push({ key: `kw-c-${name}`, tone: "finding", title: `Ad group “${name}”`, chips: arr(terms).slice(0, 8) });
    const observed = kws.filter((k) => typeof k?.avg_monthly_searches === "number").length;
    out.push({ key: "kw-vol", tone: observed ? "finding" : "limit", title: observed ? `${observed} of ${kws.length} have observed search volume` : "No search volume was observed",
      body: !observed && r.keyword_planner?.available === false ? r.keyword_planner.reason : undefined });
    const neg = arr(r.negative_ideas);
    out.push({ key: "kw-neg", tone: neg.length ? "finding" : "gap", title: neg.length ? `${plural(neg.length, "negative keyword idea")}` : "No negative keyword ideas", chips: neg.slice(0, 8) });
    if (arr(r.overlaps).length) out.push({ key: "kw-ovl", tone: "gap", title: `${plural(arr(r.overlaps).length, "keyword")} overlap between ad groups` });
    return out;
  }
  for (const k of m.keywords.slice(0, 8)) out.push({ key: `kw-d-${k.seq}`, tone: "finding", title: `${k.seed}: ${plural(k.count, "term")} from ${providerLabel(k.provider) || "a source"}`, chips: k.examples.slice(0, 6), ref: `k:${k.seq}` });
  for (const i of m.insights) out.push({ key: `kw-i-${i.seq}`, tone: "finding", title: i.title });
  return out;
}

function localItems(m: ResearchModel, r: any): DigestItem[] {
  const out: DigestItem[] = [];
  const c = r?.client;
  if (c && c.name) out.push({ key: "gbp-client", tone: "finding", title: `${c.name}${c.primary_category ? ` · ${c.primary_category}` : ""}`,
    body: [typeof c.rating === "number" ? `${c.rating}★` : "", typeof c.user_rating_count === "number" ? plural(c.user_rating_count, "review") : "", c.simulated ? "test fixture, not Google Places" : ""].filter(Boolean).join(" · ") || undefined });
  const rivals = arr(r?.competitors).filter(Boolean);
  if (rivals.length) {
    const rated = rivals.filter((x) => typeof x.rating === "number").sort((a, b) => b.rating - a.rating);
    out.push({ key: "gbp-rivals", tone: "finding", title: `${plural(rivals.length, "competing listing")} compared`, body: rated.length ? `Highest rated: ${rated[0].name} (${rated[0].rating}★${typeof rated[0].user_rating_count === "number" ? `, ${rated[0].user_rating_count} reviews` : ""})` : undefined });
  }
  for (const f of arr(r?.findings).slice(0, 4)) out.push({ key: `gbp-f-${f}`, tone: "finding", title: String(f) });
  const loc = m.locations.find((l) => l.role === "business") ?? m.locations[0];
  if (loc) out.push({ key: "gbp-loc", tone: "finding", title: `Located at ${loc.label}`, ref: `l:${loc.seq}` });
  const listings = m.competitors.filter((x) => typeof x.distance_km === "number").sort((a, b) => a.distance_km! - b.distance_km!);
  if (listings.length) out.push({ key: "gbp-near", tone: "finding", title: `${plural(listings.length, "same-category listing")} on the map`,
    body: `Nearest: ${listings.slice(0, 3).map((x) => `${x.name} (${x.distance_km} km)`).join(", ")}`, ref: `c:${listings[0].seq}` });
  const geo = r?.geo;
  if (geo && geo.available === false && geo.reason) out.push({ key: "gbp-geo", tone: "limit", title: "Local market data unavailable", body: geo.reason });
  return out;
}

/** The digest for one audit type (or the combined view). `result` is the stored audit result; pass nothing while it runs. */
export function buildDigest(kind: SceneKind, m: ResearchModel, result?: any): DigestItem[] {
  const r = result && typeof result === "object" && Object.keys(result).length ? result : undefined;
  const site = m.pages.filter((p) => p.stage !== "competitors");
  switch (kind) {
    case "website": return [...crawlItems(site, "the website", "ws-"), ...(r ? websiteFromResult(r) : [])];
    case "competitors": return competitorItems(m, r);
    case "keywords": return keywordItems(m, r);
    case "local": return localItems(m, r);
    default: {
      const out: DigestItem[] = [...crawlItems(site, "the website", "ws-")];
      const comps = m.competitors.filter((c) => c.url);
      if (comps.length) out.push({ key: "all-cmp", tone: "finding", title: `${plural(comps.length, "competitor")} identified`, body: list(comps.map((c) => c.name), 6), ref: `c:${comps[0].seq}` });
      out.push(...localItems(m, undefined));
      if (m.keywords.length) out.push({ key: "all-kw", tone: "finding", title: `${plural(m.keywords.length, "keyword source")} answered`, body: `${m.keywords.reduce((a, k) => a + (k.count || 0), 0)} terms reported` });
      out.push(...m.insights.map((i) => ({ key: `all-i-${i.seq}`, tone: "finding" as const, title: i.title })));
      return out;
    }
  }
}
