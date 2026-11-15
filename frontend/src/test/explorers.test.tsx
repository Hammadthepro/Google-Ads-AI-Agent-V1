import { fireEvent, render, screen, within } from "@testing-library/react";
import { buildModel, type ResearchEvent } from "../components/research/model";
import { EvidenceButtons } from "../components/research/Explorers";
import { aiRoleActivity } from "../components/campaignBuild/buildModel";

let seq = 0;
const ev = (kind: string, title: string, data: any = {}, stage = "competitors", level: "info" | "warn" | "error" = "info"): ResearchEvent =>
  ({ seq: ++seq, kind, stage, level, title, data, at: "2026-11-14T10:00:00Z" });

const events = [
  ev("competitor_identified", "Rapid Rooter", { url: "https://www.rapidrooter.example/", basis: "supplied by the client" }),
  ev("competitor_identified", "Rapid Rooter Columbus", { url: "https://rapidrooter.example/drains", provider: "serpapi", basis: "ranks for drain cleaning" }),
  ev("competitor_identified", "Pipe Pros", { url: "https://pipepros.example", provider: "geoapify_places", distance_km: 3.2 }),
  ev("url_crawling", "x", { url: "https://www.rapidrooter.example/" }),
  ev("page_extracted", "Rapid Rooter home", { url: "https://www.rapidrooter.example/", page_title: "Rapid Rooter" }),
  ev("serp_query", "Searched", { query: "drain cleaning columbus", engine: "google", location: "Columbus", served_from_cache: true }, "keywords"),
  ev("sponsored_ad", "rivalco.example: Fast drains", { url: "https://rivalco.example/l", query: "drain cleaning columbus" }, "keywords"),
];

describe("research model provenance", () => {
  it("merges the same competitor found twice, keeps both origins' sources, and marks only read websites as verified", () => {
    const m = buildModel(events);
    expect(m.competitors).toHaveLength(2);
    const rr = m.competitors.find((c) => c.name === "Rapid Rooter")!;
    expect(rr.origin).toBe("client");
    expect(rr.providers).toEqual(["client profile", "serpapi"]);
    expect(rr.mentions).toBe(2);
    expect(rr.verified).toBe(true);
    expect(m.competitors.find((c) => c.name === "Pipe Pros")!).toMatchObject({ origin: "discovered", verified: false });
    expect(m.sources.find((s) => s.kind === "Google search")!.cached).toBe(true);
  });
});

describe("explorers", () => {
  it("opens the competitor explorer from the count and filters by origin", () => {
    const m = buildModel(events);
    render(<EvidenceButtons sources={m.sources} competitors={m.competitors} />);
    fireEvent.click(screen.getByRole("button", { name: "2 competitors" }));
    const dlg = screen.getByRole("dialog");
    expect(within(dlg).getByText(/1 provided by the client · 1 discovered by research · 1 with their website read/)).toBeInTheDocument();
    fireEvent.change(within(dlg).getByLabelText("Filter by origin"), { target: { value: "discovered" } });
    const list = within(dlg).getByRole("list", { name: "Competitors" });
    expect(within(list).getByText("Pipe Pros")).toBeInTheDocument();
    expect(within(list).queryByText("Rapid Rooter")).toBeNull();
  });
  it("opens the source explorer and filters by kind", () => {
    const m = buildModel(events);
    render(<EvidenceButtons sources={m.sources} competitors={m.competitors} />);
    fireEvent.click(screen.getByRole("button", { name: `${m.sources.length} sources` }));
    const dlg = screen.getByRole("dialog");
    fireEvent.change(within(dlg).getByLabelText("Filter by kind"), { target: { value: "Public ad" } });
    const list = within(dlg).getByRole("list", { name: "Sources" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(1);
    expect(within(list).getByRole("link", { name: "rivalco.example: Fast drains" })).toHaveAttribute("href", "https://rivalco.example/l");
  });
});

describe("AI role activity", () => {
  it("reports each role from real events and says why the Challenger did not review", () => {
    const a = aiRoleActivity([
      ev("ai_request", "Asking Groq", { role: "main", provider: "internal:Groq", model: "openai/gpt-oss-120b", purpose: "DRAFT_CAMPAIGN" }, "draft"),
      ev("ai_result", "Groq returned a valid draft", { role: "main", provider: "internal:Groq", model: "openai/gpt-oss-120b", usage_in: 3000, usage_out: 1200, latency_ms: 4200 }, "draft"),
      ev("challenger_dispatched", "sent", { provider: "gemini", model: "gemini-flash-lite-latest" }, "draft"),
      ev("ai_request", "Asking Gemini", { role: "challenger", provider: "Google Gemini", model: "gemini-flash-lite-latest" }, "draft"),
      ev("challenger_failed", "Independent review not performed: Gemini timed out", { reason: "Gemini timed out" }, "draft", "warn"),
    ]);
    expect(a.main).toMatchObject({ status: "done", requests: 1, tokensIn: 3000, tokensOut: 1200, latencyMs: 4200, model: "openai/gpt-oss-120b" });
    expect(a.challenger).toMatchObject({ status: "not_performed", text: "Gemini timed out", requests: 1 });
  });
});
