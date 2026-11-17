import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { ChallengerFindings } from "../components/campaignBuild/ChallengerFindings";
import { TaskActions } from "../components/campaignBuild/TaskActions";
import { ResearchAppendix } from "../components/ResearchAppendix";
import { draftScene } from "../components/campaignBuild/draftScene";
import type { ResearchEvent } from "../components/research/model";

vi.mock("../lib/auth", () => ({ useAuth: () => ({ can: () => true }) }));
const mount = (node: React.ReactNode) =>
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MemoryRouter>{node}</MemoryRouter></QueryClientProvider>);
const json = (b: any, status = 200) => new Response(JSON.stringify(b), { status, headers: { "content-type": "application/json" } });

const finding = { index: 0, key: "k1", category: "WEAK_INTENT", severity: "MEDIUM", target: "digital marketing near me within 400m",
  message: "Keyword 'digital marketing near me within 400m' is overly restrictive and may yield zero search volume.",
  evidence: "No volume returned", suggestion: "Remove ultra-granular radius-based keywords like 'digital marketing near me within 400m'.",
  edits: [{ op: "remove_keyword", ad_group: "Digital Marketing", text: "digital marketing near me within 400m", match_type: "PHRASE" }], fix_source: "rules",
  changes: [{ component: "Ad group “Digital Marketing” · keyword", original: "[PHRASE] digital marketing near me within 400m", replacement: "removed" }],
  component: "Ad group “Digital Marketing” · keyword", status: "open", resolution: null };
const report = (over: any = {}) => ({ performed: true, verdict: "REVISE", reviewed_plan_version: 1, plan_id: "p1", plan_version: 1, approval_id: "a1",
  approval_status: "PENDING_APPROVAL", reviewer: { provider: "gemini", model: "gemini-flash-lite-latest" }, findings: [finding], history: [], ...over });

