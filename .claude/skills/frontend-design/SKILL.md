---
name: frontend-design
description: Use when building or changing this app's React dashboard UI — page layouts, components, the design tokens, tables, forms, approval/review screens, data visualisation, responsive behaviour, and wiring screens to real backend endpoints (no decorative or fake UI).
---

# Frontend design (this project)

Stack: React 18 + TypeScript + Vite + Tailwind 3 + TanStack Query + React Router. All calls go through `frontend/src/api/client.ts` (same-origin cookies, CSRF header from the `gaa_csrf` cookie).

## Principles (project rules)
1. **Every control does something real.** No decorative buttons, hardcoded statuses or simulated success toasts. Show errors from the API verbatim (`ErrorBox`), including `details.reasons` from the safety guard.
2. **Provenance is always visible:** `DataSourceBadge` (MOCK / LIVE / MIXED) on every metric block; the global `ModeBanner` shows the operating mode (Mock / Read-only / Validated execution) and whether real writes are possible.
3. **Status vocabulary is fixed:** integration statuses are Not Connected · Configuration Required · Connected · Connection Failed · Authorization Expired · Synchronization Required (mocks: "Mock Connected"). Use `StatusBadge`.
4. **Dangerous actions need `ConfirmDialog`** with a plain explanation (approve this exact version, execute, enable, authorize writes, disconnect).
5. **Never render secrets.** Credential fields are write-only: show "Saved · fingerprint ab12…", never the value.
6. Estimates are labelled as estimates. AI text is labelled as AI-written.

## Design tokens & components
- Tokens: `frontend/tailwind.config.js` (`brand`, `ink`, `surface` scales) and `frontend/src/index.css`.
- Kit: `frontend/src/components/ui.tsx` — `PageHeader`, `Card`, `Section`, `Stat`, `Table`, `Badge`, `StatusBadge`, `DataSourceBadge`, `Button`, `Field`, `FieldGroup`, `Input`, `Select`, `Textarea`, `Tabs`, `ConfirmDialog`, `Notice`, `KV`, `JsonBlock`, `EmptyState`, `Spinner`.
- Layout: `components/Layout.tsx` (sidebar groups, mode banner, user menu).
- Charts: lightweight inline SVG (`Sparkline`, `Bars`), with no heavy chart library; label axes and units.

## Page workflow
1. Read the backend router first (`backend/app/api/routers/*.py`) and use the real response shape.
2. `useQuery` for reads (stable `queryKey`), `useAction` (`lib/hooks.ts`) for mutations, invalidating the affected keys.
3. Handle loading (`Spinner`), empty (`EmptyState`), error (`ErrorBox`) and permission (`useAuth().can(...)`) states. Hide controls the role can't use, but rely on the backend for enforcement.
4. Responsive: the sidebar collapses below `lg`; tables scroll horizontally; forms use `grid md:grid-cols-*`.
5. Accessibility: inputs are wrapped in `<label>` (`Field`); checkbox groups use `FieldGroup` (`fieldset`/`legend`); buttons have text.

## Checklist before committing UI
- [ ] `npx tsc -b` and `npx vitest run` pass
- [ ] Browser smoke test passes: `node e2e/ui-smoke.mjs` against a running stack
- [ ] Screens checked at 1440 px and 390 px (Playwright screenshots)
- [ ] No console errors other than expected 401s before sign-in
- [ ] No secret, token or full credential value appears in the DOM or network responses
