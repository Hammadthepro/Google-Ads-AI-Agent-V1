import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../../api/client";
import { useAuth } from "../../lib/auth";
import { useAction } from "../../lib/hooks";
import { Badge, Button, ConfirmDialog, cx, dt, ErrorBox, Spinner, Textarea } from "../ui";

const SEV: Record<string, string> = { HIGH: "red", MEDIUM: "amber", LOW: "slate" };
const STATUS: Record<string, [string, string]> = {
  open: ["Open", "amber"], applied: ["Applied as a new version", "green"], dismissed: ["Dismissed", "slate"],
  revise_requested: ["Revision requested", "blue"], edited: ["Edited manually", "green"],
};

/** The Challenger's findings on the current version, each with what it would change and the actions that resolve it. Applying a fix goes
 *  through the same editor and validation as a manual edit and creates a new version; nothing is changed by the Challenger itself. */
export function ChallengerFindings({ taskId }: { taskId: string }) {
  const { can } = useAuth();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["challenger", taskId], queryFn: () => api.get(`/tasks/${taskId}/challenger`) });
  const refresh = () => ["challenger", "task", "approvals", "plan-versions", "plan"].forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
  const resolve = useAction(({ key, ...body }: any) => api.post(`/tasks/${taskId}/challenger/${key}/resolve`, body), []);
  const review = useAction(() => api.post(`/tasks/${taskId}/rerun`, { action: "rerun_challenger" }), []);
  const [dismiss, setDismiss] = useState<any>(null);
  const [reason, setReason] = useState("");
  const [confirm, setConfirm] = useState<any>(null);
  if (q.isLoading) return <Spinner />;
  const d = q.data;
  if (!d) return <ErrorBox error={q.error} />;
  const approved = d.approval_status === "APPROVED";
  const act = (f: any, action: string, extra: any = {}) => resolve.mutate({ key: f.key, action, ...extra }, { onSettled: refresh });
  const openCount = d.findings.filter((f: any) => f.status === "open").length;
  return (
    <section aria-label="Challenger findings" className="rounded-lg border border-slate-200 bg-white">
      <header className="flex flex-wrap items-start justify-between gap-3 border-b border-slate-100 px-5 py-3.5">
        <div className="min-w-0">
          <h2 className="flex flex-wrap items-center gap-2 text-[13px] font-semibold text-slate-900">
            Challenger review {d.verdict && <Badge tone={d.verdict === "APPROVE" ? "green" : d.verdict === "REJECT" ? "red" : "amber"}>{d.verdict}</Badge>}
            {d.reviewed_plan_version && <Badge>reviewed v{d.reviewed_plan_version}{d.reviewed_plan_version !== d.plan_version ? ` · current v${d.plan_version}` : ""}</Badge>}
            {openCount > 0 && <Badge tone="amber">{openCount} open</Badge>}
          </h2>
          <p className="mt-0.5 text-xs text-slate-500">
            {d.performed ? `${d.reviewer?.provider ?? "Challenger AI"}${d.reviewer?.model ? ` · ${d.reviewer.model}` : ""}. Advisory only: it never approves, publishes or overrides the validation rules.`
              : `Independent review not performed: ${d.reason || "no review recorded for this version"}`}
          </p>
        </div>
        {can("tasks.run") && <Button size="sm" disabled={review.isPending} onClick={() => review.mutate(undefined, { onSettled: refresh })}>
          {review.isPending ? "Reviewing…" : "Re-run Challenger review"}</Button>}
      </header>
      <ErrorBox error={resolve.error || review.error} />
      {d.findings.length === 0 ? <p className="px-5 py-5 text-[13px] text-slate-500">{d.performed ? "No concerns were raised." : "No findings."}</p> : (
        <ol className="divide-y divide-slate-100">
          {d.findings.map((f: any) => {
            const [label, tone] = STATUS[f.status] ?? STATUS.open;
            return (
              <li key={f.key} aria-label={`Concern: ${f.message}`} className={cx("px-5 py-4", f.status !== "open" && "bg-slate-50/60")}>
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone={SEV[f.severity] ?? "slate"}>{f.severity}</Badge>
                  <span className="text-2xs uppercase tracking-wide text-slate-500">{String(f.category).replace(/_/g, " ")}</span>
                  <span className="text-2xs text-slate-500">· {f.component}</span>
                  <span className="ml-auto"><Badge tone={tone}>{label}</Badge></span>
                </div>
                <p className="mt-1.5 text-[13px] font-medium text-slate-900">{f.message}</p>
                {f.evidence && <p className="mt-1 text-xs text-slate-600"><span className="text-slate-400">Evidence: </span>{f.evidence}</p>}
                {f.suggestion && <p className="mt-1 text-xs text-slate-700"><span className="text-slate-400">Suggested resolution: </span>{f.suggestion}</p>}
                {f.changes.length > 0 && (
                  <table aria-label="Proposed change" className="mt-2 w-full max-w-2xl text-left text-xs">
                    <thead className="text-slate-400"><tr><th className="py-1 pr-3 font-medium">Component</th><th className="py-1 pr-3 font-medium">Current</th><th className="py-1 font-medium">Proposed</th></tr></thead>
                    <tbody>{f.changes.map((c: any, i: number) => (
                      <tr key={i} className="align-top"><td className="py-0.5 pr-3 text-slate-600">{c.component}</td>
                        <td className="py-0.5 pr-3"><del className="text-rose-700">{c.original}</del></td><td className="py-0.5 text-emerald-700">{c.replacement}</td></tr>))}</tbody>
                  </table>)}
                {f.changes.length > 0 && <p className="mt-1 text-2xs text-slate-400">{f.fix_source === "challenger" ? "Change stated by the Challenger, checked against the draft." : "Change read from the suggestion by rules; it matches keywords present in the draft."}</p>}
                {f.resolution && f.status !== "open" && (
                  <p className="mt-1.5 text-2xs text-slate-500">{label} {dt(f.resolution.at)}{f.resolution.reason ? ` — “${f.resolution.reason}”` : ""}</p>)}
                {f.status === "open" && can("approvals.submit") && (
                  <div className="mt-2.5 flex flex-wrap gap-2">
                    {f.edits.length > 0 && <Button size="sm" variant="primary" disabled={resolve.isPending}
                      onClick={() => approved ? setConfirm(f) : act(f, "apply")}>Apply suggestion</Button>}
                    {d.approval_id && <Link to={`/approvals/${d.approval_id}#edit`}><Button size="sm">Edit manually</Button></Link>}
                    {can("tasks.run") && <Button size="sm" disabled={resolve.isPending} onClick={() => act(f, "revise")}>Ask Main AI to revise</Button>}
                    <Button size="sm" variant="ghost" onClick={() => { setReason(""); setDismiss(f); }}>Dismiss…</Button>
                  </div>)}
              </li>);
          })}
        </ol>)}
      {d.history.length > 0 && (
        <details className="border-t border-slate-100 px-5 py-3 text-xs">
          <summary className="cursor-pointer text-slate-500">Resolution history ({d.history.length})</summary>
          <ul className="mt-2 space-y-1">{d.history.map((h: any, i: number) => (
            <li key={i}><span className="text-slate-400">{dt(h.at)}</span> · <b>{h.action.replace(/_/g, " ")}</b> · {h.objection}{h.reason ? ` — “${h.reason}”` : ""}</li>))}</ul>
        </details>)}
      <ConfirmDialog open={!!dismiss} title="Dismiss this concern?" confirmLabel="Dismiss" body={<>The concern and your reason stay in the audit history. <b>{dismiss?.message}</b></>}
        onCancel={() => setDismiss(null)} onConfirm={() => { act(dismiss, "dismiss", { reason }); setDismiss(null); }}>
        <Textarea aria-label="Reason for dismissing" rows={3} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Why this concern does not apply (required)" />
      </ConfirmDialog>
      <ConfirmDialog open={!!confirm} tone="danger" title="Replace the approved version?" confirmLabel="Create new version"
        body={<>This version is approved. Applying the change creates version {d.plan_version + 1}, which must be validated and approved again. The approved version is kept unchanged in the history and can no longer be executed.</>}
        onCancel={() => setConfirm(null)} onConfirm={() => { act(confirm, "apply", { confirm_supersede: true }); setConfirm(null); }} />
    </section>
  );
}
