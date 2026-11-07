import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import Login from "../pages/Login";

it("shows the development login with seeded users when the backend is in mock auth mode", async () => {
  globalThis.fetch = vi.fn(async () => new Response(JSON.stringify({ provider: "mock", dev_users: [{ email: "admin@napollo.example", name: "Alex Admin", role: "ADMIN" }] }), { status: 200 })) as any;
  render(<QueryClientProvider client={new QueryClient()}><MemoryRouter><Login /></MemoryRouter></QueryClientProvider>);
  expect(await screen.findByText("Alex Admin")).toBeInTheDocument();
  expect(screen.getByText(/Development login/)).toBeInTheDocument();
  expect(screen.getByText("Admin")).toBeInTheDocument();
});
