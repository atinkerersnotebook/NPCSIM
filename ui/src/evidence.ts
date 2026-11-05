import type { RunEvent } from "./contracts";

export interface EvidenceExplanation {
  id: string;
  title: string;
  explanation: string;
  details: string[];
  actorId?: string;
  cycle?: number;
}

function object(value: unknown): Record<string, unknown> | undefined {
  return typeof value === "object" && value !== null && !Array.isArray(value) ?
    value as Record<string, unknown> : undefined;
}

function text(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value : undefined;
}

function label(value: string): string {
  return value.replace(/[_-]+/g, " ");
}

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function findingsText(value: unknown): string | undefined {
  if (!Array.isArray(value)) return undefined;
  return value.map(finding => text(object(finding)?.reason)).filter(Boolean).join(" ") || undefined;
}

function verificationDetails(value: unknown): string[] {
  const proof = object(value);
  if (!proof) return [];
  const details: string[] = [];
  const missing = strings(proof.missing_required_skills);
  if (missing.length) details.push(`Missing required skills: ${missing.map(label).join(", ")}.`);
  if (Array.isArray(proof.impossible_challenges)) {
    for (const value of proof.impossible_challenges) {
      const challenge = object(value);
      if (text(challenge?.description)) {
        details.push(`Required work: ${challenge?.description}${text(challenge?.capability) ?
          ` (requires ${label(String(challenge?.capability))})` : ""}`);
      }
    }
  }
  const capabilities = object(proof.team_capabilities);
  if (capabilities) {
    for (const [actor, skills] of Object.entries(capabilities)) {
      details.push(`${label(actor)} has: ${strings(skills).map(label).join(", ") || "no recorded capabilities"}.`);
    }
  }
  const agents = object(proof.agents);
  if (agents) {
    for (const [actor, value] of Object.entries(agents)) {
      const check = object(value);
      const location = text(object(check?.evidence)?.location_id);
      details.push(`${label(actor)}: ${text(check?.status) ?? "no recorded status"}${location ?
        ` at ${label(location)}` : ""}.${text(check?.reason) ? ` ${check?.reason}` : ""}`);
    }
  }
  const work = object(proof.cooperative_work);
  if (work) {
    for (const [challenge, actor] of Object.entries(work)) {
      details.push(`${label(challenge)}: ${typeof actor === "string" ? `completed by ${label(actor)}` : "not completed"}.`);
    }
  }
  const checks = object(proof.checks);
  if (checks) {
    for (const [name, result] of Object.entries(checks)) {
      if (typeof result === "boolean") details.push(`${label(name)}: ${result ? "satisfied" : "not satisfied"}.`);
      else if (typeof result === "string" || typeof result === "number") details.push(`${label(name)}: ${result}.`);
    }
  }
  for (const [key, value] of Object.entries(proof)) {
    if (key === "actor_id") continue;
    if (typeof value === "string" && value) details.push(`${label(key)}: ${label(value)}.`);
    else if (typeof value === "number" || typeof value === "boolean") details.push(`${label(key)}: ${value}.`);
  }
  return details;
}

export function evidenceReferences(value: unknown): string[] {
  const ids = new Set<string>();
  function collect(value: unknown): void {
    if (Array.isArray(value)) {
      value.forEach(collect);
      return;
    }
    const record = object(value);
    if (!record) return;
    for (const [key, child] of Object.entries(record)) {
      if (key === "evidence_ids") strings(child).forEach(id => ids.add(id));
      else if (key === "evidence_id" && typeof child === "string") ids.add(child);
      else collect(child);
    }
  }
  collect(value);
  return [...ids];
}

export function explainEvidence(ids: readonly string[], events: readonly RunEvent[],
  story: readonly { id: string; text: string }[] = []): EvidenceExplanation[] {
  const byId = new Map(events.map(event => [event.id, event]));
  const paragraphs = new Map(story.map(paragraph => [paragraph.id, paragraph.text]));
  return [...new Set(ids)].map(id => {
    const event = byId.get(id);
    const base = { id, details: [], ...(event ? { actorId: event.actor_id, cycle: event.cycle } : {}) };
    const paragraph = paragraphs.get(id);
    if (paragraph) return { ...base, title: "Recorded story evidence", explanation: paragraph };
    if (!event) return {
      ...base, title: "Evidence record unavailable",
      explanation: "This reference is not present in the loaded episode or its reviewed story. Its contents cannot be explained."
    };
    const payload = object(event.payload);
    switch (event.kind) {
      case "action_result":
        return { ...base, title: `World action result: ${label(text(payload?.outcome) ?? "unknown outcome")}`,
          explanation: text(payload?.explanation) ?? "No explanation was recorded for this action result." };
      case "verification":
      case "completion_verification": {
        const status = text(payload?.status);
        const explanation = text(payload?.reason) ?? (status === "achieved" ?
          "The world verified that the required goal conditions were satisfied." : status === "active" ?
          "The world reports that the goal has not yet been achieved." :
          `The world reports the goal as ${status ?? "unknown"}; no reason was recorded.`);
        return { ...base, title: `${event.kind === "completion_verification" ? "Final world check" : "World goal check"}: ${status ?? "unknown"}`,
          explanation, details: verificationDetails(payload?.evidence) };
      }
      case "observation":
        return { ...base, title: "Recorded world observation",
          explanation: text(payload?.narrative) ?? "No narrative was recorded for this observation." };
      case "secondary_goal_achieved":
        return { ...base, title: `Side goal completed: ${text(payload?.title) ?? "Unnamed side goal"}`,
          explanation: text(payload?.reason) ?? "The world verified every side-goal completion condition.",
          details: text(payload?.verification_revision) ? [`Verified world revision: ${payload?.verification_revision}.`] : [] };
      case "validation":
      case "dungeon_master_validation":
        return { ...base, title: payload?.valid === true ? "Schema validation passed" : "Validation rejected",
          explanation: text(payload?.error) ?? (payload?.valid === true ?
            `The proposed ${label(text(payload?.action_id) ?? "response")} passed schema validation. This is not proof of execution.` :
            "The proposed response failed validation; no specific reason was recorded.") };
      case "action_request":
        return { ...base, title: "Requested action",
          explanation: `The actor requested ${label(text(payload?.action_id) ?? "an unspecified action")}. This records an attempt, not a confirmed outcome.` };
      case "decision":
        return { ...base, title: "Recorded decision",
          explanation: text(payload?.justification) ?? "No explanation was recorded for this proposed decision." };
      default:
        return { ...base, title: label(event.kind),
          explanation: text(payload?.reason) ?? text(payload?.explanation) ?? text(payload?.message) ??
            findingsText(payload?.findings) ?? "No readable explanation was recorded for this event. Inspect its original technical JSON for details." };
    }
  });
}
