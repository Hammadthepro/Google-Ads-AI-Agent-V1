import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import { useAuth } from "../lib/auth";
import { clientOptions, useAction, useClients } from "../lib/hooks";
import { Badge, Button, Card, DataSourceBadge, dt, ErrorBox, Field, FieldGroup, Input, Notice, PageHeader, Select, Spinner, StatusBadge, Table, Textarea } from "../components/ui";

const TASKS: { value: string; label: string; needs: "business" | "campaign" | "ad_group" | "ad" }[] = [
  { value: "BUILD_CAMPAIGN", label: "Build a new campaign", needs: "business" },
  { value: "ADD_AD_GROUP", label: "Add an ad group", needs: "campaign" },
  { value: "ADD_AD", label: "Add an ad", needs: "ad_group" },
  { value: "ADD_KEYWORDS", label: "Add keywords", needs: "ad_group" },
  { value: "EDIT_AD", label: "Edit an ad", needs: "ad" },
  { value: "PAUSE_AD", label: "Pause an ad", needs: "ad" },
  { value: "CHANGE_BUDGET", label: "Change budget", needs: "campaign" },
  { value: "CHANGE_TARGET", label: "Change weekly target", needs: "campaign" },
  { value: "CHANGE_LOCATIONS", label: "Change locations", needs: "campaign" },
  { value: "AUDIT_ONLY", label: "Audit only", needs: "business" },
];
const TERMINAL = ["AWAITING_APPROVAL", "AWAITING_BUDGET_DECISION", "COMPLETED", "FAILED", "BLOCKED_TRACKING", "VALIDATION_FAILED", "EXECUTED", "EXECUTED_PAUSED", "ENABLED", "EXECUTION_FAILED"];

export function IntakeReport({ report, clar, setClar }: { report: any; clar: Record<string, string>; setClar: (c: Record<string, string>) => void }) {
  const tone = report.status === "OK" ? "green" : report.status === "BLOCKED" ? "red" : "amber";
  const label = ({ OK: "Information complete", BLOCKED: "Blocked — fix before running", NEEDS_CLARIFICATION: "Clarification needed" } as Record<string, string>)[report.status];
  return (
    <div className="mt-4 space-y-2" aria-label="Intake check">
      <Notice tone={tone as any}><b>{label}</b> · {report.blockers} blocker(s), {report.questions} question(s), {report.warnings} warning(s)</Notice>
      {report.issues.map((i: any) => (
        <div key={i.code} className="rounded-lg border border-slate-200 px-3 py-2 text-sm">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={i.severity === "BLOCKER" ? "red" : i.severity === "QUESTION" ? "amber" : i.severity === "RESOLVED" ? "green" : "slate"}>{i.severity}</Badge>
            <span>{i.message}</span>
          </div>
          {i.severity === "QUESTION" && (
            <div className="mt-2"><Field label={i.question}>
              <Input value={clar[i.code] ?? ""} onChange={(e) => setClar({ ...clar, [i.code]: e.target.value })} placeholder="Your answer (saved with the task)" />
            </Field></div>)}
          {i.severity === "RESOLVED" && <div className="mt-1 text-xs text-slate-500">Answer: {i.answer}</div>}
        </div>
      ))}
    </div>
  );
}

