import type { State } from "./contracts.ts";
import { slotLabel } from "./agent-profiles.ts";

type Trade = NonNullable<NonNullable<NonNullable<State["world"]>["village"]>["dashboard"]>["trades"][number];

export function tradeNote(state: State, trade: Trade): string {
  const visitor = state.world?.village?.dashboard?.visitors.find(item => item.id === trade.visitor)?.name
    ?? state.world?.entities[trade.visitor]?.name ?? trade.visitor;
  const resident = state.run?.agent_profiles?.[trade.resident]?.name
    ?? state.world?.entities[trade.resident]?.name
    ?? slotLabel(trade.resident);
  const goods = `${trade.quantity} ${trade.resource}`;
  const coins = `${trade.payment} coins`;
  if (trade.direction === "import") return `${resident} bought ${goods} from ${visitor} for ${coins} (paid and delivered).`;
  if (trade.direction === "export" || trade.direction === "order") {
    return `${visitor} bought ${goods} from ${resident} for ${coins} (paid and transferred).`;
  }
  return `${visitor} paid ${resident} ${coins} for ${goods} (${trade.direction} settled).`;
}
