import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useLocation, useNavigate } from "react-router-dom";
import { api, errorMessage } from "../api/client";
import { ROLE_LABEL } from "../lib/auth";
import { Badge, ErrorBox, Notice, Spinner } from "../components/ui";

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
    <div className="flex min-h-screen items-center justify-center bg-gradient-to-b from-slate-50 to-slate-100 px-4">
      <div className="w-full max-w-md">
        <div className="mb-6 text-center">
          <div className="mx-auto mb-3 flex h-11 w-11 items-center justify-center rounded-xl bg-brand-600 text-base font-bold text-white">GA</div>
          <h1 className="text-xl font-semibold">Google Ads AI Agent</h1>
          <p className="mt-1 text-sm text-slate-500">Safety-first campaign operations · V1</p>
        </div>
        <div className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
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
                  className="min-w-0 flex-1 rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-brand-500 focus:outline-none" />
                <button type="submit" disabled={!!busy} className="rounded-lg bg-brand-600 px-3.5 py-2 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-60">Sign in</button>
              </form>
              {(cfg.data.dev_users ?? []).length === 0 && <p className="mt-3 text-xs text-slate-500">No users exist yet. Create the first Super Admin with <code>python -m app.cli create-super-admin you@example.com</code> or set <code>BOOTSTRAP_ADMIN_EMAIL</code> in <code>.env</code>.</p>}
              <div className="mt-4 space-y-2">
                {(cfg.data.dev_users ?? []).map((u: any) => (
                  <button key={u.email} onClick={() => devLogin(u.email)} disabled={!!busy}
                    className="flex w-full items-center justify-between rounded-lg border border-slate-200 px-3 py-2.5 text-left text-sm hover:border-brand-500 hover:bg-brand-50 disabled:opacity-60">
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
        <p className="mt-4 text-center text-xs text-slate-500">App sign-in never grants access to Google Ads accounts — those are connected separately.</p>
      </div>
    </div>
  );
}
