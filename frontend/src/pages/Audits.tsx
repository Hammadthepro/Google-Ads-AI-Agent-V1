import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api/client";
import { useAuth } from "../lib/auth";
import { useAction, useBusinesses } from "../lib/hooks";
import { Badge, Button, Card, cx, DataSourceBadge, dt, EmptyState, ErrorBox, JsonBlock, MetricStrip, Notice, PageHeader, Select, since, Skeleton, Spinner, StatusBadge, Table, useDialogKeys } from "../components/ui";
import { Icon } from "../components/icons";
import { ErrorBoundary } from "../components/ErrorBoundary";
import { LiveFindings } from "../components/research/LiveFindings";
import type { SceneKind } from "../components/research/sceneLayout";

const TYPE_LABEL: Record<string, string> = { WEBSITE: "Website", GBP: "Business presence", COMPETITOR: "Competitors", KEYWORD: "Keyword research", ACCOUNT: "Account" };
const TYPE_ICON: Record<string, string> = { WEBSITE: "globe", GBP: "pin", COMPETITOR: "target", KEYWORD: "key", ACCOUNT: "accounts" };
const TYPE_SCENE: Record<string, SceneKind> = { WEBSITE: "website", GBP: "local", COMPETITOR: "competitors", KEYWORD: "keywords" };
const STATE_TONE: Record<string, string> = { usable: "green", outdated: "amber", insufficient: "amber", missing: "slate", running: "blue" };
const ACTIVE = ["QUEUED", "RUNNING"];

const duration = (a: any) => {
  if (!a.finished_at || !a.started_at) return null;
  const s = Math.round((new Date(a.finished_at).getTime() - new Date(a.started_at).getTime()) / 1000);
  return s < 90 ? `${s}s` : `${Math.round(s / 60)} min`;
};

function TypeLabel({ type }: { type: string }) {
  return (
    <span className="inline-flex items-center gap-2 whitespace-nowrap font-medium text-slate-900">
      <span className="flex h-6 w-6 items-center justify-center rounded-md bg-slate-100 text-slate-600"><Icon name={TYPE_ICON[type] ?? "research"} className="h-3.5 w-3.5" /></span>
      {TYPE_LABEL[type] ?? type}
    </span>
  );
}

