import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { useAction } from "../lib/hooks";
import { Badge, Button, Card, dt, EmptyState, ErrorBox, Field, Input, Notice, Select, Spinner, StatusBadge, Textarea } from "./ui";

const SIDE_LABEL: Record<string, string> = { internal: "Internal AI", claude: "Claude" };

export function CompetitionPanel({ comp }: { comp: any }) {
  if (!comp || !comp.providers) return <EmptyState message="This plan was not produced by the AI competition." />;
  const cmp = comp.comparison ?? {};
  return (
    <div className="space-y-4">
      <div className="grid gap-3 md:grid-cols-2">
        {Object.entries(comp.providers).map(([side, p]: any) => (
          <div key={side} className="rounded-lg border border-slate-200 p-3 text-sm">
            <div className="flex items-center justify-between"><b>{SIDE_LABEL[side]}</b><Badge tone={p.source === "LIVE" ? "green" : "violet"}>{p.source === "LIVE" ? "LIVE AI" : "TEST FIXTURE"}</Badge></div>
            <div className="mt-1 text-slate-600">{p.provider} · {p.model ?? "no draft"}</div>
            <div className="text-xs text-slate-500">prompt {p.prompt ?? "—"}</div>
            {comp.errors?.[side] && <div className="mt-1 text-xs text-rose-700">Failed: {comp.errors[side]}</div>}
          </div>
        ))}
      </div>
      <Notice tone="blue">Both sides received <b>identical input</b> (hash <code>{String(comp.input_hash).slice(0, 12)}…</code>), drafted independently, then challenged the other side’s draft. The final plan is built by <b>rules</b>, not by either AI. Agreement between the two is a signal, not proof.</Notice>
      {comp.mode === "single" && <Notice tone="amber"><b>Only one AI produced a draft.</b> {comp.degraded_reason}</Notice>}
      {cmp.comparable && (
        <dl className="grid gap-2 text-sm sm:grid-cols-4">
          <div><dt className="text-slate-500">Ad groups in common</dt><dd className="font-medium">{cmp.ad_groups.both} of {cmp.ad_groups.both + cmp.ad_groups.internal_only + cmp.ad_groups.claude_only}</dd></div>
          <div><dt className="text-slate-500">Keywords in common</dt><dd className="font-medium">{cmp.keywords.both} · overlap {Math.round(cmp.keywords.jaccard * 100)}%</dd></div>
          <div><dt className="text-slate-500">Only internal / only Claude</dt><dd className="font-medium">{cmp.keywords.internal_only} / {cmp.keywords.claude_only}</dd></div>
          <div><dt className="text-slate-500">Bidding</dt><dd className="font-medium">{cmp.bidding.same ? "same" : `${cmp.bidding.internal} vs ${cmp.bidding.claude}`}</dd></div>
        </dl>
      )}
      <div>
        <h4 className="mb-1 text-sm font-semibold">Cross-challenges</h4>
        {Object.entries(comp.challenges ?? {}).map(([k, c]: any) => (
          <details key={k} className="mb-1 rounded border border-slate-200 px-3 py-2 text-sm">
            <summary className="cursor-pointer">{k.replace(/_/g, " ")} · <StatusBadge value={c.report.verdict} /> · {c.report.objections.length} objection(s)</summary>
            <ul className="mt-2 list-disc space-y-1 pl-5">
              {c.report.objections.map((o: any, i: number) => <li key={i}><Badge tone={o.severity === "HIGH" ? "red" : o.severity === "MEDIUM" ? "amber" : "slate"}>{o.category}</Badge> {o.message}</li>)}
              {c.report.agreements.map((a: string, i: number) => <li key={`a${i}`} className="text-emerald-700">{a}</li>)}
            </ul>
          </details>
        ))}
      </div>
      <details className="rounded border border-slate-200 px-3 py-2 text-sm">
        <summary className="cursor-pointer font-medium">How the final plan was consolidated</summary>
        <ul className="mt-2 list-disc pl-5 text-slate-600">{comp.consolidation.rules.map((r: string) => <li key={r}>{r}</li>)}</ul>
        {comp.consolidation.notes?.map((n: string) => <p key={n} className="mt-1 text-amber-800">{n}</p>)}
        {comp.consolidation.groups.map((g: any) => (
          <div key={g.service} className="mt-3">
            <div className="font-medium">{g.service}</div>
            <div className="text-xs text-slate-500">Ad: {g.ad?.winner ? `${SIDE_LABEL[g.ad.winner]}'s ad kept` : "n/a"}{g.ad?.added_from_other_side?.length ? ` · +${g.ad.added_from_other_side.length} headline(s) from the other side` : ""}{g.ad?.dropped_headlines?.length ? ` · dropped: ${g.ad.dropped_headlines.join(", ")}` : ""}</div>
            {g.dropped.length > 0 && <ul className="mt-1 list-disc pl-5 text-xs text-slate-600">{g.dropped.map((d: any, i: number) => <li key={i}>dropped “{d.keyword}” [{d.match_type}] — {d.reason}</li>)}</ul>}
          </div>
        ))}
      </details>
    </div>
  );
}

