import { useMemo, useState } from "react";
import type { ReactNode } from "react";

export type Col<T> = {
  key: string;
  label: string;
  render?: (r: T) => ReactNode;
  /** value used for sorting and search; defaults to r[key] */
  value?: (r: T) => string | number | null | undefined;
  align?: "right";
};
export type Filter<T> = { key: string; label: string; options: { value: string; label: string }[]; test: (r: T, v: string) => boolean };

const compactFmt = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });
/** 12,400 → 12.4K; null stays null (never 0). */
export const compact = (v: unknown) => (typeof v === "number" ? (Math.abs(v) < 1000 ? v.toLocaleString("en-US") : compactFmt.format(v)) : null);
export const usd = (v: unknown) => (typeof v === "number" ? `$${v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : null);

/** Search, sort, filters and pagination over rows already in memory. A null value sorts last in both directions. */
export function DataTable<T>({ rows, cols, label, filters = [], pageSize = 25, initialSort, empty = "None recorded.", search = true }: {
  rows: T[]; cols: Col<T>[]; label: string; filters?: Filter<T>[]; pageSize?: number; initialSort?: { key: string; dir: "asc" | "desc" };
  empty?: string; search?: boolean;
}) {
  const [q, setQ] = useState("");
  const [sort, setSort] = useState(initialSort ?? null);
  const [page, setPage] = useState(0);
  const [fv, setFv] = useState<Record<string, string>>({});
  const val = (c: Col<T>, r: T) => (c.value ? c.value(r) : (r as any)[c.key]);
  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase();
    let out = rows.filter((r) => filters.every((f) => !fv[f.key] || f.test(r, fv[f.key])));
    if (needle) out = out.filter((r) => cols.some((c) => String(val(c, r) ?? "").toLowerCase().includes(needle)));
    if (sort) {
      const c = cols.find((x) => x.key === sort.key);
      if (c) out = [...out].sort((a, b) => {
        const x = val(c, a), y = val(c, b);
        if (x === null || x === undefined || x === "") return 1;
        if (y === null || y === undefined || y === "") return -1;
        const d = typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y));
        return sort.dir === "asc" ? d : -d;
      });
    }
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows, q, sort, fv]);
  const pages = Math.max(1, Math.ceil(shown.length / pageSize));
  const p = Math.min(page, pages - 1);
  const slice = shown.slice(p * pageSize, (p + 1) * pageSize);
  const toggle = (k: string) => { setSort(sort?.key === k ? { key: k, dir: sort.dir === "asc" ? "desc" : "asc" } : { key: k, dir: "desc" }); setPage(0); };
  return (
    <div>
      {(search || filters.length > 0) && (
        <div className="mb-2 flex flex-wrap items-center gap-2 text-xs">
          {search && <input aria-label={`Search ${label}`} placeholder="Search…" value={q} onChange={(e) => { setQ(e.target.value); setPage(0); }}
            className="w-48 rounded-md border-0 px-2 py-1 ring-1 ring-inset ring-slate-300 focus:ring-2 focus:ring-brand-500" />}
          {filters.map((f) => (
            <select key={f.key} aria-label={f.label} value={fv[f.key] ?? ""} onChange={(e) => { setFv({ ...fv, [f.key]: e.target.value }); setPage(0); }}
              className="rounded-md border-0 py-1 pl-2 pr-7 ring-1 ring-inset ring-slate-300">
              <option value="">{f.label}: all</option>
              {f.options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>))}
          <span className="text-slate-500">{shown.length.toLocaleString()} of {rows.length.toLocaleString()}</span>
        </div>)}
      {!shown.length ? <p className="text-xs text-slate-500">{rows.length ? "No row matches these filters." : empty}</p> : (
        <div className="overflow-x-auto">
          <table aria-label={label} className="w-full min-w-[720px] text-left text-xs">
            <thead className="text-slate-500"><tr className="border-b border-slate-100">{cols.map((c) => (
              <th key={c.key} className={`py-1.5 pr-3 font-medium ${c.align === "right" ? "text-right" : ""}`}
                aria-sort={sort?.key === c.key ? (sort.dir === "asc" ? "ascending" : "descending") : "none"}>
                <button type="button" className="hover:text-slate-900" onClick={() => toggle(c.key)}>{c.label}{sort?.key === c.key ? (sort.dir === "asc" ? " ▲" : " ▼") : ""}</button>
              </th>))}</tr></thead>
            <tbody className="divide-y divide-slate-100 align-top">{slice.map((r, i) => (
              <tr key={i}>{cols.map((c) => <td key={c.key} className={`py-1.5 pr-3 text-slate-700 ${c.align === "right" ? "text-right tabular-nums" : ""}`}>
                {c.render ? c.render(r) : (val(c, r) as ReactNode) ?? "—"}</td>)}</tr>))}</tbody>
          </table>
        </div>)}
      {pages > 1 && (
        <div className="mt-2 flex items-center gap-2 text-xs text-slate-600">
          <button type="button" className="rounded px-2 py-0.5 ring-1 ring-slate-300 disabled:opacity-40" disabled={p === 0} onClick={() => setPage(p - 1)}>Previous</button>
          <span>Page {p + 1} of {pages}</span>
          <button type="button" className="rounded px-2 py-0.5 ring-1 ring-slate-300 disabled:opacity-40" disabled={p >= pages - 1} onClick={() => setPage(p + 1)}>Next</button>
        </div>)}
    </div>
  );
}