/** Keyword research never starts other audits by itself: this dialog shows what exists and asks which path to take. */
function KeywordChoice({ businessId, onClose, onStarted }: { businessId: string; onClose: () => void; onStarted: (rows: any[]) => void }) {
  const src = useQuery({ queryKey: ["kw-sources", businessId], queryFn: () => api.get(`/businesses/${businessId}/keyword-research/sources`) });
  const [mode, setMode] = useState<"reuse" | "refresh">("reuse");
  const [refresh, setRefresh] = useState<string[] | null>(null);
  const start = useAction(() => api.post("/audits", { business_id: businessId, audit_type: "KEYWORD", keyword_mode: mode, refresh: mode === "refresh" ? picked : [],
    confirm: mode === "refresh", background: true }), [["audits"]]);
  const sources: any[] = src.data?.sources ?? [];
  const picked = refresh ?? sources.filter((s) => s.state !== "usable").map((s) => s.type);
  const toggle = (t: string) => setRefresh(picked.includes(t) ? picked.filter((x) => x !== t) : [...picked, t]);
  const ref = useDialogKeys(true, onClose);
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-ink/45 sm:items-center sm:p-4" role="dialog" aria-modal="true" aria-label="Keyword research"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div ref={ref} className="flex max-h-[92vh] w-full max-w-xl animate-dialog-in flex-col rounded-t-xl bg-white shadow-pop sm:rounded-xl">
        <div className="overflow-y-auto px-5 pt-5">
          <h3 className="text-base font-semibold text-slate-900">Keyword research</h3>
          <p className="mt-1 text-[13px] leading-5 text-slate-600">Keyword research builds on the website, business presence and competitor research. Nothing starts until you choose.</p>
          <div className="mt-3"><ErrorBox error={src.error || start.error} /></div>
          {src.isLoading ? <Spinner /> : (<>
            <ul className="mt-3 divide-y divide-slate-100 rounded-lg border border-slate-200" aria-label="Available research">
              {sources.map((s) => (
                <li key={s.type} className="flex items-start justify-between gap-3 px-3.5 py-2.5 text-[13px]">
                  <div className="min-w-0"><div className="font-medium text-slate-800">{s.label}</div><div className="text-xs text-slate-500">{s.reason}{s.finished_at ? ` · ${dt(s.finished_at)}` : ""}</div></div>
                  <Badge tone={STATE_TONE[s.state] ?? "slate"} dot>{s.state}</Badge>
                </li>))}
            </ul>
            <fieldset className="mt-4 space-y-2 text-[13px]">
              <legend className="mb-2 text-xs font-medium text-slate-700">How to proceed</legend>
              <label className={cx("flex cursor-pointer gap-3 rounded-lg border p-3.5 transition-colors", mode === "reuse" ? "border-brand-500 bg-brand-50/50" : "border-slate-200 hover:border-slate-300")}>
                <input type="radio" name="kwmode" className="mt-0.5 accent-brand-600" checked={mode === "reuse"} onChange={() => setMode("reuse")} />
                <span><b className="font-medium text-slate-900">Use available data</b><span className="mt-0.5 block text-xs leading-5 text-slate-500">Run keyword research on the existing results{src.data && !src.data.all_usable ? `; missing or outdated sources are reported as gaps` : ""}. No other audit is started.</span></span></label>
              <label className={cx("flex cursor-pointer gap-3 rounded-lg border p-3.5 transition-colors", mode === "refresh" ? "border-brand-500 bg-brand-50/50" : "border-slate-200 hover:border-slate-300")}>
                <input type="radio" name="kwmode" className="mt-0.5 accent-brand-600" checked={mode === "refresh"} onChange={() => setMode("refresh")} />
                <span><b className="font-medium text-slate-900">Run fresh audits first</b><span className="mt-0.5 block text-xs leading-5 text-slate-500">Only the sources you tick are run again; the rest are reused.</span>
                  {mode === "refresh" && <span className="mt-2.5 flex flex-wrap gap-x-4 gap-y-2">{sources.map((s) => (
                    <label key={s.type} className="inline-flex items-center gap-1.5 text-xs text-slate-700"><input type="checkbox" className="accent-brand-600" checked={picked.includes(s.type)} onChange={() => toggle(s.type)} />{s.label}</label>))}</span>}
                </span></label>
            </fieldset>
          </>)}
        </div>
        <div className="mt-5 flex justify-end gap-2 rounded-b-xl border-t border-slate-100 bg-slate-50/70 px-5 py-3">
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={start.isPending || src.isLoading || (mode === "refresh" && picked.length === 0)}
            onClick={() => start.mutate({}, { onSuccess: (rows: any) => onStarted(rows) })}>
            {mode === "reuse" ? "Start keyword research" : `Run ${picked.length} audit(s), then keyword research`}</Button>
        </div>
      </div>
    </div>
  );
}

