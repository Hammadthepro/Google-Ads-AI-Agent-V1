import { fireEvent, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { ResearchAppendix } from "../components/ResearchAppendix";
import { compact, DataTable } from "../components/report/DataTable";

const mount = (node: React.ReactNode) =>
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MemoryRouter>{node}</MemoryRouter></QueryClientProvider>);
const json = (b: any) => new Response(JSON.stringify(b), { status: 200, headers: { "content-type": "application/json" } });

const row = (i: number, over: any = {}) => ({ keyword: `drain cleaning ${i}`, service: "Drain Cleaning", ad_group: "Drain Cleaning", match_types: ["EXACT", "PHRASE"],
  search_volume: null, avg_cpc: null, competition: null, top_of_page_bid_high: null, keyword_class: "high_intent_commercial", keyword_class_label: "High-intent commercial",
  location_relevance: "none", measurement_status: "unmeasured", selection_status: "eligible", unmeasured_reason: "DataForSEO: account must be verified", providers: ["Google Autocomplete"], ...over });

/** The ABC case as the backend now records it: DataForSEO blocked by account verification, Keyword Planner refused (Explorer access),
 *  no business hours, google.com rejected as a competitor, a keyword for another city rejected. */
const abc = {
  version: 2, plan_version: 1, captured_with_version: true, captured_at: "2026-11-16T10:00:00Z",
  settings: { location: { mode: "radius", label: "15 mile radius around 1200 Example Ave, Columbus, OH" }, location_targeting_mode: { label: "Presence: people in or regularly in the targeted area" },
    language: { code: "en", label: "English" }, ad_schedule: { mode: null, label: null },
    issues: [{ code: "NO_AD_SCHEDULE", field: "ad_schedule", blocking: true, message: "No business hours are recorded: set the hours in the client profile, or set the campaign to run all days and hours." }] },
  keywords: {
    keyword_planner: { available: false, reason: "refused", access_level: "explorer", access_note: "Apply for Basic or Standard access." },
    dataforseo: { used: true, metrics: { requested: 40, with_volume: 0, blocked_by: "account_verification_required" },
      errors: [{ step: "search_volume", kind: "account_verification_required", message: "DataForSEO: the account must be verified (status 40104)." }] },
    measurement_coverage: { total: 31, eligible: 30, eligible_measured: 0, eligible_unmeasured: 30, measured_total: 0, research_only: 0, rejected: 1,
      unmeasured_reasons: [{ reason: "DataForSEO: account must be verified", count: 30 }], service_areas: ["columbus", "oh", "ohio"] },
    rows: [...Array.from({ length: 30 }, (_, i) => row(i)),
      row(99, { keyword: "drain cleaning pittsburgh", keyword_class: "irrelevant_location", keyword_class_label: "Irrelevant location", location_relevance: "outside_area",
        selection_status: "rejected", rejection_reason: "names “pittsburgh”, which is not one of the client's service areas", ad_group: null })],
  },
  competitor_verification: { counts: { discovered: 3, serp_verified: 1, website_verified: 0, market_verified: 0, verified: 1, pending: 1, rejected: 1, client_provided: 1 },
    records: [
      { domain: "rapidrooter.example", name: "Rapid Rooter", url: "https://rapidrooter.example", classification: "client_provided", status: "Pending Verification", discovery_sources: ["client profile"], serp_evidence: [], service_evidence: [], geo_overlap_evidence: [], evidence_note: "supplied by the client" },
      { domain: "localdrains.example", name: "Local Drains", url: "https://localdrains.example", classification: "discovered", status: "SERP Verified", discovery_sources: ["Google results (SerpApi)"], serp_evidence: [{ provider: "serpapi", query: "drain cleaning columbus", position: 2 }], service_evidence: [], geo_overlap_evidence: [] },
      { domain: "google.com", name: "google.com", url: "https://google.com", classification: "discovered", status: "Rejected", discovery_sources: ["Google ads (SerpApi)"], serp_evidence: [], service_evidence: [], geo_overlap_evidence: [], rejection_reason: "search engine (google.com): not a business competing for the client's customers" },
    ] },
  source_ledger: { counts: { discovered: 2, crawled: 1, extracted: 1, relevant: 1, verified: 1, rejected: 0, failed: 1 }, records: [
    { title: "Drain cleaning", url: "https://northside.example/drain", provider: "Website crawler", source_type: "web page", http_status: 200, extraction_status: "extracted (400 words)", relevance_score: 100, verification_status: "Verified" },
    { title: "Rival", url: "https://rival.example", provider: "Website crawler", source_type: "web page", http_status: 503, extraction_status: "not extracted", verification_status: "Failed", failure_reason: "HTTP 503" }] },
  provider_errors: [{ provider: "dataforseo", count: 3, reasons: ["account_verification_required"], first_at: "2026-11-16T10:00:00Z", last_at: "2026-11-16T10:01:00Z" }],
  sources: [], competitors: [], feasibility: { verdict: "TIGHT", daily_budget: 98.68 }, methodology: { events_recorded: 12, unavailable: [] },
};

