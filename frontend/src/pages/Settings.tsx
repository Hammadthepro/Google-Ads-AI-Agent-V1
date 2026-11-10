import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { useAuth } from "../lib/auth";
import { useAction, useClients } from "../lib/hooks";
import { Badge, Button, Card, ConfirmDialog, ErrorBox, Input, Notice, PageHeader, Spinner, Table, Textarea } from "../components/ui";

function SettingRow({ s, editable }: { s: any; editable: boolean }) {
  const [val, setVal] = useState(JSON.stringify(s.value));
  const save = useAction((v) => api.patch("/settings", { key: s.key, value: v }), [["settings"]]);
  const dirty = val !== JSON.stringify(s.value);
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Input className="max-w-xs font-mono text-xs" value={val} disabled={!editable || s.super_admin_only} onChange={(e) => setVal(e.target.value)} />
      {dirty && <Button size="sm" variant="primary" onClick={() => { try { save.mutate(JSON.parse(val)); } catch { alert("Value must be valid JSON"); } }}>Save</Button>}
      {save.error ? <span className="text-xs text-rose-600">{(save.error as Error).message}</span> : null}
    </div>
  );
}

export default function Settings() {
  const { can } = useAuth();
  const q = useQuery({ queryKey: ["settings"], enabled: can("settings.view"), queryFn: () => api.get("/settings") });
  const ks = useQuery({ queryKey: ["killswitches"], queryFn: () => api.get("/kill-switches") });
  const clients = useClients();
  const setKill = useAction((b) => api.post("/kill-switches", b), [["killswitches"]]);
  const authorize = useAction((b) => api.patch("/settings", b), [["settings"]]);
  const [authOpen, setAuthOpen] = useState(false);
  const [authReason, setAuthReason] = useState("");
  if (!can("settings.view")) return <div><PageHeader title="System Settings" /><Notice>Admins and Super Admins only.</Notice></div>;
  const kill = (scope: string, id = "") => (ks.data ?? []).find((k: any) => k.scope === scope && k.scope_id === id)?.is_on;
  const cfg = q.data?.config;
  const realAuth = q.data?.settings.find((s: any) => s.key === "real_writes_authorized")?.value;
  return (
    <div className="space-y-6">
      <PageHeader title="System Settings" subtitle="Thresholds are configurable TEST VALUES from docs/DECISIONS_REQUIRED.md — not final production policy." />
      <Card title="Kill switches">
        <div className="space-y-3">
          <div className="flex items-center justify-between rounded-lg border border-slate-200 p-3">
            <div><div className="font-medium">Global kill switch</div><div className="text-xs text-slate-500">Blocks every Google Ads write and all automation.</div></div>
            <Button variant={kill("GLOBAL") ? "secondary" : "danger"} disabled={!can("killswitch.global")} onClick={() => setKill.mutate({ scope: "GLOBAL", scope_id: "", is_on: !kill("GLOBAL"), reason: "Set from settings" })}>
              {kill("GLOBAL") ? "Turn OFF" : "Turn ON"}</Button>
          </div>
          {(clients.data ?? []).map((c: any) => (
            <div key={c.id} className="flex items-center justify-between rounded-lg border border-slate-100 px-3 py-2 text-sm">
              <span>{c.name} {kill("CLIENT", c.id) && <Badge tone="red">kill switch on</Badge>} {c.automation_frozen && <Badge tone="amber">automation frozen</Badge>}</span>
              <Button size="sm" disabled={!can("killswitch.client")} onClick={() => setKill.mutate({ scope: "CLIENT", scope_id: c.id, is_on: !kill("CLIENT", c.id), reason: "Set from settings" })}>{kill("CLIENT", c.id) ? "Release" : "Engage"}</Button>
            </div>
          ))}
        </div>
        <ErrorBox error={setKill.error} />
      </Card>
      <Card title="Real Google Ads writes">
        <Notice tone="red">Two keys are required: <code>GOOGLE_ADS_WRITES_ENABLED=true</code> in the server environment (currently <b>{String(cfg?.google_ads_writes_enabled)}</b>) and this Super Admin authorization (currently <b>{realAuth ? "authorized" : "not authorized"}</b>). Accounts must also be allow-listed. V1 keeps both off.</Notice>
        {can("real_writes.authorize") && <div className="mt-3"><Button variant={realAuth ? "secondary" : "danger"} onClick={() => setAuthOpen(true)}>{realAuth ? "Revoke authorization" : "Authorize real writes…"}</Button></div>}
        <ErrorBox error={authorize.error} />
      </Card>
      <Card title="Thresholds & policies">
        {q.isLoading ? <Spinner /> : <Table rows={q.data.settings.map((s: any) => ({ id: s.key, ...s }))} columns={[
          { key: "key", label: "Setting", render: (s) => <div><code className="text-xs">{s.key}</code>{s.description && <div className="text-xs text-slate-500">{s.description}</div>}</div> },
          { key: "value", label: "Value", render: (s) => <SettingRow s={s} editable={can("settings.manage")} /> },
          { key: "default", label: "Default", render: (s) => <code className="text-xs text-slate-500">{JSON.stringify(s.default)}</code> },
          { key: "is_default", label: "", render: (s) => s.is_default ? <Badge>default</Badge> : <Badge tone="blue">changed</Badge> },
        ]} />}
      </Card>
      <ConfirmDialog open={authOpen} title={realAuth ? "Revoke real-write authorization" : "Authorize real Google Ads writes"} tone="danger" confirmLabel={realAuth ? "Revoke" : "Authorize"}
        body={realAuth ? "Real writes will be blocked immediately." : "This is the second key. It has no effect unless GOOGLE_ADS_WRITES_ENABLED=true and the account is allow-listed. A written reason is recorded."}
        onCancel={() => setAuthOpen(false)} onConfirm={() => { authorize.mutate({ key: "real_writes_authorized", value: !realAuth, reason: authReason }); setAuthOpen(false); }}>
        {!realAuth && <Textarea rows={3} placeholder="Reason (20+ characters)" value={authReason} onChange={(e) => setAuthReason(e.target.value)} />}
      </ConfirmDialog>
    </div>
  );
}