export function Audits() {
  const nav = useNavigate();
  const { can } = useAuth();
  const businesses = useBusinesses();
  const [biz, setBiz] = useState("");
  const [type, setType] = useState("ALL");
  const [choosing, setChoosing] = useState(false);
  const list = useQuery({ queryKey: ["audits"], queryFn: () => api.get("/audits"),
    refetchInterval: (q) => (((q.state.data as any[]) ?? []).some((a) => ACTIVE.includes(a.status)) ? 3000 : false) });
  const run = useAction(() => api.post("/audits", { business_id: biz, audit_type: type, background: type !== "ACCOUNT" }), [["audits"]]);
  const opened = (rows: any[]) => { setChoosing(false); const target = rows.find((r) => r.audit_type === "KEYWORD") ?? rows[0]; if (target) nav(`/audits/${target.id}`); };
  const rows: any[] = list.data ?? [];
  const running = rows.filter((a) => ACTIVE.includes(a.status));
  const bizName = (id: string) => businesses.data?.find((b: any) => b.id === id)?.name ?? "—";
  return (
    <div className="space-y-8">
      <PageHeader title="Research & audits" subtitle="Run website, business presence and competitor audits, or keyword research on the research you already have." />
      {can("audits.run") && (
        <section aria-label="Run an audit" className="rounded-lg border border-slate-200 bg-white">
          <div className="flex flex-wrap items-end gap-3 p-4">
            <label className="min-w-[220px] flex-1 sm:max-w-xs"><span className="mb-1.5 block text-xs font-medium text-slate-700">Business</span>
              <Select aria-label="Business" value={biz} onChange={(e) => setBiz(e.target.value)} options={[{ value: "", label: "Choose a business…" }, ...(businesses.data ?? []).map((b: any) => ({ value: b.id, label: `${b.name} (${b.client_name})` }))]} /></label>
            <label className="min-w-[220px] flex-1 sm:max-w-sm"><span className="mb-1.5 block text-xs font-medium text-slate-700">Research</span>
              <Select aria-label="Audit type" value={type} onChange={(e) => setType(e.target.value)} options={[
                { value: "ALL", label: "Website + business presence + competitors" }, { value: "WEBSITE", label: "Website only" }, { value: "GBP", label: "Business presence only" },
                { value: "COMPETITOR", label: "Competitors only" }, { value: "KEYWORD", label: "Keyword research…" }, { value: "ACCOUNT", label: "Account audit (90 days, read-only)" }]} /></label>
            <Button variant="primary" disabled={!biz || run.isPending} onClick={() => (type === "KEYWORD" ? setChoosing(true) : run.mutate({}, { onSuccess: (rows: any) => opened(rows) }))}>
              {run.isPending ? "Starting…" : type === "KEYWORD" ? "Choose sources…" : "Run"}</Button>
          </div>
          <p className="border-t border-slate-100 px-4 py-2.5 text-xs text-slate-500">
            {type === "ALL" ? "Starts three audits that run side by side; each opens its own live view." : type === "KEYWORD" ? "You choose which existing research to reuse before anything runs."
              : type === "ACCOUNT" ? "Reads 90 days of the linked Google Ads account. Nothing is changed." : "Findings appear live while the audit runs."}
          </p>
          {run.error ? <div className="px-4 pb-4"><ErrorBox error={run.error} /></div> : null}
        </section>
      )}
      {choosing && biz && <KeywordChoice businessId={biz} onClose={() => setChoosing(false)} onStarted={opened} />}

      {running.length > 0 && (
        <section aria-label="Running now">
          <h2 className="mb-2.5 flex items-center gap-2 text-sm font-semibold text-slate-900"><span className="h-2 w-2 rounded-full bg-brand-500 motion-safe:animate-pulse" />Running now</h2>
          <ul className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
            {running.map((a) => (
              <li key={a.id}><Link to={`/audits/${a.id}`} className="flex items-center justify-between gap-3 rounded-lg border border-brand-200 bg-white px-4 py-3 transition-colors hover:border-brand-400">
                <div className="min-w-0"><TypeLabel type={a.audit_type} /><div className="mt-1 truncate pl-8 text-xs text-slate-500">{bizName(a.business_id)} · started {since(a.started_at)}</div></div>
                <StatusBadge value={a.status} /></Link></li>))}
          </ul>
        </section>)}

      <Card title="Audit history" flush description={rows.length ? `${rows.length} audit${rows.length === 1 ? "" : "s"}, newest first` : undefined}>
        {list.isLoading ? <div className="p-5"><Skeleton lines={5} /></div> : (
          <div className="px-5">
            <Table rows={rows} onRowClick={(a) => nav(`/audits/${a.id}`)} empty="No audits have been run yet." columns={[
              { key: "audit_type", label: "Type", render: (a) => <TypeLabel type={a.audit_type} /> },
              { key: "business", label: "Business", render: (a) => bizName(a.business_id) },
              { key: "status", label: "Status", render: (a) => <StatusBadge value={a.status} /> },
              { key: "summary", label: "Summary", className: "max-w-md text-xs leading-5 text-slate-600", render: (a) => a.summary || (ACTIVE.includes(a.status) ? "In progress…" : a.error ? <span className="text-rose-700">{a.error}</span> : "—") },
              { key: "started_at", label: "Started", render: (a) => <span title={dt(a.started_at)} className="whitespace-nowrap">{since(a.started_at)}</span> },
              { key: "took", label: "Took", className: "text-right", render: (a) => <span className="tabular whitespace-nowrap text-slate-500">{duration(a) ?? "—"}</span> },
              { key: "source_type", label: "Data", render: (a) => (ACTIVE.includes(a.status) ? <Badge>Pending</Badge> : <DataSourceBadge source={a.source_type} />) },
            ]} />
          </div>)}
      </Card>
    </div>
  );
}

