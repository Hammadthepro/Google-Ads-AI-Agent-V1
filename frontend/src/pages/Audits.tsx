import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api/client";
import { useAuth } from "../lib/auth";
import { useAction, useBusinesses } from "../lib/hooks";
import { Badge, Button, Card, cx, DataSourceBadge, dt, EmptyState, ErrorBox, JsonBlock, Notice, PageHeader, Select, Spinner, Stat, StatusBadge, Table } from "../components/ui";
import { ErrorBoundary } from "../components/ErrorBoundary";
import { LiveFindings } from "../components/research/LiveFindings";

const TYPE_LABEL: Record<string, string> = { WEBSITE: "Website", GBP: "Business presence", COMPETITOR: "Competitors", KEYWORD: "Keyword research", ACCOUNT: "Account" };
const STATE_TONE: Record<string, string> = { usable: "green", outdated: "amber", insufficient: "amber", missing: "slate", running: "blue" };
const ACTIVE = ["QUEUED", "RUNNING"];

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
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4" role="dialog" aria-modal="true" aria-label="Keyword research">
      <div className="max-h-[90vh] w-full max-w-xl overflow-y-auto rounded-lg bg-white p-5 shadow-pop">
        <h3 className="text-base font-semibold text-slate-900">Keyword research</h3>
        <p className="mt-1 text-sm text-slate-600">Keyword research builds on the website, business presence and competitor research. Nothing starts until you choose.</p>
        <ErrorBox error={src.error || start.error} />
        {src.isLoading ? <Spinner /> : (<>
          <ul className="mt-3 divide-y divide-slate-100 rounded-lg border border-slate-200" aria-label="Available research">
            {sources.map((s) => (
              <li key={s.type} className="flex items-start justify-between gap-3 px-3 py-2 text-sm">
                <div className="min-w-0"><div className="font-medium text-slate-800">{s.label}</div><div className="text-xs text-slate-500">{s.reason}{s.finished_at ? ` · ${dt(s.finished_at)}` : ""}</div></div>
                <Badge tone={STATE_TONE[s.state] ?? "slate"}>{s.state}</Badge>
              </li>))}
          </ul>
          <fieldset className="mt-4 space-y-2 text-sm">
            <legend className="sr-only">How to proceed</legend>
            <label className="flex gap-2 rounded-lg border border-slate-200 p-3"><input type="radio" name="kwmode" checked={mode === "reuse"} onChange={() => setMode("reuse")} />
              <span><b>Use available data</b><span className="block text-xs text-slate-500">Run keyword research on the existing results{src.data && !src.data.all_usable ? `; missing or outdated sources are reported as gaps` : ""}. No other audit is started.</span></span></label>
            <label className="flex gap-2 rounded-lg border border-slate-200 p-3"><input type="radio" name="kwmode" checked={mode === "refresh"} onChange={() => setMode("refresh")} />
              <span><b>Run fresh audits first</b><span className="block text-xs text-slate-500">Only the sources you tick are run again; the rest are reused.</span>
                {mode === "refresh" && <span className="mt-2 flex flex-wrap gap-3">{sources.map((s) => (
                  <label key={s.type} className="inline-flex items-center gap-1.5 text-xs"><input type="checkbox" checked={picked.includes(s.type)} onChange={() => toggle(s.type)} />{s.label}</label>))}</span>}
              </span></label>
          </fieldset>
        </>)}
        <div className="mt-5 flex justify-end gap-2">
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
  return (
    <div className="space-y-6">
      <PageHeader title="Research & audits" subtitle="Run website, business presence and competitor audits, or keyword research on the research you already have." />
      {can("audits.run") && (
        <Card title="Run an audit">
          <div className="flex flex-wrap items-end gap-3">
            <div className="w-72"><Select aria-label="Business" value={biz} onChange={(e) => setBiz(e.target.value)} options={[{ value: "", label: "Choose a business…" }, ...(businesses.data ?? []).map((b: any) => ({ value: b.id, label: `${b.name} (${b.client_name})` }))]} /></div>
            <div className="w-64"><Select aria-label="Audit type" value={type} onChange={(e) => setType(e.target.value)} options={[
              { value: "ALL", label: "Website + business presence + competitors" }, { value: "WEBSITE", label: "Website only" }, { value: "GBP", label: "Business presence only" },
              { value: "COMPETITOR", label: "Competitors only" }, { value: "KEYWORD", label: "Keyword research…" }, { value: "ACCOUNT", label: "Account audit (90 days, read-only)" }]} /></div>
            <Button variant="primary" disabled={!biz || run.isPending} onClick={() => (type === "KEYWORD" ? setChoosing(true) : run.mutate({}, { onSuccess: (rows: any) => opened(rows) }))}>
              {run.isPending ? "Starting…" : type === "KEYWORD" ? "Choose sources…" : "Run"}</Button>
          </div>
          <div className="mt-2"><ErrorBox error={run.error} /></div>
        </Card>
      )}
      {choosing && biz && <KeywordChoice businessId={biz} onClose={() => setChoosing(false)} onStarted={opened} />}
      <Card title="Audit history">
        {list.isLoading ? <Spinner /> : (
          <Table rows={list.data ?? []} onRowClick={(a) => nav(`/audits/${a.id}`)} columns={[
            { key: "audit_type", label: "Type", render: (a) => <Badge tone="blue">{TYPE_LABEL[a.audit_type] ?? a.audit_type}</Badge> },
            { key: "business", label: "Business", render: (a) => businesses.data?.find((b: any) => b.id === a.business_id)?.name ?? "—" },
            { key: "status", label: "Status", render: (a) => <StatusBadge value={a.status} /> },
            { key: "summary", label: "Summary", className: "max-w-md text-xs", render: (a) => a.summary || (ACTIVE.includes(a.status) ? "In progress…" : "—") },
            { key: "started_at", label: "When", render: (a) => dt(a.started_at) },
            { key: "source_type", label: "Data", render: (a) => (ACTIVE.includes(a.status) ? <Badge>Pending</Badge> : <DataSourceBadge source={a.source_type} />) },
          ]} />
        )}
      </Card>
    </div>
  );
}

