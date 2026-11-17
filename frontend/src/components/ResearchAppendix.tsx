import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import type { ReactNode } from "react";
import { api } from "../api/client";
import { compact, DataTable, usd } from "./report/DataTable";
import type { Col, Filter } from "./report/DataTable";
import { Badge, dt, ErrorBox, Notice, Spinner } from "./ui";

const NA = ({ why, text = "unavailable" }: { why?: string; text?: string }) => <span className="text-slate-400" title={why}>{text}</span>;
const uniq = (xs: (string | null | undefined)[]) => [...new Set(xs.filter(Boolean) as string[])].sort();
const opts = (xs: string[], label: (x: string) => string = (x) => x) => xs.map((x) => ({ value: x, label: label(x) }));

/** One collapsible report section: the summary line and counts stay visible when it is closed. */
export function Section({ id, n, title, summary, counts = [], open = false, tone, children }: {
  id: string; n: number; title: string; summary?: ReactNode; counts?: [string, ReactNode, string?][]; open?: boolean; tone?: "red" | "amber"; children: ReactNode;
}) {
  return (
    <details id={id} open={open} className={`group rounded-lg border bg-white ${tone === "red" ? "border-rose-200" : tone === "amber" ? "border-amber-200" : "border-slate-200"}`}>
      <summary className="flex cursor-pointer list-none flex-wrap items-center gap-x-3 gap-y-1 px-4 py-3">
        <span className="text-slate-400 transition-transform group-open:rotate-90" aria-hidden>▸</span>
        <h3 className="text-sm font-semibold text-slate-900">{n}. {title}</h3>
        {summary && <span className="min-w-0 flex-1 text-xs text-slate-500">{summary}</span>}
        <span className="flex flex-wrap gap-1.5">{counts.map(([k, v, t]) => <Badge key={k} tone={t ?? "slate"}>{k}: {v}</Badge>)}</span>
      </summary>
      <div className="border-t border-slate-100 px-4 py-4">{children}</div>
    </details>
  );
}

function Tabs<T extends string>({ value, onChange, tabs, label }: { value: T; onChange: (v: T) => void; tabs: [T, string, number][]; label: string }) {
  return (
    <div role="tablist" aria-label={label} className="mb-2 flex flex-wrap gap-1">
      {tabs.map(([k, l, c]) => (
        <button key={k} role="tab" aria-selected={value === k} type="button" onClick={() => onChange(k)}
          className={`rounded-full px-2.5 py-0.5 text-xs ring-1 ${value === k ? "bg-slate-900 text-white ring-slate-900" : "text-slate-700 ring-slate-300 hover:bg-slate-50"}`}>{l} ({c})</button>))}
    </div>
  );
}

/** A clickable counter: selects the matching tab of its section. */
function Counter({ label, value, onClick, tone = "slate" }: { label: string; value: number | undefined; onClick?: () => void; tone?: string }) {
  const cls = tone === "green" ? "text-emerald-700" : tone === "red" ? "text-rose-700" : tone === "amber" ? "text-amber-700" : "text-slate-900";
  return (
    <button type="button" onClick={onClick} className="rounded-md border border-slate-200 px-3 py-2 text-left hover:border-slate-300 hover:bg-slate-50">
      <div className={`text-lg font-semibold tabular-nums ${cls}`}>{value ?? 0}</div><div className="text-2xs text-slate-500">{label}</div>
    </button>
  );
}

const SEL_TONE: Record<string, string> = { eligible: "green", research_only: "slate", rejected: "red" };
const SEL_LABEL: Record<string, string> = { eligible: "Campaign-eligible", research_only: "Research only", rejected: "Rejected" };
const MEAS_TONE: Record<string, string> = { measured: "green", unmeasured: "amber" };

