import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { useAction } from "../lib/hooks";
import { Badge, Button, cx, dt, ErrorBox, Field, Input, Notice, Select, since } from "./ui";

/** Main AI and Challenger AI: provider, model, write-only key, real connection test, model facts, save. The API key typed here is sent
 *  once to the server (to test or save) and dropped from memory afterwards; it is never stored in the browser and never shown again. */

const KIND_TEXT: Record<string, string> = {
  app_cap_exceeded: "Over an application token cap",
  auth_failure: "API key rejected", insufficient_credits: "Insufficient credits or quota", rate_limited: "Rate limited", rpm_exceeded: "Requests-per-minute limit",
  tpm_exceeded: "Tokens-per-minute limit", context_length: "Context window exceeded", output_truncated: "Output-token limit", timeout: "Timed out",
  network: "Network error", outage: "Provider unavailable", unsupported_model: "Unknown or unavailable model", unsupported_capability: "Unsupported capability",
  malformed_json: "Malformed output", schema_invalid: "Schema validation failed", permission_denied: "Permission denied", invalid_input: "Request rejected",
};
const STATUS: Record<string, [string, string]> = {
  CONNECTED: ["Connected", "green"], FAILED: ["Connection failed", "red"], UNTESTED: ["Configured, not tested", "blue"], NOT_CONFIGURED: ["Not configured", "slate"],
};
const CAP_LABEL: Record<string, string> = { tools: "Tool calling", structured_output: "Structured output", vision: "Image input", reasoning: "Reasoning controls", chat: "Chat" };
const ROLE_TEXT: Record<string, { title: string; short: string; text: string; accent: string; ring: string }> = {
  main: { title: "Main AI", short: "Does the agent's AI work", text: "Does the work: research synthesis, planning, campaign drafting, diagnosis and the agent's other AI tasks.",
    accent: "bg-brand-600", ring: "ring-brand-200" },
  challenger: { title: "Challenger AI", short: "Independently reviews important Main AI outputs", text: "Independently reviews important Main AI outputs with their evidence. A reviewer only: it never approves, overrides rules or acts in Google Ads.",
    accent: "bg-plum-500", ring: "ring-plum-200" },
};

type Form = { provider: string; model: string; api_key: string; enabled: boolean; pricing: { input: string; output: string }; review_policy: Record<string, boolean>;
  caps: Record<string, string> };
const CAP_ORDER = ["max_input_tokens", "max_output_tokens", "max_total_tokens", "task_budget_tokens", "client_daily_tokens", "client_monthly_tokens"];
const capsOf = (c: any): Record<string, string> => Object.fromEntries(CAP_ORDER.map((k) => [k, c?.[k] != null ? String(c[k]) : ""]));
const capsBody = (c: Record<string, string>) => Object.fromEntries(Object.entries(c).filter(([, v]) => v.trim() !== "").map(([k, v]) => [k, v.replace(/[,\s]/g, "")]));

const fmt = (n?: number | null) => (typeof n === "number" ? n.toLocaleString() : "—");