describe("Challenger findings", () => {
  it("shows the concern with its evidence, component, current and proposed value, and applies it as a new version", async () => {
    const posts: any[] = [];
    globalThis.fetch = vi.fn(async (u: any, init?: any) => {
      if (init?.method === "POST") { posts.push([String(u), JSON.parse(init.body)]); return json({ resolution: "applied", findings: report() }); }
      return json(report());
    }) as any;
    mount(<ChallengerFindings taskId="t1" />);
    const item = await screen.findByRole("listitem", { name: /Concern: Keyword 'digital marketing near me within 400m'/ });
    expect(within(item).getByText("MEDIUM")).toBeInTheDocument();
    expect(within(item).getByText(/No volume returned/)).toBeInTheDocument();
    expect(within(item).getByText("[PHRASE] digital marketing near me within 400m")).toBeInTheDocument();
    expect(within(item).getByText("removed")).toBeInTheDocument();
    expect(within(item).getByRole("link", { name: "Edit manually" })).toHaveAttribute("href", "/approvals/a1#edit");
    fireEvent.click(within(item).getByRole("button", { name: "Apply suggestion" }));
    await waitFor(() => expect(posts).toEqual([["/api/v1/tasks/t1/challenger/k1/resolve", { action: "apply" }]]));
  });
  it("asks for confirmation before replacing an approved version, and requires a reason to dismiss", async () => {
    const posts: any[] = [];
    globalThis.fetch = vi.fn(async (_u: any, init?: any) => { if (init?.method === "POST") posts.push(JSON.parse(init.body)); return json(report({ approval_status: "APPROVED" })); }) as any;
    mount(<ChallengerFindings taskId="t1" />);
    fireEvent.click(await screen.findByRole("button", { name: "Apply suggestion" }));
    expect(screen.getByRole("dialog", { name: "Replace the approved version?" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Create new version" }));
    await waitFor(() => expect(posts[0]).toEqual({ action: "apply", confirm_supersede: true }));
    fireEvent.click(screen.getByRole("button", { name: "Dismiss…" }));
    fireEvent.change(screen.getByLabelText("Reason for dismissing"), { target: { value: "Client wants this hyper-local term" } });
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Dismiss" }));
    await waitFor(() => expect(posts[1]).toEqual({ action: "dismiss", reason: "Client wants this hyper-local term" }));
  });
  it("says plainly when the review was not performed", async () => {
    globalThis.fetch = vi.fn(async () => json(report({ performed: false, verdict: null, reason: "The Challenger AI is not available: not configured", findings: [] }))) as any;
    mount(<ChallengerFindings taskId="t1" />);
    expect(await screen.findByText(/Independent review not performed: The Challenger AI is not available/)).toBeInTheDocument();
  });
});

describe("Task actions", () => {
  it("lists exactly what runs again and what is reused, then calls the rerun endpoint", async () => {
    const posts: any[] = [];
    globalThis.fetch = vi.fn(async (u: any, init?: any) => { posts.push([String(u), JSON.parse(init.body)]); return json({ action: "rerun_feasibility" }); }) as any;
    mount(<TaskActions task={{ id: "t1", actions: [{ key: "rerun_feasibility", label: "Re-run feasibility analysis", stages: ["Feasibility", "AI draft", "Validation"],
      reuses: ["Audits", "Keyword research"], supersedes_approved: true }] }} />);
    fireEvent.click(screen.getByRole("button", { name: "Re-run feasibility analysis" }));
    const dlg = screen.getByRole("dialog");
    expect(within(dlg).getByText("Feasibility → AI draft → Validation")).toBeInTheDocument();
    expect(within(dlg).getByText("Audits, Keyword research")).toBeInTheDocument();
    expect(within(dlg).getByText(/current version is approved/)).toBeInTheDocument();
    fireEvent.click(within(dlg).getByRole("button", { name: "Create new version" }));
    await waitFor(() => expect(posts).toEqual([["/api/v1/tasks/t1/rerun", { action: "rerun_feasibility", instructions: "", confirm_supersede: true }]]));
  });
});

describe("Research appendix", () => {
  it("renders persisted evidence and never fills in a missing metric", async () => {
    globalThis.fetch = vi.fn(async () => json({ plan_version: 2, captured_with_version: true, captured_at: "2026-11-15T10:00:00Z",
      sources: [{ provider: "Website crawler", url: "https://napollo.example/seo", title: "SEO services", retrieved_at: "2026-11-15T09:00:00Z", cached: false, extracted: "640 words",
        verification: "retrieved", influence: "Services, landing pages and claims used for the ad groups and ads" },
        { provider: "Google results (SerpApi)", title: "google: “seo agency”", retrieved_at: "2026-11-14T09:00:00Z", cached: true, extracted: "organic results", verification: "cached result", influence: "x" }],
      competitors: [{ name: "Rival Digital", website: "https://rival.example", origin: "discovered", discovery_source: "serpapi", basis: "ranks for seo agency", services: [],
        landing_pages_reviewed: [], website_inspected: false, public_ads: [], findings: [] }],
      keywords: { keyword_planner: { available: false, reason: "Keyword Planner is not configured" }, measured: 0, total: 1, rejected_total: 0, rejected: [], policy: "",
        selected: [{ keyword: "seo agency", match_types: ["EXACT", "PHRASE"], search_volume: null, volume_period: null, avg_cpc: null, avg_cpc_note: "Not returned: Keyword Planner returns top-of-page bid ranges, not average CPC",
          competition: null, competition_index: null, top_of_page_bid_high: null, geo: "Karachi (business service area; no planner targeting for this row)", language: null,
          measurement: "Search suggestion", providers: ["Google Autocomplete"], reason: "relevance and intent (no volume data)", unavailable_reason: "No source returned search volume" }] },
      feasibility: { is_estimate: true, verdict: "TIGHT", expected_weekly_leads: 3, limitations: "Projections, not a guarantee." },
      methodology: { events_recorded: 42, integrations_used: [{ provider: "Website crawler", records: 1 }], searches: ["google: “seo agency”"], cached: ["google: “seo agency”"], unavailable: [], derivation: ["One ad group per selected service."] } })) as any;
    mount(<ResearchAppendix planId="p2" />);
    expect(await screen.findByRole("heading", { name: "Campaign report" })).toBeInTheDocument();
    expect(screen.getByText(/Plan version 2 · captured with this version/)).toBeInTheDocument();
    const src = screen.getByRole("table", { name: "Research sources" });
    expect(within(src).getByRole("link", { name: "SEO services" })).toHaveAttribute("href", "https://napollo.example/seo");
    expect(within(src).getByText("cached")).toBeInTheDocument();
    const comp = screen.getByRole("table", { name: "Competitors" });
    expect(within(comp).getByText("discovered")).toBeInTheDocument();
    expect(within(comp).getByText("website not read")).toBeInTheDocument();
    const kw = screen.getByRole("table", { name: "Keyword research" });
    expect(within(kw).getAllByText("not measured").length).toBeGreaterThan(0);
    expect(within(kw).queryByText(/\$\d/)).toBeNull();
    expect(screen.getAllByText(/Keyword Planner not used: Keyword Planner is not configured/).length).toBeGreaterThan(0);
  });
});

describe("Drafting scene model", () => {
  let seq = 0;
  const ev = (kind: string, title: string, data: any = {}, stage = "draft", level: ResearchEvent["level"] = "info"): ResearchEvent => ({ seq: ++seq, kind, stage, level, title, data, at: "2026-11-15T10:00:00Z" });
  it("advances only on recorded events and reports the Challenger as skipped with its reason", () => {
    const base = [ev("stage_started", "AI draft")];
    expect(draftScene(base).phases.map((p) => p.state)).toEqual(["running", "waiting", "waiting", "waiting", "waiting"]);
    const evs = [...base,
      ev("ai_request", "Asking Groq", { role: "main", purpose: "DRAFT_CAMPAIGN_SPEC", stage: "campaign plan", provider: "internal:Groq", model: "openai/gpt-oss-120b" }),
      ev("ai_result", "ok", { role: "main", purpose: "DRAFT_CAMPAIGN_SPEC", stage: "campaign plan", usage_in: 2100, usage_out: 600 }),
      ev("note", "plan fixed", { ad_groups: ["SEO", "PPC"] }),
      ev("ai_request", "Asking", { role: "main", purpose: "DRAFT_AD_GROUP_CONTENT", stage: "ad group 1" })];
    const mid = draftScene(evs);
    expect(mid.main).toMatchObject({ state: "requesting", tokensIn: 2100, tokensOut: 600, requests: 2, model: "openai/gpt-oss-120b" });
    expect(mid.groups.map((g) => g.state)).toEqual(["writing", "planned"]);
    expect(mid.phases.find((p) => p.key === "structure")!.state).toBe("done");
    const end = draftScene([...evs, ev("plan_stored", "stored", { ad_groups: [{ name: "SEO", keywords: 12, negatives: 3, ads: 1 }, { name: "PPC", keywords: 8, negatives: 2, ads: 1 }] }),
      ev("challenger_failed", "Independent review not performed: The Challenger AI is not available: not configured", { reason: "The Challenger AI is not available: not configured" }, "draft", "warn")]);
    expect(end.groups.map((g) => [g.name, g.keywords, g.state])).toEqual([["SEO", 12, "written"], ["PPC", 8, "written"]]);
    expect(end.challenger).toMatchObject({ state: "skipped", reason: "The Challenger AI is not available: not configured", objections: 0 });
    expect(end.challenger.phases.every((p) => p.state === "skipped")).toBe(true);
  });
});
