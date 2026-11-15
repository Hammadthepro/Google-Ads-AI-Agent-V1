/** Turns a campaign-build task's recorded events and steps into what the build workspace shows. Pure and unit-tested:
 *  every number below comes from an event the backend recorded, never from a guess or a timer. */
import type { ResearchEvent } from "../research/model";

export type StageKey = "account" | "measurement" | "research" | "keywords" | "overlap" | "feasibility" | "intelligence" | "draft" | "challenge" | "human" | "validate" | "approval";
export type StageStatus = "waiting" | "running" | "done" | "failed" | "stopped";
export type BuildStage = { key: StageKey | string; title: string; status: StageStatus; summary?: string; attempt?: number };

const FLOW: Record<string, StageKey[]> = {
  BUILD_CAMPAIGN: ["account", "measurement", "research", "keywords", "overlap", "feasibility", "intelligence", "draft", "challenge", "human", "validate", "approval"],
  ADD_AD_GROUP: ["account", "measurement", "research", "keywords", "overlap", "feasibility", "intelligence", "draft", "challenge", "human", "validate", "approval"],
};
export const STAGE_LABEL: Record<string, string> = {
  account: "Account", measurement: "Conversion audit", research: "Website, GBP & competitors", keywords: "Keyword clusters", overlap: "Overlap",
  feasibility: "Feasibility", intelligence: "Intelligence", draft: "AI drafting", challenge: "AI challenge", human: "Human plan", validate: "Validation", approval: "Approval",
};
// steps recorded before steps carried a key
const KEY_OF_NAME: [RegExp, StageKey][] = [[/^Load account/, "account"], [/^Conversion measurement/, "measurement"], [/^Audits/, "research"],
  [/^Keyword research/, "keywords"], [/^Overlap/, "overlap"], [/^Feasibility/, "feasibility"], [/^Intelligence/, "intelligence"], [/^AI draft/, "draft"],
  [/^AI challenger/, "challenge"], [/^Human plan/, "human"], [/^Validation/, "validate"], [/^Approval/, "approval"]];
const keyOf = (s: any): string => s.key === "load" ? "account" : s.key === "audits" ? "research" : s.key || KEY_OF_NAME.find(([re]) => re.test(s.name))?.[1] || "";

/** The workflow, latest state per stage. A retried draft shows its newest attempt; later stages reflect what ran after it. */
export function buildStages(taskType: string, steps: any[]): BuildStage[] {
  const latest = new Map<string, BuildStage>();
  for (const s of steps ?? []) {
    const key = keyOf(s);
    if (!key) continue;
    if (key === "draft") for (const k of ["challenge", "human", "validate", "approval"]) latest.delete(k); // a retry starts these again
    const m = /attempt (\d+)/.exec(s.name ?? "");
    latest.set(key, { key, title: STAGE_LABEL[key] ?? s.name, summary: s.summary, attempt: m ? Number(m[1]) : undefined,
      status: s.status === "DONE" ? "done" : s.status === "RUNNING" ? "running" : s.status === "FAILED" ? "failed" : s.status === "STOPPED" ? "stopped" : "waiting" });
  }
  const planned = FLOW[taskType] ?? [...latest.keys()];
  return planned.map((k) => latest.get(k) ?? { key: k, title: STAGE_LABEL[k] ?? k, status: "waiting" as const });
}

/** The newest metric of a kind (feasibility, overlap, …) the backend computed. */
export function metric(events: ResearchEvent[], name: string): Record<string, any> | null {
  for (let i = events.length - 1; i >= 0; i--) if (events[i].kind === "metric_computed" && events[i].data?.metric === name) return events[i].data;
  return null;
}

export type AiCall = { provider: string; model?: string; attempt?: number; state: "asking" | "waiting" | "failed" | "done"; text: string; seq: number; at: string; waitSeconds?: number };

/** What the AI providers are doing in the most recent drafting run: who was asked, who is rate-limited (and for how long Groq said
 *  to wait), who failed and why, who answered. Only events after the latest drafting stage started count. */