export function DiffView({ diff }: { diff: any }) {
  if (!diff) return null;
  if (!diff.changed) return <EmptyState message="No difference from the AI-consolidated original." />;
  return (
    <div className="space-y-3 text-sm">
      {diff.campaign.map((c: any) => <div key={c.field}><b>{c.field.replace(/_/g, " ")}</b>: <del className="text-rose-700">{String(c.from)}</del> → <ins className="text-emerald-700 no-underline">{String(c.to)}</ins></div>)}
      {diff.ad_groups.map((g: any) => (
        <div key={g.name} className="rounded border border-slate-200 p-3">
          <div className="font-medium">{g.name} {g.status !== "CHANGED" && <Badge tone={g.status === "ADDED" ? "green" : "red"}>{g.status}</Badge>}</div>
          {g.keywords_added?.map((k: string) => <div key={k} className="text-emerald-700">+ keyword {k}</div>)}
          {g.keywords_removed?.map((k: string) => <div key={k} className="text-rose-700">− keyword {k}</div>)}
          {g.headlines_added?.map((k: string) => <div key={k} className="text-emerald-700">+ headline “{k}”</div>)}
          {g.headlines_removed?.map((k: string) => <div key={k} className="text-rose-700">− headline “{k}”</div>)}
          {g.descriptions_changed && <div className="text-amber-800">~ descriptions changed</div>}
          {g.landing_page && <div className="text-amber-800">~ landing page {g.landing_page.from} → {g.landing_page.to}</div>}
        </div>
      ))}
      {diff.negatives.added.map((n: string) => <div key={n} className="text-emerald-700">+ negative “{n}”</div>)}
      {diff.negatives.removed.map((n: string) => <div key={n} className="text-rose-700">− negative “{n}”</div>)}
    </div>
  );
}

type Edit = Record<string, any>;
const describe = (e: Edit) => ({
  rename_campaign: `Rename campaign to “${e.value}”`, set_bidding: `Bidding → ${e.value}`, set_budget: `Monthly budget → $${e.monthly_budget}`,
  add_keyword: `Add [${e.match_type}] “${e.text}” to ${e.ad_group}`, remove_keyword: `Remove [${e.match_type}] “${e.text}” from ${e.ad_group}`,
  add_negative: `Add negative “${e.text}”`, remove_negative: `Remove negative “${e.text}”`, set_landing_page: `Landing page of ${e.ad_group} → ${e.url}`,
  remove_ad_group: `Remove ad group ${e.ad_group}`, edit_ad: `Rewrite ad in ${e.ad_group}`,
} as Record<string, string>)[e.op] ?? e.op;