function BudgetDecision({ task }: { task: any }) {
  const qc = useQueryClient();
  const feas = useQuery({ queryKey: ["feas-task", task.id], queryFn: () => api.get(`/feasibility?business_id=${task.business_id}`) });
  const f = (feas.data ?? []).find((x: any) => x.task_id === task.id);
  const [budget, setBudget] = useState("");
  const [target, setTarget] = useState("");
  const [note, setNote] = useState("");
  const decide = useAction((body) => api.post(`/tasks/${task.id}/budget-decision`, body), [["tasks"]]);
  const done = { onSuccess: () => { qc.invalidateQueries({ queryKey: ["task", task.id] }); } };
  if (!f) return <Spinner />;
  const s = f.summary;
  return (
    <Card title="Budget decision needed (estimate)">
      <p className="text-sm text-slate-600">With ${s.monthly_budget}/month the expected result is <b>{s.expected_weekly_conversions}</b> leads/week (range {s.realistic_weekly_range.join("–")}) against a target of <b>{s.weekly_target}</b>. This is <StatusBadge value={f.status} />. Estimates are not guarantees.</p>
      <div className="mt-4 grid gap-3 md:grid-cols-3">
        <div className="rounded-lg border border-slate-200 p-3 text-sm">
          <div className="font-semibold">Accept recommended budget</div>
          <div className="mt-1 text-2xl font-semibold">{s.recommended_monthly_budget ? `$${s.recommended_monthly_budget.toLocaleString()}` : "—"}<span className="text-sm font-normal text-slate-500"> /month</span></div>
          <div className="text-slate-600">Reaches {s.weekly_target} leads/week at the expected cost per lead.</div>
          <div className="mt-3"><Button variant="primary" disabled={!s.recommended_monthly_budget || decide.isPending} onClick={() => decide.mutate({ decision: "ACCEPT_RECOMMENDED", note }, done)}>Accept and continue</Button></div>
        </div>
        <div className="rounded-lg border border-slate-200 p-3 text-sm">
          <div className="font-semibold">Adjust budget and/or target</div>
          <div className="mt-2 grid gap-2">
            <Input type="number" aria-label="New monthly budget" placeholder={`Monthly budget (now ${s.monthly_budget})`} value={budget} onChange={(e) => setBudget(e.target.value)} />
            <Input type="number" aria-label="New weekly target" placeholder={`Weekly target (now ${s.weekly_target}; ~${s.achievable_weekly_target} achievable)`} value={target} onChange={(e) => setTarget(e.target.value)} />
          </div>
          <div className="mt-3"><Button disabled={(!budget && !target) || decide.isPending} onClick={() => decide.mutate({ decision: "ADJUST", monthly_budget: budget ? Number(budget) : undefined, weekly_target: target ? Number(target) : undefined, note }, done)}>Apply and continue</Button></div>
        </div>
        <div className="rounded-lg border border-slate-200 p-3 text-sm">
          <div className="font-semibold">Keep the current numbers</div>
          <div className="text-slate-600">Requires a written reason. The approval card will still show the risk.</div>
          <div className="mt-3"><Button variant="danger" disabled={note.trim().length < 10 || decide.isPending} onClick={() => decide.mutate({ decision: "KEEP_CURRENT", note }, done)}>Keep and continue</Button></div>
        </div>
      </div>
      <div className="mt-3"><Field label="Note / reason (saved with the decision)"><Input value={note} onChange={(e) => setNote(e.target.value)} /></Field></div>
      {(s.adjustment_options ?? []).length > 0 && <ul className="mt-3 list-disc pl-5 text-sm text-slate-600">{s.adjustment_options.map((o: any) => <li key={o.option}>{o.explanation}</li>)}</ul>}
      <div className="mt-2"><ErrorBox error={decide.error} /></div>
    </Card>
  );
}