function Website({ r }: { r: any }) {
  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Pages crawled" value={(r.page_inventory ?? []).length} sub={`robots.txt ${r.robots_found ? "found" : "missing"} · sitemap ${r.sitemap_urls}`} />
        <Stat label="Mobile speed" value={r.pagespeed?.mobile?.performance_score ?? r.pagespeed?.mobile?.error ?? "—"} sub="PageSpeed" />
        <Stat label="Desktop speed" value={r.pagespeed?.desktop?.performance_score ?? r.pagespeed?.desktop?.error ?? "—"} />
        <Stat label="SEO issues" value={(r.seo_issues ?? []).length} />
      </div>
      <Notice tone="amber">{r.tag_note} Detected: {(r.google_tags_detected ?? []).join(", ") || "none"} {(r.gtm_detected ?? []).length ? `· GTM ${r.gtm_detected.join(", ")}` : ""}</Notice>
      <div className="grid gap-6 lg:grid-cols-2">
        <Card title="Service inventory (from site)">
          <Table rows={r.service_inventory ?? []} columns={[{ key: "name", label: "Service" }, { key: "page_url", label: "Page", className: "text-xs" }]} />
          {(r.services_not_in_profile ?? []).length > 0 && <div className="mt-3"><Notice tone="amber">On the site but not in the profile: {r.services_not_in_profile.join(", ")}</Notice></div>}
          {(r.services_without_page ?? []).length > 0 && <div className="mt-2"><Notice tone="red">In the profile but no dedicated page (client fix): {r.services_without_page.join(", ")}</Notice></div>}
        </Card>
        <Card title="Landing page content scores">
          <Table rows={r.content_scores ?? []} columns={[{ key: "service", label: "Service" }, { key: "score", label: "Score /100" },
            { key: "notes", label: "Notes", render: (c) => <span className="text-xs">{(c.notes ?? []).join("; ") || "good"}</span> }]} />
        </Card>
      </div>
      <Card title="Recommendations & claims">
        <ul className="list-disc space-y-1 pl-5 text-sm">{(r.landing_page_recommendations ?? []).map((x: string) => <li key={x}>{x}</li>)}</ul>
        {(r.claims_not_in_verified_list ?? []).length > 0 && <div className="mt-3"><Notice tone="amber">Claims on the site that are not in the verified list (ads cannot use them): {r.claims_not_in_verified_list.join(" · ")}</Notice></div>}
        <div className="mt-3 text-xs text-slate-500">Keyword seeds: {(r.keyword_seeds ?? []).join(", ") || "none"} · Schema: {(r.schema_types ?? []).join(", ") || "none"}</div>
      </Card>
      <Card title="Technical & SEO issues"><Table rows={r.seo_issues ?? []} columns={[{ key: "url", label: "URL", className: "text-xs" }, { key: "issue", label: "Issue" }]} empty="No issues found." /></Card>
      <Card title="Page inventory"><Table rows={r.page_inventory ?? []} columns={[{ key: "url", label: "URL", className: "text-xs" }, { key: "title", label: "Title" }, { key: "words", label: "Words" }, { key: "forms", label: "Forms" }]} /></Card>
    </div>
  );
}

