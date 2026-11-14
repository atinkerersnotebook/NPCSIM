import type { State } from "./contracts.ts";
import { escape } from "./html.ts";

export interface DetailActor {
  id: string;
  name: string;
  locationId: string;
  current: boolean;
  visitor?: boolean;
  activity?: string;
}

export function actorDetailsMarkup(state: State, agent: DetailActor): string {
  const world = state.world;
  const village = world?.village;
  const actor = world?.actors[agent.id];
  const resident = village?.roster[agent.id];
  const place = world?.entities[actor?.location_id ?? agent.locationId]?.name
    ?? ((actor?.location_id ?? agent.locationId).replaceAll("-", " ") || "Unknown");
  const facts: [string, string][] = [
    ["Location", `${place}${agent.current ? "" : " (last observed / not live)"}`]
  ];
  if (resident) facts.push(["Occupation", resident.occupation]);
  if (agent.activity) facts.push(["Activity", agent.activity]);
  if (agent.visitor && agent.current) {
    const visitor = village?.dashboard?.visitors.find(item => item.id === agent.id);
    if (visitor) {
      if (visitor.stock) facts.push(["Carried goods", Object.entries(visitor.stock)
        .filter(([, quantity]) => quantity > 0)
        .map(([item, quantity]) => `${item.replaceAll("-", " ")} ×${quantity}`).join(", ") || "None"]);
      if (visitor.coins !== undefined) facts.push(["Money", `${visitor.coins} coins`]);
    }
  }
  if (agent.current && !agent.visitor && actor) {
    const items = actor.inventory.map(id => world?.entities[id]?.name ?? id);
    const cargo = village?.residents[agent.id]?.cargo ?? {};
    items.push(...Object.entries(cargo).filter(([, quantity]) => quantity > 0)
      .map(([item, quantity]) => `${item.replaceAll("-", " ")} ×${quantity} (cargo)`));
    facts.push(["Carried", items.join(", ") || "None"]);
  }
  if (agent.current && village) {
    const locationId = actor?.location_id ?? agent.locationId;
    const stock = village.stocks[locationId];
    if (stock) facts.push([`Stock at ${place} (site, not personal)`,
      Object.entries(stock).map(([item, quantity]) => `${item.replaceAll("-", " ")}: ${quantity}`).join(", ") || "None"]);
    if (!agent.visitor && actor) {
      const coins = village.coins[agent.id];
      if (coins !== undefined) facts.push(["Money", `${coins} coins`]);
    }
  }
  return `<dl class="actor-details-facts">${facts.map(([label, value]) =>
    `<dt>${escape(label)}</dt><dd>${escape(value)}</dd>`).join("")}</dl>`;
}
