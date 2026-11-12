import { render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { buildModel, buildStream, describe as describeEvent, type ResearchEvent } from "../components/research/model";
import { LiveFindings } from "../components/research/LiveFindings";
import { WriteReadiness } from "../components/WriteReadiness";

let seq = 0;
const ev = (kind: string, title: string, data: Record<string, any> = {}, stage = "", level: "info" | "warn" | "error" = "info"): ResearchEvent =>
  ({ seq: ++seq, kind, stage, level, title, data, at: new Date().toISOString() });

const EVENTS = (() => {
  seq = 0;
  return [
    ev("audit_started", "Research started for Northside", { business: "Northside Plumbing", website: "https://northside.example/" }),
    ev("stage_started", "Website & landing pages", {}, "website"),
    ev("url_crawling", "https://northside.example/", { url: "https://northside.example/" }, "website"),
    ev("page_extracted", "Northside Plumbing", { url: "https://northside.example/", page_title: "Northside Plumbing", words: 640, h1: "Plumbers in Austin" }, "website"),
    ev("url_crawling", "https://northside.example/drains", { url: "https://northside.example/drains" }, "website"),
    ev("page_failed", "https://northside.example/old", { url: "https://northside.example/old", status: 404 }, "website", "warn"),
    ev("competitor_identified", "Rival Plumbing", { url: "https://rival.example", basis: "supplied by the client" }, "competitors"),
    ev("location_identified", "Austin, Texas", { lat: 30.26, lon: -97.74, role: "business", provider: "openstreetmap_nominatim" }, "geo"),
    ev("competitor_identified", "Acme Pipes", { lat: 30.28, lon: -97.75, distance_km: 2.1, provider: "openstreetmap_overpass", basis: "map listing in the same category" }, "geo"),
    ev("keyword_discovery", "3 suggestion(s) for “drain cleaning”", { seed: "drain cleaning", provider: "google", count: 3, examples: ["drain cleaning near me"] }, "keywords"),
    ev("provider_unavailable", "Keyword Planner is not available: search volume and bids will be reported as unavailable", { provider: "keyword_planner" }, "keywords", "warn"),
    ev("research_stored", "41 keyword(s) stored, 18 selected in 3 group(s)", { count: 41 }, "keywords"),
  ];
})();

describe("research event model", () => {
  it("builds every node from real events only", () => {
    const m = buildModel(EVENTS);
    expect(m.domain).toBe("northside.example");
    expect(m.pages.map((p) => [p.path, p.state])).toEqual([["/", "extracted"], ["/drains", "fetching"], ["/old", "failed"]]);
    expect(m.competitors.map((c) => c.name)).toEqual(["Rival Plumbing", "Acme Pipes"]);
    expect(m.warnings).toHaveLength(2);
    expect(m.sources.map((s) => s.url)).toEqual(["https://northside.example/"]);
  });
  it("is empty when nothing happened: no invented progress", () => {
    const m = buildModel([]);
    expect(m.pages).toEqual([]); expect(m.competitors).toEqual([]); expect(m.started).toBe(false);
  });
  it("describes events in plain language", () => {
    expect(describeEvent(EVENTS[5])).toBe("Could not open https://northside.example/old (HTTP 404)");
  });
});

function mount(node: React.ReactNode) {
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MemoryRouter>{node}</MemoryRouter></QueryClientProvider>);
}

describe("buildStream", () => {
  it("shows one line per page that moves from reading to read, and keeps failures in place", () => {
    const st = buildStream(EVENTS);
    const web = st.find((s) => s.stage === "website")!;
    expect(web.items.map((i) => [i.text, i.state])).toEqual([
      ["Read “Northside Plumbing”", "done"], ["Reading northside.example/drains", "working"], ["Could not open /old (HTTP 404)", "failed"]]);
    expect(st.map((s) => s.stage)).toEqual(["website", "competitors", "geo", "keywords"]);
    expect(st.find((s) => s.stage === "keywords")!.items.find((i) => i.type === "finding" && i.chips?.length)!.chips).toEqual(["drain cleaning near me"]);
  });
});