function KeywordTable({ kw }: { kw: any }) {
  const rows: any[] = kw.rows ?? [...(kw.selected ?? []), ...(kw.rejected ?? [])];
  const cols: Col<any>[] = [
    { key: "keyword", label: "Keyword", render: (k) => <span><span className="font-medium text-slate-900">{k.keyword}</span>
      {k.overlap?.length > 0 && <span className="block text-2xs text-amber-700" title={k.overlap.map((o: any) => o.detail || o.ad_groups.join(" / ")).join("; ")}>overlaps {k.overlap[0].ad_groups.join(" / ")}</span>}
      {k.rejection_reason && <span className="block text-2xs text-slate-500">{k.rejection_reason}</span>}</span> },
    { key: "ad_group", label: "Ad group", value: (k) => k.ad_group ?? k.service ?? null, render: (k) => k.ad_group ?? k.service ?? <NA text="—" /> },
    { key: "match_types", label: "Match type", value: (k) => (k.match_types ?? []).join(", "), render: (k) => (k.match_types ?? []).join(", ") || "—" },
    { key: "search_volume", label: "Volume", align: "right", render: (k) => k.search_volume !== null && k.search_volume !== undefined
      ? <span title={k.volume_period ?? "average monthly searches"}>{compact(k.search_volume)}</span> : <NA why={k.unmeasured_reason || k.unavailable_reason} text="not measured" /> },
    { key: "avg_cpc", label: "Avg CPC", align: "right", render: (k) => k.avg_cpc !== null && k.avg_cpc !== undefined ? <span title={k.avg_cpc_note}>{usd(k.avg_cpc)}</span> : <NA why={k.avg_cpc_note} text="—" /> },
    { key: "competition", label: "Competition", value: (k) => k.competition ?? null, render: (k) => k.competition ?? <NA text="—" /> },
    { key: "top_of_page_bid_high", label: "Top-of-page bid", align: "right", render: (k) => k.top_of_page_bid_high ? `${usd(k.top_of_page_bid_low) ?? "—"}–${usd(k.top_of_page_bid_high)}` : <NA text="—" /> },
    { key: "intent", label: "Intent", value: (k) => k.keyword_class_label ?? k.intent ?? null, render: (k) => <span title={k.class_reason}>{k.keyword_class_label ?? k.intent ?? "—"}</span> },
    { key: "location_relevance", label: "Location", render: (k) => <span title={k.location_reason}>{({ in_area: "In service area", outside_area: "Outside area", none: "No place named" } as any)[k.location_relevance] ?? "—"}</span> },
    { key: "metric_source", label: "Source", value: (k) => k.metric_source ?? (k.providers ?? []).join(", "), render: (k) => <span title={(k.providers ?? []).join(", ")}>{k.metric_source ?? (k.providers ?? [])[0] ?? "—"}</span> },
    { key: "measurement_status", label: "Measured", render: (k) => <Badge tone={MEAS_TONE[k.measurement_status] ?? (k.search_volume !== null ? "green" : "amber")}>{k.measurement_status ?? (k.search_volume !== null ? "measured" : "unmeasured")}</Badge> },
    { key: "selection_status", label: "Selection", render: (k) => <Badge tone={SEL_TONE[k.selection_status] ?? "slate"}>{SEL_LABEL[k.selection_status] ?? (k.in_plan ? "In plan" : "—")}</Badge> },
  ];
  const filters: Filter<any>[] = [
    { key: "meas", label: "Measured", options: [{ value: "measured", label: "Measured" }, { value: "unmeasured", label: "Unmeasured" }],
      test: (k, v) => (k.measurement_status ?? (k.search_volume !== null ? "measured" : "unmeasured")) === v },
    { key: "intent", label: "Intent", options: opts(uniq(rows.map((k) => k.keyword_class_label ?? k.intent))), test: (k, v) => (k.keyword_class_label ?? k.intent) === v },
    { key: "match", label: "Match type", options: opts(uniq(rows.flatMap((k) => k.match_types ?? []))), test: (k, v) => (k.match_types ?? []).includes(v) },
    { key: "service", label: "Service", options: opts(uniq(rows.map((k) => k.service))), test: (k, v) => k.service === v },
    { key: "sel", label: "Selection", options: opts(uniq(rows.map((k) => k.selection_status)), (x) => SEL_LABEL[x] ?? x), test: (k, v) => k.selection_status === v },
  ];
  return <DataTable label="Keyword research" rows={rows} cols={cols} filters={filters} initialSort={{ key: "search_volume", dir: "desc" }} />;
}

const COMP_TONE: Record<string, string> = { "Market Verified": "green", "Website Verified": "green", "SERP Verified": "blue", "Pending Verification": "amber", Discovered: "slate", Rejected: "red" };

