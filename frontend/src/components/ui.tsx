import { ReactNode, useEffect, useId, useRef, useState } from "react";

export function cx(...c: (string | false | null | undefined)[]) {
  return c.filter(Boolean).join(" ");
}

/* --------------------------------------------------------------------------------------------------------------- page */
export function PageHeader({ title, subtitle, actions, meta }: { title: string; subtitle?: ReactNode; actions?: ReactNode; meta?: ReactNode }) {
  return (
    <div className="mb-7 flex flex-wrap items-end justify-between gap-x-8 gap-y-3">
      <div className="min-w-0">
        <h1 className="truncate text-title font-semibold text-slate-900">{title}</h1>
        {subtitle && <p className="mt-1 max-w-3xl text-[13px] leading-5 text-slate-500">{subtitle}</p>}
        {meta && <div className="mt-2 flex flex-wrap items-center gap-2">{meta}</div>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

/** A titled region with no box: for content that sits directly on the canvas (lists, prose, filters). */
export function Section({ title, description, actions, children, className }: { title?: ReactNode; description?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={className}>
      {(title || actions) && (
        <header className="mb-3 flex flex-wrap items-end justify-between gap-3">
          <div className="min-w-0">
            {title && <h2 className="text-sm font-semibold text-slate-900">{title}</h2>}
            {description && <p className="mt-0.5 text-xs text-slate-500">{description}</p>}
          </div>
          {actions && <div className="flex items-center gap-2">{actions}</div>}
        </header>
      )}
      {children}
    </section>
  );
}

export function Card({ title, actions, children, className, flush, description }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string; flush?: boolean; description?: ReactNode }) {
  return (
    <section className={cx("min-w-0 rounded-lg border border-slate-200 bg-white", className)}>
      {(title || actions) && (
        <header className="flex min-h-[46px] flex-wrap items-center justify-between gap-x-3 gap-y-1 border-b border-slate-100 px-5 py-2.5">
          <div className="min-w-0">
            <h2 className="text-[13px] font-semibold text-slate-900">{title}</h2>
            {description && <p className="text-xs text-slate-500">{description}</p>}
          </div>
          {actions && <div className="flex flex-wrap items-center gap-2 text-xs">{actions}</div>}
        </header>
      )}
      <div className={flush ? "" : "p-5"}>{children}</div>
    </section>
  );
}
export const Panel = Card;

/* ------------------------------------------------------------------------------------------------------------- badges */
const tones: Record<string, string> = {
  green: "bg-emerald-50 text-emerald-800 ring-emerald-600/20",
  amber: "bg-amber-50 text-amber-900 ring-amber-600/25",
  red: "bg-rose-50 text-rose-800 ring-rose-600/20",
  blue: "bg-brand-50 text-brand-800 ring-brand-600/20",
  slate: "bg-slate-100 text-slate-700 ring-slate-500/15",
  violet: "bg-plum-50 text-plum-700 ring-plum-500/25",
};
const dots: Record<string, string> = { green: "bg-emerald-500", amber: "bg-amber-500", red: "bg-rose-500", blue: "bg-brand-500", slate: "bg-slate-400", violet: "bg-plum-500" };

export function Badge({ tone = "slate", children, title, dot }: { tone?: keyof typeof tones | string; children: ReactNode; title?: string; dot?: boolean }) {
  return (
    <span title={title} className={cx("inline-flex items-center gap-1 whitespace-nowrap rounded px-1.5 py-px text-2xs font-medium leading-4 ring-1 ring-inset", tones[tone] ?? tones.slate)}>
      {dot && <span aria-hidden className={cx("h-1.5 w-1.5 rounded-full", dots[tone] ?? dots.slate)} />}
      {children}
    </span>
  );
}

const STATUS_TONE: Record<string, string> = {
  ENABLED: "green", PAUSED: "slate", APPROVED: "blue", EXECUTED: "green",
  PENDING_APPROVAL: "amber", REJECTED: "red", CHANGES_REQUESTED: "amber", INVALIDATED: "slate", EXPIRED: "slate",
  VALIDATION_FAILED: "red", EXECUTION_FAILED: "red", EXECUTION_BLOCKED: "red", SUCCEEDED: "green", FAILED: "red", UNKNOWN: "amber",
  NOT_APPLIED: "slate", RECONCILE_CONFLICT: "red", COMPLETED: "green", RUNNING: "blue", QUEUED: "slate",
  AWAITING_APPROVAL: "amber", EXECUTED_PAUSED: "blue", OPEN: "amber", RESOLVED: "green",
  IN_PROGRESS: "blue", HELD: "slate", SHADOW: "violet", PROPOSED: "blue", APPLIED: "green", GO: "green", HOLD: "slate",
  FEASIBLE: "green", PARTIALLY_FEASIBLE: "amber", UNREALISTIC: "red", CONNECTED: "green", ERROR: "red", NOT_CONFIGURED: "slate", AUTH_FAILED: "red", RATE_LIMITED: "amber",
  AWAITING_WINNER: "amber", HIGH: "red", MEDIUM: "amber", LOW: "slate", CRITICAL: "red", SIMULATED: "violet", INTERNAL: "blue",
  APPROVE: "green", REVISE: "amber", REJECT: "red", PASS: "green", FAIL: "red", WARN: "amber",
};
export const statusTone = (v?: string | null) => (v ? STATUS_TONE[v] ?? "slate" : "slate");

export function StatusBadge({ value }: { value?: string | null }) {
  if (!value) return <span className="text-slate-400">—</span>;
  const text = value.replace(/_/g, " ").toLowerCase();
  const tone = STATUS_TONE[value] ?? "slate";
  return <Badge tone={tone} dot>{text.charAt(0).toUpperCase() + text.slice(1)}</Badge>;
}

/** Every result shows where its data came from. Only data retrieved from a real service is labelled LIVE. */
export function DataSourceBadge({ source }: { source?: string | null }) {
  const s = (source || "NONE").toUpperCase();
  const tone = s === "LIVE" ? "green" : s === "MIXED" ? "amber" : s === "MOCK" ? "violet" : "slate";
  const label = s === "LIVE" ? "LIVE DATA" : s === "MIXED" ? "MIXED DATA" : s === "MOCK" ? "TEST FIXTURE" : "NO DATA SOURCE";
  return <Badge tone={tone} title="Data source" dot>{label}</Badge>;
}

export type DataStatus = { state: "not_configured" | "no_account" | "no_data" | "data_available" | "historical" | "failed"; message?: string; retrieved_at?: string | null };

/** What the numbers on a screen are: current, historical (a refresh failed), missing, or blocked by a missing integration. */
export function DataStatusNotice({ status }: { status?: DataStatus | null }) {
  if (!status || status.state === "data_available") return null;
  const when = status.retrieved_at ? new Date(status.retrieved_at).toLocaleString() : "";
  switch (status.state) {
    case "not_configured":
      return <Notice tone="amber"><b>Google Ads integration is not configured.</b> Please configure and test your connection on the <a className="underline" href="/integrations">Integrations page</a>.</Notice>;
    case "no_account":
      return <Notice tone="blue">{status.message || "No Google Ads account is linked to this client yet."}</Notice>;
    case "no_data":
      return <Notice tone="blue">No Google Ads data available. {status.message}</Notice>;
    case "historical":
      return <Notice tone="amber"><b>Historical data</b> — last retrieved {when}. {status.message}</Notice>;
    default:
      return <Notice tone="red">{status.message || "Google Ads data could not be retrieved."}</Notice>;
  }
}

/* ------------------------------------------------------------------------------------------------------------ buttons */
type BtnProps = { children: ReactNode; onClick?: () => void; variant?: "primary" | "secondary" | "danger" | "ghost"; disabled?: boolean; type?: "button" | "submit"; title?: string; size?: "sm" | "md" };
export function Button({ children, onClick, variant = "secondary", disabled, type = "button", title, size = "md" }: BtnProps) {
  const v = {
    primary: "bg-brand-600 text-white shadow-[inset_0_-1px_0_rgb(0_0_0/0.18)] hover:bg-brand-700 active:bg-brand-800",
    secondary: "bg-white text-slate-800 ring-1 ring-inset ring-slate-300 hover:bg-slate-50 hover:ring-slate-400/70 active:bg-slate-100",
    danger: "bg-rose-700 text-white hover:bg-rose-800 active:bg-rose-900",
    ghost: "text-slate-600 hover:bg-slate-900/[0.05] hover:text-slate-900",
  }[variant];
  return (
    <button type={type} title={title} onClick={onClick} disabled={disabled}
      className={cx("inline-flex select-none items-center justify-center gap-1.5 whitespace-nowrap rounded-md font-medium transition-[background-color,box-shadow,color] duration-100 disabled:cursor-not-allowed disabled:opacity-45",
        size === "sm" ? "h-7 px-2.5 text-xs" : "h-8 px-3.5 text-[13px]", v)}>
      {children}
    </button>
  );
}

/* -------------------------------------------------------------------------------------------------------------- forms */
export function Field({ label, hint, children }: { label: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <label className="block text-sm">
      <span className="mb-1.5 block text-xs font-medium text-slate-700">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-xs leading-4 text-slate-500">{hint}</span>}
    </label>
  );
}

/** Group of controls (checkboxes, badges) — a fieldset, so each control keeps its own accessible name. */
export function FieldGroup({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <fieldset className="block text-sm">
      <legend className="mb-1.5 block text-xs font-medium text-slate-700">{label}</legend>
      {children}
      {hint && <span className="mt-1 block text-xs text-slate-500">{hint}</span>}
    </fieldset>
  );
}

export const inputCls = "block w-full rounded-md border-0 bg-white px-2.5 py-[7px] text-[13px] leading-5 text-slate-900 shadow-[0_1px_0_rgb(14_23_32/0.03)] ring-1 ring-inset ring-slate-300 transition-shadow placeholder:text-slate-400 hover:ring-slate-400/80 focus:ring-2 focus:ring-inset focus:ring-brand-500 disabled:bg-slate-50 disabled:text-slate-500 aria-[invalid=true]:ring-rose-400";

export function Input(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={cx(inputCls, props.className)} />;
}
export function Textarea(props: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea {...props} className={cx(inputCls, "font-[inherit]", props.className)} />;
}
export function Select({ options, ...props }: React.SelectHTMLAttributes<HTMLSelectElement> & { options: { value: string; label: string }[] }) {
  return (
    <select {...props} className={cx(inputCls, "appearance-none bg-[length:16px] bg-[right_0.5rem_center] bg-no-repeat pr-8", props.className)}
      style={{ backgroundImage: "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16' fill='none' stroke='%2364737f' stroke-width='1.4' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M5 6.5l3 3 3-3'/%3E%3C/svg%3E\")", ...props.style }}>
      {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
    </select>
  );
}

/* ------------------------------------------------------------------------------------------------------------- tables */
export function Table({ columns, rows, empty = "Nothing here yet.", onRowClick }: {
  columns: { key: string; label: string; render?: (row: any) => ReactNode; className?: string }[];
  rows: any[]; empty?: string; onRowClick?: (row: any) => void;
}) {
  // tables live inside a panel already: the empty message sits in it, without a second box
  if (!rows?.length) return <p className="py-7 text-center text-[13px] leading-5 text-slate-500">{empty}</p>;
  return (
    <div className="-mx-5 overflow-x-auto">
      <table className="min-w-full text-[13px]">
        <thead>
          <tr className="border-y border-slate-200 bg-slate-50/70 first:border-t-0">
            {columns.map((c) => <th key={c.key} scope="col" className={cx("whitespace-nowrap px-5 py-2 text-left text-xs font-medium text-slate-500", c.className)}>{c.label}</th>)}
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {rows.map((r, i) => (
            <tr key={r.id ?? i} onClick={onRowClick ? () => onRowClick(r) : undefined} tabIndex={onRowClick ? 0 : undefined}
              onKeyDown={onRowClick ? (e) => { if (e.key === "Enter") onRowClick(r); } : undefined}
              className={cx("transition-colors", onRowClick ? "cursor-pointer hover:bg-brand-50/40 focus-visible:bg-brand-50/60" : "hover:bg-slate-50/60")}>
              {columns.map((c) => <td key={c.key} className={cx("px-5 py-2.5 align-top text-slate-700", c.className)}>{c.render ? c.render(r) : fmt(r[c.key])}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function fmt(v: unknown): ReactNode {
  if (v === null || v === undefined || v === "") return <span className="text-slate-400">—</span>;
  if (typeof v === "boolean") return v ? "Yes" : "No";
  if (typeof v === "number") return <span className="tabular">{Number.isInteger(v) ? v.toLocaleString() : v.toLocaleString(undefined, { maximumFractionDigits: 2 })}</span>;
  if (typeof v === "object") return <code className="font-mono text-xs">{JSON.stringify(v)}</code>;
  return String(v);
}
export const money = (v?: number | null) => (v === null || v === undefined ? "—" : `$${v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`);
export const pct = (v?: number | null) => (v === null || v === undefined ? "—" : `${(v * 100).toFixed(1)}%`);
export const dt = (v?: string | null) => (v ? new Date(v).toLocaleString() : "—");
/** Short relative time for lists ("4 min ago", "Yesterday"); the absolute time stays in the title attribute. */
export function since(v?: string | null): string {
  if (!v) return "—";
  const s = Math.round((Date.now() - new Date(v).getTime()) / 1000);
  if (Number.isNaN(s)) return "—";
  if (s < 45) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  if (s < 172800) return "yesterday";
  return new Date(v).toLocaleDateString(undefined, { month: "short", day: "numeric", year: s > 300 * 86400 ? "numeric" : undefined });
}

/* ------------------------------------------------------------------------------------------------------ feedback/state */
export function EmptyState({ message, title, action }: { message: string; title?: string; action?: ReactNode }) {
  return (
    <div className="rounded-md border border-dashed border-slate-300/80 px-4 py-8 text-center">
      {title && <div className="text-[13px] font-medium text-slate-800">{title}</div>}
      <div className={cx("mx-auto max-w-md text-[13px] text-slate-500", title && "mt-0.5")}>{message}</div>
      {action && <div className="mt-3 flex justify-center">{action}</div>}
    </div>
  );
}

export function ErrorBox({ error }: { error: unknown }) {
  if (!error) return null;
  const msg = (error as any)?.message ?? String(error);
  const reasons: string[] = (error as any)?.details?.reasons ?? [];
  const errs: any[] = (error as any)?.details?.errors ?? [];
  const notConfigured = (error as any)?.code === "integration_not_configured";
  return (
    <div role="alert" className={cx("rounded-md border-l-[3px] px-3.5 py-2.5 text-[13px] leading-5", notConfigured ? "border-amber-500 bg-amber-50 text-amber-950" : "border-rose-600 bg-rose-50 text-rose-900")}>
      <div className="font-medium">{msg}</div>
      {notConfigured && <a className="mt-1 inline-block underline" href="/integrations">Open the Integrations page</a>}
      {reasons.length > 0 && <ul className="mt-1 list-disc pl-5">{reasons.map((r) => <li key={r}>{r}</li>)}</ul>}
      {errs.length > 0 && <ul className="mt-1 list-disc pl-5">{errs.slice(0, 6).map((e, i) => <li key={i}>{e.msg ?? JSON.stringify(e)}</li>)}</ul>}
    </div>
  );
}

export function Notice({ tone = "blue", children }: { tone?: "blue" | "amber" | "violet" | "green" | "red"; children: ReactNode }) {
  const t = {
    blue: "border-brand-500 bg-brand-50/70 text-slate-800", amber: "border-amber-500 bg-amber-50 text-amber-950",
    violet: "border-plum-500 bg-plum-50 text-plum-700", green: "border-emerald-500 bg-emerald-50 text-emerald-900",
    red: "border-rose-600 bg-rose-50 text-rose-900",
  }[tone];
  return <div className={cx("rounded-md border-l-[3px] px-3.5 py-2.5 text-[13px] leading-5", t)}>{children}</div>;
}

export function Spinner({ label = "Loading…" }: { label?: string }) {
  return (
    <div role="status" className="flex items-center gap-2 py-6 text-[13px] text-slate-500">
      <span aria-hidden className="h-3.5 w-3.5 animate-spin rounded-full border-[1.5px] border-slate-300 border-t-brand-600" />
      {label}
    </div>
  );
}

/** Placeholder lines while a page loads; announces itself once, not per line. */
export function Skeleton({ lines = 4, className }: { lines?: number; className?: string }) {
  return (
    <div role="status" aria-label="Loading" className={cx("space-y-2.5 py-2", className)}>
      {Array.from({ length: lines }).map((_, i) => <div key={i} className="skeleton h-3.5" style={{ width: `${92 - ((i * 17) % 40)}%` }} />)}
    </div>
  );
}

/* -------------------------------------------------------------------------------------------------------------- stats */
export function Stat({ label, value, sub }: { label: string; value: ReactNode; sub?: ReactNode }) {
  return (
    <div className="min-w-0 rounded-lg border border-slate-200 bg-white px-4 py-3">
      <div className="truncate text-xs text-slate-500">{label}</div>
      <div className="tabular mt-1 text-xl font-semibold tracking-tight text-slate-900">{value}</div>
      {sub && <div className="mt-0.5 truncate text-xs text-slate-500">{sub}</div>}
    </div>
  );
}

/** One divided strip of figures. Reads as a single instrument panel rather than a row of separate boxes. */
export function MetricStrip({ items, className }: { items: { label: string; value: ReactNode; sub?: ReactNode; emphasis?: boolean }[]; className?: string }) {
  return (
    <dl className={cx("grid overflow-hidden rounded-lg border border-slate-200 bg-white", className)}
      style={{ gridTemplateColumns: `repeat(auto-fit, minmax(${items.length > 4 ? 150 : 180}px, 1fr))` }}>
      {items.map((m) => (
        <div key={m.label} className="-ml-px -mt-px min-w-0 border-l border-t border-slate-200 px-4 py-3.5">
          <dt className="truncate text-xs text-slate-500">{m.label}</dt>
          <dd className={cx("tabular mt-1 font-semibold tracking-tight text-slate-900", m.emphasis ? "text-display" : "text-xl")}>{m.value}</dd>
          {m.sub && <dd className="mt-0.5 truncate text-xs text-slate-500">{m.sub}</dd>}
        </div>))}
    </dl>
  );
}

export function JsonBlock({ value, maxHeight = 360 }: { value: unknown; maxHeight?: number }) {
  return <pre className="pane-scroll-dark overflow-auto rounded-lg bg-ink p-3.5 font-mono text-xs leading-relaxed text-slate-200" style={{ maxHeight }}>{JSON.stringify(value, null, 2)}</pre>;
}

/* -------------------------------------------------------------------------------------------------------------- tabs */
export function Tabs({ tabs }: { tabs: { key: string; label: string; content: ReactNode }[] }) {
  const [active, setActive] = useState(tabs[0]?.key);
  return (
    <div>
      <div className="pane-scroll mb-5 flex gap-5 overflow-x-auto border-b border-slate-200">
        {tabs.map((t) => (
          <button key={t.key} type="button" onClick={() => setActive(t.key)} aria-pressed={active === t.key}
            className={cx("-mb-px whitespace-nowrap border-b-2 pb-2.5 pt-1 text-[13px] font-medium transition-colors",
              active === t.key ? "border-brand-600 text-slate-900" : "border-transparent text-slate-500 hover:border-slate-300 hover:text-slate-800")}>
            {t.label}
          </button>
        ))}
      </div>
      {tabs.find((t) => t.key === active)?.content}
    </div>
  );
}

/* ------------------------------------------------------------------------------------------------------------ dialogs */
/** Closes on Escape and returns focus to whatever opened it. */
export function useDialogKeys(open: boolean, onClose: () => void) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const prev = document.activeElement as HTMLElement | null;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") { e.stopPropagation(); onClose(); } };
    window.addEventListener("keydown", onKey);
    const first = ref.current?.querySelector<HTMLElement>("[data-autofocus], input, select, textarea, button");
    first?.focus?.();
    return () => { window.removeEventListener("keydown", onKey); prev?.focus?.(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);
  return ref;
}

export function ConfirmDialog({ open, title, body, confirmLabel = "Confirm", tone = "primary", onConfirm, onCancel, children }: {
  open: boolean; title: string; body?: ReactNode; confirmLabel?: string; tone?: "primary" | "danger"; onConfirm: () => void; onCancel: () => void; children?: ReactNode;
}) {
  const id = useId();
  const ref = useDialogKeys(open, onCancel);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-ink/45 p-0 backdrop-blur-[1px] sm:items-center sm:p-4" role="dialog" aria-modal="true" aria-labelledby={id}
      onMouseDown={(e) => { if (e.target === e.currentTarget) onCancel(); }}>
      <div ref={ref} className="w-full max-w-lg animate-dialog-in rounded-t-xl bg-white shadow-pop sm:rounded-xl">
        <div className={cx("px-5 pt-5", tone === "danger" && "border-t-[3px] border-rose-600 sm:rounded-t-xl")}>
          <h3 id={id} className="text-base font-semibold text-slate-900">{title}</h3>
          {body && <div className="mt-2 text-[13px] leading-5 text-slate-600">{body}</div>}
          {children && <div className="mt-3">{children}</div>}
        </div>
        <div className="mt-5 flex justify-end gap-2 rounded-b-xl border-t border-slate-100 bg-slate-50/70 px-5 py-3">
          <Button onClick={onCancel}>Cancel</Button>
          <Button variant={tone} onClick={onConfirm}>{confirmLabel}</Button>
        </div>
      </div>
    </div>
  );
}

export function KV({ items }: { items: [string, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-1 gap-x-8 text-[13px] sm:grid-cols-2">
      {items.map(([k, v]) => (
        <div key={k} className="flex items-baseline justify-between gap-4 border-b border-slate-100 py-2">
          <dt className="shrink-0 text-slate-500">{k}</dt>
          <dd className="min-w-0 break-words text-right font-medium text-slate-800">{v}</dd>
        </div>
      ))}
    </dl>
  );
}
