import { ReactNode, useState } from "react";

export function cx(...c: (string | false | null | undefined)[]) {
  return c.filter(Boolean).join(" ");
}

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-slate-900">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-slate-500">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

export function Card({ title, actions, children, className }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={cx("rounded-xl border border-slate-200 bg-white shadow-sm", className)}>
      {(title || actions) && (
        <header className="flex items-center justify-between gap-3 border-b border-slate-100 px-5 py-3">
          <h2 className="text-sm font-semibold text-slate-800">{title}</h2>
          {actions}
        </header>
      )}
      <div className="p-5">{children}</div>
    </section>
  );
}

const tones: Record<string, string> = {
  green: "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
  amber: "bg-amber-50 text-amber-800 ring-amber-600/20",
  red: "bg-rose-50 text-rose-700 ring-rose-600/20",
  blue: "bg-brand-50 text-brand-700 ring-brand-600/20",
  slate: "bg-slate-100 text-slate-700 ring-slate-500/20",
  violet: "bg-violet-50 text-violet-700 ring-violet-600/20",
};

export function Badge({ tone = "slate", children, title }: { tone?: keyof typeof tones | string; children: ReactNode; title?: string }) {
  return (
    <span title={title} className={cx("inline-flex items-center gap-1 whitespace-nowrap rounded-md px-2 py-0.5 text-xs font-medium ring-1 ring-inset", tones[tone] ?? tones.slate)}>
      {children}
    </span>
  );
}

const STATUS_TONE: Record<string, string> = {
  GREEN: "green", YELLOW: "amber", AMBER: "amber", RED: "red", ENABLED: "green", PAUSED: "slate", APPROVED: "blue", EXECUTED: "green",
  PENDING_APPROVAL: "amber", REJECTED: "red", CHANGES_REQUESTED: "amber", INVALIDATED: "slate", EXPIRED: "slate",
  VALIDATION_FAILED: "red", EXECUTION_FAILED: "red", EXECUTION_BLOCKED: "red", SUCCEEDED: "green", FAILED: "red", UNKNOWN: "amber",
  NOT_APPLIED: "slate", RECONCILE_CONFLICT: "red", COMPLETED: "green", RUNNING: "blue", QUEUED: "slate",
  AWAITING_APPROVAL: "amber", BLOCKED_TRACKING: "red", EXECUTED_PAUSED: "blue", OPEN: "amber", RESOLVED: "green",
  IN_PROGRESS: "blue", HELD: "slate", SHADOW: "violet", PROPOSED: "blue", APPLIED: "green", GO: "green", HOLD: "slate",
  FEASIBLE: "green", PARTIALLY_FEASIBLE: "amber", UNREALISTIC: "red", CONNECTED: "green", ERROR: "red", NOT_CONFIGURED: "slate", AUTH_FAILED: "red", RATE_LIMITED: "amber",
  AWAITING_WINNER: "amber", HIGH: "red", MEDIUM: "amber", LOW: "slate", CRITICAL: "red", SIMULATED: "violet", INTERNAL: "blue",
  APPROVE: "green", REVISE: "amber", REJECT: "red", PASS: "green", FAIL: "red", WARN: "amber",
};

export function StatusBadge({ value }: { value?: string | null }) {
  if (!value) return <span className="text-slate-400">—</span>;
  return <Badge tone={STATUS_TONE[value] ?? "slate"}>{value.replace(/_/g, " ")}</Badge>;
}

