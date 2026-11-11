import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { MeasurementPanel } from "../components/MeasurementPanel";

vi.mock("../lib/auth", () => ({ useAuth: () => ({ can: () => true }) }));
const f = (o: any) => ({ key: "k", inspected: "x", result: "ok", actual: "", evidence: "e", severity: null, impact: "", action: "", action_type: "informational", dependency: "", ...o });
function setup(m: any) {
  globalThis.fetch = vi.fn(async () => new Response(JSON.stringify(m))) as any;
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MemoryRouter><MeasurementPanel businessId="b1" /></MemoryRouter></QueryClientProvider>);
}

describe("Conversion measurement panel", () => {
  it("lists findings with evidence, impact and action, and shows no overall status", async () => {
    setup({ evaluated_at: "2026-11-11T10:00:00Z", account_assessed: true, counts: { issue: 1, attention: 0, not_assessable: 1, ok: 1 },
      conversion_bidding: { supported: false, reason: "Conversions stopped being recorded despite clicks." },
      findings: [f({ key: "duplicates", inspected: "The same lead is not counted by two primary actions", result: "issue", severity: "high", actual: "Imported GA4 lead is primary alongside Lead form",
        evidence: "primary actions by type", impact: "Each lead is counted twice.", action: "Set the imported conversion to secondary.", action_type: "needs_approval" }),
        f({ key: "enhanced_conversions", inspected: "Enhanced conversions for leads", result: "not_assessable", actual: "Not read by this audit", action_type: "needs_dependency", dependency: "a CRM with lead outcomes" }),
        f({ key: "conversion_actions", inspected: "Conversion actions exist", actual: "3 enabled conversion action(s)" })] });
    expect(await screen.findByText("The same lead is not counted by two primary actions")).toBeInTheDocument();
    expect(screen.getByText("Each lead is counted twice.")).toBeInTheDocument();
    expect(screen.getByText(/Set the imported conversion to secondary/)).toBeInTheDocument();
    expect(screen.getByText(/Evidence: primary actions by type/)).toBeInTheDocument();
    expect(screen.getByText("Could not be read")).toBeInTheDocument();
    expect(screen.getByText(/Needs a CRM with lead outcomes/)).toBeInTheDocument();
    expect(screen.getByText(/They do not block research, drafting or deployment/)).toBeInTheDocument();
    for (const word of ["GREEN", "YELLOW", "RED", "UNKNOWN", "override", "Override", "gate"]) expect(screen.queryByText(new RegExp(`\\b${word}\\b`))).toBeNull();
  });

  it("explains what the audit reads when none has been run", async () => {
    setup(null);
    expect(await screen.findByText(/Not audited yet/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run audit" })).toBeInTheDocument();
  });
});
