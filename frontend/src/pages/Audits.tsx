import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api/client";
import { useAuth } from "../lib/auth";
import { useAction, useBusinesses } from "../lib/hooks";
import { Badge, Button, Card, DataSourceBadge, dt, ErrorBox, JsonBlock, Notice, PageHeader, Select, Spinner, Stat, StatusBadge, Table } from "../components/ui";

export function Audits() {
  const nav = useNavigate();
  const { can } = useAuth();
  const businesses = useBusinesses();
  const [biz, setBiz] = useState("");
  const [type, setType] = useState("ALL");
  const list = useQuery({ queryKey: ["audits"], queryFn: () => api.get("/audits") });
  const run = useAction(() => api.post("/audits", { business_id: biz, audit_type: type }), [["audits"]]);
  return (
    <div className="space-y-6">
      <PageHeader title="Audits" subtitle="Website, Google Business Profile and competitor audits run in parallel; keyword research uses their outputs." />
      {can("audits.run") && (
        <Card title="Run an audit">
          <div className="flex flex-wrap items-end gap-3">
            <div className="w-72"><Select value={biz} onChange={(e) => setBiz(e.target.value)} options={[{ value: "", label: "Choose a business…" }, ...(businesses.data ?? []).map((b: any) => ({ value: b.id, label: `${b.name} (${b.client_name})` }))]} /></div>
            <div className="w-64"><Select value={type} onChange={(e) => setType(e.target.value)} options={[
              { value: "ALL", label: "Website + GBP + Competitors" }, { value: "WEBSITE", label: "Website only" }, { value: "GBP", label: "Business profile only" },
              { value: "COMPETITOR", label: "Competitors only" }, { value: "KEYWORD", label: "Keyword research" }, { value: "ACCOUNT", label: "Account audit (90 days, read-only)" }]} /></div>
            <Button variant="primary" disabled={!biz || run.isPending} onClick={() => run.mutate({})}>{run.isPending ? "Running…" : "Run"}</Button>
          </div>
          <div className="mt-2"><ErrorBox error={run.error} /></div>
        </Card>
      )}
      <Card title="Audit history">
        {list.isLoading ? <Spinner /> : (
          <Table rows={list.data ?? []} onRowClick={(a) => nav(`/audits/${a.id}`)} columns={[
            { key: "audit_type", label: "Type", render: (a) => <Badge tone="blue">{a.audit_type}</Badge> },
            { key: "business", label: "Business", render: (a) => businesses.data?.find((b: any) => b.id === a.business_id)?.name ?? "—" },
            { key: "status", label: "Status", render: (a) => <StatusBadge value={a.status} /> },
            { key: "summary", label: "Summary", className: "max-w-md text-xs" },
            { key: "started_at", label: "When", render: (a) => dt(a.started_at) },
            { key: "source_type", label: "Data", render: (a) => <DataSourceBadge source={a.source_type} /> },
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
        <Stat label="Pages crawled" value={r.page_inventory.length} sub={`robots.txt ${r.robots_found ? "found" : "missing"} · sitemap ${r.sitemap_urls}`} />
        <Stat label="Mobile speed" value={r.pagespeed?.mobile?.performance_score ?? r.pagespeed?.mobile?.error ?? "—"} sub="PageSpeed" />
        <Stat label="Desktop speed" value={r.pagespeed?.desktop?.performance_score ?? r.pagespeed?.desktop?.error ?? "—"} />
        <Stat label="SEO issues" value={r.seo_issues.length} />
      </div>
      <Notice tone="amber">{r.tag_note} Detected: {r.google_tags_detected.join(", ") || "none"} {r.gtm_detected.length ? `· GTM ${r.gtm_detected.join(", ")}` : ""}</Notice>
      <div className="grid gap-6 lg:grid-cols-2">
        <Card title="Service inventory (from site)">
          <Table rows={r.service_inventory} columns={[{ key: "name", label: "Service" }, { key: "page_url", label: "Page", className: "text-xs" }]} />
          {r.services_not_in_profile.length > 0 && <div className="mt-3"><Notice tone="amber">On the site but not in the profile: {r.services_not_in_profile.join(", ")}</Notice></div>}
          {r.services_without_page.length > 0 && <div className="mt-2"><Notice tone="red">In the profile but no dedicated page (client fix): {r.services_without_page.join(", ")}</Notice></div>}
        </Card>
        <Card title="Landing page content scores">
          <Table rows={r.content_scores} columns={[{ key: "service", label: "Service" }, { key: "score", label: "Score /100" },
            { key: "notes", label: "Notes", render: (c) => <span className="text-xs">{c.notes.join("; ") || "good"}</span> }]} />
        </Card>
      </div>
      <Card title="Recommendations & claims">
        <ul className="list-disc space-y-1 pl-5 text-sm">{r.landing_page_recommendations.map((x: string) => <li key={x}>{x}</li>)}</ul>
        {r.claims_not_in_verified_list.length > 0 && <div className="mt-3"><Notice tone="amber">Claims on the site that are not in the verified list (ads cannot use them): {r.claims_not_in_verified_list.join(" · ")}</Notice></div>}
        <div className="mt-3 text-xs text-slate-500">Keyword seeds: {r.keyword_seeds.join(", ")} · Schema: {r.schema_types.join(", ") || "none"}</div>
      </Card>
      <Card title="Technical & SEO issues"><Table rows={r.seo_issues} columns={[{ key: "url", label: "URL", className: "text-xs" }, { key: "issue", label: "Issue" }]} empty="No issues found." /></Card>
      <Card title="Page inventory"><Table rows={r.page_inventory} columns={[{ key: "url", label: "URL", className: "text-xs" }, { key: "title", label: "Title" }, { key: "words", label: "Words" }, { key: "forms", label: "Forms" }]} /></Card>
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
      <ul className="list-disc pl-5 text-sm">{r.findings.map((f: string) => <li key={f}>{f}</li>)}</ul>
      <p className="text-xs text-slate-500">{r.note}</p>
      {r.client?.reviews && <Card title="Recent reviews (sample)"><ul className="space-y-2 text-sm">{r.client.reviews.map((x: any, i: number) => <li key={i}><Badge>{x.rating}★</Badge> {x.text} <span className="text-xs text-slate-400">— {x.author}</span></li>)}</ul></Card>}
    </div>
  );
}

function Competitor({ r }: { r: any }) {
  if (r.skipped) return <Notice>{r.reason}</Notice>;
  return (
    <div className="space-y-4">
      {r.competitors.map((c: any) => (
        <Card key={c.name} title={c.name}>
          <div className="text-sm"><b>Services:</b> {c.services?.join(", ") || "—"}</div>
          {c.services_client_lacks?.length > 0 && <div className="mt-1 text-sm text-amber-800"><b>Client lacks:</b> {c.services_client_lacks.join(", ")}</div>}
          <div className="mt-1 text-sm"><b>Offers on site:</b> {c.offers_on_site?.join(" · ") || "—"}</div>
          <div className="mt-1 text-xs text-slate-500"><b>Keyword ideas (what their site is about, not what they bid on):</b> {c.keyword_ideas?.join(", ")}</div>
        </Card>
      ))}
      {r.pasted_ads_analysis && <Card title="Pasted competitor ads"><Notice tone="amber">{r.pasted_ads_analysis.verification}</Notice>
        <div className="mt-2 text-sm"><b>Offers:</b> {r.pasted_ads_analysis.offers.join(" · ")}</div><div className="text-sm"><b>Angles:</b> {r.pasted_ads_analysis.angles.join(" · ")}</div></Card>}
      <p className="text-xs text-slate-500">Sources: {r.keyword_sources.join(", ")}. Missing: {r.missing_sources.join(", ")}</p>
    </div>
  );
}

function Keywords({ r }: { r: any }) {
  return (
    <div className="space-y-4">
      <Notice>{r.match_type_policy}</Notice>
      {r.overlaps.length > 0 && <Notice tone="amber">Overlaps: {r.overlaps.map((o: any) => `${o.keyword} (${o.ad_groups.join(" / ")})`).join("; ")}</Notice>}
      <Table rows={r.keywords.slice(0, 150)} columns={[{ key: "keyword", label: "Keyword" }, { key: "label", label: "Label", render: (k) => <StatusBadge value={k.label === "IRRELEVANT" ? "FAIL" : k.label === "SERVICE" ? "PASS" : "WARN"} /> },
        { key: "ad_group", label: "Ad group" }, { key: "match_types", label: "Match", render: (k) => k.match_types.join(" + ") || "negative" },
        { key: "avg_monthly_searches", label: "Searches/mo" }, { key: "bid_range", label: "Bid range" }, { key: "competition", label: "Competition" },
        { key: "selected", label: "Selected" }, { key: "labelled_by", label: "Labelled by" }]} />
      <div className="text-sm"><b>Negative ideas:</b> {r.negative_ideas.join(", ")}</div>
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

export function AuditDetail() {
  const { id } = useParams();
  const q = useQuery({ queryKey: ["audit", id], queryFn: () => api.get(`/audits/${id}`) });
  if (q.isLoading || !q.data) return <Spinner />;
  const a = q.data;
  const r = a.result || {};
  const View = { WEBSITE: Website, GBP: Gbp, COMPETITOR: Competitor, KEYWORD: Keywords, ACCOUNT: Account }[a.audit_type as string];
  return (
    <div className="space-y-6">
      <PageHeader title={`${a.audit_type} audit`} subtitle={<><Link to={`/businesses/${a.business_id}`} className="text-brand-700">Business</Link> · {dt(a.started_at)} · {a.summary}</>}
        actions={<><StatusBadge value={a.status} /><DataSourceBadge source={a.source_type} /></>} />
      {a.error ? <ErrorBox error={{ message: a.error }} /> : View ? <View r={r} /> : <JsonBlock value={r} />}
      <details><summary className="cursor-pointer text-xs text-slate-500">Raw result</summary><JsonBlock value={r} /></details>
    </div>
  );
}
