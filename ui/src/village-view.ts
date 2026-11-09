import type { RunEvent, State, VillageLife, VillageObservation, VillageResearchView, VillageCommunication } from "./contracts";
import { villageClockText } from "./view-mode.ts";

const escape = (text: string) => text.replaceAll("&", "&amp;").replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#39;");
const label = (text: string) => escape(text.replaceAll("_", " "));
const time = (minute: number) => `${String(Math.floor(minute % 1440 / 60)).padStart(2, "0")}:${String(minute % 60).padStart(2, "0")}`;
const stock = (values: Record<string, number>) => Object.entries(values)
  .map(([resource, amount]) => `<span class="fact-chip">${label(resource)}: ${amount}</span>`).join("")
  || '<span class="muted">None</span>';

export function firewoodMarkup(firewood: NonNullable<VillageObservation["firewood"]>, operator = false): string {
  if (!firewood.enabled) return "";
  return `<section class="village-panel" aria-label="${operator ? "Operator firewood and household heating" : "Local firewood and household heating"}">
    <h3>${operator ? "Town" : "Local"} firewood &amp; household fires / ${label(firewood.season)}</h3>
    <p class="muted">${operator ? "Privileged world view; not shared NPC knowledge." : "Only fuel stocks and hearths physically here."}
      Cold homes have no health penalties. Ovens, cooking and forge work require actual fuel.</p>
    <p>Supplier: ${escape(firewood.supplier)} / 1 log → ${firewood.firewood_per_log} firewood /
      ${firewood.split_minutes} minutes of committed splitting with the real axe.</p>
    ${firewood.sites.map(site => `<p>${escape(site.location)}: ${site.stock} firewood /
      target ${site.reserve} / ${site.consumed} consumed /
      funded by ${escape(site.buyer)}${site.stock === 0 && site.reserve > 0 ? " / FUEL SHORTAGE" : ""}</p>`).join("")}
    ${firewood.households.map(hearth => `<article class="village-card"><strong>${escape(hearth.id)}</strong>
      <span class="tag ${hearth.status === "warm" ? "green" : hearth.status === "cold" ? "amber" : ""}">${label(hearth.status)}</span>
      <p>${hearth.members.map(escape).join(", ")} / ${hearth.occupied ? "Occupied" : "Unoccupied"}
        ${hearth.status === "warm" ? ` / warm until day ${Math.floor(hearth.warm_until / 1440) + 1} ${time(hearth.warm_until)}` : ""}</p>
    </article>`).join("")}
    <details><summary>Fuel and delivery rules</summary><p>${escape(firewood.policy)}</p></details>
  </section>`;
}

