import { ReactNode, useState } from "react";
import { WriteReadiness } from "../components/WriteReadiness";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import { useAuth } from "../lib/auth";
import { useAction } from "../lib/hooks";
import { checkAuthorizationUrl } from "../lib/oauth";
import { Badge, Button, Card, ConfirmDialog, cx, dt, ErrorBox, Field, Input, MetricStrip, Notice, PageHeader, Select, since, Spinner, Textarea } from "../components/ui";

const MODES: { value: string; label: string; text: string }[] = [
  { value: "read_only", label: "Real account — read-only", text: "Real Google Ads data is read. No campaign write is possible in this mode. This is the default." },
  { value: "execution", label: "Validated execution", text: "Real reads plus approved, validated writes. Campaigns are created PAUSED; enabling needs a separate approval. Also needs both safety keys." },
];

const LABEL_TONE: Record<string, string> = {
  Connected: "green", "Connection failed": "red", "Not tested": "blue", "Not configured": "slate", Disabled: "slate",
  Configured: "blue", "Blocked by dependency": "amber", "Requires approval": "amber", Testing: "violet",
  "Verified by signing in": "green", "Not verified — sign in with Google to verify": "slate",
};
const SOURCE_TONE: Record<string, string> = { saved: "green", environment: "blue", default: "slate", missing: "red" };
const SOURCE_LABEL: Record<string, string> = { saved: "saved here", environment: "from deployment (.env)", default: "default", missing: "missing" };
const SUB_TONE: Record<string, string> = { PASSED: "green", FAILED: "red", SKIPPED: "slate", NOT_TESTED: "amber" };
const SUB_LABEL: Record<string, string> = { PASSED: "Passed", FAILED: "Failed", SKIPPED: "Skipped", NOT_TESTED: "Not tested" };
const NOT_CHAT = /whisper|orpheus|prompt-guard|safeguard|tts|guard|embed/i;

/** Two pills: is it configured, and is it connected. Connected is shown ONLY after a real successful request. */
export function StatusPills({ item }: { item: any }) {
  const d = item.display;
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5">
      <Badge tone={d.config === "Not configured" ? "slate" : "green"} title="Are all required settings present (from .env or saved here)?">{d.config}</Badge>
      {d.label !== "Not configured" && <Badge tone={LABEL_TONE[d.label] ?? "slate"} title={d.testable ? "Connection status from the last REAL request" : "There is no automatic test: only a real sign-in proves it, and that is not a health check"}>{d.label}</Badge>}
      {item.deployment_managed && <Badge tone="violet" title="Set by whoever deploys the application; cannot be edited on this page">Deployment-managed</Badge>}
    </span>
  );
}

const yesNo = (v: boolean | null | undefined, yes: string, no: string) => (v === true ? yes : v === false ? no : "not determined");

