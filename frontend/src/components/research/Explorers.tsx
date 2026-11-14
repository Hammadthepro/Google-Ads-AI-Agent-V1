import { useId, useMemo, useState, type ReactNode } from "react";
import { Badge, Button, cx, dt, Input, Select, useDialogKeys } from "../ui";
import { hostOf, providerLabel, type CompetitorNode, type SourceItem } from "./model";

function Sheet({ title, sub, onClose, children }: { title: string; sub: string; onClose: () => void; children: ReactNode }) {
  const id = useId();
  const ref = useDialogKeys(true, onClose);
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-ink/45 sm:items-center sm:p-4" role="dialog" aria-modal="true" aria-labelledby={id}
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div ref={ref} className="flex max-h-[88vh] w-full max-w-3xl animate-dialog-in flex-col rounded-t-xl bg-white shadow-pop sm:rounded-xl">
        <header className="flex items-start justify-between gap-3 border-b border-slate-100 px-5 py-4">
          <div><h3 id={id} className="text-base font-semibold text-slate-900">{title}</h3><p className="mt-0.5 text-xs text-slate-500">{sub}</p></div>
          <Button size="sm" variant="ghost" onClick={onClose}>Close</Button>
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">{children}</div>
      </div>
    </div>
  );
}

const uniq = (xs: string[]) => [...new Set(xs.filter(Boolean))].sort();

/** Every source this run used, with where it came from and when; filter by kind and provider, search by text. */
export function SourcesExplorer({ sources, onClose }: { sources: SourceItem[]; onClose: () => void }) {
  const [kind, setKind] = useState(""); const [prov, setProv] = useState(""); const [q, setQ] = useState("");
  const rows = useMemo(() => sources.filter((s) => (!kind || s.kind === kind) && (!prov || s.provider === prov)
    && (!q || `${s.label} ${s.url ?? ""} ${s.detail ?? ""}`.toLowerCase().includes(q.toLowerCase()))), [sources, kind, prov, q]);
  return (
    <Sheet title={`${sources.length} source${sources.length === 1 ? "" : "s"}`} sub="Every page, search and result this run actually read, as recorded. Cached results are marked." onClose={onClose}>
      <div className="mb-3 grid gap-2 sm:grid-cols-3">
        <Select aria-label="Filter by kind" value={kind} onChange={(e) => setKind(e.target.value)} options={[{ value: "", label: "All kinds" }, ...uniq(sources.map((s) => s.kind)).map((k) => ({ value: k, label: k }))]} />
        <Select aria-label="Filter by provider" value={prov} onChange={(e) => setProv(e.target.value)} options={[{ value: "", label: "All providers" }, ...uniq(sources.map((s) => s.provider)).map((p) => ({ value: p, label: providerLabel(p) }))]} />
        <Input aria-label="Search sources" placeholder="Search…" value={q} onChange={(e) => setQ(e.target.value)} />
      </div>
      <p className="mb-2 text-2xs text-slate-400">{rows.length} shown</p>
      <ol aria-label="Sources" className="divide-y divide-slate-100">{rows.map((s, i) => (
        <li key={s.key} className="flex gap-3 py-2 text-[13px]">
          <span className="tabular w-6 shrink-0 text-right text-2xs text-slate-400">{i + 1}</span>
          <div className="min-w-0 flex-1">
            {s.url ? <a className="break-words font-medium text-brand-700 hover:underline" href={s.url} target="_blank" rel="noreferrer noopener">{s.label}</a>
              : <span className="break-words font-medium text-slate-800">{s.label}</span>}
            <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-2xs text-slate-500">
              <Badge>{s.kind}</Badge><span>{providerLabel(s.provider)}</span>{s.url && <span>{hostOf(s.url)}</span>}
              {s.detail && <span>{s.detail}</span>}{s.cached ? <Badge tone="amber">cached</Badge> : null}<span>{dt(s.at)}</span>
            </div>
          </div>
        </li>))}</ol>
      {!rows.length && <p className="py-6 text-center text-[13px] text-slate-500">No source matches these filters.</p>}
    </Sheet>
  );
}

