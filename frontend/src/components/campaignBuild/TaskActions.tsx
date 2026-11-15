import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api } from "../../api/client";
import { useAuth } from "../../lib/auth";
import { useAction } from "../../lib/hooks";
import { Button, ConfirmDialog, ErrorBox, Textarea } from "../ui";

export type TaskAction = { key: string; label: string; stages: string[]; reuses: string[]; supersedes_approved: boolean; detail?: string; needs_instructions?: boolean };

/** The re-run actions the backend offers for this task now (GET /tasks/{id} "actions"). Each opens a confirmation that lists exactly which
 *  stages run again and what is reused, and calls POST /tasks/{id}/rerun. Nothing is shown that the backend would refuse. */
export function TaskActions({ task, compact }: { task: { id: string; actions?: TaskAction[] }; compact?: boolean }) {
  const { can } = useAuth();
  const qc = useQueryClient();
  const nav = useNavigate();
  const [open, setOpen] = useState<TaskAction | null>(null);
  const [text, setText] = useState("");
  const run = useAction((body: any) => api.post(`/tasks/${task.id}/rerun`, body), []);
  const acts = (task.actions ?? []).filter((a) => can("tasks.run") || (a.key === "rerun_audit" && can("audits.run")));
  if (!acts.length) return null;
  const go = () => {
    const a = open!;
    run.mutate({ action: a.key, instructions: text, confirm_supersede: a.supersedes_approved }, {
      onSuccess: (r: any) => { setOpen(null); setText(""); if (r.new_task_id) nav(`/tasks/${r.new_task_id}`); },
      onSettled: () => ["task", "tasks", "challenger", "approvals"].forEach((k) => qc.invalidateQueries({ queryKey: [k] })),
    });
  };
  return (
    <div className="flex flex-wrap items-center gap-1.5" aria-label="Run again">
      {acts.map((a) => <Button key={a.key} size="sm" onClick={(e?: any) => { e?.stopPropagation?.(); setText(""); setOpen(a); }}>{a.label}</Button>)}
      {!compact && <ErrorBox error={run.error} />}
      <ConfirmDialog open={!!open} tone={open?.supersedes_approved ? "danger" : "primary"} title={`${open?.label ?? ""}?`}
        confirmLabel={run.isPending ? "Starting…" : open?.supersedes_approved ? "Create new version" : "Run"}
        body={open && (<div className="space-y-2">
          {open.detail && <p>{open.detail}.</p>}
          <div><b>Runs again:</b> {open.stages.join(" → ")}</div>
          <div><b>Reused:</b> {open.reuses.length ? open.reuses.join(", ") : "nothing (everything is fetched again)"}</div>
          {open.supersedes_approved && <p className="text-rose-700">The current version is approved. The result is a new version that needs approval again; the approved version stays in the history but can no longer be executed.</p>}
          {!open.supersedes_approved && !["rerun_challenger", "rerun_audit", "resume", "retry_draft"].includes(open.key) && <p>The current version stays valid until the new one is ready. Nothing is sent to Google Ads.</p>}
          {compact && <ErrorBox error={run.error} />}
        </div>)}
        onCancel={() => setOpen(null)} onConfirm={() => { if (!open?.needs_instructions || text.trim()) go(); }}>
        {open?.needs_instructions && <Textarea aria-label="What should change" rows={3} value={text} onChange={(e) => setText(e.target.value)} placeholder="What should change in the new version" />}
      </ConfirmDialog>
    </div>
  );
}

/** Work-queue row: loads the task's current actions only when asked, so the list stays fast. */
export function RowActions({ taskId }: { taskId: string }) {
  const [show, setShow] = useState(false);
  const q = useQuery({ queryKey: ["task", taskId], enabled: show, queryFn: () => api.get(`/tasks/${taskId}`) });
  if (!show) return <Button size="sm" variant="ghost" onClick={(e?: any) => { e?.stopPropagation?.(); setShow(true); }}>Run again…</Button>;
  if (q.isLoading) return <span className="text-2xs text-slate-400">Loading…</span>;
  if (!q.data?.actions?.length) return <span className="text-2xs text-slate-400">Nothing to re-run</span>;
  return <div onClick={(e) => e.stopPropagation()}><TaskActions task={q.data} compact /></div>;
}
