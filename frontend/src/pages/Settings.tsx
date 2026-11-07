import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { useAuth } from "../lib/auth";
import { useAction, useClients } from "../lib/hooks";
import { Badge, Button, Card, ConfirmDialog, ErrorBox, Field, Input, Notice, PageHeader, Select, Spinner, Table, Textarea } from "../components/ui";

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
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["settings"], enabled: can("settings.view"), queryFn: () => api.get("/settings") });
  const ks = useQuery({ queryKey: ["killswitches"], queryFn: () => api.get("/kill-switches") });
  const clients = useClients();
  const failures = useQuery({ queryKey: ["failures"], enabled: can("mock.control"), queryFn: () => api.get("/dev/mock/failures") });
  const setKill = useAction((b) => api.post("/kill-switches", b), [["killswitches"]]);
  const advance = useAction((days) => api.post("/dev/mock/advance-time", { days }));
  const reset = useAction(() => api.post("/dev/mock/reset"));
  const addFail = useAction((b) => api.post("/dev/mock/failures", b), [["failures"]]);
  const delFail = useAction((fid) => api.del(`/dev/mock/failures/${fid}`), [["failures"]]);
  const authorize = useAction((b) => api.patch("/settings", b), [["settings"]]);
  const [fail, setFail] = useState({ adapter: "google_ads", method: "*", failure: "timeout", times: 1 });
  const [confirmReset, setConfirmReset] = useState(false);
  const [authOpen, setAuthOpen] = useState(false);
  const [authReason, setAuthReason] = useState("");
  const [days, setDays] = useState(1);
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
      {can("mock.control") && (
        <Card title="Mock environment controls (TEST_MODE only)">
          <div className="grid gap-6 lg:grid-cols-2">
            <div className="space-y-3">
              <div className="text-sm font-medium">Simulated clock</div>
              <p className="text-xs text-slate-500">Advancing time runs the daily job cycle (sync, tracking recheck, optimisation, emergency brake, fix rechecks, approval expiry) for each simulated day.</p>
              <div className="flex items-end gap-2"><Field label="Days"><Input type="number" min={1} max={30} value={days} onChange={(e) => setDays(Number(e.target.value))} /></Field>
                <Button onClick={() => advance.mutate(days, { onSuccess: () => qc.invalidateQueries() })} disabled={advance.isPending}>{advance.isPending ? "Simulating…" : "Advance time"}</Button></div>
              {advance.data && <Notice tone="green">Simulated clock is now {new Date(advance.data.now).toLocaleString()}.</Notice>}
              <div className="pt-2"><Button variant="danger" onClick={() => setConfirmReset(true)} disabled={reset.isPending}>{reset.isPending ? "Reseeding…" : "Reset & reseed mock world"}</Button></div>
              <ErrorBox error={advance.error || reset.error} />
            </div>
            <div className="space-y-3">
              <div className="text-sm font-medium">Failure injection</div>
              <div className="grid grid-cols-2 gap-2">
                <Select value={fail.adapter} onChange={(e) => setFail({ ...fail, adapter: e.target.value })} options={["google_ads", "places", "pagespeed", "crawler", "ai", "keyword_research"].map((v) => ({ value: v, label: v }))} />
                <Select value={fail.failure} onChange={(e) => setFail({ ...fail, failure: e.target.value })} options={["timeout", "timeout_after_commit", "auth_failure", "permission_denied", "rate_limited", "outage", "invalid_input", "not_found", "empty", "partial_data", "invalid_json", "schema_mismatch"].map((v) => ({ value: v, label: v }))} />
                <Input placeholder="method (* = any)" value={fail.method} onChange={(e) => setFail({ ...fail, method: e.target.value })} />
                <Input type="number" placeholder="times (-1 = always)" value={fail.times} onChange={(e) => setFail({ ...fail, times: Number(e.target.value) })} />
              </div>
              <Button onClick={() => addFail.mutate(fail)}>Inject failure</Button>
              <Table rows={(failures.data ?? []).filter((f: any) => f.is_active)} empty="No active failures." columns={[{ key: "adapter", label: "Adapter" }, { key: "method", label: "Method" }, { key: "failure", label: "Failure" },
                { key: "used", label: "Used" }, { key: "a", label: "", render: (f) => <Button size="sm" onClick={() => delFail.mutate(f.id)}>Clear</Button> }]} />
            </div>
          </div>
        </Card>
      )}
      <ConfirmDialog open={confirmReset} title="Reset the mock world?" tone="danger" confirmLabel="Reset & reseed"
        body="All simulated data, clients, approvals and logs are deleted and reseeded deterministically. Users are kept. This only affects the mock environment."
        onCancel={() => setConfirmReset(false)} onConfirm={() => { setConfirmReset(false); reset.mutate({}, { onSuccess: () => qc.invalidateQueries() }); }} />
      <ConfirmDialog open={authOpen} title={realAuth ? "Revoke real-write authorization" : "Authorize real Google Ads writes"} tone="danger" confirmLabel={realAuth ? "Revoke" : "Authorize"}
        body={realAuth ? "Real writes will be blocked immediately." : "This is the second key. It has no effect unless GOOGLE_ADS_WRITES_ENABLED=true and the account is allow-listed. A written reason is recorded."}
        onCancel={() => setAuthOpen(false)} onConfirm={() => { authorize.mutate({ key: "real_writes_authorized", value: !realAuth, reason: authReason }); setAuthOpen(false); }}>
        {!realAuth && <Textarea rows={3} placeholder="Reason (20+ characters)" value={authReason} onChange={(e) => setAuthReason(e.target.value)} />}
      </ConfirmDialog>
    </div>
  );
}
