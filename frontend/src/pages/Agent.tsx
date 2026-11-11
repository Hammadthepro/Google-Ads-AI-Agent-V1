import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import { useAuth } from "../lib/auth";
import { useAction, useClients } from "../lib/hooks";
import { Badge, Button, dt, ErrorBox, Notice, PageHeader, Select, Spinner } from "../components/ui";

const label = (s: string) => s.replace(/_/g, " ").toLowerCase().replace(/^\w/, (c) => c.toUpperCase());
const RESULT_TONE: Record<string, string> = { PASS: "green", WARN: "amber", FAIL: "red", INFO: "blue", UNAVAILABLE: "slate", NA: "slate" };
const STEP_TONE: Record<string, string> = { DONE: "green", RUNNING: "blue", PENDING: "slate", FAILED: "red", BLOCKED: "amber" };
const ACTION_TONE: Record<string, string> = { run: "blue", reuse: "green", skip: "slate" };
const DECISION_TONE: Record<string, string> = { ready: "green", ready_with_conditions: "amber", blocked: "red", not_commercially_viable: "red" };

function Evidence({ it }: { it: any }) {
  return (
    <li className="py-2 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={RESULT_TONE[it.result] ?? "slate"}>{it.result === "NA" ? "N/A" : label(it.result)}</Badge>
        <span className="font-medium text-slate-800">{label(it.check)}</span>
        <Badge tone="slate" title={`Origin: ${label(it.origin)}`}>{label(it.verification)}</Badge>
        <span className="text-xs text-slate-500">confidence {Math.round(it.confidence * 100)}%</span>
      </div>
      <p className="mt-1 text-slate-700">{it.text}</p>
      <p className="mt-0.5 text-xs text-slate-500">Source: {it.source} · {dt(it.retrieved_at)}</p>
      {it.limitations?.length > 0 && <p className="mt-0.5 text-xs text-amber-700">{it.limitations.join(" ")}</p>}
      {it.recommendation && <p className="mt-0.5 text-xs text-emerald-800">Recommendation: {it.recommendation}</p>}
    </li>
  );
}

function Section({ s }: { s: any }) {
  const [open, setOpen] = useState(s.type !== "findings");
  return (
    <div className="rounded-lg border border-slate-200">
      <button type="button" className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm font-medium" aria-expanded={open} onClick={() => setOpen(!open)}>
        {s.title}{s.status && <Badge tone={s.status.startsWith("COMPLETED") ? "green" : s.status === "BLOCKED" || s.status === "FAILED" ? "red" : "amber"}>{label(s.status)}</Badge>}
        {s.stale && <Badge tone="amber">Out of date</Badge>}{s.assessed && <span className="ml-auto text-xs font-normal text-slate-500">{s.assessed} assessed</span>}
      </button>
      {open && (
        <div className="border-t border-slate-100 px-3 py-2">
          {s.type === "findings" && <ul className="divide-y divide-slate-100">{s.items.map((it: any) => <Evidence key={it.finding_id} it={it} />)}{s.items.length === 0 && <li className="py-2 text-sm text-slate-500">Nothing assessed yet.</li>}</ul>}
          {s.type === "table" && (
            <div className="overflow-x-auto"><table className="min-w-full text-sm"><thead><tr>{s.columns.map((c: string) => <th key={c} className="px-2 py-1 text-left text-xs font-semibold text-slate-500">{c}</th>)}</tr></thead>
              <tbody>{s.rows.map((r: any[], i: number) => <tr key={i} className="border-t border-slate-100">{r.map((v, j) => <td key={j} className="px-2 py-1">{String(v)}</td>)}</tr>)}</tbody></table>
              {s.note && <p className="mt-1 text-xs text-slate-500">{s.note}</p>}</div>)}
          {s.type === "numbers" && (
            <div><dl className="grid gap-x-6 gap-y-1 sm:grid-cols-2">{s.items.map((i: any) => <div key={i.label} className="flex justify-between gap-3 border-b border-slate-100 py-1 text-sm"><dt className="text-slate-600">{i.label}</dt><dd className="font-medium text-slate-900">{Array.isArray(i.value) ? i.value.join(" – ") : String(i.value)}</dd></div>)}</dl>
              {s.note && <p className="mt-1 text-xs text-slate-500">{s.note}</p>}</div>)}
          {s.type === "list" && <ul className="list-disc pl-5 text-sm">{s.items.map((t: string, i: number) => <li key={i}>{t}</li>)}</ul>}
        </div>)}
    </div>
  );
}

