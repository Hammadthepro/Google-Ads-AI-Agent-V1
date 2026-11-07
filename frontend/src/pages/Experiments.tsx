import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { useAuth } from "../lib/auth";
import { useAction, useClients } from "../lib/hooks";
import { Badge, Button, Card, DataSourceBadge, ErrorBox, Field, Input, money, Notice, PageHeader, Select, Spinner, StatusBadge, Table } from "../components/ui";

function Results({ id }: { id: string }) {
  const { can } = useAuth();
  const q = useQuery({ queryKey: ["exp", id], queryFn: () => api.get(`/experiments/${id}/results`) });
  const win = useAction(({ arm }) => api.post(`/experiments/${id}/select-winner`, { arm, reason: "Chosen by a person after reviewing both arms" }), [["exp", id], ["experiments"]]);
  if (q.isLoading) return <Spinner />;
  const r = q.data?.results ?? {};
  const arms = Object.entries(r.arms ?? {}).map(([k, v]: any) => ({ id: k, arm: k, ...v }));
  return (
    <div className="space-y-3">
      {r.reason && <Notice>{r.reason}</Notice>}
      <Table rows={arms} empty="No results yet." columns={[{ key: "arm", label: "Arm" }, { key: "clicks", label: "Clicks" }, { key: "conversions", label: "Conversions" },
        { key: "cost", label: "Cost", render: (a) => money(a.cost) }, { key: "cost_per_lead", label: "Cost per lead", render: (a) => money(a.cost_per_lead) }]} />
      {arms.length > 0 && <div className="text-sm">Enough data in both arms: {r.enough_data ? <Badge tone="green">yes</Badge> : <Badge>not yet</Badge>}</div>}
      <Notice tone="amber">{q.data?.results?.note ?? "A person must choose the winner. The system never picks automatically."}</Notice>
      {can("experiments.pick_winner") && r.enough_data && ["RUNNING", "AWAITING_WINNER"].includes(q.data.experiment.status) && (
        <div className="flex gap-2">{["control", "treatment"].map((arm) => <Button key={arm} onClick={() => win.mutate({ arm })}>Choose {arm} → approval</Button>)}</div>)}
      {win.data && <Notice tone="green">Winner approval created. <Link className="underline" to={`/approvals/${win.data.approval_id}`}>Open it</Link> — the winner applies only after approval and execution.</Notice>}
      <ErrorBox error={win.error} />
    </div>
  );
}

export default function Experiments() {
  const { can } = useAuth();
  const list = useQuery({ queryKey: ["experiments"], queryFn: () => api.get("/experiments") });
  const clients = useClients();
  const [clientId, setClientId] = useState("");
  const structure = useQuery({ queryKey: ["structure", clientId], enabled: !!clientId, queryFn: () => api.get(`/clients/${clientId}/structure`) });
  const [f, setF] = useState({ campaign_id: "", variable: "AD_ANGLE", hypothesis: "", treatment: "" });
  const create = useAction((b) => api.post("/experiments", b), [["experiments"]]);
  const [open, setOpen] = useState("");
  return (
    <div className="space-y-6">
      <PageHeader title="Experiments" subtitle="One variable per test · approval to start · 50/50 split · a person picks the winner." />
      {can("plans.create") && (
        <Card title="Propose an experiment">
          <div className="grid gap-3 md:grid-cols-5">
            <Field label="Client"><Select value={clientId} onChange={(e) => setClientId(e.target.value)} options={[{ value: "", label: "Choose…" }, ...(clients.data ?? []).map((c: any) => ({ value: c.id, label: c.name }))]} /></Field>
            <Field label="Search campaign"><Select value={f.campaign_id} onChange={(e) => setF({ ...f, campaign_id: e.target.value })}
              options={[{ value: "", label: "Choose…" }, ...(structure.data?.campaigns ?? []).filter((c: any) => !c.read_only).map((c: any) => ({ value: c.id, label: c.name }))]} /></Field>
            <Field label="Variable"><Select value={f.variable} onChange={(e) => setF({ ...f, variable: e.target.value })} options={["BIDDING", "AD_ANGLE", "MATCH_TYPE", "LANDING_PAGE"].map((v) => ({ value: v, label: v.replace("_", " ") }))} /></Field>
            <Field label="Treatment" hint="e.g. Same-Day Service angle"><Input value={f.treatment} onChange={(e) => setF({ ...f, treatment: e.target.value })} /></Field>
            <Field label="Hypothesis"><Input value={f.hypothesis} onChange={(e) => setF({ ...f, hypothesis: e.target.value })} /></Field>
          </div>
          <div className="mt-3 flex items-center gap-3">
            <Button variant="primary" disabled={!f.campaign_id || !f.hypothesis || create.isPending}
              onClick={() => create.mutate({ campaign_id: f.campaign_id, variable: f.variable, hypothesis: f.hypothesis, treatment: { value: f.treatment } })}>Propose (creates approval)</Button>
            <ErrorBox error={create.error} />
          </div>
        </Card>
      )}
      <Card title="Experiments">
        {list.isLoading ? <Spinner /> : (
          <Table rows={list.data ?? []} onRowClick={(e) => setOpen(open === e.id ? "" : e.id)} columns={[
            { key: "name", label: "Experiment", render: (e) => <span className="font-medium">{e.name}</span> }, { key: "variable", label: "Variable" },
            { key: "status", label: "Status", render: (e) => <StatusBadge value={e.status} /> }, { key: "winner_arm", label: "Winner" },
            { key: "start", label: "Start approval", render: (e) => e.start_approval_id && <Link onClick={(ev) => ev.stopPropagation()} className="text-brand-700" to={`/approvals/${e.start_approval_id}`}>open</Link> },
            { key: "source_type", label: "Data", render: (e) => <DataSourceBadge source={e.source_type} /> },
          ]} />
        )}
      </Card>
      {open && <Card title="Results"><Results id={open} /></Card>}
    </div>
  );
}
