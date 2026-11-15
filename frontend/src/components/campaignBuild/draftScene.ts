/** What the drafting and review scene shows, derived ONLY from recorded backend events of the latest drafting run. Pure and unit-tested.
 *  A phase lights up only when an event for it exists; token counts appear only when the provider reported them; a skipped review says why. */
import type { ResearchEvent } from "../research/model";

export type PhaseState = "waiting" | "running" | "done" | "failed" | "skipped";
export type Phase = { key: string; label: string; state: PhaseState; detail?: string };
export type GroupNode = { name: string; keywords: number; negatives: number; ads: number; state: "planned" | "writing" | "written" };
export type DraftSceneModel = {
  started: boolean; finished: boolean;
  main: { provider?: string; model?: string; state: "idle" | "requesting" | "waiting" | "done" | "failed"; tokensIn: number; tokensOut: number; requests: number; waitSeconds?: number; text: string };
  phases: Phase[]; groups: GroupNode[];
  challenger: { state: "idle" | "reviewing" | "done" | "skipped" | "failed"; provider?: string; model?: string; verdict?: string; objections: number; high: number;
    tokensIn: number; tokensOut: number; reason?: string; phases: Phase[] };
};

const isDraft = (d: any) => String(d.purpose ?? "").startsWith("DRAFT");

