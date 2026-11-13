import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { useAction } from "../lib/hooks";
import { Badge, Button, cx, dt, ErrorBox, Field, Input, Notice, Select, since } from "./ui";

/** Main AI and Challenger AI: provider, model, write-only key, real connection test, model facts, save. The API key typed here is sent
 *  once to the server (to test or save) and dropped from memory afterwards; it is never stored in the browser and never shown again. */

const KIND_TEXT: Record<string, string> = {
  auth_failure: "API key rejected", insufficient_credits: "Insufficient credits or quota", rate_limited: "Rate limited", rpm_exceeded: "Requests-per-minute limit",
  tpm_exceeded: "Tokens-per-minute limit", context_length: "Context window exceeded", output_truncated: "Output-token limit", timeout: "Timed out",
  network: "Network error", outage: "Provider unavailable", unsupported_model: "Unknown or unavailable model", unsupported_capability: "Unsupported capability",
  malformed_json: "Malformed output", schema_invalid: "Schema validation failed", permission_denied: "Permission denied", invalid_input: "Request rejected",
};
const STATUS: Record<string, [string, string]> = {
  CONNECTED: ["Connected", "green"], FAILED: ["Connection failed", "red"], UNTESTED: ["Configured, not tested", "blue"], NOT_CONFIGURED: ["Not configured", "slate"],
};
const CAP_LABEL: Record<string, string> = { tools: "Tool calling", structured_output: "Structured output", vision: "Image input", reasoning: "Reasoning controls", chat: "Chat" };
const ROLE_TEXT: Record<string, { title: string; text: string; accent: string; ring: string }> = {
  main: { title: "Main AI", text: "Does the work: research synthesis, planning, campaign drafting, diagnosis and the agent's other AI tasks.",
    accent: "bg-brand-600", ring: "ring-brand-200" },
  challenger: { title: "Challenger AI", text: "Independently reviews important Main AI outputs with their evidence. A reviewer only: it never approves, overrides rules or acts in Google Ads.",
    accent: "bg-plum-500", ring: "ring-plum-200" },
};

type Form = { provider: string; model: string; api_key: string; enabled: boolean; pricing: { input: string; output: string }; review_policy: Record<string, boolean> };

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

