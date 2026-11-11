import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { Badge, Card, dt, ErrorBox, Input, PageHeader, Spinner, StatusBadge, Table, Tabs } from "../components/ui";

export default function Activity() {
  const [action, setAction] = useState("");
  const q = useQuery({ queryKey: ["activity", action], queryFn: () => api.get(`/activity${action ? `?action=${encodeURIComponent(action)}` : ""}`) });
  const alerts = useQuery({ queryKey: ["alerts"], queryFn: () => api.get("/alerts") });
  const ops = useQuery({ queryKey: ["operations"], queryFn: () => api.get("/operations") });
  const runs = useQuery({ queryKey: ["ai-runs"], queryFn: () => api.get("/ai-runs") });
  return (
    <div className="space-y-6">
      <PageHeader title="Activity log" subtitle="Who requested, who approved, which adapter executed, the response and whether read-back passed." />
      <Tabs tabs={[
        { key: "log", label: "Activity", content: (
          <Card actions={<div className="w-64"><Input placeholder="Filter by action prefix, e.g. approval." value={action} onChange={(e) => setAction(e.target.value)} /></div>}>
            {q.isLoading ? <Spinner /> : q.error ? <ErrorBox error={q.error} /> : <Table rows={q.data ?? []} columns={[{ key: "at", label: "When", render: (r) => dt(r.at) }, { key: "actor", label: "Actor" },
              { key: "action", label: "Action", render: (r) => <code className="text-xs">{r.action}</code> }, { key: "entity", label: "Entity" },
              { key: "integration_mode", label: "Mode", render: (r) => r.integration_mode && <Badge tone="violet">{r.integration_mode}</Badge> },
              { key: "details", label: "Details", className: "max-w-md", render: (r) => <code className="block truncate text-[11px] text-slate-500" title={JSON.stringify(r.details)}>{JSON.stringify(r.details)}</code> }]} />}
          </Card>) },
        { key: "ops", label: "External operations", content: (
          <Card><Table rows={ops.data ?? []} columns={[{ key: "created_at", label: "When", render: (o) => dt(o.created_at) }, { key: "operation_type", label: "Type" }, { key: "policy", label: "Policy" },
            { key: "adapter_name", label: "Adapter" }, { key: "integration_mode", label: "Mode", render: (o) => <Badge tone="violet">{o.integration_mode}</Badge> },
            { key: "state", label: "State", render: (o) => <StatusBadge value={o.state} /> }, { key: "verification_passed", label: "Verified", render: (o) => o.verification_passed === null ? "—" : o.verification_passed ? "yes" : "NO" },
            { key: "idempotency_key", label: "Idempotency key", render: (o) => <code className="text-[11px]">{o.idempotency_key.slice(0, 12)}…</code> }]} /></Card>) },
        { key: "ai", label: "AI runs", content: (
          <Card><Table rows={runs.data ?? []} columns={[{ key: "created_at", label: "When", render: (r) => dt(r.created_at) }, { key: "purpose", label: "Purpose" }, { key: "provider", label: "Provider" },
            { key: "model", label: "Model" }, { key: "prompt", label: "Prompt" }, { key: "attempt", label: "Attempt" },
            { key: "validation_ok", label: "Valid", render: (r) => r.validation_ok ? <Badge tone="green">valid</Badge> : <Badge tone="red">rejected</Badge> }, { key: "error", label: "Error", className: "max-w-xs text-xs" }]} /></Card>) },
        { key: "alerts", label: "Alerts", content: (
          <Card><Table rows={alerts.data ?? []} columns={[{ key: "at", label: "When", render: (a) => dt(a.at) }, { key: "category", label: "Category" }, { key: "subject", label: "Subject" },
            { key: "recipients", label: "To", render: (a) => a.recipients.join(", ") }, { key: "status", label: "Delivery", render: (a) => <Badge tone="violet">{a.status}</Badge> }]} /></Card>) },
      ]} />
    </div>
  );
}