export function TaskForm() {
  const [params] = useSearchParams();
  const nav = useNavigate();
  const clients = useClients();
  const [clientId, setClientId] = useState(params.get("client") ?? "");
  const [type, setType] = useState("BUILD_CAMPAIGN");
  const [businessId, setBusinessId] = useState("");
  const [campaignId, setCampaignId] = useState("");
  const [adGroupId, setAdGroupId] = useState("");
  const [adId, setAdId] = useState("");
  const [f, setF] = useState<any>({ monthly_budget: 1500, weekly_target: 5, radius_miles: 20 });
  const [humanPlan, setHumanPlan] = useState("");
  const [note, setNote] = useState("");
  const [showOpt, setShowOpt] = useState(false);
  const client = clients.data?.find((c: any) => c.id === clientId);
  const structure = useQuery({ queryKey: ["structure", clientId], enabled: !!clientId, queryFn: () => api.get(`/clients/${clientId}/structure`) });
  const spec = TASKS.find((t) => t.value === type)!;
  const business = client?.businesses.find((b: any) => b.id === businessId) ?? (client?.businesses.length === 1 ? client.businesses[0] : null);
  const campaigns = (structure.data?.campaigns ?? []).filter((c: any) => !businessId || !c.business_id || c.business_id === businessId);
  const campaign = campaigns.find((c: any) => c.id === campaignId);
  const adGroup = campaign?.ad_groups.find((g: any) => g.id === adGroupId);
  useEffect(() => { if (client && !client.businesses.find((b: any) => b.id === businessId)) setBusinessId(client.businesses[0]?.id ?? ""); }, [clientId, client]);
  useEffect(() => { setCampaignId(""); setAdGroupId(""); setAdId(""); }, [clientId, type]);
  useEffect(() => { if (business && type === "BUILD_CAMPAIGN") setF((x: any) => ({ ...x, services: business.services.filter((s: any) => !s.is_excluded).map((s: any) => s.name), radius_miles: business.default_radius_miles })); }, [business?.id, type]);
  const run = useAction((body) => api.post("/tasks", body), [["tasks"]]);
  const [clar, setClar] = useState<Record<string, string>>({});
  const intakeCheck = useAction((body) => api.post("/intake/validate", body));
  const set = (k: string, v: unknown) => setF({ ...f, [k]: v });

  const fields = useMemo(() => {
    const out: JSX.Element[] = [];
    const num = (k: string, label: string, hint?: string) => out.push(<Field key={k} label={label} hint={hint}><Input type="number" value={f[k] ?? ""} onChange={(e) => set(k, e.target.value)} /></Field>);
    if (type === "BUILD_CAMPAIGN") {
      out.push(<FieldGroup key="goal" label="Goal · Campaign type" hint="Fixed in V1"><div className="flex gap-2 py-2"><Badge tone="blue">Leads (conversions)</Badge><Badge tone="blue">Search</Badge></div></FieldGroup>);
      num("monthly_budget", "Monthly budget ($)", "Any amount — feasibility warns if too low");
      num("weekly_target", "Weekly conversion target");
      num("radius_miles", "Radius (miles)", "Prefilled from the business");
      out.push(<FieldGroup key="services" label="Services covered"><div className="flex flex-wrap gap-2 py-1">{(business?.services ?? []).filter((s: any) => !s.is_excluded).map((s: any) => (
        <label key={s.id} className="flex items-center gap-1 text-sm"><input type="checkbox" checked={(f.services ?? []).includes(s.name)}
          onChange={(e) => set("services", e.target.checked ? [...(f.services ?? []), s.name] : (f.services ?? []).filter((x: string) => x !== s.name))} />{s.name}</label>))}</div></FieldGroup>);
    }
    if (type === "ADD_AD_GROUP") {
      out.push(<Field key="service" label="Service"><Select value={f.service ?? ""} onChange={(e) => set("service", e.target.value)}
        options={[{ value: "", label: "Choose…" }, ...(business?.services ?? []).filter((s: any) => !s.is_excluded).map((s: any) => ({ value: s.name, label: s.name }))]} /></Field>);
      out.push(<Field key="lp" label="Landing page" hint="Prefilled from the profile / website audit"><Input value={f.landing_page ?? ""} placeholder="(auto)" onChange={(e) => set("landing_page", e.target.value)} /></Field>);
    }
    if (type === "ADD_AD") out.push(<Field key="lp" label="Landing page (optional)"><Input value={f.landing_page ?? ""} placeholder={adGroup?.landing_page_url || "(auto)"} onChange={(e) => set("landing_page", e.target.value)} /></Field>);
    if (type === "ADD_KEYWORDS") out.push(<Field key="sw" label="Starter words (optional, comma separated)"><Input value={f.starter_words ?? ""} onChange={(e) => set("starter_words", e.target.value)} /></Field>);
    if (type === "EDIT_AD") out.push(<Field key="cr" label="What to change (optional)"><Input value={f.change_request ?? ""} placeholder="e.g. lead with speed" onChange={(e) => set("change_request", e.target.value)} /></Field>);
    if (type === "PAUSE_AD") out.push(<Field key="r" label="Reason (optional)"><Input value={f.reason ?? ""} onChange={(e) => set("reason", e.target.value)} /></Field>);
    if (type === "CHANGE_BUDGET") num("monthly_budget", "New monthly budget ($)", campaign ? `Current: $${campaign.monthly_budget.toFixed(0)}/month` : undefined);
    if (type === "CHANGE_TARGET") num("weekly_target", "New weekly conversion target", campaign ? `Current: ${campaign.weekly_target}` : undefined);
    if (type === "CHANGE_LOCATIONS") {
      num("radius_miles", "Radius (miles)");
      out.push(<Field key="z" label="or ZIP codes (comma separated)"><Input value={f.zip_text ?? ""} onChange={(e) => set("zip_text", e.target.value)} /></Field>);
    }
    return out;
  }, [type, f, business, campaign, adGroup]);

  const buildBody = () => {
    const fields: any = { ...f };
    for (const k of ["monthly_budget", "weekly_target", "radius_miles"]) if (fields[k] !== undefined && fields[k] !== "") fields[k] = Number(fields[k]);
    if (type === "CHANGE_LOCATIONS" && f.zip_text) fields.zip_codes = f.zip_text.split(",").map((z: string) => z.trim()).filter(Boolean);
    const keep: Record<string, string[]> = { BUILD_CAMPAIGN: ["monthly_budget", "weekly_target", "radius_miles", "services"], ADD_AD_GROUP: ["service", "landing_page"],
      ADD_AD: ["landing_page"], ADD_KEYWORDS: ["starter_words"], EDIT_AD: ["change_request"], PAUSE_AD: ["reason"], CHANGE_BUDGET: ["monthly_budget"],
      CHANGE_TARGET: ["weekly_target"], CHANGE_LOCATIONS: ["radius_miles", "zip_codes"], AUDIT_ONLY: [] };
    const clean = Object.fromEntries(Object.entries(fields).filter(([k, v]) => keep[type].includes(k) && v !== "" && v !== undefined));
    if (Object.keys(clar).length) clean.clarifications = clar;
    return { task_type: type, client_id: clientId, business_id: business?.id, campaign_id: campaignId || undefined, ad_group_id: adGroupId || undefined,
      ad_id: adId || undefined, fields: clean, human_plan: humanPlan, note };
  };
  const submit = () => run.mutate(buildBody(), { onSuccess: (t: any) => nav(`/tasks/${t.id}`) });
  const check = () => intakeCheck.mutate(buildBody());
  const intake = (run.error as any)?.details?.intake ?? intakeCheck.data;
  const ready = clientId && (spec.needs === "business" ? !!business : spec.needs === "campaign" ? !!campaignId : spec.needs === "ad_group" ? !!adGroupId : !!adId);

  return (
    <Card title="Run a task" actions={<span className="text-xs text-slate-500">Runs every step automatically up to the approval stage. Nothing changes in Google Ads.</span>}>
      <div className="grid gap-4 md:grid-cols-3">
        <Field label="Client"><Select value={clientId} onChange={(e) => setClientId(e.target.value)} options={[{ value: "", label: "Choose a client…" }, ...clientOptions(clients.data)]} /></Field>
        {client && client.businesses.length > 1 && <Field label="Business"><Select value={businessId} onChange={(e) => setBusinessId(e.target.value)} options={client.businesses.map((b: any) => ({ value: b.id, label: b.name }))} /></Field>}
        <Field label="Task"><Select value={type} onChange={(e) => setType(e.target.value)} options={TASKS} /></Field>
        {clientId && ["campaign", "ad_group", "ad"].includes(spec.needs) && (
          <Field label="Campaign" hint="Read from your connected Google Ads account">
            <Select value={campaignId} onChange={(e) => { setCampaignId(e.target.value); setAdGroupId(""); setAdId(""); }}
              options={[{ value: "", label: structure.isLoading ? "Loading…" : "Choose…" }, ...campaigns.map((c: any) => ({ value: c.id, label: `${c.name} · ${c.status}${c.read_only ? " · read-only" : ""}` }))]} />
          </Field>)}
        {campaign?.read_only && <Notice tone="amber">{campaign.campaign_type} campaigns are read-only in V1.</Notice>}
        {campaign && ["ad_group", "ad"].includes(spec.needs) && (
          <Field label="Ad group"><Select value={adGroupId} onChange={(e) => { setAdGroupId(e.target.value); setAdId(""); }}
            options={[{ value: "", label: "Choose…" }, ...campaign.ad_groups.map((g: any) => ({ value: g.id, label: `${g.name} · ${g.status}` }))]} /></Field>)}
        {adGroup && spec.needs === "ad" && (
          <Field label="Ad"><Select value={adId} onChange={(e) => setAdId(e.target.value)}
            options={[{ value: "", label: "Choose…" }, ...adGroup.ads.map((a: any) => ({ value: a.id, label: `${a.headlines[0]} · ${a.status}` }))]} /></Field>)}
      </div>
      {clientId && <div className="mt-4 grid gap-4 md:grid-cols-3">{fields}</div>}
      <div className="mt-4">
        <button className="text-sm text-brand-700" onClick={() => setShowOpt(!showOpt)}>{showOpt ? "▾" : "▸"} Optional: human plan, note</button>
        {showOpt && (
          <div className="mt-2 grid gap-4 md:grid-cols-2">
            <Field label="Human plan" hint="One item per line, e.g. Bidding: Maximize Conversions · Keywords: a, b · Negatives: c · Budget: $2000 · Ad groups: X, Y">
              <Textarea rows={5} value={humanPlan} onChange={(e) => setHumanPlan(e.target.value)} /></Field>
            <Field label="Note"><Textarea rows={5} value={note} onChange={(e) => setNote(e.target.value)} /></Field>
          </div>)}
      </div>
      {type === "BUILD_CAMPAIGN" && ready && (
        <div className="mt-4"><Button onClick={check} disabled={intakeCheck.isPending}>{intakeCheck.isPending ? "Checking…" : "Check client information"}</Button></div>)}
      {intake && <IntakeReport report={intake} clar={clar} setClar={setClar} />}
      <div className="mt-5 flex items-center gap-3">
        <Button variant="primary" disabled={!ready || run.isPending} onClick={submit}>{run.isPending ? "Starting…" : "Run"}</Button>
        {!(run.error as any)?.details?.intake && <ErrorBox error={run.error} />}
        <ErrorBox error={intakeCheck.error} />
      </div>
    </Card>
  );
}