const ORIGIN: Record<string, [string, string]> = { client: ["Client-provided", "blue"], discovered: ["Discovered", "violet"] };

/** Competitors, one entry per business (deduplicated by website), with where each came from and whether its website was read. */
export function CompetitorsExplorer({ competitors, onClose }: { competitors: CompetitorNode[]; onClose: () => void }) {
  const [origin, setOrigin] = useState(""); const [onlyVerified, setOnlyVerified] = useState(false); const [q, setQ] = useState("");
  const rows = useMemo(() => competitors.filter((c) => (!origin || c.origin === origin) && (!onlyVerified || c.verified)
    && (!q || `${c.name} ${c.url ?? ""}`.toLowerCase().includes(q.toLowerCase()))), [competitors, origin, onlyVerified, q]);
  const n = (o: string) => competitors.filter((c) => c.origin === o).length;
  return (
    <Sheet title={`${competitors.length} competitor${competitors.length === 1 ? "" : "s"}`}
      sub={`${n("client")} provided by the client · ${n("discovered")} discovered by research · ${competitors.filter((c) => c.verified).length} with their website read`} onClose={onClose}>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <div className="w-48"><Select aria-label="Filter by origin" value={origin} onChange={(e) => setOrigin(e.target.value)}
          options={[{ value: "", label: "All origins" }, { value: "client", label: "Client-provided" }, { value: "discovered", label: "Discovered" }]} /></div>
        <label className="flex items-center gap-1.5 text-xs text-slate-700"><input type="checkbox" checked={onlyVerified} onChange={(e) => setOnlyVerified(e.target.checked)} />Website read only</label>
        <div className="min-w-[10rem] flex-1"><Input aria-label="Search competitors" placeholder="Search…" value={q} onChange={(e) => setQ(e.target.value)} /></div>
      </div>
      <ul aria-label="Competitors" className="divide-y divide-slate-100">{rows.map((c) => (
        <li key={c.url ?? c.name} className="py-2 text-[13px]">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium text-slate-900">{c.name}</span>
            <Badge tone={ORIGIN[c.origin][1]}>{ORIGIN[c.origin][0]}</Badge>
            <Badge tone={c.verified ? "green" : "slate"}>{c.verified ? "website read" : "not verified"}</Badge>
            {c.mentions > 1 && <span className="text-2xs text-slate-400">found {c.mentions}×</span>}
          </div>
          <div className={cx("mt-0.5 flex flex-wrap gap-x-3 text-2xs text-slate-500")}>
            {c.url && <a className="text-brand-700 hover:underline" href={c.url} target="_blank" rel="noreferrer noopener">{hostOf(c.url)}</a>}
            {c.providers.length > 0 && <span>via {c.providers.map(providerLabel).join(", ")}</span>}
            {c.basis && <span>{c.basis}</span>}{typeof c.distance_km === "number" && <span>{c.distance_km} km away</span>}
          </div>
        </li>))}</ul>
      {!rows.length && <p className="py-6 text-center text-[13px] text-slate-500">No competitor matches these filters.</p>}
    </Sheet>
  );
}

/** "N sources" / "N competitors" as buttons that open the explorers. */
export function EvidenceButtons({ sources, competitors }: { sources: SourceItem[]; competitors: CompetitorNode[] }) {
  const [open, setOpen] = useState<"" | "s" | "c">("");
  const chip = "rounded bg-slate-100 px-1.5 py-px text-2xs font-medium text-slate-700 hover:bg-slate-200 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500";
  return (<>
    {sources.length > 0 && <button type="button" className={chip} onClick={() => setOpen("s")}>{sources.length} source{sources.length === 1 ? "" : "s"}</button>}
    {competitors.length > 0 && <button type="button" className={chip} onClick={() => setOpen("c")}>{competitors.length} competitor{competitors.length === 1 ? "" : "s"}</button>}
    {open === "s" && <SourcesExplorer sources={sources} onClose={() => setOpen("")} />}
    {open === "c" && <CompetitorsExplorer competitors={competitors} onClose={() => setOpen("")} />}
  </>);
}