/* ---------------------------------------------------------------------------------------------------------- results */
const Muted = ({ children }: { children: React.ReactNode }) => <span className="text-slate-400">{children}</span>;

function Website({ r }: { r: any }) {
  const ps = (k: "mobile" | "desktop") => r.pagespeed?.[k]?.performance_score ?? r.pagespeed?.[k]?.error ?? "—";
  return (
    <div className="space-y-6">
      <MetricStrip items={[
        { label: "Pages crawled", value: (r.page_inventory ?? []).length, sub: `robots.txt ${r.robots_found ? "found" : "missing"} · sitemap ${r.sitemap_urls}` },
        { label: "Mobile speed", value: ps("mobile"), sub: "PageSpeed" }, { label: "Desktop speed", value: ps("desktop"), sub: "PageSpeed" },
        { label: "SEO issues", value: (r.seo_issues ?? []).length },
      ]} />
      <Notice tone="amber">{r.tag_note} Detected: {(r.google_tags_detected ?? []).join(", ") || "none"} {(r.gtm_detected ?? []).length ? `· GTM ${r.gtm_detected.join(", ")}` : ""}</Notice>
      <div className="grid gap-6 lg:grid-cols-2">
        <Card title="Service inventory (from site)">
          <Table rows={r.service_inventory ?? []} columns={[{ key: "name", label: "Service" }, { key: "page_url", label: "Page", className: "break-all font-mono text-xs" }]} />
          {(r.services_not_in_profile ?? []).length > 0 && <div className="mt-3"><Notice tone="amber">On the site but not in the profile: {r.services_not_in_profile.join(", ")}</Notice></div>}
          {(r.services_without_page ?? []).length > 0 && <div className="mt-2"><Notice tone="red">In the profile but no dedicated page (client fix): {r.services_without_page.join(", ")}</Notice></div>}
        </Card>
        <Card title="Landing page content scores">
          <Table rows={r.content_scores ?? []} columns={[{ key: "service", label: "Service" }, { key: "score", label: "Score /100", className: "text-right" },
            { key: "notes", label: "Notes", render: (c) => <span className="text-xs">{(c.notes ?? []).join("; ") || "good"}</span> }]} />
        </Card>
      </div>
      <Card title="Recommendations & claims">
        <ul className="prose-read">{(r.landing_page_recommendations ?? []).map((x: string) => <li key={x}>{x}</li>)}</ul>
        {(r.claims_not_in_verified_list ?? []).length > 0 && <div className="mt-3"><Notice tone="amber">Claims on the site that are not in the verified list (ads cannot use them): {r.claims_not_in_verified_list.join(" · ")}</Notice></div>}
        <div className="mt-4 text-xs text-slate-500">Keyword seeds: {(r.keyword_seeds ?? []).join(", ") || "none"} · Schema: {(r.schema_types ?? []).join(", ") || "none"}</div>
      </Card>
      <Card title="Technical & SEO issues"><Table rows={r.seo_issues ?? []} columns={[{ key: "url", label: "URL", className: "break-all font-mono text-xs" }, { key: "issue", label: "Issue" }]} empty="No issues found." /></Card>
      <Card title="Page inventory"><Table rows={r.page_inventory ?? []} columns={[{ key: "url", label: "URL", className: "break-all font-mono text-xs" }, { key: "title", label: "Title" }, { key: "words", label: "Words", className: "text-right" }, { key: "forms", label: "Forms", className: "text-right" }]} /></Card>
    </div>
  );
}