function Plan({ plan }: { plan: any[] }) {
  if (!plan?.length) return null;
  return (
    <ol className="space-y-1" aria-label="Plan">
      {plan.map((s) => (
        <li key={s.key} className="flex flex-wrap items-center gap-2 text-sm">
          <Badge tone={ACTION_TONE[s.action] ?? "slate"}>{s.action.toUpperCase()}</Badge><span className="font-medium text-slate-800">{s.label}</span>
          <Badge tone={STEP_TONE[s.status] ?? "slate"}>{label(s.status)}</Badge><span className="text-xs text-slate-500">{s.reason}</span>
          {s.status === "BLOCKED" && <span className="text-xs text-amber-700">{s.result}</span>}
        </li>))}
    </ol>
  );
}

function Result({ r, onSay, canAct }: { r: any; onSay: (t: string) => void; canAct: boolean }) {
  return (
    <div className="space-y-3">
      <p className="text-sm font-medium text-slate-900">{r.headline}</p>
      {r.decision_label && <div className="flex items-center gap-2 text-sm"><span className="text-slate-600">Readiness:</span><Badge tone={DECISION_TONE[r.decision] ?? "slate"}>{r.decision_label}</Badge></div>}
      {r.blockers?.length > 0 && <Notice tone="red"><b>Blockers</b><ul className="mt-1 list-disc pl-5">{r.blockers.map((b: any, i: number) => <li key={i}>{b.what}{b.needs ? <span className="text-xs"> — needs: {b.needs}</span> : null}</li>)}</ul></Notice>}
      {r.warnings?.length > 0 && <Notice tone="amber"><b>Warnings</b><ul className="mt-1 list-disc pl-5">{r.warnings.map((w: string, i: number) => <li key={i}>{w}</li>)}</ul></Notice>}
      {r.conditions?.length > 0 && <Notice tone="blue"><b>Conditions</b><ul className="mt-1 list-disc pl-5">{r.conditions.map((w: string, i: number) => <li key={i}>{w}</li>)}</ul></Notice>}
      <div className="space-y-2">{(r.sections ?? []).map((s: any, i: number) => <Section key={i} s={s} />)}</div>
      {r.reused?.length > 0 && <p className="text-xs text-slate-500">Reused, not repeated: {r.reused.map((x: any) => x.why).join(" · ")}</p>}
      {r.legend && <p className="text-xs text-slate-500">{r.legend}</p>}
      {canAct && r.next_actions?.length > 0 && <div className="flex flex-wrap gap-2">{r.next_actions.map((a: any) => <Button key={a.label} size="sm" onClick={() => onSay(a.say)}>{a.label}</Button>)}</div>}
    </div>
  );
}

