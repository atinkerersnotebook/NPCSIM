import type { CompletionAnalysis, State } from "./contracts";
import { clockTime } from "./bakery-view.ts";

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

export type ViewMode = "player" | "admin" | "world";

export function viewMode(search: string): ViewMode {
  const view = new URLSearchParams(search).get("view");
  return view === "admin" || view === "world" ? view : "player";
}

export function isAdminView(search: string): boolean {
  return viewMode(search) === "admin";
}

export function viewNavigation(current: ViewMode): string {
  return `<nav class="view-navigation" aria-label="View">
    ${(["player", "world", "admin"] as const).map(view =>
      `<a class="button subtle" href="${view === "player" ? "/" : `/?view=${view}`}"${view === current ? ' aria-current="page"' : ""}>${view[0].toUpperCase() + view.slice(1)} view</a>`).join("")}
  </nav>`;
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
    (run.status === "error" && run.reason.startsWith("Timed out while waiting for response to ClientRequest."))));
}

export function budgetSummary(state: State): string {
  const budgets = state.run?.config?.budgets ?? state.config.budgets;
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
    case "reopen": return status === "succeeded" || status === "error";
    default: return false;
  }
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
