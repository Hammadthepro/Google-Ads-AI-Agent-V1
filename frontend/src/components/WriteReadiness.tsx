import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { Badge, Card, cx, ErrorBox, Spinner } from "./ui";

const ICON: Record<string, { mark: string; cls: string; text: string }> = {
  met: { mark: "✓", cls: "bg-emerald-100 text-emerald-700", text: "Met" },
  missing: { mark: "•", cls: "bg-slate-100 text-slate-500", text: "Missing" },
  unverified: { mark: "?", cls: "bg-amber-100 text-amber-800", text: "Unverified" },
};

/** Every key a real Google Ads write needs, who controls it and what to do next. Read-only: it changes nothing. */
export function WriteReadiness({ accountId, clientId }: { accountId?: string; clientId?: string }) {
  const qs = accountId ? `?account_id=${accountId}` : clientId ? `?client_id=${clientId}` : "";
  const q = useQuery({ queryKey: ["write-readiness", accountId ?? clientId ?? ""], queryFn: () => api.get(`/google-ads/write-readiness${qs}`) });
  return (
    <Card title="Publishing to Google Ads" actions={Array.isArray(q.data?.checks) && <Badge tone={q.data.ready ? "amber" : "slate"}>{q.data.ready ? "Writes possible with approval" : "Writes off"}</Badge>}>
      <div id="writes" className="space-y-3">
        <ErrorBox error={q.error} />
        {q.isLoading ? <Spinner /> : Array.isArray(q.data?.checks) && (<>
          <p className="text-sm text-slate-700">{q.data.summary}</p>
          <ol className="divide-y divide-slate-100 rounded-lg border border-slate-200" aria-label="Write authorization keys">
            {q.data.checks.map((c: any) => (
              <li key={c.key} className="flex items-start gap-3 px-3 py-2.5">
                <span aria-label={ICON[c.status].text} className={cx("mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-2xs font-bold", ICON[c.status].cls)}>{ICON[c.status].mark}</span>
                <div className="min-w-0 flex-1 text-[13px]">
                  <div className="flex flex-wrap items-center justify-between gap-2"><span className="font-medium text-slate-900">{c.label}</span>{c.who && <span className="text-2xs text-slate-400">{c.who}</span>}</div>
                  <div className="text-slate-600">{c.detail}</div>
                  {c.action && <div className="mt-0.5 text-2xs text-slate-500">Next: {c.action}</div>}
                </div>
              </li>))}
          </ol>
          <div className="text-xs text-slate-500"><div className="font-medium text-slate-700">Even when every key is met</div>
            <ul className="mt-1 list-disc space-y-0.5 pl-4">{(q.data.per_change ?? []).map((x: string) => <li key={x}>{x}</li>)}</ul></div>
        </>)}
      </div>
    </Card>
  );
}