export function draftScene(events: ResearchEvent[]): DraftSceneModel {
  let start = -1;
  for (let i = events.length - 1; i >= 0; i--) if (events[i].kind === "stage_started" && events[i].stage === "draft") { start = i; break; }
  const m: DraftSceneModel = {
    started: start >= 0, finished: false,
    main: { state: "idle", tokensIn: 0, tokensOut: 0, requests: 0, text: "Waiting for drafting to start" },
    phases: [
      { key: "prepare", label: "Request preparation", state: "waiting" },
      { key: "structure", label: "Campaign structure", state: "waiting" },
      { key: "groups", label: "Ad groups & keywords", state: "waiting" },
      { key: "copy", label: "Ad copy", state: "waiting" },
      { key: "validate", label: "Draft validation", state: "waiting" },
    ],
    groups: [],
    challenger: { state: "idle", objections: 0, high: 0, tokensIn: 0, tokensOut: 0, phases: [
      { key: "activate", label: "Independent reviewer", state: "waiting" },
      { key: "inspect", label: "Structure, keywords, ad copy & policy", state: "waiting", detail: "one review request covers all four" },
      { key: "concerns", label: "Concerns identified", state: "waiting" },
      { key: "verdict", label: "Review verdict", state: "waiting" },
    ] },
  };
  if (start < 0) return m;
  const ph = (k: string) => m.phases.find((p) => p.key === k)!;
  const cph = (k: string) => m.challenger.phases.find((p) => p.key === k)!;
  ph("prepare").state = "running";
  let staged = false;
  for (const e of events.slice(start)) {
    const d = e.data ?? {};
    if (e.kind === "stage_completed" && e.stage === "draft") { m.finished = true; }
    if (e.kind === "stage_failed" && e.stage === "draft") { m.finished = true; m.main.state = "failed"; }
    if (e.kind === "note" && e.stage === "draft" && Array.isArray(d.ad_groups)) {  // staged: the plan is fixed, contents follow
      staged = true;
      ph("structure").state = "done";
      m.groups = d.ad_groups.map((name: string) => ({ name, keywords: 0, negatives: 0, ads: 0, state: "planned" as const }));
      ph("groups").state = "running";
    }
    if (e.kind === "plan_stored") {
      m.groups = (d.ad_groups ?? []).map((g: any) => ({ name: g.name, keywords: g.keywords ?? 0, negatives: g.negatives ?? 0, ads: g.ads ?? 0, state: "written" as const }));
      for (const k of ["structure", "groups", "copy"]) ph(k).state = "done";
      ph("groups").detail = `${m.groups.length} ad group(s) · ${m.groups.reduce((a, g) => a + g.keywords, 0)} keywords`;
      ph("copy").detail = `${m.groups.reduce((a, g) => a + g.ads, 0)} ad(s)`;
    }
    if (e.stage === "validate" && e.kind === "stage_started") ph("validate").state = "running";
    if (e.stage === "validate" && e.kind === "stage_completed") ph("validate").state = "done";
    if (e.stage === "validate" && e.kind === "stage_failed") ph("validate").state = "failed";
    // challenger
    if (e.kind === "challenger_dispatched") {
      Object.assign(m.challenger, { state: "reviewing", provider: d.provider, model: d.model });
      cph("activate").state = "done"; cph("inspect").state = "running";
    }
    if (e.kind === "challenger_result") {
      Object.assign(m.challenger, { state: "done", verdict: d.verdict, objections: d.objections ?? 0, high: d.high ?? 0,
        tokensIn: d.tokens_in ?? m.challenger.tokensIn, tokensOut: d.tokens_out ?? m.challenger.tokensOut });
      cph("activate").state = "done"; cph("inspect").state = "done";
      cph("concerns").state = "done"; cph("concerns").detail = `${d.objections ?? 0} concern(s)${d.high ? `, ${d.high} high` : ""}`;
      cph("verdict").state = "done"; cph("verdict").detail = d.verdict;
    }
    if (e.kind === "challenger_failed") {
      const dispatched = cph("activate").state === "done";
      Object.assign(m.challenger, { state: dispatched ? "failed" : "skipped", reason: d.reason || e.title });
      for (const p of m.challenger.phases) if (p.state !== "done") p.state = dispatched ? "failed" : "skipped";
      cph("verdict").detail = "not performed";
    }
    if (!["ai_request", "ai_result", "ai_failed", "ai_waiting"].includes(e.kind)) continue;
    if (d.role === "challenger") {
      if (e.kind === "ai_result") { m.challenger.tokensIn += d.usage_in ?? 0; m.challenger.tokensOut += d.usage_out ?? 0; }
      continue;
    }
    if (!isDraft(d)) continue;
    const sub = String(d.stage ?? "");
    m.main.provider = d.provider ?? m.main.provider; m.main.model = d.model ?? m.main.model;
    if (e.kind === "ai_request") {
      m.main.requests += 1; m.main.state = "requesting"; m.main.text = e.title; m.main.waitSeconds = undefined;
      ph("prepare").state = "done";
      if (sub.startsWith("ad group")) {
        staged = true; ph("groups").state = "running"; ph("copy").state = "running";
        const nums = (sub.match(/\d+/g) ?? []).map(Number);
        const [a, b] = [nums[0] ?? 1, nums[1] ?? nums[0] ?? 1];
        m.groups.forEach((g, i) => { if (i + 1 >= a && i + 1 <= b && g.state === "planned") g.state = "writing"; });
      } else if (ph("structure").state !== "done") ph("structure").state = "running";
    }
    if (e.kind === "ai_waiting") { m.main.state = "waiting"; m.main.text = e.title; m.main.waitSeconds = d.seconds; }
    if (e.kind === "ai_result") {
      m.main.tokensIn += d.usage_in ?? 0; m.main.tokensOut += d.usage_out ?? 0; m.main.state = "done"; m.main.text = e.title;
      if (sub === "campaign plan") ph("structure").state = "done";
      if (sub.startsWith("ad group")) m.groups.forEach((g) => { if (g.state === "writing") g.state = "written"; });
      if (!staged && !sub) for (const k of ["structure", "groups", "copy"]) if (ph(k).state !== "done") ph(k).state = "done";
    }
    if (e.kind === "ai_failed" && e.level === "error") {
      m.main.state = "failed"; m.main.text = d.reason ?? e.title;
      for (const k of ["structure", "groups", "copy"]) if (ph(k).state === "running") ph(k).state = "failed";
    }
  }
  return m;
}
