# Research providers, live research view and publishing authorization

## Research capabilities in plain language

| Capability | What it does | Where its content comes from | Provider | Needs |
|---|---|---|---|---|
| **Web Search** | Finds pages the app does not know yet: competitors, customer questions, market context. Returns URLs, titles and snippets. | A search API's index | Tavily (primary), Brave Search (fallback) | `TAVILY_API_KEY` and/or `BRAVE_SEARCH_API_KEY` |
| **Web Fetch** | Opens ONE known URL and extracts its title, headings and text. Cannot find pages. | The page itself, requested directly from this server | Built in (SSRF-guarded fetcher) | Nothing; outbound internet and DNS |
| **Website Crawler** | Starts from a website it is given, reads robots.txt and the sitemap, follows internal links and reads each page (via Web Fetch), within crawl limits. Cannot discover other sites. | The client's or a competitor's own site | Built in | Nothing |
| **Keyword ideas** | Phrases people search for. | Keyword Planner (with Google's volume/bid estimates) or free suggestion sources (no metrics) | Google Ads API; Google Autocomplete, DuckDuckGo | Keyword Planner: Basic/Standard API access + research customer ID |

The crawler is not a search engine. Without Tavily or Brave Search the app does **no** web search: competitor discovery uses only the
competitors you supplied and map listings, and every audit says web search was not used.

```
                         ┌──────────────── Web Search ────────────────┐
  business, category ──▶ │ Tavily ──(fails / not configured)──▶ Brave │──▶ URLs + snippets (stored as unverified web evidence)
  city, services         └───────────────────────────────────────────┘          │
                                                                                 ▼
                                                     ┌──────── Web Fetch (one URL) ────────┐
  client website URL ─────────────┐                  │ SSRF guard → GET → redirects checked │──▶ title, headings, text, status
  competitor website URLs ────────┤                  └──────────────────────────────────────┘
                                  ▼                                     ▲
                       ┌──── Website Crawler ────┐   uses Web Fetch     │
                       │ robots.txt → sitemap →  │──────────────────────┘
                       │ internal links (limits) │──▶ page inventory, services, landing pages
                       └─────────────────────────┘
                                  │
                                  ▼
  ┌──────────────────────────── Keyword research ────────────────────────────┐
  │ Keyword Planner (if access) ─▶ ideas + Google volume/competition/bids      │
  │ Google Autocomplete, DuckDuckGo ─▶ ideas, no metrics                      │
  │ client + competitor page headings ─▶ ideas, no metrics                    │──▶ every keyword: sources[], metrics{value,
  │ account search terms ─▶ ideas + real clicks/conversions                   │    source, retrieved_at, basis}, intent,
  │ web search phrases ─▶ ideas, no metrics                                   │    relevance score (app's own, not volume)
  └───────────────────────────────────────────────────────────────────────────┘
```

Request flow for a full audit: the crawler reads the client site (Website Crawler → Web Fetch per page); competitor sites you supplied
are crawled the same way; if fewer than three competitors were supplied, Web Search looks for more and Web Fetch opens the top
candidates' home pages (they stay "suggested, not verified"); OpenStreetMap/Geoapify add nearby same-category businesses; keyword
research merges every source above. Each step is a recorded research event, and each provider failure is shown, never turned into an
empty success.

## Providers evaluated (November 2026)

| Provider | Free? | Key | Real web results | Used here |
|---|---|---|---|---|
| Tavily | 1,000 API credits/month, no card (per Tavily docs) | yes | yes, with snippets | **Primary web search** |
| Brave Search API | No unconditional free plan: metered (~$5/1,000) with a ~$5 monthly credit that needs a payment method (check Brave's terms) | yes | yes, own index, country targeting | **Fallback web search** |
| Google Custom Search JSON API | Closed to new customers; shuts down 1 Jan 2027 | — | — | Not used |
| DuckDuckGo Instant Answer API | Free, no key | no | No: instant answers only, not web results | Not used for search |
| SearXNG | Free software, self-hosted | no | Aggregates other engines by scraping them | Not used (would scrape search engines) |
| Google Keyword Planner | Free with an Ads account, but needs **Basic or Standard** API access | OAuth | ideas + Google estimates | Used when access allows |
| Google Autocomplete / DuckDuckGo suggestions | Free, no key | no | Suggestions only, no metrics | Used for keyword ideas; endpoints are undocumented and can change — switch them off on the card if your policy requires documented APIs only |
| Bing suggestions | — | no | Returned HTTP 204 (no content) in testing | Off by default |

## Environment variables (or enter them on the Integrations page)
`TAVILY_API_KEY` (primary web search), `BRAVE_SEARCH_API_KEY` (fallback web search), `GEOAPIFY_API_KEY` (optional places/geocoding),
`GOOGLE_ADS_RESEARCH_CUSTOMER_ID` (Keyword Planner research account). Web Fetch, the crawler, OpenStreetMap and search suggestions need none.

## Live research view
Every audit records what it actually does in `research_events` (migration 0011): audit and stage boundaries, each URL the crawler
opens, each page it reads (title, headings, word count), robots.txt blocks and page failures, competitors (supplied, found on the map,
or suggested by web search), keyword discoveries per source, locations, web results, stored results, evidence checks, and every
provider that is unavailable or fails. The audit page polls `GET /audit-reports/{id}/events?after=<seq>` and draws the research canvas,
activity feed, modules, sources, findings, limitations and raw diagnostics from those rows only. Nothing is simulated and there is no
percentage bar: when nothing has been recorded the page says it is waiting. Because events are stored, a refresh or a second viewer
replays the same history, and a finished audit keeps a "how this research was done" replay.

The canvas is SVG with CSS depth and motion: it needs no WebGL, renders on low-powered devices, and drops all motion and tilt under
`prefers-reduced-motion`.

## Keyword research without Keyword Planner
Sources (each optional; one failing never stops the others):

| Source | What it gives | Metrics | Notes |
|---|---|---|---|
| Google Autocomplete | phrases people type for each service seed, seed + city, question seeds | none | unofficial endpoint; throttled, cached 14 days |
| DuckDuckGo suggestions | same | none | unofficial endpoint |
| Bing suggestions | same | none | returned no content from this server during testing (HTTP 204); reported as unavailable |
| Client website | page titles and headings from the website audit | none | |
| Competitor websites | service headings found on competitor sites | none | |
| Google Ads search terms | terms real customers searched in the linked account | observed clicks, impressions, cost, conversions | only with synced account history |
| Tavily (optional) | customer questions and wording in web results | none | budgeted per audit |
| Keyword Planner (optional) | ideas | Google's volume, competition and bid estimates | needs the Google Ads API access Keyword Planner requires |

Every keyword carries `metrics` with value, source, retrieval time, metric type, confidence and basis (`observed` or `unavailable`).
Volume, competition and bids come only from Keyword Planner; otherwise they are shown as "Not available". `relevance_score` is this
app's own 0–100 fit score (label, intent, location, agreement between sources, first-party evidence). It is not search volume.
Without volume, keywords are selected and prioritised on relevance and intent, and each row says so.

Google Trends: there is no generally available public API (Google's official Trends API is in limited alpha), so seasonality is
reported as not measured rather than scraped from unofficial endpoints.

## Geographic research without Google Places
1. Geocode the business: Geoapify when configured, else OpenStreetMap Nominatim.
2. Nearby same-category businesses: OpenStreetMap Overpass (and Geoapify Places when configured), mapped from the business type to
   established OSM tags; an unmapped type is reported as unsupported.
3. Deduplicate across sources (same name within 200 m, or same website), match the client and the supplied competitors, compute density.

Limits and obligations: Nominatim allows at most one request per second with an identifying User-Agent and requires attribution
("© OpenStreetMap contributors", ODbL); Overpass public servers are fair-use. Requests are throttled and cached (geocodes 90 days,
POIs 14 days), and both URLs are configurable for a self-hosted or commercial instance. Geoapify is credit-metered with a free daily
allowance and requires attribution; check its current plan. Neither source has ratings, reviews, hours or operating status: those
checks are reported as unavailable unless Google Places (paid, optional) is configured.

## Tavily
Optional. Used only when it adds something: competitor discovery when fewer than three competitors were supplied, customer language for
up to four services, and local market context. Results are stored with query, URL, title, snippet, retrieval time and purpose, with
confidence `unverified_snippet`; candidates are suggestions, never added automatically; directory sites are filtered out. Identical
queries are served from the cache for 7 days, and `max_searches_per_audit` (default 8) caps spend. Credits: 1 per basic search,
2 per advanced (Tavily's published pricing); the usage ledger (`provider_calls`) is shown on the Integrations card.

## Failure classes
Provider failures are classified as auth failure, permission denied, quota exceeded (Tavily HTTP 432/433), rate limited, timeout,
outage/network, invalid response, unsupported, or this app's own budget exhausted. Missing configuration is `IntegrationNotConfigured`.
Each failure is recorded in the event log and the usage ledger; research continues with the remaining sources.

## Publishing authorization (real Google Ads writes)
`GET /google-ads/write-readiness` lists every key, who controls it and the next step; the Integrations page shows it and the header
badge names how many keys are missing. A real write needs all of:

1. Deployment permit: `GOOGLE_ADS_WRITES_ENABLED=true` in the server environment (operator).
2. Google Ads connection enabled and passing Test Connection.
3. Operating mode "Validated execution" (now enforced by the write guard and inside the adapter, not only displayed).
4. Super Admin authorization (`real_writes_authorized`, written reason, audited).
5. No active kill switch (global, client, account).
6. The deployment account on the write allowlist: `PUT /ads-accounts/{id}/write-allowlist` (Super Admin, 20+ character reason,
   audited; manager or non-enabled accounts are refused).
7. Google's own permission: the Cloud project's API access level and permissible use must include campaign management. A valid OAuth
   token does not prove this; it is shown as unverified until Google accepts a validate-only or real mutate.

Then each change still needs approval of its exact version and account, campaigns are created paused, and nothing is reported as
published until Google confirms the write and read-back matches the approved version.
