import type { CompletionAnalysis } from "./contracts";

export function isAdminView(search: string): boolean {
  return new URLSearchParams(search).get("view") === "admin";
}

export function playerCommandVisible(command: string | undefined, status: string): boolean {
  switch (command) {
    case "start": return true;
    case "reset": return status !== "idle";
    case "run": return status === "ready";
    case "pause": return status === "running";
    case "resume": return status === "paused";
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
