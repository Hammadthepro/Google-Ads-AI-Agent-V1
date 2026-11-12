import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import { useAuth } from "../lib/auth";
import { useAction, useClients } from "../lib/hooks";
import { Icon } from "../components/icons";
import { Badge, Button, cx, dt, ErrorBox, Notice, Select, since, Spinner } from "../components/ui";

const label = (s: string) => s.replace(/_/g, " ").toLowerCase().replace(/^\w/, (c) => c.toUpperCase());
const RESULT_TONE: Record<string, string> = { PASS: "green", WARN: "amber", FAIL: "red", INFO: "blue", UNAVAILABLE: "slate", NA: "slate" };
const ACTION_TONE: Record<string, string> = { run: "blue", reuse: "green", skip: "slate" };
const DECISION_TONE: Record<string, string> = { ready: "green", ready_with_conditions: "amber", blocked: "red", not_commercially_viable: "red" };

function Evidence({ it }: { it: any }) {
  return (
    <li className="py-3 text-[13px]">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={RESULT_TONE[it.result] ?? "slate"}>{it.result === "NA" ? "N/A" : label(it.result)}</Badge>
        <span className="font-medium text-slate-800">{label(it.check)}</span>
        <Badge tone="slate" title={`Origin: ${label(it.origin)}`}>{label(it.verification)}</Badge>
        <span className="text-xs text-slate-500">confidence {Math.round(it.confidence * 100)}%</span>
      </div>
      <p className="mt-1.5 leading-6 text-slate-700">{it.text}</p>
      <p className="mt-0.5 text-xs text-slate-500">Source: {it.source} · {dt(it.retrieved_at)}</p>
      {it.limitations?.length > 0 && <p className="mt-0.5 text-xs text-amber-700">{it.limitations.join(" ")}</p>}
      {it.recommendation && <p className="mt-0.5 text-xs text-emerald-800">Recommendation: {it.recommendation}</p>}
    </li>
  );
}

function Section({ s }: { s: any }) {
  const [open, setOpen] = useState(s.type !== "findings");
  return (
    <div className="overflow-hidden rounded-lg border border-slate-200 bg-white">
      <button type="button" className="flex w-full flex-wrap items-center gap-2 px-4 py-2.5 text-left text-[13px] font-medium text-slate-900 transition-colors hover:bg-slate-50" aria-expanded={open} onClick={() => setOpen(!open)}>
        <svg aria-hidden viewBox="0 0 16 16" className={cx("h-3.5 w-3.5 shrink-0 text-slate-400 transition-transform", open && "rotate-90")} fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="M6 3.5l4.5 4.5-4.5 4.5" /></svg>
        {s.title}{s.status && <Badge tone={s.status.startsWith("COMPLETED") ? "green" : s.status === "BLOCKED" || s.status === "FAILED" ? "red" : "amber"}>{label(s.status)}</Badge>}
        {s.stale && <Badge tone="amber">Out of date</Badge>}{s.assessed && <span className="ml-auto text-xs font-normal text-slate-500">{s.assessed} assessed</span>}
      </button>
      {open && (
        <div className="border-t border-slate-100 px-4 py-2">
          {s.type === "findings" && <ul className="divide-y divide-slate-100">{s.items.map((it: any) => <Evidence key={it.finding_id} it={it} />)}{s.items.length === 0 && <li className="py-2 text-sm text-slate-500">Nothing assessed yet.</li>}</ul>}
          {s.type === "table" && (
            <div className="overflow-x-auto"><table className="min-w-full text-sm"><thead><tr>{s.columns.map((c: string) => <th key={c} scope="col" className="px-2 py-1.5 text-left text-xs font-medium text-slate-500">{c}</th>)}</tr></thead>
              <tbody>{s.rows.map((r: any[], i: number) => <tr key={i} className="border-t border-slate-100">{r.map((v, j) => <td key={j} className="px-2 py-1.5 text-[13px]">{String(v)}</td>)}</tr>)}</tbody></table>
              {s.note && <p className="mt-1 text-xs text-slate-500">{s.note}</p>}</div>)}
          {s.type === "numbers" && (
            <div><dl className="grid gap-x-6 gap-y-1 sm:grid-cols-2">{s.items.map((i: any) => <div key={i.label} className="flex justify-between gap-3 border-b border-slate-100 py-1 text-sm"><dt className="text-slate-600">{i.label}</dt><dd className="font-medium text-slate-900">{Array.isArray(i.value) ? i.value.join(" – ") : String(i.value)}</dd></div>)}</dl>
              {s.note && <p className="mt-1 text-xs text-slate-500">{s.note}</p>}</div>)}
          {s.type === "list" && <ul className="list-disc pl-5 text-sm">{s.items.map((t: string, i: number) => <li key={i}>{t}</li>)}</ul>}
        </div>)}
    </div>
  );
}