function Gbp({ r }: { r: any }) {
  const rows = [r.client && { ...r.client, who: "Client" }, ...(r.competitors ?? []).filter(Boolean).map((c: any) => ({ ...c, who: "Competitor" }))].filter(Boolean);
  return (
    <div className="space-y-4">
      {r.client?.simulated && <Notice tone="violet">Business listings and reviews below come from a test fixture, not Google Places.</Notice>}
      <Table rows={rows} columns={[{ key: "who", label: "" }, { key: "name", label: "Business" }, { key: "rating", label: "Rating" },
        { key: "user_rating_count", label: "Reviews" }, { key: "primary_category", label: "Category" }, { key: "photo_count", label: "Photos (sample)" }, { key: "phone", label: "Phone" }]} />
      <ul className="list-disc pl-5 text-sm">{(r.findings ?? []).map((f: string) => <li key={f}>{f}</li>)}</ul>
      <GeoSummary geo={r.geo} />
      <p className="text-xs text-slate-500">{r.note}</p>
      {r.client?.reviews && <Card title="Recent reviews (sample)"><ul className="space-y-2 text-sm">{r.client.reviews.map((x: any, i: number) => <li key={i}><Badge>{x.rating}★</Badge> {x.text} <span className="text-xs text-slate-400">— {x.author}</span></li>)}</ul></Card>}
    </div>
  );
}

function Competitor({ r }: { r: any }) {
  if (r.skipped) return <Notice>{r.reason}</Notice>;
  return (
    <div className="space-y-4">
      {(r.competitors ?? []).map((c: any) => (
        <Card key={c.name} title={c.name}>
          <div className="text-sm"><b>Services:</b> {c.services?.join(", ") || "—"}</div>
          {c.services_client_lacks?.length > 0 && <div className="mt-1 text-sm text-amber-800"><b>Client lacks:</b> {c.services_client_lacks.join(", ")}</div>}
          <div className="mt-1 text-sm"><b>Offers on site:</b> {c.offers_on_site?.join(" · ") || "—"}</div>
          <div className="mt-1 text-xs text-slate-500"><b>Keyword ideas (what their site is about, not what they bid on):</b> {c.keyword_ideas?.join(", ") || "—"}</div>
        </Card>
      ))}
      {r.pasted_ads_analysis && <Card title="Pasted competitor ads"><Notice tone="amber">{r.pasted_ads_analysis.verification}</Notice>
        <div className="mt-2 text-sm"><b>Offers:</b> {(r.pasted_ads_analysis.offers ?? []).join(" · ")}</div><div className="text-sm"><b>Angles:</b> {(r.pasted_ads_analysis.angles ?? []).join(" · ")}</div></Card>}
      {(r.web_candidates ?? []).length > 0 && <Card title="Suggested by web search (not verified)"><ul className="list-disc pl-5 text-sm">{r.web_candidates.map((w: any) => <li key={w.website_url}><a className="text-brand-700 hover:underline" href={w.website_url} target="_blank" rel="noreferrer noopener">{w.name}</a></li>)}</ul></Card>}
      <p className="text-xs text-slate-500">Sources: {(r.keyword_sources ?? []).join(", ") || "website crawl"}. Missing: {(r.missing_sources ?? []).join(", ") || "none"}</p>
    </div>
  );
}

