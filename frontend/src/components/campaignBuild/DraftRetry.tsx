import { useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { useAuth } from "../../lib/auth";
import { useAction } from "../../lib/hooks";
import { Badge, Button, dt, ErrorBox } from "../ui";
import { providerName } from "./buildModel";

type Attempt = { attempt: number; kind: string; status: string; error?: string; started_at?: string; finished_at?: string; requested_at?: string;
  calls?: { provider: string; model: string; attempt: number; ok: boolean; error: string; error_kind?: string; stage?: string; finish_reason?: string;
    tokens_in?: number; tokens_out?: number; prompt_estimate?: number }[] };

/** What each failure kind means, so the cause is named rather than guessed at. */
export const KIND_LABEL: Record<string, string> = {
  tpm_exceeded: "Request larger than the per-minute token limit", rate_limited: "Per-minute token budget used up",
  context_length: "Longer than the model's context window", output_truncated: "Answer cut off at the output-token limit",
  malformed_json: "Malformed JSON", schema_invalid: "Did not match the campaign schema", auth_failure: "API key rejected",
  outage: "Provider unavailable", timeout: "Provider timed out", invalid_input: "Request rejected by the provider",
};

const TONE: Record<string, string> = { failed: "red", succeeded: "green", running: "blue", queued: "amber" };

/** The AI drafting stage failed: why (per provider), the attempt history, and a single Retry Draft action that reuses this task's
 *  stored research. Retrying only drafts; the result still goes through challenge, validation and approval. */
export function DraftFailure({ task }: { task: any }) {
  const { can } = useAuth();
  const qc = useQueryClient();
  const retry = useAction(() => api.post(`/tasks/${task.id}/retry-draft`), [["task", task.id], ["tasks"]]);
  const attempts: Attempt[] = task.draft_attempts ?? [];
  const last = attempts[attempts.length - 1];
  const st = task.draft_retry ?? {};
  const busy = retry.isPending || task.status === "RETRYING_DRAFT" || (task.status === "RUNNING" && last?.kind === "retry");
  if (!attempts.length && !st.available) return null;
  const failedCalls = (last?.calls ?? []).filter((c) => !c.ok);
  return (
    <section aria-label="AI drafting" className="rounded-lg border border-rose-200 bg-white">
      <header className="flex flex-wrap items-start justify-between gap-3 border-b border-rose-100 px-5 py-3.5">
        <div className="min-w-0">
          <h2 className="text-[13px] font-semibold text-slate-900">{busy ? "Retrying the AI draft" : "AI drafting failed"}</h2>
          <p className="mt-0.5 text-xs text-slate-500">
            {busy ? "Drafting again from the research already stored for this task. Nothing is created in Google Ads."
              : "The research, keyword clusters and feasibility estimate are stored. Retrying runs only the drafting stage, then challenge, validation and the approval request."}
          </p>
        </div>
        {can("tasks.run") && (st.available || busy) && (
          <Button variant="primary" disabled={busy} onClick={() => retry.mutate(undefined, { onSettled: () => qc.invalidateQueries({ queryKey: ["task", task.id] }) })}>
            {busy ? "Retrying…" : "Retry draft"}
          </Button>)}
      </header>
      <div className="space-y-3 px-5 py-4 text-[13px]">
        {!busy && failedCalls.length > 0 && (
          <ul className="space-y-1.5" aria-label="Provider errors">
            {failedCalls.map((c, i) => (
              <li key={i} className="border-l-2 border-rose-300 pl-2.5">
                <span className="font-medium text-slate-900">{providerName(c.provider)}</span>
                {c.model && <span className="ml-1 font-mono text-2xs text-slate-500">{c.model}</span>}
                <span className="ml-1 text-2xs text-slate-400">{c.stage ? `${c.stage} · ` : ""}call {c.attempt}</span>
                {c.error_kind && <Badge tone={c.error_kind === "rate_limited" ? "amber" : "red"}>{KIND_LABEL[c.error_kind] ?? c.error_kind.replace(/_/g, " ")}</Badge>}
                <div className="break-words text-slate-700">{c.error.replace(/^[a-z_]+: /, "")}</div>
                {(c.tokens_in || c.prompt_estimate) ? <div className="tabular text-2xs text-slate-400">{c.tokens_in ? `${c.tokens_in.toLocaleString()} prompt + ${(c.tokens_out ?? 0).toLocaleString()} completion tokens` : `~${c.prompt_estimate?.toLocaleString()} tokens estimated, not sent`}{c.finish_reason ? ` · finish: ${c.finish_reason}` : ""}</div> : null}
              </li>))}
          </ul>)}
        {!busy && !failedCalls.length && task.error && <p className="break-words text-slate-700">{task.error}</p>}
        {!st.available && !busy && st.reason && <p className="rounded-md bg-amber-50 px-3 py-2 text-amber-950">{st.reason}</p>}
        <ErrorBox error={retry.error} />
        {attempts.length > 0 && (
          <div>
            <div className="mb-1.5 text-xs font-medium text-slate-500">Draft attempts</div>
            <ol className="space-y-1">
              {attempts.map((a) => (
                <li key={a.attempt} className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-slate-600">
                  <span className="tabular w-16 font-medium text-slate-800">Attempt {a.attempt}</span>
                  <Badge tone={TONE[a.status] ?? "slate"}>{a.status}</Badge>
                  <span>{a.kind === "retry" ? "retry" : a.kind === "rerun" ? "re-run" : "first run"}</span>
                  <span className="text-slate-400">{dt(a.finished_at ?? a.started_at ?? a.requested_at)}</span>
                  {(a.calls ?? []).length > 0 && <span className="text-slate-500">· {[...new Set((a.calls ?? []).map((c) => `${providerName(c.provider)}${c.model ? ` (${c.model})` : ""}`))].join(", ")}</span>}
                </li>))}
            </ol>
          </div>)}
      </div>
    </section>
  );
}