function ModelFacts({ m, hiddenReason }: { m: any; hiddenReason?: string }) {
  if (!m || !m.id) return <p className="text-xs text-slate-500">No model details yet. Load models from the provider, or enter a model id: it is checked with a real request when you test.</p>;
  const caps = Object.entries(m.capabilities ?? {});
  return (
    <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs sm:grid-cols-3" aria-label="Model details">
      <div><dt className="text-slate-400">Model</dt><dd className="break-all font-mono text-slate-800">{m.id}</dd></div>
      <div><dt className="text-slate-400">Context window</dt><dd className="tabular text-slate-800">{m.context_window ? `${fmt(m.context_window)} tokens` : "not stated"}</dd></div>
      <div><dt className="text-slate-400">Max output</dt><dd className="tabular text-slate-800">{m.max_output ? `${fmt(m.max_output)} tokens` : "not stated"}</dd></div>
      <div className="col-span-2 sm:col-span-3"><dt className="text-slate-400">Capabilities (as stated by the provider)</dt>
        <dd className="mt-0.5 flex flex-wrap gap-1">{caps.length ? caps.map(([k, v]) => <Badge key={k} tone={v ? "green" : "slate"}>{CAP_LABEL[k] ?? k}: {v ? "yes" : "no"}</Badge>)
          : <span className="text-slate-500">The provider's model list states none; nothing is assumed.</span>}</dd></div>
      {m.pricing && <div className="col-span-2 sm:col-span-3"><dt className="text-slate-400">Provider price</dt><dd className="tabular text-slate-800">${m.pricing.input} input · ${m.pricing.output} output per 1M tokens</dd></div>}
      {m.deprecated && <div className="col-span-full text-amber-800">The provider marks this model as deprecated or inactive.</div>}
      {hiddenReason && <div className="col-span-full text-amber-800">Not offered by default: {hiddenReason}.</div>}
    </dl>
  );
}

export const ROLE_DOT: Record<string, string> = { CONNECTED: "bg-emerald-500", FAILED: "bg-rose-500", UNTESTED: "bg-brand-500", NOT_CONFIGURED: "bg-slate-300" };
export const ROLE_LABEL: Record<string, string> = { CONNECTED: "Connected", FAILED: "Connection failed", UNTESTED: "Configured, not tested", NOT_CONFIGURED: "Not configured" };

/** One AI role as an integration row: the same collapsed header (status, last test, quick test) as every other provider; the settings
 *  open below it. */
export function AIRoleRow({ role, data, providers, canManage, other, open, onToggle }: { role: "main" | "challenger"; data: any; providers: any[];
  canManage: boolean; other: any; open: boolean; onToggle: () => void }) {
  const qc = useQueryClient();
  const t = ROLE_TEXT[role];
  const init = (): Form => ({ provider: data.provider || "", model: data.model || "", api_key: "", enabled: !!data.enabled,
    pricing: { input: data.pricing?.input?.toString() ?? "", output: data.pricing?.output?.toString() ?? "" }, review_policy: data.review_policy ?? {},
    caps: capsOf(data.caps) });
  const [f, setF] = useState<Form>(init);
  const [manual, setManual] = useState(false);
  useEffect(() => { setF(init()); }, [data.updated_at, data.provider, data.model, data.enabled]);  // eslint-disable-line react-hooks/exhaustive-deps
  const spec = providers.find((p) => p.key === f.provider);
  const sameProvider = f.provider === data.provider;
  const keyNote = !f.provider ? "" : !sameProvider ? `Enter a ${spec?.label ?? ""} API key (the saved key belongs to another provider).`
    : data.key.unreadable ? "The stored key cannot be decrypted (the server's encryption key changed). Enter it again."
    : data.key.source === "saved" ? `Saved · fingerprint ${data.key.fingerprint}. Leave blank to keep it.`
    : data.key.source === "groq_integration" ? "Using the existing Groq key (from .env). Enter a key here to use a separate one."
    : data.key.source === "claude_integration" ? "Using the existing Anthropic key (from .env). Enter a key here to use a separate one." : "No key saved.";
  const inv = [["ai-roles"], ["integrations"]];
  const discover = useAction((body: any) => api.post(`/ai/roles/${role}/models`, body), []);
  const test = useAction((body: any) => api.post(`/ai/roles/${role}/test`, body), inv);
  const save = useAction((body: any) => api.put(`/ai/roles/${role}`, body), inv);
  const revoke = useAction(() => api.post(`/ai/roles/${role}/revoke`, {}), inv);
  const models: any[] = discover.data?.models ?? [];
  const hidden: any[] = discover.data?.hidden ?? [];
  const selected = models.find((m) => m.id === f.model) ?? hidden.find((m) => m.id === f.model) ?? (f.model === data.model ? data.model_meta : null);
  const probe = () => ({ provider: f.provider, model: f.model, ...(f.api_key ? { api_key: f.api_key } : {}) });
  const dirty = f.provider !== (data.provider || "") || f.model !== (data.model || "") || !!f.api_key || f.enabled !== !!data.enabled
    || f.pricing.input !== (data.pricing?.input?.toString() ?? "") || f.pricing.output !== (data.pricing?.output?.toString() ?? "")
    || (role === "challenger" && JSON.stringify(f.review_policy) !== JSON.stringify(data.review_policy ?? {}))
    || JSON.stringify(capsBody(f.caps)) !== JSON.stringify(capsBody(capsOf(data.caps)));
  const [label, tone] = STATUS[data.status] ?? STATUS.NOT_CONFIGURED;
  const sameAsMain = role === "challenger" && other && f.provider && f.provider === other.provider;
  const lastTest = test.data ?? (data.last_test?.at ? { ok: data.last_test.ok, steps: data.last_test.steps, error: data.last_test.error ? { message: data.last_test.error, kind: data.last_test.error_kind } : null, recorded: true, at: data.last_test.at } : null);
  const doSave = () => save.mutate({
    provider: f.provider, model: f.model, enabled: f.enabled, ...(f.api_key ? { api_key: f.api_key } : {}),
    pricing: f.pricing.input !== "" && f.pricing.output !== "" ? { input: f.pricing.input, output: f.pricing.output } : {},
    ...(role === "challenger" ? { review_policy: f.review_policy } : {}),
    caps: capsBody(f.caps),
  }, { onSuccess: () => { setF((x) => ({ ...x, api_key: "" })); qc.invalidateQueries({ queryKey: ["ai-roles"] }); } });
  const id = `ai_${role}`;
  const configured = data.status !== "NOT_CONFIGURED";
  const nCaps = Object.keys(capsBody(capsOf(data.caps))).length;
  const summary = (data.provider ? `${data.provider_label} · ${data.model || "no model selected"}` : "Not configured") + ` · token limits: ${nCaps ? `${nCaps} set` : "none"}`;
  const canQuickTest = canManage && !!data.provider && data.key.present;

  return (
    <section id={`card-${id}`} className="scroll-mt-20" aria-labelledby={`h-${id}`}>
      <div className={cx("flex items-center gap-3 px-4 py-3 transition-colors sm:px-5", open ? "bg-slate-50/70" : "hover:bg-slate-50/50")}>
        <button type="button" onClick={onToggle} aria-expanded={open} aria-controls={`panel-${id}`} className="flex min-w-0 flex-1 items-center gap-3 rounded text-left">
          <svg aria-hidden viewBox="0 0 16 16" className={cx("h-3.5 w-3.5 shrink-0 text-slate-400 transition-transform", open && "rotate-90")} fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="M6 3.5l4.5 4.5-4.5 4.5" /></svg>
          <span aria-hidden className={cx("h-2 w-2 shrink-0 rounded-full", ROLE_DOT[data.status] ?? "bg-slate-300")} />
          <span className="min-w-0">
            <span id={`h-${id}`} className="block truncate text-[13px] font-semibold text-slate-900">{t.title}</span>
            <span className="block truncate text-xs text-slate-500"><span className="md:hidden">{label} · </span>{open ? t.short : summary}</span>
          </span>
        </button>
        <div className="hidden shrink-0 items-center gap-1.5 md:flex">
          <Badge tone={configured ? "green" : "slate"}>{configured ? "Configured" : "Not configured"}</Badge>
          {configured && <Badge tone={tone}>{label}</Badge>}
          {data.enabled && <Badge tone="green">Enabled in app</Badge>}
        </div>
        <span className="hidden w-24 shrink-0 text-right text-2xs text-slate-400 lg:block" title={data.last_test?.at ? dt(data.last_test.at) : undefined}>{data.last_test?.at ? `tested ${since(data.last_test.at)}` : "never tested"}</span>
        {canQuickTest && !open && <Button size="sm" disabled={test.isPending} onClick={() => test.mutate({})}>{test.isPending ? "Testing…" : "Test Connection"}</Button>}
      </div>
      <div id={`panel-${id}`} role="region" aria-label={`${t.title} settings`} className={cx("grid transition-[grid-template-rows] duration-200 ease-out", open ? "grid-rows-[1fr]" : "grid-rows-[0fr]")}
        {...(open ? {} : { inert: "" as any, "aria-hidden": true })}>
      <div className="min-h-0 overflow-hidden"><div className="border-t border-slate-100 px-4 pb-5 pt-3 sm:px-5 sm:pl-[52px]">
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div className="max-w-2xl">
          <p className="text-[13px] leading-5 text-slate-600">{t.text}</p>
          {role === "challenger" && !data.enabled && <p className="mt-1 text-xs text-slate-500">Challenger AI is off: drafts are checked by the Main AI and the deterministic validation only, and every result says that no independent review was performed.</p>}
        </div>
        <label className={cx("flex shrink-0 items-center gap-2 text-xs font-medium", canManage ? "cursor-pointer text-slate-700" : "text-slate-400")}>
          <input type="checkbox" role="switch" aria-checked={f.enabled} className="h-4 w-4 accent-brand-600" disabled={!canManage}
            checked={f.enabled} onChange={(e) => setF({ ...f, enabled: e.target.checked })} />{f.enabled ? "Enabled" : "Disabled"}
        </label>
      </div>
      <div className="max-w-3xl space-y-4">
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Provider">
            <Select value={f.provider} disabled={!canManage} onChange={(e) => { setF({ ...f, provider: e.target.value, model: "", api_key: "" }); discover.reset(); test.reset(); setManual(false); }}
              options={[{ value: "", label: "Choose a provider…" }, ...providers.map((p) => ({ value: p.key, label: p.label }))]} />
          </Field>
          <Field label="API key" hint={keyNote}>
            <Input type="password" autoComplete="new-password" spellCheck={false} value={f.api_key} disabled={!canManage || !f.provider}
              placeholder={spec ? `${spec.key_hint}` : "Choose a provider first"} onChange={(e) => setF({ ...f, api_key: e.target.value.trim() })} />
          </Field>
        </div>
        {spec && <p className="-mt-2 text-2xs text-slate-500">{spec.label} API features (provider documentation, not a per-model guarantee): {spec.api_features.join(", ")}. {spec.console && <a className="text-brand-700 hover:underline" href={spec.console} target="_blank" rel="noreferrer noopener">Get a key</a>}</p>}

        <div>
          <div className="flex flex-wrap items-end gap-2">
            <div className="min-w-0 flex-1">
              <Field label="Model">
                {manual || !models.length ? (
                  <Input value={f.model} disabled={!canManage || !f.provider} placeholder="Model id, exactly as the provider names it" spellCheck={false}
                    onChange={(e) => setF({ ...f, model: e.target.value.trim() })} />
                ) : (
                  <Select value={f.model} disabled={!canManage} onChange={(e) => setF({ ...f, model: e.target.value })}
                    options={[{ value: "", label: `Choose one of ${models.length} models…` }, ...models.map((m) => ({ value: m.id, label: `${m.id}${m.deprecated ? " (deprecated)" : ""}` })),
                      ...(f.model && !models.some((m) => m.id === f.model) ? [{ value: f.model, label: `${f.model} (entered manually)` }] : [])]} />
                )}
              </Field>
            </div>
            {canManage && <Button size="sm" disabled={!f.provider || discover.isPending || (!f.api_key && !(sameProvider && data.key.present))}
              onClick={() => discover.mutate({ ...probe(), refresh: true })}>{discover.isPending ? "Loading…" : models.length ? "Refresh models" : "Load models"}</Button>}
            {models.length > 0 && <Button size="sm" variant="ghost" onClick={() => setManual(!manual)}>{manual ? "Pick from list" : "Enter manually"}</Button>}
          </div>
          {discover.data?.error && <p className="mt-1.5 text-xs text-amber-800" role="status"><b>{KIND_TEXT[discover.data.error.kind] ?? "Model discovery unavailable"}:</b> {discover.data.error.message}. {discover.data.error.hint}</p>}
          {discover.data && !discover.data.error && <p className="mt-1.5 text-2xs text-slate-500">{models.length} model(s) from {spec?.label}{discover.data.cached ? " (cached)" : ""}, {since(discover.data.fetched_at)}{hidden.length ? ` · ${hidden.length} hidden (not chat models)` : ""}.</p>}
          <ErrorBox error={discover.error} />
        </div>

        <div className="rounded-md bg-slate-50 px-3 py-2.5"><ModelFacts m={selected?.id ? selected : f.model ? { id: f.model } : null} hiddenReason={selected?.hidden_reason} /></div>

        {role === "challenger" && (
          <fieldset className="space-y-1.5">
            <legend className="text-xs font-medium text-slate-700">What the Challenger reviews</legend>
            {Object.entries(data.reviews ?? {}).map(([k, r]: any) => (
              <label key={k} className="flex items-start gap-2 text-xs text-slate-700">
                <input type="checkbox" className="mt-0.5 h-3.5 w-3.5 accent-plum-500" disabled={!canManage} checked={!!f.review_policy[k]}
                  onChange={(e) => setF({ ...f, review_policy: { ...f.review_policy, [k]: e.target.checked } })} />
                <span><span className="font-medium text-slate-800">{r.label}</span> — {r.detail}</span>
              </label>))}
            <p className="text-2xs text-slate-500">Classification, labelling, formatting and calculations are never sent to the Challenger.</p>
          </fieldset>)}
        {sameAsMain && <Notice tone="amber">{f.model === other.model ? "Same provider and model as the Main AI: its reviews are not an independent second opinion." : "Same provider as the Main AI (different model): limited independence."}</Notice>}

        <TokenLimits role={role} title={t.title} data={data} caps={f.caps} canManage={canManage} onChange={(k, v) => setF({ ...f, caps: { ...f.caps, [k]: v } })} />

        <details className="text-xs">
          <summary className="cursor-pointer text-slate-500 hover:text-slate-800">Cost tracking (optional)</summary>
          <p className="mt-1.5 text-slate-500">Costs are shown only when known: the provider reports the charge (OpenRouter), or you enter your price here. Otherwise usage shows tokens without a cost.</p>
          <div className="mt-2 grid max-w-sm grid-cols-2 gap-2">
            <Field label="USD per 1M input tokens"><Input inputMode="decimal" value={f.pricing.input} disabled={!canManage} onChange={(e) => setF({ ...f, pricing: { ...f.pricing, input: e.target.value } })} /></Field>
            <Field label="USD per 1M output tokens"><Input inputMode="decimal" value={f.pricing.output} disabled={!canManage} onChange={(e) => setF({ ...f, pricing: { ...f.pricing, output: e.target.value } })} /></Field>
          </div>
        </details>

        {lastTest && (
          <div aria-label="Connection test" className={cx("rounded-md border px-3 py-2.5 text-xs", lastTest.ok ? "border-emerald-200 bg-emerald-50/60" : "border-rose-200 bg-rose-50/60")}>
            <div className="mb-1 font-medium text-slate-800">{lastTest.ok ? "Connection test passed" : `Connection test failed${lastTest.error?.kind ? ` · ${KIND_TEXT[lastTest.error.kind] ?? lastTest.error.kind}` : ""}`}
              <span className="ml-1 font-normal text-slate-500">{lastTest.recorded === false ? "(unsaved values; the saved status is unchanged)" : lastTest.at ? dt(lastTest.at) : ""}</span></div>
            <ul className="space-y-0.5">{(lastTest.steps ?? []).map((s: any) => (
              <li key={s.name} className="flex gap-2"><span className={s.ok ? "text-emerald-700" : "text-rose-700"}>{s.ok ? "✓" : "✕"}</span><span className="text-slate-700"><b>{s.name}:</b> {s.detail}</span></li>))}</ul>
          </div>)}
        <ErrorBox error={test.error} /><ErrorBox error={save.error} /><ErrorBox error={revoke.error} />

        {canManage && (
          <div className="flex flex-wrap items-center gap-2 border-t border-slate-100 pt-3">
            <Button variant="primary" disabled={!dirty || save.isPending || !f.provider} onClick={doSave}>{save.isPending ? "Saving…" : "Save configuration"}</Button>
            <Button disabled={test.isPending || !f.provider || (!f.api_key && !(sameProvider && data.key.present))} onClick={() => test.mutate(probe())}>{test.isPending ? "Testing…" : "Test connection"}</Button>
            {data.key.source === "saved" && <Button variant="ghost" disabled={revoke.isPending} onClick={() => { if (window.confirm(`Remove the saved ${t.title} API key? The role is turned off.`)) revoke.mutate({}); }}>Revoke key</Button>}
            {save.isSuccess && !dirty && <span className="text-xs text-emerald-700" role="status">Saved</span>}
            {dirty && <span className="text-xs text-amber-700">Unsaved changes</span>}
          </div>)}
      </div>
      </div></div></div>
    </section>
  );
}

/** Token limits of one role: the app's own caps (editable, enforced on the server before sending) next to the limits the provider
 *  enforces, and what each client has left today and this month. */
function TokenLimits({ role, title, data, caps, canManage, onChange }: { role: string; title: string; data: any; caps: Record<string, string>;
  canManage: boolean; onChange: (k: string, v: string) => void }) {
  const budget = useQuery({ queryKey: ["ai-budget", role], enabled: canManage, queryFn: () => api.get(`/ai/roles/${role}/budget`) });
  const saved = capsBody(capsOf(data.caps));
  const pl = data.provider_limits ?? {};
  return (
    <section aria-label={`${title} token limits`} className="rounded-md border border-slate-200">
      <header className="flex flex-wrap items-baseline justify-between gap-2 border-b border-slate-100 px-3 py-2">
        <h4 className="text-xs font-semibold text-slate-900">Token limits</h4>
        <span className="text-2xs text-slate-500">{Object.keys(saved).length ? `${Object.keys(saved).length} application limit(s) saved` : "No application limits saved"}</span>
      </header>
      <div className="grid gap-3 p-3 lg:grid-cols-2">
        <div>
          <div className="mb-1.5 text-2xs font-medium uppercase tracking-wide text-slate-500">Application limits · set here, enforced before sending</div>
          <div className="grid grid-cols-2 gap-2">
            {CAP_ORDER.map((k) => (
              <Field key={k} label={data.cap_fields?.[k] ?? k.replace(/_/g, " ")}
                hint={saved[k] ? `Saved: ${Number(saved[k]).toLocaleString()}` : "Saved: no limit"}>
                <Input inputMode="numeric" placeholder="no limit" value={caps[k] ?? ""} disabled={!canManage} onChange={(e) => onChange(k, e.target.value)} /></Field>))}
          </div>
          <p className="mt-1.5 text-2xs text-slate-500">A request over a limit is not sent; the task shows which limit stopped it. Save with “Save configuration”.</p>
        </div>
        <div className="space-y-3">
          <dl aria-label="Provider limits" className="grid grid-cols-2 gap-x-4 gap-y-1 rounded border border-slate-100 bg-slate-50/60 px-3 py-2 text-2xs">
            <div className="col-span-full font-medium uppercase tracking-wide text-slate-500">Provider limits · enforced by {data.provider_label || "the provider"}</div>
            <div><dt className="text-slate-400">Context window</dt><dd className="tabular text-slate-700">{pl.context_window ? `${fmt(pl.context_window)} tokens` : "not stated by the provider"}</dd></div>
            <div><dt className="text-slate-400">Maximum output</dt><dd className="tabular text-slate-700">{pl.max_output ? `${fmt(pl.max_output)} tokens` : "not stated by the provider"}</dd></div>
            <div className="col-span-full"><dt className="text-slate-400">Tokens / requests per minute</dt><dd className="text-slate-700">read from the provider's response headers when it sends them</dd></div>
          </dl>
          {canManage && (
            <div aria-label="Remaining budget">
              <div className="mb-1 text-2xs font-medium uppercase tracking-wide text-slate-500">Remaining client budget · {budget.data?.day ?? "today"}</div>
              {!saved.client_daily_tokens && !saved.client_monthly_tokens ? <p className="text-2xs text-slate-500">No daily or monthly client limit is set, so nothing is counted against a budget.</p>
                : !(budget.data?.clients ?? []).length ? <p className="text-2xs text-slate-500">No client has used this role since the limits were set.</p> : (
                <table className="w-full text-left text-2xs">
                  <thead className="text-slate-400"><tr><th className="py-0.5 pr-2 font-medium">Client</th><th className="py-0.5 pr-2 font-medium">Left today</th><th className="py-0.5 font-medium">Left this month</th></tr></thead>
                  <tbody>{budget.data.clients.map((c: any) => (
                    <tr key={c.client_id}><td className="py-0.5 pr-2 text-slate-700">{c.client}</td>
                      <td className={cx("tabular py-0.5 pr-2", c.left_today !== null && c.left_today <= 0 && "text-rose-700")}>{c.left_today === null ? "no limit" : fmt(Math.max(c.left_today, 0))}</td>
                      <td className={cx("tabular py-0.5", c.left_month !== null && c.left_month <= 0 && "text-rose-700")}>{c.left_month === null ? "no limit" : fmt(Math.max(c.left_month, 0))}</td></tr>))}</tbody>
                </table>)}
            </div>)}
        </div>
      </div>
    </section>
  );
}

export function AIUsageRow({ open, onToggle }: { open: boolean; onToggle: () => void }) {
  return (
    <section id="card-ai_usage" className="scroll-mt-20" aria-labelledby="h-ai_usage">
      <div className={cx("flex items-center gap-3 px-4 py-3 transition-colors sm:px-5", open ? "bg-slate-50/70" : "hover:bg-slate-50/50")}>
        <button type="button" onClick={onToggle} aria-expanded={open} aria-controls="panel-ai_usage" className="flex min-w-0 flex-1 items-center gap-3 rounded text-left">
          <svg aria-hidden viewBox="0 0 16 16" className={cx("h-3.5 w-3.5 shrink-0 text-slate-400 transition-transform", open && "rotate-90")} fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="M6 3.5l4.5 4.5-4.5 4.5" /></svg>
          <span aria-hidden className="h-2 w-2 shrink-0 rounded-full bg-slate-300" />
          <span className="min-w-0">
            <span id="h-ai_usage" className="block truncate text-[13px] font-semibold text-slate-900">AI usage</span>
            <span className="block truncate text-xs text-slate-500">Requests, tokens, latency, errors and known cost. Limits are set in Main AI / Challenger AI → Token limits.</span>
          </span>
        </button>
      </div>
      {open && <div id="panel-ai_usage" className="border-t border-slate-100"><Usage /></div>}
    </section>
  );
}

function Usage() {
  const [days, setDays] = useState(30);
  const q = useQuery({ queryKey: ["ai-usage", days], queryFn: () => api.get(`/ai/usage?days=${days}`) });
  const rows: any[] = q.data?.models ?? [];
  return (
    <section aria-label="AI usage table" className="bg-white">
      <header className="flex flex-wrap items-center justify-between gap-2 px-4 py-3 sm:px-5 sm:pl-[52px]">
        <p className="text-xs text-slate-500">Tokens and latency as reported by each provider. Cost only where it is known.</p>
        <Select className="w-auto" value={String(days)} onChange={(e) => setDays(Number(e.target.value))} options={[7, 30, 90].map((d) => ({ value: String(d), label: `Last ${d} days` }))} />
      </header>
      <ErrorBox error={q.error} />
      {q.data && rows.length > 0 && <UsageBreakdowns data={q.data} />}
      {q.data && !rows.length ? <p className="px-5 py-6 text-center text-[13px] text-slate-500">No AI requests in this period.</p> : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[760px] text-left text-xs">
            <thead className="text-slate-500"><tr className="border-b border-slate-100">{["Role", "Provider", "Model", "Requests", "Failed", "Input", "Output", "Total tokens", "Avg latency", "Cost", "Errors"].map((h) => <th key={h} className="px-3 py-2 font-medium first:pl-5">{h}</th>)}</tr></thead>
            <tbody className="divide-y divide-slate-100">{rows.map((r) => (
              <tr key={`${r.role}${r.provider}${r.model}`} className="text-slate-700">
                <td className="px-3 py-2 pl-5"><Badge tone={r.role === "challenger" ? "violet" : r.role === "main" ? "blue" : "slate"}>{r.role.replace("_", " ")}</Badge></td>
                <td className="px-3 py-2">{r.provider}</td><td className="max-w-[220px] truncate px-3 py-2 font-mono" title={r.model}>{r.model}</td>
                <td className="tabular px-3 py-2">{fmt(r.requests)}</td><td className={cx("tabular px-3 py-2", r.failed && "text-rose-700")}>{fmt(r.failed)}</td>
                <td className="tabular px-3 py-2">{fmt(r.input_tokens)}</td><td className="tabular px-3 py-2">{fmt(r.output_tokens)}</td><td className="tabular px-3 py-2 font-medium">{fmt(r.total_tokens)}</td>
                <td className="tabular px-3 py-2">{r.avg_latency_ms != null ? `${(r.avg_latency_ms / 1000).toFixed(1)} s` : "—"}</td>
                <td className="tabular px-3 py-2">{r.cost_usd != null ? `$${r.cost_usd.toFixed(4)}` : "unknown"}{r.cost_note && r.cost_usd != null ? <span className="block text-2xs text-slate-400">{r.cost_note}</span> : null}</td>
                <td className="px-3 py-2">{Object.entries(r.errors ?? {}).map(([k, n]: any) => <span key={k} className="mr-1 inline-block text-2xs text-rose-700">{KIND_TEXT[k] ?? k} ×{n}</span>)}</td>
              </tr>))}</tbody>
          </table>
        </div>)}
    </section>
  );
}

