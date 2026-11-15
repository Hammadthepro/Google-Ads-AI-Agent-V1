import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { DraftRevision } from "../components/campaignBuild/DraftRevision";

vi.mock("../lib/auth", () => ({ useAuth: () => ({ can: () => true }) }));
const mount = (node: React.ReactNode) =>
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MemoryRouter>{node}</MemoryRouter></QueryClientProvider>);
const json = (b: any) => new Response(JSON.stringify(b), { status: 200, headers: { "content-type": "application/json" } });

describe("DraftRevision", () => {
  it("sends instructions or a plain regenerate, and says research is reused", async () => {
    const posts: any[] = [];
    globalThis.fetch = vi.fn(async (u: any, init?: any) => { if (init?.method === "POST") posts.push([String(u), JSON.parse(init.body)]); return json({}); }) as any;
    mount(<DraftRevision task={{ id: "t1", status: "AWAITING_APPROVAL", revision: { available: true, current_version: 2 }, draft_attempts: [] }} />);
    expect(screen.getByText("current: v2")).toBeInTheDocument();
    expect(screen.getByText(/nothing is researched again/)).toBeInTheDocument();
    const revise = screen.getByRole("button", { name: "Revise with these instructions" });
    expect(revise).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Revision instructions"), { target: { value: "Shorter headlines" } });
    fireEvent.click(revise);
    await waitFor(() => expect(posts).toEqual([["/api/v1/tasks/t1/revise-draft", { instructions: "Shorter headlines" }]]));
  });

  it("shows a running revision without offering a second one, and a failed one as unchanged", () => {
    globalThis.fetch = vi.fn(async () => json({})) as any;
    const { unmount } = mount(<DraftRevision task={{ id: "t1", status: "RUNNING", revision: { available: false, running: true, current_version: 1 },
      draft_attempts: [{ attempt: 2, kind: "revision", status: "running" }] }} />);
    expect(screen.getByText("drafting v2")).toBeInTheDocument();
    expect(screen.queryByRole("button")).toBeNull();
    unmount();
    mount(<DraftRevision task={{ id: "t1", status: "AWAITING_APPROVAL", revision: { available: true, current_version: 1 },
      draft_attempts: [{ attempt: 2, kind: "revision", status: "failed", error: "Groq timed out" }] }} />);
    expect(screen.getByRole("alert")).toHaveTextContent("failed and nothing changed: Groq timed out");
  });
});
