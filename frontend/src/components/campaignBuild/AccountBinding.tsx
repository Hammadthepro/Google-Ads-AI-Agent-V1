import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../api/client";
import { useAuth } from "../../lib/auth";
import { useAction } from "../../lib/hooks";
import { Badge, Button, ErrorBox } from "../ui";

export type Binding = {
  status: "linked" | "none" | "ambiguous"; message: string; customer_id?: string | null; customer_display?: string | null;
  candidates?: { id: string; customer_id: string; display: string; name: string }[]; stale_selection?: boolean;
  approval_customer_id?: string | null; approval_status?: string | null; rebind_available: boolean; choice_needed?: boolean;
};

const fmt = (c?: string | null) => (c && c.length === 10 ? `${c.slice(0, 3)}-${c.slice(3, 6)}-${c.slice(6)}` : c ?? "");

/** Which Google Ads account this task's approval is bound to, and the one action that fixes a mismatch.
 *  The account comes only from the client's link on the Google Ads accounts page; nothing is guessed or silently substituted. */
export function AccountBinding({ task }: { task: any }) {
  const { can } = useAuth();
  const b: Binding | undefined = task.account_binding;
  const [pick, setPick] = useState("");
  const bind = useAction((account_id?: string) => api.post(`/tasks/${task.id}/bind-account`, account_id ? { account_id } : {}),
    [["task", task.id], ["tasks"], ["approvals"]]);
  if (!b || !task.approval_id || b.approval_status === "EXECUTED") return null;
  const bound = b.approval_customer_id;
  const ok = b.status === "linked" && bound === b.customer_id;
  const tone = ok ? "border-emerald-200" : b.rebind_available || b.choice_needed ? "border-amber-300" : "border-slate-200";
  return (
    <section aria-label="Google Ads account binding" className={`rounded-lg border bg-white ${tone}`}>
      <div className="flex flex-wrap items-start justify-between gap-3 px-5 py-3.5">
        <div className="min-w-0 space-y-1">
          <h2 className="flex flex-wrap items-center gap-2 text-[13px] font-semibold text-slate-900">
            Deployment account
            {ok ? <Badge tone="green">bound to {fmt(bound)}</Badge>
              : bound ? <Badge tone="amber">bound to {fmt(bound)}, client now links {b.customer_display ?? "another account"}</Badge>
              : <Badge tone={b.status === "none" ? "slate" : "amber"}>not bound</Badge>}
          </h2>
          <p className="text-xs text-slate-600">
            {b.rebind_available
              ? `This approval was prepared ${bound ? `for ${fmt(bound)}` : "before an account was linked"}. ${b.customer_display} is now linked to the client. Binding prepares a new version for ${b.customer_display} from the stored plan; it must be approved again. Nothing is sent to Google Ads.`
              : b.message}
          </p>
        </div>
        {can("tasks.run") && b.rebind_available && (
          <Button variant="primary" disabled={bind.isPending} onClick={() => bind.mutate(undefined)}>
            {bind.isPending ? "Binding…" : `Bind to ${b.customer_display}`}
          </Button>)}
        {b.status === "none" && can("clients.edit") && <Link to="/accounts"><Button>Link an account →</Button></Link>}
      </div>
      {b.choice_needed && can("tasks.run") && (
        <fieldset className="space-y-1.5 border-t border-slate-100 px-5 py-3 text-[13px]">
          <legend className="sr-only">Choose the deployment account</legend>
          {(b.candidates ?? []).map((c) => (
            <label key={c.id} className="flex items-center gap-2">
              <input type="radio" name={`acct-${task.id}`} value={c.id} checked={pick === c.id} onChange={() => setPick(c.id)} />
              <span className="font-mono text-xs">{c.display}</span>{c.name && <span className="text-slate-600">{c.name}</span>}
            </label>))}
          <Button variant="primary" size="sm" disabled={!pick || bind.isPending} onClick={() => bind.mutate(pick)}>Bind to the chosen account</Button>
        </fieldset>)}
      {bind.error && <div className="px-5 pb-3"><ErrorBox error={bind.error} /></div>}
    </section>
  );
}