/** What the last test really established. A test without credentials is never run, so it never appears here as a test. */
export function TestFacts({ item }: { item: any }) {
  const t = item.test;
  if (!item.display?.testable) return null;
  if (!t) return <p className="mt-2 text-xs text-slate-500">Never tested{item.missing?.length ? " — add the missing settings first; a test needs them" : ""}.</p>;
  return (
    <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-0.5 text-xs text-slate-600 sm:grid-cols-3" aria-label="Last test">
      <div><dt className="inline text-slate-400">Last test </dt><dd className="inline">{dt(t.at || item.last_test_at)}</dd></div>
      <div><dt className="inline text-slate-400">Last success </dt><dd className="inline">{item.last_success_at ? dt(item.last_success_at) : "never"}</dd></div>
      <div><dt className="inline text-slate-400">Credentials </dt><dd className="inline">{t.credentials_present ? "present" : "missing"}</dd></div>
      <div><dt className="inline text-slate-400">Provider contacted </dt><dd className="inline">{t.contacted ? "yes" : "no"}</dd></div>
      <div><dt className="inline text-slate-400">Authentication </dt><dd className="inline">{yesNo(t.authenticated, "succeeded", "failed")}</dd></div>
      <div><dt className="inline text-slate-400">Capability </dt><dd className="inline">{yesNo(t.capability_available, "available", "not available")}</dd></div>
      {t.failure_reason && <div className="col-span-full text-rose-700">Failure: {t.failure_reason}</div>}
    </dl>
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
      <div className="mb-1.5 text-xs font-medium text-slate-500">Individual checks</div>
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
  if (i.usage_30d) {
    const rows = Object.entries(i.usage_30d as Record<string, any>);
    return (
      <dl className="mt-3">
        {rows.map(([name, u]) => (
          <Row key={name} label={rows.length > 1 ? `Usage, 30 days (${name.replace("suggest_", "")})` : "Usage, 30 days"}>
            {u.requests_sent} request(s) sent · {u.served_from_cache} served from cache{u.failed ? ` · ${u.failed} failed` : ""}
            {u.cost_units ? ` · ${u.cost_units} credit(s)` : ""}
            {u.last_error && <div className="text-2xs text-amber-800">Last failure: {u.last_error.kind} — {u.last_error.message}</div>}
          </Row>))}
      </dl>);
  }
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
      {refresh.data && (refresh.data.client_accounts_found > 0
        ? <Notice tone={refresh.data.errors?.length ? "amber" : "green"}>{refresh.data.client_accounts_found} client account(s) found. <a className="underline" href="/accounts">Open the account selector</a>.</Notice>
        : <Notice tone="amber">No client accounts were found. {refresh.data.no_clients_reason}</Notice>)}
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

const DEPLOY_HELP: Record<string, string[]> = {
  google_login: ["Create an OAuth 2.0 Web client in Google Cloud (APIs & Services > Credentials) and add this app's origin under “Authorised JavaScript origins”.",
                 "Set GOOGLE_CLIENT_ID=<that client ID> and AUTH_PROVIDER=google in the deployment environment (.env), then restart the backend.",
                 "This is the sign-in client only. It is not the Google Ads OAuth client and needs no client secret.", "Verified by signing in: no connection test can prove it."],
  database: ["Set DATABASE_URL in the deployment environment (for Supabase use the Session Pooler URL with ?sslmode=require).",
             "The URL and password are never shown. Test Connection checks the database this process is really connected to."],
  redis: ["Set REDIS_URL in the deployment environment. Start the worker and beat services so scheduled and background jobs run.",
          "The URL is never shown. Test Connection checks the broker queue and whether a worker answers."],
};

function DependencyList({ item }: { item: any }) {
  if (!item.dependencies?.length) return null;
  return (
    <div className="mt-3" aria-label="Dependencies">
      <div className="mb-1.5 text-xs font-medium text-slate-500">Depends on</div>
      <ul className="divide-y divide-slate-100 rounded-lg border border-slate-200 text-sm">
        {item.dependencies.map((d: any) => (
          <li key={d.key} className="flex flex-wrap items-start justify-between gap-2 px-3 py-2">
            <div><span className={d.ok ? "text-emerald-700" : "text-rose-700"}>{d.ok ? "✓" : "✗"}</span> <span className="font-medium">{d.label}</span>
              <div className="text-xs text-slate-500">{d.detail}</div></div>
            {!d.ok && d.action && <a className="text-xs text-brand-700 underline" href={`#card-${d.action}`}>Go to {d.action.replace(/_/g, " ")}</a>}
          </li>))}
      </ul>
    </div>
  );
}

/** Live, read-only discovery of the choices a user must pick (GA4 properties, Claude models). */
function useOptions(provider: string, enabled: boolean) {
  return useQuery({ queryKey: ["integration-options", provider], enabled, retry: false, staleTime: 60_000, queryFn: () => api.get(`/integrations/${provider}/options`) });
}

function SmtpTestEmail() {
  const [to, setTo] = useState("");
  const send = useAction(() => api.post("/integrations/smtp/test-email", { to }), []);
  return (
    <div className="mt-3 flex flex-wrap items-end gap-2" aria-label="Send a test email">
      <div className="w-64"><Field label="Send a test email to" hint="Sends ONE real message; nothing is sent unless you press the button."><Input type="email" value={to} placeholder="you@example.com" onChange={(e) => setTo(e.target.value)} /></Field></div>
      <Button disabled={!to || send.isPending} onClick={() => send.mutate({})}>{send.isPending ? "Sending…" : "Send test email"}</Button>
      {send.data && (send.data.sent ? <span className="text-sm text-emerald-700">Sent to {send.data.to}</span> : <span className="text-sm text-rose-700">Not sent: {send.data.error}</span>)}
      <ErrorBox error={send.error} />
    </div>
  );
}

type QuickTest = { run: (provider: string) => void; busy: string | null };

/** One provider as an accordion item. The panel stays mounted when collapsed (hidden and inert), so a half-typed draft is kept
 *  and every control still exists for the page; it is simply out of the way until the provider is opened. */
function IntegrationCard({ item, canManage, resultOf, open, onToggle, quickTest }: { item: any; canManage: boolean; resultOf?: any; open: boolean; onToggle: () => void; quickTest?: QuickTest }) {
  const inv = [["integrations"], ["integrations-mode"], ["integrations-health"], ["integration-banner"]];
  const [settings, setSettings] = useState<Record<string, string>>({});
  const [secrets, setSecrets] = useState<Record<string, string>>({});
  const [confirm, setConfirm] = useState<"" | "disconnect" | "enable" | "disable">("");
  const [oauthProblem, setOauthProblem] = useState("");
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
  const pickable = ["ga4", "claude"].includes(item.provider) && !item.missing.filter((m: string) => m !== "property_id").length;
  const [discover, setDiscover] = useState(false);
  const opts = useOptions(item.provider, discover && pickable);
  const choices: Record<string, { value: string; label: string }[]> = {
    property_id: (opts.data?.properties ?? []).map((p: any) => ({ value: p.property, label: `${p.name || p.property} (${p.property}) — ${p.account}` })),
    model: item.provider === "claude" ? (opts.data?.models ?? item.info?.models ?? []).map((m: string) => ({ value: m, label: m })) : [],
    security: item.provider === "smtp" ? [{ value: "starttls", label: "STARTTLS (port 587)" }, { value: "ssl", label: "SSL/TLS (port 465)" }, { value: "none", label: "None (not recommended)" }] : [],
  };
  const unsaved = Object.entries(settings).some(([k, v]) => v !== (item.settings[k]?.value ?? "")) || Object.values(secrets).some(Boolean);
  const p = item.provider;
  const testing = quickTest?.busy === p;
  return (
    <section id={`card-${p}`} className="scroll-mt-20" aria-labelledby={`h-${p}`}>
      <div className={cx("flex items-center gap-3 px-4 py-3 transition-colors sm:px-5", open ? "bg-slate-50/70" : "hover:bg-slate-50/50")}>
        <button type="button" onClick={onToggle} aria-expanded={open} aria-controls={`panel-${p}`} className="flex min-w-0 flex-1 items-center gap-3 rounded text-left">
          <svg aria-hidden viewBox="0 0 16 16" className={cx("h-3.5 w-3.5 shrink-0 text-slate-400 transition-transform", open && "rotate-90")} fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="M6 3.5l4.5 4.5-4.5 4.5" /></svg>
          <span aria-hidden className={cx("h-2 w-2 shrink-0 rounded-full", DOT_TONE[LABEL_TONE[d.label] ?? "slate"] ?? "bg-slate-300")} />
          <span className="min-w-0">
            <span id={`h-${p}`} className="block truncate text-[13px] font-semibold text-slate-900">{item.label}</span>
            <span className="block truncate text-xs text-slate-500"><span className="md:hidden">{d.label}{open ? "" : " · "}</span>{!open && item.description}</span>
          </span>
        </button>
        <div className="hidden shrink-0 items-center gap-1.5 md:flex"><StatusPills item={item} />{item.enabled && <Badge tone="green">Enabled in app</Badge>}</div>
        <span className="hidden w-24 shrink-0 text-right text-2xs text-slate-400 lg:block" title={item.last_test_at ? dt(item.last_test_at) : undefined}>{d.testable ? (item.last_test_at ? `tested ${since(item.last_test_at)}` : "never tested") : "sign-in only"}</span>
        {quickTest && canManage && testable && !open && (
          <Button size="sm" disabled={testing} onClick={() => quickTest.run(p)}>{testing ? "Testing…" : "Test Connection"}</Button>)}
      </div>
      <div id={`panel-${p}`} role="region" aria-labelledby={`h-${p}`} className={cx("grid transition-[grid-template-rows] duration-200 ease-out", open ? "grid-rows-[1fr]" : "grid-rows-[0fr]")}
        {...(open ? {} : { inert: "" as any, "aria-hidden": true })}>
      <div className="min-h-0 overflow-hidden"><div className="border-t border-slate-100 px-4 pb-5 pt-3 sm:px-5 sm:pl-[52px]">
      <p className="text-[13px] leading-5 text-slate-600">{item.description}</p>
      <TestFacts item={item} />
      {item.last_error && d.label === "Connection failed" && <div className="mt-3"><Notice tone="red"><b>Last error:</b> {item.last_error}</Notice></div>}
      {item.credentials_unreadable && <div className="mt-3"><Notice tone="red"><b>Saved credentials cannot be decrypted.</b> TOKEN_ENCRYPTION_KEY has changed since they were saved, so they are treated as missing. Re-enter them (or restore the previous key).</Notice></div>}
      {item.deployment_managed && (
        <div className="mt-3"><Notice tone="blue"><b>Deployment-managed.</b> This is infrastructure configuration set by whoever deploys the app; it is not editable here and its values are never shown.
          <ul className="mt-1 list-disc pl-5">{(DEPLOY_HELP[item.provider] ?? []).map((t) => <li key={t}>{t}</li>)}</ul></Notice></div>)}
      {<DependencyList item={item} />}
      {item.provider === "ga4" && <div className="mt-3"><Notice tone="blue">Needs the <b>Google Analytics Admin API</b> and the <b>Analytics Data API</b> enabled in your Cloud project, the analytics.readonly scope (use “Connect with Google” here) and Viewer access to the property. GA4 data is never mixed into Ads conversion counts.</Notice></div>}
      {item.provider === "places" && <div className="mt-3"><Notice tone="blue">Enable <b>Places API (New)</b> and billing in the key's Google Cloud project, and restrict the key to that API.</Notice></div>}
      {item.provider === "claude" && item.info?.usage && <p className="mt-3 text-xs text-slate-500">Last {item.info.usage.window_days} days: {item.info.usage.runs} runs, {item.info.usage.tokens_in.toLocaleString()} tokens in / {item.info.usage.tokens_out.toLocaleString()} out. {item.info.usage.cost_note} {item.info.independent}</p>}
      {item.provider === "crawler" && <ul className="mt-3 list-disc pl-5 text-xs text-slate-500">{(item.info?.guarantees ?? []).map((g: string) => <li key={g}>{g}</li>)}</ul>}
      {<ProviderFacts item={item} />}
      {<SubtestRows item={item} busy={test.isPending} canManage={canManage} run={(t) => test.mutate(t)} />}
      {item.provider === "google_ads" && !item.missing.length && <ManagerPicker canManage={canManage} />}
      {hasFields && (
        <div className="mt-4 space-y-3 border-t border-slate-100 pt-4">
          <div className="grid gap-3 md:grid-cols-2">
            {Object.entries(item.settings).map(([k, f]: any) => {
              const val = settings[k] ?? f.value ?? "";
              const changed = settings[k] !== undefined && settings[k] !== (f.value ?? "");
              const options = choices[k] ?? [];
              return (
              <Field key={k} label={`${f.label}${f.required ? " *" : ""}`}
                hint={<span className="inline-flex flex-wrap items-center gap-1"><Badge tone={SOURCE_TONE[f.source] ?? "slate"}>{SOURCE_LABEL[f.source] ?? f.source}</Badge>
                  {changed && <Badge tone="amber">unsaved change</Badge>}{f.source === "missing" && f.required && <span className="text-rose-700">required</span>}</span>}>
                {options.length > 0 ? (
                  <Select aria-label={f.label} value={val} disabled={!canManage} onChange={(e) => setSettings({ ...settings, [k]: e.target.value })}
                    options={[{ value: "", label: "Choose…" }, ...(options.some((o) => o.value === val) || !val ? options : [{ value: val, label: `${val} (current)` }, ...options])]} />
                ) : (
                  <Input list={item.provider === "groq" && (k === "model" || k === "challenger_model") ? "groq-models" : undefined} value={val} disabled={!canManage} autoComplete="off" onChange={(e) => setSettings({ ...settings, [k]: e.target.value })} />
                )}
              </Field>); })}
            {Object.entries(item.secrets).map(([k, f]: any) => (
              <Field key={k} label={`${f.label}${f.required ? " *" : ""}`} hint={f.saved ? `${f.source === "environment" ? "Provided by the deployment (.env)" : "Saved here (encrypted)"} · ••••••••${f.fingerprint ? ` · fingerprint ${f.fingerprint}…` : ""} — leave blank to keep` : <span className="text-rose-700">{f.required ? "Missing — paste it, write-only: never shown after saving" : "Optional · write-only: never shown after saving"}</span>}>
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
          {hasFields && unsaved && <><Badge tone="amber">Unsaved changes — not saved on the server yet</Badge><Button variant="ghost" onClick={() => { setSecrets({}); setSettings({}); }}>Discard</Button></>}
          {hasFields && !unsaved && save.isSuccess && <span className="text-xs text-emerald-700">Saved. Run Test Connection to verify.</span>}
          {pickable && <Button disabled={opts.isFetching} onClick={() => setDiscover(true)}>{opts.isFetching ? "Discovering…" : item.provider === "claude" ? "Load models" : item.provider === "ga4" ? "Discover properties" : "Discover accounts & locations"}</Button>}
          {testable && <Button variant="primary" disabled={test.isPending} onClick={() => test.mutate("all")}>{test.isPending ? "Testing…" : "Test Connection"}</Button>}
          {item.oauth && <Button disabled={oauth.isPending} title="Opens Google's consent screen" onClick={() => { setOauthProblem(""); oauth.mutate({}, { onSuccess: (r: any) => { const c = checkAuthorizationUrl(r?.authorization_url); if (c.ok) window.location.assign(c.url); else setOauthProblem(c.reason); } }); }}>Connect with Google</Button>}
          {!item.readonly && d.label === "Connected" && !item.enabled && <Button onClick={() => setConfirm("enable")}>Enable in app</Button>}
          {!item.readonly && item.enabled && <Button onClick={() => setConfirm("disable")}>Disable</Button>}
          {!item.readonly && (item.status !== "NOT_CONNECTED" || Object.values(item.secrets).some((s: any) => s.saved && s.source === "saved")) && <Button variant="ghost" onClick={() => setConfirm("disconnect")}>Disconnect</Button>}
        </div>
      )}
      <div className="mt-2"><ErrorBox error={err || opts.error} /></div>
      {oauthProblem && <div className="mt-2"><Notice tone="red"><b>Google sign-in was not started.</b> {oauthProblem} Nothing was sent to Google.</Notice></div>}
      {opts.data && !opts.data.ok && opts.data.error && <div className="mt-2"><Notice tone="amber"><b>{opts.data.error.step}:</b> {opts.data.error.detail} {opts.data.error.hint}</Notice></div>}
      {item.provider === "smtp" && canManage && <SmtpTestEmail />}
      {<StepList steps={steps} />}
      {item.last_test_at && <p className="mt-2 text-xs text-slate-400">Last test {dt(item.last_test_at)} — every step is a real request; nothing is marked Connected unless all required steps pass.</p>}
      <ConfirmDialog open={confirm === "enable"} title={`Enable ${item.label}?`} confirmLabel="Enable" body={item.provider === "groq" ? "The application will use Groq for AI drafting and critique. Google Ads is not touched." : "Enabling lets the app use this integration. It does not by itself allow any Google Ads write."}
        onCancel={() => setConfirm("")} onConfirm={() => enable.mutate(true, { onSettled: () => setConfirm("") })} />
      <ConfirmDialog open={confirm === "disable"} title={`Disable ${item.label}?`} confirmLabel="Disable" body={item.provider === "google_ads" ? "Google Ads features report “not configured” until you enable it again, and the operating mode returns to read-only." : "Features that need this integration report “not configured” until you enable it again. Nothing is simulated in its place."}
        onCancel={() => setConfirm("")} onConfirm={() => enable.mutate(false, { onSettled: () => setConfirm("") })} />
      <ConfirmDialog open={confirm === "disconnect"} title={`Disconnect ${item.label}?`} tone="danger" confirmLabel="Disconnect" body="Credentials saved on this page and discovered data are deleted. Values from .env are not changed."
        onCancel={() => setConfirm("")} onConfirm={() => disconnect.mutate({}, { onSettled: () => setConfirm("") })} />
      </div></div>
      </div>
    </section>
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

const GROUPS: [string, string][] = [["Google", "Google"], ["AI", "Other AI"], ["Website research", "Website research"], ["System", "System"]];
const DOT_TONE: Record<string, string> = { green: "bg-emerald-500", red: "bg-rose-500", amber: "bg-amber-500", blue: "bg-brand-500", slate: "bg-slate-300", violet: "bg-plum-500" };

export default function Integrations() {
  const { can } = useAuth();
  const [params] = useSearchParams();
  const q = useQuery({ queryKey: ["integrations"], queryFn: () => api.get("/integrations") });
  const inv = [["integrations"], ["integrations-mode"], ["integrations-health"], ["integration-banner"]];
  const test = useAction((p: string) => api.post(`/integrations/${p}/test`), inv);
  const [busy, setBusy] = useState("");
  // Which providers are open. Starts with the one named in the address (#card-…, or the providers just authorised with Google).
  const [opened, setOpened] = useState<Set<string>>(() => new Set([
    ...(typeof window !== "undefined" && window.location.hash.startsWith("#card-") ? [window.location.hash.slice(6)] : []),
    ...((params.get("providers") ?? "").split(",").filter(Boolean))]));
  const toggle = (p: string) => setOpened((o) => { const n = new Set(o); n.has(p) ? n.delete(p) : n.add(p); return n; });
  const reveal = (p: string) => { setOpened((o) => new Set(o).add(p)); requestAnimationFrame(() => document.getElementById(`card-${p}`)?.scrollIntoView({ behavior: "smooth", block: "start" })); };
  const testAll = useAction(() => api.post("/integrations/test-all"), [["integrations"], ["integrations-mode"], ["integration-banner"]]);
  if (q.isLoading || !q.data) return <Spinner />;
  const items: any[] = q.data.integrations;
  const counts = items.reduce((a: any, i) => ({ ...a, [i.display.label]: (a[i.display.label] ?? 0) + 1 }), {});
  const canManage = can("integrations.manage");
  const googleOk = !!items.find((i) => i.provider === "google_ads")?.last_test_ok;
  const results: Record<string, any> = Object.fromEntries((testAll.data?.results ?? []).map((r: any) => [r.provider, r]));
  const needsAttention = (i: any) => ["Connection failed", "Blocked by dependency", "Requires approval"].includes(i.display.label);
  return (
    <div className="space-y-6">
      <PageHeader title="Integrations" subtitle="Credentials come from .env or are saved here (encrypted, write-only). Connected appears only after a real request succeeds."
        actions={canManage && <Button disabled={testAll.isPending} onClick={() => testAll.mutate({})}>{testAll.isPending ? "Testing all…" : "Test all connections"}</Button>} />
      {params.get("oauth") === "connected" && <Notice tone="green">Google authorization saved for: {params.get("providers")}. Now run Test Connection.</Notice>}
      {params.get("oauth") === "failed" && <Notice tone="red">Google authorization failed ({params.get("reason")}). Nothing was saved.</Notice>}
      <ErrorBox error={testAll.error} />
      <div aria-label="Integration health">
        <MetricStrip items={["Connected", "Connection failed", "Not tested", "Blocked by dependency", "Requires approval", "Not configured"].map((s) => ({ label: s,
          value: <span className={(counts[s] ?? 0) && s === "Connected" ? "text-emerald-700" : (counts[s] ?? 0) && s === "Connection failed" ? "text-rose-700" : ""}>{counts[s] ?? 0}</span> }))} />
      </div>
      {canManage && (String(q.data.redirect_uri).includes("/api/v1/integrations/google/callback")
        ? <Notice tone="blue">Google OAuth redirect URI sent to Google (source: {q.data.redirect_uri_source}) — it must be listed under “Authorised redirect URIs” of your OAuth client, character for character: <code className="break-all font-mono text-xs">{q.data.redirect_uri}</code></Notice>
        : <Notice tone="amber">GOOGLE_ADS_REDIRECT_URI points outside this app (<code className="break-all font-mono text-xs">{q.data.redirect_uri}</code>, e.g. the OAuth Playground). A refresh token obtained there works, so you do not need “Connect with Google”. To use that button, register <code>&lt;app origin&gt;/api/v1/integrations/google/callback</code> and set it as the redirect URI.</Notice>)}
      <div className="grid gap-8 xl:grid-cols-[208px_minmax(0,1fr)]">
        <nav aria-label="Providers" className="hidden xl:block">
          <div className="sticky top-[76px] space-y-5 text-[13px]">
            {GROUPS.map(([g, title]) => {
              const list = items.filter((i) => i.group === g);
              if (!list.length) return null;
              return (
                <div key={g}>
                  <div className="mb-1.5 px-2 text-xs font-medium text-slate-500">{title}</div>
                  <ul className="space-y-px">{list.map((i) => (
                    <li key={i.provider}><a href={`#card-${i.provider}`} onClick={(e) => { e.preventDefault(); reveal(i.provider); history.replaceState(null, "", `#card-${i.provider}`); }}
                      aria-current={opened.has(i.provider) ? "true" : undefined}
                      className={cx("flex items-center gap-2 rounded-md px-2 py-1 transition-colors", opened.has(i.provider) ? "bg-white font-medium text-slate-900 shadow-inset" : "text-slate-700 hover:bg-white/70 hover:text-slate-900")}>
                      <span aria-hidden className={cx("h-1.5 w-1.5 shrink-0 rounded-full", DOT_TONE[LABEL_TONE[i.display.label] ?? "slate"] ?? "bg-slate-300")} />
                      <span className="truncate">{i.label}</span></a></li>))}</ul>
                </div>);
            })}
          </div>
        </nav>
        <div className="min-w-0 space-y-7">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="text-xs text-slate-500">{items.length} providers · {items.filter(needsAttention).length} need attention · open one to see its settings, checks and actions.</p>
            <div className="flex gap-1">
              <Button size="sm" variant="ghost" onClick={() => setOpened(new Set(items.map((i) => i.provider)))}>Expand all</Button>
              <Button size="sm" variant="ghost" onClick={() => setOpened(new Set())}>Collapse all</Button>
            </div>
          </div>
          <ErrorBox error={test.error} />
          {test.data && <Notice tone={test.data.connected ? "green" : "amber"}><b>{test.data.integration?.label ?? test.data.provider ?? "Connection test"}:</b> {test.data.connected ? "Connected — real request succeeded." : `Not connected — ${(test.data.steps?.find((s: any) => s.status === "FAILED")?.detail) ?? "open it for the step-by-step result"}`}</Notice>}
          {GROUPS.map(([g, title]) => {
            const list = items.filter((i) => i.group === g);
            if (!list.length) return null;
            return (
              <section key={g} aria-label={title}>
                <div className="mb-2 flex items-baseline justify-between gap-3">
                  <h2 className="text-sm font-semibold text-slate-900">{title}</h2>
                  <span className="text-2xs text-slate-400">{list.filter((i) => i.display.label === "Connected").length} of {list.length} connected</span>
                </div>
                {g === "System" && <p className="mb-2 text-xs leading-5 text-slate-500">System integrations are managed by a Super Admin. Items marked <b>Deployment-managed</b> are set by whoever deploys the application (they hold infrastructure secrets) and are never editable here.</p>}
                <div className="divide-y divide-slate-200 overflow-hidden rounded-lg border border-slate-200 bg-white">
                  {list.map((i) => <IntegrationCard key={i.provider} item={i} canManage={canManage} resultOf={results[i.provider]} open={opened.has(i.provider)} onToggle={() => toggle(i.provider)}
                    quickTest={{ busy: test.isPending ? busy : null, run: (p) => { setBusy(p); test.mutate(p); } }} />)}
                </div>
              </section>);
          })}
        </div>
      </div>
      <ModePanel mode={q.data.mode} canManage={canManage} googleOk={googleOk} />
      <WriteReadiness />
    </div>
  );
}