function Gbp({ r }: { r: any }) {
  const rows = [r.client && { ...r.client, who: "Client" }, ...(r.competitors ?? []).filter(Boolean).map((c: any) => ({ ...c, who: "Competitor" }))].filter(Boolean);
  return (
    <div className="space-y-6">
      {r.client?.simulated && <Notice tone="violet">Business listings and reviews below come from a test fixture, not Google Places.</Notice>}
      <Card title="Listings compared" flush><div className="px-5"><Table rows={rows} columns={[{ key: "who", label: "", render: (x) => <Badge tone={x.who === "Client" ? "blue" : "slate"}>{x.who}</Badge> }, { key: "name", label: "Business" }, { key: "rating", label: "Rating", className: "text-right" },
        { key: "user_rating_count", label: "Reviews", className: "text-right" }, { key: "primary_category", label: "Category" }, { key: "photo_count", label: "Photos (sample)", className: "text-right" }, { key: "phone", label: "Phone" }]} /></div></Card>
      {(r.findings ?? []).length > 0 && <Card title="Findings"><ul className="prose-read">{(r.findings ?? []).map((f: string) => <li key={f}>{f}</li>)}</ul></Card>}
      <GeoSummary geo={r.geo} />
      <p className="text-xs text-slate-500">{r.note}</p>
      {r.client?.reviews?.length > 0 && <Card title="Recent reviews (sample)"><ul className="space-y-3 text-[13px]">{r.client.reviews.map((x: any, i: number) => <li key={i} className="flex gap-3"><Badge>{x.rating}★</Badge><div><p className="text-slate-700">{x.text}</p><p className="text-xs text-slate-400">{x.author}</p></div></li>)}</ul></Card>}
    </div>
  );
}

function Competitor({ r }: { r: any }) {
  if (r.skipped) return <Notice>{r.reason}</Notice>;
  return (
    <div className="space-y-6">
      {(r.competitors ?? []).length > 0 && (
        <div className="overflow-hidden rounded-lg border border-slate-200 bg-white">
          {(r.competitors ?? []).map((c: any, i: number) => (
            <section key={c.name} className={cx("grid gap-x-8 gap-y-2 px-5 py-4 lg:grid-cols-[220px_minmax(0,1fr)]", i > 0 && "border-t border-slate-100")}>
              <h3 className="text-sm font-semibold text-slate-900">{c.name}</h3>
              <dl className="space-y-1.5 text-[13px]">
                <div><dt className="inline font-medium text-slate-800">Services: </dt><dd className="inline text-slate-600">{c.services?.join(", ") || "—"}</dd></div>
                {c.services_client_lacks?.length > 0 && <div className="text-amber-900"><dt className="inline font-medium">Client lacks: </dt><dd className="inline">{c.services_client_lacks.join(", ")}</dd></div>}
                <div><dt className="inline font-medium text-slate-800">Offers on site: </dt><dd className="inline text-slate-600">{c.offers_on_site?.join(" · ") || "—"}</dd></div>
                <div className="text-xs text-slate-500"><dt className="inline font-medium">Keyword ideas (what their site is about, not what they bid on): </dt><dd className="inline">{c.keyword_ideas?.join(", ") || "—"}</dd></div>
              </dl>
            </section>))}
        </div>)}
      {r.pasted_ads_analysis && <Card title="Pasted competitor ads"><Notice tone="amber">{r.pasted_ads_analysis.verification}</Notice>
        <div className="mt-3 text-[13px]"><b>Offers:</b> {(r.pasted_ads_analysis.offers ?? []).join(" · ")}</div><div className="text-[13px]"><b>Angles:</b> {(r.pasted_ads_analysis.angles ?? []).join(" · ")}</div></Card>}
      {(r.web_candidates ?? []).length > 0 && <Card title="Suggested by web search (not verified)"><ul className="prose-read">{r.web_candidates.map((w: any) => <li key={w.website_url}><a className="text-brand-700 hover:underline" href={w.website_url} target="_blank" rel="noreferrer noopener">{w.name}</a></li>)}</ul></Card>}
      <p className="text-xs text-slate-500">Sources: {(r.keyword_sources ?? []).join(", ") || "website crawl"}. Missing: {(r.missing_sources ?? []).join(", ") || "none"}</p>
    </div>
  );
}