export function PlanEditor({ approvalId, draft, scope }: { approvalId: string; draft: any; scope: string }) {
  const nav = useNavigate();
  const groups: any[] = scope === "CAMPAIGN" ? draft.ad_groups : [draft];
  const [queue, setQueue] = useState<Edit[]>([]);
  const [note, setNote] = useState("");
  const [kind, setKind] = useState("add_keyword");
  const [f, setF] = useState<Edit>({ ad_group: groups[0]?.name, match_type: "PHRASE" });
  const submit = useAction(() => api.post(`/approvals/${approvalId}/plan-edit`, { edits: queue, note }), [["approvals"]]);
  const set = (k: string, v: any) => setF({ ...f, [k]: v });
  const group = groups.find((g) => g.name === f.ad_group) ?? groups[0];
  const ad = group?.ads?.[0];
  const kinds = [
    { value: "add_keyword", label: "Add keyword" }, { value: "remove_keyword", label: "Remove keyword" },
    { value: "add_negative", label: "Add negative keyword" }, { value: "remove_negative", label: "Remove negative keyword" },
    { value: "edit_ad", label: "Rewrite the ad" }, { value: "set_landing_page", label: "Change landing page" },
    ...(scope === "CAMPAIGN" ? [{ value: "rename_campaign", label: "Rename campaign" }, { value: "set_bidding", label: "Change bidding" }, { value: "remove_ad_group", label: "Remove ad group" }] : []),
    { value: "set_budget", label: "Change monthly budget" },
  ];
  const add = () => {
    const e: Edit = { op: kind };
    if (["add_keyword", "remove_keyword"].includes(kind)) Object.assign(e, { ad_group: f.ad_group, text: f.text, match_type: f.match_type });
    if (["add_negative", "remove_negative"].includes(kind)) e.text = f.text;
    if (kind === "rename_campaign" || kind === "set_bidding") e.value = f.value;
    if (kind === "set_budget") e.monthly_budget = Number(f.monthly_budget);
    if (kind === "set_landing_page") Object.assign(e, { ad_group: f.ad_group, url: f.url });
    if (kind === "remove_ad_group") e.ad_group = f.ad_group;
    if (kind === "edit_ad") Object.assign(e, { ad_group: f.ad_group, index: 0, headlines: (f.headlines ?? ad?.headlines.join("\n") ?? "").split("\n"), descriptions: (f.descriptions ?? ad?.descriptions.join("\n") ?? "").split("\n") });
    setQueue([...queue, e]);
    setF({ ...f, text: "" });
  };
  const needsGroup = ["add_keyword", "remove_keyword", "set_landing_page", "edit_ad", "remove_ad_group"].includes(kind);
  return (
    <div className="space-y-3">
      <div className="grid gap-3 md:grid-cols-4">
        <Field label="Edit"><Select value={kind} onChange={(e) => setKind(e.target.value)} options={kinds} /></Field>
        {needsGroup && scope === "CAMPAIGN" && <Field label="Ad group"><Select value={f.ad_group} onChange={(e) => set("ad_group", e.target.value)} options={groups.map((g) => ({ value: g.name, label: g.name }))} /></Field>}
        {["add_keyword", "remove_keyword", "add_negative", "remove_negative"].includes(kind) && <Field label="Text"><Input value={f.text ?? ""} onChange={(e) => set("text", e.target.value)} /></Field>}
        {["add_keyword", "remove_keyword"].includes(kind) && <Field label="Match type" hint="Broad match is not allowed in main ad groups"><Select value={f.match_type} onChange={(e) => set("match_type", e.target.value)} options={[{ value: "PHRASE", label: "Phrase" }, { value: "EXACT", label: "Exact" }]} /></Field>}
        {kind === "rename_campaign" && <Field label="New name"><Input value={f.value ?? ""} onChange={(e) => set("value", e.target.value)} /></Field>}
        {kind === "set_bidding" && <Field label="Bidding"><Select value={f.value ?? "MAXIMIZE_CONVERSIONS"} onChange={(e) => set("value", e.target.value)} options={[{ value: "MAXIMIZE_CONVERSIONS", label: "Maximize conversions" }, { value: "MAXIMIZE_CLICKS", label: "Maximize clicks (RED override only)" }]} /></Field>}
        {kind === "set_budget" && <Field label="Monthly budget ($)"><Input type="number" value={f.monthly_budget ?? ""} onChange={(e) => set("monthly_budget", e.target.value)} /></Field>}
        {kind === "set_landing_page" && <Field label="URL"><Input value={f.url ?? group?.landing_page ?? ""} onChange={(e) => set("url", e.target.value)} /></Field>}
      </div>
      {kind === "edit_ad" && (
        <div className="grid gap-3 md:grid-cols-2">
          <Field label="Headlines (one per line, max 30 characters each)"><Textarea rows={7} value={f.headlines ?? ad?.headlines.join("\n") ?? ""} onChange={(e) => set("headlines", e.target.value)} /></Field>
          <Field label="Descriptions (one per line, max 90 characters each)"><Textarea rows={7} value={f.descriptions ?? ad?.descriptions.join("\n") ?? ""} onChange={(e) => set("descriptions", e.target.value)} /></Field>
        </div>
      )}
      <Button onClick={add}>Add to pending edits</Button>
      {queue.length > 0 && (
        <div className="rounded-lg border border-slate-200 p-3">
          <div className="mb-1 text-sm font-semibold">Pending edits ({queue.length})</div>
          <ul className="space-y-1 text-sm">{queue.map((e, i) => <li key={i} className="flex justify-between gap-3"><span>{describe(e)}</span><button className="text-xs text-rose-700" onClick={() => setQueue(queue.filter((_, j) => j !== i))}>remove</button></li>)}</ul>
          <div className="mt-3"><Field label="Why (saved with the version)"><Input value={note} onChange={(e) => setNote(e.target.value)} /></Field></div>
          <div className="mt-3 flex items-center gap-3">
            <Button variant="primary" disabled={submit.isPending} onClick={() => submit.mutate({}, { onSuccess: (r: any) => nav(`/approvals/${r.approval_id}`) })}>{submit.isPending ? "Saving…" : "Save as new version"}</Button>
            <span className="text-xs text-slate-500">Creates a new plan version, rebuilds and re-validates the exact payload, and requires a fresh approval. Nothing is sent to Google Ads.</span>
          </div>
        </div>
      )}
      <ErrorBox error={submit.error} />
    </div>
  );
}

