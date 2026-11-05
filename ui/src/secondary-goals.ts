import { z } from "zod";
import { secondaryConditionSchema } from "./contracts.ts";

export function conditionText(
  condition: z.infer<typeof secondaryConditionSchema>,
  entityNames: Record<string, { name: string }>,
  actorNames: Record<string, { name: string }>
): string {
  const target = entityNames[condition.target]?.name ?? condition.target;
  const who = condition.actor_id ? actorNames[condition.actor_id]?.name ?? condition.actor_id :
    condition.quantifier === "all" ? "Every assigned NPC" : "Any assigned NPC";
  switch (condition.kind) {
    case "discovered": return `${who} has discovered ${target} through observation.`;
    case "carrying": return `${who} is carrying ${target}.`;
    case "at_location": return `${who} is at ${target}.`;
    case "read": return `${who} has read ${target}.`;
    case "item_at": return `${target} is placed at ${entityNames[condition.location_id ?? ""]?.name ?? condition.location_id}.`;
    case "opened": return `${target} is open.`;
    case "unlocked": return `${target} is unlocked.`;
  }
}
