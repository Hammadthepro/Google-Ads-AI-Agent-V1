import { ReactNode, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import { useAuth } from "../lib/auth";
import { useAction } from "../lib/hooks";
import { Badge, Button, Card, ConfirmDialog, cx, dt, ErrorBox, Field, Input, Notice, PageHeader, Spinner, Stat, Textarea } from "../components/ui";

const MODES: { value: string; label: string; text: string }[] = [
  { value: "read_only", label: "Real account — read-only", text: "Real Google Ads data is read. No campaign write is possible in this mode. This is the default." },
  { value: "execution", label: "Validated execution", text: "Real reads plus approved, validated writes. Campaigns are created PAUSED; enabling needs a separate approval. Also needs both safety keys." },
];

const LABEL_TONE: Record<string, string> = {
  Connected: "green", "Connection failed": "red", "Not tested": "amber", "Not configured": "slate", Disabled: "slate", Configured: "blue",
};
const SUB_TONE: Record<string, string> = { PASSED: "green", FAILED: "red", SKIPPED: "slate", NOT_TESTED: "amber" };
const SUB_LABEL: Record<string, string> = { PASSED: "Passed", FAILED: "Failed", SKIPPED: "Skipped", NOT_TESTED: "Not tested" };
const NOT_CHAT = /whisper|orpheus|prompt-guard|safeguard|tts|guard|embed/i;

/** Two pills: is it configured, and is it connected. Connected is shown ONLY after a real successful request. */
export function StatusPills({ item }: { item: any }) {
  const d = item.display;
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5">
      <Badge tone={d.config === "Configured" ? "green" : "slate"} title="Are all required settings present (from .env or saved here)?">{d.config}</Badge>
      {d.testable && d.label !== "Not configured" && <Badge tone={LABEL_TONE[d.label]} title="Connection status from the last REAL request">{d.label}</Badge>}
      {!d.testable && <Badge tone="slate" title="No automatic test exists">Verified by signing in</Badge>}
    </span>
  );
}