export function PlanReview({ planId, approvalId, canEdit, editable }: { planId: string; approvalId: string; canEdit: boolean; editable: boolean }) {
  const plan = useQuery({ queryKey: ["plan", planId], queryFn: () => api.get(`/plans/${planId}`) });
  const diff = useQuery({ queryKey: ["plan-diff", planId], queryFn: () => api.get(`/plans/${planId}/diff`) });
  const versions = useQuery({ queryKey: ["plan-versions", planId], queryFn: () => api.get(`/plans/${planId}/versions`) });
  if (plan.isLoading || !plan.data) return <Spinner />;
  const p = plan.data;
  return (
    <div className="space-y-5">
      <Card title="AI competition"><CompetitionPanel comp={p.competition} /></Card>
      <Card title={<span>Original AI plan → current version {p.version}</span>}><DiffView diff={diff.data} /></Card>
      {(versions.data?.length ?? 0) > 1 && (
        <Card title="Versions">
          <ul className="space-y-1 text-sm">{versions.data.map((v: any) => <li key={v.id} className="flex flex-wrap gap-2"><Badge tone={v.is_current ? "blue" : "slate"}>v{v.version}</Badge><StatusBadge value={v.status} /><span>{v.change_summary || "AI-consolidated original"}</span><span className="text-slate-400">{dt(v.created_at)}</span></li>)}</ul>
        </Card>
      )}
      {canEdit && editable ? <Card title="Edit this plan"><PlanEditor approvalId={approvalId} draft={p.draft} scope={p.scope} /></Card>
        : <p className="text-xs text-slate-500">Only open approvals can be edited. <Link className="text-brand-700" to="/approvals">Back to approvals</Link></p>}
    </div>
  );
}
