import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { IntelligencePanel } from "../components/IntelligencePanel";

const finding = (o: any) => ({ finding_id: "FND-01-0001", category: "usp", subject: "s", finding: "3 selling points", result: "WARN", severity: "medium", origin: "client_provided",
  verification: "client_provided", source_type: "client_intake", source_reference: "Client intake form (CL-000001)", retrieved_at: "2026-11-11T10:00:00Z", confidence: 0.7,
  recommendation: "Collect more", limitations: [], ...o });
const MODS = [
  { module: "business_intelligence", number: 1, title: "Business intelligence", status: "COMPLETED_WITH_WARNINGS", stale: false, version: 2, summary: "", checks_total: 21, checks_evaluated: 18,
    findings: [finding({}), finding({ finding_id: "FND-01-0002", category: "profitability", finding: "Not assessed.", result: "UNAVAILABLE", source_type: "none", verification: "unknown", confidence: 0, recommendation: "" })],
    client_actions: [{ ask: "Ask which services earn the most" }], depends_on: [] },
  { module: "account_policy", number: 9, title: "Google Ads account and policy", status: "WAITING_FOR_INTEGRATION", stale: true, version: 1, summary: "No Google Ads account linked.", checks_total: 22, checks_evaluated: 0, findings: [], depends_on: [] },
];
const REPORT = { modules: MODS, stale_modules: ["account_policy"], decision: "ready_with_conditions", decision_label: "Ready with conditions", decision_reason: "Ready with conditions. No account.",
  account_linked: false, totals: { checks_specified: 202, checks_evaluated: 18 }, legend: { note: "Confidence is categorical." } };
const calls: { url: string; method: string; body?: any }[] = [];

function setup(canRun = true) {
  calls.length = 0;
  globalThis.fetch = vi.fn(async (url: any, init: any = {}) => {
    calls.push({ url: String(url), method: init.method ?? "GET", body: init.body ? JSON.parse(init.body) : undefined });
    return new Response(JSON.stringify(REPORT));
  }) as any;
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><IntelligencePanel clientId="c1" canRun={canRun} /></QueryClientProvider>);
}

describe("IntelligencePanel", () => {
  it("shows the readiness decision, the unlinked-account note and every module with its status", async () => {
    setup();
    expect(await screen.findByText("Ready with conditions", { selector: "span" })).toBeInTheDocument();
    expect(screen.getByText(/No Google Ads account is linked/)).toBeInTheDocument();
    expect(screen.getByText("Business intelligence")).toBeInTheDocument();
    expect(screen.getByText("Google Ads account and policy")).toBeInTheDocument();
    expect(screen.getByText("18/21 checks assessed · v2")).toBeInTheDocument();
    expect(screen.getByText("Waiting for integration")).toBeInTheDocument();
    expect(screen.getByText("Out of date")).toBeInTheDocument();
  });

  it("opens a module to show provenance on each finding and hides not-assessed placeholders until asked", async () => {
    setup();
    fireEvent.click(await screen.findByRole("button", { name: /Business intelligence/ }));
    expect(screen.getByText("3 selling points")).toBeInTheDocument();
    expect(screen.getByText(/Source: Client intake form/)).toBeInTheDocument();
    expect(screen.getByText(/confidence 70%/)).toBeInTheDocument();
    expect(screen.getByText("Needed from the client:")).toBeInTheDocument();
    expect(screen.queryByText("Not assessed.")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Show 1 not assessed/ }));
    expect(screen.getByText("Not assessed.")).toBeInTheDocument();
  });

  it("re-runs a single stage and brings everything up to date through the same endpoint", async () => {
    setup();
    fireEvent.click(await screen.findByRole("button", { name: /Business intelligence/ }));
    fireEvent.click(screen.getByRole("button", { name: "Re-run this stage" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.url.includes("/clients/c1/intelligence/refresh") && c.body.modules?.[0] === "business_intelligence")).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: /Bring up to date \(1 stage\)/ }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.body?.research === true)).toBe(true));
  });

  it("offers no run buttons without the permission", async () => {
    setup(false);
    await screen.findByText("Business intelligence");
    expect(screen.queryByRole("button", { name: /Bring up to date/ })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Business intelligence/ }));
    expect(within(document.body).queryByRole("button", { name: "Re-run this stage" })).toBeNull();
  });
});
