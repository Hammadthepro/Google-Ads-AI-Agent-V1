import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import Integrations from "../pages/Integrations";

let canManage = true;
vi.mock("../lib/auth", () => ({ useAuth: () => ({ user: { id: "u", email: "a@b.co", name: "A", role: "SUPER_ADMIN", permissions: [] }, loading: false, can: () => canManage }),
  ROLE_LABEL: {} }));

const base = { enabled: false, readonly: false, deployment_managed: false, credentials_unreadable: false, dependencies: [], blocked_by: [], subtests: [], targets: {}, info: {},
  missing: [], required: [], scopes: [], oauth: false, docs: "", last_test_at: null, last_test_ok: null, last_test_steps: [], last_success_at: null, last_error: "", discovered: {},
  connected_account: "", account_id: "", status: "NOT_CONNECTED", display: { config: "Configured", connection: "Configured, not tested", label: "Configured, not tested", testable: true } };
const ITEMS = [
  { ...base, provider: "google_ads", label: "Google Ads API", group: "Google", description: "Ads", oauth: true, missing: ["client_id"],
    display: { config: "Not configured", connection: "Not configured", label: "Not configured", testable: true },
    settings: { client_id: { label: "OAuth client ID", value: "", source: "missing", required: true }, login_customer_id: { label: "Default manager", value: "", source: "missing", required: false } },
    secrets: { client_secret: { label: "OAuth client secret", saved: true, source: "environment", fingerprint: "ab12cd34", required: true },
               refresh_token: { label: "Refresh token", saved: true, source: "environment", fingerprint: "ef56ab78", required: true } } },
  { ...base, provider: "keyword_planner", label: "Keyword Planner", group: "Google", description: "KP", settings: {}, secrets: {},
    display: { config: "Configured", connection: "Blocked by dependency", label: "Blocked by dependency", testable: true },
    dependencies: [{ key: "google_ads", label: "Google Ads connected", ok: false, detail: "Run Test Connection on the Google Ads card", action: "google_ads" },
                   { key: "client_account", label: "At least one enabled client account discovered", ok: false, detail: "No client account has been discovered yet", action: "google_ads" }] },
  { ...base, provider: "smtp", label: "Email alerts (SMTP)", group: "System", description: "SMTP", missing: ["host"], display: { config: "Not configured", connection: "Not configured", label: "Not configured", testable: true },
    settings: { host: { label: "SMTP host", value: "", source: "missing", required: true }, security: { label: "Security", value: "starttls", source: "default", required: false } },
    secrets: { password: { label: "Password", saved: false, source: "", fingerprint: "", required: false } } },
  { ...base, provider: "groq", label: "Groq (internal AI)", group: "AI", description: "Groq", settings: {}, secrets: {} },
  { ...base, provider: "crawler", label: "Website crawler", group: "Website research", description: "Crawler", settings: {}, secrets: {} },
  { ...base, provider: "database", label: "Database", group: "System", description: "DB", readonly: true, deployment_managed: true, settings: {}, secrets: {} },
];
const calls: { url: string; method: string; body?: any }[] = [];

function setup(manage = true) {
  canManage = manage;
  calls.length = 0;
  globalThis.fetch = vi.fn(async (url: any, init: any = {}) => {
    const u = String(url), method = init.method ?? "GET";
    calls.push({ url: u, method, body: init.body ? JSON.parse(init.body) : undefined });
    if (u.endsWith("/integrations")) return new Response(JSON.stringify({ integrations: ITEMS, mode: { mode: "read_only", label: "Real account — read-only", write_keys: {}, real_reads: true, real_writes_possible: false, explanation: "" }, redirect_uri: "http://x/api/v1/integrations/google/callback" }));
    if (method === "PUT" && u.includes("/integrations/google_ads")) return new Response(JSON.stringify({ ...ITEMS[0] }));
    return new Response("{}");
  }) as any;
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MemoryRouter><Integrations /></MemoryRouter></QueryClientProvider>);
}

/** Providers start collapsed; a test opens the one it is about, exactly as a person would. */
const openProvider = async (label: RegExp) => fireEvent.click(await screen.findByRole("button", { name: label, expanded: false }));