const DOT: Record<string, string> = { DONE: "bg-emerald-500", RUNNING: "bg-brand-500 animate-pulse", PENDING: "bg-slate-300", FAILED: "bg-rose-500", BLOCKED: "bg-amber-500" };

/** The persisted stages of a request: what was decided for each (run / reuse / skip), why, and where it stands. */
function Plan({ plan }: { plan: any[] }) {
  if (!plan?.length) return null;
  return (
    <ol className="space-y-1.5 border-l border-slate-200 pl-3" aria-label="Plan">
      {plan.map((s) => (
        <li key={s.key} className="relative text-[13px]">
          <span aria-hidden className={`absolute -left-[17px] top-1.5 h-2 w-2 rounded-full ring-2 ring-white ${DOT[s.status] ?? DOT.PENDING}`} />
          <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
            <span className="font-medium text-slate-900">{s.label}</span>
            <Badge tone={ACTION_TONE[s.action] ?? "slate"}>{s.action.toUpperCase()}</Badge>
            <span className="text-2xs text-slate-500">{label(s.status)}</span>
          </div>
          <p className="text-xs leading-5 text-slate-500">{s.reason}</p>
          {s.status === "BLOCKED" && <p className="text-xs text-amber-700">{s.result}</p>}
        </li>))}
    </ol>
  );
}