export function communicationMarkup(view: VillageCommunication, operator = false): string {
  const at = (minute: number) => `day ${Math.floor(minute / 1440) + 1} ${time(minute)}`;
  return `<section class="village-panel communication-panel" aria-label="${operator ? "Operator noticeboard" : "Personally learned notices"}">
    <h3>${operator ? "Operator: noticeboard, commitments & appointments" : "Your learned notices, commitments & appointments"}</h3>
    <p class="muted">${operator ? "Privileged world view. This is not shared NPC knowledge." :
      "Personally received snapshots only. Absence of a reply is not rejection; old versions may be stale."}</p>
    ${view.notices.map(notice => `<article class="communication-notice">
      <h4>${escape(notice.subject)} / ${label(notice.status)} / version ${notice.version}</h4>
      <p>${escape(notice.author)} → ${notice.recipients.map(escape).join(", ") || "Public"}${notice.private ? " / Private" : ""}</p>
      <p>${escape(notice.description)}</p>
      <p>${notice.quantity !== null ? `${notice.quantity} units / ` : ""}${notice.destination ? `${escape(notice.destination)} / ` : ""}
        ${notice.due_at !== null ? `due ${at(notice.due_at)} / ` : ""}updated ${at(notice.updated_at)}</p>
      ${notice.unit_price !== null ? `<p>Quoted price: ${notice.unit_price} coins per unit /
        delivery fee ${notice.delivery_fee ?? 0} coins.</p>` : ""}
      <p>Source: ${escape(notice.source)}${notice.learned_at !== null ? ` / received ${at(notice.learned_at)}` : ""}
        ${notice.last_verified_at !== null ? ` / last directly verified ${at(notice.last_verified_at)}` : ""}
        ${notice.confidence ? ` / ${escape(notice.confidence)} / ${notice.hops} relay hops` : ""}.
        ${escape(notice.freshness)}</p>
      ${notice.source_chain.length ? `<p>Provenance: ${notice.source_chain.map(escape).join(" → ")}</p>` : ""}
      ${notice.response ? `<p>Reply: ${escape(notice.response)}</p>` : ""}
      ${notice.commitment ? `<p><b>Commitment:</b> ${escape(notice.commitment.supplier)} → ${escape(notice.commitment.buyer)} /
        ${label(notice.commitment.status ?? "unknown")} / ${notice.commitment.delivered} units unloaded /
        ${notice.commitment.paid} coins paid. Acceptance alone transfers nothing.</p>` : ""}
      ${notice.appointment ? `<p><b>Appointment:</b> ${notice.appointment.members.map(escape).join(" &amp; ")} /
        ${escape(notice.appointment.place)} / ${at(notice.appointment.due_at)}–${time(notice.appointment.end_at)} /
        ${label(notice.appointment.status)} / confirmed by ${notice.appointment.accepted.map(escape).join(", ")}.
        Each worker must actually attend with supplies.</p>` : ""}
    </article>`).join("") || `<p class="muted">${operator ? "No notices posted yet." : "No notices received yet."}</p>`}
    <details><summary>Physical workplace inboxes</summary>
      ${view.boxes.map(box => `<p>${escape(box.owner)} / ${escape(box.place)} /
        ${operator ? `${box.notes ?? 0} physical notes` : box.here ? box.can_read ? "Here; yours to read" : "Here; owner-only reading" : "Visit in person to read"}.
        No remote delivery.</p>`).join("") || "<p>No boxes configured.</p>"}
    </details>
  </section>`;
}

function researchTableMarkup(projects: VillageResearchView[], name: (id: string) => string): string {
  return `<div class="maintenance-list research-list"><table><thead><tr>
    <th>Project</th><th>Researcher</th><th>Site</th><th>Research minutes</th>
    <th>Trials</th><th>Status</th><th>Recipe & use</th></tr></thead><tbody>
    ${projects.map(project => `<tr>
      <td><strong>${escape(project.title)}</strong><p>${escape(project.description)}</p></td>
      <td>${name(project.owner)}</td><td>${name(project.location)}</td>
      <td>${project.worked_minutes}/${project.required_minutes} min</td>
      <td>${project.successes}/${project.attempts} successful/total trials /
        ${project.required_successes} successes required</td>
      <td>${label(project.status)}${project.blocker ? ` / ${escape(project.blocker)}` : ""}
        ${project.discovered_at !== null ? `<p>Discovered day ${Math.floor(project.discovered_at / 1440) + 1}
          ${time(project.discovered_at)}</p>` : ""}</td>
      <td><b>${project.status === "discovered" ? "Unlocked recipe" : "Proposed recipe (not unlocked)"}:</b>
        ${Object.entries(project.inputs).map(([item, quantity]) => `${quantity} ${label(item)}`).join(", ") || "No inputs"}
        → 1 ${label(project.product)} / ${project.duration} min
        <p>Equipment: ${project.equipment.map(name).join(", ") || "None"}</p>
        <p>${project.kind === "food" || project.kind === "crop" ? "Use: 2 meal portions" :
          project.kind === "tool" ? "Use: repairs actual damaged objects" : "Use: ingredient for further recipes"}</p>
      </td></tr>`).join("")}</tbody></table></div>`;
}