export function draftActivity(events: ResearchEvent[]): { calls: AiCall[]; plan: Record<string, any> | null; started: boolean } {
  let start = -1;
  for (let i = events.length - 1; i >= 0; i--) if (events[i].kind === "stage_started" && events[i].stage === "draft") { start = i; break; }
  if (start < 0) return { calls: [], plan: null, started: false };
  const calls = new Map<string, AiCall>();
  let plan: Record<string, any> | null = null;
  for (const e of events.slice(start)) {
    const d = e.data ?? {};
    if (e.stage !== "draft") continue;
    if (e.kind === "plan_stored") { plan = d; continue; }
    if (!d.provider || !String(d.purpose ?? "").startsWith("DRAFT")) continue;
    const base = { provider: d.stage ? `${d.provider} · ${d.stage}` : d.provider, model: d.model, attempt: d.attempt, seq: e.seq, at: e.at };
    const key = base.provider;
    if (e.kind === "ai_request") calls.set(key, { ...base, state: "asking", text: `Asked for a draft (attempt ${d.attempt ?? 1})` });
    else if (e.kind === "ai_waiting") calls.set(key, { ...base, state: "waiting", text: e.title, waitSeconds: d.seconds });
    else if (e.kind === "ai_failed") calls.set(key, { ...base, state: e.level === "error" ? "failed" : "asking", text: d.reason ?? e.title });
    else if (e.kind === "ai_result") calls.set(key, { ...base, state: "done", text: `Returned a valid draft${d.usage_out ? ` · ${d.usage_out} output tokens` : ""}` });
  }
  return { calls: [...calls.values()], plan, started: true };
}

/** Seconds left of a wait the provider itself asked for (e.g. "retry in 52s"); 0 once over. */
export const waitLeft = (c: AiCall, now: number) => Math.max(0, Math.round((c.waitSeconds ?? 0) - (now - new Date(c.at).getTime()) / 1000));

export const providerName = (p: string) => {
  const [who, ...rest] = p.split(" · ");
  const name = who.startsWith("internal") ? "Groq (internal AI)" : who.startsWith("claude") ? "Claude" : who;
  return rest.length ? `${name} · ${rest.join(" · ")}` : name;
};

export type RoleActivity = { provider?: string; model?: string; status: "idle" | "working" | "done" | "failed" | "not_performed"; requests: number; failed: number;
  tokensIn: number; tokensOut: number; latencyMs: number; text: string; verdict?: string; objections?: number; high?: number };

/** What the Main AI and the Challenger AI did in this run, from the recorded AI events only (requests, tokens and latency as the
 *  provider reported them). A Challenger that did not run says why; nothing is shown as reviewed when it was not. */
export function aiRoleActivity(events: ResearchEvent[]): { main: RoleActivity; challenger: RoleActivity } {
  const blank = (): RoleActivity => ({ status: "idle", requests: 0, failed: 0, tokensIn: 0, tokensOut: 0, latencyMs: 0, text: "Not used yet in this run" });
  const out = { main: blank(), challenger: blank() };
  for (const e of events) {
    const d = e.data ?? {};
    if (e.kind === "challenger_dispatched") Object.assign(out.challenger, { status: "working", provider: d.provider, model: d.model, text: "Reviewing the draft against its evidence" });
    else if (e.kind === "challenger_result") Object.assign(out.challenger, { status: "done", verdict: d.verdict, objections: d.objections, high: d.high,
      text: `Verdict ${d.verdict} · ${d.objections ?? 0} objection(s)${d.high ? `, ${d.high} high` : ""}` });
    else if (e.kind === "challenger_failed") Object.assign(out.challenger, { status: "not_performed", text: d.reason || e.title.replace(/^Independent review not performed: /, "") });
    if (!["ai_request", "ai_result", "ai_failed"].includes(e.kind) || !d.role) continue;
    const r = d.role === "challenger" ? out.challenger : d.role === "main" ? out.main : null;
    if (!r) continue;
    r.provider = d.provider ?? r.provider; r.model = d.model ?? r.model;
    if (e.kind === "ai_request") { r.requests += 1; if (r.status !== "not_performed") { r.status = "working"; r.text = e.title; } }
    if (e.kind === "ai_result") { r.tokensIn += d.usage_in ?? 0; r.tokensOut += d.usage_out ?? 0; r.latencyMs += d.latency_ms ?? 0;
      if (d.role === "main") { r.status = "done"; r.text = e.title; } }
    if (e.kind === "ai_failed" && e.level === "error") { r.failed += 1; if (d.role === "main") { r.status = "failed"; r.text = d.reason ?? e.title; } }
  }
  return out;
}
