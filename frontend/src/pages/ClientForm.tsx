import { KeyboardEvent, ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api/client";
import { useAction, useClients } from "../lib/hooks";
import { Button, cx, ErrorBox, inputCls, Select, Spinner } from "../components/ui";

type Competitor = { name: string; website: string; maps_url: string };
type Profile = {
  name: string; category: string; phone: string; email: string; website: string; lead_form_url: string; thank_you_url: string; gtm_id: string;
  has_gbp: boolean | null; gbp_url: string; weekly_conversion_target: string; services: string[]; selling_points: string[]; service_areas: string[]; states: string[];
  targeting_mode: "AREAS" | "RADIUS"; radius_miles: number; monthly_budget: string; competitors: Competitor[]; notes: string;
};
const EMPTY: Profile = { name: "", category: "", phone: "", email: "", website: "", lead_form_url: "", thank_you_url: "", gtm_id: "", has_gbp: null, gbp_url: "", weekly_conversion_target: "", services: [],
  selling_points: [], service_areas: [], states: [], targeting_mode: "AREAS", radius_miles: 20, monthly_budget: "", competitors: [{ name: "", website: "", maps_url: "" }], notes: "" };

const SECTIONS = [
  { id: "business", label: "Business", fields: ["name", "category", "phone"] },
  { id: "website", label: "Website", fields: ["website"] },
  { id: "gbp", label: "Google profile", fields: ["has_gbp", "gbp_url"] },
  { id: "services", label: "Services", fields: ["services"] },
  { id: "locations", label: "Locations", fields: ["service_areas", "targeting_mode"] },
  { id: "targets", label: "Target", fields: ["weekly_conversion_target"] },
  { id: "competitors", label: "Competitors", fields: [] },
  { id: "notes", label: "Notes", fields: [] },
];

function Card({ id, title, hint, children }: { id: string; title: string; hint?: string; children: ReactNode }) {
  return (
    <section id={`sec-${id}`} className="scroll-mt-6 rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
      <h2 className="text-base font-semibold text-slate-900">{title}</h2>
      {hint && <p className="mt-1 text-sm text-slate-500">{hint}</p>}
      <div className="mt-4 space-y-4">{children}</div>
    </section>
  );
}

function Field({ label, required, note, error, children }: { label: string; required?: boolean; note?: string; error?: string; children: ReactNode }) {
  return (
    <label className="block text-sm">
      <span className="mb-1 block font-medium text-slate-800">{label}{required && <span className="text-rose-600"> *</span>}{note && <span className="font-normal text-slate-500"> · {note}</span>}</span>
      {children}
      {error && <span role="alert" className="mt-1 block text-xs text-rose-700">{error}</span>}
    </label>
  );
}

/** Chips input. Enter adds; commas add too unless `commaInside` (selling points keep their commas). Paste a list adds many. */
function TagInput({ label, required, note, hint, placeholder, values, onChange, error, commaInside, onBlur }: {
  label: string; required?: boolean; note?: string; hint?: string; placeholder: string; values: string[]; onChange: (v: string[]) => void; error?: string; commaInside?: boolean; onBlur?: () => void;
}) {
  const [text, setText] = useState("");
  const add = (raw: string) => {
    const parts = (commaInside ? raw.split("\n") : raw.split(/[\n,;]/)).map((s) => s.trim()).filter(Boolean);
    if (!parts.length) return;
    const seen = new Set(values.map((v) => v.toLowerCase()));
    const next = [...values];
    for (const p of parts) if (!seen.has(p.toLowerCase())) { seen.add(p.toLowerCase()); next.push(p); }
    onChange(next);
    setText("");
  };
  const key = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" || (!commaInside && e.key === ",")) { e.preventDefault(); add(text); }
    else if (e.key === "Backspace" && !text && values.length) onChange(values.slice(0, -1));
  };
  return (
    <div className="block text-sm">
      <label className="mb-1 block font-medium text-slate-800">{label}{required && <span className="text-rose-600"> *</span>}{note && <span className="font-normal text-slate-500"> · {note}</span>}
        <div className={cx("mt-1 flex min-h-[2.5rem] flex-wrap items-center gap-1.5 rounded-lg px-2 py-1.5 ring-1 ring-inset focus-within:ring-2 focus-within:ring-brand-500", error ? "ring-rose-400" : "ring-slate-300")}>
          {values.map((v, i) => (
            <span key={v} className="inline-flex items-center gap-1 rounded-md bg-brand-50 px-2 py-0.5 text-xs font-medium text-brand-700">
              {v}<button type="button" aria-label={`Remove ${v}`} className="text-brand-700/70 hover:text-brand-900" onClick={() => onChange(values.filter((_, j) => j !== i))}>×</button>
            </span>))}
          <input aria-label={`${label} input`} className="min-w-[10rem] flex-1 border-0 bg-transparent p-1 text-sm font-normal outline-none placeholder:text-slate-400" value={text} placeholder={values.length ? "" : placeholder}
            onChange={(e) => setText(e.target.value)} onKeyDown={key} onBlur={() => { add(text); onBlur?.(); }}
            onPaste={(e) => { const t = e.clipboardData.getData("text"); if (/[\n,;]/.test(t) && !commaInside || t.includes("\n")) { e.preventDefault(); add(t); } }} />
        </div>
      </label>
      {hint && <span className="block text-xs text-slate-500">{hint}</span>}
      {error && <span role="alert" className="mt-1 block text-xs text-rose-700">{error}</span>}
    </div>
  );
}