function Result({ r, onSay, canAct }: { r: any; onSay: (t: string) => void; canAct: boolean }) {
  return (
    <div className="space-y-3">
      <p className="text-[15px] font-semibold leading-6 text-slate-900">{r.headline}</p>
      {r.decision_label && <div className="flex items-center gap-2 text-[13px]"><span className="text-slate-600">Readiness:</span><Badge tone={DECISION_TONE[r.decision] ?? "slate"}>{r.decision_label}</Badge></div>}
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

/** What the agent is working with: the client, its account, readiness and what is out of date. All read from the shared record. */
function ContextPanel({ clientId }: { clientId: string }) {
  const q = useQuery<any>({ queryKey: ["intelligence", clientId], enabled: !!clientId, queryFn: () => api.get(`/clients/${clientId}/intelligence`) });
  const r = q.data;
  if (!clientId) return <p className="p-5 text-[13px] leading-5 text-slate-500">Choose a client to see what is known about it: its account, readiness and which research is out of date.</p>;
  if (q.isLoading) return <div className="p-4"><Spinner /></div>;
  if (!r || !Array.isArray(r.modules)) return null;
  return (
    <div className="space-y-6 p-5 text-[13px]">
      <section>
        <h3 className="text-xs font-medium text-slate-500">Client</h3>
        <Link to={`/clients/${clientId}`} className="mt-1 block font-medium text-slate-900 hover:text-brand-700">{r.client_code}</Link>
        <p className="mt-1 text-slate-600">{r.account_linked ? "Google Ads account linked" : "No Google Ads account linked. Research and drafting work without one."}</p>
      </section>
      <section>
        <h3 className="text-xs font-medium text-slate-500">Readiness</h3>
        <div className="mt-1"><Badge tone={DECISION_TONE[r.decision] ?? "slate"}>{r.decision_label}</Badge></div>
        <p className="mt-1 text-xs text-slate-500">{r.totals.checks_evaluated} of {r.totals.checks_specified} checks assessed</p>
      </section>
      <section>
        <h3 className="text-xs font-medium text-slate-500">Stages</h3>
        <ul className="mt-1.5 space-y-1">
          {r.modules.map((m: any) => (
            <li key={m.module} className="flex items-center gap-2">
              <span aria-hidden className={cx("h-1.5 w-1.5 shrink-0 rounded-full", m.version === 0 ? "bg-slate-300" : m.stale ? "bg-amber-400" : m.status === "BLOCKED" || m.status === "FAILED" ? "bg-rose-500" : "bg-emerald-500")} />
              <span className="min-w-0 flex-1 truncate text-slate-700">{m.title}</span>
              <span className="shrink-0 text-2xs text-slate-400">{m.version === 0 ? "not run" : m.stale ? "out of date" : `${m.checks_evaluated}/${m.checks_total}`}</span>
            </li>))}
        </ul>
      </section>
      {r.research?.exists && <p className="text-xs text-slate-500">Research from {String(r.research.date).slice(0, 10)}: {r.research.reason?.toLowerCase()}.</p>}
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
  const [panel, setPanel] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);
  const open = useAction((v) => api.post("/agent/conversations", v), []);
  useEffect(() => { open.mutate({ client_id: clientId || null }, { onSuccess: (t: any) => setConvId(t.id) }); /* eslint-disable-next-line */ }, [clientId]);
  const thread = useQuery({
    queryKey: ["agent", convId], enabled: !!convId, queryFn: () => api.get<any>(`/agent/conversations/${convId}`),
    refetchInterval: (q: any) => (q.state.data?.busy ? 1500 : false),
  });
  const history = useQuery<any>({ queryKey: ["agent-history", clientId, convId], queryFn: () => api.get(`/agent/conversations${clientId ? `?client_id=${clientId}` : ""}`) });
  const send = useAction((v) => api.post(`/agent/conversations/${convId}/messages`, v), []);
  const resume = useAction((id) => api.post(`/agent/runs/${id}/resume`), []);
  const confirm = useAction((v) => api.post(`/agent/runs/${v.id}/confirm`, { approve: v.approve }), []);
  const refresh = () => { qc.invalidateQueries({ queryKey: ["agent", convId] }); qc.invalidateQueries({ queryKey: ["intelligence", clientId] }); };
  const say = (t: string, choice?: any) => { if (!t && !choice) return; send.mutate({ text: t, choice }, { onSuccess: (d: any) => { qc.setQueryData(["agent", convId], d); setText(""); } }); };
  const t = thread.data;
  useEffect(() => { bottom.current?.scrollIntoView?.({ behavior: "smooth" }); }, [t?.messages?.length, t?.busy]);
  useEffect(() => { if (t && !t.busy) qc.invalidateQueries({ queryKey: ["intelligence", clientId] }); /* eslint-disable-next-line */ }, [t?.busy]);
  const busy = !!t?.busy || send.isPending;
  const canTask = can("tasks.run");
  const convs: any[] = Array.isArray(history.data) ? history.data : [];
  const interrupted = t?.runs ? Object.values(t.runs).filter((r: any) => r.status === "INTERRUPTED") : [];
  const newConversation = () => open.mutate({ client_id: clientId || null, new: true }, { onSuccess: (d: any) => setConvId(d.id) });
  const submit = () => say(text.trim());
  return (
    <div className="flex h-[calc(100vh-52px)] bg-white">
      {/* history */}
      <aside className="hidden w-[248px] shrink-0 flex-col border-r border-slate-200 bg-slate-50/60 xl:flex" aria-label="Conversations">
        <div className="flex items-center justify-between px-4 pb-2 pt-4">
          <span className="text-xs font-medium text-slate-500">Conversations</span>
          <Button size="sm" variant="ghost" onClick={newConversation}><Icon name="plus" className="h-3.5 w-3.5" /><span className="sr-only">New conversation</span><span aria-hidden>New</span></Button>
        </div>
        <ul className="pane-scroll flex-1 space-y-px overflow-y-auto px-2 pb-3">
          {convs.map((c) => (
            <li key={c.id}><button type="button" onClick={() => setConvId(c.id)} aria-current={c.id === convId}
              className={cx("w-full rounded-md px-2.5 py-2 text-left text-[13px] transition-colors", c.id === convId ? "bg-white font-medium text-slate-900 shadow-inset" : "text-slate-600 hover:bg-white/70 hover:text-slate-900")}>
              <span className="block truncate">{c.title || "Conversation"}</span><span className="block text-2xs font-normal text-slate-400" title={dt(c.updated_at)}>{since(c.updated_at)}</span></button></li>))}
          {convs.length === 0 && <li className="px-2.5 py-3 text-xs leading-5 text-slate-400">Earlier conversations appear here and can be resumed.</li>}
        </ul>
      </aside>
      {/* thread */}
      <section className="flex min-w-0 flex-1 flex-col" aria-label="Agent">
        <header className="flex items-center gap-2 border-b border-slate-200 px-4 py-2.5 sm:px-6">
          <h1 className="sr-only">Agent</h1>
          <div className="flex min-w-0 flex-1 items-center gap-2 sm:flex-none">
            <span className="hidden text-xs text-slate-500 sm:inline">Working on</span>
            <div className="w-full min-w-0 sm:w-60"><Select aria-label="Client" value={clientId} onChange={(e) => { const v = e.target.value; setParams(v ? { client: v } : {}); }}
              options={[{ value: "", label: "Choose a client…" }, ...(clients.data ?? []).map((c: any) => ({ value: c.id, label: c.name }))]} /></div>
          </div>
          <div className="xl:hidden"><Button size="sm" variant="ghost" onClick={newConversation}>New</Button></div>
          <div className="ml-auto lg:hidden"><Button size="sm" onClick={() => setPanel(!panel)} >{panel ? "Hide context" : "Context"}</Button></div>
        </header>
        {(open.error || thread.error || send.error || resume.error || confirm.error) ? <div className="mx-auto w-full max-w-[760px] px-4 pt-3 sm:px-6"><ErrorBox error={open.error || thread.error || send.error || resume.error || confirm.error} /></div> : null}
        {panel && <div className="border-b border-slate-200 bg-slate-50/60 lg:hidden"><ContextPanel clientId={clientId} /></div>}
        <div className="pane-scroll flex-1 overflow-y-auto" role="log" aria-live="polite">
          <div className="mx-auto w-full max-w-[760px] space-y-7 px-4 py-8 sm:px-6">
            {!t ? <Spinner /> : t.messages.map((m: any) => {
              const run = m.run_id ? t.runs[m.run_id] : null;
              if (m.role === "user") return <div key={m.id} className="flex justify-end"><div className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-md bg-slate-100 px-4 py-2.5 text-sm leading-6 text-slate-900">{m.content}</div></div>;
              return (
                <article key={m.id} className="flex animate-fade-in gap-3.5 text-sm leading-6 text-slate-800">
                  <div aria-hidden className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-brand-600 text-white"><Icon name="agent" className="h-3.5 w-3.5" /></div>
                  <div className="min-w-0 flex-1">
                  {m.kind === "plan" && <div className="space-y-3"><p className="text-slate-700">{m.content.split(" Plan: ")[0]}</p><Plan plan={run?.plan ?? m.data.plan} />
                    {run && run.status === "FAILED" && <Badge tone="red">Failed</Badge>}</div>}
                  {(m.kind === "result" || m.kind === "confirmation") && m.data?.headline && <Result r={m.data} onSay={(x) => say(x)} canAct={!busy} />}
                  {m.kind === "result" && !m.data?.headline && <p>{m.content}</p>}
                  {m.kind === "text" && <div><p>{m.content}</p>{m.data?.suggestions && <div className="mt-3 grid gap-2 sm:grid-cols-2">{m.data.suggestions.map((s: string) => <button key={s} type="button" className="rounded-lg border border-slate-200 bg-white px-3.5 py-2.5 text-left text-[13px] leading-5 text-slate-700 transition-colors hover:border-brand-300 hover:bg-brand-50/40 hover:text-slate-900 disabled:cursor-not-allowed disabled:opacity-50" onClick={() => say(s)} disabled={busy || !clientId}>{s}</button>)}</div>}</div>}
                  {m.kind === "clarification" && <div className="space-y-2.5"><p className="font-medium text-slate-900">{m.content}</p><div className="flex flex-wrap gap-1.5">{m.data.options.map((o: any) => <Button key={o.value} size="sm" disabled={busy || t.pending?.kind !== "clarify"} onClick={() => say("", { value: o.value, label: o.label })}>{o.label}</Button>)}</div></div>}
                  {m.kind === "error" && <div className="space-y-2"><Notice tone="red">{m.content}</Notice>{run?.resumable && <Button size="sm" variant="primary" disabled={busy} onClick={() => resume.mutate(run.id, { onSuccess: refresh })}>Resume from where it stopped</Button>}</div>}
                  {m.kind === "confirmation" && t.pending?.kind === "confirm" && t.pending.run_id === m.run_id && (
                    <div className="mt-4 overflow-hidden rounded-lg border border-amber-300 bg-amber-50/60"><div className="px-4 py-3"><p className="font-medium text-slate-900">Confirm before I do anything</p><p className="mt-0.5 text-[13px] text-slate-700">{m.data.confirmation.describes}</p></div>
                      <div className="flex flex-wrap gap-2 border-t border-amber-200 bg-white/60 px-4 py-2.5"><Button variant="primary" disabled={!canTask || confirm.isPending} onClick={() => confirm.mutate({ id: m.run_id, approve: true }, { onSuccess: refresh })}>Confirm and create the draft task</Button>
                        <Button disabled={confirm.isPending} onClick={() => confirm.mutate({ id: m.run_id, approve: false }, { onSuccess: refresh })}>Cancel</Button></div>
                      {!canTask && <p className="px-4 pb-3 text-xs text-amber-800">Your role cannot create tasks.</p>}</div>)}
                  <p className="mt-2 text-2xs text-slate-400" title={dt(m.created_at)}>{dt(m.created_at)}</p>
                  </div>
                </article>);
            })}
            {interrupted.length > 0 && <Notice tone="amber">A request was interrupted. {interrupted.map((r: any) => <Button key={r.id} size="sm" onClick={() => resume.mutate(r.id, { onSuccess: refresh })}>Resume “{r.intent_label}”</Button>)}</Notice>}
            {busy && <div className="flex gap-3.5"><div aria-hidden className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-brand-50 text-brand-600"><Icon name="agent" className="h-3.5 w-3.5" /></div><Spinner label="Working… stages are saved as they finish, so nothing is lost if this is interrupted." /></div>}
            <div ref={bottom} />
          </div>
        </div>
        <form className="bg-white px-4 pb-4 pt-2 sm:px-6" onSubmit={(e) => { e.preventDefault(); submit(); }}>
          <div className="mx-auto w-full max-w-[760px] rounded-xl bg-white shadow-[0_1px_2px_rgb(14_23_32/0.06)] ring-1 ring-slate-300 transition-shadow focus-within:ring-2 focus-within:ring-brand-500">
            <textarea aria-label="Ask the agent" rows={1}
              className="block max-h-48 min-h-[52px] w-full resize-none border-0 bg-transparent px-4 pb-1 pt-3.5 text-sm leading-6 placeholder:text-slate-400 focus:outline-none focus:ring-0 focus-visible:ring-0 focus-visible:ring-offset-0 disabled:text-slate-400"
              placeholder={clientId ? "Ask about this client, or tell the agent what to do" : "Choose a client above, or name one in your request"} value={text} maxLength={2000}
              onChange={(e) => { setText(e.target.value); e.currentTarget.style.height = "auto"; e.currentTarget.style.height = `${Math.min(e.currentTarget.scrollHeight, 192)}px`; }}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); if (!busy && text.trim()) submit(); } }} disabled={busy} />
            <div className="flex items-center justify-between gap-3 px-3 pb-2.5 pt-1">
              <span className="truncate pl-1 text-2xs text-slate-400">{text.length > 1600 ? `${text.length} / 2000` : "Enter to send · Shift + Enter for a new line"}</span>
              <Button variant="primary" type="submit" size="sm" disabled={busy || !text.trim()}><Icon name="send" className="h-3.5 w-3.5" />Send</Button>
            </div>
          </div>
          <p className="mx-auto mt-2 w-full max-w-[760px] text-center text-2xs text-slate-400">The agent reads and researches on its own. Anything that creates a task or an approval asks for your confirmation first.</p>
        </form>
      </section>
      {/* context */}
      <aside className="pane-scroll hidden w-[300px] shrink-0 overflow-y-auto border-l border-slate-200 bg-slate-50/60 lg:block" aria-label="Client context"><ContextPanel clientId={clientId} /></aside>
    </div>
  );
}