function Keywords({ r }: { r: any }) {
  return (
    <div className="space-y-5">
      <Notice>{r.match_type_policy}</Notice>
      {(r.overlaps ?? []).length > 0 && <Notice tone="amber">Overlaps: {r.overlaps.map((o: any) => `${o.keyword} (${o.ad_groups.join(" / ")})`).join("; ")}</Notice>}
      <KeywordInputs inputs={r.inputs} />
      <Card title="Keywords" flush description={(r.keywords ?? []).length > 150 ? `First 150 of ${(r.keywords ?? []).length}` : undefined}>
        <div className="px-5"><Table rows={(r.keywords ?? []).slice(0, 150)} columns={[{ key: "keyword", label: "Keyword", className: "font-medium text-slate-900" }, { key: "label", label: "Label", render: (k) => <StatusBadge value={k.label === "IRRELEVANT" ? "FAIL" : k.label === "SERVICE" ? "PASS" : "WARN"} /> },
          { key: "ad_group", label: "Ad group" }, { key: "match_types", label: "Match", render: (k) => (k.match_types ?? []).join(" + ") || "negative" },
          { key: "intent", label: "Intent" }, { key: "relevance_score", label: "Relevance (app score)", className: "text-right" },
          { key: "avg_monthly_searches", label: "Searches/mo", className: "text-right", render: (k) => k.avg_monthly_searches ?? <Muted>Not available</Muted> }, { key: "bid_range", label: "Bid range" }, { key: "competition", label: "Competition" },
          { key: "selected", label: "Selected" }, { key: "labelled_by", label: "Labelled by" }]} /></div>
      </Card>
      <div className="text-[13px]"><b className="font-medium">Negative ideas:</b> <span className="text-slate-600">{(r.negative_ideas ?? []).join(", ") || "none"}</span></div>
    </div>
  );
}

function GeoSummary({ geo }: { geo: any }) {
  if (!geo) return null;
  if (!geo.available) return <Notice tone="amber">Local market data unavailable: {geo.reason}</Notice>;
  return (
    <Card title="Local market (map data)">
      <p className="text-[13px] text-slate-700">{geo.density?.total ?? 0} same-category business(es) within {Math.round((geo.radius_m ?? 0) / 1609)} miles of {geo.center?.display_name}.</p>
      <ul className="mt-3 grid gap-x-6 gap-y-1 text-[13px] sm:grid-cols-2 lg:grid-cols-3">{(geo.nearby ?? []).slice(0, 12).map((p: any) => <li key={p.name + p.lat} className="flex justify-between gap-2 border-b border-slate-100 py-1"><span className="truncate">{p.name}</span><span className="tabular shrink-0 text-xs text-slate-500">{p.distance_km} km</span></li>)}</ul>
      <p className="mt-3 text-2xs text-slate-500">{(geo.limitations ?? []).join(" ")} {(geo.attribution ?? []).join(" · ")}</p>
    </Card>
  );
}

function KeywordInputs({ inputs }: { inputs: any }) {
  if (!inputs) return null;
  return (
    <div className="flex flex-wrap gap-2 text-xs" aria-label="Research used">
      {Object.entries(inputs as Record<string, any>).map(([k, v]) => (
        <span key={k} className={cx("rounded-md px-2.5 py-1 ring-1 ring-inset", v ? "bg-white text-slate-700 ring-slate-200" : "bg-amber-50 text-amber-900 ring-amber-200")}>
          {TYPE_LABEL[k] ?? k}: {v ? (v.fresh ? "run fresh" : <>reused <Link className="underline" to={`/audits/${v.audit_id}`}>{dt(v.finished_at)}</Link></>) : "not available"}
        </span>))}
    </div>
  );
}

