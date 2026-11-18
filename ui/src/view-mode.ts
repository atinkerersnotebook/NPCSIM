import type { CompletionAnalysis, RunEvent, State } from "./contracts";
import { clockTime } from "./bakery-view.ts";
import { escape } from "./html.ts";

export function villageClockTime(minute: number): string {
  const time = clockTime(minute % 1440 * 60).slice(0, 5);
  return minute >= 1440 ? `${time} on day ${Math.floor(minute / 1440) + 1}` : time;
}

export function villageClockText(text: string): string {
  return text.replace(/\b(at|until|by|after|before|since|due) minute (\d{1,15})\b/gi,
    (_, prefix: string, minute: string) => `${prefix} ${villageClockTime(Number(minute))}`);
}

type VillageTimeSource = { village?: { minute: number } | null } | null | undefined;

export function latestVillageMinute(snapshot: VillageTimeSource, observed?: VillageTimeSource): number | undefined {
  const savedMinute = snapshot?.village?.minute;
  const observedMinute = observed?.village?.minute;
  if (savedMinute === undefined) return observedMinute;
  return observedMinute === undefined ? savedMinute : Math.max(savedMinute, observedMinute);
}

export function simulationClock(snapshot: {
  village?: { minute: number } | null;
  clock?: { seconds: number } | null;
  bbq?: { elapsed_seconds: number } | null;
} | null | undefined, observed?: VillageTimeSource): { time: string; description: string } | null {
  const minute = latestVillageMinute(snapshot, observed);
  if (minute !== undefined) {
    return { time: villageClockTime(minute % 1440), description: `Day ${Math.floor(minute / 1440) + 1}` };
  }
  const seconds = snapshot?.clock?.seconds ?? snapshot?.bbq?.elapsed_seconds;
  return seconds === undefined ? null : { time: clockTime(seconds), description: "Simulated elapsed time" };
}

export type ViewMode = "player" | "admin" | "world" | "supply";
export type DashboardTab = "live" | "cast" | "story" | "technical" | "village-policy" | "settings";

