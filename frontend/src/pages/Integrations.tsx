import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import { useAuth } from "../lib/auth";
import { useAction } from "../lib/hooks";
import { Badge, Button, Card, ConfirmDialog, dt, ErrorBox, Field, Input, Notice, PageHeader, Spinner, Stat, Textarea } from "../components/ui";

const MODES: { value: string; label: string; text: string }[] = [
  { value: "mock", label: "Mock mode", text: "Everything is simulated. Nothing can reach Google Ads. This is the default." },
  { value: "read_only", label: "Real account — read-only", text: "Real Google Ads data is read. No campaign write is possible in this mode." },
  { value: "execution", label: "Validated execution", text: "Real reads plus approved, validated writes. Campaigns are created PAUSED; enabling needs a separate approval. Also needs both safety keys." },
];

const STATUS_LABEL: Record<string, string> = {
  NOT_CONNECTED: "Not Connected", CONFIGURATION_REQUIRED: "Configuration Required", CONNECTED: "Connected", CONNECTION_FAILED: "Connection Failed",
  AUTHORIZATION_EXPIRED: "Authorization Expired", SYNCHRONIZATION_REQUIRED: "Synchronization Required",
};
const STATUS_TONE: Record<string, string> = {
  NOT_CONNECTED: "slate", CONFIGURATION_REQUIRED: "amber", CONNECTED: "green", CONNECTION_FAILED: "red", AUTHORIZATION_EXPIRED: "red", SYNCHRONIZATION_REQUIRED: "blue",
};
export const IntegrationStatus = ({ value }: { value: string }) => <Badge tone={STATUS_TONE[value] ?? "slate"}>{STATUS_LABEL[value] ?? value}</Badge>;

function StepList({ steps }: { steps: any[] }) {
  if (!steps?.length) return null;
  return (
    <ol className="mt-3 space-y-1.5 text-sm" aria-label="Connection test steps">
      {steps.map((s, i) => (
        <li key={i} className="flex gap-2">
          <span className={`mt-0.5 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[11px] font-bold ${s.status === "PASSED" ? "bg-emerald-100 text-emerald-700" : s.status === "FAILED" ? "bg-rose-100 text-rose-700" : "bg-slate-100 text-slate-500"}`}>
            {s.status === "PASSED" ? "✓" : s.status === "FAILED" ? "✕" : "–"}</span>
          <div className="min-w-0">
            <div className="font-medium text-slate-800">{s.name} <span className="text-xs font-normal text-slate-400">{s.status}</span></div>
            {s.detail && <div className="break-words text-slate-600">{s.detail}</div>}
            {s.hint && <div className="break-words text-amber-800">Hint: {s.hint}</div>}
          </div>
        </li>
      ))}
    </ol>
  );
}

