import type { VillageObservation } from "./contracts";

export function villageMarkup(village: VillageObservation): string {
  const escape = (text: string) => text.replaceAll("&", "&amp;").replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#39;");
  const label = (text: string) => escape(text.replaceAll("_", " "));
  const stock = (values: Record<string, number>) => Object.entries(values)
    .map(([resource, amount]) => `<span class="fact-chip">${label(resource)}: ${amount}</span>`).join("")
    || '<span class="muted">No stock here</span>';
  return `<section class="village-panel" aria-label="Millhaven living village">
    <div class="network-heading"><span class="eyebrow">MILLHAVEN / DAY ${village.day}</span>
      <strong>${escape(village.time)}</strong><span>${village.coins} coins</span></div>
    <p class="muted">${escape(village.policy)}</p>
    <h3>People nearby</h3>
    <div class="village-residents">${village.residents.map(person =>
      `<article class="village-card"><strong>${escape(person.name)}</strong>
        <span class="tag">${escape(person.occupation)}</span><p>${escape(person.activity)}</p></article>`).join("")
      || '<p class="muted">Nobody nearby. Explore a workplace or meet the villagers at the tavern in the evening.</p>'}</div>
    <h3>Local resource stock</h3><div>${stock(village.stocks)}</div>
    <details><summary>Your purchased resources</summary>${stock(village.resource_inventory)}</details>
    <h3>Your requests</h3>
    ${village.tasks.map(task => `<article class="village-card">
      <strong>${label(task.kind)}</strong>
      <span class="tag ${task.status === "completed" ? "green" : "amber"}">${label(task.status)}</span>
      <p>${escape(task.assignee)} / ${label(task.step)}</p>
      ${task.blocker ? `<p class="village-blocker">${escape(task.blocker)}</p>` : ""}</article>`).join("")
      || '<p class="muted">Ask a nearby specialist for help. Acceptance is not completion.</p>'}
    <details class="village-events"><summary>Events you witnessed (${village.events.length} recent)</summary>
      ${village.events.map(entry => `<p><small>Day ${Math.floor(entry.minute / 1440) + 1}
        ${String(Math.floor(entry.minute % 1440 / 60)).padStart(2, "0")}:${String(entry.minute % 60).padStart(2, "0")}
        / ${escape(entry.actor)}</small><br>${escape(entry.result)}</p>`).join("")
        || '<p class="muted">No witnessed events yet.</p>'}
    </details>
  </section>`;
}