/** Every result shows where its data came from. Only data retrieved from a real service is labelled LIVE. */
export function DataSourceBadge({ source }: { source?: string | null }) {
  const s = (source || "NONE").toUpperCase();
  const tone = s === "LIVE" ? "green" : s === "MIXED" ? "amber" : s === "MOCK" ? "violet" : "slate";
  const label = s === "LIVE" ? "LIVE DATA" : s === "MIXED" ? "MIXED DATA" : s === "MOCK" ? "TEST FIXTURE" : "NO DATA SOURCE";
  return <Badge tone={tone} title="Data source">{label}</Badge>;
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

type BtnProps = { children: ReactNode; onClick?: () => void; variant?: "primary" | "secondary" | "danger" | "ghost"; disabled?: boolean; type?: "button" | "submit"; title?: string; size?: "sm" | "md" };
export function Button({ children, onClick, variant = "secondary", disabled, type = "button", title, size = "md" }: BtnProps) {
  const v = {
    primary: "bg-brand-600 text-white hover:bg-brand-700 shadow-sm",
    secondary: "bg-white text-slate-700 ring-1 ring-inset ring-slate-300 hover:bg-slate-50",
    danger: "bg-rose-600 text-white hover:bg-rose-700 shadow-sm",
    ghost: "text-slate-600 hover:bg-slate-100",
  }[variant];
  return (
    <button type={type} title={title} onClick={onClick} disabled={disabled}
      className={cx("inline-flex items-center justify-center gap-1.5 rounded-lg font-medium transition disabled:cursor-not-allowed disabled:opacity-50",
        size === "sm" ? "px-2.5 py-1 text-xs" : "px-3.5 py-2 text-sm", v)}>
      {children}
    </button>
  );
}

export function Field({ label, hint, children }: { label: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <label className="block text-sm">
      <span className="mb-1 block font-medium text-slate-700">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-xs text-slate-500">{hint}</span>}
    </label>
  );
}

/** Group of controls (checkboxes, badges) — a fieldset, so each control keeps its own accessible name. */
export function FieldGroup({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <fieldset className="block text-sm">
      <legend className="mb-1 block font-medium text-slate-700">{label}</legend>
      {children}
      {hint && <span className="mt-1 block text-xs text-slate-500">{hint}</span>}
    </fieldset>
  );
}

export const inputCls = "block w-full rounded-lg border-0 px-3 py-2 text-sm text-slate-900 ring-1 ring-inset ring-slate-300 placeholder:text-slate-400 focus:ring-2 focus:ring-inset focus:ring-brand-500";

export function Input(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={cx(inputCls, props.className)} />;
}
export function Textarea(props: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea {...props} className={cx(inputCls, "font-[inherit]", props.className)} />;
}
export function Select({ options, ...props }: React.SelectHTMLAttributes<HTMLSelectElement> & { options: { value: string; label: string }[] }) {
  return (
    <select {...props} className={cx(inputCls, "pr-8", props.className)}>
      {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
    </select>
  );
}

export function Table({ columns, rows, empty = "Nothing here yet.", onRowClick }: {
  columns: { key: string; label: string; render?: (row: any) => ReactNode; className?: string }[];
  rows: any[]; empty?: string; onRowClick?: (row: any) => void;
}) {
  if (!rows?.length) return <EmptyState message={empty} />;
  return (
    <div className="-mx-5 overflow-x-auto">
      <table className="min-w-full divide-y divide-slate-200 text-sm">
        <thead>
          <tr>{columns.map((c) => <th key={c.key} className={cx("whitespace-nowrap px-5 py-2 text-left text-xs font-semibold uppercase tracking-wide text-slate-500", c.className)}>{c.label}</th>)}</tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {rows.map((r, i) => (
            <tr key={r.id ?? i} onClick={onRowClick ? () => onRowClick(r) : undefined} className={cx(onRowClick && "cursor-pointer hover:bg-slate-50")}>
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
  if (typeof v === "number") return Number.isInteger(v) ? v.toLocaleString() : v.toLocaleString(undefined, { maximumFractionDigits: 2 });
  if (typeof v === "object") return <code className="text-xs">{JSON.stringify(v)}</code>;
  return String(v);
}
export const money = (v?: number | null) => (v === null || v === undefined ? "—" : `$${v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`);
export const pct = (v?: number | null) => (v === null || v === undefined ? "—" : `${(v * 100).toFixed(1)}%`);
export const dt = (v?: string | null) => (v ? new Date(v).toLocaleString() : "—");

export function EmptyState({ message }: { message: string }) {
  return <div className="rounded-lg border border-dashed border-slate-200 px-4 py-8 text-center text-sm text-slate-500">{message}</div>;
}

export function ErrorBox({ error }: { error: unknown }) {
  if (!error) return null;
  const msg = (error as any)?.message ?? String(error);
  const reasons: string[] = (error as any)?.details?.reasons ?? [];
  const errs: any[] = (error as any)?.details?.errors ?? [];
  const notConfigured = (error as any)?.code === "integration_not_configured";
  return (
    <div role="alert" className={cx("rounded-lg border px-4 py-3 text-sm", notConfigured ? "border-amber-200 bg-amber-50 text-amber-900" : "border-rose-200 bg-rose-50 text-rose-800")}>
      <div className="font-medium">{msg}</div>
      {notConfigured && <a className="mt-1 inline-block underline" href="/integrations">Open the Integrations page</a>}
      {reasons.length > 0 && <ul className="mt-1 list-disc pl-5">{reasons.map((r) => <li key={r}>{r}</li>)}</ul>}
      {errs.length > 0 && <ul className="mt-1 list-disc pl-5">{errs.slice(0, 6).map((e, i) => <li key={i}>{e.msg ?? JSON.stringify(e)}</li>)}</ul>}
    </div>
  );
}

export function Notice({ tone = "blue", children }: { tone?: "blue" | "amber" | "violet" | "green" | "red"; children: ReactNode }) {
  const t = { blue: "border-brand-100 bg-brand-50 text-brand-700", amber: "border-amber-200 bg-amber-50 text-amber-900",
    violet: "border-violet-200 bg-violet-50 text-violet-800", green: "border-emerald-200 bg-emerald-50 text-emerald-800",
    red: "border-rose-200 bg-rose-50 text-rose-800" }[tone];
  return <div className={cx("rounded-lg border px-4 py-3 text-sm", t)}>{children}</div>;
}

export function Spinner({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 py-6 text-sm text-slate-500">
      <span className="h-4 w-4 animate-spin rounded-full border-2 border-slate-300 border-t-brand-600" />
      {label}
    </div>
  );
}

export function Stat({ label, value, sub }: { label: string; value: ReactNode; sub?: ReactNode }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-sm">
      <div className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</div>
      <div className="mt-1 text-xl font-semibold text-slate-900">{value}</div>
      {sub && <div className="mt-0.5 text-xs text-slate-500">{sub}</div>}
    </div>
  );
}

export function JsonBlock({ value, maxHeight = 360 }: { value: unknown; maxHeight?: number }) {
  return <pre className="overflow-auto rounded-lg bg-slate-900 p-3 font-mono text-xs leading-relaxed text-slate-100" style={{ maxHeight }}>{JSON.stringify(value, null, 2)}</pre>;
}

export function Tabs({ tabs }: { tabs: { key: string; label: string; content: ReactNode }[] }) {
  const [active, setActive] = useState(tabs[0]?.key);
  return (
    <div>
      <div className="mb-4 flex gap-1 border-b border-slate-200">
        {tabs.map((t) => (
          <button key={t.key} onClick={() => setActive(t.key)}
            className={cx("-mb-px border-b-2 px-3 py-2 text-sm font-medium", active === t.key ? "border-brand-600 text-brand-700" : "border-transparent text-slate-500 hover:text-slate-700")}>
            {t.label}
          </button>
        ))}
      </div>
      {tabs.find((t) => t.key === active)?.content}
    </div>
  );
}

export function ConfirmDialog({ open, title, body, confirmLabel = "Confirm", tone = "primary", onConfirm, onCancel, children }: {
  open: boolean; title: string; body?: ReactNode; confirmLabel?: string; tone?: "primary" | "danger"; onConfirm: () => void; onCancel: () => void; children?: ReactNode;
}) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4" role="dialog" aria-modal="true">
      <div className="w-full max-w-lg rounded-xl bg-white p-5 shadow-xl">
        <h3 className="text-base font-semibold text-slate-900">{title}</h3>
        {body && <div className="mt-2 text-sm text-slate-600">{body}</div>}
        {children && <div className="mt-3">{children}</div>}
        <div className="mt-5 flex justify-end gap-2">
          <Button onClick={onCancel}>Cancel</Button>
          <Button variant={tone} onClick={onConfirm}>{confirmLabel}</Button>
        </div>
      </div>
    </div>
  );
}

export function KV({ items }: { items: [string, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-1 gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
      {items.map(([k, v]) => (
        <div key={k} className="flex justify-between gap-3 border-b border-slate-100 py-1.5">
          <dt className="text-slate-500">{k}</dt>
          <dd className="text-right font-medium text-slate-800">{v}</dd>
        </div>
      ))}
    </dl>
  );
}
