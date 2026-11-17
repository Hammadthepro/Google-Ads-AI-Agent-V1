import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { api } from "../api/client";
import { Badge, dt, ErrorBox, Notice, Spinner } from "./ui";

const n = (v: any, d = 0) => (typeof v === "number" ? v.toLocaleString(undefined, { maximumFractionDigits: d }) : null);
const money = (v: any) => (typeof v === "number" ? `$${v.toLocaleString(undefined, { maximumFractionDigits: 2 })}` : null);
const NA = ({ why }: { why?: string }) => <span className="text-slate-400" title={why}>unavailable</span>;

function H({ id, children }: { id: string; children: ReactNode }) {
  return <h3 id={id} className="mb-2 mt-6 text-sm font-semibold text-slate-900 first:mt-0">{children}</h3>;
}

function Table({ head, rows, label }: { head: string[]; rows: ReactNode[][]; label: string }) {
  if (!rows.length) return <p className="text-xs text-slate-500">None recorded.</p>;
  return (
    <div className="overflow-x-auto">
      <table aria-label={label} className="w-full min-w-[720px] text-left text-xs">
        <thead className="text-slate-500"><tr className="border-b border-slate-100">{head.map((h) => <th key={h} className="py-1.5 pr-3 font-medium">{h}</th>)}</tr></thead>
        <tbody className="divide-y divide-slate-100 align-top">{rows.map((r, i) => <tr key={i}>{r.map((c, j) => <td key={j} className="py-1.5 pr-3 text-slate-700">{c}</td>)}</tr>)}</tbody>
      </table>
    </div>
  );
}

