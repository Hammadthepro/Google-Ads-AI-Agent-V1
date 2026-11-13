import { buildModel, type ResearchEvent } from "../components/research/model";
import { buildDigest, recurringHeadings } from "../components/research/digest";

let seq = 0;
const ev = (kind: string, title: string, data: Record<string, any> = {}, stage = "website", level: "info" | "warn" = "info"): ResearchEvent =>
  ({ seq: ++seq, kind, stage, level, title, data, at: "2026-11-13T00:00:00Z" });
const page = (url: string, title: string, h1: string, headings: string[], words: number, stage = "website") =>
  ev("page_extracted", title, { url, page_title: title, h1, headings, words }, stage);
const view = (items: ReturnType<typeof buildDigest>) => items.map((i) => [i.tone, i.title]);

describe("what-was-found digest", () => {
  beforeEach(() => { seq = 0; });

  it("states nothing when nothing was recorded", () => {
    for (const k of ["website", "competitors", "keywords", "local", "combined"] as const) expect(buildDigest(k, buildModel([]))).toEqual([]);
  });

  it("every line names what it rests on", () => {
    const m = buildModel([page("https://a.example/", "Home", "Plumbing in Austin", ["Services"], 400)]);
    for (const it of buildDigest("website", m, { service_inventory: [] })) expect(it.basis).toMatch(/Recorded|Stored|Page read|Word counts|Headings/);
  });

  it("website while running: discoveries first, problems next, all from page events", () => {
    const m = buildModel([
      page("https://a.example/", "Home", "Emergency plumbing in Austin", ["Services", "Reviews"], 400),
      page("https://a.example/drains", "Drains", "Drain cleaning", ["Services", "Pricing"], 1200),
      page("https://a.example/water", "Water heaters", "Water heater repair", ["Services", "Pricing"], 800),
      ev("page_failed", "", { url: "https://a.example/old", status: 404 }, "website", "warn"),
    ]);
    const d = buildDigest("website", m);
    expect(view(d)).toEqual([
      ["finding", "Read 3 pages on a.example"], ["finding", "Home page leads with “Emergency plumbing in Austin”"],
      ["finding", "Pages with the most content"], ["finding", "Sections that repeat across pages"],
      ["gap", "1 address could not be opened (HTTP 404)"]]);
    expect(d.find((i) => i.title === "Pages with the most content")!.points).toEqual(["Drains — 1200 words", "Water heaters — 800 words", "Home — 400 words"]);
    expect(d.find((i) => i.title === "Sections that repeat across pages")!.chips).toEqual(["Services · 3 pages", "Pricing · 2 pages"]);
    expect(d[1].ref).toBe("p:https://a.example/");
  });

  it("does not call sitemap files pages, and says when they were most of what was read", () => {
    const m = buildModel([page("https://a.example/", "Home", "Hello", [], 300), ...[0, 1, 2].map((i) => page(`https://a.example/0${i}.sitemap.xml`, `/0${i}.sitemap.xml`, "", [], 8000))]);
    const d = buildDigest("website", m);
    expect(d[0].title).toBe("Read 1 web page and 3 sitemap or data files on a.example");
    expect(d[0].body).toMatch(/sitemap files/);
    expect(d.some((i) => i.title === "Pages with the most content")).toBe(false);   // word counts of sitemap files are not content
  });

  it("website once completed adds the stored analysis, and says so when a part found nothing", () => {
    const d = buildDigest("website", buildModel([]), { service_inventory: [], seo_issues: [], google_tags_detected: [], gtm_detected: [] });
    expect(view(d)).toEqual([
      ["finding", "No technical or SEO issues were flagged"],
      ["gap", "No services could be identified from the site's pages"], ["gap", "No Google tag or Tag Manager container was detected"]]);
    expect(d.every((i) => i.basis === "Stored audit result")).toBe(true);
  });

  it("a failed audit says the analysis did not finish and what that leaves out, from the recorded error", () => {
    const d = buildDigest("website", buildModel([page("https://a.example/", "Home", "Hi", [], 10)]), undefined, { status: "FAILED", error: "Groq (internal AI) integration is not configured." });
    const last = d[d.length - 1];
    expect([last.tone, last.title]).toEqual(["limit", "The analysis did not finish"]);
    expect(last.body).toBe("Groq (internal AI) integration is not configured. The service inventory, landing-page scores, SEO checks and tag detection were not produced.");
  });

  it("sources that did not answer are listed once each under what could not be verified", () => {
    const m = buildModel([
      ev("provider_failed", "x", { provider: "google", error_kind: "outage" }, "keywords", "warn"),
      ev("provider_failed", "x again", { provider: "google", error_kind: "outage" }, "keywords", "warn"),
      ev("provider_unavailable", "y", { provider: "keyword_planner", reason: "Google Ads API integration is not configured." }, "keywords", "warn"),
    ]);
    expect(buildDigest("keywords", m).map((i) => [i.tone, i.title, i.body])).toEqual([
      ["limit", "Google Autocomplete did not answer", "Reason: outage"],
      ["limit", "Keyword Planner was not available", "Google Ads API integration is not configured."]]);
  });

  it("competitors: one line per competitor with what was read and what the stored result holds", () => {
    const m = buildModel([
      ev("competitor_identified", "PyPI", { url: "https://pypi.org/", basis: "supplied by the client" }, "competitors"),
      page("https://pypi.org/", "PyPI", "Find, install and publish Python packages", [], 293, "competitors"),
      ev("competitor_identified", "npm", { url: "https://www.npmjs.com/", basis: "supplied by the client" }, "competitors"),
      ev("page_failed", "", { url: "https://www.npmjs.com/", status: 403 }, "competitors", "warn"),
    ]);
    const d = buildDigest("competitors", m, { competitors: [{ name: "PyPI", services: [], keyword_error: "not configured" }, { name: "npm", services: [] }],
      web_discovery: { used: false, reason: "Tavily is not configured" }, missing_sources: ["Paid competitor bidding data not connected"] });
    expect(d.map((i) => [i.tone, i.title, i.points])).toEqual([
      ["finding", "PyPI (supplied by the client)", ["1 address read; their site leads with “Find, install and publish Python packages”", "No services could be extracted from their pages", "Keyword ideas unavailable: Google Ads is not connected"]],
      ["gap", "npm (supplied by the client)", ["Their site could not be opened (HTTP 403)", "No services could be extracted from their pages"]],
      ["limit", "Competitors were not searched for on the web", undefined],
      ["limit", "Paid competitor bidding data not connected", undefined]]);
    expect(d[0].ref).toBe("c:1");
  });

  it("competitors: a skipped audit says why, from the stored result", () => {
    expect(view(buildDigest("competitors", buildModel([]), { skipped: true, reason: "No competitors in the business profile (decision D-05)" })))
      .toEqual([["limit", "No competitor was audited"]]);
  });

  it("keywords: totals, ad groups, volume and missing inputs come from the stored result", () => {
    const d = buildDigest("keywords", buildModel([]), { keywords: [{ keyword: "a", selected: true }, { keyword: "b", selected: false, avg_monthly_searches: 90 }],
      clusters: { "Drain cleaning": ["a"] }, sources_used: ["client_profile"], negative_ideas: [], overlaps: [], inputs: { WEBSITE: null, GBP: { audit_id: "x" } } });
    expect(view(d)).toEqual([["finding", "2 keywords researched, 1 selected for ads"], ["finding", "Proposed ad group “Drain cleaning”"],
      ["finding", "1 of 2 keywords have observed search volume"], ["gap", "No negative keyword ideas were produced"], ["limit", "Built without website research"]]);
    expect(d[0].body).toBe("Built from: client profile");
  });

  it("local: listings are named by real distance, nearest first; fixture data says so on every line", () => {
    const m = buildModel([
      ev("location_identified", "Austin, Texas", { lat: 30, lon: -97, role: "business" }, "geo"),
      ev("competitor_identified", "Far Co", { lat: 30.1, lon: -97, distance_km: 11.1 }, "geo"),
      ev("competitor_identified", "Near Co", { lat: 30.01, lon: -97, distance_km: 1.1 }, "geo"),
    ]);
    const d = buildDigest("local", m);
    expect(view(d)).toEqual([["finding", "Located at Austin, Texas"], ["finding", "2 same-category businesses on the map nearby"]]);
    expect(d[1].points).toEqual(["Near Co — 1.1 km", "Far Co — 11.1 km"]);
    expect(buildDigest("local", m, undefined, { simulated: true }).every((i) => /test fixture/.test(i.basis))).toBe(true);
  });

  it("local: a run with no map data at all states the gaps instead of breaking", () => {
    const d = buildDigest("local", buildModel([]), { client: null, competitors: [], findings: [], places_available: false, geo: { available: false, reason: "No geocoding provider answered" } });
    expect(view(d)).toEqual([["limit", "Local market data was not available"], ["limit", "Google Places was not available"]]);
  });

  it("recurring headings ignore pages that repeat a heading and very long headings", () => {
    const m = buildModel([page("https://a.example/", "A", "", ["Help", "Help"], 1), page("https://a.example/b", "B", "", ["Help", "x".repeat(80)], 1)]);
    expect(recurringHeadings(m.pages)).toEqual([{ heading: "Help", pages: 2 }]);
  });

  it("local, real path without Google Places: OpenStreetMap presence is a discovery and the Places gap is stated once", () => {
    const m = buildModel([
      ev("location_identified", "Austin, Texas", { lat: 30, lon: -97, role: "business" }, "geo"),
      ev("provider_unavailable", "Google Places is not configured: ratings, reviews and hours are not available", { provider: "places" }, "gbp", "warn"),
    ]);
    const d = buildDigest("local", m, { client: null, competitors: [], findings: ["Listed on OpenStreetMap: Acme Plumbing"], places_available: false,
      presence: { name: "Acme Plumbing" }, geo: { available: true, density: { total: 7 }, radius_m: 16093, center: { display_name: "Austin" } } });
    expect(view(d)).toEqual([
      ["finding", "Listed on OpenStreetMap: Acme Plumbing"], ["finding", "Located at Austin, Texas"], ["finding", "7 same-category businesses within 10 miles of Austin"],
      ["limit", "Google Places was not available"]]);
    expect(buildDigest("local", m, { findings: ["Not found on OpenStreetMap (common for small businesses; it does not mean there is no Google profile)"] }).find((i) => i.title.startsWith("Not found"))!.tone).toBe("limit");
  });
});
