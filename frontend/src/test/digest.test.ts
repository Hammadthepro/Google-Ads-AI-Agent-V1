import { buildModel, type ResearchEvent } from "../components/research/model";
import { buildDigest, recurringHeadings } from "../components/research/digest";

let seq = 0;
const ev = (kind: string, title: string, data: Record<string, any> = {}, stage = "website", level: "info" | "warn" = "info"): ResearchEvent =>
  ({ seq: ++seq, kind, stage, level, title, data, at: "2026-11-13T00:00:00Z" });
const page = (url: string, title: string, h1: string, headings: string[], words: number, stage = "website") =>
  ev("page_extracted", title, { url, page_title: title, h1, headings, words }, stage);

describe("what-was-found digest", () => {
  beforeEach(() => { seq = 0; });

  it("states nothing when nothing was recorded", () => {
    for (const k of ["website", "competitors", "keywords", "local", "combined"] as const) expect(buildDigest(k, buildModel([]))).toEqual([]);
  });

  it("website while running: counts, the home page heading, the largest pages and headings repeated across pages — all from page events", () => {
    const m = buildModel([
      page("https://a.example/", "Home", "Emergency plumbing in Austin", ["Services", "Reviews"], 400),
      page("https://a.example/drains", "Drains", "Drain cleaning", ["Services", "Pricing"], 1200),
      page("https://a.example/water", "Water heaters", "Water heater repair", ["Services", "Pricing"], 800),
      ev("page_failed", "", { url: "https://a.example/old", status: 404 }, "website", "warn"),
    ]);
    const d = buildDigest("website", m);
    expect(d.map((i) => i.title)).toEqual(["Read 3 pages on a.example", "Home page leads with “Emergency plumbing in Austin”", "Most substantial pages", "Sections repeated across pages"]);
    expect(d[0].body).toBe("1 page could not be opened (HTTP 404)");
    expect(d[2].body).toBe("Drains (1200 words) · Water heaters (800 words) · Home (400 words)");
    expect(d[3].chips).toEqual(["Services · 3", "Pricing · 2"]);
    expect(d[1].ref).toBe("p:https://a.example/");
  });

  it("website once completed adds the stored analysis, and says so when a part found nothing", () => {
    const d = buildDigest("website", buildModel([]), { service_inventory: [], seo_issues: [], google_tags_detected: [], gtm_detected: [] });
    expect(d.map((i) => [i.tone, i.title])).toEqual([
      ["gap", "No services could be identified from the site's pages"], ["finding", "No technical or SEO issues were flagged"],
      ["gap", "No Google tag or Tag Manager container was detected"]]);
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
    expect(d.map((i) => [i.tone, i.title, i.body])).toEqual([
      ["finding", "PyPI — supplied by the client", "1 page read; leads with “Find, install and publish Python packages” · No services could be extracted · Keyword ideas unavailable (Google Ads not connected)"],
      ["gap", "npm — supplied by the client", "Site could not be opened (HTTP 403) · No services could be extracted"],
      ["limit", "Competitor discovery by web search was not used", "Tavily is not configured"],
      ["limit", "Paid competitor bidding data not connected", undefined]]);
    expect(d[0].ref).toBe("c:1");
  });

  it("keywords: totals, ad groups and observed volume come from the stored result", () => {
    const d = buildDigest("keywords", buildModel([]), { keywords: [{ keyword: "a", selected: true }, { keyword: "b", selected: false, avg_monthly_searches: 90 }],
      clusters: { "Drain cleaning": ["a"] }, sources_used: ["client_profile"], negative_ideas: [], overlaps: [] });
    expect(d.map((i) => i.title)).toEqual(["2 keywords researched, 1 selected", "Ad group “Drain cleaning”", "1 of 2 have observed search volume", "No negative keyword ideas"]);
    expect(d[0].body).toBe("From: client profile");
  });

  it("local: listings are named by real distance, nearest first", () => {
    const m = buildModel([
      ev("location_identified", "Austin, Texas", { lat: 30, lon: -97, role: "business" }, "geo"),
      ev("competitor_identified", "Far Co", { lat: 30.1, lon: -97, distance_km: 11.1 }, "geo"),
      ev("competitor_identified", "Near Co", { lat: 30.01, lon: -97, distance_km: 1.1 }, "geo"),
    ]);
    const d = buildDigest("local", m);
    expect(d.map((i) => i.title)).toEqual(["Located at Austin, Texas", "2 same-category listings on the map"]);
    expect(d[1].body).toBe("Nearest: Near Co (1.1 km), Far Co (11.1 km)");
  });

  it("recurring headings ignore pages that repeat a heading and very long headings", () => {
    const m = buildModel([page("https://a.example/", "A", "", ["Help", "Help"], 1), page("https://a.example/b", "B", "", ["Help", "x".repeat(80)], 1)]);
    expect(recurringHeadings(m.pages)).toEqual([{ heading: "Help", pages: 2 }]);
  });
});