function Account({ r }: { r: any }) {
  return (
    <div className="space-y-5">
      {(r.findings ?? []).length > 0 && <Card title="Findings"><ul className="prose-read">{(r.findings ?? []).map((f: string) => <li key={f}>{f}</li>)}</ul></Card>}
      <Card title="Campaigns (90 days)" flush><div className="px-5"><Table rows={r.campaigns ?? []} columns={[{ key: "campaign", label: "Campaign", className: "font-medium text-slate-900" }, { key: "type", label: "Type" }, { key: "clicks", label: "Clicks", className: "text-right" }, { key: "cost", label: "Cost", className: "text-right" },
        { key: "conversions", label: "Conv.", className: "text-right" }, { key: "cost_per_conversion", label: "CPA", className: "text-right" }, { key: "budget_lost_is", label: "Lost IS (budget)", className: "text-right" }, { key: "rank_lost_is", label: "Lost IS (rank)", className: "text-right" }]} /></div></Card>
      <div className="text-[13px]"><b className="font-medium">Wasted spend (90 days):</b> ${r.wasted_spend}</div>
    </div>
  );
}

/* ------------------------------------------------------------------------------------------------------------ detail */
const STATE_COPY: Record<string, string> = {
  QUEUED: "Queued — waiting for the worker to start this audit.", RUNNING: "Running — findings appear below as they are collected.",
  SKIPPED: "Skipped — there was nothing to audit.", CANCELLED: "Cancelled.",
};

/** Where the audit is in its lifecycle. Each step is lit only from the audit's real status. */
function Lifecycle({ status }: { status: string }) {
  const end = status === "FAILED" ? "Failed" : status === "CANCELLED" ? "Cancelled" : status === "SKIPPED" ? "Skipped" : "Completed";
  const idx = status === "QUEUED" ? 0 : status === "RUNNING" ? 1 : 2;
  const steps = ["Queued", "Running", end];
  const bad = ["FAILED", "CANCELLED"].includes(status);
  return (
    <ol className="flex items-center gap-2 text-xs" aria-label="Audit lifecycle">
      {steps.map((s, i) => {
        const done = i < idx || (i === idx && i === 2), now = i === idx && i < 2;
        return (
          <li key={s} className="flex items-center gap-2" aria-current={i === idx ? "step" : undefined}>
            {i > 0 && <span aria-hidden className={cx("h-px w-6 sm:w-10", i <= idx ? "bg-slate-400" : "bg-slate-200")} />}
            <span className={cx("inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 font-medium",
              done && i === 2 && bad ? "bg-rose-50 text-rose-800" : done ? "bg-slate-100 text-slate-700" : now ? "bg-brand-50 text-brand-800 ring-1 ring-inset ring-brand-200" : "text-slate-400")}>
              <span aria-hidden className={cx("h-1.5 w-1.5 rounded-full", done && i === 2 && bad ? "bg-rose-500" : done ? (i === 2 ? "bg-emerald-500" : "bg-slate-400") : now ? "bg-brand-500 motion-safe:animate-pulse" : "bg-slate-300")} />
              {s}
            </span>
          </li>);
      })}
    </ol>
  );
}

