import type { RunEvent, State, VillageLife, VillageObservation } from "./contracts";
import { villageClockText } from "./view-mode.ts";

const escape = (text: string) => text.replaceAll("&", "&amp;").replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#39;");
const label = (text: string) => escape(text.replaceAll("_", " "));
const time = (minute: number) => `${String(Math.floor(minute % 1440 / 60)).padStart(2, "0")}:${String(minute % 60).padStart(2, "0")}`;
const stock = (values: Record<string, number>) => Object.entries(values)
  .map(([resource, amount]) => `<span class="fact-chip">${label(resource)}: ${amount}</span>`).join("")
  || '<span class="muted">None</span>';

function lifeMarkup(life: VillageLife, minute: number): string {
  const at = (value: number) => `day ${Math.floor(value / 1440) + 1} ${time(value)}`;
  const meals = Object.values(life.meals).filter(meal =>
    Math.floor(meal.due / 1440) === Math.floor(minute / 1440) || meal.status === "pending");
  const day = Math.floor(minute / 1440);
  const tired = life.fatigue_days?.includes(day) && life.tea_day !== day;
  const hunger = life.meals[`${day - 1}:daily`]?.status === "missed";
  const factor = (hunger ? 2 : 1) * (tired ? 2 : 1);
  return `<div class="village-daily-life">
    <p><b>Rest:</b> ${life.sleeping && life.sleep_until !== null ? `Sleeping until ${at(life.sleep_until)}` : `Awake since ${at(life.last_woke)}`}</p>
    <p><b>Daily meal:</b> 19:00 at the tavern / <b>Hunger:</b> ${life.hungry ? "Hungry" : "Not hungry"} / missed meals: ${life.missed_meals}</p>
    <p><b>Work cooldown:</b> ${factor === 1 ? "Normal" : `${factor}x (${[hunger ? "missed yesterday's meal" : "", tired ? "tiredness" : ""].filter(Boolean).join(" + ")})`}</p>
    ${life.urgent_project && life.urgent_until !== null && life.urgent_until !== undefined && minute < life.urgent_until ?
      `<p><b>Explicit bedtime override:</b> ${label(life.urgent_project)} until ${at(life.urgent_until)}</p>` : ""}
    ${meals.map(meal => `<p>${label(meal.kind)}: ${escape(meal.status)}${meal.source ? ` / ${escape(meal.source)}` : ""}</p>`).join("")}
    ${life.work ? `<p><b>Committed batch:</b> Must finish at ${at(life.work.finishes_at)} before leaving, eating or sleeping.</p>` : ""}
  </div>`;
}