function lifeMarkup(life: VillageLife, minute: number, breakfast = false): string {
  const at = (value: number) => `day ${Math.floor(value / 1440) + 1} ${time(value)}`;
  const meals = Object.values(life.meals).filter(meal =>
    Math.floor(meal.due / 1440) === Math.floor(minute / 1440) || meal.status === "pending");
  const day = Math.floor(minute / 1440);
  const tired = life.fatigue_days?.includes(day) && life.tea_day !== day;
  const hunger = Object.values(life.meals).some(meal =>
    Math.floor(meal.due / 1440) === day - 1 && meal.status === "missed");
  const factor = (hunger ? 2 : 1) * (tired ? 2 : 1);
  return `<div class="village-daily-life">
    <p><b>Rest:</b> ${life.sleeping && life.sleep_until !== null ? `Sleeping until ${at(life.sleep_until)}` : `Awake since ${at(life.last_woke)}`}</p>
    <p>${breakfast || Object.values(life.meals).some(meal => meal.kind === "breakfast")
      ? "<b>Meals:</b> Breakfast and 19:00 supper at the tavern" : "<b>Daily meal:</b> 19:00 at the tavern"} /
      <b>Hunger:</b> ${life.hungry ? "Hungry" : "Not hungry"} / missed meals: ${life.missed_meals}</p>
    <p><b>Work cooldown:</b> ${factor === 1 ? "Normal" : `${factor}x (${[hunger ? "missed yesterday's meal" : "", tired ? "tiredness" : ""].filter(Boolean).join(" + ")})`}</p>
    ${life.urgent_project && life.urgent_until !== null && life.urgent_until !== undefined && minute < life.urgent_until ?
      `<p><b>Explicit bedtime override:</b> ${label(life.urgent_project)} until ${at(life.urgent_until)}</p>` : ""}
    ${meals.map(meal => `<p>${label(meal.kind)}: ${escape(meal.status)} / due ${at(meal.due)}${meal.source ? ` / ${escape(meal.source)}` : ""}</p>`).join("")}
    ${life.work ? `<p><b>Committed batch:</b> Must finish at ${at(life.work.finishes_at)} before leaving, eating or sleeping.</p>` : ""}
  </div>`;
}

function livestockDetailsMarkup(livestock: NonNullable<VillageObservation["livestock"]>,
  minute: number, stocks: Record<string, number> = {}): string {
  const animals: [string, typeof livestock.cow][] = [["Cow", livestock.cow], ["Hens", livestock.hens]];
  const products = Object.fromEntries(Object.entries(stocks).filter(([resource]) =>
    ["milk", "eggs", "plant-scraps", "compost-material", "compost"].includes(resource)));
  return `<p>${escape(livestock.policy)}</p>
    <p>Keeper: ${escape(livestock.keeper)}</p>
    ${animals.map(([name, care]) => {
      const today = care.day === Math.floor(minute / 1440);
      return `<p>${name}: fed ${today && care.fed ? "yes" : "due"} /
        watered ${today && care.watered ? "yes" : "due"} / clean ${today && care.cleaned ? "yes" : "due"}
        / ${today ? care.progress : 0} carried work min</p>`;
    }).join("")}
    <p>Bucket water: ${livestock.bucket_water} care portions /
      milk sessions: ${escape(livestock.cow.milk_sessions.join(", ") || "none")} /
      eggs collected: ${livestock.hens.eggs}</p>
    <h4>Food recycling</h4>
    <p>Safe fresh plant-scraps can supplement chicken grain, never cow hay.
      Spoiled food becomes compost-material, never animal feed. Use real tools and timed
      composting at the farm; compost fertilizer is not animal manure.</p>
    ${Object.keys(products).length ? `<p>Farm food and recycling stocks: ${stock(products)}</p>` : ""}`;
}