function Keywords({ r }: { r: any }) {
  return (
    <div className="space-y-4">
      <Notice>{r.match_type_policy}</Notice>
      {(r.overlaps ?? []).length > 0 && <Notice tone="amber">Overlaps: {r.overlaps.map((o: any) => `${o.keyword} (${o.ad_groups.join(" / ")})`).join("; ")}</Notice>}
      <KeywordInputs inputs={r.inputs} />
      <Table rows={(r.keywords ?? []).slice(0, 150)} columns={[{ key: "keyword", label: "Keyword" }, { key: "label", label: "Label", render: (k) => <StatusBadge value={k.label === "IRRELEVANT" ? "FAIL" : k.label === "SERVICE" ? "PASS" : "WARN"} /> },
        { key: "ad_group", label: "Ad group" }, { key: "match_types", label: "Match", render: (k) => (k.match_types ?? []).join(" + ") || "negative" },
        { key: "intent", label: "Intent" }, { key: "relevance_score", label: "Relevance (app score)" },
        { key: "avg_monthly_searches", label: "Searches/mo", render: (k) => k.avg_monthly_searches ?? <span className="text-slate-400">Not available</span> }, { key: "bid_range", label: "Bid range" }, { key: "competition", label: "Competition" },
        { key: "selected", label: "Selected" }, { key: "labelled_by", label: "Labelled by" }]} />
      <div className="text-sm"><b>Negative ideas:</b> {(r.negative_ideas ?? []).join(", ") || "none"}</div>
    </div>
  );
}

function GeoSummary({ geo }: { geo: any }) {
  if (!geo) return null;
  if (!geo.available) return <Notice tone="amber">Local market data unavailable: {geo.reason}</Notice>;
  return (
    <Card title="Local market (map data)">
      <p className="text-sm text-slate-700">{geo.density?.total ?? 0} same-category business(es) within {Math.round((geo.radius_m ?? 0) / 1609)} miles of {geo.center?.display_name}.</p>
      <ul className="mt-2 grid gap-1 text-sm sm:grid-cols-2">{(geo.nearby ?? []).slice(0, 12).map((p: any) => <li key={p.name + p.lat}>{p.name} <span className="text-xs text-slate-500">· {p.distance_km} km</span></li>)}</ul>
      <p className="mt-2 text-2xs text-slate-500">{(geo.limitations ?? []).join(" ")} {(geo.attribution ?? []).join(" · ")}</p>
    </Card>
  );
}

function KeywordInputs({ inputs }: { inputs: any }) {
  if (!inputs) return null;
  return (
    <div className="flex flex-wrap gap-2 text-xs" aria-label="Research used">
      {Object.entries(inputs as Record<string, any>).map(([k, v]) => (
        <span key={k} className={cx("rounded-full px-2.5 py-1 ring-1 ring-inset", v ? "bg-white text-slate-700 ring-slate-200" : "bg-amber-50 text-amber-900 ring-amber-200")}>
          {TYPE_LABEL[k] ?? k}: {v ? (v.fresh ? "run fresh" : <>reused <Link className="underline" to={`/audits/${v.audit_id}`}>{dt(v.finished_at)}</Link></>) : "not available"}
        </span>))}
    </div>
  );
}

function Account({ r }: { r: any }) {
  return (
    <div className="space-y-4">
      <ul className="list-disc pl-5 text-sm">{(r.findings ?? []).map((f: string) => <li key={f}>{f}</li>)}</ul>
      <Table rows={r.campaigns ?? []} columns={[{ key: "campaign", label: "Campaign" }, { key: "type", label: "Type" }, { key: "clicks", label: "Clicks" }, { key: "cost", label: "Cost" },
        { key: "conversions", label: "Conv." }, { key: "cost_per_conversion", label: "CPA" }, { key: "budget_lost_is", label: "Lost IS (budget)" }, { key: "rank_lost_is", label: "Lost IS (rank)" }]} />
      <div className="text-sm"><b>Wasted spend (90 days):</b> ${r.wasted_spend}</div>
    </div>
  );
}

