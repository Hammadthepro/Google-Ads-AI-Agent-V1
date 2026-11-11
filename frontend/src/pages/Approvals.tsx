import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api/client";
import { useAuth } from "../lib/auth";
import { useAction } from "../lib/hooks";
import { Badge, Button, Card, ConfirmDialog, DataSourceBadge, dt, ErrorBox, JsonBlock, KV, Notice, PageHeader, Select, Spinner, StatusBadge, Table, Textarea } from "../components/ui";
import { ChallengerView, DraftView, HumanComparison } from "./Planner";
import { PlanReview } from "../components/PlanReview";

const STATUSES = ["", "PENDING_APPROVAL", "APPROVED", "EXECUTED", "REJECTED", "CHANGES_REQUESTED", "VALIDATION_FAILED", "INVALIDATED", "EXPIRED", "EXECUTION_FAILED", "EXECUTION_BLOCKED"];

export function Approvals() {
  const nav = useNavigate();
  const [status, setStatus] = useState("PENDING_APPROVAL");
  const q = useQuery({ queryKey: ["approvals", status], queryFn: () => api.get(`/approvals${status ? `?status=${status}` : ""}`) });
  return (
    <div className="space-y-6">
      <PageHeader title="Approvals" subtitle="Every proposed change. An approval covers only the exact version reviewed; editing creates a new version."
        actions={<div className="w-56"><Select value={status} onChange={(e) => setStatus(e.target.value)} options={STATUSES.map((s) => ({ value: s, label: s ? s.replace(/_/g, " ") : "All statuses" }))} /></div>} />
      <Card>
        {q.isLoading ? <Spinner /> : (
          <Table rows={q.data ?? []} onRowClick={(a) => nav(`/approvals/${a.id}`)} empty="No approvals in this state." columns={[
            { key: "title", label: "Proposed change", render: (a) => <span className="font-medium text-slate-900">{a.title}</span> },
            { key: "approval_type", label: "Type", render: (a) => <Badge tone="blue">{a.approval_type}</Badge> },
            { key: "status", label: "Status", render: (a) => <StatusBadge value={a.status} /> },
            { key: "payload_version", label: "Ver." },
            { key: "requested_by_email", label: "Requested by" },
            { key: "created_at", label: "Created", render: (a) => dt(a.created_at) },
            { key: "integration_mode", label: "Mode", render: (a) => <Badge tone="violet">{a.integration_mode}</Badge> },
          ]} />
        )}
      </Card>
    </div>
  );
}

function Section({ n, title, children }: { n: number; title: string; children: React.ReactNode }) {
  return (
    <Card title={<span className="flex items-center gap-2"><span className="flex h-5 w-5 items-center justify-center rounded-full bg-slate-100 text-[11px] text-slate-600">{n}</span>{title}</span>}>
      {children}
    </Card>
  );
}