function IntegrationCard({ item, canManage, mode }: { item: any; canManage: boolean; mode: string }) {
  const inv = [["integrations"], ["integrations-mode"], ["integrations-health"], ["integration-banner"]];
  const [open, setOpen] = useState(false);
  const [settings, setSettings] = useState<Record<string, string>>({});
  const [secrets, setSecrets] = useState<Record<string, string>>({});
  const [confirm, setConfirm] = useState<"" | "disconnect" | "enable" | "disable">("");
  const save = useAction(() => api.put(`/integrations/${item.provider}`, { settings, secrets }), inv);
  const test = useAction(() => api.post(`/integrations/${item.provider}/test`), inv);
  const enable = useAction((enabled: boolean) => api.post(`/integrations/${item.provider}/enable`, { enabled }), inv);
  const disconnect = useAction(() => api.post(`/integrations/${item.provider}/disconnect`), inv);
  const oauth = useAction(() => api.post("/integrations/google/start", { providers: item.provider === "google_ads" ? ["google_ads", "ga4", "gbp"] : [item.provider] }));
  const err = save.error || test.error || enable.error || disconnect.error || oauth.error;
  const steps = test.data?.steps ?? item.last_test_steps;
  const dirty = Object.keys(settings).length > 0 || Object.values(secrets).some(Boolean);
  const hasFields = Object.keys(item.settings).length + Object.keys(item.secrets).length > 0;
  return (
    <Card title={<span className="flex flex-wrap items-center gap-2">{item.label}<IntegrationStatus value={item.status} />{item.enabled && <Badge tone="green">Enabled</Badge>}</span>}
      actions={<Button size="sm" variant="ghost" onClick={() => setOpen(!open)}>{open ? "Hide details" : "Details"}</Button>}>
      <p className="text-sm text-slate-600">{item.description}</p>
      <dl className="mt-3 grid grid-cols-1 gap-x-6 gap-y-1 text-sm sm:grid-cols-2">
        <div className="flex justify-between gap-2"><dt className="text-slate-500">Connected account</dt><dd className="font-medium">{item.connected_account || "—"}</dd></div>
        <div className="flex justify-between gap-2"><dt className="text-slate-500">Account / customer ID</dt><dd className="font-mono text-xs">{item.account_id || item.settings.customer_id?.value || item.settings.property_id?.value || "—"}</dd></div>
        <div className="flex justify-between gap-2"><dt className="text-slate-500">Last test</dt><dd>{dt(item.last_test_at)}</dd></div>
        <div className="flex justify-between gap-2"><dt className="text-slate-500">Last successful</dt><dd>{dt(item.last_success_at)}</dd></div>
        <div className="flex justify-between gap-2"><dt className="text-slate-500">Last sync</dt><dd>{dt(item.last_sync_at)}</dd></div>
        <div className="flex justify-between gap-2"><dt className="text-slate-500">Still required</dt><dd className="text-right">{item.missing.length ? item.missing.join(", ") : "nothing"}</dd></div>
      </dl>
      {item.last_error && <div className="mt-3"><Notice tone="red"><b>Last error:</b> {item.last_error}</Notice></div>}
      {item.scopes?.length > 0 && <div className="mt-2 flex flex-wrap gap-1">{item.scopes.map((s: string) => <Badge key={s} tone="slate">{s.split("/").pop()}</Badge>)}</div>}
      {item.provider === "google_ads" && mode === "mock" && item.status === "CONNECTED" && <div className="mt-3"><Notice tone="violet">Connected, but the app is still in <b>Mock mode</b>. Switch the operating mode above to use real data.</Notice></div>}

      {open && hasFields && (
        <div className="mt-4 space-y-3 border-t border-slate-100 pt-4">
          <div className="grid gap-3 md:grid-cols-2">
            {Object.entries(item.settings).map(([k, f]: any) => (
              <Field key={k} label={f.label}>
                <Input value={settings[k] ?? f.value ?? ""} disabled={!canManage} autoComplete="off" onChange={(e) => setSettings({ ...settings, [k]: e.target.value })} />
              </Field>
            ))}
            {Object.entries(item.secrets).map(([k, f]: any) => (
              <Field key={k} label={f.label} hint={f.saved ? `Saved (${f.source}) · fingerprint ${f.fingerprint}… — leave blank to keep` : "Write-only: never shown after saving"}>
                <Input type="password" autoComplete="new-password" disabled={!canManage} value={secrets[k] ?? ""}
                  placeholder={f.saved ? "•••••••• saved" : "Paste value"} onChange={(e) => setSecrets({ ...secrets, [k]: e.target.value })} />
              </Field>
            ))}
          </div>
          {item.docs && <a className="text-xs text-brand-700 underline" href={item.docs} target="_blank" rel="noreferrer">Official documentation</a>}
        </div>
      )}

      {canManage && (
        <div className="mt-4 flex flex-wrap items-center gap-2">
          {hasFields && <Button disabled={!dirty || save.isPending} onClick={() => save.mutate({}, { onSuccess: () => { setSecrets({}); setSettings({}); } })}>{save.isPending ? "Saving…" : "Save"}</Button>}
          <Button variant="primary" disabled={test.isPending} onClick={() => test.mutate({})}>{test.isPending ? "Testing…" : "Test Connection"}</Button>
          {item.oauth && <Button disabled={oauth.isPending} title="Opens Google's consent screen" onClick={() => oauth.mutate({}, { onSuccess: (r: any) => { window.location.href = r.authorization_url; } })}>Connect with Google</Button>}
          {item.last_test_ok && !item.enabled && <Button onClick={() => setConfirm("enable")}>Enable</Button>}
          {item.enabled && <Button onClick={() => setConfirm("disable")}>Disable</Button>}
          {(item.status !== "NOT_CONNECTED" || Object.values(item.secrets).some((s: any) => s.saved)) && <Button variant="ghost" onClick={() => setConfirm("disconnect")}>Disconnect</Button>}
        </div>
      )}
      <div className="mt-2"><ErrorBox error={err} /></div>
      <StepList steps={steps} />
      {test.data && <p className="mt-2 text-xs text-slate-500">Every step above was a real request to the provider; nothing is marked connected unless all steps pass.</p>}

      <ConfirmDialog open={confirm === "enable"} title={`Enable ${item.label}?`} confirmLabel="Enable"
        body="Enabling lets the app use this integration. It does not by itself allow any Google Ads write."
        onCancel={() => setConfirm("")} onConfirm={() => enable.mutate(true, { onSettled: () => setConfirm("") })} />
      <ConfirmDialog open={confirm === "disable"} title={`Disable ${item.label}?`} confirmLabel="Disable"
        body={item.provider === "google_ads" ? "The app returns to Mock mode." : "The app falls back to its simulated implementation for this integration."}
        onCancel={() => setConfirm("")} onConfirm={() => enable.mutate(false, { onSettled: () => setConfirm("") })} />
      <ConfirmDialog open={confirm === "disconnect"} title={`Disconnect ${item.label}?`} tone="danger" confirmLabel="Disconnect"
        body="Saved credentials and discovered data are deleted from this app. Settings such as IDs and model names are kept. You can reconnect later."
        onCancel={() => setConfirm("")} onConfirm={() => disconnect.mutate({}, { onSettled: () => setConfirm("") })} />
    </Card>
  );
}