const STATE_COPY: Record<string, string> = {
  QUEUED: "Queued — waiting for the worker to start this audit.", RUNNING: "Running — findings appear below as they are collected.",
  SKIPPED: "Skipped — there was nothing to audit.", CANCELLED: "Cancelled.",
};

export function AuditDetail() {
  const { id } = useParams();
  const nav = useNavigate();
  const q = useQuery({ queryKey: ["audit", id], queryFn: () => api.get(`/audits/${id}`),
    refetchInterval: (query) => (ACTIVE.includes((query.state.data as any)?.status) ? 1500 : false) });
  const retry = useAction(() => api.post("/audits", { business_id: q.data.business_id, audit_type: q.data.audit_type, background: true }), [["audits"]]);
  if (q.error) return <div className="space-y-4"><PageHeader title="Audit" /><ErrorBox error={q.error} /><Link className="text-sm text-brand-700" to="/audits">Back to Research & audits</Link></div>;
  if (q.isLoading || !q.data) return <Spinner />;
  const a = q.data;
  const r = a.result || {};
  const active = ACTIVE.includes(a.status);
  const View = ({ WEBSITE: Website, GBP: Gbp, COMPETITOR: Competitor, KEYWORD: Keywords, ACCOUNT: Account } as Record<string, any>)[a.audit_type];
  const hasResult = r && Object.keys(r).length > 0 && a.status !== "FAILED";
  const secs = a.finished_at ? Math.round((new Date(a.finished_at).getTime() - new Date(a.started_at).getTime()) / 1000) : null;
  return (
    <div className="space-y-6">
      <PageHeader title={`${TYPE_LABEL[a.audit_type] ?? a.audit_type} audit`}
        subtitle={<>{a.business ? <Link to={`/businesses/${a.business_id}`} className="text-brand-700">{a.business.name}</Link> : "Business"}
          {" · started "}{dt(a.started_at)}{a.finished_at ? ` · finished ${dt(a.finished_at)}${secs !== null ? ` (${secs < 90 ? `${secs}s` : `${Math.round(secs / 60)} min`})` : ""}` : ""}</>}
        actions={<><StatusBadge value={a.status} />{active ? <Badge>Data pending</Badge> : <DataSourceBadge source={a.source_type} />}</>} />
      {STATE_COPY[a.status] && <Notice tone={a.status === "SKIPPED" || a.status === "CANCELLED" ? "amber" : "blue"}>{STATE_COPY[a.status]}{a.status === "SKIPPED" && a.summary ? ` ${a.summary}` : ""}</Notice>}
      {a.status === "FAILED" && (
        <div role="alert" className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-900">
          <div className="font-semibold">This audit failed</div><div className="mt-1 break-words">{a.error || "No error was recorded."}</div>
          {a.retryable && (a.audit_type === "KEYWORD"
            ? <p className="mt-2 text-xs">To retry, start keyword research again from <Link className="underline" to="/audits">Research & audits</Link> and choose which sources to use.</p>
            : <div className="mt-2"><Button size="sm" disabled={retry.isPending} onClick={() => retry.mutate({}, { onSuccess: (rows: any) => rows[0] && nav(`/audits/${rows[0].id}`) })}>{retry.isPending ? "Starting…" : "Retry this audit"}</Button></div>)}
          <ErrorBox error={retry.error} />
        </div>)}
      {a.audit_type !== "ACCOUNT" && <LiveFindings url={`/audits/${a.id}/events`} active={active} title={active ? "Live findings" : "Research activity"}
        steps={[{ key: a.audit_type.toLowerCase(), name: TYPE_LABEL[a.audit_type] ?? a.audit_type, status: a.status }]} />}
      {hasResult && (
        <ErrorBoundary label="The findings">
          {a.summary && <p className="text-sm text-slate-700"><b>Summary:</b> {a.summary}</p>}
          {View ? <View r={r} /> : <JsonBlock value={r} />}
        </ErrorBoundary>)}
      {!hasResult && !active && a.status !== "FAILED" && <EmptyState message="This audit has no stored findings." />}
      <details><summary className="cursor-pointer text-xs text-slate-500">Raw result</summary><JsonBlock value={r} /></details>
    </div>
  );
}
