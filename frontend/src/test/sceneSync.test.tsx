/** The scene and the findings beside it read one event stream and share one focus. In the test runner there is no WebGL, so the
 *  flat (SVG) scene is drawn — the same layout the 3D view uses. */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import type { ResearchEvent } from "../components/research/model";
import { LiveFindings } from "../components/research/LiveFindings";

let seq = 0;
const ev = (kind: string, title: string, data: Record<string, any> = {}, stage = "website", level: "info" | "warn" = "info"): ResearchEvent =>
  ({ seq: ++seq, kind, stage, level, title, data, at: new Date().toISOString() });
const EVENTS = (() => {
  seq = 0;
  return [
    ev("audit_started", "Research started", { business: "Northside Plumbing", website: "https://northside.example/" }, ""),
    ev("stage_started", "Website & landing pages", {}),
    ev("page_extracted", "Home", { url: "https://northside.example/", page_title: "Northside Plumbing", h1: "Plumbers in Austin", headings: ["Services"], words: 640 }),
    ev("page_extracted", "Drains", { url: "https://northside.example/drains", page_title: "Drains", h1: "Drain cleaning", headings: ["Services"], words: 900 }),
    ev("url_crawling", "", { url: "https://northside.example/water" }),
    ev("page_failed", "", { url: "https://northside.example/old", status: 404 }, "website", "warn"),
  ];
})();

function mount(status: string, done: boolean) {
  globalThis.fetch = vi.fn(async () => new Response(JSON.stringify({ events: EVENTS, steps: [], done, last_seq: EVENTS.length, status }))) as any;
  return render(<QueryClientProvider client={new QueryClient()}><MemoryRouter>
    <LiveFindings url="/audits/a1/events" active={!done} kind="website" audit={{ status }} /></MemoryRouter></QueryClientProvider>);
}

describe("scene and findings stay in step", () => {
  it("count the same recorded pages", async () => {
    mount("RUNNING", false);
    const scene = await screen.findByRole("img", { name: /Site map/ });
    await waitFor(() => expect(scene).toHaveAccessibleName("Site map: 2 pages read, 1 page opening, 1 page failed."));
    const summary = screen.getByRole("region", { name: "What was found" });
    expect(within(summary).getByText("Read 2 pages on northside.example")).toBeInTheDocument();
    expect(within(summary).getByText("1 address could not be opened (HTTP 404)")).toBeInTheDocument();
  });

  it("hovering a summary line rings the matching point in the scene", async () => {
    const { container } = mount("RUNNING", false);
    const line = await screen.findByText("Home page leads with “Plumbers in Austin”");
    const ring = () => container.querySelectorAll("svg circle[stroke='#fff']").length;
    expect(ring()).toBe(0);
    fireEvent.mouseEnter(line.closest("li")!);
    expect(ring()).toBe(1);
    fireEvent.mouseLeave(line.closest("li")!);
    expect(ring()).toBe(0);
  });

  it("hovering a point in the scene highlights its line in the step log", async () => {
    const { container } = mount("RUNNING", false);
    const step = (await screen.findByText("Read “Drains”")).closest("li")!;
    expect(step.className).not.toMatch(/bg-brand-50/);
    const nodes = [...container.querySelectorAll("svg g[style]")];
    // the scene draws one group per node; find the one for /drains by hovering until its step lights up
    for (const g of nodes) { fireEvent.mouseEnter(g); if (/bg-brand-50/.test(step.className)) break; }
    expect(step.className).toMatch(/bg-brand-50/);
  });

  it("a finished, failed audit is shown as finished and failed in both", async () => {
    mount("FAILED", true);
    const scene = await screen.findByRole("img", { name: /Site map/ });
    expect(screen.queryByText("live")).toBeNull();
    expect(scene.textContent).not.toMatch(/live/);
    expect(await screen.findByText("The analysis did not finish")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(/Finished/);
  });
});