export function villageMarkup(village: VillageObservation): string {
  return `<section class="village-panel" aria-label="Millhaven living village">
    <div class="network-heading"><span class="eyebrow">MILLHAVEN / DAY ${village.day}</span>
      <strong>${escape(village.time)}</strong><span>${village.coins} coins</span></div>
    <p class="muted">${escape(village.policy)}</p>
    ${village.personal?.life ? lifeMarkup(village.personal.life, village.minute) : ""}
    ${village.notices?.length ? `<details><summary>Public bell notices</summary>
      ${village.notices.map(entry => `<p>${time(entry.minute)} / ${label(entry.actor)} / ${escape(villageClockText(entry.result))}</p>`).join("")}</details>` : ""}
    ${village.personal?.routes?.length ? `<details><summary>Shortest known routes (verify conditions along the way)</summary>
      ${village.personal.routes.map(route => `<p>${label(route.destination.replaceAll("-", " "))}: ${route.steps} steps /
        ${route.path.map(place => label(place.replaceAll("-", " "))).join(" → ")}</p>`).join("")}</details>` : ""}
    ${village.personal?.stock_report_at !== null && village.personal?.stock_report_at !== undefined ?
      `<details><summary>Stocks last read on the noticeboard at ${time(village.personal.stock_report_at)}</summary>
      ${Object.entries(village.personal.stock_report ?? {}).map(([place, goods]) =>
        `<p>${label(place)}: ${stock(goods)}</p>`).join("")}</details>` : ""}
    <h3>People nearby</h3>
    <div class="village-residents">${village.residents.map(person =>
      `<article class="village-card"><strong>${escape(person.name)}</strong>
        <span class="tag">${escape(person.occupation)}</span><p>${escape(villageClockText(person.activity))}</p></article>`).join("")
      || '<p class="muted">Nobody nearby. Explore a workplace or meet the villagers at the tavern in the evening.</p>'}</div>
    <h3>Local resource stock</h3><div>${stock(village.stocks)}</div>
    <details><summary>${village.personal ? "Your shipment cargo" : "Your purchased resources"}</summary>${stock(village.resource_inventory)}</details>
    ${village.personal ? `<details><summary>Your private knowledge & schedule</summary>
      <p>${escape(village.personal.personality)}</p>
      <p>Scheduled: ${escape(village.personal.routine.activity)} / ${label(village.personal.routine.location)}</p>
      ${Object.values(village.personal.beliefs).map(belief => `<p>${label(belief.subject)}:
        ${label(belief.location ?? "unknown")} / ${escape(belief.source)} (${Math.round(belief.confidence * 100)}%)</p>`).join("")}
    </details>` : ""}
    ${village.personal ? "" : `
    <h3>Your requests</h3>
    ${village.tasks.map(task => `<article class="village-card">
      <strong>${label(task.kind)}</strong>
      <span class="tag ${task.status === "completed" ? "green" : "amber"}">${label(task.status)}</span>
      <p>${escape(task.assignee)} / ${label(task.step)}</p>
      ${task.blocker ? `<p class="village-blocker">${escape(task.blocker)}</p>` : ""}</article>`).join("")
      || '<p class="muted">Ask a nearby specialist for help. Acceptance is not completion.</p>'}`}
    <details class="village-events"><summary>Events you witnessed (${village.events.length} recent)</summary>
      ${village.events.map(entry => `<p><small>Day ${Math.floor(entry.minute / 1440) + 1}
        ${String(Math.floor(entry.minute % 1440 / 60)).padStart(2, "0")}:${String(entry.minute % 60).padStart(2, "0")}
        / ${escape(entry.actor.replace(/\b\w/g, char => char.toUpperCase()))}</small><br>${escape(villageClockText(entry.result))}</p>`).join("")
        || '<p class="muted">No witnessed events yet.</p>'}
    </details>
  </section>`;
}

