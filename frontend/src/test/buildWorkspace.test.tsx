import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import type { ResearchEvent } from "../components/research/model";
import { buildStages, draftActivity, metric, waitLeft } from "../components/campaignBuild/buildModel";
import { BuildWorkspace } from "../components/campaignBuild/BuildWorkspace";
import { DraftFailure } from "../components/campaignBuild/DraftRetry";

vi.mock("../lib/auth", () => ({ useAuth: () => ({ can: () => true }) }));

let seq = 0;
const ev = (kind: string, title: string, data: Record<string, any> = {}, stage = "", level: "info" | "warn" | "error" = "info", at = new Date().toISOString()): ResearchEvent =>
  ({ seq: ++seq, kind, stage, level, title, data, at });

const STEPS_FAILED = [
  { key: "load", name: "Load account", status: "DONE", summary: "No account" },
  { key: "measurement", name: "Conversion measurement audit", status: "DONE" },
  { key: "audits", name: "Audits (website, GBP, competitors)", status: "DONE" },
  { key: "keywords", name: "Keyword research", status: "DONE" },
  { key: "overlap", name: "Overlap check", status: "DONE" },
  { key: "feasibility", name: "Feasibility", status: "DONE" },
  { key: "intelligence", name: "Intelligence record", status: "DONE" },
  { key: "draft", name: "AI draft", status: "FAILED", summary: "AIOutputInvalid: Neither AI produced a valid draft; Groq (internal AI): too large (HTTP 413)" },
  { name: "error", status: "FAILED", summary: "traceback" },
];