describe("Integrations dashboard", () => {
  it("groups cards into the four categories", async () => {
    setup();
    for (const t of ["Google", "Other AI", "Website research", "System"]) expect((await screen.findAllByText(t, { selector: "h2" })).length).toBeGreaterThan(0);
  });

  it("shows where every Google Ads field comes from and marks the missing required one", async () => {
    setup();
    const card = (await screen.findAllByText("Google Ads API")).map((e) => e.closest("[id]")).length;
    expect(card).toBeGreaterThan(0);
    expect(await screen.findAllByText("missing")).not.toHaveLength(0);
    expect(screen.getAllByText(/Provided by the deployment/).length).toBe(2); // client secret and refresh token come from the environment
    expect(screen.getByLabelText(/OAuth client ID \*/, { selector: "input" })).toBeInTheDocument();
  });

  it("flags unsaved edits, saves only what changed, and never sends blank secrets", async () => {
    setup();
    await openProvider(/^Google Ads API/);
    const input = await screen.findByLabelText(/OAuth client ID \*/);
    fireEvent.change(input, { target: { value: "609-abc.apps.googleusercontent.com" } });
    expect(await screen.findByText(/Unsaved changes — not saved on the server yet/)).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: "Save" })[0]);
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    const put = calls.find((c) => c.method === "PUT")!;
    expect(put.body.settings).toEqual({ client_id: "609-abc.apps.googleusercontent.com" });
    expect(put.body.secrets).toEqual({}); // environment secrets are left alone: blank means keep
    expect(JSON.stringify(calls)).not.toMatch(/1\/\/|client_secret":"/);
  });

  it("discard throws the draft away", async () => {
    setup();
    await openProvider(/^Google Ads API/);
    fireEvent.change(await screen.findByLabelText(/OAuth client ID \*/), { target: { value: "typed" } });
    fireEvent.click(await screen.findByRole("button", { name: "Discard" }));
    await waitFor(() => expect(screen.queryByText(/Unsaved changes/)).toBeNull());
  });

  it("explains the blocking dependency instead of showing a dependent integration as connected", async () => {
    setup();
    const deps = await screen.findByLabelText("Dependencies");
    expect(within(deps).getByText(/Google Ads connected/)).toBeInTheDocument();
    expect(within(deps).getByText(/No client account has been discovered yet/)).toBeInTheDocument();
    expect(screen.getAllByText("Blocked by dependency").length).toBeGreaterThan(0);
    expect(screen.queryByText("Connected", { selector: "span" })).toBeNull();
  });

  it("has no Google Business Profile card: the profile link is only a research input in client intake", async () => {
    setup();
    await screen.findAllByText("Deployment-managed");
    expect(screen.queryByText(/Business Profile/i)).toBeNull();
  });

  it("marks infrastructure as deployment-managed with no editable fields", async () => {
    setup();
    expect((await screen.findAllByText("Deployment-managed")).length).toBeGreaterThan(0);
    expect(screen.getByText(/Set DATABASE_URL in the deployment environment/)).toBeInTheDocument();
  });

  it("offers the SMTP security choice and an explicit test-email action", async () => {
    setup();
    await openProvider(/^Email alerts \(SMTP\)/);
    expect(await screen.findByRole("combobox", { name: "Security" })).toHaveValue("starttls");
    expect(screen.getByRole("button", { name: "Send test email" })).toBeDisabled(); // needs an address; nothing is sent implicitly
  });

  it("hides every mutating control from users who cannot manage integrations", async () => {
    setup(false);
    await screen.findAllByText("Google Ads API");
    fireEvent.click(screen.getByRole("button", { name: "Expand all" }));   // hidden by permission, not merely collapsed
    expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Disconnect" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Send test email" })).toBeNull();
    expect(screen.queryByRole("button", { name: /Test all connections/ })).toBeNull();
  });

  it("starts with every provider collapsed to a summary row, out of the accessibility tree until opened", async () => {
    setup();
    const header = await screen.findByRole("button", { name: /^Google Ads API/, expanded: false });
    const panel = document.getElementById(header.getAttribute("aria-controls")!)!;
    expect(panel).toHaveAttribute("aria-hidden", "true");
    expect(panel).toHaveAttribute("inert");
    expect(screen.queryByRole("textbox", { name: /OAuth client ID/ })).toBeNull();
    fireEvent.click(header);
    expect(header).toHaveAttribute("aria-expanded", "true");
    expect(panel).not.toHaveAttribute("aria-hidden");
    expect(screen.getByRole("textbox", { name: /OAuth client ID/ })).toBeInTheDocument();
  });

  it("keeps a half-typed draft when the provider is collapsed and reopened", async () => {
    setup();
    await openProvider(/^Google Ads API/);
    fireEvent.change(screen.getByLabelText(/OAuth client ID \*/), { target: { value: "draft-id" } });
    fireEvent.click(screen.getByRole("button", { name: /^Google Ads API/, expanded: true }));
    await openProvider(/^Google Ads API/);
    expect(screen.getByLabelText(/OAuth client ID \*/)).toHaveValue("draft-id");
    expect(screen.getByText(/Unsaved changes — not saved on the server yet/)).toBeInTheDocument();
  });

  it("tests a collapsed provider from its summary row with the same request as before", async () => {
    setup();
    await screen.findByRole("button", { name: /^Groq/ });
    const row = document.getElementById("card-groq")!;
    fireEvent.click(within(row).getByRole("button", { name: "Test Connection" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.url.endsWith("/integrations/groq/test"))).toBe(true));
  });
});
