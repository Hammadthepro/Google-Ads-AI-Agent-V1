import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { AuditDetail, Audits } from "../pages/Audits";

const base = { id: "a1", client_id: "c1", business_id: "b1", task_id: null, audit_type: "WEBSITE", summary: "", error: "", result: {}, source_type: "NONE",
  started_at: "2026-11-12T10:00:00Z", finished_at: null, business: { id: "b1", name: "Northside Plumbing", website_url: "https://northside.example" }, retryable: false };
const calls: { url: string; method: string; body?: any }[] = [];

function mountDetail(audit: any) {
  calls.length = 0;
  globalThis.fetch = vi.fn(async (url: any, init: any = {}) => {
    const u = String(url); calls.push({ url: u, method: init.method ?? "GET", body: init.body && JSON.parse(init.body) });
    if (u.includes("/events")) return new Response(JSON.stringify({ events: [], status: audit.status, done: !["QUEUED", "RUNNING"].includes(audit.status), last_seq: 0 }));
    if (u.endsWith("/audits") && init.method === "POST") return new Response(JSON.stringify([{ ...base, id: "a2", status: "QUEUED" }]));
    return new Response(JSON.stringify(audit));
  }) as any;
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <MemoryRouter initialEntries={["/audits/a1"]}><Routes><Route path="/audits/:id" element={<AuditDetail />} /></Routes></MemoryRouter></QueryClientProvider>);
}

describe("AuditDetail in every state", () => {
  it("queued: explains the state and shows the live view instead of a blank page", async () => {
    mountDetail({ ...base, status: "QUEUED" });
    expect(await screen.findByText(/Queued — waiting for the worker/)).toBeInTheDocument();
    expect(screen.getByText("Northside Plumbing")).toBeInTheDocument();
    expect(screen.getByText("Data pending")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Live findings" })).toBeInTheDocument();
  });
  it("running with an empty result does not crash", async () => {
    mountDetail({ ...base, status: "RUNNING" });
    expect(await screen.findByText(/Running — findings appear below/)).toBeInTheDocument();
  });
  it("completed: shows the findings", async () => {
    mountDetail({ ...base, status: "COMPLETED", finished_at: "2026-11-12T10:01:00Z", source_type: "LIVE", summary: "3 pages crawled",
      result: { page_inventory: [{ url: "https://northside.example/", title: "Home", words: 500, forms: 1 }], seo_issues: [], pagespeed: {}, tag_note: "", google_tags_detected: [], gtm_detected: [] } });
    expect(await screen.findByText("Page inventory")).toBeInTheDocument();
    expect(screen.getByText("https://northside.example/")).toBeInTheDocument();
  });
  it("completed with an unexpected result shape still renders the page", async () => {
    mountDetail({ ...base, status: "COMPLETED", finished_at: "2026-11-12T10:01:00Z", result: { something_else: true } });
    expect(await screen.findByText("Website audit")).toBeInTheDocument();
    expect(screen.getByText("Raw result")).toBeInTheDocument();
  });
  it("failed: shows the real error and can be retried", async () => {
    mountDetail({ ...base, status: "FAILED", finished_at: "2026-11-12T10:01:00Z", error: "No page of the website could be read.", retryable: true });
    expect(await screen.findByText("No page of the website could be read.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry this audit" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.body?.audit_type === "WEBSITE" && c.body?.background === true)).toBe(true));
  });
  it("an audit that cannot be loaded shows the error, not a blank page", async () => {
    globalThis.fetch = vi.fn(async () => new Response(JSON.stringify({ error: { code: "not_found", message: "Audit not found" } }), { status: 404 })) as any;
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={["/audits/zz"]}><Routes><Route path="/audits/:id" element={<AuditDetail />} /></Routes></MemoryRouter></QueryClientProvider>);
    expect(await screen.findByText(/Audit not found/)).toBeInTheDocument();
  });
});

describe("keyword research asks before running anything", () => {
  const SOURCES = { all_usable: false, sources: [
    { type: "WEBSITE", label: "Website audit", state: "usable", reason: "5 page(s) read", audit_id: "w1", finished_at: "2026-11-10T10:00:00Z" },
    { type: "GBP", label: "Business presence (GBP) audit", state: "missing", reason: "Never run for this business", audit_id: null },
    { type: "COMPETITOR", label: "Competitor research", state: "outdated", reason: "2 competitor(s) analysed; 40 days old (limit 30)", audit_id: "c1" }] };
  function mountList() {
    calls.length = 0;
    globalThis.fetch = vi.fn(async (url: any, init: any = {}) => {
      const u = String(url); calls.push({ url: u, method: init.method ?? "GET", body: init.body && JSON.parse(init.body) });
      if (u.includes("/auth/me")) return new Response(JSON.stringify({ user: { id: "u", email: "a@x", name: "A", role: "ADMIN", permissions: ["audits.run"] } }));
      if (u.includes("/businesses/b1/keyword-research/sources")) return new Response(JSON.stringify(SOURCES));
      if (u.endsWith("/businesses")) return new Response(JSON.stringify([{ id: "b1", name: "Northside Plumbing", client_name: "Northside" }]));
      if (u.endsWith("/audits") && init.method === "POST") return new Response(JSON.stringify([{ ...base, id: "k1", audit_type: "KEYWORD", status: "QUEUED" }]));
      if (u.endsWith("/audits")) return new Response(JSON.stringify([]));
      return new Response("{}");
    }) as any;
    return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MemoryRouter>
      <AuthShim><Audits /></AuthShim></MemoryRouter></QueryClientProvider>);
  }
  it("shows what exists and starts only keyword research when reusing", async () => {
    mountList();
    fireEvent.change(await screen.findByLabelText("Business"), { target: { value: "b1" } });
    fireEvent.change(screen.getByLabelText("Audit type"), { target: { value: "KEYWORD" } });
    fireEvent.click(screen.getByRole("button", { name: "Choose sources…" }));
    expect(await screen.findByText("Never run for this business")).toBeInTheDocument();
    expect(calls.some((c) => c.method === "POST")).toBe(false);  // nothing started yet
    fireEvent.click(screen.getByRole("button", { name: "Start keyword research" }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST")?.body).toEqual({ business_id: "b1", audit_type: "KEYWORD", keyword_mode: "reuse", refresh: [], confirm: false, background: true }));
  });
  it("runs only the ticked sources, with confirmation, when refreshing", async () => {
    mountList();
    fireEvent.change(await screen.findByLabelText("Business"), { target: { value: "b1" } });
    fireEvent.change(screen.getByLabelText("Audit type"), { target: { value: "KEYWORD" } });
    fireEvent.click(screen.getByRole("button", { name: "Choose sources…" }));
    await screen.findByText("Never run for this business");
    fireEvent.click(screen.getByLabelText(/Run fresh audits first/));
    fireEvent.click(screen.getByRole("checkbox", { name: "Competitor research" }));  // keep only the missing business-presence audit
    fireEvent.click(screen.getByRole("button", { name: "Run 1 audit(s), then keyword research" }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST")?.body).toEqual({ business_id: "b1", audit_type: "KEYWORD", keyword_mode: "refresh", refresh: ["GBP"], confirm: true, background: true }));
  });
});

import { AuthProvider } from "../lib/auth";
function AuthShim({ children }: { children: React.ReactNode }) { return <AuthProvider>{children}</AuthProvider>; }