export function Tasks() {
  const { can } = useAuth();
  const nav = useNavigate();
  const tasks = useQuery({ queryKey: ["tasks"], queryFn: () => api.get("/tasks"), refetchInterval: 5000 });
  return (
    <div className="space-y-6">
      <PageHeader title="Tasks" subtitle="One form, one Run button. Results and proposals appear on Approvals, Monitor and the Weekly Review." />
      {can("tasks.run") || can("audits.run") ? <TaskForm /> : <Notice>Your role is read-only.</Notice>}
      <Card title="Recent tasks">
        <Table rows={tasks.data ?? []} onRowClick={(t) => nav(`/tasks/${t.id}`)} columns={[
          { key: "task_type", label: "Task", render: (t) => <span className="font-medium">{t.task_type.replace(/_/g, " ")}</span> },
          { key: "status", label: "Status", render: (t) => <StatusBadge value={t.status} /> },
          { key: "current_step", label: "Step" },
          { key: "steps", label: "Progress", render: (t) => `${t.steps.filter((s: any) => s.status === "DONE").length}/${t.steps.length}` },
          { key: "created_at", label: "Created", render: (t) => dt(t.created_at) },
          { key: "source_type", label: "Data", render: (t) => <DataSourceBadge source={t.source_type} /> },
        ]} />
      </Card>
    </div>
  );
}

