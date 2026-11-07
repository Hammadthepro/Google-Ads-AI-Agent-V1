import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { ROLE_LABEL, useAuth } from "../lib/auth";
import { useAction, useClients } from "../lib/hooks";
import { Badge, Button, Card, dt, ErrorBox, Field, Input, Notice, PageHeader, Select, Spinner, Table } from "../components/ui";

const MATRIX: [string, string[]][] = [
  ["Manage users / roles", ["SUPER_ADMIN", "ADMIN"]], ["Integrations, system settings, global kill switch", ["SUPER_ADMIN"]],
  ["Create clients & businesses", ["SUPER_ADMIN", "ADMIN"]], ["Run audits", ["SUPER_ADMIN", "ADMIN", "CAMPAIGN_MANAGER", "ANALYST"]],
  ["Create tasks / plans / submit approvals", ["SUPER_ADMIN", "ADMIN", "CAMPAIGN_MANAGER"]], ["Approve / reject / execute changes", ["SUPER_ADMIN", "ADMIN"]],
  ["Tracking gate override", ["SUPER_ADMIN", "ADMIN"]], ["Authorize real Google Ads writes", ["SUPER_ADMIN"]], ["View reports", ["SUPER_ADMIN", "ADMIN", "CAMPAIGN_MANAGER", "ANALYST"]],
];
const ROLES = ["SUPER_ADMIN", "ADMIN", "CAMPAIGN_MANAGER", "ANALYST"];

export default function Users() {
  const { can, user: me } = useAuth();
  const q = useQuery({ queryKey: ["users"], enabled: can("users.manage"), queryFn: () => api.get("/users") });
  const clients = useClients();
  const [f, setF] = useState({ email: "", name: "", role: "ANALYST" });
  const create = useAction((b) => api.post("/users", b), [["users"]]);
  const patch = useAction(({ id, ...b }) => api.patch(`/users/${id}`, b), [["users"]]);
  if (!can("users.manage")) return <div className="space-y-6"><PageHeader title="Users & Roles" /><Notice>Only Admins and Super Admins can manage users.</Notice><Card title="Role permissions"><Matrix /></Card></div>;
  return (
    <div className="space-y-6">
      <PageHeader title="Users & Roles" subtitle="Users must be invited before they can sign in. Permissions are enforced by the backend on every request." />
      <Card title="Invite user">
        <div className="grid gap-3 md:grid-cols-4">
          <Field label="Email"><Input value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} /></Field>
          <Field label="Name"><Input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
          <Field label="Role"><Select value={f.role} onChange={(e) => setF({ ...f, role: e.target.value })} options={ROLES.map((r) => ({ value: r, label: ROLE_LABEL[r] }))} /></Field>
          <div className="flex items-end"><Button variant="primary" disabled={!f.email || create.isPending} onClick={() => create.mutate({ ...f, client_ids: [] })}>Invite</Button></div>
        </div>
        <ErrorBox error={create.error} />
      </Card>
      <Card title="Users">
        {q.isLoading ? <Spinner /> : <Table rows={q.data ?? []} columns={[
          { key: "email", label: "User", render: (u) => <div><div className="font-medium">{u.name || u.email}</div><div className="text-xs text-slate-500">{u.email}</div></div> },
          { key: "role", label: "Role", render: (u) => <Select value={u.role} disabled={u.id === me?.id} onChange={(e) => patch.mutate({ id: u.id, role: e.target.value })} options={ROLES.map((r) => ({ value: r, label: ROLE_LABEL[r] }))} /> },
          { key: "client_ids", label: "Assigned clients", render: (u) => ["SUPER_ADMIN", "ADMIN"].includes(u.role) ? <Badge>all clients</Badge> : (
            <div className="flex flex-wrap gap-1">{(clients.data ?? []).map((c: any) => {
              const on = u.client_ids.includes(c.id);
              return <button key={c.id} onClick={() => patch.mutate({ id: u.id, client_ids: on ? u.client_ids.filter((x: string) => x !== c.id) : [...u.client_ids, c.id] })}>
                <Badge tone={on ? "blue" : "slate"}>{on ? "✓ " : ""}{c.name}</Badge></button>;
            })}</div>) },
          { key: "is_active", label: "Status", render: (u) => <Badge tone={u.is_active ? "green" : "red"}>{u.is_active ? "active" : "deactivated"}</Badge> },
          { key: "last_login_at", label: "Last login", render: (u) => dt(u.last_login_at) },
          { key: "a", label: "", render: (u) => u.id !== me?.id && <Button size="sm" onClick={() => patch.mutate({ id: u.id, is_active: !u.is_active })}>{u.is_active ? "Deactivate" : "Activate"}</Button> },
        ]} />}
        <ErrorBox error={patch.error} />
      </Card>
      <Card title="Role permissions"><Matrix /></Card>
    </div>
  );
}

function Matrix() {
  return (
    <Table rows={MATRIX.map(([cap, roles]) => ({ id: cap, cap, roles }))} columns={[{ key: "cap", label: "Capability" },
      ...ROLES.map((r) => ({ key: r, label: ROLE_LABEL[r], render: (row: any) => row.roles.includes(r) ? <span className="text-emerald-600">✓</span> : <span className="text-slate-300">—</span> }))]} />
  );
}
