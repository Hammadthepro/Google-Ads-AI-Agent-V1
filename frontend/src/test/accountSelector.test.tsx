import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import AccountSelector from "../components/AccountSelector";

const ACCOUNTS = [
  { customer_id: "1110000001", customer_id_display: "111-000-0001", name: "Acme Plumbing", status: "ENABLED", selectable: true, currency_code: "USD", time_zone: "America/Chicago", parent_customer_id: "1000000000", discovery_state: "ACTIVE", unavailable_reason: "" },
  { customer_id: "1110000002", customer_id_display: "111-000-0002", name: "Old Dental", status: "CLOSED", selectable: false, currency_code: "USD", time_zone: "America/Phoenix", parent_customer_id: "1000000000", discovery_state: "ACTIVE", unavailable_reason: "Account is closed" },
];
const REASON = "Manager 4433432999 has no client accounts. The only account(s) linked to it are manager account(s): Sub MCC (5484362542), with no client accounts below.";
const MANAGERS = [
  { customer_id: "4433432999", customer_id_display: "443-343-2999", name: "Root MCC", status: "ENABLED", is_manager: true, parent_customer_id: "", level: 0, selectable: false },
  { customer_id: "5484362542", customer_id_display: "548-436-2542", name: "Sub MCC", status: "ENABLED", is_manager: true, parent_customer_id: "4433432999", level: 1, selectable: false },
];
const calls: { url: string; method: string; body?: string }[] = [];

function setup(opts: { manager?: string | null; selected?: any; empty?: boolean } = {}) {
  calls.length = 0;
  globalThis.fetch = vi.fn(async (url: any, init: any = {}) => {
    const u = String(url), method = init.method ?? "GET";
    calls.push({ url: u, method, body: init.body });
    const ws = { manager_customer_id: opts.manager === undefined ? "1000000000" : opts.manager, manager_name: "Agency MCC", client_accounts: 2, accounts_discovered_at: "2026-11-10T10:00:00Z", discovery_status: "OK", discovery_message: "", manager_accounts: [] as any[],
      ...(opts.empty ? { manager_customer_id: "4433432999", manager_name: "Root MCC", manager_source: "integration_setting", client_accounts: 0, discovery_status: "NO_CLIENTS", discovery_message: REASON, manager_accounts: MANAGERS } : {}) };
    if (u.includes("/google-ads/status")) return new Response(JSON.stringify({ ...ws, selected: opts.selected ?? null }));
    if (u.includes("/google-ads/accounts/refresh")) return new Response(JSON.stringify(opts.empty
      ? { found: 2, client_accounts_found: 0, sub_managers_found: 1, no_clients_reason: REASON, errors: [] }
      : { found: 2, client_accounts_found: 2, sub_managers_found: 0, no_clients_reason: "", errors: [] }));
    if (u.includes("/google-ads/accounts")) {
      const q = new URL(u, "http://x").searchParams;
      const term = (q.get("q") ?? "").toLowerCase(), st = q.get("status") ?? "";
      const items = (opts.empty ? [] : ACCOUNTS).filter((a) => (!term || a.name.toLowerCase().includes(term)) && (!st || a.status === st));
      return new Response(JSON.stringify({ ...ws, total: items.length, items }));
    }
    if (u.includes("/google-ads/selection") && method === "PUT") return new Response(JSON.stringify({ selected: ACCOUNTS[0] }));
    return new Response("{}");
  }) as any;
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MemoryRouter><AccountSelector canRefresh /></MemoryRouter></QueryClientProvider>);
}

describe("AccountSelector", () => {
  it("lists accounts with name, formatted customer ID and status, and disables unusable ones", async () => {
    setup();
    expect(await screen.findByText("Acme Plumbing")).toBeInTheDocument();
    expect(screen.getByText("111-000-0001")).toBeInTheDocument();
    expect(screen.getByText("CLOSED")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Select" })[1]).toBeDisabled();
    expect(screen.getByText("Account is closed")).toBeInTheDocument();
  });

  it("searches and filters through the backend", async () => {
    setup();
    await screen.findByText("Acme Plumbing");
    fireEvent.change(screen.getByLabelText("Search accounts"), { target: { value: "dental" } });
    await waitFor(() => expect(screen.queryByText("Acme Plumbing")).toBeNull());
    expect(await screen.findByText("Old Dental")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Search accounts"), { target: { value: "zzz" } });
    expect(await screen.findByText("No accounts match this search.")).toBeInTheDocument();
  });

  it("stores the selection in the backend using the 10-digit API format, never touching .env", async () => {
    setup();
    fireEvent.click((await screen.findAllByRole("button", { name: "Select" }))[0]);
    await waitFor(() => expect(calls.some((c) => c.method === "PUT" && c.url.endsWith("/google-ads/selection"))).toBe(true));
    expect(JSON.parse(calls.find((c) => c.method === "PUT")!.body!)).toEqual({ customer_id: "1110000001" });
  });

  it("refreshes accounts and says the selection was kept", async () => {
    setup({ selected: ACCOUNTS[0] });
    fireEvent.click(await screen.findByRole("button", { name: "Refresh accounts" }));
    expect(await screen.findByText(/2 client account\(s\) found/)).toBeInTheDocument();
    expect(screen.getByText(/Your selection was kept/)).toBeInTheDocument();
    expect(screen.getByLabelText("Selected account")).toHaveTextContent("Acme Plumbing");
  });

  it("recognises a manager saved on the integration instead of saying none is selected", async () => {
    setup({ empty: true });
    expect(await screen.findByText(/from the manager ID saved on the Google Ads integration/)).toBeInTheDocument();
    expect(screen.queryByText(/No manager \(MCC\) account is selected yet/)).toBeNull();
  });

  it("lists manager accounts separately and never offers them for selection", async () => {
    setup({ empty: true });
    const list = await screen.findByLabelText("Google Ads manager accounts");
    expect(list).toHaveTextContent("Root MCC");
    expect(list).toHaveTextContent("selected manager");
    expect(list).toHaveTextContent("sub-manager under 4433432999");
    expect(screen.getByLabelText("Google Ads client accounts")).not.toHaveTextContent("Sub MCC");
    expect(screen.queryByRole("button", { name: "Select" })).toBeNull();
  });

  it("shows the stored reason for zero client accounts on load, without a refresh", async () => {
    setup({ empty: true });
    expect(await screen.findByText(REASON)).toBeInTheDocument();
    expect(screen.getByText("No client accounts below this manager.")).toBeInTheDocument();
  });

  it("does not report a successful discovery when no client accounts were retrieved", async () => {
    setup({ empty: true });
    fireEvent.click(await screen.findByRole("button", { name: "Refresh accounts" }));
    expect(await screen.findByText(/No client accounts were found\./)).toBeInTheDocument();
    expect(screen.queryByText(/account\(s\) found/)).toBeNull();
    expect(screen.queryByText(/Your selection was kept/)).toBeNull();
  });

  it("tells the user to choose a manager first when none is selected", async () => {
    setup({ manager: null });
    expect(await screen.findByText(/No manager \(MCC\) account is selected yet/)).toBeInTheDocument();
  });
});
