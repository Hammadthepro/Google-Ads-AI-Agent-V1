import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useLocation, useNavigate } from "react-router-dom";
import { api, errorMessage } from "../api/client";
import { ROLE_LABEL } from "../lib/auth";
import { Badge, cx, ErrorBox, inputCls, Notice, Spinner } from "../components/ui";

declare global { interface Window { google?: any } }

export default function Login() {
  const cfg = useQuery({ queryKey: ["auth-config"], queryFn: () => api.get("/auth/config") });
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState("");
  const qc = useQueryClient();
  const nav = useNavigate();
  const loc = useLocation() as any;
  const googleBtn = useRef<HTMLDivElement>(null);

  const done = async () => {
    await qc.invalidateQueries({ queryKey: ["me"] });
    nav(loc.state?.from || "/", { replace: true });
  };

  const devLogin = async (email: string) => {
    setBusy(email);
    setError(null);
    try {
      await api.post("/auth/dev-login", { email });
      await done();
    } catch (e) {
      setError(new Error(errorMessage(e)));
    } finally {
      setBusy("");
    }
  };

  useEffect(() => {
    if (cfg.data?.provider !== "google" || !cfg.data.google_client_id) return;
    const s = document.createElement("script");
    s.src = "https://accounts.google.com/gsi/client";
    s.async = true;
    s.onload = () => {
      window.google?.accounts.id.initialize({
        client_id: cfg.data.google_client_id,
        callback: async (resp: { credential: string }) => {
          try { await api.post("/auth/google", { credential: resp.credential }); await done(); }
          catch (e) { setError(new Error(errorMessage(e))); }
        },
      });
      window.google?.accounts.id.renderButton(googleBtn.current, { theme: "outline", size: "large", width: 320 });
    };
    document.body.appendChild(s);
  }, [cfg.data]);

  return (
    <div className="grid min-h-screen bg-canvas lg:grid-cols-[minmax(0,5fr)_minmax(0,6fr)]">
      <aside className="relative hidden flex-col justify-between overflow-hidden bg-ink px-12 py-10 text-slate-200 lg:flex">
        <div className="flex items-center gap-2.5">
          <svg aria-hidden viewBox="0 0 24 24" className="h-7 w-7"><rect width="24" height="24" rx="6" fill="#136977" /><path d="M6 8.5h7M6 12h4.5" stroke="#d3e9ec" strokeWidth="1.8" strokeLinecap="round" /><path d="M6 17.2l4-2.6 3 1.6 5-5" stroke="#fff" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" fill="none" /></svg>
          <span className="text-sm font-semibold text-white">Ads Agent <span className="font-normal text-ink-muted">V1</span></span>
        </div>
        <div className="max-w-md">
          <p className="text-display font-semibold text-white">Research, plan and run Google Search campaigns with a human on every change.</p>
          <dl className="mt-10 space-y-5 text-[13px]">
            {[["Approval first", "Nothing changes in Google Ads without an approval."], ["Evidence shown", "Every figure says where it came from: live, historical or unavailable."],
              ["Writes off by default", "Real Google Ads writes need two separate keys to be turned on."]].map(([t, d]) => (
              <div key={t} className="border-l border-ink-line pl-4"><dt className="font-medium text-white">{t}</dt><dd className="mt-0.5 text-ink-muted">{d}</dd></div>))}
          </dl>
        </div>
        <p className="text-2xs text-ink-muted">App sign-in never grants access to Google Ads accounts — those are connected separately.</p>
      </aside>
      <div className="flex items-center justify-center px-4 py-12">
      <div className="w-full max-w-[400px]">
        <div className="mb-8">
          <h1 className="text-title font-semibold text-slate-900">Sign in</h1>
          <p className="mt-1 text-[13px] text-slate-500">Google Ads AI Agent · safety-first campaign operations</p>
        </div>
        <div className="rounded-xl border border-slate-200 bg-white p-6">
          {cfg.isLoading && <Spinner />}
          {cfg.data?.provider === "google" && (
            <>
              <p className="mb-4 text-sm text-slate-600">Sign in with your Google account. Your account must be invited by an administrator.</p>
              <div ref={googleBtn} className="flex justify-center" />
            </>
          )}
          {cfg.data?.provider === "dev" && (
            <>
              <Notice tone="violet">
                <b>Development login.</b> Google sign-in is not configured, so sign in with the email of a user that already exists. This screen is disabled in production.
              </Notice>
              <form className="mt-4 flex gap-2" onSubmit={(e) => { e.preventDefault(); const em = new FormData(e.currentTarget).get("email"); if (em) devLogin(String(em)); }}>
                <input name="email" type="email" required placeholder="you@example.com" aria-label="Email" disabled={!!busy}
                  className={cx(inputCls, "min-w-0 flex-1")} />
                <button type="submit" disabled={!!busy} className="rounded-md bg-brand-600 px-3.5 text-[13px] font-medium text-white hover:bg-brand-700 disabled:opacity-60">Sign in</button>
              </form>
              {(cfg.data.dev_users ?? []).length === 0 && <p className="mt-3 text-xs text-slate-500">No users exist yet. Create the first Super Admin with <code>python -m app.cli create-super-admin you@example.com</code> or set <code>BOOTSTRAP_ADMIN_EMAIL</code> in <code>.env</code>.</p>}
              <div className="mt-4 space-y-2">
                {(cfg.data.dev_users ?? []).map((u: any) => (
                  <button key={u.email} onClick={() => devLogin(u.email)} disabled={!!busy}
                    className="flex w-full items-center justify-between rounded-md border border-slate-200 px-3 py-2.5 text-left text-[13px] transition-colors hover:border-brand-400 hover:bg-brand-50/50 disabled:opacity-60">
                    <span>
                      <span className="block font-medium text-slate-800">{u.name}</span>
                      <span className="block text-xs text-slate-500">{u.email}</span>
                    </span>
                    <Badge tone={u.role === "SUPER_ADMIN" ? "red" : u.role === "ADMIN" ? "blue" : u.role === "ANALYST" ? "slate" : "green"}>
                      {busy === u.email ? "Signing in…" : ROLE_LABEL[u.role]}
                    </Badge>
                  </button>
                ))}
              </div>
            </>
          )}
          {error ? <div className="mt-4"><ErrorBox error={error} /></div> : null}
        </div>
        <p className="mt-4 text-xs text-slate-500 lg:hidden">App sign-in never grants access to Google Ads accounts — those are connected separately.</p>
      </div>
      </div>
    </div>
  );
}
