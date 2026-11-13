import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { AIRoles } from "../components/AIRoles";

const json = (body: any, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const providers = [
  { key: "openai", label: "OpenAI", key_hint: "sk-…", api_features: ["JSON mode"], console: "https://platform.openai.com/api-keys", discovery: true },
  { key: "groq", label: "Groq", key_hint: "gsk_…", api_features: ["JSON mode"], console: "", discovery: true },
];
const role = (r: string, over: any = {}) => ({
  role: r, provider: "groq", provider_label: "Groq", model: "openai/gpt-oss-120b", enabled: true, status: "CONNECTED", source: "migrated_from_groq",
  key: { present: true, source: "groq_integration", fingerprint: "", unreadable: false }, model_meta: {}, models_error: "",
  last_test: { at: null, ok: null, steps: [], error: "", error_kind: "" }, pricing: {}, updated_at: "2026-11-13T10:00:00Z",
  ...(r === "challenger" ? { review_policy: { campaign_draft: true, ad_copy: true, performance_diagnosis: false },
    reviews: { campaign_draft: { label: "Campaign and ad group drafts", detail: "d" }, ad_copy: { label: "New and edited ads", detail: "d" },
      performance_diagnosis: { label: "Weekly performance diagnosis", detail: "d" } } } : {}),
  ...over,
});
const overview = (main: any, chal: any, note = "") => ({ roles: { main, challenger: chal }, providers, independence: { note }, migrated_now: [] });

function mount(canManage = true) {
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MemoryRouter><AIRoles canManage={canManage} /></MemoryRouter></QueryClientProvider>);
}

describe("AI roles", () => {
  afterEach(() => { localStorage.clear(); sessionStorage.clear(); });

  it("shows two distinct role cards, the adopted Groq key, and the independence warning", async () => {
    globalThis.fetch = vi.fn(async (u: any) => String(u).includes("/ai/usage") ? json({ models: [], tasks: [], totals: {} })
      : json(overview(role("main"), role("challenger", { model: "qwen/qwen3.8-27b" }), "Same provider, different model: limited independence."))) as any;
    mount();
    const main = await screen.findByRole("region", { name: "Main AI" });
    const chal = screen.getByRole("region", { name: "Challenger AI" });
    expect(within(main).getByText("Connected")).toBeInTheDocument();
    expect(within(main).getAllByText("from Groq integration").length).toBe(1);
    expect(within(main).getByText(/Using the key of the existing Groq integration/)).toBeInTheDocument();
    expect(within(chal).getByText("What the Challenger reviews")).toBeInTheDocument();
    expect(screen.getAllByText(/limited independence/).length).toBeGreaterThan(0);
    expect(screen.getByText("No AI requests in this period.")).toBeInTheDocument();
  });

  it("sends a typed key once, never keeps it in the browser, and shows the real test result", async () => {
    const calls: { url: string; body: any }[] = [];
    let saved = role("main", { provider: "openai", model: "gpt-x", source: "configured", status: "UNTESTED", key: { present: true, source: "saved", fingerprint: "a1b2c3d4e5f6", unreadable: false } });
    globalThis.fetch = vi.fn(async (u: any, init?: any) => {
      const url = String(u);
      const body = init?.body ? JSON.parse(init.body) : null;
      calls.push({ url, body });
      if (url.endsWith("/ai/roles/main/test")) return json({ ok: false, recorded: false, error: { kind: "auth_failure", message: "OpenAI rejected the API key" },
        steps: [{ name: "API key", ok: false, detail: "OpenAI rejected the API key", error_kind: "auth_failure" }] });
      if (url.endsWith("/ai/roles/main") && init?.method === "PUT") { saved = { ...saved, updated_at: "2026-11-13T11:00:00Z" }; return json(saved); }
      if (url.includes("/ai/usage")) return json({ models: [], tasks: [], totals: {} });
      return json(overview(saved, role("challenger", { enabled: false, provider: "", model: "", status: "NOT_CONFIGURED", key: { present: false, source: "", fingerprint: "", unreadable: false } })));
    }) as any;
    mount();
    const main = await screen.findByRole("region", { name: "Main AI" });
    expect(within(main).getByText(/Saved · fingerprint a1b2c3d4e5f6/)).toBeInTheDocument();
    const keyInput = within(main).getByLabelText(/API key/) as HTMLInputElement;
    expect(keyInput.type).toBe("password");
    fireEvent.change(keyInput, { target: { value: "sk-new-secret-key-123456" } });
    fireEvent.click(within(main).getByRole("button", { name: "Test connection" }));
    expect(await within(main).findByText(/Connection test failed · API key rejected/)).toBeInTheDocument();
    expect(within(main).getByText(/unsaved values; the saved status is unchanged/)).toBeInTheDocument();
    fireEvent.click(within(main).getByRole("button", { name: "Save configuration" }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/ai/roles/main") && c.body?.api_key === "sk-new-secret-key-123456")).toBe(true));
    await waitFor(() => expect((within(main).getByLabelText(/API key/) as HTMLInputElement).value).toBe(""));
    const stored = JSON.stringify({ ...localStorage }) + JSON.stringify({ ...sessionStorage });
    expect(stored).not.toContain("sk-new-secret");
    expect(screen.getByText("Challenger AI is off", { exact: false })).toBeInTheDocument();
  });

  it("tells a discovery failure apart and still allows a manual model id", async () => {
    globalThis.fetch = vi.fn(async (u: any) => {
      const url = String(u);
      if (url.endsWith("/models")) return json({ models: [], hidden: [], manual_entry: true, error: { kind: "outage", message: "OpenAI is unavailable (HTTP 503)", hint: "The provider is unavailable right now." } });
      if (url.includes("/ai/usage")) return json({ models: [{ role: "main", provider: "OpenAI", model: "gpt-x", requests: 3, failed: 1, input_tokens: 1200, output_tokens: 300, total_tokens: 1500,
        avg_latency_ms: 2100, cost_usd: null, cost_note: "no price known", errors: { rate_limited: 1 } }], tasks: [], totals: {} });
      return json(overview(role("main", { provider: "openai", model: "gpt-x", key: { present: true, source: "saved", fingerprint: "f", unreadable: false } }), role("challenger")));
    }) as any;
    mount();
    const main = await screen.findByRole("region", { name: "Main AI" });
    fireEvent.click(within(main).getByRole("button", { name: "Load models" }));
    expect(await within(main).findByText(/Provider unavailable:/)).toBeInTheDocument();
    expect(within(main).getByPlaceholderText(/Model id, exactly as the provider names it/)).toBeInTheDocument();
    const usage = await screen.findByRole("region", { name: "AI usage" });
    expect(within(usage).getByText("unknown")).toBeInTheDocument();
    expect(within(usage).getByText("Rate limited ×1")).toBeInTheDocument();
  });

  it("is read-only for people who cannot manage integrations", async () => {
    globalThis.fetch = vi.fn(async () => json(overview(role("main"), role("challenger")))) as any;
    mount(false);
    const main = await screen.findByRole("region", { name: "Main AI" });
    expect(within(main).queryByRole("button", { name: "Save configuration" })).toBeNull();
    expect(screen.queryByRole("region", { name: "AI usage" })).toBeNull();
  });
});
