import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { useAction } from "../lib/hooks";
import { Badge, Button, Card, dt, ErrorBox, Input, Notice, Select, Spinner } from "./ui";

const STATUS_TONE: Record<string, string> = { ENABLED: "green", SUSPENDED: "amber", CLOSED: "red", CANCELED: "red", UNKNOWN: "slate" };
const KEYS = [["gads-accounts"], ["gads-selection"], ["gads-status"], ["accounts"]];

/** Searchable list of the Google Ads client accounts discovered below the selected manager; selecting stores the choice in the database. */
export default function AccountSelector({ canRefresh }: { canRefresh: boolean }) {
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const status_ = useQuery({ queryKey: ["gads-status"], queryFn: () => api.get("/google-ads/status") });
  const list = useQuery({ queryKey: ["gads-accounts", q, status], queryFn: () => api.get(`/google-ads/accounts?q=${encodeURIComponent(q)}&status=${status}`) });
  const refresh = useAction(() => api.post("/google-ads/accounts/refresh"), KEYS);
  const select = useAction((customer_id: string) => api.put("/google-ads/selection", { customer_id }), KEYS);
  const sel = status_.data?.selected;
  const ws = list.data ?? status_.data;
  const managers: any[] = ws?.manager_accounts ?? [];
  const noClients = !!ws?.manager_customer_id && ws.client_accounts === 0 && !!ws.accounts_discovered_at;
  const done = refresh.data;
  return (
    <Card title="Client accounts" actions={canRefresh && <Button variant="primary" onClick={() => refresh.mutate({})} disabled={refresh.isPending}>{refresh.isPending ? "Refreshing…" : "Refresh accounts"}</Button>}>
      <div className="space-y-4">
        <ErrorBox error={refresh.error || select.error || status_.error || list.error} />
        {ws && !ws.manager_customer_id && <Notice tone="amber">No manager (MCC) account is selected yet. Open the <Link className="underline" to="/integrations">Google Ads card on the Integrations page</Link>, connect Google Ads and choose your manager account.</Notice>}
        {ws?.manager_customer_id && (
          <div className="text-xs text-slate-500">Manager: <b>{ws.manager_name || ws.manager_customer_id}</b> ({ws.manager_customer_id}) · {ws.client_accounts} client account(s) ·{" "}
            {ws.accounts_discovered_at ? `last refreshed ${dt(ws.accounts_discovered_at)}` : "never refreshed"}{ws.discovery_status === "PARTIAL" && <Badge tone="amber"> partial: {ws.discovery_message}</Badge>}
            {ws.discovery_status === "FAILED" && <Badge tone="red"> last refresh failed</Badge>}
            {ws.manager_source === "integration_setting" && <span> · from the manager ID saved on the Google Ads integration</span>}</div>
        )}
        {ws?.discovery_status === "FAILED" && ws.discovery_message && <Notice tone="red">{ws.discovery_message}</Notice>}
        {done && (done.client_accounts_found > 0
          ? <Notice tone={done.errors?.length ? "amber" : "green"}>{done.client_accounts_found} client account(s) found{done.sub_managers_found ? ` and ${done.sub_managers_found} sub-manager account(s)` : ""}{done.errors?.length ? `; ${done.errors.length} branch(es) could not be read (${done.errors.map((e: any) => e.code).join(", ")})` : ""}. Your selection was kept.</Notice>
          : <Notice tone="amber">No client accounts were found. {done.no_clients_reason}</Notice>)}
        {!done && noClients && ws.discovery_status !== "FAILED" && <Notice tone="amber">{ws.discovery_message || "The last refresh found no client accounts below this manager."}</Notice>}
        {managers.length > 0 && (
          <div>
            <div className="mb-1 text-xs font-medium text-slate-600">Manager accounts</div>
            <ul className="divide-y divide-slate-100 rounded-lg border border-slate-200" aria-label="Google Ads manager accounts">
              {managers.map((m) => (
                <li key={m.customer_id} className="flex flex-wrap items-center justify-between gap-2 px-4 py-2 text-sm">
                  <div className="min-w-0"><span className="font-medium text-slate-900">{m.name || "(unnamed manager)"}</span>{" "}
                    <span className="font-mono text-xs text-slate-500">{m.customer_id_display}</span>
                    <span className="text-xs text-slate-500">{m.customer_id === ws?.manager_customer_id ? " · selected manager" : m.parent_customer_id ? ` · sub-manager under ${m.parent_customer_id}` : ""}</span></div>
                  <div className="flex items-center gap-2"><Badge tone="blue">Manager</Badge><Badge tone={STATUS_TONE[m.status] ?? "slate"}>{m.status}</Badge></div>
                </li>))}
            </ul>
            <div className="mt-1 text-xs text-slate-400">Manager accounts hold no campaigns and cannot be selected or linked to a client.</div>
          </div>
        )}
        {sel && (
          <div className="rounded-lg border border-brand-100 bg-brand-50 px-4 py-3 text-sm" aria-label="Selected account">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div><span className="text-xs font-medium text-brand-700">Working in</span><div className="font-semibold">{sel.name || "(unnamed)"} <span className="font-mono text-xs font-normal">{sel.customer_id_display ?? sel.customer_id}</span></div>
                {!sel.selectable && <div className="text-xs text-amber-800">{sel.unavailable_reason}</div>}</div>
              <div className="flex gap-2">{sel.client_id ? <Link to={`/clients/${sel.client_id}`}><Button>Open workspace</Button></Link>
                : <Link to={`/clients/new?customer_id=${sel.customer_id}`}><Button>Create a client, then link it below</Button></Link>}</div>
            </div>
          </div>
        )}
        <div className="flex flex-wrap gap-2">
          <div className="min-w-[14rem] flex-1"><Input aria-label="Search accounts" placeholder="Search by name or customer ID…" value={q} onChange={(e) => setQ(e.target.value)} /></div>
          <div className="w-44"><Select aria-label="Filter by status" value={status} onChange={(e) => setStatus(e.target.value)}
            options={[{ value: "", label: "All statuses" }, { value: "ENABLED", label: "Enabled" }, { value: "SUSPENDED", label: "Suspended" }, { value: "CLOSED", label: "Closed" }, { value: "CANCELED", label: "Canceled" }]} /></div>
        </div>
        {list.isLoading ? <Spinner /> : (
          <ul className="divide-y divide-slate-100 rounded-lg border border-slate-200" aria-label="Google Ads client accounts">
            {(list.data?.items ?? []).map((a: any) => (
              <li key={a.customer_id} className="flex flex-wrap items-center justify-between gap-2 px-4 py-2.5 text-sm">
                <div className="min-w-0">
                  <div className="font-medium text-slate-900">{a.name || "(unnamed account)"} {a.is_test_account && <Badge tone="violet">test account</Badge>}</div>
                  <div className="text-xs text-slate-500"><span className="font-mono">{a.customer_id_display}</span> · {a.currency_code} · {a.time_zone}{a.parent_customer_id && a.parent_customer_id !== ws?.manager_customer_id ? ` · under ${a.parent_customer_id}` : ""}</div>
                  {a.unavailable_reason && <div className="text-xs text-slate-500">{a.unavailable_reason}</div>}
                </div>
                <div className="flex items-center gap-2">
                  <Badge tone={STATUS_TONE[a.status] ?? "slate"}>{a.status}</Badge>
                  {a.discovery_state === "NOT_SEEN" && <Badge tone="amber">not seen in last refresh</Badge>}
                  {sel?.customer_id === a.customer_id ? <Badge tone="blue">selected</Badge>
                    : <Button size="sm" disabled={!a.selectable || select.isPending} onClick={() => select.mutate(a.customer_id)}>Select</Button>}
                </div>
              </li>
            ))}
            {(list.data?.items ?? []).length === 0 && <li className="px-4 py-6 text-center text-sm text-slate-500">{q || status ? "No accounts match this search." : !ws?.manager_customer_id ? "No client accounts yet. Choose a manager account and refresh accounts."
              : !ws.accounts_discovered_at ? "Accounts have not been read yet. Use Refresh accounts." : "No client accounts below this manager."}</li>}
          </ul>
        )}
        {list.data && <div className="text-xs text-slate-400">{list.data.total} client account(s) shown</div>}
      </div>
    </Card>
  );
}