/** Research & Evidence Appendix: exactly the research this plan version was built from, as persisted when the version was created. */
export function ResearchAppendix({ planId }: { planId: string }) {
  const q = useQuery({ queryKey: ["plan-appendix", planId], queryFn: () => api.get(`/plans/${planId}/appendix`) });
  if (q.isLoading) return <Spinner />;
  if (q.error) return <ErrorBox error={q.error} />;
  const a = q.data;
  if (a.error) return <Notice tone="amber">{a.error}</Notice>;
  const kw = a.keywords ?? {};
  const f = a.feasibility ?? {};
  const m = a.methodology ?? {};
  const kwRow = (k: any) => [
    <span className="font-medium text-slate-900">{k.keyword}</span>, (k.match_types ?? []).join(", ") || "—",
    k.search_volume !== null ? <span title={k.volume_period}>{n(k.search_volume)}<span className="block text-2xs text-slate-400">avg/month, 12 mo</span></span> : <NA why={k.unavailable_reason} />,
    k.avg_cpc !== null && k.avg_cpc !== undefined ? <span title={k.avg_cpc_note}>{money(k.avg_cpc)}</span> : <span className="text-slate-400" title={k.avg_cpc_note}>not returned</span>,
    k.competition ?? <NA />, k.competition_index ?? <span className="text-slate-400">not returned</span>,
    k.top_of_page_bid_high ? `${money(k.top_of_page_bid_low) ?? "—"}–${money(k.top_of_page_bid_high)}` : <NA />,
    k.geo && String(k.geo).includes("(business service area") ? <span title={k.geo}>service area*</span> : (k.geo ?? "—"), k.language ?? "—", <Badge tone={/measurement/.test(k.measurement ?? "") ? "green" : k.measurement === "AI-generated idea" ? "violet" : "slate"}>{k.measurement}</Badge>,
    (k.providers ?? []).join(", ") || "—", <span className="block max-w-[16rem] text-slate-600">{k.reason || k.unavailable_reason}</span>];
  const kwHead = ["Keyword", "Match type", "Search volume", "Avg CPC", "Competition", "Comp. index", "Top-of-page bid", "Geo", "Language", "Measurement", "Provider", "Why"];
  return (
    <section aria-labelledby="appendix-title" className="rounded-lg border border-slate-200 bg-white px-5 py-5">
      <h2 id="appendix-title" className="text-base font-semibold text-slate-900">Research & Evidence Appendix</h2>
      <p className="mt-1 text-xs text-slate-500">
        Plan version {a.plan_version} · {a.captured_with_version ? `captured with this version on ${dt(a.captured_at)}` : "assembled from the task's persisted records"} · {m.events_recorded ?? 0} recorded research events.
        Every row comes from a stored record; a metric a provider did not return is shown as unavailable.
      </p>
      {a.note && <div className="mt-2"><Notice tone="amber">{a.note}</Notice></div>}

      <H id="ap-sources">A. Research sources ({(a.sources ?? []).length})</H>
      <Table label="Research sources" head={["Provider", "Source", "Retrieved", "Cached", "Extracted", "Verification", "Influence"]}
        rows={(a.sources ?? []).map((s: any) => [s.provider, s.url ? <a className="break-all text-brand-700 hover:underline" href={s.url} target="_blank" rel="noreferrer noopener">{s.title || s.url}</a> : s.title,
          dt(s.retrieved_at), s.cached ? <Badge tone="amber">cached</Badge> : "live", s.extracted, s.verification, s.influence])} />

      <H id="ap-competitors">B. Competitor intelligence ({(a.competitors ?? []).length})</H>
      <Table label="Competitors" head={["Business", "Origin", "Discovered via", "Services identified", "Pages reviewed", "Public ads seen", "Findings"]}
        rows={(a.competitors ?? []).map((c: any) => [
          <span><span className="font-medium text-slate-900">{c.name}</span>{c.website && <a className="block break-all text-2xs text-brand-700 hover:underline" href={c.website} target="_blank" rel="noreferrer noopener">{c.website}</a>}</span>,
          <Badge tone={c.origin === "client-provided" ? "blue" : "violet"}>{c.origin}</Badge>, c.discovery_source + (c.basis ? ` · ${c.basis}` : ""),
          (c.services ?? []).join(", ") || "—",
          c.website_inspected ? <span>{c.landing_pages_reviewed.length} page(s){c.landing_pages_reviewed.slice(0, 3).map((u: string) => <a key={u} className="block break-all text-2xs text-brand-700" href={u} target="_blank" rel="noreferrer noopener">{u}</a>)}</span>
            : <span className="text-slate-400">website not read</span>,
          (c.public_ads ?? []).length ? c.public_ads.slice(0, 2).map((ad: any, i: number) => <span key={i} className="block">“{ad.title}” <span className="text-2xs text-slate-400">for “{ad.query}”</span></span>) : <span className="text-slate-400">none observed</span>,
          (c.findings ?? []).join("; ") || "—"])} />

      <H id="ap-keywords">C. Keyword research · {kw.measured ?? 0} of {kw.total ?? 0} with measured search volume</H>
      {kw.keyword_planner && <p className="mb-2 text-xs text-slate-600">Keyword Planner: {kw.keyword_planner.available ? "used" : `not used — ${kw.keyword_planner.reason || "unavailable"}`}. {kw.policy}</p>}
      <div className="mb-1 text-xs font-medium text-slate-700">Selected ({(kw.selected ?? []).length})</div>
      <Table label="Selected keywords" head={kwHead} rows={(kw.selected ?? []).map(kwRow)} />
      <div className="mb-1 mt-3 text-xs font-medium text-slate-700">Rejected ({kw.rejected_total ?? 0}{(kw.rejected ?? []).length < (kw.rejected_total ?? 0) ? `, first ${(kw.rejected ?? []).length} shown` : ""})</div>
      <Table label="Rejected keywords" head={kwHead} rows={(kw.rejected ?? []).map(kwRow)} />
      {[...(kw.selected ?? []), ...(kw.rejected ?? [])].some((k: any) => String(k.geo ?? "").includes("(business service area")) && (
        <p className="mt-1.5 text-2xs text-slate-500">* {String([...(kw.selected ?? []), ...(kw.rejected ?? [])].find((k: any) => String(k.geo ?? "").includes("(business service area")).geo)}: these rows have no Keyword Planner geo targeting, so the business's own service area is shown.</p>)}

      <H id="ap-feasibility">D. Feasibility and market measurements <Badge tone="amber">estimates</Badge></H>
      <dl className="grid gap-x-6 gap-y-2 text-xs sm:grid-cols-3">
        {[["Verdict", f.verdict ?? "—"], ["Expected leads / week (estimate)", f.expected_weekly_leads ?? "—"],
          ["Realistic range / week", Array.isArray(f.realistic_weekly_range) ? f.realistic_weekly_range.join("–") : "—"],
          ["Estimated cost per lead", money(f.estimated_cpl) ?? "—"], ["Allowed cost per lead", money(f.allowed_cpl) ?? "—"],
          ["Suggested monthly budget", money(f.suggested_monthly_budget) ?? "—"], ["CPC assumption", money(f.cpc_assumption) ?? "not recorded"],
          ["Conversion-rate assumption", typeof f.conversion_rate_assumption === "number" ? `${(f.conversion_rate_assumption * (f.conversion_rate_assumption < 1 ? 100 : 1)).toFixed(1)}%` : "not recorded"],
          ["Competitor density", f.competitor_density ? `${f.competitor_density.client_provided} client-provided · ${f.competitor_density.discovered} discovered · ${f.competitor_density.local_businesses_found} local businesses` : "—"],
        ].map(([k, v]) => <div key={k as string}><dt className="text-slate-400">{k}</dt><dd className="text-slate-800">{v as any}</dd></div>)}
      </dl>
      <p className="mt-2 text-xs text-slate-500">{f.limitations}</p>

      <H id="ap-method">E. Research methodology</H>
      <dl className="space-y-2 text-xs">
        <div><dt className="font-medium text-slate-700">Integrations used</dt><dd className="text-slate-600">{(m.integrations_used ?? []).map((i: any) => `${i.provider} (${i.records})`).join(" · ") || "none recorded"}</dd></div>
        <div><dt className="font-medium text-slate-700">Searches performed</dt><dd className="text-slate-600">{(m.searches ?? []).join(" · ") || "none recorded"}</dd></div>
        <div><dt className="font-medium text-slate-700">Served from cache</dt><dd className="text-slate-600">{(m.cached ?? []).join(" · ") || "none"}</dd></div>
        <div><dt className="font-medium text-slate-700">Unavailable or failed</dt><dd className="text-slate-600">{(m.unavailable ?? []).length ? (m.unavailable as any[]).map((u, i) => <span key={i} className="block">{u.what}{u.reason ? ` — ${u.reason}` : ""}</span>) : "none recorded"}</dd></div>
        <div><dt className="font-medium text-slate-700">Rejected evidence</dt><dd className="text-slate-600">{m.rejected_evidence ? `${m.rejected_evidence.keywords_rejected} keyword(s) rejected · ${m.rejected_evidence.pages_failed_or_blocked} page(s) failed or blocked` : "—"}</dd></div>
        <div><dt className="font-medium text-slate-700">How the structure was derived</dt><dd className="text-slate-600"><ul className="list-disc pl-4">{(m.derivation ?? []).map((d: string) => <li key={d}>{d}</li>)}</ul></dd></div>
      </dl>
    </section>
  );
}