function mount(node: React.ReactNode) {
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MemoryRouter>{node}</MemoryRouter></QueryClientProvider>);
}
const json = (body: any, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

describe("build model", () => {
  it("shows the newest draft attempt and resets the stages after it", () => {
    const steps = [...STEPS_FAILED, { key: "draft", name: "AI draft (retry, attempt 2)", status: "RUNNING" }];
    const st = buildStages("BUILD_CAMPAIGN", steps);
    const draft = st.find((s) => s.key === "draft")!;
    expect(draft.status).toBe("running");
    expect(draft.attempt).toBe(2);
    expect(st.find((s) => s.key === "approval")!.status).toBe("waiting");
    expect(st.map((s) => s.key)).toContain("research");
  });
  it("maps steps recorded before keys existed by their names", () => {
    const st = buildStages("BUILD_CAMPAIGN", [{ name: "Load account", status: "DONE" }, { name: "AI draft", status: "FAILED" }]);
    expect(st.find((s) => s.key === "account")!.status).toBe("done");
    expect(st.find((s) => s.key === "draft")!.status).toBe("failed");
  });
  it("follows provider requests, waits and failures of the latest drafting run only", () => {
    seq = 0;
    const evs = [
      ev("stage_started", "AI draft", {}, "draft"),
      ev("ai_request", "Asking", { purpose: "DRAFT_CAMPAIGN", provider: "internal:GroqAIProvider", model: "gpt-oss", attempt: 1 }, "draft"),
      ev("ai_failed", "old failure", { purpose: "DRAFT_CAMPAIGN", provider: "internal:GroqAIProvider" }, "draft", "error"),
      ev("stage_started", "AI draft (retry, attempt 2)", {}, "draft"),
      ev("ai_request", "Asking", { purpose: "DRAFT_CAMPAIGN", provider: "internal:GroqAIProvider", model: "gpt-oss", attempt: 1 }, "draft"),
      ev("ai_waiting", "rate-limited; waiting 52s", { purpose: "DRAFT_CAMPAIGN", provider: "internal:GroqAIProvider", seconds: 52 }, "draft", "warn", new Date(Date.now() - 10000).toISOString()),
    ];
    const a = draftActivity(evs);
    expect(a.calls).toHaveLength(1);
    expect(a.calls[0].state).toBe("waiting");
    expect(waitLeft(a.calls[0], Date.now())).toBeGreaterThanOrEqual(41);
    expect(waitLeft(a.calls[0], Date.now())).toBeLessThanOrEqual(42);
    expect(a.plan).toBeNull();
  });
  it("is empty when nothing was recorded", () => {
    expect(draftActivity([])).toEqual({ calls: [], plan: null, started: false });
    expect(metric([], "feasibility")).toBeNull();
  });
});

describe("BuildWorkspace", () => {
  afterEach(() => { vi.useRealTimers(); });

  it("renders real feasibility numbers and the stored campaign structure from recorded events", async () => {
    seq = 0;
    const events = [
      ev("audit_started", "Build campaign started", { business: "Northside" }),
      ev("stage_started", "Feasibility", {}, "feasibility"),
      ev("metric_computed", "Feasibility REALISTIC", { metric: "feasibility", status: "REALISTIC", estimate: true, monthly_budget: 3000, daily_budget: 98.68,
        allowed_cost_per_lead: 120, expected_weekly_conversions: 4.2, realistic_weekly_range: [3, 5.5], weekly_target: 3 }, "feasibility"),
      ev("stage_completed", "Feasibility", { summary: "ok" }, "feasibility"),
      ev("stage_started", "AI draft", {}, "draft"),
      ev("ai_result", "ok", { purpose: "DRAFT_CAMPAIGN", provider: "claude:ClaudeAIProvider", model: "claude-x", usage_out: 3200 }, "draft"),
      ev("plan_stored", "Draft stored", { plan_id: "p1", campaign_name: "Northside | Search", bidding_strategy: "MAXIMIZE_CONVERSIONS",
        ad_groups: [{ name: "Drain Cleaning", keywords: 12, negatives: 3, ads: 2 }, { name: "Water Heater Repair", keywords: 9, negatives: 1, ads: 1 }] }, "draft"),
    ];
    globalThis.fetch = vi.fn(async () => json({ events, done: true, last_seq: events.length, status: "AWAITING_APPROVAL" })) as any;
    const steps = STEPS_FAILED.slice(0, 7).concat([{ key: "draft", name: "AI draft", status: "DONE" }]);
    mount(<BuildWorkspace task={{ id: "t1", task_type: "BUILD_CAMPAIGN", steps }} active={false} />);
    expect(await screen.findByText("Northside | Search")).toBeInTheDocument();
    expect(screen.getByText("Water Heater Repair")).toBeInTheDocument();
    expect(screen.getByText("9 keywords · 1 negatives · 1 ad")).toBeInTheDocument();
    expect(screen.getByText(/Returned a valid draft · 3200 output tokens/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Feasibility/ }));
    expect(screen.getByText("$3,000")).toBeInTheDocument();
    expect(screen.getByText("$120")).toBeInTheDocument();
    expect(screen.getByLabelText(/Expected 4.2 leads per week \(range 3 to 5.5\), target 3/)).toBeInTheDocument();
  });

  it("shows a failed drafting stage with its reason and no invented structure", async () => {
    seq = 0;
    const events = [ev("stage_started", "AI draft", {}, "draft"),
      ev("ai_failed", "Groq (internal AI): too large (HTTP 413)", { purpose: "DRAFT_CAMPAIGN", provider: "internal:GroqAIProvider", error_kind: "rate_limited" }, "draft", "error"),
      ev("stage_failed", "AI draft", { summary: "Neither AI produced a valid draft" }, "draft", "error")];
    globalThis.fetch = vi.fn(async () => json({ events, done: true, last_seq: 3, status: "FAILED" })) as any;
    mount(<BuildWorkspace task={{ id: "t2", task_type: "BUILD_CAMPAIGN", steps: STEPS_FAILED }} active={false} />);
    expect(await screen.findByText("No draft was stored: no provider returned a valid campaign.")).toBeInTheDocument();
    expect(screen.getByText(/Neither AI produced a valid draft; Groq/)).toBeInTheDocument();
    expect(screen.getByLabelText("failed")).toBeInTheDocument();
  });

  it("says when the event stream is disconnected and keeps retrying", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let n = 0;
    globalThis.fetch = vi.fn(async () => { n++; return json({ code: "upstream", message: "Bad gateway" }, 502); }) as any;
    mount(<BuildWorkspace task={{ id: "t3", task_type: "BUILD_CAMPAIGN", steps: [{ key: "load", name: "Load account", status: "RUNNING" }] }} active />);
    expect(await screen.findByText("Connection lost · retrying")).toBeInTheDocument();
    const before = n;
    await act(async () => { await vi.advanceTimersByTimeAsync(4100); });
    expect(n).toBeGreaterThan(before);
  });

  it("stops polling when the page is left (no leaked timers)", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const calls: string[] = [];
    globalThis.fetch = vi.fn(async (u: any) => { calls.push(String(u)); return json({ events: [], done: false, last_seq: 0, status: "RUNNING" }); }) as any;
    const r = mount(<BuildWorkspace task={{ id: "t4", task_type: "BUILD_CAMPAIGN", steps: [] }} active />);
    await waitFor(() => expect(calls.length).toBe(1));
    expect(calls[0]).toMatch(/\/tasks\/t4\/events\?after=0$/);
    r.unmount();
    await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
    expect(calls.length).toBe(1);
  });

  it("falls back to the flat research scene without WebGL", async () => {
    seq = 0;
    const events = [ev("stage_started", "Website & landing pages", {}, "website"),
      ev("page_extracted", "Home", { url: "https://northside.example/", page_title: "Home", words: 300 }, "website")];
    globalThis.fetch = vi.fn(async () => json({ events, done: true, last_seq: 2 })) as any;
    const { container } = mount(<BuildWorkspace task={{ id: "t5", task_type: "BUILD_CAMPAIGN", steps: [{ key: "audits", name: "Audits (website, GBP, competitors)", status: "DONE" }] }} active={false} />);
    await waitFor(() => expect(container.querySelector("svg")).not.toBeNull());
    expect(container.querySelector("canvas")).toBeNull();
    expect(screen.getByRole("button", { name: "Flat" })).toHaveAttribute("aria-pressed", "true");
  });
});

