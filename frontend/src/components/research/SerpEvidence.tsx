import { Badge, Card, cx, dt, Notice } from "../ui";

/** What SerpApi actually returned for this research: every query (with place, time and whether it came from the cache), the public ads,
 *  organic competitors, related searches, autocomplete and local businesses, each linked to its source. Nothing here is a metric. */
export function SerpEvidence({ serp }: { serp: any }) {
  if (!serp) return null;
  if (!serp.used) return <p className="text-xs text-slate-500">Google results (SerpApi): {serp.reason}</p>;
  const queries: any[] = serp.queries ?? [];
  const ads: any[] = (serp.sponsored_ads ?? []).filter((a: any) => !a.is_client);
  const organic: any[] = serp.organic_competitors ?? [];
  const local: any[] = serp.local_businesses ?? [];
  const phrases = [...(serp.related_searches ?? []).map((r: any) => ({ t: r.phrase, k: "related" })), ...(serp.autocomplete ?? []).map((r: any) => ({ t: r.phrase, k: "autocomplete" }))];
  return (
    <Card title="Google results (SerpApi)" description={<>Observed for the queries below{serp.location ? <> in <b>{serp.location}</b></> : " (country only)"}. A rank is not search volume and an ad is not a CPC or proof of performance.</>}>
      <div className="space-y-5 text-[13px]">
        {serp.location_note && <Notice tone="amber">{serp.location_note}</Notice>}
        {(serp.unavailable ?? []).length > 0 && <Notice tone="amber">Not researched: {(serp.unavailable as any[]).map((u) => `${u.capability}${u.query ? ` (“${u.query}”)` : ""} — ${u.reason}`).join("; ")}</Notice>}
        <section aria-label="Queries executed">
          <h4 className="mb-1.5 text-xs font-medium text-slate-500">Queries ({queries.length}){serp.quota?.plan_searches_left != null ? ` · ${serp.quota.plan_searches_left.toLocaleString()} plan searches left when checked` : ""}</h4>
          <ul className="divide-y divide-slate-100 rounded-md border border-slate-200">
            {queries.map((q, i) => (
              <li key={i} className="flex flex-wrap items-center gap-x-3 gap-y-0.5 px-3 py-1.5 text-xs">
                <span className="font-medium text-slate-800">“{q.query}”</span>
                <span className="text-slate-500">{(q.engine || "").replace(/_/g, " ")}</span>
                {q.location && <span className="text-slate-500">{q.location}</span>}
                {q.ok === false ? <Badge tone="red">{q.error_kind}: {q.error}</Badge>
                  : q.served_from_cache ? <Badge tone="slate" title="Answered from this app's cache; the results are from the original retrieval time">cached · retrieved {dt(q.retrieved_at)}</Badge>
                  : <Badge tone="green">live · {dt(q.retrieved_at)}</Badge>}
                {q.empty && <span className="text-slate-500">Google returned no results</span>}
                {q.search_id && <span className="font-mono text-2xs text-slate-400">{q.search_id}</span>}
              </li>))}
          </ul>
        </section>
        {ads.length > 0 && <section aria-label="Sponsored results">
          <h4 className="mb-1.5 text-xs font-medium text-slate-500">Sponsored results seen ({ads.length}) — public ad copy, not bids or performance</h4>
          <ul className="space-y-2">{ads.slice(0, 12).map((a, i) => (
            <li key={i} className="rounded-md border border-slate-200 px-3 py-2">
              <div className="flex flex-wrap items-baseline gap-x-2"><a className="font-medium text-brand-700 hover:underline" href={a.link} target="_blank" rel="noreferrer noopener">{a.title}</a>
                <span className="text-xs text-slate-500">{a.displayed_link || a.domain}</span>{a.block_position && <Badge tone="slate">{a.block_position}</Badge>}</div>
              {a.description && <p className="text-xs text-slate-700">{a.description}</p>}
              {a.sitelinks?.length > 0 && <p className="text-2xs text-slate-500">Sitelinks: {a.sitelinks.join(" · ")}</p>}
              <p className="text-2xs text-slate-400">for “{a.query}”{a.location ? ` · ${a.location}` : ""} · {dt(a.retrieved_at)}{a.served_from_cache ? " (cached)" : ""}</p>
            </li>))}</ul>
        </section>}
        {organic.length > 0 && <section aria-label="Organic competitors">
          <h4 className="mb-1.5 text-xs font-medium text-slate-500">Organic competitors ({organic.length}) — directories and the client's own site excluded</h4>
          <ul className="space-y-1">{organic.slice(0, 12).map((c) => (
            <li key={c.domain} className="flex flex-wrap items-baseline gap-x-2 text-xs">
              <span className="tabular w-8 text-right text-slate-400">#{c.best_position ?? "—"}</span>
              <a className="font-medium text-brand-700 hover:underline" href={c.url} target="_blank" rel="noreferrer noopener">{c.domain}</a>
              <span className="truncate text-slate-500">{c.title}</span>
              {c.already_known && <Badge tone="blue">already a listed competitor</Badge>}
              <span className="text-2xs text-slate-400">{c.appearances.length} query(ies)</span>
            </li>))}</ul>
        </section>}
        {phrases.length > 0 && <section aria-label="Search phrases">
          <h4 className="mb-1.5 text-xs font-medium text-slate-500">Related searches and autocomplete ({phrases.length}) — phrases people search, no volume</h4>
          <div className="flex flex-wrap gap-1">{phrases.slice(0, 40).map((p, i) => <span key={i} className={cx("rounded px-1.5 py-px text-2xs", p.k === "related" ? "bg-brand-50 text-brand-800" : "bg-slate-100 text-slate-700")}>{p.t}</span>)}</div>
          {(serp.related_questions ?? []).length > 0 && <ul className="mt-2 list-disc pl-4 text-xs text-slate-700">{serp.related_questions.slice(0, 8).map((q: any, i: number) => <li key={i}>{q.url ? <a className="hover:underline" href={q.url} target="_blank" rel="noreferrer noopener">{q.question}</a> : q.question}</li>)}</ul>}
        </section>}
        {local.length > 0 && <section aria-label="Local businesses">
          <h4 className="mb-1.5 text-xs font-medium text-slate-500">Local businesses ({local.length})</h4>
          <ul className="grid gap-1 sm:grid-cols-2">{local.slice(0, 12).map((l, i) => (
            <li key={i} className="text-xs"><span className="font-medium text-slate-800">{l.title}</span>
              {l.rating != null && <span className="text-slate-500"> · {l.rating}★{l.reviews != null ? ` (${l.reviews})` : ""}</span>}
              <span className="block text-2xs text-slate-400">{l.address}{l.address ? " · " : ""}{l.source_engine}</span></li>))}</ul>
        </section>}
        {(serp.ad_creatives ?? []).length > 0 && <section aria-label="Ads Transparency Center">
          <h4 className="mb-1.5 text-xs font-medium text-slate-500">Ads Transparency Center ({serp.ad_creatives.length} creatives)</h4>
          <ul className="space-y-0.5 text-xs">{serp.ad_creatives.slice(0, 10).map((c: any, i: number) => <li key={i}>{c.advertiser} · {c.format}{c.total_days_shown ? ` · shown ${c.total_days_shown} days` : ""}{c.details_link ? <> · <a className="text-brand-700 hover:underline" href={c.details_link} target="_blank" rel="noreferrer noopener">details</a></> : null}</li>)}</ul>
        </section>}
      </div>
    </Card>
  );
}