export function TaskDetail() {
  const { id } = useParams();
  const q = useQuery({ queryKey: ["task", id], queryFn: () => api.get(`/tasks/${id}`),
    refetchInterval: (query) => (TERMINAL.includes((query.state.data as any)?.status) ? false : 1500) });
  if (q.isLoading || !q.data) return <Spinner />;
  const t = q.data;
  return (
    <div className="space-y-6">
      <PageHeader title={t.task_type.replace(/_/g, " ")} subtitle={<>Task {t.id.slice(0, 8)} · {dt(t.created_at)}</>}
        actions={<><StatusBadge value={t.status} /><DataSourceBadge source={t.source_type} />
          {t.approval_id && <Link to={`/approvals/${t.approval_id}`}><Button variant="primary">Open approval →</Button></Link>}
          {t.plan_id && <Link to={`/planner/${t.plan_id}`}><Button>View plan</Button></Link>}</>} />
      {t.status === "BLOCKED_TRACKING" && <Notice tone="red"><b>Blocked by the conversion tracking gate.</b> {t.error} — fix tracking or ask an admin for an override on the business page.</Notice>}
      {t.status === "VALIDATION_FAILED" && <Notice tone="red">{t.error}</Notice>}
      {t.status === "FAILED" && <Notice tone="red">{t.error}</Notice>}
      {t.status === "AWAITING_BUDGET_DECISION" && <BudgetDecision task={t} />}
      {t.status === "AWAITING_APPROVAL" && <Notice tone="amber">The workflow stopped at the approval stage. Nothing has been changed in Google Ads.</Notice>}
      <Card title="Workflow steps">
        <ol className="space-y-3">
          {t.steps.map((s: any, i: number) => (
            <li key={i} className="flex gap-3">
              <span className={`mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-semibold ${s.status === "DONE" ? "bg-emerald-100 text-emerald-700" : s.status === "RUNNING" ? "bg-brand-100 text-brand-700" : "bg-rose-100 text-rose-700"}`}>
                {s.status === "DONE" ? "✓" : s.status === "RUNNING" ? "…" : "!"}</span>
              <div className="min-w-0">
                <div className="text-sm font-medium text-slate-800">{s.name} <span className="ml-1 text-xs font-normal text-slate-400">{s.status}</span></div>
                {s.summary && <div className="whitespace-pre-wrap break-words text-sm text-slate-600">{s.summary}</div>}
              </div>
            </li>
          ))}
          {!TERMINAL.includes(t.status) && <Spinner label="Running…" />}
        </ol>
      </Card>
      <Card title="Task input"><pre className="whitespace-pre-wrap text-xs text-slate-600">{JSON.stringify({ fields: t.fields, human_plan: t.human_plan, note: t.note }, null, 2)}</pre></Card>
    </div>
  );
}