function RoleCard({ role, data, providers, canManage, other }: { role: "main" | "challenger"; data: any; providers: any[]; canManage: boolean; other: any }) {
  const qc = useQueryClient();
  const t = ROLE_TEXT[role];
  const init = (): Form => ({ provider: data.provider || "", model: data.model || "", api_key: "", enabled: !!data.enabled,
    pricing: { input: data.pricing?.input?.toString() ?? "", output: data.pricing?.output?.toString() ?? "" }, review_policy: data.review_policy ?? {} });
  const [f, setF] = useState<Form>(init);
  const [manual, setManual] = useState(false);
  useEffect(() => { setF(init()); }, [data.updated_at, data.provider, data.model, data.enabled]);  // eslint-disable-line react-hooks/exhaustive-deps
  const spec = providers.find((p) => p.key === f.provider);
  const sameProvider = f.provider === data.provider;
  const keyNote = !f.provider ? "" : !sameProvider ? `Enter a ${spec?.label ?? ""} API key (the saved key belongs to another provider).`
    : data.key.unreadable ? "The stored key cannot be decrypted (the server's encryption key changed). Enter it again."
    : data.key.source === "saved" ? `Saved · fingerprint ${data.key.fingerprint}. Leave blank to keep it.`
    : data.key.source === "groq_integration" ? "Using the key of the existing Groq integration. Enter a key here to use a separate one." : "No key saved.";
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
    || (role === "challenger" && JSON.stringify(f.review_policy) !== JSON.stringify(data.review_policy ?? {}));
  const [label, tone] = STATUS[data.status] ?? STATUS.NOT_CONFIGURED;
  const sameAsMain = role === "challenger" && other && f.provider && f.provider === other.provider;
  const lastTest = test.data ?? (data.last_test?.at ? { ok: data.last_test.ok, steps: data.last_test.steps, error: data.last_test.error ? { message: data.last_test.error, kind: data.last_test.error_kind } : null, recorded: true, at: data.last_test.at } : null);
  const doSave = () => save.mutate({
    provider: f.provider, model: f.model, enabled: f.enabled, ...(f.api_key ? { api_key: f.api_key } : {}),
    pricing: f.pricing.input !== "" && f.pricing.output !== "" ? { input: f.pricing.input, output: f.pricing.output } : {},
    ...(role === "challenger" ? { review_policy: f.review_policy } : {}),
  }, { onSuccess: () => { setF((x) => ({ ...x, api_key: "" })); qc.invalidateQueries({ queryKey: ["ai-roles"] }); } });

  return (
    <section aria-label={t.title} className={cx("relative min-w-0 overflow-hidden rounded-lg border border-slate-200 bg-white")}>
      <span aria-hidden className={cx("absolute inset-y-0 left-0 w-1", t.accent)} />
      <header className="flex flex-wrap items-start justify-between gap-3 border-b border-slate-100 px-5 py-4 pl-6">
        <div className="min-w-0">
          <h3 className="flex items-center gap-2 text-sm font-semibold text-slate-900">{t.title}
            <Badge tone={tone} dot>{label}</Badge>
            {data.source === "migrated_from_groq" && <Badge tone="violet" title="Adopted from the existing Groq integration; nothing was re-entered or copied">from Groq integration</Badge>}
          </h3>
          <p className="mt-1 max-w-xl text-xs leading-5 text-slate-500">{t.text}</p>
        </div>
        <label className={cx("flex shrink-0 items-center gap-2 text-xs font-medium", canManage ? "cursor-pointer text-slate-700" : "text-slate-400")}>
          <input type="checkbox" role="switch" aria-checked={f.enabled} className="h-4 w-4 accent-brand-600" disabled={!canManage}
            checked={f.enabled} onChange={(e) => setF({ ...f, enabled: e.target.checked })} />{f.enabled ? "On" : "Off"}
        </label>
      </header>

      <div className="space-y-4 px-5 py-4 pl-6">
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
    </section>
  );
}

function Usage() {
  const [days, setDays] = useState(30);
  const q = useQuery({ queryKey: ["ai-usage", days], queryFn: () => api.get(`/ai/usage?days=${days}`) });
  const rows: any[] = q.data?.models ?? [];
  return (
    <section aria-label="AI usage" className="rounded-lg border border-slate-200 bg-white">
      <header className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-100 px-5 py-3">
        <div><h3 className="text-[13px] font-semibold text-slate-900">AI usage</h3>
          <p className="text-xs text-slate-500">Tokens and latency as reported by each provider. Cost only where it is known.</p></div>
        <Select className="w-auto" value={String(days)} onChange={(e) => setDays(Number(e.target.value))} options={[7, 30, 90].map((d) => ({ value: String(d), label: `Last ${d} days` }))} />
      </header>
      <ErrorBox error={q.error} />
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

export function AIRoles({ canManage }: { canManage: boolean }) {
  const q = useQuery({ queryKey: ["ai-roles"], queryFn: () => api.get("/ai/roles") });
  const roles = q.data?.roles;
  const providers = useMemo(() => q.data?.providers ?? [], [q.data]);
  return (
    <section aria-label="AI models" className="space-y-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-sm font-semibold text-slate-900">AI models</h2>
        <span className="text-2xs text-slate-400">Keys are encrypted on the server and never shown again after saving.</span>
      </div>
      <ErrorBox error={q.error} />
      {q.data?.independence?.note && <Notice tone="amber">{q.data.independence.note}</Notice>}
      {roles && (
        <div className="grid gap-4 xl:grid-cols-2">
          <RoleCard role="main" data={roles.main} providers={providers} canManage={canManage} other={roles.challenger} />
          <RoleCard role="challenger" data={roles.challenger} providers={providers} canManage={canManage} other={roles.main} />
        </div>)}
      {roles && !roles.challenger.enabled && <p className="text-xs text-slate-500">Challenger AI is off: drafts are checked by the Main AI and the deterministic validation only, and every result says that no independent review was performed.</p>}
      {canManage && <Usage />}
    </section>
  );
}
