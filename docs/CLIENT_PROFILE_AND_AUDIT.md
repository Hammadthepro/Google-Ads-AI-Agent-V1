# Client profile form and the five-part audit

## Client profile
- **New client** (`/clients/new`) is one page with eight sections (Business, Website and tracking, Google Business Profile, Services, Locations, Budget, Competitors, Notes). Required fields are marked `*`; the server validates as you type (`POST /client-profiles/validate`) and returns a field → message map.
- Saving creates the client **and** its business in one transaction (`POST /client-profiles`) and assigns a **unique client ID** (`CL-000001`, …; unique DB index). It then lands on the client profile with *Watch the analysis live*, *Open the dashboard* and *Add another client*, and starts the first audit in the background.
- **Edit client** (`/clients/:id/edit`, `PUT /client-profiles/:id`) edits the same fields. Things the form does not show are preserved (services marked "do not advertise", per-service landing pages). The client ID never changes. Changing the profile does **not** rewrite old audits; they are flagged "profile edited since".
- Normalisation: phone → `(512) 555-0100` / `+<digits>`, website and `/contact` paths → full URLs, budget `1.5k`/`$1,500` → 1500, `Austin, TX 78701` → city/state/zip, pasted tag code → GTM/G-/AW- ID, state names → abbreviations. A website can belong to only one client.

## Audit (inside the existing pipeline)
`services/audit_report.py` runs the existing website / Business Profile / competitor / keyword audits (`services/audits.py`, which still save their own `Audit` rows) and turns their output plus the client's information into five sections: **Client & business**, **Website & landing pages**, **Google Business Profile**, **Competitors**, **Keywords**.

- Each finding has `status` (PASS/WARN/FAIL/INFO/UNAVAILABLE/N-A), `evidence` (**Verified**, **Estimated**, **AI-generated**, **Unavailable**, **Not applicable**), a source, the date, a `simulated` flag when mock data was used, a recommendation and whether it needs client clarification or manual verification.
- **Scores** (per section and overall) are computed from findings only: weight × credit (PASS 1, WARN ½, FAIL 0). UNAVAILABLE, N/A and INFO checks are excluded and shown as coverage. Overall = client 25%, website 25%, profile 15%, competitors 15%, keywords 20% (sections that could be scored).
- **Never fabricated:** keyword volume, bids and competition come from Keyword Planner or are shown as *unavailable*; Keyword Planner has no average CPC, so that column is always unavailable. Rule-based keyword variations are labelled *AI-generated* hypotheses with no metrics. Business economics (margin, CAC, CPL, LTV…) are listed as questions for the client.
- Output: scores, critical/high/medium/low recommendations, items needing client clarification, items needing manual verification, next steps, a 14-point summary, and a keyword spreadsheet (UI table + CSV).
- **History:** every run is a new, immutable `AuditReport` (`version` per business) stored under the client's ID with a snapshot of the information it used. A new run is compared with the previous one (resolved / worse / new). Reports are only readable through their own client (access checked on every request).

## Limits
Not connected, therefore reported as unavailable/manual: Google Autocomplete / People Also Ask / Trends, Semrush / Ahrefs, the Ads Transparency Center, the Business Profile API (claimed/verified status, posts, Q&A, responses), rendered-page checks (pop-ups, form behaviour, cross-browser). Writing quality and Google Ads policy compliance need a person.
