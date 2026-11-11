import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api/client";
import { MeasurementPanel } from "../components/MeasurementPanel";
import { useAuth } from "../lib/auth";
import { useAction, useBusinesses } from "../lib/hooks";
import { Button, Card, DataSourceBadge, ErrorBox, Field, Input, Notice, PageHeader, Select, Spinner, StatusBadge, Table, Textarea } from "../components/ui";

export function Businesses() {
  const { data, isLoading } = useBusinesses();
  const nav = useNavigate();
  return (
    <div className="space-y-6">
      <PageHeader title="Businesses" subtitle="Business profiles feed every audit, keyword research, strategy and validation." />
      <Card>
        {isLoading ? <Spinner /> : (
          <Table rows={data ?? []} onRowClick={(b) => nav(`/businesses/${b.id}`)} columns={[
            { key: "name", label: "Business", render: (b) => <span className="font-medium">{b.name}</span> },
            { key: "client_name", label: "Client" },
            { key: "business_type", label: "Type" },
            { key: "website_url", label: "Website" },
            { key: "services", label: "Services", render: (b) => b.services.filter((s: any) => !s.is_excluded).length },
            { key: "competitors", label: "Competitors", render: (b) => b.competitors.length },
            { key: "measurement", label: "Measurement", render: (b) => !b.measurement?.evaluated_at ? <span className="text-slate-400">not audited</span> : b.measurement.counts.issue ? `${b.measurement.counts.issue} problem(s)` : "no problems found" },
          ]} />
        )}
      </Card>
    </div>
  );
}

const lines = (s: string) => s.split("\n").map((x) => x.trim()).filter(Boolean);

