import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { useAuth } from "../lib/auth";
import { useAction, useClients } from "../lib/hooks";
import { Badge, Button, Card, ConfirmDialog, ErrorBox, Input, Notice, PageHeader, Spinner, Textarea } from "../components/ui";

function SettingRow({ s, editable }: { s: any; editable: boolean }) {
  const [val, setVal] = useState(JSON.stringify(s.value));
  const save = useAction((v) => api.patch("/settings", { key: s.key, value: v }), [["settings"]]);
  const dirty = val !== JSON.stringify(s.value);
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Input className="w-full font-mono text-xs sm:w-64" value={val} disabled={!editable || s.super_admin_only} onChange={(e) => setVal(e.target.value)} />
      {dirty && <Button size="sm" variant="primary" onClick={() => { try { save.mutate(JSON.parse(val)); } catch { alert("Value must be valid JSON"); } }}>Save</Button>}
      {save.error ? <span className="text-xs text-rose-600">{(save.error as Error).message}</span> : null}
    </div>
  );
}

const GROUP_LABEL: Record<string, string> = {
  measurement: "Conversion measurement", approvals: "Approvals", feasibility: "Budget feasibility", enough_data: "Enough-data gate", negatives: "Negative keywords",
  wasted: "Wasted spend", monitoring: "Monitoring", emergency: "Emergency brake", experiments: "Experiments", intake: "Client intake", general: "Operating mode & write keys",
};
/** Settings keep their order from the server; they are only grouped by the part of the key before the first dot. */
function groupSettings(list: any[]): [string, any[]][] {
  const out = new Map<string, any[]>();
  for (const x of list) { const g = x.key.includes(".") ? x.key.split(".")[0] : "general"; (out.get(g) ?? out.set(g, []).get(g)!).push(x); }
  return [...out.entries()];
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
  if (!can("settings.view")) return <div><PageHeader title="Settings" /><Notice>Admins and Super Admins only.</Notice></div>;
  const kill = (scope: string, id = "") => (ks.data ?? []).find((k: any) => k.scope === scope && k.scope_id === id)?.is_on;
  const cfg = q.data?.config;
  const realAuth = q.data?.settings.find((s: any) => s.key === "real_writes_authorized")?.value;
  return (
    <div className="space-y-6">
      <PageHeader title="Settings" subtitle="Thresholds are configurable TEST VALUES from docs/DECISIONS_REQUIRED.md — not final production policy." />
      <Card title="Kill switches">
        <div className="space-y-3">
          <div className="flex items-center justify-between gap-4 rounded-lg border border-slate-200 p-4">
            <div><div className="text-[13px] font-semibold text-slate-900">Global kill switch</div><div className="text-xs text-slate-500">Blocks every Google Ads write and all automation.</div></div>
            <Button variant={kill("GLOBAL") ? "secondary" : "danger"} disabled={!can("killswitch.global")} onClick={() => setKill.mutate({ scope: "GLOBAL", scope_id: "", is_on: !kill("GLOBAL"), reason: "Set from settings" })}>
              {kill("GLOBAL") ? "Turn OFF" : "Turn ON"}</Button>
          </div>
          {(clients.data ?? []).map((c: any) => (
            <div key={c.id} className="flex items-center justify-between gap-4 border-b border-slate-100 px-4 py-2.5 text-[13px] last:border-0">
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
      <Card title="Thresholds & policies" flush description="Grouped by area. Values are JSON; a changed value is marked until it matches the default again.">
        {q.isLoading ? <div className="p-5"><Spinner /></div> : (
          <div className="divide-y divide-slate-200">
            {groupSettings(q.data.settings).map(([group, rows]) => (
              <section key={group} aria-label={GROUP_LABEL[group] ?? group}>
                <h3 className="bg-slate-50/70 px-5 py-2 text-xs font-medium text-slate-600">{GROUP_LABEL[group] ?? group} <span className="font-normal text-slate-400">· {rows.length}</span></h3>
                <ul className="divide-y divide-slate-100">
                  {rows.map((x: any) => (
                    <li key={x.key} className="grid gap-x-6 gap-y-2 px-5 py-3 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)_minmax(0,0.8fr)_72px] lg:items-start">
                      <div className="min-w-0"><code className="break-all font-mono text-xs text-slate-800">{x.key}</code>{x.description && <div className="mt-0.5 text-xs leading-5 text-slate-500">{x.description}</div>}</div>
                      <SettingRow s={x} editable={can("settings.manage")} />
                      <div className="min-w-0 text-xs text-slate-500"><span className="lg:hidden">Default </span><code className="break-all font-mono">{JSON.stringify(x.default)}</code></div>
                      <div className="lg:text-right">{x.is_default ? <Badge>default</Badge> : <Badge tone="blue" dot>changed</Badge>}</div>
                    </li>))}
                </ul>
              </section>))}
          </div>)}
      </Card>
      <ConfirmDialog open={authOpen} title={realAuth ? "Revoke real-write authorization" : "Authorize real Google Ads writes"} tone="danger" confirmLabel={realAuth ? "Revoke" : "Authorize"}
        body={realAuth ? "Real writes will be blocked immediately." : "This is the second key. It has no effect unless GOOGLE_ADS_WRITES_ENABLED=true and the account is allow-listed. A written reason is recorded."}
        onCancel={() => setAuthOpen(false)} onConfirm={() => { authorize.mutate({ key: "real_writes_authorized", value: !realAuth, reason: authReason }); setAuthOpen(false); }}>
        {!realAuth && <Textarea rows={3} placeholder="Reason (20+ characters)" value={authReason} onChange={(e) => setAuthReason(e.target.value)} />}
      </ConfirmDialog>
    </div>
  );
}
