import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { useAuth } from "../lib/auth";
import { clientOptions, useAction, useClients } from "../lib/hooks";
import AccountSelector from "../components/AccountSelector";
import { Badge, Button, Card, DataSourceBadge, dt, ErrorBox, Notice, PageHeader, Select, Spinner, StatusBadge, Table } from "../components/ui";

export default function Accounts() {
  const { can } = useAuth();
  const accounts = useQuery({ queryKey: ["accounts"], queryFn: () => api.get("/ads-accounts") });
  const clients = useClients();
  const link = useAction(({ id, client_id }) => api.post(`/ads-accounts/${id}/link`, { client_id: client_id || null }), [["accounts"]]);
  const sync = useAction((id) => api.post(`/ads-accounts/${id}/sync`), [["accounts"]]);
  const [clientId, setClientId] = useState("");
  const structure = useQuery({ queryKey: ["structure", clientId], enabled: !!clientId, queryFn: () => api.get(`/clients/${clientId}/structure`) });
  const nameOf = (id: string) => clients.data?.find((c: any) => c.id === id)?.name;
  return (
    <div className="space-y-6">
      <PageHeader title="Google Ads accounts" subtitle="Client accounts discovered below your manager (MCC). Selecting or linking an account to a client never changes anything in Google Ads."
        />
      <Notice tone="blue">Accounts come from your connected Google Ads login (configure it on the <a className="underline" href="/integrations">Integrations page</a>). The app stays read-only until real writes are explicitly authorized.</Notice>
      <AccountSelector canRefresh={can("clients.edit")} />
      <ErrorBox error={link.error || sync.error} />
      <Card title="Link accounts to clients">
        {accounts.isLoading ? <Spinner /> : (
          <Table rows={accounts.data ?? []} columns={[
            { key: "customer_id", label: "Customer ID", render: (a) => <span className="font-mono text-xs">{a.customer_id_display ?? a.customer_id}</span> },
            { key: "status", label: "Status", render: (a) => <StatusBadge value={a.status} /> },
            { key: "descriptive_name", label: "Name", render: (a) => <>{a.descriptive_name} {a.is_manager && <Badge tone="blue">MCC</Badge>}</> },
            { key: "client_id", label: "Client", render: (a) => a.is_manager ? "—" : can("clients.edit") ? (
              <Select value={a.client_id ?? ""} onChange={(e) => link.mutate({ id: a.id, client_id: e.target.value })}
                options={[{ value: "", label: "— not linked —" }, ...clientOptions(clients.data)]} />) : nameOf(a.client_id) },
            { key: "auto_tagging_enabled", label: "Auto-tagging" },
            { key: "writes_allowed", label: "Write allowlist", render: (a) => a.writes_allowed ? <Badge tone="red">allowed</Badge> : <Badge>not allowed</Badge> },
            { key: "integration_mode", label: "Mode", render: (a) => <Badge tone="violet">{a.integration_mode}</Badge> },
            { key: "last_synced_at", label: "Last sync", render: (a) => dt(a.last_synced_at) },
            { key: "act", label: "", render: (a) => a.client_id && <Button size="sm" onClick={() => sync.mutate(a.id)}>Sync (read)</Button> },
          ]} />
        )}
      </Card>
      <Card title="Account structure" actions={<div className="w-64"><Select value={clientId} onChange={(e) => setClientId(e.target.value)} options={[{ value: "", label: "Choose a client…" }, ...clientOptions(clients.data)]} /></div>}>
        {!clientId ? <Notice>Choose a client to see campaigns → ad groups → ads and keywords.</Notice> : structure.isLoading ? <Spinner /> : (
          <div className="space-y-4">
            <div className="flex justify-end"><DataSourceBadge source={structure.data?.source} /></div>
            {(structure.data?.campaigns ?? []).map((c: any) => (
              <details key={c.id} className="rounded-lg border border-slate-200">
                <summary className="flex cursor-pointer flex-wrap items-center gap-2 px-4 py-2.5 text-sm">
                  <span className="font-medium">{c.name}</span><StatusBadge value={c.status} /><Badge>{c.campaign_type}</Badge>
                  {c.read_only && <Badge tone="amber">read-only in V1</Badge>}
                  <span className="text-xs text-slate-500">${c.daily_budget.toFixed(2)}/day · {c.bidding_strategy}</span>
                </summary>
                <div className="space-y-3 border-t border-slate-100 p-4">
                  {c.ad_groups.length === 0 && <div className="text-sm text-slate-500">No ad groups (or not readable for this campaign type).</div>}
                  {c.ad_groups.map((g: any) => (
                    <div key={g.id} className="rounded-md bg-slate-50 p-3">
                      <div className="mb-2 flex items-center gap-2 text-sm font-medium">{g.name} <StatusBadge value={g.status} /></div>
                      <div className="mb-2 flex flex-wrap gap-1">{g.keywords.map((k: any) => <Badge key={k.id} tone={k.match_type === "EXACT" ? "blue" : "slate"}>{k.match_type === "EXACT" ? `[${k.text}]` : `"${k.text}"`}</Badge>)}</div>
                      {g.ads.map((a: any) => (
                        <div key={a.id} className="mb-1 rounded border border-slate-200 bg-white p-2 text-xs">
                          <div className="flex items-center gap-2"><StatusBadge value={a.status} /><span className="text-slate-500">{a.final_url}</span></div>
                          <div className="mt-1 text-brand-700">{a.headlines.slice(0, 3).join(" | ")}</div>
                          <div className="text-slate-600">{a.descriptions[0]}</div>
                        </div>
                      ))}
                    </div>
                  ))}
                  {c.negatives.length > 0 && <div className="text-xs text-slate-500">Negatives: {c.negatives.map((n: any) => n.text).join(", ")}</div>}
                </div>
              </details>
            ))}
            <div>
              <div className="mb-1 text-sm font-medium">Conversion actions</div>
              <Table rows={structure.data?.conversion_actions ?? []} columns={[{ key: "name", label: "Name" }, { key: "type", label: "Type" },
                { key: "counting_type", label: "Counting" }, { key: "primary", label: "Primary" }, { key: "call_min_seconds", label: "Min call (s)" }]} />
            </div>
          </div>
        )}
      </Card>
    </div>
  );
}
