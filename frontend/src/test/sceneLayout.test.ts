import { buildModel, type ResearchEvent } from "../components/research/model";
import { buildScene, sceneCounts } from "../components/research/sceneLayout";

let seq = 0;
const ev = (kind: string, title: string, data: Record<string, any> = {}, stage = "", level: "info" | "warn" | "error" = "info"): ResearchEvent =>
  ({ seq: ++seq, kind, stage, level, title, data, at: "2026-11-13T00:00:00Z" });

const SITE = () => { seq = 0; return [
  ev("audit_started", "Research started", { business: "Northside Plumbing", website: "https://northside.example/" }),
  ev("url_crawling", "", { url: "https://northside.example/" }, "website"),
  ev("page_extracted", "Home", { url: "https://northside.example/", page_title: "Home", words: 600 }, "website"),
  ev("page_extracted", "Services", { url: "https://northside.example/services", page_title: "Services", words: 300 }, "website"),
  ev("page_extracted", "Drains", { url: "https://northside.example/services/drains", page_title: "Drains", words: 900 }, "website"),
  ev("url_crawling", "", { url: "https://northside.example/contact" }, "website"),
  ev("page_failed", "", { url: "https://northside.example/old", status: 404 }, "website", "warn"),
  ev("crawl_blocked", "", { url: "https://northside.example/admin" }, "website", "warn"),
]; };

describe("scene layouts draw only what was recorded", () => {
  it("draws nothing but a waiting centre when there are no events", () => {
    for (const kind of ["website", "local", "competitors", "keywords"] as const) {
      const g = buildScene(kind, buildModel([]));
      expect(g.nodes).toHaveLength(1);
      expect(g.nodes[0].hub).toBe(true);
      expect(g.edges).toEqual([]);
      expect(g.empty).toBeTruthy();
    }
    expect(sceneCounts("website", buildModel([])).every((c) => c.value === 0)).toBe(true);
  });

  it("website: one node per page, nested under the page whose path contains it, with the real page state", () => {
    const g = buildScene("website", buildModel(SITE()));
    const pages = g.nodes.filter((n) => n.id.startsWith("p:"));
    expect(pages.map((n) => n.id).sort()).toEqual([
      "p:https://northside.example/", "p:https://northside.example/admin", "p:https://northside.example/contact",
      "p:https://northside.example/old", "p:https://northside.example/services", "p:https://northside.example/services/drains"]);
    const role = (u: string) => g.nodes.find((n) => n.id === `p:https://northside.example${u}`)!.role;
    expect([role("/contact"), role("/old"), role("/admin"), role("/services")]).toEqual(["working", "failed", "blocked", "page"]);
    // the home page is the centre; /services/drains hangs off /services, not off the centre
    expect(g.nodes.find((n) => n.hub)!.id).toBe("p:https://northside.example/");
    expect(g.edges).toContainEqual(expect.objectContaining({ a: "p:https://northside.example/services", b: "p:https://northside.example/services/drains" }));
    expect(sceneCounts("website", buildModel(SITE())).map((c) => [c.label, c.value])).toEqual([["pages read", 3], ["page opening", 1], ["page failed", 1], ["page skipped", 1]]);
  });

  it("local market: listings sit at their true direction from the business, inside distance rings", () => {
    seq = 0;
    const m = buildModel([
      ev("location_identified", "Austin, Texas", { lat: 30.0, lon: -97.0, role: "business" }, "geo"),
      ev("competitor_identified", "East Co", { lat: 30.0, lon: -96.95, distance_km: 4.8 }, "geo"),
      ev("competitor_identified", "North Co", { lat: 30.03, lon: -97.0, distance_km: 3.3 }, "geo"),
      ev("competitor_identified", "Web-only rival", { url: "https://rival.example" }, "competitors"),
    ]);
    const g = buildScene("local", m);
    const east = g.nodes.find((n) => n.label === "East Co")!, north = g.nodes.find((n) => n.label === "North Co")!;
    expect(east.x).toBeGreaterThan(0); expect(Math.abs(east.z)).toBeLessThan(0.01);
    expect(north.z).toBeLessThan(0); expect(Math.abs(north.x)).toBeLessThan(0.01);
    expect(g.nodes.some((n) => n.label === "Web-only rival")).toBe(false);   // no coordinates, so no invented position
    expect(g.rings.map((r) => r.label)).toEqual(["1 km", "2 km", "5 km"]);
  });

  it("competitors: crawled competitor pages orbit the competitor they belong to", () => {
    seq = 0;
    const m = buildModel([
      ev("competitor_identified", "Rival", { url: "https://rival.example", basis: "supplied by the client" }, "competitors"),
      ev("page_extracted", "Rival home", { url: "https://rival.example/", page_title: "Rival" }, "competitors"),
      ev("page_extracted", "Rival drains", { url: "https://rival.example/drains" }, "competitors"),
    ]);
    const g = buildScene("competitors", m);
    expect(g.nodes.filter((n) => n.role === "competitor")).toHaveLength(1);
    expect(g.edges.filter((e) => e.a === "c:1").map((e) => e.b)).toEqual(["p:https://rival.example/", "p:https://rival.example/drains"]);
  });

  it("keywords: one cluster per discovery, terms only from the examples the event carried", () => {
    seq = 0;
    const m = buildModel([
      ev("keyword_discovery", "3 suggestion(s) for “drain cleaning”", { seed: "drain cleaning", provider: "google", count: 3, examples: ["drain cleaning near me", "drain cleaning cost"] }, "keywords"),
      ev("keyword_discovery", "12 term(s) from the client website", { provider: "crawler", count: 12 }, "keywords"),
    ]);
    const g = buildScene("keywords", m);
    expect(g.nodes.filter((n) => n.role === "cluster").map((n) => n.label)).toEqual(["drain cleaning", "12 term(s) from the client website"]);
    expect(g.nodes.filter((n) => n.role === "term").map((n) => n.label)).toEqual(["drain cleaning near me", "drain cleaning cost"]);
    expect(sceneCounts("keywords", m).find((c) => c.label === "terms reported")!.value).toBe(15);
  });

  it("keywords: sources that were asked but did not answer are shown as such, not hidden", () => {
    seq = 0;
    const m = buildModel([
      ev("provider_unavailable", "Keyword Planner is not available", { provider: "keyword_planner" }, "keywords", "warn"),
      ev("provider_failed", "Google Autocomplete: network error", { provider: "google", error_kind: "network_error" }, "keywords", "warn"),
    ]);
    const g = buildScene("keywords", m);
    expect(g.nodes.filter((n) => !n.hub).map((n) => [n.id, n.role, n.sub])).toEqual([["x:1", "blocked", "Not available"], ["x:2", "failed", "Did not answer: network error"]]);
    expect(g.empty).toBeNull();
  });

  it("combined: each research stream is its own island, lit only once its stage has started", () => {
    const g = buildScene("combined", buildModel(SITE()), { website: "running" });
    expect(g.islands.map((i) => [i.id, i.state])).toEqual([["website", "running"], ["local", "waiting"], ["competitors", "waiting"]]);
    expect(g.nodes.filter((n) => n.id.startsWith("local|")).every((n) => n.dim)).toBe(true);
    expect(new Set(g.nodes.map((n) => n.id)).size).toBe(g.nodes.length);   // ids stay unique across islands
  });
});