export function villageMarkup(village: VillageObservation): string {
  return `<section class="village-panel" aria-label="Millhaven living village">
    <div class="network-heading"><span class="eyebrow">MILLHAVEN / DAY ${village.day}</span>
      <strong>${escape(village.time)}</strong><span>${village.coins} coins</span></div>
    <p class="muted">${escape(village.policy)}</p>
    ${village.communication ? communicationMarkup(village.communication) : ""}
    ${village.personal?.life ? lifeMarkup(village.personal.life, village.minute, village.personal.life.breakfast) : ""}
    ${village.livestock?.enabled ? `<details><summary>Farm livestock / 1 cow + ${village.livestock.hen_count} hens</summary>
      ${livestockDetailsMarkup(village.livestock, village.minute)}
    </details>` : ""}
    ${village.maintenance?.enabled ? `<details><summary>Your known town maintenance</summary>
      <p>${escape(village.maintenance.policy)}</p>
      ${village.maintenance.jobs.map(job => `<p>${escape(job.title)} / ${escape(job.assignee)} /
        ${label(job.location)} / ${job.worked_minutes}/${job.required_minutes} work min /
        ${escape(job.status)}${job.blocker ? ` / ${escape(job.blocker)}` : ""}</p>`).join("")
        || '<p class="muted">No maintenance assigned or observed here.</p>'}
    </details>` : ""}
    ${village.research?.enabled ? `<details><summary>Your known research projects & discoveries</summary>
      <p>${escape(village.research.policy)}</p>
      ${village.research.projects.length ? researchTableMarkup(village.research.projects,
        id => escape(village.residents.find(person => person.id === id)?.name ?? id.replaceAll("-", " ")))
        : '<p class="muted">No research projects assigned or observed here.</p>'}
    </details>` : ""}
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
    ${village.firewood ? firewoodMarkup(village.firewood) : ""}
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
      ${person.life ? lifeMarkup(person.life, village.minute,
        village.day_policy?.breakfast ?? village.dashboard?.day_policy?.breakfast) : ""}
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
    ${villageSystemsMarkup(state, events, state.observation?.village?.research?.enabled)}
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

export function villageSystemsMarkup(state: State, events: readonly RunEvent[] = [], researchEnabled = false): string {
  const dashboard = state.world?.village?.dashboard;
  const firewoodPanel = dashboard?.firewood ? firewoodMarkup(dashboard.firewood, true) : "";
  const livestock = state.observation?.village?.livestock ?? state.run?.observation?.village?.livestock;
  const livestockPanel = livestock?.enabled ? `<section class="village-panel" aria-label="Farm livestock and food recycling">
    <h3>Farm livestock / 1 cow + ${livestock.hen_count} hens</h3>
    ${livestockDetailsMarkup(livestock, state.world?.village?.minute
      ?? state.observation?.village?.minute ?? state.run?.observation?.village?.minute ?? 0,
      state.world?.village?.stocks.farm)}
  </section>` : "";
  if (!dashboard) return livestockPanel;
  const name = (id: string) => escape(state.run?.agent_profiles?.[id]?.name
    ?? state.world?.entities[id]?.name ?? id.replaceAll("-", " "));
  const at = (minute: number) => `day ${Math.floor(minute / 1440) + 1} ${time(minute)}`;
  const day = Math.floor((state.world?.village?.minute ?? 0) / 1440) + 1;
  const maintenance = dashboard.maintenance ?? [];
  const current = maintenance.filter(job => job.day === day || job.status !== "completed"
    || job.completed_at !== null && job.completed_at !== undefined
      && Math.floor(job.completed_at / 1440) + 1 === day);
  const maintenanceRow = (job: typeof maintenance[number]) => `<tr>
    <td>${escape(job.title)}${job.day < day ? ` <small>(carried from day ${job.day})</small>` : ""}</td>
    <td>${name(job.assignee)}</td><td>${name(job.location)}</td>
    <td>${job.worked_minutes}/${job.required_minutes} min</td>
    <td>${at(job.due_at)}</td><td>${escape(job.status)}${job.blocker ? ` / ${escape(job.blocker)}` : ""}</td>
    <td>${Object.entries(job.supplies).map(([item, quantity]) => `${quantity} ${label(item)}`).join(", ")
      || "No consumables"}</td></tr>`;
  const calls = new Map<string, { pending: number; wait: number; read: number; coordination: number }>();
  for (const event of events) {
    if (event.run_id !== state.run?.id) continue;
    const count = calls.get(event.actor_id) ?? { pending: 0, wait: 0, read: 0, coordination: 0 };
    if (event.kind === "proxy_request") count.pending += 1;
    if (event.kind === "action_request" && event.payload && typeof event.payload === "object"
      && "action_id" in event.payload && typeof event.payload.action_id === "string") {
      const action = event.payload.action_id;
      if (["village_wait", "village_wait_event"].includes(action)) count.wait += count.pending;
      else if (["read", "look"].includes(action)) count.read += count.pending;
      else if (["say", "village_ask", "village_tell", "village_share_route", "village_meeting"].includes(action)) {
        count.coordination += count.pending;
      }
      count.pending = 0;
    }
    calls.set(event.actor_id, count);
  }
  return `${firewoodPanel}${livestockPanel}${dashboard.communication?.enabled ? communicationMarkup(dashboard.communication, true) : ""}
    <section class="village-panel" aria-label="Village economy and activities">
    ${maintenance.length ? `<h3>Town maintenance checklist</h3>
      <p class="muted">Required daily upkeep. Unfinished jobs carry forward at midnight without duplicating
        the backlog. Meals, sleep and funded commitments still take precedence.</p>
      <div class="maintenance-list"><table><thead><tr><th>Job</th><th>Assigned to</th><th>Site</th>
        <th>Work</th><th>Due</th><th>Status</th><th>Materials</th></tr></thead>
        <tbody>${current.map(maintenanceRow).join("")}</tbody></table></div>
      <details><summary>Completed maintenance history</summary>
        <div class="maintenance-list"><table><tbody>${maintenance.filter(job =>
          job.status === "completed" && !current.includes(job)).slice(-32).map(maintenanceRow).join("")}
        </tbody></table></div>
      </details>` : ""}
    ${researchEnabled || dashboard.research?.length ? `<h3>Town research & discoveries</h3>
      ${dashboard.research?.length ? researchTableMarkup(dashboard.research, name) :
        '<p class="muted">Occupational research is enabled. No research projects proposed or discoveries yet.</p>'}` : ""}
    <h3>Orders and reserves</h3>
    ${dashboard.orders.map(order => `<p>${name(order.buyer)} orders ${order.quantity} ${label(order.product)}
      from ${name(order.seller)} / ${order.delivered} delivered / ${escape(order.status)} /
      due ${at(order.deadline)}</p>`).join("") || '<p class="muted">No orders.</p>'}
    <details><summary>Usable stock and reserve warnings</summary>
      ${dashboard.products.map(product => `<p>${name(product.location)} / ${label(product.product)}:
        ${product.quantity} total, ${product.usable} usable, ${product.expiring} expiring /
        reserve ${product.reserve}${product.usable < product.reserve ? " / SHORTAGE" : ""}</p>`).join("")}
    </details>
    <details><summary>Business accounts (not net profit)</summary>
      <p class="muted">Inventory is valued at configured prices, not guaranteed sale proceeds.</p>
      ${dashboard.accounts.map(account => `<p>${name(account.owner)}: sales ${account.sales},
        purchases ${account.purchases}, fees ${account.fees}, subsidies ${account.subsidies},
        net cash ${account.net_cash}, inventory value ${account.inventory_value}</p>`).join("")}
    </details>
    <h3>Personal projects and optional quests</h3>
    ${dashboard.projects.map(project => `<p>${name(project.owner)} / ${escape(project.title)}:
      ${project.progress}/${project.required} / ${escape(project.status)}</p>`).join("")}
    ${dashboard.quests.map(quest => `<p>${name(quest.actor)} / ${escape(quest.title)}:
      ${quest.progress}/${quest.required} / ${escape(quest.status)}</p>`).join("")}
    <details><summary>Activities and village events</summary>
      ${dashboard.activities.slice(-24).map(activity => `<p>${at(activity.start)} /
        ${name(activity.actor)} / ${escape(activity.label)} / ${escape(activity.status)}</p>`).join("")}
      ${dashboard.incidents.slice(-12).map(incident => `<p>${at(incident.start)} /
        ${escape(incident.title)} / ${name(incident.location)} / ${escape(incident.status)}</p>`).join("")}
    </details>
    ${dashboard.visitors.length ? `<details><summary>Visitor history</summary>
      ${dashboard.visitors.map(visitor => `<p>${escape(visitor.name)} / Visitor / ${escape(visitor.status)} /
        arrived ${at(visitor.arrival)}${visitor.departure === null ? "" : ` / departed ${at(visitor.departure)}`}</p>`).join("")}
    </details>` : ""}
    <details><summary>Time use and model-call efficiency</summary>
      <p class="muted">Waits and production cooldowns are not automatically wasted time.
        Total recorded run model calls: ${state.run?.model_calls ?? 0}.</p>
      ${Object.entries(dashboard.time_use).map(([actor, usage]) => `<p>${name(actor)} /
        measured since ${at(usage.since)}: work ${usage.work} min, travel ${usage.travel} min,
        requested wait ${usage.wait} min, leisure ${usage.leisure} min, idle ${usage.idle} min,
        sleep ${usage.sleep} min / ${usage.successful_actions} successful actions</p>`).join("")}
      ${[...calls].filter(([, count]) => count.wait + count.read + count.coordination > 0)
        .map(([actor, count]) => `<p>${name(actor)} / model calls linked to actions:
          waiting ${count.wait}, reading/looking ${count.read}, coordination ${count.coordination}</p>`).join("")}
    </details>
  </section>`;
}