export function AuditDetail() {
  const { id } = useParams();
  const nav = useNavigate();
  const q = useQuery({ queryKey: ["audit", id], queryFn: () => api.get(`/audits/${id}`),
    refetchInterval: (query) => (ACTIVE.includes((query.state.data as any)?.status) ? 1500 : false) });
  const retry = useAction(() => api.post("/audits", { business_id: q.data.business_id, audit_type: q.data.audit_type, background: true }), [["audits"]]);
  if (q.error) return <div className="space-y-4"><PageHeader title="Audit" /><ErrorBox error={q.error} /><Link className="text-[13px] font-medium text-brand-700 hover:underline" to="/audits">Back to Research & audits</Link></div>;
  if (q.isLoading || !q.data) return <div><Skeleton lines={3} /><div className="mt-6 h-[420px] rounded-lg bg-ink" /></div>;
  const a = q.data;
  const r = a.result || {};
  const active = ACTIVE.includes(a.status);
  const View = ({ WEBSITE: Website, GBP: Gbp, COMPETITOR: Competitor, KEYWORD: Keywords, ACCOUNT: Account } as Record<string, any>)[a.audit_type];
  const hasResult = r && Object.keys(r).length > 0 && a.status !== "FAILED";
  const secs = duration(a);
  return (
    <div className="space-y-6">
      <PageHeader title={`${TYPE_LABEL[a.audit_type] ?? a.audit_type} audit`}
        subtitle={<>{a.business ? <Link to={`/businesses/${a.business_id}`} className="font-medium text-brand-700 hover:underline">{a.business.name}</Link> : "Business"}
          {" · started "}{dt(a.started_at)}{a.finished_at ? ` · finished ${dt(a.finished_at)}${secs !== null ? ` (${secs})` : ""}` : ""}</>}
        meta={<Lifecycle status={a.status} />}
        actions={<><StatusBadge value={a.status} />{active ? <Badge>Data pending</Badge> : <DataSourceBadge source={a.source_type} />}</>} />
      {STATE_COPY[a.status] && <Notice tone={a.status === "SKIPPED" || a.status === "CANCELLED" ? "amber" : "blue"}>{STATE_COPY[a.status]}{a.status === "SKIPPED" && a.summary ? ` ${a.summary}` : ""}</Notice>}
      {a.status === "FAILED" && (
        <div role="alert" className="rounded-lg border border-rose-200 bg-rose-50/70 px-5 py-4 text-[13px] text-rose-950">
          <div className="flex items-center gap-2 font-semibold"><Icon name="alert" className="h-4 w-4 text-rose-600" />This audit failed</div>
          <div className="mt-1 break-words pl-6">{a.error || "No error was recorded."}</div>
          {a.retryable && (a.audit_type === "KEYWORD"
            ? <p className="mt-2 pl-6 text-xs">To retry, start keyword research again from <Link className="underline" to="/audits">Research & audits</Link> and choose which sources to use.</p>
            : <div className="mt-3 pl-6"><Button size="sm" disabled={retry.isPending} onClick={() => retry.mutate({}, { onSuccess: (rows: any) => rows[0] && nav(`/audits/${rows[0].id}`) })}>{retry.isPending ? "Starting…" : "Retry this audit"}</Button></div>)}
          <div className="mt-2"><ErrorBox error={retry.error} /></div>
        </div>)}
      {a.audit_type !== "ACCOUNT" && <LiveFindings url={`/audits/${a.id}/events`} active={active} title={active ? "Live findings" : "Research activity"} kind={TYPE_SCENE[a.audit_type]} subject={{ website: a.business?.website_url, business: a.business?.name }} result={a.status === "COMPLETED" || a.status === "SKIPPED" ? a.result : undefined}
        audit={{ status: a.status, error: a.error, simulated: a.source_type === "MOCK" || !!r?.simulated || !!r?.client?.simulated }}
        steps={[{ key: a.audit_type.toLowerCase(), name: TYPE_LABEL[a.audit_type] ?? a.audit_type, status: a.status }]} />}
      {hasResult && (
        <ErrorBoundary label="The findings">
          <div className="space-y-6 pt-2">
            <div className="flex items-baseline justify-between gap-4 border-b border-slate-200 pb-3">
              <h2 className="text-base font-semibold text-slate-900">Results</h2>
              {a.finished_at && <span className="text-xs text-slate-500">Stored {dt(a.finished_at)}</span>}
            </div>
            {a.summary && <p className="max-w-read text-sm leading-6 text-slate-700"><b className="font-medium text-slate-900">Summary:</b> {a.summary}</p>}
            {View ? <View r={r} /> : <JsonBlock value={r} />}
          </div>
        </ErrorBoundary>)}
      {!hasResult && !active && a.status !== "FAILED" && <EmptyState message="This audit has no stored findings." />}
      <details className="rounded-lg border border-slate-200 bg-white px-4 py-2.5"><summary className="cursor-pointer text-xs font-medium text-slate-500 hover:text-slate-800">Raw result</summary><div className="mt-2"><JsonBlock value={r} /></div></details>
    </div>
  );
}