function StepList({ steps }: { steps: any[] }) {
  if (!steps?.length) return null;
  return (
    <ol className="mt-3 space-y-1.5 text-sm" aria-label="Connection test steps">
      {steps.map((s, i) => (
        <li key={i} className="flex gap-2">
          <span className={cx("mt-0.5 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[11px] font-bold",
            s.status === "PASSED" ? "bg-emerald-100 text-emerald-700" : s.status === "FAILED" ? "bg-rose-100 text-rose-700" : "bg-slate-100 text-slate-500")}>
            {s.status === "PASSED" ? "✓" : s.status === "FAILED" ? "✕" : "–"}</span>
          <div className="min-w-0">
            <div className="font-medium text-slate-800">{s.name} <span className="text-xs font-normal text-slate-400">{s.status}</span></div>
            {s.detail && <div className="break-words text-slate-600">{s.detail}</div>}
            {s.hint && <div className="break-words text-amber-800">What to do: {s.hint}</div>}
          </div>
        </li>
      ))}
    </ol>
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-1 border-b border-slate-100 py-2 text-sm"><dt className="text-slate-500">{label}</dt><dd className="max-w-[70%] text-right font-medium text-slate-800">{children}</dd></div>;
}

function SubtestRows({ item, run, canManage, busy }: { item: any; run: (target: string) => void; canManage: boolean; busy: boolean }) {
  if (!item.subtests?.length) return null;
  return (
    <div className="mt-3" aria-label="Individual checks">
      <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">Individual checks</div>
      <ul className="divide-y divide-slate-100 rounded-lg border border-slate-200">
        {item.subtests.map((t: any) => {
          const target = Object.keys(item.targets || {}).find((k) => k === t.key);
          return (
            <li key={t.key} className="flex flex-wrap items-start justify-between gap-2 px-3 py-2 text-sm">
              <div className="min-w-0 flex-1">
                <span className="font-medium text-slate-800">{t.label}</span> <Badge tone={SUB_TONE[t.status] ?? "slate"}>{SUB_LABEL[t.status] ?? t.status}</Badge>
                {t.detail && <div className="break-words text-slate-600">{t.detail}</div>}
                {t.hint && <div className="break-words text-amber-800">What to do: {t.hint}</div>}
                <div className="text-xs text-slate-400">{t.last_success_at ? `Last success ${dt(t.last_success_at)}` : "Never succeeded"}{t.at ? ` · last run ${dt(t.at)}` : ""}</div>
              </div>
              {target && canManage && <Button size="sm" disabled={busy} onClick={() => run(target)}>{item.targets[target]}</Button>}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function ProviderFacts({ item }: { item: any }) {
  const i = item.info || {};
  if (item.provider === "google_ads") {
    const a = i.api_access || {};
    return (
      <dl className="mt-3">
        <Row label="Configuration">{item.missing.length ? <span className="text-rose-700">Missing: {item.missing.join(", ")}</span> : "OAuth client ID, secret and refresh token present"}</Row>
        <Row label="Developer token"><span className="text-slate-600">Not required — Google sunset developer tokens on 2026-09-09</span></Row>
        <Row label="Google Cloud project">{i.cloud_project_number ? <>#{i.cloud_project_number} · <a className="text-brand-700 underline" href={i.console_url} target="_blank" rel="noreferrer">API overview</a></> : "—"}</Row>
        <Row label="Google Ads API access">{a.api_enabled === true ? "Enabled for the project" : a.api_enabled === false ? <span className="text-rose-700">Not enabled</span> : "Not tested"} · level: <b>{a.level === "production" ? "Production (Explorer/Basic/Standard)" : a.level === "test" ? "Test only" : a.level === "test-or-higher" ? "Test account read (production not proven)" : a.level ?? "unknown"}</b></Row>
        <Row label="Manager account (MCC)">{i.manager_id_set ? "Chosen — see “Choose manager account” below" : <span className="text-amber-800">Not chosen yet</span>}</Row>
        <Row label="Enabled managers found">{(i.managers ?? []).filter((m: any) => m.status === "ENABLED").length ? (i.managers as any[]).filter((m: any) => m.status === "ENABLED").map((m: any) => `${m.name || m.customer_id} (${m.customer_id})`).join(", ") : <span className="text-amber-800">none found in the last test</span>}</Row>
        <Row label="Accounts this Google user can reach directly">{(item.discovered?.accessible_customers ?? []).length ? (item.discovered.accessible_customers as string[]).join(", ") : "—"}</Row>
        <Row label="Last successful connection test">{item.last_success_at ? dt(item.last_success_at) : "Never"}</Row>
        {a.basis && <p className="mt-2 text-xs text-slate-500">{a.basis}</p>}
      </dl>
    );
  }
  if (item.provider === "groq") {
    return (
      <dl className="mt-3">
        <Row label="API key">{item.secrets.api_key?.saved ? <>Configured <span className="font-normal text-slate-500">(••••••••, {item.secrets.api_key.source})</span></> : <span className="text-rose-700">Not configured</span>}</Row>
        <Row label="Primary model">{i.primary_model || <span className="text-rose-700">not set</span>}</Row>
        <Row label="Challenger model">{i.challenger_model || <span className="text-amber-800">not set — falls back to the primary model</span>}</Row>
        {i.rate_limits && Object.keys(i.rate_limits).length > 0 && <Row label="Groq plan limit (tokens / minute)">{Object.entries(i.rate_limits).map(([m, l]: any) => `${m}: ${l.tokens_per_minute?.toLocaleString()}`).join(" · ")}
          {Math.min(...Object.values(i.rate_limits).map((l: any) => l.tokens_per_minute || 1e9)) < 20000 && <div className="text-xs font-normal text-amber-800">Low limit: long campaign drafts can exceed it. The app then reports the rate limit and falls back to a single draft; a higher Groq tier removes this.</div>}</Row>}
        <Row label="Used by the application now">{i.active_in_app ? <Badge tone="green">Groq is active</Badge> : <Badge tone="slate">Not configured — AI features report this instead of running</Badge>}</Row>
        <Row label="Last successful connection test">{item.last_success_at ? dt(item.last_success_at) : "Never"}</Row>
      </dl>
    );
  }
  if (item.readonly) {
    return <dl className="mt-3">{Object.entries(i).map(([k, v]) => <Row key={k} label={k.replace(/_/g, " ")}>{typeof v === "boolean" ? (v ? "yes" : "no") : Array.isArray(v) ? (v.join(", ") || "—") : String(v ?? "—")}</Row>)}
      <Row label="Configuration">{item.missing.length ? <span className="text-rose-700">Missing: {item.missing.join(", ")}</span> : "Present"}</Row></dl>;
  }
  return (
    <dl className="mt-3">
      <Row label="Configuration">{item.missing.length ? <span className="text-rose-700">Missing: {item.missing.join(", ")}</span> : "All required settings present"}</Row>
      <Row label="Connected account">{item.connected_account || "—"}</Row>
      <Row label="Last successful connection test">{item.last_success_at ? dt(item.last_success_at) : "Never"}</Row>
    </dl>
  );
}

function ManagerPicker({ canManage }: { canManage: boolean }) {
  const status = useQuery({ queryKey: ["gads-status"], queryFn: () => api.get("/google-ads/status") });
  const [load, setLoad] = useState(false);
  const managers = useQuery({ queryKey: ["gads-managers"], enabled: load, retry: false, queryFn: () => api.get("/google-ads/managers") });
  const KEYS = [["gads-status"], ["gads-accounts"], ["gads-managers"], ["integrations"], ["gads-selection"]];
  const choose = useAction((customer_id: string) => api.put("/google-ads/manager", { customer_id }), KEYS);
  const refresh = useAction(() => api.post("/google-ads/accounts/refresh"), KEYS);
  const st = status.data;
  return (
    <div className="mt-4 space-y-3 border-t border-slate-100 pt-4" aria-label="Manager account">
      <div className="text-sm font-semibold text-slate-800">Manager account (MCC) and client accounts</div>
      <p className="text-xs text-slate-500">Connect with OAuth above, choose the manager account, then the app lists every client account below it. No customer IDs go in .env.</p>
      {st?.manager_customer_id && <div className="text-sm">Selected manager: <b>{st.manager_name || st.manager_customer_id}</b> <span className="font-mono text-xs">({st.manager_customer_id})</span> · {st.client_accounts} client account(s){st.accounts_discovered_at ? ` · refreshed ${dt(st.accounts_discovered_at)}` : " · not refreshed yet"}</div>}
      {canManage && <div className="flex flex-wrap gap-2">
        <Button size="sm" onClick={() => setLoad(true)} disabled={managers.isFetching}>{managers.isFetching ? "Loading…" : "Choose manager account"}</Button>
        {st?.manager_customer_id && <Button size="sm" variant="primary" onClick={() => refresh.mutate({})} disabled={refresh.isPending}>{refresh.isPending ? "Discovering…" : "Discover client accounts"}</Button>}
      </div>}
      <ErrorBox error={managers.error || choose.error || refresh.error} />
      {refresh.data && <Notice tone={refresh.data.errors?.length ? "amber" : "green"}>{refresh.data.found} account(s) found. <a className="underline" href="/accounts">Open the account selector</a>.</Notice>}
      {managers.data && (
        <ul className="divide-y divide-slate-100 rounded-lg border border-slate-200" aria-label="Accessible accounts">
          {managers.data.candidates.map((m: any) => (
            <li key={m.customer_id} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2 text-sm">
              <div><span className="font-medium">{m.descriptive_name || "(no name)"}</span> <span className="font-mono text-xs text-slate-500">{m.customer_id_display}</span>
                <div className="text-xs text-slate-500">{m.is_manager ? "Manager (MCC)" : m.is_manager === false ? "Client account" : "Unknown"} · {m.status}{m.test_account ? " · test account" : ""}{m.note ? ` · ${m.note}` : ""}</div></div>
              {m.selected ? <Badge tone="blue">selected</Badge> : <Button size="sm" disabled={!m.selectable || choose.isPending} onClick={() => choose.mutate(m.customer_id)}>Use as manager</Button>}
            </li>
          ))}
          {managers.data.candidates.length === 0 && <li className="px-3 py-3 text-sm text-slate-500">This Google user cannot reach any Google Ads account.</li>}
        </ul>
      )}
    </div>
  );
}

function IntegrationCard({ item, canManage, resultOf }: { item: any; canManage: boolean; resultOf?: any }) {
  const inv = [["integrations"], ["integrations-mode"], ["integrations-health"], ["integration-banner"]];
  const [open, setOpen] = useState(["google_ads", "groq", "pagespeed"].includes(item.provider));
  const [settings, setSettings] = useState<Record<string, string>>({});
  const [secrets, setSecrets] = useState<Record<string, string>>({});
  const [confirm, setConfirm] = useState<"" | "disconnect" | "enable" | "disable">("");
  const save = useAction(() => api.put(`/integrations/${item.provider}`, { settings, secrets }), inv);
  const test = useAction((target: string) => api.post(`/integrations/${item.provider}/test?target=${target}`), inv);
  const enable = useAction((enabled: boolean) => api.post(`/integrations/${item.provider}/enable`, { enabled }), inv);
  const disconnect = useAction(() => api.post(`/integrations/${item.provider}/disconnect`), inv);
  const oauth = useAction(() => api.post("/integrations/google/start", { providers: item.provider === "google_ads" ? ["google_ads"] : [item.provider] }));
  const err = save.error || test.error || enable.error || disconnect.error || oauth.error;
  const steps = test.data?.steps ?? resultOf?.steps ?? item.last_test_steps;
  const dirty = Object.keys(settings).length > 0 || Object.values(secrets).some(Boolean);
  const hasFields = !item.readonly && Object.keys(item.settings).length + Object.keys(item.secrets).length > 0;
  const testable = item.display.testable;
  const chatModels = ((item.discovered?.models as string[]) ?? []).filter((m) => !NOT_CHAT.test(m));
  const d = item.display;
  return (
    <Card title={<span id={`card-${item.provider}`} className="flex flex-wrap items-center gap-2">{item.label}<StatusPills item={item} />{item.enabled && <Badge tone="green">Enabled in app</Badge>}</span>}
      actions={<Button size="sm" variant="ghost" onClick={() => setOpen(!open)}>{open ? "Hide details" : "Details"}</Button>}>
      <p className="text-sm text-slate-600">{item.description}</p>
      {item.last_error && d.label === "Connection failed" && <div className="mt-3"><Notice tone="red"><b>Last error:</b> {item.last_error}</Notice></div>}
      {open && <ProviderFacts item={item} />}
      {open && <SubtestRows item={item} busy={test.isPending} canManage={canManage} run={(t) => test.mutate(t)} />}
      {open && item.provider === "google_ads" && !item.missing.length && <ManagerPicker canManage={canManage} />}
      {open && hasFields && (
        <div className="mt-4 space-y-3 border-t border-slate-100 pt-4">
          <div className="grid gap-3 md:grid-cols-2">
            {Object.entries(item.settings).map(([k, f]: any) => (
              <Field key={k} label={f.label}>
                <Input list={item.provider === "groq" && (k === "model" || k === "challenger_model") ? "groq-models" : undefined} value={settings[k] ?? f.value ?? ""} disabled={!canManage} autoComplete="off" onChange={(e) => setSettings({ ...settings, [k]: e.target.value })} />
              </Field>
            ))}
            {Object.entries(item.secrets).map(([k, f]: any) => (
              <Field key={k} label={f.label} hint={f.saved ? `Configured (${f.source}) · ••••••••${f.fingerprint ? ` · fingerprint ${f.fingerprint}…` : ""} — leave blank to keep` : "Write-only: never shown after saving"}>
                <Input type="password" autoComplete="new-password" disabled={!canManage} value={secrets[k] ?? ""} placeholder={f.saved ? "•••••••• configured" : "Paste value"} onChange={(e) => setSecrets({ ...secrets, [k]: e.target.value })} />
              </Field>
            ))}
          </div>
          {item.provider === "groq" && <datalist id="groq-models">{chatModels.map((m) => <option key={m} value={m} />)}</datalist>}
          {item.provider === "groq" && chatModels.length > 0 && <p className="text-xs text-slate-500">Chat models your key can use: {chatModels.join(", ")}</p>}
          {item.docs && <a className="text-xs text-brand-700 underline" href={item.docs} target="_blank" rel="noreferrer">Official documentation</a>}
        </div>
      )}
      {canManage && (
        <div className="mt-4 flex flex-wrap items-center gap-2">
          {hasFields && <Button disabled={!dirty || save.isPending} onClick={() => save.mutate({}, { onSuccess: () => { setSecrets({}); setSettings({}); } })}>{save.isPending ? "Saving…" : "Save"}</Button>}
          {testable && <Button variant="primary" disabled={test.isPending} onClick={() => test.mutate("all")}>{test.isPending ? "Testing…" : "Test Connection"}</Button>}
          {item.oauth && <Button disabled={oauth.isPending} title="Opens Google's consent screen" onClick={() => oauth.mutate({}, { onSuccess: (r: any) => { window.location.href = r.authorization_url; } })}>Connect with Google</Button>}
          {!item.readonly && d.label === "Connected" && !item.enabled && <Button onClick={() => setConfirm("enable")}>Enable in app</Button>}
          {!item.readonly && item.enabled && <Button onClick={() => setConfirm("disable")}>Disable</Button>}
          {!item.readonly && (item.status !== "NOT_CONNECTED" || Object.values(item.secrets).some((s: any) => s.saved && s.source === "saved")) && <Button variant="ghost" onClick={() => setConfirm("disconnect")}>Disconnect</Button>}
        </div>
      )}
      <div className="mt-2"><ErrorBox error={err} /></div>
      {open && <StepList steps={steps} />}
      {open && item.last_test_at && <p className="mt-2 text-xs text-slate-400">Last test {dt(item.last_test_at)} — every step is a real request; nothing is marked Connected unless all required steps pass.</p>}
      <ConfirmDialog open={confirm === "enable"} title={`Enable ${item.label}?`} confirmLabel="Enable" body={item.provider === "groq" ? "The application will use Groq for AI drafting and critique. Google Ads is not touched." : "Enabling lets the app use this integration. It does not by itself allow any Google Ads write."}
        onCancel={() => setConfirm("")} onConfirm={() => enable.mutate(true, { onSettled: () => setConfirm("") })} />
      <ConfirmDialog open={confirm === "disable"} title={`Disable ${item.label}?`} confirmLabel="Disable" body={item.provider === "google_ads" ? "Google Ads features report “not configured” until you enable it again, and the operating mode returns to read-only." : "Features that need this integration report “not configured” until you enable it again. Nothing is simulated in its place."}
        onCancel={() => setConfirm("")} onConfirm={() => enable.mutate(false, { onSettled: () => setConfirm("") })} />
      <ConfirmDialog open={confirm === "disconnect"} title={`Disconnect ${item.label}?`} tone="danger" confirmLabel="Disconnect" body="Credentials saved on this page and discovered data are deleted. Values from .env are not changed."
        onCancel={() => setConfirm("")} onConfirm={() => disconnect.mutate({}, { onSettled: () => setConfirm("") })} />
    </Card>
  );
}

export function ModePanel({ mode, canManage, googleOk }: { mode: any; canManage: boolean; googleOk: boolean }) {
  const [target, setTarget] = useState("");
  const [reason, setReason] = useState("");
  const change = useAction(() => api.put("/integrations/mode", { mode: target, reason }), [["integrations"], ["integrations-mode"], ["integrations-health"], ["integration-banner"]]);
  return (
    <Card title="Operating mode" actions={<Badge tone={mode.mode === "read_only" ? "blue" : "amber"}>{mode.label}</Badge>}>
      <div className="grid gap-3 md:grid-cols-2">
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
      {!googleOk && <p className="mt-1 text-xs text-slate-500">Validated execution unlocks once Google Ads passes Test Connection (client account read) and is enabled.</p>}
      <ConfirmDialog open={!!target} title={`Switch to “${MODES.find((m) => m.value === target)?.label}”?`} tone={target === "execution" ? "danger" : "primary"} confirmLabel="Switch mode"
        body={MODES.find((m) => m.value === target)?.text} onCancel={() => { setTarget(""); change.reset(); }}
        onConfirm={() => change.mutate({}, { onSuccess: () => setTarget("") })}>
        <Textarea rows={2} placeholder="Reason (saved in the activity log)" value={reason} onChange={(e) => setReason(e.target.value)} />
        <div className="mt-2"><ErrorBox error={change.error} /></div>
      </ConfirmDialog>
    </Card>
  );
}

function Overview({ items, canManage }: { items: any[]; canManage: boolean }) {
  const inv = [["integrations"], ["integrations-mode"], ["integrations-health"], ["integration-banner"]];
  const test = useAction((p: string) => api.post(`/integrations/${p}/test`), inv);
  const [busy, setBusy] = useState("");
  return (
    <Card title="Overview">
      <div className="-mx-5 overflow-x-auto">
        <table className="min-w-full divide-y divide-slate-200 text-sm" aria-label="Integration overview">
          <thead><tr>{["Integration", "Configuration", "Connection", "Last test", "Last success", ""].map((h) => <th key={h} className="whitespace-nowrap px-5 py-2 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">{h}</th>)}</tr></thead>
          <tbody className="divide-y divide-slate-100">
            {items.map((i) => (
              <tr key={i.provider} data-provider={i.provider}>
                <td className="px-5 py-2.5"><a href={`#card-${i.provider}`} className="font-medium text-slate-900 hover:underline">{i.label}</a><div className="text-xs text-slate-500">{i.group}</div></td>
                <td className="px-5 py-2.5"><Badge tone={i.display.config === "Configured" ? "green" : "slate"}>{i.display.config}</Badge></td>
                <td className="px-5 py-2.5">{i.display.testable ? <Badge tone={LABEL_TONE[i.display.label]}>{i.display.label}</Badge> : <span className="text-xs text-slate-500">Verified by signing in</span>}</td>
                <td className="px-5 py-2.5 text-xs text-slate-600">{dt(i.last_test_at)}</td>
                <td className="px-5 py-2.5 text-xs text-slate-600">{i.last_success_at ? dt(i.last_success_at) : "—"}</td>
                <td className="px-5 py-2.5 text-right">{canManage && i.display.testable && <Button size="sm" disabled={test.isPending && busy === i.provider}
                  onClick={() => { setBusy(i.provider); test.mutate(i.provider); }}>{test.isPending && busy === i.provider ? "Testing…" : "Test Connection"}</Button>}</td>
              </tr>))}
          </tbody>
        </table>
      </div>
      <div className="mt-2"><ErrorBox error={test.error} /></div>
      {test.data && <p className="mt-2 text-sm"><b>{test.data.integration.label}:</b> {test.data.connected ? "Connected — real request succeeded." : `Not connected — ${(test.data.steps.find((s: any) => s.status === "FAILED")?.detail) ?? "see details below"}`}</p>}
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
  const counts = items.reduce((a: any, i) => ({ ...a, [i.display.label]: (a[i.display.label] ?? 0) + 1 }), {});
  const canManage = can("integrations.manage");
  const googleOk = !!items.find((i) => i.provider === "google_ads")?.last_test_ok;
  const results: Record<string, any> = Object.fromEntries((testAll.data?.results ?? []).map((r: any) => [r.provider, r]));
  const order = ["google_ads", "groq", "pagespeed"];
  const first = order.map((p) => items.find((i) => i.provider === p)).filter(Boolean);
  const rest = items.filter((i) => !order.includes(i.provider));
  return (
    <div className="space-y-6">
      <PageHeader title="API & Account Integrations" subtitle="Credentials come from .env or are saved here (encrypted, write-only). Connected appears only after a real request succeeds."
        actions={canManage && <Button disabled={testAll.isPending} onClick={() => testAll.mutate({})}>{testAll.isPending ? "Testing all…" : "Test all connections"}</Button>} />
      {params.get("oauth") === "connected" && <Notice tone="green">Google authorization saved for: {params.get("providers")}. Now run Test Connection.</Notice>}
      {params.get("oauth") === "failed" && <Notice tone="red">Google authorization failed ({params.get("reason")}). Nothing was saved.</Notice>}
      <ErrorBox error={testAll.error} />
      <div className="grid grid-cols-2 gap-3 md:grid-cols-5" aria-label="Integration health">
        {["Connected", "Connection failed", "Not tested", "Not configured", "Disabled"].map((s) => <Stat key={s} label={s} value={counts[s] ?? 0} />)}
      </div>
      <Overview items={items} canManage={canManage} />
      <ModePanel mode={q.data.mode} canManage={canManage} googleOk={googleOk} />
      {canManage && (String(q.data.redirect_uri).includes("/api/v1/integrations/google/callback")
        ? <Notice tone="blue">Google OAuth redirect URI to register in your Google Cloud OAuth client: <code>{q.data.redirect_uri}</code></Notice>
        : <Notice tone="amber">GOOGLE_ADS_REDIRECT_URI points outside this app (<code>{q.data.redirect_uri}</code>, e.g. the OAuth Playground). A refresh token obtained there works, so you do not need “Connect with Google”. To use that button, register <code>&lt;app origin&gt;/api/v1/integrations/google/callback</code> and set it as the redirect URI.</Notice>)}
      <section className="space-y-4">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">Key integrations</h2>
        <div className="grid gap-4 xl:grid-cols-2">{first.map((i: any) => <IntegrationCard key={i.provider} item={i} canManage={canManage} resultOf={results[i.provider]} />)}</div>
      </section>
      {["Google", "AI", "Website research", "System"].map((g) => {
        const list = rest.filter((i) => i.group === g);
        if (!list.length) return null;
        return (
          <section key={g} className="space-y-4">
            <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">{g === "Google" ? "Other Google" : g === "AI" ? "Other AI" : g}</h2>
            <div className="grid gap-4 xl:grid-cols-2">{list.map((i) => <IntegrationCard key={i.provider} item={i} canManage={canManage} resultOf={results[i.provider]} />)}</div>
          </section>);
      })}
    </div>
  );
}
