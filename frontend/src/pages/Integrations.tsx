import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { useAuth } from "../lib/auth";
import { useAction } from "../lib/hooks";
import { Badge, Button, Card, dt, ErrorBox, KV, Notice, PageHeader, Spinner, StatusBadge, Table } from "../components/ui";

export default function Integrations() {
  const { can } = useAuth();
  const q = useQuery({ queryKey: ["integrations"], queryFn: () => api.get("/integrations") });
  const test = useAction((name) => api.post(`/integrations/${name}/test`), [["integrations"]]);
  const oauth = useAction(() => api.get("/ads-oauth/start"));
  if (q.isLoading || !q.data) return <Spinner />;
  const d = q.data;
  return (
    <div className="space-y-6">
      <PageHeader title="Integrations" subtitle="Each integration runs through a replaceable adapter. Mock integrations are never shown as plain “Connected”." />
      <Notice tone="violet">Real integrations are enabled only by explicit configuration (<code>*_MODE=real</code>), never because credentials exist. Real Google Ads writes additionally need <code>GOOGLE_ADS_WRITES_ENABLED=true</code> <b>and</b> a Super Admin authorization.</Notice>
      <Card title="Adapters">
        <Table rows={d.integrations.map((i: any) => ({ id: i.name, ...i }))} columns={[
          { key: "name", label: "Integration", render: (i) => <span className="font-medium">{i.name.replace(/_/g, " ")}</span> },
          { key: "adapter", label: "Adapter", render: (i) => <code className="text-xs">{i.adapter}</code> },
          { key: "mode", label: "Mode", render: (i) => <Badge tone={i.is_mock ? "violet" : "green"}>{i.mode}</Badge> },
          { key: "status", label: "Status", render: (i) => <StatusBadge value={i.status} /> },
          { key: "last_success_at", label: "Last successful test", render: (i) => dt(i.last_success_at) },
          { key: "last_error", label: "Last error", className: "max-w-xs text-xs text-rose-700" },
          { key: "a", label: "", render: (i) => can("integrations.manage") && <Button size="sm" onClick={() => test.mutate(i.name)}>Test</Button> },
        ]} />
        <ErrorBox error={test.error} />
        {test.data && <div className="mt-3"><Notice tone={test.data.status === "ERROR" ? "red" : "green"}>{test.data.name}: {test.data.status} {test.data.detail} {test.data.error}</Notice></div>}
      </Card>
      <div className="grid gap-6 lg:grid-cols-2">
        <Card title="Configuration (secret-free)">
          <KV items={Object.entries(d.config).filter(([k]) => k !== "modes").map(([k, v]) => [k.replace(/_/g, " "), String(v)] as [string, string])} />
        </Card>
        <Card title="Google Ads account authorization" actions={can("integrations.manage") && <Button onClick={() => oauth.mutate({}, { onSuccess: (r: any) => { window.location.href = r.authorization_url; } })}>Connect Google Ads (OAuth)</Button>}>
          <p className="mb-3 text-sm text-slate-600">Separate from app sign-in. Refresh tokens are encrypted at rest; only a fingerprint is shown. In mock mode this runs a simulated OAuth flow.</p>
          <Table rows={d.ads_connections} empty="No Google Ads OAuth connection." columns={[{ key: "status", label: "Status", render: (c) => <StatusBadge value={c.status} /> },
            { key: "mode", label: "Mode" }, { key: "fingerprint", label: "Token fingerprint", render: (c) => <code className="text-xs">{c.fingerprint || "—"}</code> }]} />
          <div className="mt-3"><KV items={[["Real writes (env)", d.real_writes.env_enabled ? "enabled" : "disabled"], ["Real writes (Super Admin authorization)", d.real_writes.authorized ? "authorized" : "not authorized"]]} /></div>
          <ErrorBox error={oauth.error} />
        </Card>
      </div>
    </div>
  );
}
