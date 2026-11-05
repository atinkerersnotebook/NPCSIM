import type { AgentProfile, SkillMode } from "./contracts";

export function slotLabel(id: string): string {
  return id === "npc-001" ? "Player" : id.replaceAll("_", " ");
}

export function profileText(profile: AgentProfile, name: "IDENTITY.md" | "SOUL.md" | "AGENTS.md"): string {
  const fallback = name === "IDENTITY.md" ? profile.identity : name === "SOUL.md" ? profile.personality :
    [...profile.operating_rules, ...profile.constraints].join("\n");
  return profile.documents[name]?.replace(/^#[^\r\n]*(?:\r?\n|$)\s*/, "")
    .replace(/^#{2,6}\s+/gm, "").trim() ?? fallback;
}

export function effectiveBody(profile: AgentProfile, scenarioId: string,
  skillMode: SkillMode = "scenario"): AgentProfile["body"] {
  return skillMode === "npc_only" ? profile.body : profile.scenario_bodies[scenarioId] ?? profile.body;
}

export function validateAssignments(slots: readonly string[], assignments: Record<string, string>,
  profiles: readonly AgentProfile[]): void {
  if (Object.keys(assignments).length !== slots.length ||
      slots.some(slot => !profiles.some(profile => profile.id === assignments[slot]))) {
    throw new Error("Choose an agent for every task slot.");
  }
  if (new Set(Object.values(assignments)).size !== slots.length) {
    throw new Error("Choose different agents for each task slot.");
  }
}