function Toggle({ options, value, onChange, label }: { options: { value: any; label: string }[]; value: any; onChange: (v: any) => void; label: string }) {
  return (
    <div role="group" aria-label={label} className="inline-flex overflow-hidden rounded-lg ring-1 ring-inset ring-slate-300">
      {options.map((o, i) => (
        <button type="button" key={String(o.value)} aria-pressed={value === o.value} onClick={() => onChange(o.value)}
          className={cx("px-4 py-2 text-sm font-medium", i > 0 && "border-l border-slate-300", value === o.value ? "bg-brand-50 text-brand-700" : "bg-white text-slate-600 hover:bg-slate-50")}>{o.label}</button>))}
    </div>
  );
}

export function ClientForm({ mode }: { mode: "new" | "edit" }) {
  const { id } = useParams();
  const nav = useNavigate();
  const clients = useClients();
  const existing = useQuery({ queryKey: ["client-profile", id], enabled: mode === "edit" && !!id, queryFn: () => api.get(`/client-profiles/${id}`) });
  const [p, setP] = useState<Profile>(EMPTY);
  const [touched, setTouched] = useState<Record<string, boolean>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [submitted, setSubmitted] = useState(false);
  const [fresh, setFresh] = useState(false);  // true only when `errors` was computed for the CURRENT input
  const loaded = useRef(false);
  const freshRef = useRef(false);
  const toFixRef = useRef(0);
  useEffect(() => {
    if (mode === "edit" && existing.data && !loaded.current) {
      loaded.current = true;
      const d = existing.data;
      setP({ ...EMPTY, ...d, monthly_budget: d.monthly_budget ? String(d.monthly_budget) : "", weekly_conversion_target: d.weekly_conversion_target ? String(d.weekly_conversion_target) : "", gbp_url: d.gbp_url ?? "", competitors: d.competitors?.length ? d.competitors : EMPTY.competitors });
    }
  }, [existing.data, mode]);
  useEffect(() => { if (mode === "new") { loaded.current = false; setP(EMPTY); setTouched({}); setSubmitted(false); } }, [mode, id]);

  // authoritative validation while typing (debounced); the server owns the rules
  useEffect(() => {
    let stale = false;  // a slow earlier response must never overwrite the result for newer input
    setFresh(false);    // hide per-field messages while the user is still typing, so the layout does not jump
    const t = setTimeout(() => {
      api.post(`/client-profiles/validate${mode === "edit" ? `?client_id=${id}` : ""}`, { profile: p })
        .then((r: any) => { if (!stale) { setErrors(r.errors ?? {}); setFresh(true); } }).catch(() => undefined);
    }, 350);
    return () => { stale = true; clearTimeout(t); };
  }, [p, mode, id]);

  const save = useAction((body) => (mode === "new" ? api.post("/client-profiles", body) : api.put(`/client-profiles/${id}`, body)), [["clients"], ["client", id!]]);
  const set = <K extends keyof Profile>(k: K, v: Profile[K]) => { setP((x) => ({ ...x, [k]: v })); };
  const touch = (k: string) => setTouched((t) => ({ ...t, [k]: true }));
  const err = (k: string) => ((fresh && (touched[k] || submitted)) ? errors[k] : undefined);
  const toFix = Object.keys(errors).length;
  freshRef.current = fresh;
  toFixRef.current = toFix;
  const sectionDone = useMemo(() => SECTIONS.map((s) => {
    if (s.id === "competitors") return p.competitors.some((c) => c.name || c.website);
    if (s.id === "notes") return !!p.notes.trim();
    if (s.id === "gbp") return p.has_gbp !== null && !errors.has_gbp && !errors.gbp_url && (p.has_gbp === false || !!p.gbp_url.trim());
    if (s.id === "targets") return !errors.weekly_conversion_target;  // optional: complete unless something invalid was typed
    return s.fields.every((f) => !errors[f] && ((p as any)[f] !== "" && (p as any)[f] !== null && !(Array.isArray((p as any)[f]) && !(p as any)[f].length)));
  }), [p, errors]);
  const doneCount = sectionDone.filter(Boolean).length;
  const submit = () => {
    setSubmitted(true);
    if (!freshRef.current) { setTimeout(submit, 200); return; }  // wait for validation of the latest input (read through refs: the closure is stale)
    if (toFixRef.current) { setTimeout(() => document.querySelector('[role="alert"]')?.scrollIntoView({ behavior: "smooth", block: "center" }), 50); return; }
    save.mutate({ profile: p }, {
      onSuccess: (r: any) => nav(mode === "new" ? `/clients/${r.client_id}?created=1${r.audit_report_id ? `&report=${r.audit_report_id}` : ""}` : `/clients/${id}?saved=1`),
    });
  };
  if (mode === "edit" && (existing.isLoading || !loaded.current && !existing.data)) return existing.error ? <ErrorBox error={existing.error} /> : <Spinner />;
  const serverFieldErrors: Record<string, string> = (save.error as any)?.details?.fields ?? {};
  const e = (k: string) => err(k) ?? (serverFieldErrors[k]);
  const setComp = (i: number, patch: Partial<Competitor>) => set("competitors", p.competitors.map((c, j) => (j === i ? { ...c, ...patch } : c)));

  return (
    <div className="-mx-4 -my-6 sm:-mx-6">
      <div className="flex items-center gap-3 border-b border-slate-200 bg-white px-6 py-3 text-sm">
        <span className="font-semibold text-brand-700">Google Ads <span className="font-normal text-slate-800">· client</span></span>
        <div className="inline-flex rounded-full bg-slate-100 p-0.5">
          <Link to="/clients/new" className={cx("rounded-full px-3 py-1 text-xs font-medium", mode === "new" ? "bg-brand-600 text-white" : "text-slate-600")}>New client</Link>
          <span className={cx("rounded-full px-3 py-1 text-xs font-medium", mode === "edit" ? "bg-brand-600 text-white" : "text-slate-600")}>
            {mode === "edit" ? "Edit client" : <EditPicker clients={clients.data ?? []} onPick={(cid) => nav(`/clients/${cid}/edit`)} />}</span>
        </div>
        <Link to="/" className="ml-auto text-brand-700">Dashboard</Link>
      </div>
      <div className="mx-auto grid max-w-6xl gap-8 px-4 py-8 lg:grid-cols-[14rem_1fr]">
        <nav aria-label="Form sections" className="hidden lg:block">
          <ul className="sticky top-6 space-y-1">
            {SECTIONS.map((s, i) => (
              <li key={s.id}>
                <a href={`#sec-${s.id}`} onClick={(ev) => { ev.preventDefault(); document.getElementById(`sec-${s.id}`)?.scrollIntoView({ behavior: "smooth" }); }}
                  className="flex items-center gap-3 rounded-lg px-3 py-2 text-sm text-slate-700 hover:bg-white">
                  <span className={cx("flex h-5 w-5 items-center justify-center rounded-full text-[11px] font-semibold", sectionDone[i] ? "bg-emerald-600 text-white" : "bg-slate-200 text-slate-600")}>{sectionDone[i] ? "✓" : i + 1}</span>{s.label}
                </a>
              </li>))}
            <li className="px-3 pt-3 text-xs text-slate-500">{doneCount} of {SECTIONS.length} sections complete
              <div className="mt-1 h-1 rounded bg-slate-200"><div className="h-1 rounded bg-brand-600 transition-all" style={{ width: `${(doneCount / SECTIONS.length) * 100}%` }} /></div></li>
          </ul>
        </nav>
        <div className="space-y-5 pb-24">
          <header>
            <h1 className="text-2xl font-semibold tracking-tight text-slate-900">{mode === "new" ? "New client" : "Edit client"}</h1>
            <p className="mt-1 text-sm text-slate-500">{mode === "new" ? "Fill this in after the client call. Fields with * are required. Everything is checked as you type."
              : <>Changes are saved to the profile of client <b>{existing.data?.client_code}</b>. Run a new audit afterwards to refresh the findings; earlier audits are kept.</>}</p>
          </header>

          <Card id="business" title="Business" hint="Calls and emails from the ads land on this phone and email.">
            <div className="grid gap-4 md:grid-cols-2">
              <Field label="Business name" required error={e("name")}><input className={inputCls} value={p.name} onChange={(ev) => set("name", ev.target.value)} onBlur={() => touch("name")} /></Field>
              <Field label="Business category" required error={e("category")}><input className={inputCls} placeholder="e.g. plumber, animal hospital" value={p.category} onChange={(ev) => set("category", ev.target.value)} onBlur={() => touch("category")} /></Field>
              <Field label="Business phone" required error={e("phone")}><input className={inputCls} inputMode="tel" placeholder="(512) 555-0100 or +1 512 555 0100" value={p.phone} onChange={(ev) => set("phone", ev.target.value)} onBlur={() => touch("phone")} /></Field>
              <Field label="Business email" note="form leads land here" error={e("email")}><input className={inputCls} type="email" placeholder="leads@business.com" value={p.email} onChange={(ev) => set("email", ev.target.value)} onBlur={() => touch("email")} /></Field>
            </div>
          </Card>

          <Card id="website" title="Website and tracking" hint="The thank-you page lets Google count form leads: without it the launch checklist fails.">
            <div className="grid gap-4 md:grid-cols-2">
              <Field label="Website" required error={e("website")}><input className={inputCls} placeholder="business.com" value={p.website} onChange={(ev) => set("website", ev.target.value)} onBlur={() => touch("website")} /></Field>
              <Field label="Lead form page" note="optional" error={e("lead_form_url")}><input className={inputCls} placeholder="/contact or the full link" value={p.lead_form_url} onChange={(ev) => set("lead_form_url", ev.target.value)} onBlur={() => touch("lead_form_url")} /></Field>
              <Field label="Thank-you page" note="recommended" error={e("thank_you_url")}><input className={inputCls} placeholder="/thank-you or the full link" value={p.thank_you_url} onChange={(ev) => set("thank_you_url", ev.target.value)} onBlur={() => touch("thank_you_url")} /></Field>
              <Field label="Google tag / GTM ID" note="if known" error={e("gtm_id")}><input className={inputCls} placeholder="GTM-XXXXXXX, G-XXXXXXXXXX, or paste the tag code" value={p.gtm_id} onChange={(ev) => set("gtm_id", ev.target.value)} onBlur={() => touch("gtm_id")} /></Field>
            </div>
          </Card>

          <Card id="gbp" title="Google Business Profile" hint="The public profile is used as a research input (reviews, services, location). No Google Business Profile API connection is needed.">
            <div><div className="mb-1 text-sm font-medium text-slate-800">Does this business have a Google Business Profile? <span className="text-rose-600">*</span></div>
              <Toggle label="Has Google Business Profile" options={[{ value: true, label: "Yes" }, { value: false, label: "No" }]} value={p.has_gbp}
                onChange={(v) => { setP((x) => ({ ...x, has_gbp: v, gbp_url: v ? x.gbp_url : "" })); touch("has_gbp"); }} />
              {e("has_gbp") && <span role="alert" className="mt-1 block text-xs text-rose-700">{e("has_gbp")}</span>}</div>
            {p.has_gbp === true && (
              <Field label="Google Business Profile URL" required error={e("gbp_url")}>
                <input className={inputCls} type="url" inputMode="url" placeholder="Paste the Google Maps or Google Business Profile link" value={p.gbp_url}
                  onChange={(ev) => set("gbp_url", ev.target.value)} onBlur={() => touch("gbp_url")} />
              </Field>)}
          </Card>

          <Card id="services" title="Services" hint="Every service becomes its own ad group. The key selling points are the only claims the ads may use.">
            <TagInput label="Services" required placeholder="Type a service, press Enter (or paste a list)" hint="Enter or comma adds it · paste a list to add many at once" values={p.services} onChange={(v) => set("services", v)} error={e("services")} onBlur={() => touch("services")} />
            <TagInput label="Key selling points" note="optional" placeholder="e.g. Licensed & insured, press Enter" hint="Enter adds it (commas stay inside a point)" commaInside values={p.selling_points} onChange={(v) => set("selling_points", v)} />
          </Card>

          <Card id="locations" title="Locations" hint={'Where the ads show. "Austin, TX 78701" works: the city, zip and state are split for you.'}>
            <div className="grid gap-4 md:grid-cols-2">
              <TagInput label="Service areas: cities or zip codes" required placeholder="City or zip, press Enter" commaInside values={p.service_areas} onChange={(v) => set("service_areas", v)} error={e("service_areas")} onBlur={() => touch("service_areas")} />
              <TagInput label="State(s)" note="empty = nationwide" placeholder="Texas or TX, press Enter" values={p.states} onChange={(v) => set("states", v)} error={e("states")} />
            </div>
            <div><div className="mb-1 text-sm font-medium text-slate-800">Location targeting <span className="text-rose-600">*</span></div>
              <Toggle label="Location targeting" options={[{ value: "AREAS", label: "Specific areas" }, { value: "RADIUS", label: "Radius around a location" }]} value={p.targeting_mode} onChange={(v) => set("targeting_mode", v)} />
              {p.targeting_mode === "RADIUS" && (
                <div className="mt-3 max-w-xs"><Field label="Radius (miles) around the first area" error={e("radius_miles")}><input className={inputCls} type="number" min={1} max={500} value={p.radius_miles} onChange={(ev) => set("radius_miles", Number(ev.target.value))} /></Field></div>)}</div>
          </Card>

          <Card id="targets" title="Weekly conversion target" hint="Optional. A conversion is whatever the client counts as a result: a qualified lead, call, purchase or booking. Leave it blank if unknown: the analysis recommends the daily and monthly budget from the evidence, so there is no budget to guess.">
            <div className="max-w-sm"><Field label="Weekly conversion target" note="optional" error={e("weekly_conversion_target")}>
              <input className={inputCls} inputMode="decimal" placeholder="e.g. 15 conversions per week" value={p.weekly_conversion_target}
                onChange={(ev) => set("weekly_conversion_target", ev.target.value)} onBlur={() => touch("weekly_conversion_target")} /></Field></div>
          </Card>

          <Card id="competitors" title="Competitors" hint="Every competitor needs a name and a website: both feed the website crawl, service comparison and keyword research. The Google Maps link is optional.">
            {p.competitors.map((c, i) => (
              <div key={i} className="space-y-3 rounded-lg border border-slate-200 p-4">
                <div className="flex items-center justify-between text-sm"><span className="font-medium text-slate-800">Competitor {i + 1}</span>
                  <button type="button" className="text-slate-500 hover:text-rose-700" onClick={() => set("competitors", p.competitors.length > 1 ? p.competitors.filter((_, j) => j !== i) : [{ name: "", website: "", maps_url: "" }])}>Remove</button></div>
                <div className="grid gap-3 md:grid-cols-2">
                  <Field label="Name" required error={e(`competitors.${i}`)}><input className={inputCls} placeholder="e.g. Ace Plumbing" value={c.name} onChange={(ev) => setComp(i, { name: ev.target.value })} /></Field>
                  <Field label="Website" required error={e(`competitors.${i}.website`)}><input className={inputCls} placeholder="aceplumbing.com" value={c.website} onChange={(ev) => setComp(i, { website: ev.target.value })} /></Field>
                </div>
                <Field label="Google Maps link" note="optional" error={e(`competitors.${i}.maps_url`)}><input className={inputCls} placeholder="google.com/maps/place/…" value={c.maps_url} onChange={(ev) => setComp(i, { maps_url: ev.target.value })} /></Field>
              </div>))}
            {p.competitors.length < 10 && <button type="button" onClick={() => set("competitors", [...p.competitors, { name: "", website: "", maps_url: "" }])}
              className="w-full rounded-lg border border-dashed border-brand-300 py-2.5 text-sm font-medium text-brand-700 hover:bg-brand-50">+ Add competitor</button>}
            {e("competitors") && <span role="alert" className="block text-xs text-rose-700">{e("competitors")}</span>}
          </Card>

          <Card id="notes" title="Notes">
            <textarea className={cx(inputCls, "min-h-[6rem]")} placeholder="anything from the call the team should know" value={p.notes} onChange={(ev) => set("notes", ev.target.value)} />
          </Card>
          {save.error && !Object.keys(serverFieldErrors).length && <ErrorBox error={save.error} />}
        </div>
      </div>
      <div className="fixed inset-x-0 bottom-0 z-30 border-t border-slate-200 bg-white/95 px-6 py-3 backdrop-blur lg:left-60">
        <div className="mx-auto flex max-w-6xl items-center justify-between gap-3">
          <span className={cx("text-sm font-medium", toFix ? "text-rose-700" : "text-emerald-700")} aria-live="polite">{toFix ? `${toFix} field(s) to fix` : "Ready to save"}</span>
          <div className="flex gap-2">
            <Button onClick={() => { setP(mode === "edit" && existing.data ? { ...EMPTY, ...existing.data, monthly_budget: existing.data.monthly_budget ? String(existing.data.monthly_budget) : "", weekly_conversion_target: existing.data.weekly_conversion_target ? String(existing.data.weekly_conversion_target) : "", gbp_url: existing.data.gbp_url ?? "", competitors: existing.data.competitors?.length ? existing.data.competitors : EMPTY.competitors } : EMPTY); setTouched({}); setSubmitted(false); save.reset(); }}>{mode === "edit" ? "Reset" : "Clear"}</Button>
            <Button variant="primary" disabled={save.isPending} onClick={submit}>{save.isPending ? "Saving…" : mode === "new" ? "Save client" : "Save changes"}</Button>
          </div>
        </div>
      </div>
    </div>
  );
}

function EditPicker({ clients, onPick }: { clients: any[]; onPick: (id: string) => void }) {
  return <Select aria-label="Edit an existing client" className="!w-40 !py-0.5 !text-xs" value="" onChange={(ev) => ev.target.value && onPick(ev.target.value)}
    options={[{ value: "", label: "Edit client…" }, ...clients.map((c) => ({ value: c.id, label: `${c.client_code ?? ""} ${c.name}` }))]} />;
}

export const NewClient = () => <ClientForm mode="new" />;
export const EditClient = () => <ClientForm mode="edit" />;
