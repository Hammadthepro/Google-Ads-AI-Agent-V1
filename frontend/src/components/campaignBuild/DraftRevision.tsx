import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { useAuth } from "../../lib/auth";
import { useAction } from "../../lib/hooks";
import { Badge, Button, ErrorBox, Textarea } from "../ui";

const MAX = 2000;

/** Regenerate the draft, or revise it with instructions, from the research already stored for this task. The current version and its
 *  approval stay valid until the new version exists; the new one is challenged, validated and needs approval again. */
export function DraftRevision({ task }: { task: any }) {
  const { can } = useAuth();
  const qc = useQueryClient();
  const [text, setText] = useState("");
  const revise = useAction((instructions: string) => api.post(`/tasks/${task.id}/revise-draft`, { instructions }), [["task", task.id], ["tasks"]]);
  const st = task.revision ?? {};
  const attempts: any[] = task.draft_attempts ?? [];
  const last = attempts[attempts.length - 1];
  const running = revise.isPending || (last?.kind === "revision" && ["queued", "running"].includes(last?.status)
    && ["RETRYING_DRAFT", "RUNNING"].includes(task.status));
  if (!can("tasks.run") || (!st.available && !running && !st.reason)) return null;
  const go = (instructions: string) => revise.mutate(instructions, { onSettled: () => qc.invalidateQueries({ queryKey: ["task", task.id] }) });
  const failed = last?.kind === "revision" && last?.status === "failed";
  return (
    <section aria-label="New draft version" className="rounded-lg border border-slate-200 bg-white">
      <header className="flex flex-wrap items-start justify-between gap-3 border-b border-slate-100 px-5 py-3.5">
        <div className="min-w-0">
          <h2 className="flex items-center gap-2 text-[13px] font-semibold text-slate-900">
            New version {st.current_version ? <Badge>current: v{st.current_version}</Badge> : null}
            {running && <Badge tone="blue" dot>drafting v{(st.current_version ?? 1) + 1}</Badge>}
          </h2>
          <p className="mt-0.5 text-xs text-slate-500">
            {running ? "Drafting from the stored research. The current version and its approval stay valid until the new one is ready."
              : "Uses the research already stored for this task (nothing is researched again). The new version is reviewed, validated and needs approval; the current approval is then superseded."}
          </p>
        </div>
      </header>
      <div className="space-y-2.5 px-5 py-4">
        {!st.available && !running && st.reason && <p className="text-xs text-slate-600">{st.reason}</p>}
        {failed && !running && <p role="alert" className="text-xs text-rose-700">The last revision failed and nothing changed: {last.error}</p>}
        {st.available && !running && (<>
          <Textarea aria-label="Revision instructions" rows={3} maxLength={MAX} value={text} onChange={(e) => setText(e.target.value)}
            placeholder="What should change? e.g. “Lead with same-day emergency service”, “Split water heater repair and installation”, “Shorter headlines”" />
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="primary" disabled={!text.trim()} onClick={() => go(text.trim())}>Revise with these instructions</Button>
            <Button onClick={() => go("")}>Regenerate</Button>
            <span className="tabular text-2xs text-slate-400">{text.length}/{MAX}</span>
          </div>
        </>)}
        <ErrorBox error={revise.error} />
      </div>
    </section>
  );
}
