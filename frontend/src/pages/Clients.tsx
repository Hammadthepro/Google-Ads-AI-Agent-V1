import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api/client";
import { useAuth } from "../lib/auth";
import { useAction, useClients } from "../lib/hooks";
import { Badge, Button, Card, ErrorBox, Field, Input, KV, PageHeader, Spinner, StatusBadge, Table } from "../components/ui";

export function Clients() {
  const { data, isLoading } = useClients();
  const { can } = useAuth();
  const nav = useNavigate();
  const [form, setForm] = useState({ name: "", owner_contact: "", backup_contact: "", tag_access_contact: "" });
  const create = useAction((b) => api.post("/clients", b), [["clients"]]);
  return (
    <div className="space-y-6">
      <PageHeader title="Clients" subtitle="A client can have several businesses; a business with its own Google Ads account is a separate client." />
      {can("clients.create") && (
        <Card title="New client">
          <div className="grid gap-3 md:grid-cols-4">
            <Field label="Client name"><Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></Field>
            <Field label="Client owner"><Input value={form.owner_contact} onChange={(e) => setForm({ ...form, owner_contact: e.target.value })} placeholder="name / email" /></Field>
            <Field label="Backup contact"><Input value={form.backup_contact} onChange={(e) => setForm({ ...form, backup_contact: e.target.value })} /></Field>
            <Field label="Website / tag access contact"><Input value={form.tag_access_contact} onChange={(e) => setForm({ ...form, tag_access_contact: e.target.value })} /></Field>
          </div>
          <div className="mt-3 flex items-center gap-3">
            <Button variant="primary" disabled={!form.name || create.isPending} onClick={() => create.mutate(form, { onSuccess: (c: any) => nav(`/clients/${c.id}`) })}>Create client</Button>
            <ErrorBox error={create.error} />
          </div>
        </Card>
      )}
      <Card title="All clients">
        {isLoading ? <Spinner /> : (
          <Table rows={data ?? []} onRowClick={(c) => nav(`/clients/${c.id}`)} columns={[
            { key: "name", label: "Client", render: (c) => <span className="font-medium text-slate-900">{c.name}</span> },
            { key: "businesses", label: "Businesses", render: (c) => c.businesses.map((b: any) => b.name).join(", ") || "—" },
            { key: "owner_contact", label: "Owner" },
            { key: "tag_access_contact", label: "Tag access", className: "max-w-xs truncate" },
            { key: "flags", label: "", render: (c) => c.automation_frozen ? <Badge tone="red">automation frozen</Badge> : null },
          ]} />
        )}
      </Card>
    </div>
  );
}

export function ClientDetail() {
  const { id } = useParams();
  const { can } = useAuth();
  const q = useQuery({ queryKey: ["client", id], queryFn: () => api.get(`/clients/${id}`) });
  const structure = useQuery({ queryKey: ["structure", id], queryFn: () => api.get(`/clients/${id}/structure`) });
  const [b, setB] = useState({ name: "", website_url: "", phone: "", address: "", business_type: "home_services" });
  const addBiz = useAction((v) => api.post(`/clients/${id}/businesses`, v), [["client", id!], ["businesses"]]);
  const unfreeze = useAction(() => api.post(`/clients/${id}/unfreeze`), [["client", id!]]);
  if (q.isLoading || !q.data) return <Spinner />;
  const c = q.data;
  return (
    <div className="space-y-6">
      <PageHeader title={c.name} subtitle="Client profile" actions={<>
        {c.automation_frozen && <Badge tone="red">automation frozen by emergency brake</Badge>}
        {c.automation_frozen && can("killswitch.client") && <Button size="sm" onClick={() => unfreeze.mutate({})}>Unfreeze automation</Button>}
        <Link to={`/tasks?client=${c.id}`}><Button variant="primary">Run a task</Button></Link></>} />
      <div className="grid gap-6 lg:grid-cols-3">
        <Card title="Contacts" className="lg:col-span-1">
          <KV items={[["Owner", c.owner_contact || "—"], ["Backup", c.backup_contact || "—"], ["Website / tag access", c.tag_access_contact || "—"]]} />
        </Card>
        <Card title="Google Ads accounts" className="lg:col-span-2" actions={<Link className="text-xs text-brand-700" to="/accounts">Manage →</Link>}>
          <Table rows={c.accounts} empty="No Google Ads account linked yet — link one on the Google Ads Accounts page." columns={[
            { key: "customer_id", label: "Customer ID" }, { key: "descriptive_name", label: "Name" },
            { key: "auto_tagging_enabled", label: "Auto-tagging" }, { key: "integration_mode", label: "Mode", render: (a) => <Badge tone="violet">{a.integration_mode}</Badge> },
          ]} />
        </Card>
      </div>
      <Card title="Businesses">
        <Table rows={c.businesses} columns={[
          { key: "name", label: "Business", render: (x) => <Link className="font-medium text-brand-700 hover:underline" to={`/businesses/${x.id}`}>{x.name}</Link> },
          { key: "website_url", label: "Website" },
          { key: "services", label: "Services", render: (x) => x.services.filter((s: any) => !s.is_excluded).map((s: any) => s.name).join(", ") },
          { key: "tracking", label: "Tracking", render: (x) => <StatusBadge value={c.tracking[x.id]?.effective_state} /> },
        ]} />
        {can("clients.edit") && (
          <div className="mt-5 border-t border-slate-100 pt-4">
            <div className="mb-2 text-sm font-medium">Add business</div>
            <div className="grid gap-3 md:grid-cols-5">
              <Input placeholder="Business name" value={b.name} onChange={(e) => setB({ ...b, name: e.target.value })} />
              <Input placeholder="https://website" value={b.website_url} onChange={(e) => setB({ ...b, website_url: e.target.value })} />
              <Input placeholder="Phone" value={b.phone} onChange={(e) => setB({ ...b, phone: e.target.value })} />
              <Input placeholder="Address (street, city, state)" value={b.address} onChange={(e) => setB({ ...b, address: e.target.value })} />
              <Button variant="primary" disabled={!b.name || addBiz.isPending} onClick={() => addBiz.mutate(b)}>Add business</Button>
            </div>
            <div className="mt-2"><ErrorBox error={addBiz.error} /></div>
          </div>
        )}
      </Card>
      <Card title="Campaign structure" actions={structure.data && <Badge tone="violet">{structure.data.source} DATA</Badge>}>
        {structure.isLoading ? <Spinner /> : (
          <Table rows={structure.data?.campaigns ?? []} empty="No campaigns." columns={[
            { key: "name", label: "Campaign", render: (x) => <span className="font-medium">{x.name}</span> },
            { key: "campaign_type", label: "Type", render: (x) => <>{x.campaign_type} {x.read_only && <Badge>read-only in V1</Badge>}</> },
            { key: "status", label: "Status", render: (x) => <StatusBadge value={x.status} /> },
            { key: "daily_budget", label: "Daily budget", render: (x) => `$${x.daily_budget.toFixed(2)}` },
            { key: "weekly_target", label: "Weekly target" },
            { key: "ad_groups", label: "Ad groups", render: (x) => x.ad_groups.length },
            { key: "created_by_app", label: "Created here", render: (x) => x.created_by_app ? <Badge tone="blue">yes</Badge> : "" },
          ]} />
        )}
      </Card>
    </div>
  );
}
