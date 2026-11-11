import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { NewClient, EditClient } from "../pages/ClientForm";

const calls: { url: string; method: string; body?: any }[] = [];

function setup(path: string, existing?: Record<string, unknown>) {
  calls.length = 0;
  globalThis.fetch = vi.fn(async (url: any, init: any = {}) => {
    const u = String(url), method = init.method ?? "GET";
    calls.push({ url: u, method, body: init.body ? JSON.parse(init.body) : undefined });
    if (u.includes("/client-profiles/validate")) return new Response(JSON.stringify({ valid: true, errors: {}, normalized: {} }));
    if (u.match(/\/client-profiles\/[^/?]+$/) && method === "GET") return new Response(JSON.stringify(existing ?? {}));
    if (u.endsWith("/clients") || u.includes("/clients?")) return new Response("[]");
    return new Response("{}");
  }) as any;
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="clients/new" element={<NewClient />} />
          <Route path="clients/:id/edit" element={<EditClient />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const gbpChoice = (name: "Yes" | "No") => within(screen.getByRole("group", { name: "Has Google Business Profile" })).getByRole("button", { name });
const lastValidatedProfile = () => [...calls].reverse().find((c) => c.url.includes("/client-profiles/validate"))?.body.profile;

describe("client intake form", () => {
  it("shows the Google Business Profile URL only after Yes, and clears it on No", async () => {
    setup("/clients/new");
    expect(screen.queryByLabelText(/Google Business Profile URL/)).toBeNull();
    await screen.findByRole("group", { name: "Has Google Business Profile" });
    fireEvent.click(gbpChoice("Yes"));
    const url = await screen.findByPlaceholderText("Paste the Google Maps or Google Business Profile link");
    fireEvent.change(url, { target: { value: "https://maps.app.goo.gl/abc" } });
    await waitFor(() => expect(lastValidatedProfile()?.gbp_url).toBe("https://maps.app.goo.gl/abc"));
    fireEvent.click(gbpChoice("No"));
    expect(screen.queryByPlaceholderText("Paste the Google Maps or Google Business Profile link")).toBeNull();
    await waitFor(() => expect(lastValidatedProfile()?.gbp_url).toBe(""));  // the hidden value is cleared, not just hidden
    fireEvent.click(gbpChoice("Yes"));  // coming back does not resurrect it
    expect((await screen.findByPlaceholderText("Paste the Google Maps or Google Business Profile link") as HTMLInputElement).value).toBe("");
  });

  it("asks for an optional weekly conversion target instead of a monthly budget", async () => {
    setup("/clients/new");
    expect(await screen.findByPlaceholderText("e.g. 15 conversions per week")).toBeInTheDocument();
    expect(screen.queryByPlaceholderText(/1500/)).toBeNull();
    expect(screen.queryByText(/Monthly ad budget/)).toBeNull();
    fireEvent.change(screen.getByPlaceholderText("e.g. 15 conversions per week"), { target: { value: "12" } });
    await waitFor(() => expect(lastValidatedProfile()?.weekly_conversion_target).toBe("12"));
  });

  it("marks competitor name and website as required", async () => {
    setup("/clients/new");
    const card = (await screen.findByText("Competitor 1")).closest("div")!.parentElement!;
    expect(card.textContent).toMatch(/Name\s*\*/);
    expect(card.textContent).toMatch(/Website\s*\*/);
  });

  it("opens an existing client with its saved profile link and target", async () => {
    setup("/clients/c1/edit", { name: "Acme", category: "plumber", website: "https://acme.example/", has_gbp: true, gbp_url: "https://g.page/acme",
      weekly_conversion_target: 9, monthly_budget: "", services: ["Drains"], service_areas: [], competitors: [{ name: "Rival", website: "https://rival.example/", maps_url: "" }] });
    expect(await screen.findByDisplayValue("https://g.page/acme")).toBeInTheDocument();
    expect(screen.getByDisplayValue("9")).toBeInTheDocument();
    expect(screen.getByDisplayValue("https://rival.example/")).toBeInTheDocument();
  });
});
