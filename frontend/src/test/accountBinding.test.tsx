import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { AccountBinding } from "../components/campaignBuild/AccountBinding";

vi.mock("../lib/auth", () => ({ useAuth: () => ({ can: () => true }) }));

const mount = (node: React.ReactNode) =>
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MemoryRouter>{node}</MemoryRouter></QueryClientProvider>);
const json = (body: any) => new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });
const base = { id: "t1", status: "AWAITING_APPROVAL", approval_id: "ap1" };

describe("AccountBinding", () => {
  it("explains a draft prepared before linking and binds it to the linked account in one action", async () => {
    const posts: { url: string; body: any }[] = [];
    globalThis.fetch = vi.fn(async (u: any, init?: any) => { if (init?.method === "POST") posts.push({ url: String(u), body: JSON.parse(init.body ?? "{}") }); return json({}); }) as any;
    mount(<AccountBinding task={{ ...base, account_binding: { status: "linked", message: "Deploys to 492-972-6198, linked to ABC Home & Commercial Services.",
      customer_id: "4929726198", customer_display: "492-972-6198", approval_customer_id: null, approval_status: "PENDING_APPROVAL", rebind_available: true } }} />);
    expect(screen.getByText("not bound")).toBeInTheDocument();
    expect(screen.getByText(/prepared before an account was linked\. 492-972-6198 is now linked/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Bind to 492-972-6198" }));
    await waitFor(() => expect(posts).toEqual([{ url: "/api/v1/tasks/t1/bind-account", body: {} }]));
  });

  it("shows a bound approval plainly and offers nothing to do", () => {
    mount(<AccountBinding task={{ ...base, account_binding: { status: "linked", message: "Deploys to 492-972-6198.", customer_id: "4929726198",
      customer_display: "492-972-6198", approval_customer_id: "4929726198", rebind_available: false } }} />);
    expect(screen.getByText("bound to 492-972-6198")).toBeInTheDocument();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("links to the accounts page when nothing is linked, and makes the user choose when several are", async () => {
    const { unmount } = mount(<AccountBinding task={{ ...base, account_binding: { status: "none", message: "No Google Ads account is linked to ABC.", rebind_available: false } }} />);
    expect(screen.getByRole("link")).toHaveAttribute("href", "/accounts");
    unmount();
    const posts: any[] = [];
    globalThis.fetch = vi.fn(async (_u: any, init?: any) => { if (init?.method === "POST") posts.push(JSON.parse(init.body)); return json({}); }) as any;
    mount(<AccountBinding task={{ ...base, account_binding: { status: "ambiguous", message: "2 Google Ads accounts are linked.", rebind_available: false, choice_needed: true,
      candidates: [{ id: "a1", customer_id: "1112223333", display: "111-222-3333", name: "Main" }, { id: "a2", customer_id: "4445556666", display: "444-555-6666", name: "Second" }] } }} />);
    const go = screen.getByRole("button", { name: "Bind to the chosen account" });
    expect(go).toBeDisabled();
    fireEvent.click(screen.getByLabelText(/444-555-6666/));
    fireEvent.click(go);
    await waitFor(() => expect(posts).toEqual([{ account_id: "a2" }]));
  });
});