describe("DraftFailure", () => {
  const task = { id: "t9", status: "FAILED", error: "Neither AI…", draft_retry: { available: true },
    draft_attempts: [{ attempt: 1, kind: "initial", status: "failed", finished_at: new Date().toISOString(),
      calls: [{ provider: "internal:GroqAIProvider", model: "openai/gpt-oss-120b", attempt: 1, ok: false, error: "rate_limited: Groq rejected the request as too large (HTTP 413)" }] }] };

  it("names each provider's error and retries once per click", async () => {
    const posts: string[] = [];
    let resolve: (r: Response) => void = () => {};
    globalThis.fetch = vi.fn((u: any, init?: any) => {
      if (init?.method === "POST") { posts.push(String(u)); return new Promise<Response>((r) => { resolve = r; }); }
      return Promise.resolve(json(task));
    }) as any;
    mount(<DraftFailure task={task} />);
    expect(screen.getByText("Groq (internal AI)")).toBeInTheDocument();
    expect(screen.getByText("Groq rejected the request as too large (HTTP 413)")).toBeInTheDocument();
    expect(screen.getByText("Attempt 1")).toBeInTheDocument();
    const btn = screen.getByRole("button", { name: "Retry draft" });
    fireEvent.click(btn);
    await waitFor(() => expect(screen.getByRole("button", { name: "Retrying…" })).toBeDisabled());
    fireEvent.click(screen.getByRole("button", { name: "Retrying…" }));
    expect(posts).toEqual(["/api/v1/tasks/t9/retry-draft"]);
    resolve(json({ ...task, status: "RETRYING_DRAFT" }));
  });

  it("explains why a retry is not possible instead of offering it", () => {
    globalThis.fetch = vi.fn(async () => json({})) as any;
    mount(<DraftFailure task={{ ...task, draft_retry: { available: false, reason: "The keyword research stored for this task is missing. Run the build again." } }} />);
    expect(screen.queryByRole("button", { name: "Retry draft" })).toBeNull();
    expect(screen.getByText(/keyword research stored for this task is missing/)).toBeInTheDocument();
  });

  it("shows a running retry as busy", () => {
    mount(<DraftFailure task={{ ...task, status: "RETRYING_DRAFT", draft_retry: { available: false, running: true } }} />);
    expect(screen.getByRole("button", { name: "Retrying…" })).toBeDisabled();
    expect(screen.getByText("Retrying the AI draft")).toBeInTheDocument();
  });
});
