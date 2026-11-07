import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { useAuth } from "../lib/auth";
import { useAction } from "../lib/hooks";
import { Badge, Button, Card, dt, ErrorBox, Field, Notice, PageHeader, Select, Spinner, StatusBadge, Table, Textarea } from "../components/ui";

export default function Learning() {
  const { can } = useAuth();
  const rules = useQuery({ queryKey: ["playbook"], queryFn: () => api.get("/playbook") });
  const fb = useQuery({ queryKey: ["feedback"], queryFn: () => api.get("/learning/feedback") });
  const cases = useQuery({ queryKey: ["cases"], queryFn: () => api.get("/learning/regression-cases") });
  const preds = useQuery({ queryKey: ["preds"], queryFn: () => api.get("/learning/predictions") });
  const [text, setText] = useState("");
  const [scope, setScope] = useState("GLOBAL");
  const add = useAction(() => api.post("/playbook", { text, scope }), [["playbook"]]);
  const toggle = useAction((r) => api.patch(`/playbook/${r.id}`, { is_active: !r.is_active }), [["playbook"]]);
  const run = useAction(() => api.post("/learning/regression-cases/run"), [["cases"]]);
  return (
    <div className="space-y-6">
      <PageHeader title="Learning / Playbook" subtitle="People turn repeated edits and rejections into plain-language rules. AI never rewrites production rules." />
      <Card title="Playbook rules" actions={<span className="text-xs text-slate-500">Used by the drafting AI on every new plan</span>}>
        {rules.isLoading ? <Spinner /> : <Table rows={rules.data ?? []} columns={[{ key: "text", label: "Rule" }, { key: "scope", label: "Scope" }, { key: "version", label: "Ver." },
          { key: "is_active", label: "Active", render: (r) => <Badge tone={r.is_active ? "green" : "slate"}>{r.is_active ? "active" : "off"}</Badge> },
          { key: "a", label: "", render: (r) => can("playbook.manage") && <Button size="sm" onClick={() => toggle.mutate(r)}>{r.is_active ? "Disable" : "Enable"}</Button> }]} />}
        {can("playbook.manage") && (
          <div className="mt-4 grid gap-3 md:grid-cols-[1fr_12rem_auto] md:items-end">
            <Field label="New rule (plain language)"><Textarea rows={2} value={text} onChange={(e) => setText(e.target.value)} /></Field>
            <Field label="Scope"><Select value={scope} onChange={(e) => setScope(e.target.value)} options={[{ value: "GLOBAL", label: "All clients" }, { value: "BUSINESS_TYPE", label: "Business type" }, { value: "CLIENT", label: "One client" }]} /></Field>
            <Button variant="primary" disabled={text.length < 10} onClick={() => add.mutate({}, { onSuccess: () => setText("") })}>Add rule</Button>
          </div>
        )}
        <ErrorBox error={add.error} />
      </Card>
      <Card title="Regression cases" actions={can("playbook.manage") && <Button onClick={() => run.mutate({})} disabled={run.isPending}>{run.isPending ? "Running…" : "Re-run all cases"}</Button>}>
        <Notice>Re-run saved cases before changing any prompt or rule. Each case drafts with the current prompts and playbook and checks the expectations.</Notice>
        <div className="mt-3"><Table rows={cases.data ?? []} columns={[{ key: "name", label: "Case" },
          { key: "last", label: "Last result", render: (c) => c.last_result?.passed === undefined ? "never run" : <StatusBadge value={c.last_result.passed ? "PASS" : "FAIL"} /> },
          { key: "checks", label: "Checks", render: (c) => <span className="text-xs">{(c.last_result?.checks ?? []).map((x: any) => `${x.passed ? "✓" : "✕"} ${x.expectation}`).join(" · ")}</span> },
          { key: "pv", label: "Prompt", render: (c) => <span className="text-xs">{c.last_result?.prompt_version}</span> }, { key: "last_run_at", label: "Run at", render: (c) => dt(c.last_run_at) }]} /></div>
      </Card>
      <div className="grid gap-6 lg:grid-cols-2">
        <Card title="Edits, rejections & challenger feedback">
          <Table rows={(fb.data ?? []).slice(0, 40)} columns={[{ key: "type", label: "Type", render: (f) => <StatusBadge value={f.type} /> }, { key: "approval", label: "Item", className: "max-w-[14rem] truncate" },
            { key: "reason", label: "Detail", className: "max-w-xs text-xs" }, { key: "at", label: "When", render: (f) => dt(f.at) }]} />
        </Card>
        <Card title="Predictions vs actual">
          <Table rows={preds.data ?? []} empty="Predictions are recorded when a campaign is created and evaluated after ~5 weeks." columns={[
            { key: "subject", label: "Subject" }, { key: "predicted", label: "Predicted/wk", render: (p) => p.predicted?.weekly_conversions },
            { key: "actual", label: "Actual/wk", render: (p) => p.actual?.weekly_conversions ?? "pending" }, { key: "err", label: "Error", render: (p) => p.actual?.error_pct != null ? `${p.actual.error_pct}%` : "—" },
            { key: "evaluate_after", label: "Evaluate after" }]} />
        </Card>
      </div>
    </div>
  );
}