export function ApprovalDetail() {
  const { id } = useParams();
  const { can } = useAuth();
  const qc = useQueryClient();
  const nav = useNavigate();
  const q = useQuery({ queryKey: ["approval", id], queryFn: () => api.get(`/approvals/${id}`) });
  const inv = [["approval", id!], ["approvals"]];
  const approve = useAction((h) => api.post(`/approvals/${id}/approve`, { payload_hash: h }), inv);
  const reject = useAction((r) => api.post(`/approvals/${id}/reject`, { reason: r }), inv);
  const changes = useAction((r) => api.post(`/approvals/${id}/request-changes`, { reason: r }), inv);
  const execute = useAction(() => api.post(`/approvals/${id}/execute`), inv);
  const edit = useAction((p) => api.put(`/approvals/${id}/payload`, { payload: p }), inv);
  const [dialog, setDialog] = useState<"" | "approve" | "reject" | "changes" | "execute" | "edit">("");
  const [reason, setReason] = useState("");
  const [payloadText, setPayloadText] = useState("");
  if (q.isLoading || !q.data) return <Spinner />;
  const a = q.data;
  const ctx = a.context || {};
  const v = a.validation || {};
  const pending = a.status === "PENDING_APPROVAL";
  const err = approve.error || reject.error || changes.error || execute.error || edit.error;
  const close = () => { setDialog(""); setReason(""); qc.invalidateQueries({ queryKey: ["approval", id] }); };
  return (
    <div className="space-y-6">
      <PageHeader title={a.title}
        subtitle={<>Version {a.payload_version} · hash <code className="text-xs">{a.payload_hash.slice(0, 16)}…</code> · requested by {a.requested_by_email} · {dt(a.created_at)}
          {a.supersedes_id && <> · supersedes <Link className="text-brand-700" to={`/approvals/${a.supersedes_id}`}>previous version</Link></>}</>}
        actions={<><StatusBadge value={a.status} /><Badge tone="blue">{a.approval_type}</Badge><DataSourceBadge source={a.source_type} /></>} />
      {a.integration_mode === "MOCK" && <Notice tone="violet">Test fixture — executing this approval changes only a test double, never Google Ads.</Notice>}
      {a.superseded_by_id && <Notice tone="amber">This version was superseded. <Link className="font-medium underline" to={`/approvals/${a.superseded_by_id}`}>Open the current version →</Link></Notice>}
      {ctx.requires_super_admin && <Notice tone="amber">Budget increase above threshold — only a Super Admin can approve.</Notice>}
      <ErrorBox error={err} />

      <div className="flex flex-wrap gap-2">
        {pending && can("approvals.decide") && <Button variant="primary" onClick={() => setDialog("approve")}>Approve this version</Button>}
        {pending && can("approvals.decide") && <Button onClick={() => setDialog("changes")}>Request changes</Button>}
        {["PENDING_APPROVAL", "VALIDATION_FAILED"].includes(a.status) && can("approvals.decide") && <Button variant="danger" onClick={() => setDialog("reject")}>Reject</Button>}
        {["PENDING_APPROVAL", "VALIDATION_FAILED", "CHANGES_REQUESTED", "APPROVED"].includes(a.status) && can("approvals.submit") && a.payload?.operations?.length > 0 &&
          <Button onClick={() => { setPayloadText(JSON.stringify(a.payload, null, 2)); setDialog("edit"); }}>Edit payload (new version)</Button>}
        {a.status === "APPROVED" && can("execution.run") && <Button variant="primary" onClick={() => setDialog("execute")}>Execute approved change</Button>}
      </div>

      <Section n={1} title="Conversion measurement">
        {ctx.measurement?.evaluated_at ? <div className="space-y-1 text-sm">
          <p className="text-slate-700">{ctx.measurement.conversion_bidding?.reason}</p>
          {ctx.measurement.findings.filter((f: any) => f.result === "issue").map((f: any) => <p key={f.key} className="text-slate-600"><span className="font-medium text-slate-900">{f.inspected}:</span> {f.actual}. {f.impact}</p>)}
          {ctx.measurement.counts.issue === 0 && <p className="text-slate-500">No measurement problems found ({ctx.measurement.counts.not_assessable} check(s) could not be read).</p>}
        </div> : <span className="text-sm text-slate-500">Not audited for this approval.</span>}
      </Section>
      <Section n={2} title="Feasibility (estimate)">
        {ctx.feasibility?.summary ? <><KV items={[["Status", <StatusBadge value={ctx.feasibility.status} />], ["Expected leads/week", ctx.feasibility.summary.expected_weekly_conversions],
          ["Realistic range", ctx.feasibility.summary.realistic_weekly_range?.join(" – ")], ["Allowed cost per lead", `$${ctx.feasibility.summary.allowed_cost_per_lead}`],
          ["Daily budget", `$${ctx.feasibility.summary.daily_budget}`], ["Recommended monthly budget", ctx.feasibility.summary.recommended_monthly_budget ? `$${ctx.feasibility.summary.recommended_monthly_budget}` : "—"], ["Team decision", ctx.feasibility.decision || "—"]]} />
          {ctx.feasibility.budget_thin_warning && <div className="mt-2"><Notice tone="amber">{ctx.feasibility.budget_thin_warning}</Notice></div>}
          <p className="mt-2 text-xs text-slate-500">Estimates only, never guaranteed outcomes.</p></> : <span className="text-sm text-slate-500">n/a</span>}
      </Section>
      <Section n={3} title="Open client fixes">
        {ctx.open_client_fixes?.length ? <ul className="list-disc pl-5 text-sm">{ctx.open_client_fixes.map((f: string) => <li key={f}>{f}</li>)}</ul> : <span className="text-sm text-slate-500">None</span>}
      </Section>
      <Section n={4} title="Research summary">
        <KV items={[["Website audit", ctx.research_summary?.website ?? "—"], ["Business profile", ctx.research_summary?.gbp ?? "—"], ["Competitors", ctx.research_summary?.competitor ?? "—"],
          ["Keyword clusters", ctx.keyword_summary ? Object.entries(ctx.keyword_summary.clusters).map(([k, n]) => `${k} (${n})`).join(", ") : "—"]]} />
      </Section>
      <Section n={5} title="Proposed change">
        <div className="grid gap-4 md:grid-cols-2">
          <div><div className="mb-1 text-xs font-semibold uppercase text-slate-500">Current state</div><JsonBlock value={a.current_state} maxHeight={220} /></div>
          <div><div className="mb-1 text-xs font-semibold uppercase text-slate-500">New state</div><JsonBlock value={a.proposed_state} maxHeight={220} /></div>
        </div>
        <div className="mt-4"><KV items={[["Reason", a.reason || "—"], ["Expected effect", a.expected_effect || "—"]]} /></div>
        {a.risks?.length > 0 && <div className="mt-3"><Notice tone="amber"><b>Risks:</b> {a.risks.join(" · ")}</Notice></div>}
        {ctx.draft && <div className="mt-4"><DraftView draft={ctx.draft} /></div>}
        <details className="mt-4"><summary className="cursor-pointer text-sm text-slate-600">Exact payload ({a.payload?.operations?.length ?? 0} operations) — this is what an approval authorises</summary><JsonBlock value={a.payload} /></details>
      </Section>
      <Section n={6} title="Validation">
        <div className="mb-2 flex items-center gap-2 text-sm"><StatusBadge value={v.passed ? "PASS" : "FAIL"} /> {v.errors?.length ?? 0} errors · {v.warnings?.length ?? 0} warnings
          {a.validation_run?.ai_fix_attempts > 0 && <Badge tone="violet">{a.validation_run.ai_fix_attempts} AI fix attempt(s)</Badge>}</div>
        <Table rows={[...(v.errors ?? []), ...(v.warnings ?? [])]} empty="All checks passed." columns={[{ key: "severity", label: "", render: (e) => <StatusBadge value={e.severity === "warning" ? "WARN" : "FAIL"} /> },
          { key: "code", label: "Rule" }, { key: "message", label: "Message" }]} />
        <p className="mt-2 text-xs text-slate-500">Validation passing does not mean the change is approved or launched.</p>
      </Section>
      {ctx.plan_id && (
        <Section n={7} title={`AI competition, plan versions and editing (plan v${ctx.plan_version ?? 1})`}>
          <PlanReview planId={ctx.plan_id} approvalId={a.id} canEdit={can("approvals.submit")}
            editable={["PENDING_APPROVAL", "VALIDATION_FAILED", "CHANGES_REQUESTED"].includes(a.status)} />
        </Section>)}
      <Section n={8} title="Challenger disagreements"><ChallengerView ch={ctx.challenger} /></Section>
      <Section n={9} title="Human plan comparison"><HumanComparison hc={ctx.human_comparison} /></Section>
      <Section n={10} title="AI explanation"><p className="text-sm text-slate-700">{a.ai_explanation || "—"}</p><p className="mt-1 text-xs text-slate-500">AI explains; it never decides or executes.</p></Section>

      {a.operations?.length > 0 && (
        <Card title="Execution record">
          <Table rows={a.operations} columns={[{ key: "state", label: "State", render: (o) => <StatusBadge value={o.state} /> },
            { key: "adapter_name", label: "Adapter" }, { key: "integration_mode", label: "Mode", render: (o) => <Badge tone="violet">{o.integration_mode}</Badge> },
            { key: "verification_passed", label: "Read-back verified", render: (o) => o.verification_passed === null ? "—" : o.verification_passed ? <Badge tone="green">verified</Badge> : <Badge tone="red">mismatch</Badge> },
            { key: "attempts", label: "Attempts" }, { key: "reconciled_at", label: "Reconciled", render: (o) => dt(o.reconciled_at) },
            { key: "external_resource_names", label: "Resources", render: (o) => o.external_resource_names.length }, { key: "error", label: "Error", className: "max-w-xs text-xs" }]} />
        </Card>
      )}
      <Card title="Approval history">
        <Table rows={a.events ?? []} columns={[{ key: "event", label: "Event", render: (e) => <StatusBadge value={e.event} /> }, { key: "actor", label: "By" }, { key: "at", label: "When", render: (e) => dt(e.at) },
          { key: "version", label: "Ver." }, { key: "payload_hash", label: "Hash", render: (e) => <code className="text-xs">{e.payload_hash.slice(0, 12)}</code> }, { key: "reason", label: "Reason" }]} />
      </Card>

      <ConfirmDialog open={dialog === "approve"} title="Approve this exact version?" confirmLabel="Approve"
        body={<>You approve payload version {a.payload_version} (hash <code>{a.payload_hash.slice(0, 12)}…</code>) only. Any later change invalidates this approval. Approving does not execute it.</>}
        onCancel={close} onConfirm={() => approve.mutate(a.payload_hash, { onSettled: close })} />
      <ConfirmDialog open={dialog === "reject" || dialog === "changes"} title={dialog === "reject" ? "Reject this change" : "Request changes"} tone={dialog === "reject" ? "danger" : "primary"}
        confirmLabel={dialog === "reject" ? "Reject" : "Request changes"} body="A reason is required and is saved for the learning loop."
        onCancel={close} onConfirm={() => (dialog === "reject" ? reject : changes).mutate(reason, { onSettled: close })}>
        <Textarea rows={3} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Reason" />
      </ConfirmDialog>
      <ConfirmDialog open={dialog === "execute"} title="Execute the approved change?" confirmLabel="Execute"
        body={<>The backend re-validates, re-checks the approval hash, kill switches and manual changes, then {a.payload?.kind === "ENABLE" ? "turns the verified resources on" : "creates resources PAUSED and verifies them by reading them back"}. {a.integration_mode === "MOCK" && <b>MOCK: only the simulated environment changes.</b>}</>}
        onCancel={close} onConfirm={() => execute.mutate({}, { onSettled: close })} />
      <ConfirmDialog open={dialog === "edit"} title="Edit payload → new version" confirmLabel="Create new version"
        body="Saving creates version +1, re-runs validation and invalidates this version. The new version needs its own approval."
        onCancel={close} onConfirm={() => { try { edit.mutate(JSON.parse(payloadText), { onSuccess: (n: any) => nav(`/approvals/${n.id}`), onSettled: () => setDialog("") }); } catch { alert("Invalid JSON"); } }}>
        <Textarea rows={14} className="font-mono text-xs" value={payloadText} onChange={(e) => setPayloadText(e.target.value)} />
      </ConfirmDialog>
    </div>
  );
}