export function dashboardTab(hash: string, admin: boolean, hasEpisode: boolean): DashboardTab {
  const requested = hash.replace(/^#/, "").toLowerCase();
  if (requested === "cast" || requested === "story" ||
    requested === "live" || (!admin && requested === "play") ||
    (admin && (requested === "technical" || requested === "village-policy" || requested === "settings"))) {
    return requested === "play" ? "live" : requested as DashboardTab;
  }
  return hasEpisode ? "live" : "cast";
}

export function viewMode(search: string): ViewMode {
  const view = new URLSearchParams(search).get("view");
  return view === "admin" || view === "world" || view === "supply" ? view : "player";
}

export function isAdminView(search: string): boolean {
  return viewMode(search) === "admin";
}

export function viewNavigation(current: ViewMode): string {
  return `<nav class="view-navigation" aria-label="View">
    ${(["player", "world", "supply", "admin"] as const).map(view =>
      `<a class="button subtle" href="${view === "player" ? "/" : `/?view=${view}`}"${view === current ? ' aria-current="page"' : ""}>${view === "supply" ? "Town supply" : `${view[0].toUpperCase() + view.slice(1)} view`}</a>`).join("")}
  </nav>`;
}

export function dashboardHeader(current: ViewMode, eyebrow: string, title: string, tools = ""): string {
  return `<header class="dashboard-masthead">
    <div class="masthead-title"><a class="brand" href="/" aria-label="NPCSIM home">NPCSIM<span>·</span></a><div><span class="eyebrow">${escape(eyebrow)}</span><h1>${escape(title)}</h1></div></div>
    <div class="header-right">${tools}${viewNavigation(current)}</div>
  </header>`;
}

export function dashboardWorldSummary(title: string, details: string, clock: string, titleId?: string): string {
  return `<section class="monitor-summary" aria-label="World summary">
    <div><span class="eyebrow">WORLD</span><h2${titleId ? ` id="${escape(titleId)}"` : ""}>${escape(title)}</h2>${details}</div>
    ${clock}
  </section>`;
}

export function trackDashboardHeader(app: HTMLElement): void {
  const shell = app.querySelector<HTMLElement>(".command-shell");
  if (!shell) return;
  const update = () => app.style.setProperty("--dashboard-header-height", `${shell.offsetHeight}px`);
  update();
  const observer = new ResizeObserver(update);
  observer.observe(shell);
  window.addEventListener("pagehide", () => observer.disconnect(), { once: true });
}

export function canAskQuestions(status: string, hasObservation: boolean): boolean {
  return hasObservation && [
    "ready", "running", "paused", "succeeded", "blocked", "stalled",
    "budget_exceeded", "cancelled", "error"
  ].includes(status);
}

export function canExtendBudget(status: string, hasWorld: boolean): boolean {
  return hasWorld && ["ready", "running", "paused", "budget_exceeded"].includes(status);
}

export function canReopenAdventure(run: State["run"], hasWorld: boolean): boolean {
  return Boolean(hasWorld && run?.goal.type === "village_restored" && (
    (run.status === "succeeded" && run.completion_analysis?.status === "completed"
      && run.completion_analysis.verdict === "fail") ||
    (run.status === "blocked"
      && run.reason.startsWith(`${run.dungeon_master?.config.name ?? "Dungeon Master"} rules that the team must admit defeat.`)
      && run.verification?.status === "active"
      && run.verification.evidence?.completion_scope === "full_village_day"
      && run.verification.evidence.daily_requirements_recoverable === true) ||
    (run.status === "error" && (run.reason.startsWith("Timed out while waiting for response to ClientRequest.")
      || run.reason === "TimeoutError"))));
}

export function budgetSummary(state: State): string {
  const budgets = state.run?.config?.budgets ?? state.config.budgets;
  if (state.run?.run_until_complete) {
    return `${state.run.actions} actions · ${state.run.model_calls} calls · ` +
      `${(state.run.elapsed_seconds / 60).toFixed(1)} active minutes · No budget limits` +
      (state.world?.village ? " · Cycle ends at midnight" : " · Until verified completion");
  }
  return `${state.run?.actions ?? 0} / ${budgets.max_actions} actions · ` +
    `${state.run?.model_calls ?? 0} / ${budgets.max_model_calls} calls · ` +
    `${((state.run?.elapsed_seconds ?? 0) / 60).toFixed(1)} / ${(budgets.max_seconds / 60).toFixed(1)} active minutes`;
}

export function playerCommandVisible(command: string | undefined, status: string): boolean {
  switch (command) {
    case "start": return true;
    case "reset": return status !== "idle";
    case "run": return status === "ready";
    case "pause": return status === "running";
    case "resume": return status === "paused";
    case "reopen": return status === "succeeded" || status === "error" || status === "blocked";
    default: return false;
  }
}

export function playerPrimaryCommand(status: string, canReopen = false): string | undefined {
  if (canReopen && playerCommandVisible("reopen", status)) return "reopen";
  return ({ idle: "start", ready: "run", running: "pause", paused: "resume" } as Record<string, string>)[status];
}

export function adminTriage(state: State, events: readonly RunEvent[]) {
  const run = state.run;
  if (!run) return null;
  const lastFailure = events.reduce<RunEvent | undefined>((latest, event) => {
    if (event.run_id !== run.id || event.kind !== "action_result" || (latest && latest.sequence >= event.sequence)) return latest;
    const payload = event.payload as { outcome?: unknown } | null;
    return payload?.outcome === "REJECTED" ? event : latest;
  }, undefined);
  const budgets = run.config?.budgets ?? state.config.budgets;
  const usage = [
    { label: "Actions", used: run.actions, limit: budgets.max_actions },
    { label: "Model calls", used: run.model_calls, limit: budgets.max_model_calls },
    { label: "Active time", used: run.elapsed_seconds, limit: budgets.max_seconds },
  ].filter(item => item.limit > 0).sort((a, b) => b.used / b.limit - a.used / a.limit);
  return { lastFailure, pressure: run.run_until_complete ? undefined : usage[0] };
}

export function adventureStatus(status: string,
  analysis?: Pick<CompletionAnalysis, "status" | "verdict">): string {
  if (analysis?.status === "pending" || analysis?.status === "running") return "Reviewing the outcome";
  if (analysis?.status === "error") return "Outcome review unavailable";
  if (analysis?.status === "completed" && analysis.verdict) {
    return analysis.verdict === "pass" ? "Adventure successful" : "Adventure not successful";
  }
  const labels: Record<string, string> = {
    idle: "Choose your adventure", ready: "Ready to play", running: "Adventure in progress",
    paused: "Paused", succeeded: "Goal reached", blocked: "Admitted defeat",
    stalled: "Progress stopped", cancelled: "Adventure stopped",
    budget_exceeded: "Adventure limit reached", error: "Adventure interrupted"
  };
  return labels[status] ?? status.replaceAll("_", " ");
}