export function ModePanel({ mode, canManage, googleOk }: { mode: any; canManage: boolean; googleOk: boolean }) {
  const [target, setTarget] = useState("");
  const [reason, setReason] = useState("");
  const change = useAction(() => api.put("/integrations/mode", { mode: target, reason }), [["integrations"], ["integrations-mode"], ["integrations-health"], ["integration-banner"]]);
  return (
    <Card title="Operating mode" actions={<Badge tone={mode.mode === "mock" ? "violet" : mode.mode === "read_only" ? "blue" : "amber"}>{mode.label}</Badge>}>
      <div className="grid gap-3 md:grid-cols-3">
        {MODES.map((m) => (
          <button key={m.value} disabled={!canManage || m.value === mode.mode} onClick={() => setTarget(m.value)} aria-pressed={m.value === mode.mode}
            className={`rounded-lg border p-3 text-left text-sm transition ${m.value === mode.mode ? "border-brand-600 bg-brand-50" : "border-slate-200 hover:bg-slate-50"} disabled:cursor-default`}>
            <div className="font-semibold text-slate-900">{m.label}{m.value === mode.mode && <span className="ml-2 text-xs font-normal text-brand-700">current</span>}</div>
            <div className="mt-1 text-slate-600">{m.text}</div>
          </button>
        ))}
      </div>
      <div className="mt-4 grid gap-2 text-sm sm:grid-cols-3">
        <div>Real reads: <b>{mode.real_reads ? "yes" : "no"}</b></div>
        <div>Safety key 1 (server environment): <b>{mode.write_keys.environment_enabled ? "on" : "off"}</b></div>
        <div>Safety key 2 (Super Admin authorization): <b>{mode.write_keys.super_admin_authorized ? "on" : "off"}</b></div>
      </div>
      <p className="mt-2 text-sm text-slate-600">Real campaign writes possible right now: <b className={mode.real_writes_possible ? "text-rose-700" : "text-emerald-700"}>{mode.real_writes_possible ? "YES" : "NO"}</b>. {mode.explanation}</p>
      {!googleOk && mode.mode === "mock" && <p className="mt-1 text-xs text-slate-500">Real modes unlock once Google Ads passes Test Connection and is enabled.</p>}
      <ConfirmDialog open={!!target} title={`Switch to “${MODES.find((m) => m.value === target)?.label}”?`} tone={target === "execution" ? "danger" : "primary"} confirmLabel="Switch mode"
        body={MODES.find((m) => m.value === target)?.text} onCancel={() => { setTarget(""); change.reset(); }}
        onConfirm={() => change.mutate({}, { onSuccess: () => setTarget("") })}>
        <Textarea rows={2} placeholder="Reason (saved in the activity log)" value={reason} onChange={(e) => setReason(e.target.value)} />
        <div className="mt-2"><ErrorBox error={change.error} /></div>
      </ConfirmDialog>
    </Card>
  );
}

export default function Integrations() {
  const { can } = useAuth();
  const [params] = useSearchParams();
  const q = useQuery({ queryKey: ["integrations"], queryFn: () => api.get("/integrations") });
  const testAll = useAction(() => api.post("/integrations/test-all"), [["integrations"], ["integrations-mode"], ["integration-banner"]]);
  if (q.isLoading || !q.data) return <Spinner />;
  const items: any[] = q.data.integrations;
  const groups = ["Google", "AI", "Website research"];
  const counts = items.reduce((a: any, i) => ({ ...a, [i.status]: (a[i.status] ?? 0) + 1 }), {});
  const canManage = can("integrations.manage");
  const googleOk = !!items.find((i) => i.provider === "google_ads")?.last_test_ok;
  return (
    <div className="space-y-6">
      <PageHeader title="API & Account Integrations" subtitle="Credentials are encrypted and write-only. A connection counts only after a real request succeeds."
        actions={canManage && <Button disabled={testAll.isPending} onClick={() => testAll.mutate({})}>{testAll.isPending ? "Testing all…" : "Test all connections"}</Button>} />
      {params.get("oauth") === "connected" && <Notice tone="green">Google authorization saved for: {params.get("providers")}. Now run Test Connection on each card.</Notice>}
      {params.get("oauth") === "failed" && <Notice tone="red">Google authorization failed ({params.get("reason")}). Nothing was saved.</Notice>}
      <ErrorBox error={testAll.error} />
      <div className="grid grid-cols-2 gap-3 md:grid-cols-6" aria-label="Integration health">
        {Object.keys(STATUS_LABEL).map((s) => <Stat key={s} label={STATUS_LABEL[s]} value={counts[s] ?? 0} />)}
      </div>
      <ModePanel mode={q.data.mode} canManage={canManage} googleOk={googleOk} />
      {canManage && <Notice tone="blue">Google OAuth redirect URI to register in your Google Cloud OAuth client: <code>{q.data.redirect_uri}</code></Notice>}
      {groups.map((g) => (
        <section key={g} className="space-y-4">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">{g}</h2>
          <div className="grid gap-4 xl:grid-cols-2">
            {items.filter((i) => i.group === g).map((i) => <IntegrationCard key={i.provider} item={i} canManage={canManage} mode={q.data.mode.mode} />)}
          </div>
        </section>
      ))}
    </div>
  );
}