function Competitors({ cv, legacy }: { cv: any; legacy: any[] }) {
  const [tab, setTab] = useState<"all" | "verified" | "pending" | "rejected">("all");
  const recs: any[] = cv?.records ?? [];
  const c = cv?.counts ?? {};
  if (!recs.length) return legacy.length ? (
    <div><Notice tone="amber">This version was captured before competitor verification records existed: the competitors below are listed as found, without verification.</Notice>
      <DataTable label="Competitors" rows={legacy} cols={[{ key: "name", label: "Business" }, { key: "origin", label: "Origin" }, { key: "discovery_source", label: "Discovered via" },
        { key: "website", label: "Website", render: (r) => r.website ? <a className="break-all text-brand-700" href={r.website} target="_blank" rel="noreferrer noopener">{r.website}</a> : "—" },
        { key: "pages", label: "Pages reviewed", value: (r) => (r.landing_pages_reviewed ?? []).length, render: (r) => r.website_inspected ? `${r.landing_pages_reviewed.length} page(s)` : <NA text="website not read" /> },
        { key: "findings", label: "Findings", value: (r) => (r.findings ?? []).join("; ") }]} /></div>)
    : <p className="text-xs text-slate-500">No competitor was recorded.</p>;
  const isV = (r: any) => ["Market Verified", "Website Verified", "SERP Verified"].includes(r.status);
  const isP = (r: any) => ["Pending Verification", "Discovered"].includes(r.status);
  const shown = recs.filter((r) => tab === "all" || (tab === "verified" ? isV(r) : tab === "pending" ? isP(r) : r.status === "Rejected"));
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-6">
        <Counter label="Discovered" value={c.discovered} onClick={() => setTab("all")} />
        <Counter label="SERP verified" value={c.serp_verified} onClick={() => setTab("verified")} tone="green" />
        <Counter label="Website verified" value={c.website_verified} onClick={() => setTab("verified")} tone="green" />
        <Counter label="Market verified" value={c.market_verified} onClick={() => setTab("verified")} tone="green" />
        <Counter label="Pending" value={c.pending} onClick={() => setTab("pending")} tone="amber" />
        <Counter label="Rejected" value={c.rejected} onClick={() => setTab("rejected")} tone="red" />
      </div>
      <Tabs label="Competitor status" value={tab} onChange={setTab} tabs={[["all", "All", recs.length], ["verified", "Verified", recs.filter(isV).length],
        ["pending", "Pending", recs.filter(isP).length], ["rejected", "Rejected", recs.filter((r) => r.status === "Rejected").length]]} />
      <DataTable label="Competitors" rows={shown} filters={[
        { key: "origin", label: "Origin", options: [{ value: "client_provided", label: "Client-provided" }, { value: "discovered", label: "Discovered" }], test: (r, v) => r.classification === v }]}
        cols={[
          { key: "name", label: "Competitor", render: (r) => <span><span className="font-medium text-slate-900">{r.name}</span>
            <a className="block break-all text-2xs text-brand-700 hover:underline" href={r.url} target="_blank" rel="noreferrer noopener">{r.domain}</a></span> },
          { key: "classification", label: "Origin", render: (r) => <Badge tone={r.classification === "client_provided" ? "blue" : "violet"}>{r.classification === "client_provided" ? "client-provided" : "discovered"}</Badge> },
          { key: "status", label: "Status", render: (r) => <Badge tone={COMP_TONE[r.status]}>{r.status}</Badge> },
          { key: "discovery_sources", label: "Found via", value: (r) => (r.discovery_sources ?? []).join(", ") },
          { key: "serp", label: "SERP evidence", value: (r) => r.serp_evidence?.length ?? 0, render: (r) => r.serp_evidence?.length
            ? <span title={r.serp_evidence.map((e: any) => `${e.provider}: ${e.query}${e.position ? ` #${e.position}` : ""}${e.location ? ` · ${e.location}` : ""}`).join("\n")}>
              {r.serp_evidence.length} · “{r.serp_evidence[0].query}”{r.serp_evidence[0].position ? ` #${r.serp_evidence[0].position}` : ""}</span> : <NA text="none" /> },
          { key: "site", label: "Website / services", value: (r) => (r.services_found ?? []).join(", "), render: (r) => r.website_accessible === null || r.website_accessible === undefined
            ? <NA text="not opened" /> : r.website_accessible ? <span>opened{r.service_evidence?.[0] ? ` · ${r.service_evidence[0].detail}` : " · services not confirmed"}</span> : <span className="text-rose-700">could not be opened</span> },
          { key: "geo", label: "Market overlap", value: (r) => r.geo_overlap_evidence?.length ?? 0, render: (r) => r.geo_overlap_evidence?.length ? r.geo_overlap_evidence[0].detail : <NA text="not shown" /> },
          { key: "reason", label: "Reason", value: (r) => r.rejection_reason ?? r.evidence_note, render: (r) => <span className="block max-w-[16rem] text-slate-600">{r.rejection_reason ?? r.evidence_note ?? ""}</span> },
          { key: "timestamp", label: "Checked", render: (r) => r.timestamp ? dt(r.timestamp) : "—" },
        ]} />
      {cv?.policy && <p className="text-2xs text-slate-500">{cv.policy}</p>}
    </div>
  );
}

const SRC_TONE: Record<string, string> = { Verified: "green", Relevant: "blue", Extracted: "slate", Crawled: "slate", Discovered: "slate", Rejected: "red", Failed: "red" };

function Sources({ ledger, legacy, m }: { ledger: any; legacy: any[]; m: any }) {
  const [tab, setTab] = useState<"all" | "verified" | "pending" | "rejected">("all");
  const recs: any[] = ledger?.records ?? [];
  const c = ledger?.counts ?? {};
  const grouped = [["provider", "Provider"], ["source_type", "Type"], ["verification_status", "Status"]] as const;
  const isPending = (r: any) => ["Discovered", "Crawled", "Extracted", "Relevant"].includes(r.verification_status);
  const shown = recs.filter((r) => tab === "all" || (tab === "verified" ? r.verification_status === "Verified" : tab === "pending" ? isPending(r) : ["Rejected", "Failed"].includes(r.verification_status)));
  return (
    <div className="space-y-3">
      {recs.length ? <>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-6">
          <Counter label="Sources discovered" value={c.discovered} onClick={() => setTab("all")} />
          <Counter label="Crawled" value={c.crawled} onClick={() => setTab("pending")} />
          <Counter label="Relevant" value={c.relevant} onClick={() => setTab("pending")} />
          <Counter label="Evidence-verified" value={c.verified} onClick={() => setTab("verified")} tone="green" />
          <Counter label="Rejected" value={c.rejected} onClick={() => setTab("rejected")} tone="red" />
          <Counter label="Failed" value={c.failed} onClick={() => setTab("rejected")} tone="red" />
        </div>
        <Tabs label="Source status" value={tab} onChange={setTab} tabs={[["all", "All", recs.length], ["verified", "Verified", recs.filter((r) => r.verification_status === "Verified").length],
          ["pending", "Pending", recs.filter(isPending).length], ["rejected", "Rejected / failed", recs.filter((r) => ["Rejected", "Failed"].includes(r.verification_status)).length]]} />
        <DataTable label="Research sources" rows={shown} filters={grouped.map(([k, l]) => ({ key: k, label: l, options: opts(uniq(recs.map((r) => r[k]))), test: (r: any, v: string) => r[k] === v }))}
          cols={[
            { key: "title", label: "Source", render: (s) => s.url ? <a className="break-all text-brand-700 hover:underline" href={s.url} target="_blank" rel="noreferrer noopener">{s.title || s.url}</a> : s.title },
            { key: "provider", label: "Provider" }, { key: "source_type", label: "Type" },
            { key: "http_status", label: "HTTP", render: (s) => s.http_status ?? "—" },
            { key: "extraction_status", label: "Extraction" },
            { key: "relevance_score", label: "Relevance", render: (s) => s.relevance_score === null || s.relevance_score === undefined ? "—" : <span title={s.relevance_reason}>{s.relevance_score}/100</span> },
            { key: "verification_status", label: "Status", render: (s) => <Badge tone={SRC_TONE[s.verification_status]}>{s.verification_status}</Badge> },
            { key: "failure_reason", label: "Failure / rejection", render: (s) => <span className="block max-w-[14rem] text-slate-600">{s.failure_reason ?? ""}</span> },
            { key: "crawled_at", label: "Retrieved", render: (s) => s.crawled_at ? dt(s.crawled_at) : "—" },
          ]} />
        {ledger.policy && <p className="text-2xs text-slate-500">{ledger.policy}</p>}
      </> : (
        <DataTable label="Research sources" rows={legacy} cols={[
          { key: "provider", label: "Provider" },
          { key: "title", label: "Source", render: (s) => s.url ? <a className="break-all text-brand-700 hover:underline" href={s.url} target="_blank" rel="noreferrer noopener">{s.title || s.url}</a> : s.title },
          { key: "retrieved_at", label: "Retrieved", render: (s) => dt(s.retrieved_at) }, { key: "cached", label: "Cached", render: (s) => s.cached ? <Badge tone="amber">cached</Badge> : "live" },
          { key: "extracted", label: "Extracted" }, { key: "verification", label: "Verification" }, { key: "influence", label: "Influence" }]} />)}
      <details className="rounded border border-slate-200 px-3 py-2 text-xs">
        <summary className="cursor-pointer font-medium text-slate-700">Research methodology</summary>
        <dl className="mt-2 space-y-2">
          <div><dt className="font-medium text-slate-700">Integrations used</dt><dd className="text-slate-600">{(m.integrations_used ?? []).map((i: any) => `${i.provider} (${i.records})`).join(" · ") || "none recorded"}</dd></div>
          <div><dt className="font-medium text-slate-700">Searches performed</dt><dd className="text-slate-600">{(m.searches ?? []).join(" · ") || "none recorded"}</dd></div>
          <div><dt className="font-medium text-slate-700">Served from cache</dt><dd className="text-slate-600">{(m.cached ?? []).join(" · ") || "none"}</dd></div>
          <div><dt className="font-medium text-slate-700">How the structure was derived</dt><dd className="text-slate-600"><ul className="list-disc pl-4">{(m.derivation ?? []).map((d: string) => <li key={d}>{d}</li>)}</ul></dd></div>
        </dl>
      </details>
    </div>
  );
}

const KP_ACCESS: Record<string, string> = { explorer: "Explorer API access (Keyword Planner not included)", api_disabled: "Google Ads API not enabled in the Cloud project",
  project_not_authorized: "Cloud project not authorized for this method" };
const D4S_BLOCK: Record<string, string> = { account_verification_required: "account verification required", insufficient_funds: "insufficient balance",
  auth_failure: "credentials rejected", quota_exceeded: "quota or cost limit reached", rate_limited: "rate limited", service_unavailable: "provider unavailable" };

/** Campaign report of one plan version: ten sections built only from the evidence persisted with the version. Missing data is shown as
 *  missing, with its reason, never filled in. */
export function ResearchAppendix({ planId, adGroups, aiReview, draft }: { planId: string; adGroups?: ReactNode; aiReview?: ReactNode; draft?: any }) {
  const q = useQuery({ queryKey: ["plan-appendix", planId], queryFn: () => api.get(`/plans/${planId}/appendix`) });
  if (q.isLoading) return <Spinner />;
  if (q.error) return <ErrorBox error={q.error} />;
  const a = q.data;
  if (a.error) return <Notice tone="amber">{a.error}</Notice>;
  const kw = a.keywords ?? {};
  const f = a.feasibility ?? {};
  const m = a.methodology ?? {};
  const st = a.settings;
  const cov = kw.measurement_coverage ?? {};
  const kp = kw.keyword_planner ?? {};
  const d4s = kw.dataforseo ?? {};
  const cv = a.competitor_verification ?? {};
  const rows: any[] = kw.rows ?? [...(kw.selected ?? []), ...(kw.rejected ?? [])];
  const settingIssues: any[] = st?.issues ?? [];
  const d4sBlock = d4s.metrics?.blocked_by;
  const blockers: { level: "blocking" | "warning"; text: string }[] = [
    ...settingIssues.map((i) => ({ level: "blocking" as const, text: i.message })),
    ...(d4sBlock ? [{ level: "warning" as const, text: `DataForSEO did not measure keywords: ${D4S_BLOCK[d4sBlock] ?? d4sBlock}. ${d4s.errors?.[0]?.message ?? ""}` }] : []),
    ...(!kp.available ? [{ level: "warning" as const, text: `Keyword Planner not used: ${kp.access_level ? KP_ACCESS[kp.access_level] ?? kp.access_level : kp.reason || "unavailable"}.` }] : []),
    ...(cov.eligible_unmeasured ? [{ level: "warning" as const, text: `${cov.eligible_unmeasured} of ${cov.eligible} campaign-eligible keyword(s) have no measured search volume.` }] : []),
    ...(cv.counts && !cv.counts.verified ? [{ level: "warning" as const, text: "No competitor is verified by search, website or market evidence." }] : []),
  ];
  const nBlock = blockers.filter((b) => b.level === "blocking").length;
  const negs: string[] = draft?.campaign_negatives ?? [];
  const groupNegs = (draft?.ad_groups ?? []).flatMap((g: any) => (g.negatives ?? []).map((n: any) => ({ g: g.name, n: typeof n === "string" ? n : n.text })));
  const rejectedKw = rows.filter((k) => k.selection_status === "rejected");
  const ledgerC = a.source_ledger?.counts;
  const setting = (label: string, v: ReactNode | null | undefined, why?: string) => (
    <div><dt className="text-slate-400">{label}</dt><dd className="text-slate-800">{v ?? <span className="font-medium text-rose-700" title={why}>Not set — blocking</span>}</dd></div>);
  return (
    <section aria-labelledby="appendix-title" className="space-y-2">
      <div>
        <h2 id="appendix-title" className="text-base font-semibold text-slate-900">Campaign report</h2>
        <p className="mt-1 text-xs text-slate-500">
          Plan version {a.plan_version} · {a.captured_with_version ? `captured with this version on ${dt(a.captured_at)}` : "assembled from the task's persisted records"} · {m.events_recorded ?? 0} recorded research events.
          Every row comes from a stored record; a metric no provider returned is shown as not measured.
        </p>
        {a.note && <div className="mt-2"><Notice tone="amber">{a.note}</Notice></div>}
        {(a.version ?? 1) < 2 && a.captured_with_version && <div className="mt-2"><Notice tone="amber">This version was captured with an earlier report format: keyword classification, competitor verification and campaign settings were not recorded for it. Re-run the research to see them.</Notice></div>}
      </div>

      <Section id="rp-overview" n={1} title="Overview" open tone={nBlock ? "red" : undefined}
        summary={nBlock ? `${nBlock} blocking issue(s) must be fixed before approval` : blockers.length ? `${blockers.length} warning(s)` : "No blocking issue recorded"}
        counts={[["Blocking", nBlock, nBlock ? "red" : "green"], ["Warnings", blockers.length - nBlock, blockers.length - nBlock ? "amber" : "slate"]]}>
        <dl className="grid gap-x-6 gap-y-2 text-xs sm:grid-cols-4">
          {[["Keywords researched", compact(cov.total ?? kw.total ?? rows.length)], ["Campaign-eligible", cov.eligible ?? "—"],
            ["Eligible with measured volume", cov.eligible !== undefined ? `${cov.eligible_measured} / ${cov.eligible}` : `${kw.measured ?? 0} / ${kw.total ?? 0}`],
            ["Competitors verified", cv.counts ? `${cv.counts.verified} of ${cv.counts.discovered}` : "not recorded"],
            ["Sources evidence-verified", ledgerC ? `${ledgerC.verified} of ${ledgerC.discovered}` : `${(a.sources ?? []).length} recorded`],
            ["Expected leads / week (estimate)", f.expected_weekly_leads ?? "—"], ["Estimated cost per lead", usd(f.estimated_cpl) ?? "—"],
            ["Daily budget", usd(f.daily_budget) ?? "—"]].map(([k, v]) => <div key={k as string}><dt className="text-slate-400">{k}</dt><dd className="font-medium text-slate-900">{v as any}</dd></div>)}
        </dl>
        {blockers.length > 0 && <ul aria-label="Blockers and warnings" className="mt-3 space-y-1 text-xs">{blockers.map((b, i) => (
          <li key={i} className="flex gap-2"><Badge tone={b.level === "blocking" ? "red" : "amber"}>{b.level}</Badge><span className="text-slate-700">{b.text}</span></li>))}</ul>}
      </Section>

      <Section id="rp-settings" n={2} title="Settings & Budget" tone={settingIssues.length ? "red" : undefined}
        summary={st ? [st.location?.label ?? "no location", st.language?.label ?? "no language", st.ad_schedule?.label ?? "no schedule"].join(" · ") : "Settings were not recorded with this version"}
        counts={settingIssues.length ? [["Blocking", settingIssues.length, "red"]] : []}>
        {st ? <dl className="grid gap-x-6 gap-y-2 text-xs sm:grid-cols-3">
          {setting("Campaign name", draft?.campaign_name)}
          {setting("Target location", st.location?.label, settingIssues.find((i) => i.field === "location")?.message)}
          {setting("Location targeting mode", st.location_targeting_mode?.label)}
          {setting("Language", st.language?.label)}
          {setting("Ad schedule", st.ad_schedule?.label, settingIssues.find((i) => i.field === "ad_schedule")?.message)}
          {setting("Bidding", draft?.bidding_strategy)}
          {setting("Daily budget (estimate input)", usd(f.daily_budget))}
          {setting("Suggested monthly budget", usd(f.suggested_monthly_budget) ?? "not computed")}
          <div><dt className="text-slate-400">Status on create</dt><dd className="text-slate-800">Paused (enabling needs its own approval)</dd></div>
        </dl> : <p className="text-xs text-slate-500">Not recorded with this version.</p>}
        {settingIssues.map((i) => <div key={i.code} className="mt-2"><Notice tone="red">{i.message}</Notice></div>)}
      </Section>

      <Section id="rp-keywords" n={3} title="Keyword Research"
        summary={cov.eligible !== undefined ? `${cov.eligible_measured} of ${cov.eligible} eligible keywords measured · ${cov.rejected} rejected · ${cov.research_only} research-only` : `${kw.measured ?? 0} of ${kw.total ?? 0} with measured search volume`}
        counts={[["Rows", compact(rows.length) ?? 0], ["Measured", cov.measured_total ?? kw.measured ?? 0, "green"], ["Unmeasured", (rows.length - (cov.measured_total ?? kw.measured ?? 0)), "amber"]]}>
        <div className="mb-3 grid gap-2 text-xs sm:grid-cols-2">
          <div className="rounded border border-slate-200 p-2"><b>Keyword Planner:</b> {kp.available ? "used" : `not used — ${kp.access_level ? KP_ACCESS[kp.access_level] ?? kp.access_level : kp.reason || "unavailable"}`}
            {kp.access_note && <p className="mt-1 text-slate-600">{kp.access_note}</p>}</div>
          <div className="rounded border border-slate-200 p-2"><b>DataForSEO:</b> {!d4s.used ? `not used${d4s.reason ? ` — ${d4s.reason}` : ""}` : d4sBlock
            ? <span className="text-rose-700">blocked — {D4S_BLOCK[d4sBlock] ?? d4sBlock}</span> : `${d4s.metrics?.with_volume ?? 0} of ${d4s.metrics?.requested ?? 0} measured`}
            {d4s.errors?.[0] && <p className="mt-1 text-slate-600">{d4s.errors[0].message}</p>}</div>
        </div>
        {cov.unmeasured_reasons?.length > 0 && <p className="mb-2 text-xs text-slate-600">Why eligible keywords are unmeasured: {cov.unmeasured_reasons.map((r: any) => `${r.reason} (${r.count})`).join(" · ")}</p>}
        {cov.service_areas?.length > 0 && <p className="mb-2 text-2xs text-slate-500">Service areas used for location filtering: {cov.service_areas.join(", ")}</p>}
        <KeywordTable kw={kw} />
        {kw.policy && <p className="mt-2 text-2xs text-slate-500">{kw.policy}</p>}
      </Section>

      <Section id="rp-adgroups" n={4} title="Ad Groups & Ads" summary={draft ? `${(draft.ad_groups ?? []).length} ad group(s) · ${(draft.ad_groups ?? []).reduce((s: number, g: any) => s + (g.keywords?.length ?? 0), 0)} keyword(s)` : undefined}>
        {adGroups ?? <p className="text-xs text-slate-500">Shown in the plan above.</p>}
      </Section>

      <Section id="rp-competitors" n={5} title="Competitor Intelligence"
        summary={cv.counts ? `${cv.counts.verified} verified · ${cv.counts.pending} pending · ${cv.counts.rejected} rejected (${cv.counts.client_provided} client-provided)` : `${(a.competitors ?? []).length} recorded`}
        counts={cv.counts ? [["Verified", cv.counts.verified, cv.counts.verified ? "green" : "amber"], ["Rejected", cv.counts.rejected, "red"]] : []}>
        <Competitors cv={cv} legacy={a.competitors ?? []} />
      </Section>

      <Section id="rp-negatives" n={6} title="Negative Keywords" summary={`${negs.length} campaign negative(s) · ${groupNegs.length} ad-group negative(s) · ${rejectedKw.length} rejected research keyword(s)`}>
        <div className="space-y-2 text-xs">
          <div><b>Campaign negatives:</b> {negs.length ? negs.join(", ") : <span className="text-slate-400">none in the draft</span>}</div>
          {groupNegs.length > 0 && <div><b>Ad-group negatives:</b> {groupNegs.map((x: any) => `${x.n} (${x.g})`).join(", ")}</div>}
          <p className="text-slate-500">A shared list of job, DIY and free-intent patterns is also attached to every campaign.</p>
          {rejectedKw.length > 0 && <details><summary className="cursor-pointer text-slate-700">Rejected research keywords by reason ({rejectedKw.length})</summary>
            <ul className="mt-1 list-disc pl-4">{Object.entries(rejectedKw.reduce((acc: Record<string, string[]>, k) => { (acc[k.keyword_class_label ?? "Other"] ??= []).push(k.keyword); return acc; }, {}))
              .map(([cls, ks]) => <li key={cls}><b>{cls}</b> ({(ks as string[]).length}): {(ks as string[]).slice(0, 12).join(", ")}{(ks as string[]).length > 12 ? "…" : ""}</li>)}</ul></details>}
        </div>
      </Section>

      <Section id="rp-feasibility" n={7} title="Feasibility & Forecast" summary={`${f.verdict ?? "no verdict"} · estimates, not guarantees`}>
        <dl className="grid gap-x-6 gap-y-2 text-xs sm:grid-cols-3">
          {[["Verdict", f.verdict ?? "—"], ["Expected leads / week (estimate)", f.expected_weekly_leads ?? "—"],
            ["Realistic range / week", Array.isArray(f.realistic_weekly_range) ? f.realistic_weekly_range.join("–") : "—"],
            ["Estimated cost per lead", usd(f.estimated_cpl) ?? "—"], ["Allowed cost per lead", usd(f.allowed_cpl) ?? "—"],
            ["Suggested monthly budget", usd(f.suggested_monthly_budget) ?? "—"], ["CPC assumption", usd(f.cpc_assumption) ?? "not recorded"],
            ["Conversion-rate assumption", typeof f.conversion_rate_assumption === "number" ? `${(f.conversion_rate_assumption * (f.conversion_rate_assumption < 1 ? 100 : 1)).toFixed(1)}%` : "not recorded"],
            ["Competitor density", f.competitor_density ? `${f.competitor_density.client_provided} client-provided · ${f.competitor_density.discovered} discovered · ${f.competitor_density.local_businesses_found} local businesses` : "—"],
          ].map(([k, v]) => <div key={k as string}><dt className="text-slate-400">{k}</dt><dd className="text-slate-800">{v as any}</dd></div>)}
        </dl>
        <p className="mt-2 text-xs text-slate-500">{f.limitations}</p>
      </Section>

      <Section id="rp-ai" n={8} title="AI Review & Recommendations">{aiReview ?? <p className="text-xs text-slate-500">Shown in the plan above.</p>}</Section>

      <Section id="rp-sources" n={9} title="Research Sources & Evidence"
        summary={ledgerC ? `${ledgerC.discovered} sources · ${ledgerC.crawled} crawled · ${ledgerC.verified} evidence-verified · ${ledgerC.failed} failed` : `${(a.sources ?? []).length} sources recorded`}>
        <Sources ledger={a.source_ledger} legacy={a.sources ?? []} m={m} />
      </Section>

      <Section id="rp-errors" n={10} title="Errors, Warnings & Missing Data" tone={(a.provider_errors ?? []).length || (m.unavailable ?? []).length ? "amber" : undefined}
        summary={`${(a.provider_errors ?? []).length} provider(s) with failures · ${(m.unavailable ?? []).length} unavailable or failed request(s)`}>
        {(a.provider_errors ?? []).length > 0 && <DataTable<any> label="Provider errors" search={false} rows={a.provider_errors} cols={[
          { key: "provider", label: "Provider" }, { key: "count", label: "Failures", align: "right" },
          { key: "reasons", label: "Reasons", value: (e) => e.reasons.join("; "), render: (e) => <ul className="list-disc pl-4">{e.reasons.map((r: string) => <li key={r}>{r}</li>)}</ul> },
          { key: "last_at", label: "Last", render: (e) => dt(e.last_at) }]} />}
        <details className="mt-2 text-xs"><summary className="cursor-pointer text-slate-700">Every unavailable or failed request ({(m.unavailable ?? []).length})</summary>
          <DataTable<any> label="Unavailable requests" rows={m.unavailable ?? []} cols={[{ key: "provider", label: "Provider" }, { key: "what", label: "What" },
            { key: "reason", label: "Reason" }, { key: "at", label: "When", render: (u) => dt(u.at) }]} /></details>
        {m.rejected_evidence && <p className="mt-2 text-xs text-slate-600">{m.rejected_evidence.keywords_rejected} keyword(s) rejected · {m.rejected_evidence.pages_failed_or_blocked} page(s) failed or blocked</p>}
      </Section>
    </section>
  );
}