function ProfileEditor({ b, onSaved }: { b: any; onSaved: () => void }) {
  const { can } = useAuth();
  const [f, setF] = useState<any>(null);
  useEffect(() => {
    setF({ ...b, verified_claims_text: (b.verified_claims || []).join("\n"), brand_rules_text: (b.brand_rules || []).join("\n"),
      zip_text: (b.zip_codes || []).join(", "), hours_text: Object.entries(b.business_hours || {}).map(([k, v]) => `${k}: ${v}`).join("\n"),
      services: b.services.map((s: any) => ({ ...s })), competitors: b.competitors.map((c: any) => ({ ...c })) });
  }, [b]);
  const save = useAction((v) => api.patch(`/businesses/${b.id}`, v));
  if (!f) return null;
  const set = (k: string, v: unknown) => setF({ ...f, [k]: v });
  const submit = () => {
    const hours: Record<string, string> = {};
    lines(f.hours_text).forEach((l) => { const [k, ...r] = l.split(":"); if (r.length) hours[k.trim()] = r.join(":").trim(); });
    save.mutate({ name: f.name, business_type: f.business_type, website_url: f.website_url, gbp_url: f.gbp_url, phone: f.phone, address: f.address,
      lead_form_url: f.lead_form_url, thank_you_url: f.thank_you_url, default_radius_miles: Number(f.default_radius_miles) || 20,
      zip_codes: f.zip_text.split(",").map((z: string) => z.trim()).filter(Boolean), business_hours: hours,
      verified_claims: lines(f.verified_claims_text), brand_rules: lines(f.brand_rules_text), competitor_ads_text: f.competitor_ads_text,
      services: f.services.filter((s: any) => s.name), competitors: f.competitors.filter((c: any) => c.name) }, { onSuccess: onSaved });
  };
  const ro = !can("clients.edit");
  return (
    <Card title="Business profile" actions={!ro && <Button variant="primary" disabled={save.isPending} onClick={submit}>Save profile</Button>}>
      <fieldset disabled={ro} className="space-y-5">
        <div className="grid gap-3 md:grid-cols-3">
          <Field label="Business name"><Input value={f.name} onChange={(e) => set("name", e.target.value)} /></Field>
          <Field label="Business type"><Select value={f.business_type} onChange={(e) => set("business_type", e.target.value)}
            options={["home_services", "dental", "legal", "medical", "other"].map((v) => ({ value: v, label: v.replace("_", " ") }))} /></Field>
          <Field label="Phone"><Input value={f.phone} onChange={(e) => set("phone", e.target.value)} /></Field>
          <Field label="Website"><Input value={f.website_url} onChange={(e) => set("website_url", e.target.value)} /></Field>
          <Field label="Google Business Profile link"><Input value={f.gbp_url} onChange={(e) => set("gbp_url", e.target.value)} /></Field>
          <Field label="Address"><Input value={f.address} onChange={(e) => set("address", e.target.value)} /></Field>
          <Field label="Lead form page"><Input value={f.lead_form_url} onChange={(e) => set("lead_form_url", e.target.value)} /></Field>
          <Field label="Thank-you page"><Input value={f.thank_you_url} onChange={(e) => set("thank_you_url", e.target.value)} /></Field>
          <Field label="Default radius (miles)" hint="Default 20 miles (decision D-24)"><Input type="number" value={f.default_radius_miles} onChange={(e) => set("default_radius_miles", e.target.value)} /></Field>
          <Field label="ZIP codes (optional, comma separated)" hint="If set, ZIPs are targeted instead of the radius"><Input value={f.zip_text} onChange={(e) => set("zip_text", e.target.value)} /></Field>
          <Field label="Business hours (one per line, e.g. mon-fri: 07:00-19:00)"><Textarea rows={3} value={f.hours_text} onChange={(e) => set("hours_text", e.target.value)} /></Field>
        </div>
        <div className="grid gap-5 md:grid-cols-2">
          <div>
            <div className="mb-2 text-sm font-medium">Services</div>
            {f.services.map((s: any, i: number) => (
              <div key={i} className="mb-2 flex gap-2">
                <Input placeholder="Service" value={s.name} onChange={(e) => { const n = [...f.services]; n[i] = { ...s, name: e.target.value }; set("services", n); }} />
                <Input placeholder="Landing page URL" value={s.landing_page_url} onChange={(e) => { const n = [...f.services]; n[i] = { ...s, landing_page_url: e.target.value }; set("services", n); }} />
                <label className="flex items-center gap-1 whitespace-nowrap text-xs text-slate-600">
                  <input type="checkbox" checked={s.is_excluded} onChange={(e) => { const n = [...f.services]; n[i] = { ...s, is_excluded: e.target.checked }; set("services", n); }} />excluded</label>
                <Button size="sm" variant="ghost" onClick={() => set("services", f.services.filter((_: any, j: number) => j !== i))}>✕</Button>
              </div>
            ))}
            <Button size="sm" onClick={() => set("services", [...f.services, { name: "", landing_page_url: "", is_excluded: false }])}>+ Add service</Button>
          </div>
          <div>
            <div className="mb-2 text-sm font-medium">Competitors (up to 3)</div>
            {f.competitors.map((c: any, i: number) => (
              <div key={i} className="mb-2 flex gap-2">
                <Input placeholder="Name" value={c.name} onChange={(e) => { const n = [...f.competitors]; n[i] = { ...c, name: e.target.value }; set("competitors", n); }} />
                <Input placeholder="Website" value={c.website_url} onChange={(e) => { const n = [...f.competitors]; n[i] = { ...c, website_url: e.target.value }; set("competitors", n); }} />
                <Button size="sm" variant="ghost" onClick={() => set("competitors", f.competitors.filter((_: any, j: number) => j !== i))}>✕</Button>
              </div>
            ))}
            {f.competitors.length < 3 && <Button size="sm" onClick={() => set("competitors", [...f.competitors, { name: "", website_url: "" }])}>+ Add competitor</Button>}
          </div>
        </div>
        <div className="grid gap-3 md:grid-cols-3">
          <Field label="Verified claims (one per line)" hint="Ads may only use these claims"><Textarea rows={4} value={f.verified_claims_text} onChange={(e) => set("verified_claims_text", e.target.value)} /></Field>
          <Field label="Brand rules (one per line)" hint='Use "Avoid: word, phrase" to ban phrases'><Textarea rows={4} value={f.brand_rules_text} onChange={(e) => set("brand_rules_text", e.target.value)} /></Field>
          <Field label="Pasted competitor ads (optional)" hint="From the Ads Transparency Center; never assumed to be live"><Textarea rows={4} value={f.competitor_ads_text} onChange={(e) => set("competitor_ads_text", e.target.value)} /></Field>
        </div>
      </fieldset>
      {save.isSuccess && <div className="mt-3"><Notice tone="green">Profile saved.</Notice></div>}
      <div className="mt-3"><ErrorBox error={save.error} /></div>
    </Card>
  );
}

