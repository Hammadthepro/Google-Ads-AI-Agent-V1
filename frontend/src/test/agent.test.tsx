import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { Agent } from "../pages/Agent";

vi.mock("../lib/auth", () => ({ useAuth: () => ({ can: (p: string) => p !== "never" }) }));

const item = { finding_id: "FND-01-0001", check: "usp", text: "3 selling points", result: "WARN", severity: "medium", verification: "client_provided", origin: "client_provided",
  confidence: 0.7, source: "Client intake form (CL-000001)", retrieved_at: "2026-11-11T10:00:00Z", recommendation: "Collect more", limitations: [] };
const PLAN = [{ key: "research", label: "External research", action: "reuse", reason: "Research from 2026-11-10 (v1) still matches the client's information", status: "DONE", result: "" },
  { key: "modules", label: "Update the intelligence record", action: "run", reason: "Website: inputs changed", status: "DONE", result: "Updated 1 stage(s)" }];
const RESULT = { headline: "Audit of Acme: ran 1 stage(s), reused 1. Readiness: Ready with conditions.", decision: "ready_with_conditions", decision_label: "Ready with conditions",
  blockers: [{ what: "Keyword Planner is not configured", needs: "Missing: research_customer_id" }], warnings: ["No Google Ads account is linked"], reused: [{ stage: "x", why: "Research reused" }],
  sections: [{ type: "findings", title: "1. Business intelligence", status: "COMPLETED_WITH_WARNINGS", stale: false, assessed: "18/21", items: [item] }],
  next_actions: [{ label: "Research keywords", say: "Find profitable keywords for these services" }], legend: "Confidence is categorical." };
const base = (extra: any = {}) => ({ id: "c1", client_id: "k1", title: "Acme", busy: false, pending: null, intents: [], context: {}, ...extra });
const calls: { url: string; method: string; body?: any }[] = [];
let thread: any;

function setup(t: any) {
  thread = t; calls.length = 0;
  globalThis.fetch = vi.fn(async (url: any, init: any = {}) => {
    const u = String(url), method = init.method ?? "GET";
    calls.push({ url: u, method, body: init.body ? JSON.parse(init.body) : undefined });
    if (u.endsWith("/clients")) return new Response(JSON.stringify([{ id: "k1", name: "Acme" }]));
    if (u.includes("/agent/conversations") && method === "POST" && !u.includes("/messages")) return new Response(JSON.stringify(thread));
    if (u.includes("/messages") && method === "POST") return new Response(JSON.stringify(thread));
    return new Response(JSON.stringify(thread));
  }) as any;
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MemoryRouter initialEntries={["/agent?client=k1"]}><Agent /></MemoryRouter></QueryClientProvider>);
}

describe("Agent workspace", () => {
  it("shows the plan with RUN / REUSE decisions and reasons, then the evidence, blockers and warnings", async () => {
    setup(base({ messages: [
      { id: "m1", role: "user", kind: "text", content: "Audit this client", data: {}, run_id: null, created_at: "2026-11-11T10:00:00Z" },
      { id: "m2", role: "agent", kind: "plan", content: "I read this as “Audit”. Plan: x", data: { plan: PLAN }, run_id: "r1", created_at: "2026-11-11T10:00:01Z" },
      { id: "m3", role: "agent", kind: "result", content: RESULT.headline, data: RESULT, run_id: "r1", created_at: "2026-11-11T10:00:02Z" }],
      runs: { r1: { id: "r1", status: "COMPLETED_WITH_BLOCKERS", plan: PLAN, intent_label: "Audit", resumable: false, result: RESULT } } }));
    expect(await screen.findByText("REUSE")).toBeInTheDocument();
    expect(screen.getByText("RUN")).toBeInTheDocument();
    expect(screen.getByText(/still matches the client's information/)).toBeInTheDocument();
    expect(screen.getByText("Blockers")).toBeInTheDocument();
    expect(screen.getByText(/Missing: research_customer_id/)).toBeInTheDocument();
    expect(screen.getByText("Warnings")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /1\. Business intelligence/ }));
    expect(screen.getByText("3 selling points")).toBeInTheDocument();
    expect(screen.getByText(/Source: Client intake form/)).toBeInTheDocument();
    expect(screen.getByText(/confidence 70%/)).toBeInTheDocument();
  });

  it("sends what you type, and next-action buttons send their own request", async () => {
    setup(base({ messages: [{ id: "m3", role: "agent", kind: "result", content: RESULT.headline, data: RESULT, run_id: "r1", created_at: "2026-11-11T10:00:02Z" }], runs: { r1: { id: "r1", status: "COMPLETED", plan: [], intent_label: "Audit", resumable: false } } }));
    fireEvent.click(await screen.findByRole("button", { name: "Research keywords" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.url.includes("/messages") && c.body.text === "Find profitable keywords for these services")).toBe(true));
    fireEvent.change(screen.getByLabelText("Ask the agent"), { target: { value: "What is out of date?" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(calls.some((c) => c.body?.text === "What is out of date?")).toBe(true));
  });

  it("asks clarifying questions as buttons and sends the chosen option", async () => {
    setup(base({ pending: { kind: "clarify", type: "business" }, messages: [{ id: "m1", role: "agent", kind: "clarification", content: "Which business?", data: { type: "business", options: [{ label: "Acme North", value: "b1" }, { label: "Acme South", value: "b2" }] }, run_id: null, created_at: "2026-11-11T10:00:00Z" }], runs: {} }));
    fireEvent.click(await screen.findByRole("button", { name: "Acme South" }));
    await waitFor(() => expect(calls.some((c) => c.body?.choice?.value === "b2")).toBe(true));
  });

  it("requires an explicit confirmation before anything is created", async () => {
    const conf = { ...RESULT, confirmation: { describes: "This will create a Build campaign task. Nothing is created in Google Ads.", readiness: "Ready with conditions" } };
    setup(base({ pending: { kind: "confirm", run_id: "r1" }, messages: [{ id: "m3", role: "agent", kind: "confirmation", content: conf.headline, data: conf, run_id: "r1", created_at: "2026-11-11T10:00:02Z" }], runs: { r1: { id: "r1", status: "WAITING_FOR_CONFIRMATION", plan: [], intent_label: "Draft", resumable: false } } }));
    expect(await screen.findByText(/Nothing is created in Google Ads/)).toBeInTheDocument();
    expect(calls.some((c) => c.url.includes("/confirm"))).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(calls.some((c) => c.url.includes("/runs/r1/confirm") && c.body.approve === false)).toBe(true));
  });

  it("offers to resume a failed run", async () => {
    setup(base({ messages: [{ id: "m1", role: "agent", kind: "error", content: "I could not finish: worker died.", data: {}, run_id: "r1", created_at: "2026-11-11T10:00:00Z" }], runs: { r1: { id: "r1", status: "FAILED", plan: [], intent_label: "Audit", resumable: true } } }));
    fireEvent.click(await screen.findByRole("button", { name: /Resume from where it stopped/ }));
    await waitFor(() => expect(calls.some((c) => c.url.includes("/runs/r1/resume"))).toBe(true));
  });
});
