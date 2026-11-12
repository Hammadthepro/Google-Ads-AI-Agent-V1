# Frontend redesign — design direction (V2 interface)

Frontend-only. No backend file, API contract, query key, mutation, permission check or user-visible workflow changes.
Stack stays React 18 + Vite + TypeScript + Tailwind + TanStack Query. One dependency added: `three` (lazy-loaded, research scenes only).

## Intent
An agency operations tool for Google Search campaigns that must read as a serious instrument: precise, calm, dense where data is dense,
and honest about provenance. The memorable element is the live research workspace; everything around it stays quiet.

## Identity
- **Palette** — "Ink & Petrol". Ink neutrals (cool blue-grey) replace Tailwind slate app-wide; petrol (#136977) is the single accent for
  primary actions, active navigation, links and focus. Saffron (#C98A1C) and plum (#7B4F8E) exist only for data series.
  Status colours stay semantic (emerald / amber / rose). Canvas #F2F3F1, surfaces white, sidebar ink #0E1720.
- **Type** — IBM Plex Sans (UI and reading), IBM Plex Mono only for real identifiers (customer IDs, URLs, event logs).
  Scale 11 / 12 / 13 / 14 / 16 / 20 / 26 px. Sentence case labels; no tracked all-caps eyebrow labels.
- **Shape** — radius follows hierarchy: 4 px badges, 6 px controls, 10 px panels. Hairline borders do the structural work; shadows only
  on things that float (menus, dialogs).

## Shell
Dark ink sidebar (232 px, collapsible to a 60 px icon rail, preference kept per browser), grouped by product area. White 52 px header
with route breadcrumb and the write-safety status. Content max width 1280 px; the agent workspace uses the full width.

## Components
`ui.tsx` keeps every export and prop signature (tests and pages depend on them) and is restyled. Additions: `MetricStrip` (one
divided strip instead of a row of KPI cards), `Section` (flat titled region without a box), `Panel` alias of Card.

## Live research workspace
`LiveFindings` keeps its API and test-visible text, and gains an optional `kind` prop. When set, the panel becomes a two-column
workspace: a 3D scene of what is being researched (left) and the readable findings stream (right), both driven by the same single
event stream (`useEventStream`) so they never disagree. Scene layouts are pure functions of `buildModel(events)` and are unit-tested:

| Audit | Scene | Drawn only from |
|---|---|---|
| Website | Site tree: domain hub, pages placed by URL depth, edges to parent path; fetching / read / failed / blocked states | `url_crawling`, `page_extracted`, `page_failed`, `crawl_blocked` |
| Business presence (GBP) | Local terrain: geocoded business at centre, map listings at true relative position, distance rings | `location_identified`, `competitor_identified` with lat/lon |
| Competitors | Orbit: target hub, competitors on rings (distance when known, else arrival order), their crawled pages as satellites | `competitor_identified`, competitor-stage pages |
| Keyword research | Clusters: one cluster per discovery (seed), example terms around it, lanes by provider, size by real count | `keyword_discovery` |
| Combined (ALL, client audit report) | Three islands — website, local market, competitors (+keywords when present) — each dimmed until its stage starts | all of the above + stage state |

No progress percentages, no invented nodes. Scenes render only while visible, stop animating under reduced motion, dispose GPU
resources on unmount, cap pixel ratio, and fall back to an SVG projection of the same layout when WebGL is unavailable.
The findings panel never waits on the 3D chunk.

## Non-goals
Backend events for richer animation; new metrics; mock data; changing copy that tests or users rely on (button names, notices).

## Calls made by the builder (not discussed with the client)
Palette, typefaces, dark sidebar, sidebar collapse, the scene concepts above, three.js as the single new dependency.
