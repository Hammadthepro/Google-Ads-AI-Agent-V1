import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { TrackingPanel } from "../pages/Businesses";

vi.mock("../lib/auth", () => ({ useAuth: () => ({ can: () => true }) }));

const base = { id: "g1", business_id: "b1", override_active: false, checks: [], fix_list: [], outstanding: [], evaluated_at: "2026-11-11T10:00:00Z", source_type: "NONE", stale: false, stale_reason: "" };
function setup(gate: any) {
  globalThis.fetch = vi.fn(async () => new Response(JSON.stringify(gate))) as any;
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MemoryRouter><TrackingPanel businessId="b1" /></MemoryRouter></QueryClientProvider>);
}

describe("Conversion tracking gate panel", () => {
  it("shows UNKNOWN as a non-failure that does not stop drafting, with what is still to verify", async () => {
    setup({ ...base, state: "UNKNOWN", effective_state: "UNKNOWN", outstanding: [{ check: "account_linked", what: "Link the client's Google Ads account before deployment to verify conversion tracking." }],
      checks: [{ key: "account_linked", label: "Google Ads account linked", status: "UNKNOWN", critical: false, evidence: "No account linked: conversions cannot be verified in Google Ads yet" }] });
    expect(await screen.findByText(/This is not a failure and does not stop research, strategy or drafting/)).toBeInTheDocument();
    expect(screen.getByText("Still to verify before deployment")).toBeInTheDocument();
    expect(screen.getByText(/Link the client's Google Ads account before deployment/)).toBeInTheDocument();
    expect(screen.queryByText("Fix list")).toBeNull();           // no client work is demanded for missing evidence
    expect(screen.queryByText(/confirms tracking is broken/)).toBeNull();
  });

  it("says a stale assessment must be run again instead of showing its old verdict", async () => {
    setup({ ...base, state: "UNKNOWN", effective_state: "UNKNOWN", stale: true, stale_reason: "The Google Ads account link changed since tracking was assessed: reassess", previous_state: "RED" });
    expect(await screen.findByText(/Tracking must be assessed again: The Google Ads account link changed/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Admin override" })).toBeNull();
  });

  it("explains YELLOW and confirmed RED separately, and only RED shows a fix list", async () => {
    setup({ ...base, state: "YELLOW", effective_state: "YELLOW", outstanding: [{ check: "recorded_conversions", what: "Submit a test lead and confirm it appears in Google Ads." }] });
    expect(await screen.findByText(/live deployment needs verified tracking \(GREEN\) or an explicit admin authorisation/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Admin override" })).toBeInTheDocument();
  });

  it("shows RED only with its confirmed fix list", async () => {
    setup({ ...base, state: "RED", effective_state: "RED", fix_list: [{ check: "form_conversion", fix: "Create a primary lead conversion action", owner: "web team" }] });
    expect(await screen.findByText(/Evidence confirms tracking is broken or misconfigured/)).toBeInTheDocument();
    expect(screen.getByText("Fix list")).toBeInTheDocument();
  });
});