/** Usage by client, day and month (tokens as the providers reported them; no costs invented). */
function UsageBreakdowns({ data }: { data: any }) {
  const [view, setView] = useState<"clients" | "daily" | "monthly">("clients");
  const rows: any[] = data[view] ?? [];
  const label = (r: any) => view === "clients" ? (r.client || "—") : view === "daily" ? r.day : r.month;
  return (
    <div className="border-b border-slate-100 px-4 pb-3 sm:px-5 sm:pl-[52px]">
      <div role="tablist" aria-label="Usage breakdown" className="mb-2 flex gap-1">
        {([["clients", "By client"], ["daily", "Daily"], ["monthly", "Monthly"]] as const).map(([k, l]) => (
          <button key={k} role="tab" aria-selected={view === k} onClick={() => setView(k)}
            className={cx("rounded px-2 py-1 text-xs", view === k ? "bg-slate-900 text-white" : "text-slate-600 hover:bg-slate-100")}>{l}</button>))}
      </div>
      <table aria-label={`Usage ${view}`} className="w-full text-left text-xs">
        <thead className="text-slate-500"><tr>{[view === "clients" ? "Client" : view === "daily" ? "Day" : "Month", "Requests", "Failed", "Main AI tokens", "Challenger tokens", "Total tokens"].map((h) => <th key={h} className="py-1 pr-3 font-medium">{h}</th>)}</tr></thead>
        <tbody className="divide-y divide-slate-100">{rows.map((r, i) => (
          <tr key={i} className="text-slate-700">
            <td className="py-1 pr-3">{label(r)}</td><td className="tabular py-1 pr-3">{fmt(r.requests)}</td>
            <td className={cx("tabular py-1 pr-3", r.failed && "text-rose-700")}>{fmt(r.failed)}</td>
            <td className="tabular py-1 pr-3">{fmt((r.by_role?.main ?? 0) + (r.by_role?.second_drafter ?? 0))}</td>
            <td className="tabular py-1 pr-3">{fmt(r.by_role?.challenger ?? 0)}</td>
            <td className="tabular py-1 pr-3 font-medium">{fmt(r.input_tokens + r.output_tokens)}</td>
          </tr>))}</tbody>
      </table>
      {data.note && <p className="mt-1.5 text-2xs text-slate-400">{data.note}</p>}
    </div>
  );
}

export function useAIRoles() {
  return useQuery({ queryKey: ["ai-roles"], queryFn: () => api.get("/ai/roles") });
}

/** The rows of the "AI models" integrations group: Main AI, Challenger AI, AI usage. */
export function AIRoleRows({ data, canManage, opened, toggle }: { data: any; canManage: boolean; opened: Set<string>; toggle: (k: string) => void }) {
  const roles = data.roles;
  return (<>
    <AIRoleRow role="main" data={roles.main} providers={data.providers} canManage={canManage} other={roles.challenger} open={opened.has("ai_main")} onToggle={() => toggle("ai_main")} />
    <AIRoleRow role="challenger" data={roles.challenger} providers={data.providers} canManage={canManage} other={roles.main} open={opened.has("ai_challenger")} onToggle={() => toggle("ai_challenger")} />
    {canManage && <AIUsageRow open={opened.has("ai_usage")} onToggle={() => toggle("ai_usage")} />}
  </>);
}