export function villageDashboardMarkup(state: State, events: RunEvent[]): string {
  const world = state.world;
  const village = world?.village;
  if (!world || !village || !Object.keys(village.roster).length) return "";
  const name = (id: string) => state.run?.agent_profiles?.[id]?.name
    ?? world.entities[id]?.name ?? id.replaceAll("-", " ").replace(/\b\w/g, char => char.toUpperCase());
  const named = (id: string) => escape(name(id));
  const latest = new Map<string, { action: string; reason: string }>();
  for (const event of events) {
    const value = event.payload;
    if (event.kind === "decision" && value && typeof value === "object"
      && "action_id" in value && typeof value.action_id === "string"
      && "justification" in value && typeof value.justification === "string") {
      latest.set(event.actor_id, { action: value.action_id, reason: value.justification });
    }
  }
  const cards = Object.entries(village.roster).map(([id, resident]) => {
    const actor = world.actors[id];
    const person = village.residents[id];
    if (!person) throw new Error(`Missing tracked village resident: ${id}`);
    if (!actor) return `<article class="village-card" data-village-resident="${escape(id)}">
      <strong>${named(id)}</strong><span class="tag">${escape(resident.occupation)}</span>
      <p>Initializing actor: awaiting world binding.</p></article>`;
    const saved = state.run?.agents?.[id];
    const memory = state.observation?.self.actor_id === id ? state.memory : saved?.memory;
    const focus = typeof memory?.subgoal === "string" ? memory.subgoal : "";
    const plan = Array.isArray(memory?.plan) ? memory.plan.filter((entry): entry is string => typeof entry === "string") : [];
    const routine = [...resident.schedule].reverse().find(entry => entry.minute <= village.minute % 1440);
    const decision = latest.get(id);
    const lastResult = Array.isArray(memory?.recent) ? memory.recent.at(-1) : undefined;
    const outcome = lastResult && typeof lastResult === "object" && "explanation" in lastResult
      && typeof lastResult.explanation === "string" ? lastResult.explanation : "";
    const active = state.run?.actor_id === id && (state.run.status === "running" || state.in_flight);
    return `<article class="village-card${active ? " taking-turn" : ""}" data-village-resident="${escape(id)}">
      <strong>${named(id)}</strong><span class="tag">${escape(resident.occupation)}</span>
      ${active ? '<span class="tag green">Current turn</span>' : ""}
      <p><b>Location:</b> ${named(actor.location_id)}</p>
      <p><b>Activity:</b> ${escape(villageClockText(person.activity))}</p>
      ${person.life ? lifeMarkup(person.life, village.minute) : ""}
      <p><b>Carrying:</b> ${actor.inventory.map(named).join(", ") || "Empty hands"}</p>
      <p><b>Cargo:</b> ${stock(person.cargo)} / <b>Coins:</b> ${village.coins[id]}</p>
      ${person.timber_route ? "<p>Automatic timber foot route active</p>" : ""}
      ${person.shipments?.length ? `<p>Foot deliveries: ${person.shipments.map(item =>
        `${item.quantity} ${label(item.resource)} to ${named(item.destination)}`).join("; ")}</p>` : ""}
      ${person.fertilized_harvests ? `<p>Fertilized harvests remaining: ${person.fertilized_harvests}</p>` : ""}
      <p><b>Contribution:</b> ${village.role_contributions[id] ?? 0} verified work actions</p>
      <p><b>Scheduled:</b> ${routine ? `${escape(routine.activity)} at ${named(routine.location)}` : "No scheduled activity"}</p>
      <p><b>Focus:</b> ${escape(focus || "No decision recorded yet")}</p>
      ${decision ? `<p><b>Last decision:</b> ${label(decision.action)} - ${escape(villageClockText(decision.reason))}</p>` : ""}
      ${outcome ? `<p><b>Last outcome:</b> ${escape(villageClockText(outcome))}</p>` : ""}
      <details><summary>Goals, job & schedule</summary>
        <p>${escape(saved?.role ?? "")}</p><p>${escape(resident.personality)}</p>
        <p><b>Skills:</b> ${resident.skills.map(label).join(", ")}</p>
        ${person.life ? `<p>Wake: ${time(resident.wake_minute)} / bedtime: ${time(resident.bed_minute)}.
          Interruptible work can pause without losing progress; started baking must finish.</p>` : ""}
        <p><b>Plan:</b> ${plan.map(escape).join("; ") || "No plan recorded"}</p>
        ${resident.job ? `<p>Job at ${named(resident.job.location)} / every ${resident.job.interval} minutes.
          Next batch: day ${Math.floor(person.next_work / 1440) + 1} ${time(person.next_work)}.</p>
          ${!resident.job.interruptible ? `<p>Batch duration: ${resident.job.duration} minutes; must finish once started.</p>` : ""}
          <p>Inputs: ${stock(resident.job.inputs)} / outputs: ${stock(resident.job.outputs)}</p>` : ""}
        ${resident.schedule.map(entry => `<p>${time(entry.minute)} / ${named(entry.location)} / ${escape(entry.activity)}</p>`).join("")}
      </details>
      <details><summary>Private beliefs (${Object.keys(person.beliefs).length})</summary>
        ${Object.values(person.beliefs).map(belief => `<p>${named(belief.subject)}:
          ${belief.location ? named(belief.location) : "Unknown"} / ${escape(belief.source)}
          / ${Math.round(belief.confidence * 100)}% / day ${Math.floor(belief.learned_at / 1440) + 1} ${time(belief.learned_at)}</p>`).join("")
          || '<p class="muted">No recorded beliefs</p>'}
      </details>
      <details><summary>Relationships & witnessed memories (${person.memories.length})</summary>
        ${Object.entries(person.trust).map(([other, trust]) => `<p>${named(other)}: trust ${trust}</p>`).join("")}
        ${person.memories.slice(-12).map(index => {
          const entry = village.events[index];
          if (!entry) throw new Error(`Invalid village memory reference: ${index}`);
          return `<p>${time(entry.minute)} / ${named(entry.actor)} / ${escape(villageClockText(entry.result))}</p>`;
        }).join("") || '<p class="muted">No witnessed events yet</p>'}
      </details>
    </article>`;
  }).join("");
  return `<div class="network-heading"><span class="eyebrow">MILLHAVEN EXPANDED / DAY ${Math.floor(village.minute / 1440) + 1}</span>
      <strong>${time(village.minute)}</strong><span>${Object.keys(village.roster).length} AI villagers</span></div>
    <h2>Village-wide tracking</h2>
    <p class="muted">Operator overview: private beliefs, plans and memories are shown to you, never shared with other NPCs.
      Schedules describe intentions; locations and inventories show actual world state. Select any member in Ask an NPC for a private question.</p>
    <div class="village-progress">
      <span class="fact-chip">Shovel: ${village.shovel_recovered ? "recovered" : "outstanding"}</span>
      <span class="fact-chip">Well: ${village.well_dug ? "completed" : "unfinished"}</span>
      <span class="fact-chip">Mill: ${village.repaired ? "repaired" : village.breakdown ? "broken" : "running"}</span>
      <span class="fact-chip">${village.wagon ? `Bread delivered (no repair prerequisite): ${village.bread_deliveries}` : `Bread deliveries after repair: ${village.bread_after_repair}`}</span>
      <span class="fact-chip">Residents contributed: ${Object.values(village.role_contributions).filter(count => count > 0).length}/${Object.keys(village.roster).length}</span>
      ${Object.entries(village.project_contributions).map(([project, crew]) =>
        `<p>${label(project)}: ${Math.min(6, Object.values(crew).reduce((sum, n) => sum + n, 0))}/6 contributions /
          ${Object.entries(crew).map(([id, count]) => `${named(id)} ${count * 5} work min`).join(", ")}</p>`).join("")}
    </div>
    ${village.wagon ? `<section aria-label="Village wagon">
      <h3>Six-unit wagon / ${village.wagon.repaired ? "Working" : "Awaiting repair"}</h3>
      <p>Location: ${world.entities.wagon?.location ? named(world.entities.wagon.location) : "Unknown"} /
        ${village.wagon.automatic ? "Automatic supply circuit active" : "Parked"} /
        ${village.wagon.loads.reduce((total, item) => total + item.quantity, 0)}/6 units</p>
      ${world.entities["work-horse"] ? `<p>Horse: ${village.wagon.hitched ? "Hitched" : "Unhitched"} /
        ${world.entities["work-horse"].location ? named(world.entities["work-horse"].location) : "Unknown"} /
        ${village.horse_fed_day === Math.floor(village.minute / 1440) ? "Fed today" : "Needs feeding"}</p>
        <p>Driver: ${village.wagon.driver ? named(village.wagon.driver) : "None"} / roads only</p>` : ""}
      ${village.wagon.loads.map(item => `<p>${item.quantity} ${label(item.resource)} → ${named(item.destination)}</p>`).join("")}
      ${village.wagon.blocker ? `<p>${escape(village.wagon.blocker)}</p>` : ""}
    </section>` : ""}
    ${Object.keys(village.deadlines ?? {}).length ? `<section aria-label="Village deadlines"><h3>Goal deadlines</h3>
      ${Object.entries(village.deadlines ?? {}).map(([goal, deadline]) => `<p>${label(goal)} /
        day ${Math.floor(deadline.due / 1440) + 1} ${time(deadline.due)} /
        ${deadline.completed_at !== null ? deadline.overdue ? "Completed late" : "Completed on time" :
          deadline.overdue ? "Overdue; recovery allowed" : "Pending"}</p>`).join("")}</section>` : ""}
    ${village.notices.length ? `<details><summary>Public noticeboard & bell</summary>
      ${village.notices.map(entry => `<p>${time(entry.minute)} / ${named(entry.actor)} / ${escape(villageClockText(entry.result))}</p>`).join("")}</details>` : ""}
    <div class="village-roster">${cards}</div>
    <details><summary>Village stock & event ledger</summary>
      ${Object.entries(village.stocks).map(([location, values]) => `<p><b>${named(location)}:</b> ${stock(values)}</p>`).join("")}
      ${village.events.slice(-24).map(entry => `<p>${time(entry.minute)} / ${named(entry.actor)} / ${escape(villageClockText(entry.result))}</p>`).join("")}
    </details>
    ${village.stock_history?.length ? `<details aria-label="Hourly stock records"><summary>Hourly workplace stock records (${village.stock_history.length})</summary>
      ${village.stock_history.slice(-24).map(record => `<p><b>Day ${Math.floor(record.minute / 1440) + 1} ${time(record.minute)}</b></p>
        ${Object.entries(record.stocks).map(([place, values]) => `<p>${named(place)}: ${stock(values)}</p>`).join("")}`).join("")}
    </details>` : ""}
    ${village.stock_lots ? `<details><summary>Batch freshness and spoilage</summary>
      ${Object.entries(village.stock_lots).map(([place, resources]) => Object.entries(resources)
        .filter(([, lots]) => lots.some(lot => lot.expires_at !== null))
        .map(([resource, lots]) => `<p>${named(place)} / ${label(resource)}:
          ${lots.flatMap(lot => lot.expires_at === null ? [] : [`${lot.quantity} units expire day
            ${Math.floor(lot.expires_at / 1440) + 1} ${time(lot.expires_at)}`]).join("; ")}</p>`).join("")).join("")}
    </details>` : ""}`;
}