export function Agent() {
  const [params, setParams] = useSearchParams();
  const clients = useClients();
  const { can } = useAuth();
  const qc = useQueryClient();
  const clientId = params.get("client") ?? "";
  const [convId, setConvId] = useState<string>("");
  const [text, setText] = useState("");
  const bottom = useRef<HTMLDivElement>(null);
  const open = useAction((v) => api.post("/agent/conversations", v), []);
  useEffect(() => { open.mutate({ client_id: clientId || null }, { onSuccess: (t: any) => setConvId(t.id) }); /* eslint-disable-next-line */ }, [clientId]);
  const thread = useQuery({
    queryKey: ["agent", convId], enabled: !!convId, queryFn: () => api.get<any>(`/agent/conversations/${convId}`),
    refetchInterval: (q: any) => (q.state.data?.busy ? 1500 : false),
  });
  const send = useAction((v) => api.post(`/agent/conversations/${convId}/messages`, v), []);
  const resume = useAction((id) => api.post(`/agent/runs/${id}/resume`), []);
  const confirm = useAction((v) => api.post(`/agent/runs/${v.id}/confirm`, { approve: v.approve }), []);
  const refresh = () => qc.invalidateQueries({ queryKey: ["agent", convId] });
  const say = (t: string, choice?: any) => { if (!t && !choice) return; send.mutate({ text: t, choice }, { onSuccess: (d: any) => { qc.setQueryData(["agent", convId], d); setText(""); } }); };
  const t = thread.data;
  useEffect(() => { bottom.current?.scrollIntoView?.({ behavior: "smooth" }); }, [t?.messages?.length, t?.busy]);
  const busy = !!t?.busy || send.isPending;
  const canTask = can("tasks.run");
  return (
    <div className="space-y-4">
      <PageHeader title="Agent" subtitle="One conversation per client. It reads what is already known, runs only what is missing or out of date, and shows its evidence. Nothing changes in Google Ads without your explicit approval." actions={
        <div className="flex items-center gap-2">
          <Select aria-label="Client" value={clientId} onChange={(e) => { const v = e.target.value; setParams(v ? { client: v } : {}); }}
            options={[{ value: "", label: "Choose a client…" }, ...(clients.data ?? []).map((c: any) => ({ value: c.id, label: c.name }))]} />
          <Button size="sm" onClick={() => open.mutate({ client_id: clientId || null, new: true }, { onSuccess: (d: any) => setConvId(d.id) })}>New conversation</Button>
        </div>} />
      <ErrorBox error={open.error || thread.error || send.error || resume.error || confirm.error} />
      {!t ? <Spinner /> : (
        <div className="rounded-xl border border-slate-200 bg-white shadow-sm">
          <div className="max-h-[60vh] space-y-4 overflow-y-auto p-4" role="log" aria-live="polite">
            {t.messages.map((m: any) => {
              const run = m.run_id ? t.runs[m.run_id] : null;
              if (m.role === "user") return <div key={m.id} className="ml-auto max-w-[80%] rounded-xl bg-brand-600 px-4 py-2 text-sm text-white">{m.content}</div>;
              return (
                <div key={m.id} className="max-w-[95%] rounded-xl bg-slate-50 px-4 py-3 text-sm text-slate-800">
                  {m.kind === "plan" && <div className="space-y-2"><p>{m.content.split(" Plan: ")[0]}</p><Plan plan={run?.plan ?? m.data.plan} />{run && <Badge tone={run.status === "FAILED" ? "red" : run.status.startsWith("COMPLETED") ? "green" : "blue"}>{label(run.status)}</Badge>}</div>}
                  {(m.kind === "result" || m.kind === "confirmation") && m.data?.headline && <Result r={m.data} onSay={(x) => say(x)} canAct={!busy} />}
                  {m.kind === "result" && !m.data?.headline && <p>{m.content}</p>}
                  {m.kind === "text" && <div><p>{m.content}</p>{m.data?.suggestions && <div className="mt-2 flex flex-wrap gap-2">{m.data.suggestions.map((s: string) => <button key={s} type="button" className="rounded-full border border-slate-300 bg-white px-3 py-1 text-xs hover:bg-slate-100" onClick={() => say(s)} disabled={busy || !clientId}>{s}</button>)}</div>}</div>}
                  {m.kind === "clarification" && <div className="space-y-2"><p className="font-medium">{m.content}</p><div className="flex flex-wrap gap-2">{m.data.options.map((o: any) => <Button key={o.value} size="sm" disabled={busy || t.pending?.kind !== "clarify"} onClick={() => say("", { value: o.value, label: o.label })}>{o.label}</Button>)}</div></div>}
                  {m.kind === "error" && <div className="space-y-2"><Notice tone="red">{m.content}</Notice>{run?.resumable && <Button size="sm" variant="primary" disabled={busy} onClick={() => resume.mutate(run.id, { onSuccess: refresh })}>Resume from where it stopped</Button>}</div>}
                  {m.kind === "confirmation" && t.pending?.kind === "confirm" && t.pending.run_id === m.run_id && (
                    <div className="mt-3 space-y-2 border-t border-slate-200 pt-3"><p className="font-medium text-slate-900">Confirm before I do anything</p><p>{m.data.confirmation.describes}</p>
                      <div className="flex gap-2"><Button variant="primary" disabled={!canTask || confirm.isPending} onClick={() => confirm.mutate({ id: m.run_id, approve: true }, { onSuccess: refresh })}>Confirm and create the draft task</Button>
                        <Button disabled={confirm.isPending} onClick={() => confirm.mutate({ id: m.run_id, approve: false }, { onSuccess: refresh })}>Cancel</Button></div>
                      {!canTask && <p className="text-xs text-amber-700">Your role cannot create tasks.</p>}</div>)}
                  <p className="mt-1 text-[10px] text-slate-400">{dt(m.created_at)}</p>
                </div>);
            })}
            {t.runs && Object.values(t.runs).some((r: any) => r.resumable && r.status === "INTERRUPTED") && <Notice tone="amber">A request was interrupted. {Object.values(t.runs).filter((r: any) => r.status === "INTERRUPTED").map((r: any) => <Button key={r.id} size="sm" onClick={() => resume.mutate(r.id, { onSuccess: refresh })}>Resume “{r.intent_label}”</Button>)}</Notice>}
            {busy && <div className="text-sm text-slate-500"><Spinner label="Working… stages are saved as they finish, so nothing is lost if this is interrupted." /></div>}
            <div ref={bottom} />
          </div>
          <form className="flex gap-2 border-t border-slate-200 p-3" onSubmit={(e) => { e.preventDefault(); say(text.trim()); }}>
            <input aria-label="Ask the agent" className="block w-full rounded-lg border-0 px-3 py-2 text-sm ring-1 ring-inset ring-slate-300 focus:ring-2 focus:ring-brand-600" placeholder={clientId ? "e.g. Audit this client's business and website" : "Choose a client above, or name one in your request"}
              value={text} maxLength={2000} onChange={(e) => setText(e.target.value)} disabled={busy} />
            <Button variant="primary" type="submit" disabled={busy || !text.trim()}>Send</Button>
          </form>
        </div>)}
    </div>
  );
}
