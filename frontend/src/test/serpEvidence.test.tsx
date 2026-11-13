import { render, screen, within } from "@testing-library/react";
import { SerpEvidence } from "../components/research/SerpEvidence";
import { buildStream, describe as describeEvent, type ResearchEvent } from "../components/research/model";

const serp = {
  used: true, location: "Columbus,Ohio,United States", location_note: "",
  queries: [
    { engine: "google", query: "drain cleaning columbus", location: "Columbus,Ohio,United States", retrieved_at: "2026-11-13T10:00:00Z", served_from_cache: false, search_id: "s_1", ok: true },
    { engine: "google_autocomplete", query: "drain cleaning", retrieved_at: "2026-11-10T09:00:00Z", served_from_cache: true, ok: true },
    { engine: "google", query: "water heater repair columbus", ok: false, error_kind: "quota_exceeded", error: "no searches left" },
  ],
  sponsored_ads: [{ domain: "rivalco.example", title: "Fast Drain Cleaning | RivalCo", link: "https://www.rivalco.example/l", displayed_link: "www.rivalco.example",
    description: "Same-day service.", block_position: "top", query: "drain cleaning columbus", retrieved_at: "2026-11-13T10:00:00Z", is_client: false, sitelinks: ["Pricing"] },
    { domain: "northside.example", title: "Our own ad", link: "https://northside.example", query: "x", is_client: true }],
  organic_competitors: [{ domain: "acmepipes.example", url: "https://acmepipes.example/drains", title: "AcmePipes", best_position: 3, appearances: [{}], already_known: false }],
  related_searches: [{ phrase: "drain cleaning cost" }], autocomplete: [{ phrase: "drain cleaning near me" }],
  related_questions: [{ question: "How much does drain cleaning cost?", url: "https://help.example/cost" }],
  local_businesses: [{ title: "Local Pipe Pros", rating: 4.8, reviews: 120, address: "1 Main St", source_engine: "google local pack" }],
  unavailable: [{ capability: "Google Maps local businesses", reason: "The business location could not be geocoded" }],
  quota: { plan_searches_left: 241 },
};

describe("SerpEvidence", () => {
  it("shows every query with its place and time, and never presents a cached answer as live", () => {
    render(<SerpEvidence serp={serp} />);
    const q = screen.getByRole("region", { name: "Queries executed" });
    expect(within(q).getByText("“drain cleaning columbus”")).toBeInTheDocument();
    expect(within(q).getByText(/^live ·/)).toBeInTheDocument();
    expect(within(q).getByText(/^cached · retrieved/)).toBeInTheDocument();
    expect(within(q).getByText("quota_exceeded: no searches left")).toBeInTheDocument();
    expect(screen.getByText(/241 plan searches left/)).toBeInTheDocument();
  });
  it("labels ads as public copy, excludes the client's own ad, and shows no metrics", () => {
    const { container } = render(<SerpEvidence serp={serp} />);
    const ads = screen.getByRole("region", { name: "Sponsored results" });
    expect(within(ads).getByText(/public ad copy, not bids or performance/)).toBeInTheDocument();
    expect(within(ads).getByRole("link", { name: "Fast Drain Cleaning | RivalCo" })).toHaveAttribute("href", "https://www.rivalco.example/l");
    expect(within(ads).queryByText("Our own ad")).toBeNull();
    expect(container.textContent).not.toMatch(/search volume:|CPC:|\$\d/);
    expect(screen.getByText(/Not researched: Google Maps local businesses/)).toBeInTheDocument();
    expect(screen.getByText("drain cleaning near me")).toBeInTheDocument();
  });
  it("says plainly when SerpApi was not used", () => {
    render(<SerpEvidence serp={{ used: false, reason: "SerpApi is not configured or is switched off; Google results research was not used." }} />);
    expect(screen.getByText(/SerpApi is not configured/)).toBeInTheDocument();
  });
});

describe("SERP events in the live stream", () => {
  const ev = (seq: number, kind: string, title: string, data: any): ResearchEvent => ({ seq, kind, stage: "keywords", level: "info", title, data, at: "2026-11-13T10:00:00Z" });
  it("describes live and cached searches, ads and local businesses from real events", () => {
    const evs = [ev(1, "serp_query", "Searched", { query: "drain cleaning columbus", location: "Columbus,Ohio,United States", engine: "google", served_from_cache: false }),
      ev(2, "serp_query", "Cached", { query: "drain cleaning", engine: "google_autocomplete", served_from_cache: true }),
      ev(3, "sponsored_ad", "rivalco.example: Fast Drain Cleaning", { query: "drain cleaning columbus", url: "https://www.rivalco.example/l" }),
      ev(4, "local_business", "Local Pipe Pros", { rating: 4.8, reviews: 120, source: "google local pack" })];
    expect(describeEvent(evs[0])).toBe("Google search (SerpApi): “drain cleaning columbus” · Columbus,Ohio,United States");
    expect(describeEvent(evs[1])).toBe("From cache (SerpApi): “drain cleaning”");
    const items = buildStream(evs)[0].items;
    expect(items.map((i) => i.detail)[1]).toMatch(/^from cache, retrieved earlier/);
    expect(items[2].text).toBe("Ad: rivalco.example: Fast Drain Cleaning");
    expect(items[3].detail).toContain("4.8★ (120)");
  });
});
