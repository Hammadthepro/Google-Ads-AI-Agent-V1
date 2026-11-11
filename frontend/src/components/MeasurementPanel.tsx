import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { useAuth } from "../lib/auth";
import { useAction } from "../lib/hooks";
import { Button, Card, dt, EmptyState, ErrorBox, Notice, Spinner } from "./ui";

type Finding = { key: string; inspected: string; result: "ok" | "issue" | "attention" | "not_assessable"; actual: string; evidence: string;
  severity: string | null; impact: string; action: string; action_type: string; dependency: string };
const MARK: Record<Finding["result"], { label: string; cls: string }> = {
  issue: { label: "Problem found", cls: "bg-rose-500" }, attention: { label: "Worth a look", cls: "bg-amber-400" },
  not_assessable: { label: "Could not be read", cls: "bg-slate-300" }, ok: { label: "As expected", cls: "bg-emerald-500" },
};
const ACTION: Record<string, string> = { needs_approval: "A person makes this change in Google Ads or on the site", needs_dependency: "Needs", informational: "" };

/** Conversion measurement as findings. No overall status: each row is what was inspected, what was found, and what follows from it. */
export function MeasurementPanel({ businessId }: { businessId: string }) {
  const { can } = useAuth();
  const q = useQuery<any>({ queryKey: ["measurement", businessId], queryFn: () => api.get(`/businesses/${businessId}/measurement`) });
  const run = useAction(() => api.post(`/businesses/${businessId}/measurement/run`), [["measurement", businessId], ["businesses"]]);
  const m = q.data;
  return (
    <Card title="Conversion measurement" actions={can("audits.run") && <Button size="sm" onClick={() => run.mutate({})} disabled={run.isPending}>{run.isPending ? "Auditing…" : m?.evaluated_at ? "Audit again" : "Run audit"}</Button>}>
      <ErrorBox error={run.error} />
      {q.isLoading ? <Spinner /> : !m ? <EmptyState message="Not audited yet. The audit reads the website and, when an account is linked, its conversion actions and recorded conversions." /> : (
        <div className="space-y-4">
          {m.outdated && <Notice tone="amber">{m.outdated}</Notice>}
          {m.evaluated_at && <p className="text-xs text-slate-500">Audited {dt(m.evaluated_at)} · {m.account_assessed ? "website and Google Ads account" : "website only (no account was read)"} · {m.counts.issue} problem(s), {m.counts.attention} to look at, {m.counts.not_assessable} could not be read</p>}
          {m.conversion_bidding?.reason && m.evaluated_at && <p className="text-sm text-slate-700"><span className="font-medium">Conversion-based bidding: </span>{m.conversion_bidding.reason}</p>}
          <ul className="divide-y divide-slate-100">
            {(m.findings as Finding[]).map((f) => (
              <li key={f.key} className="grid gap-x-4 gap-y-1 py-3 sm:grid-cols-[minmax(0,14rem)_minmax(0,1fr)]">
                <div className="flex items-start gap-2">
                  <span aria-hidden className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${MARK[f.result].cls}`} />
                  <div><div className="text-sm font-medium text-slate-900">{f.inspected}</div><div className="text-xs text-slate-500">{MARK[f.result].label}{f.severity ? ` · ${f.severity} impact` : ""}</div></div>
                </div>
                <div className="text-sm">
                  <p className="text-slate-800">{f.actual}</p>
                  <p className="text-xs text-slate-500">Evidence: {f.evidence}</p>
                  {f.impact && <p className="mt-1 text-slate-600">{f.impact}</p>}
                  {f.action && <p className="mt-1 text-slate-900"><span className="font-medium">Recommended: </span>{f.action}</p>}
                  {(f.action_type !== "informational") && <p className="text-xs text-slate-500">{ACTION[f.action_type]}{f.action_type === "needs_dependency" ? ` ${f.dependency}` : ""}</p>}
                </div>
              </li>))}
          </ul>
          <p className="text-xs text-slate-500">These findings inform audits, strategy and recommendations. They do not block research, drafting or deployment.</p>
        </div>)}
    </Card>
  );
}