describe("Campaign report", () => {
  beforeEach(() => { globalThis.fetch = vi.fn(async () => json(abc)) as any; });

  it("leads with the blockers and shows the real provider states, never a dash for a missing setting", async () => {
    mount(<ResearchAppendix planId="p1" draft={{ campaign_name: "ABC Search", bidding_strategy: "MAXIMIZE_CLICKS", ad_groups: [] }} />);
    const blockers = await screen.findByRole("list", { name: "Blockers and warnings" });
    expect(within(blockers).getByText(/No business hours are recorded/)).toBeInTheDocument();
    expect(within(blockers).getByText(/account verification required/)).toBeInTheDocument();
    expect(within(blockers).getByText(/Explorer API access/)).toBeInTheDocument();
    expect(within(blockers).getByText(/30 of 30 campaign-eligible keyword\(s\) have no measured search volume/)).toBeInTheDocument();
    const settings = document.getElementById("rp-settings")!;
    expect(within(settings).getByText("Not set — blocking")).toBeInTheDocument();  // the ad schedule
    expect(within(settings).getAllByText(/15 mile radius around/).length).toBeGreaterThan(0);
    expect(within(settings).getByText("English")).toBeInTheDocument();
    // ten sections, Overview open by default, the rest collapsed with their summaries visible
    const sections = document.querySelectorAll("section[aria-labelledby='appendix-title'] > details");
    expect(sections).toHaveLength(10);
    expect((sections[0] as HTMLDetailsElement).open).toBe(true);
    expect([...sections].slice(1).every((d) => !(d as HTMLDetailsElement).open)).toBe(true);
    expect(within(document.getElementById("rp-keywords")!).getByText(/0 of 30 eligible keywords measured · 1 rejected/)).toBeInTheDocument();
  });

  it("keyword table: no invented metric, filters, search and pagination", async () => {
    mount(<ResearchAppendix planId="p1" />);
    const table = await screen.findByRole("table", { name: "Keyword research" });
    expect(within(table).queryByText(/\$\d/)).toBeNull();
    expect(within(table).getAllByText("not measured").length).toBe(25);  // first page of 25
    expect(screen.getByText("Page 1 of 2")).toBeInTheDocument();
    fireEvent.change(screen.getByRole("combobox", { name: "Selection" }), { target: { value: "rejected" } });
    const t2 = screen.getByRole("table", { name: "Keyword research" });
    expect(within(t2).getByText("drain cleaning pittsburgh")).toBeInTheDocument();
    expect(within(t2).getByText(/not one of the client's service areas/)).toBeInTheDocument();
    expect(within(t2).getByText("Irrelevant location")).toBeInTheDocument();
    fireEvent.change(screen.getByRole("combobox", { name: "Selection" }), { target: { value: "" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Search Keyword research" }), { target: { value: "cleaning 7" } });
    expect(within(screen.getByRole("table", { name: "Keyword research" })).getAllByRole("row")).toHaveLength(2);  // header + 1
  });

  it("competitors: clickable counters and tabs; the rejected search engine keeps its reason", async () => {
    mount(<ResearchAppendix planId="p1" />);
    await screen.findByRole("heading", { name: /Competitor Intelligence/ });
    const sec = document.getElementById("rp-competitors")!;
    await within(sec).findByRole("table", { name: "Competitors" });
    fireEvent.click(within(sec).getByRole("button", { name: /Rejected/ }));
    const t = within(sec).getByRole("table", { name: "Competitors" });
    expect(within(t).getByText("google.com", { selector: "a" })).toBeInTheDocument();
    expect(within(t).getByText(/search engine \(google\.com\)/)).toBeInTheDocument();
    expect(within(t).queryByText("Local Drains")).toBeNull();
    fireEvent.click(within(sec).getByRole("tab", { name: /Verified/ }));
    expect(within(within(sec).getByRole("table", { name: "Competitors" })).getByText("Local Drains")).toBeInTheDocument();
    const src = document.getElementById("rp-sources")!;
    fireEvent.click(within(src).getByRole("tab", { name: /Rejected \/ failed/ }));
    expect(within(within(src).getByRole("table", { name: "Research sources" })).getByText("HTTP 503")).toBeInTheDocument();
  });
});

describe("DataTable", () => {
  it("sorts with nulls last and formats compact numbers", () => {
    const rows = [{ k: "a", v: 1200 }, { k: "b", v: null }, { k: "c", v: 15000 }];
    render(<DataTable label="T" rows={rows} cols={[{ key: "k", label: "K" }, { key: "v", label: "V", render: (r: any) => compact(r.v) ?? "—" }]} />);
    fireEvent.click(screen.getByRole("button", { name: "V" }));
    const cells = () => screen.getAllByRole("row").slice(1).map((r) => within(r).getAllByRole("cell")[0].textContent);
    expect(cells()).toEqual(["c", "a", "b"]);
    fireEvent.click(screen.getByRole("button", { name: /V/ }));
    expect(cells()).toEqual(["a", "c", "b"]);
    expect(compact(15000)).toBe("15K");
    expect(compact(null)).toBeNull();
  });
});