export function BusinessDetail() {
  const { id } = useParams();
  const q = useQuery({ queryKey: ["business", id], queryFn: () => api.get(`/businesses/${id}`) });
  const fixes = useQuery({ queryKey: ["fixes", q.data?.client_id], enabled: !!q.data, queryFn: () => api.get(`/fixes?client_id=${q.data.client_id}`) });
  const audits = useQuery({ queryKey: ["audits", id], queryFn: () => api.get(`/audits?business_id=${id}`) });
  const runAudit = useAction((t) => api.post("/audits", { business_id: id, audit_type: t }), [["audits", id!]]);
  const updFix = useAction(({ fid, status }) => api.patch(`/fixes/${fid}`, { status }), [["fixes", q.data?.client_id]]);
  const { can } = useAuth();
  if (q.isLoading || !q.data) return <Spinner />;
  const b = q.data;
  const myFixes = (fixes.data ?? []).filter((f: any) => f.business_id === b.id);
  return (
    <div className="space-y-6">
      <PageHeader title={b.name} subtitle={<Link className="text-brand-700" to={`/clients/${b.client_id}`}>← Client</Link>}
        actions={can("audits.run") && <>{["ALL", "KEYWORD"].map((t) => <Button key={t} onClick={() => runAudit.mutate(t)} disabled={runAudit.isPending}>
          {runAudit.isPending ? "Running…" : t === "ALL" ? "Run website + GBP + competitor audits" : "Run keyword research"}</Button>)}</>} />
      <ErrorBox error={runAudit.error} />
      <MeasurementPanel businessId={b.id} />
      <ProfileEditor b={b} onSaved={() => q.refetch()} />
      <div className="grid gap-6 lg:grid-cols-2">
        <Card title="Client fix tracker">
          <Table rows={myFixes} empty="No open fixes." columns={[
            { key: "issue", label: "Issue", className: "max-w-xs" }, { key: "priority", label: "Priority", render: (f) => <StatusBadge value={f.priority} /> },
            { key: "owner", label: "Owner", className: "max-w-[10rem] truncate" }, { key: "status", label: "Status", render: (f) => <StatusBadge value={f.status} /> },
            { key: "act", label: "", render: (f) => can("plans.create") && f.status !== "RESOLVED" ? <Button size="sm" onClick={() => updFix.mutate({ fid: f.id, status: "RESOLVED" })}>Resolve</Button> : null },
          ]} />
        </Card>
        <Card title="Audits">
          <Table rows={audits.data ?? []} empty="No audits yet." columns={[
            { key: "audit_type", label: "Type", render: (a) => <Link className="text-brand-700 hover:underline" to={`/audits/${a.id}`}>{a.audit_type}</Link> },
            { key: "status", label: "Status", render: (a) => <StatusBadge value={a.status} /> },
            { key: "summary", label: "Summary", className: "max-w-xs text-xs" },
            { key: "source_type", label: "Data", render: (a) => <DataSourceBadge source={a.source_type} /> },
          ]} />
        </Card>
      </div>
    </div>
  );
}