describe("LiveFindings", () => {
  const steps = [{ key: "website", name: "2 · Website & landing pages", status: "RUNNING" }, { key: "keywords", name: "5 · Keyword research", status: "PENDING" }];
  it("shows real activity, findings, sources and failures as readable text, with no percentage and no graphics", async () => {
    globalThis.fetch = vi.fn(async () => new Response(JSON.stringify({ events: EVENTS, steps, done: false, last_seq: EVENTS.length }))) as any;
    const { container } = mount(<LiveFindings url="/audit-reports/r1/events" active />);
    expect(await screen.findByText("Reading northside.example/drains")).toBeInTheDocument();
    expect(screen.getByText("Read “Northside Plumbing”")).toBeInTheDocument();
    expect(screen.getAllByText("Rival Plumbing").length).toBeGreaterThan(0);
    expect(screen.getByText("Could not open /old (HTTP 404)")).toBeInTheDocument();
    expect(screen.getAllByText(/Keyword Planner is not available/).length).toBeGreaterThan(0);
    expect(screen.getByRole("status")).toHaveTextContent(/last update/);
    const sources = screen.getByText("Sources").parentElement!;
    expect(within(sources).getByRole("link", { name: "Northside Plumbing" })).toHaveAttribute("href", "https://northside.example/");
    expect(container.querySelector("svg")).toBeNull();          // no decorative scene
    expect(container.textContent).not.toMatch(/\d+\s?%/);       // no invented progress
  });
  it("asks only for new events after the first load", async () => {
    const calls: string[] = [];
    let n = 0;
    globalThis.fetch = vi.fn(async (url: any) => { calls.push(String(url)); n++;
      return new Response(JSON.stringify(n === 1 ? { events: EVENTS.slice(0, 3), steps, done: false, last_seq: 3 } : { events: [], steps, done: true, last_seq: 3 })); }) as any;
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mount(<LiveFindings url="/audits/a2/events" active />);
    await waitFor(() => expect(calls.length).toBeGreaterThan(0));
    await vi.advanceTimersByTimeAsync(1600);
    await waitFor(() => expect(calls.some((c) => c.endsWith("/audits/a2/events?after=3"))).toBe(true));
    expect(calls[0]).toMatch(/after=0$/);
    vi.useRealTimers();
  });
  it("says it is waiting when nothing has been recorded, and never pretends to work when nothing runs", async () => {
    globalThis.fetch = vi.fn(async () => new Response(JSON.stringify({ events: [], steps, done: false, last_seq: 0 }))) as any;
    const r1 = mount(<LiveFindings url="/audits/a3/events" active />);
    expect(await screen.findByText(/Waiting for the research worker to record its first step/)).toBeInTheDocument();
    r1.unmount();
    globalThis.fetch = vi.fn(async () => new Response(JSON.stringify({ events: [], status: "FAILED", done: true, last_seq: 0 }))) as any;
    mount(<LiveFindings url="/audits/a4/events" active={false} />);
    expect(await screen.findByText("No activity was recorded for this run.")).toBeInTheDocument();
  });
});

describe("WriteReadiness", () => {
  it("lists every key with its owner and next action, and says writes are off", async () => {
    globalThis.fetch = vi.fn(async () => new Response(JSON.stringify({
      ready: false, summary: "Real Google Ads writes are off: 2 of 6 keys missing (Deployment permit, Super Admin authorization).", missing: [],
      checks: [{ key: "deployment_permit", label: "Deployment permit (server environment)", status: "missing", detail: "GOOGLE_ADS_WRITES_ENABLED is false.", action: "Set it and restart.", who: "Operator" },
               { key: "google_access", label: "Google Ads API access for campaign management", status: "unverified", detail: "Google decides.", action: "Check the API Center.", who: "Google" }],
      per_change: ["Campaigns are created paused."] }))) as any;
    mount(<WriteReadiness />);
    expect(await screen.findByText(/Real Google Ads writes are off/)).toBeInTheDocument();
    expect(screen.getByText("Writes off")).toBeInTheDocument();
    expect(screen.getByLabelText("Unverified")).toBeInTheDocument();
    expect(screen.getByText("Next: Set it and restart.")).toBeInTheDocument();
    expect(screen.getByText("Campaigns are created paused.")).toBeInTheDocument();
  });
});
