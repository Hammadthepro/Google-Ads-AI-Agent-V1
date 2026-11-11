import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { useAction } from "../lib/hooks";
import { Badge, Button, Card, dt, ErrorBox, Notice, Spinner } from "./ui";

type Finding = {
  finding_id: string; category: string; subject: string; finding: string; result: string; severity: string; origin: string; verification: string;
  source_type: string; source_reference: string; retrieved_at: string; confidence: number; recommendation: string; limitations: string[];
};
type Mod = {
  module: string; number: number; title: string; status: string; stale: boolean; version: number; summary?: string; findings: Finding[];
  checks_total: number; checks_evaluated: number; missing_information?: { check: string; what: string; needs: string }[];
  client_actions?: { ask: string }[]; retrieved_at?: string | null; depends_on: string[];
};
type Report = {
  modules: Mod[]; stale_modules: string[]; decision: string; decision_label: string; decision_reason: string; account_linked: boolean;
  totals: { checks_specified: number; checks_evaluated: number }; legend: { note: string };
};

const STATUS_TONE: Record<string, string> = {
  COMPLETED: "green", COMPLETED_WITH_WARNINGS: "amber", PARTIALLY_COMPLETED: "amber", NOT_STARTED: "slate", IN_PROGRESS: "blue", WAITING_FOR_CLIENT: "amber",
  WAITING_FOR_INTEGRATION: "amber", BLOCKED: "red", FAILED: "red", NOT_APPLICABLE: "slate",
};
const RESULT_TONE: Record<string, string> = { PASS: "green", WARN: "amber", FAIL: "red", INFO: "blue", UNAVAILABLE: "slate", NA: "slate" };
const DECISION_TONE: Record<string, string> = { ready: "green", ready_with_conditions: "amber", blocked: "red", not_commercially_viable: "red" };
const label = (s: string) => s.replace(/_/g, " ").toLowerCase().replace(/^\w/, (c) => c.toUpperCase());

function ModuleRow({ m, onRerun, busy, canRun }: { m: Mod; onRerun: (k: string) => void; busy: boolean; canRun: boolean }) {
  const [open, setOpen] = useState(false);
  const [all, setAll] = useState(false);
  const shown = m.findings.filter((f) => all || f.source_type !== "none");
  const hidden = m.findings.length - shown.length;
  return (
    <li className="rounded-lg border border-slate-200">
      <button type="button" className="flex w-full flex-wrap items-center gap-2 px-4 py-3 text-left" aria-expanded={open} onClick={() => setOpen(!open)}>
        <span className="flex h-6 w-6 items-center justify-center rounded-full bg-slate-100 text-xs font-semibold text-slate-700">{m.number}</span>
        <span className="font-medium text-slate-900">{m.title}</span>
        <Badge tone={STATUS_TONE[m.status] ?? "slate"}>{label(m.status)}</Badge>
        {m.stale && m.version > 0 && <Badge tone="amber" title="An input this stage depends on has changed since it ran">Out of date</Badge>}
        <span className="ml-auto text-xs text-slate-500">{m.checks_evaluated}/{m.checks_total} checks assessed · v{m.version}</span>
      </button>
      {open && (
        <div className="space-y-3 border-t border-slate-100 px-4 py-3">
          {m.summary && <p className="text-sm text-slate-600">{m.summary}</p>}
          <div className="flex items-center gap-3 text-xs text-slate-500">
            {m.retrieved_at && <span>Last evaluated {dt(m.retrieved_at)}</span>}
            {canRun && <Button size="sm" disabled={busy} onClick={() => onRerun(m.module)}>Re-run this stage</Button>}
            {hidden > 0 && <button type="button" className="text-brand-700 underline" onClick={() => setAll(!all)}>{all ? "Hide" : "Show"} {hidden} not assessed</button>}
          </div>
          {m.client_actions && m.client_actions.length > 0 && (
            <Notice tone="amber"><b>Needed from the client:</b><ul className="mt-1 list-disc pl-5">{m.client_actions.slice(0, 6).map((a, i) => <li key={i}>{a.ask}</li>)}</ul></Notice>)}
          <ul className="divide-y divide-slate-100">
            {shown.map((f) => (
              <li key={f.finding_id} className="py-2 text-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone={RESULT_TONE[f.result] ?? "slate"}>{f.result === "NA" ? "N/A" : label(f.result)}</Badge>
                  <span className="font-medium text-slate-800">{label(f.category)}</span>
                  <Badge tone="slate" title={`Origin: ${label(f.origin)}`}>{label(f.verification)}</Badge>
                  <span className="text-xs text-slate-500">{label(f.origin)} · confidence {Math.round(f.confidence * 100)}%</span>
                </div>
                <p className="mt-1 text-slate-700">{f.finding}</p>
                <p className="mt-0.5 text-xs text-slate-500">Source: {f.source_reference || label(f.source_type)} · {dt(f.retrieved_at)}</p>
                {f.limitations.length > 0 && <p className="mt-0.5 text-xs text-amber-700">{f.limitations.join(" ")}</p>}
                {f.recommendation && <p className="mt-0.5 text-xs text-emerald-800">Recommendation: {f.recommendation}</p>}
              </li>))}
            {shown.length === 0 && <li className="py-2 text-sm text-slate-500">Nothing has been assessed for this stage yet.</li>}
          </ul>
        </div>)}
    </li>
  );
}

export function IntelligencePanel({ clientId, canRun }: { clientId: string; canRun: boolean }) {
  const q = useQuery<Report>({ queryKey: ["intelligence", clientId], queryFn: () => api.get(`/clients/${clientId}/intelligence`) });
  const run = useAction((body) => api.post(`/clients/${clientId}/intelligence/refresh`, body), [["intelligence", clientId]]);
  if (q.isLoading) return <Card title="Intelligence"><Spinner /></Card>;
  if (q.error || !q.data) return <Card title="Intelligence"><ErrorBox error={q.error} /></Card>;
  const r = q.data;
  const stale = r.stale_modules.length;
  return (
    <Card title="Intelligence — one record for research, audit, strategy and monitoring"
      actions={canRun && <Button variant="primary" disabled={run.isPending} onClick={() => run.mutate({ research: true })}>{run.isPending ? "Working…" : stale ? `Bring up to date (${stale} stage${stale > 1 ? "s" : ""})` : "Check for updates"}</Button>}>
      <div className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm text-slate-600">Readiness:</span>
          <Badge tone={DECISION_TONE[r.decision] ?? "slate"}>{r.decision_label}</Badge>
          <span className="text-xs text-slate-500">{r.totals.checks_evaluated} of {r.totals.checks_specified} specified checks assessed</span>
        </div>
        {r.decision_reason && <p className="text-sm text-slate-700">{r.decision_reason}</p>}
        {!r.account_linked && <Notice tone="blue">No Google Ads account is linked. Research, strategy, drafts and validation work without one; link an account on the Accounts page before deployment. Account history stays <b>Unavailable</b>, never zero.</Notice>}
        <ErrorBox error={run.error} />
        <ul className="space-y-2">{r.modules.map((m) => <ModuleRow key={m.module} m={m} canRun={canRun} busy={run.isPending} onRerun={(k) => run.mutate({ modules: [k] })} />)}</ul>
        <p className="text-xs text-slate-500">{r.legend.note} Estimates and forecasts are never shown as verified facts. Ready for campaign generation is not authorisation to publish or spend.</p>
      </div>
    </Card>
  );
}
