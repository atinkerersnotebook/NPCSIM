import { z } from "zod";
import type { RunEvent, State } from "./contracts.ts";
import { escape } from "./html.ts";
import { villageClockTime } from "./view-mode.ts";
import { actorDetailsMarkup, siteStockText } from "./actor-details.ts";
import { npcPortrait, npcSprite } from "./npc-sprite.ts";
import { sceneAnchors, sceneHeight, sceneTerrain, sceneWidth } from "./millhaven-orthographic.ts";

import { townMapAnchors as mapAnchors, townMapFixtureAnchors, townMapWidth as mapWidth, townMapHeight as mapHeight } from "./millhaven-geography.ts";

const mapImage = new URL("./assets/millhaven-extended-new.png", import.meta.url).href;
const bubbleDuration = 18_000;
export const townMapAnchors: Readonly<Record<string, { x: number; y: number }>> = mapAnchors;

export interface MapAgent {
  id: string;
  name: string;
  locationId: string;
  location: string;
  activity: string;
  current: boolean;
  deciding: boolean;
  visitor?: boolean;
}

export interface SpokenLine {
  id: string;
  actorId: string;
  locationId: string;
  text: string;
  timestamp: string;
  minute?: number;
  expiresAt: number;
}

const requestSchema = z.object({
  request_id: z.string(), action_id: z.string(), arguments: z.record(z.string(), z.unknown())
});
const resultSchema = z.object({
  request_id: z.string(), outcome: z.string(), code: z.string().optional()
});
const spokenObservationSchema = z.object({
  observation: z.object({ self: z.object({ actor_id: z.string(), location_id: z.string() }) })
});

export class TownDialogue {
  readonly lines: SpokenLine[] = [];
  private readonly pending = new Map<string, { actorId: string; text: string }>();

  record(event: RunEvent, live: boolean, now: number): void {
    if (event.kind === "action_request") {
      const request = requestSchema.parse(event.payload);
      if (request.action_id === "say") {
        this.pending.set(request.request_id, {
          actorId: event.actor_id, text: z.string().min(1).parse(request.arguments.message)
        });
      }
    } else if (event.kind === "action_result") {
      const result = resultSchema.parse(event.payload);
      const request = this.pending.get(result.request_id);
      if (!request) return;
      this.pending.delete(result.request_id);
      if (result.outcome !== "APPLIED" || result.code !== "SPOKEN") return;
      const { self } = spokenObservationSchema.parse(event.payload).observation;
      if (self.actor_id !== request.actorId || event.actor_id !== request.actorId) {
        throw new Error("Spoken dialogue actor does not match the confirmed action.");
      }
      if (this.lines.some(line => line.id === event.id)) return;
      this.lines.push({
        id: event.id, actorId: request.actorId, locationId: self.location_id, text: request.text,
        timestamp: event.timestamp, minute: event.village_minute,
        expiresAt: live ? now + bubbleDuration : 0
      });
      if (this.lines.length > 40) this.lines.shift();
    }
  }

  active(now: number): SpokenLine[] {
    const latest = new Map<string, SpokenLine>();
    for (const line of this.lines) {
      if (line.expiresAt > now) latest.set(line.actorId, line);
    }
    return [...latest.values()];
  }
}

export interface TownMapOptions {
  presentation?: "map" | "scene";
  zoom?: number;
  selectedActorId?: string;
  dialogue?: TownDialogue;
  now?: number;
}

function locationSupplyMarkup(state: State, locationId: string): string {
  const world = state.world!;
  const dashboard = world.village?.dashboard;
  if (!dashboard) return "";
  const locationName = world.entities[locationId].name;
  const person = (id: string) => state.run?.agent_profiles?.[id]?.name
    ?? world.entities[id]?.name ?? id.replaceAll("-", " ").replace(/\b\w/g, char => char.toUpperCase());
  const outstanding = dashboard.orders.filter(order => order.quantity > order.delivered
    && !["fulfilled", "cancelled", "rejected", "declined", "expired", "failed"].includes(order.status));
  const incoming = outstanding.filter(order => order.destination === locationId);
  const production = outstanding.filter(order => order.source === locationId
    && (order.production_plan?.output || order.status === "blocked"));
  const requests = dashboard.communication?.notices.filter(notice => notice.type === "supply_request"
    && (notice.destination_id === locationId || notice.destination_id === undefined && notice.destination === locationName)
    && notice.commitment === null
    && ["posted", "seen"].includes(notice.status)) ?? [];
  const requestedOutput = dashboard.communication?.notices.filter(notice => notice.type === "supply_request"
    && notice.commitment === null && ["posted", "seen"].includes(notice.status)
    && Object.entries(world.village!.roster).some(([id, resident]) => resident.work === locationId
      && notice.subject in (resident.job?.outputs ?? {})
      && (notice.recipient_ids?.includes(id) ?? notice.recipients.includes(person(id))))) ?? [];
  const list = (title: string, entries: string[]) => entries.length
    ? `<div class="town-map-supply-section"><b>${title}</b><ul>${entries.map(entry => `<li>${entry}</li>`).join("")}</ul></div>` : "";
  const due = (minute: number) => `due ${escape(villageClockTime(minute))}`;
  const incomingMarkup = list("Outstanding orders / not guaranteed arrivals", incoming.map(order =>
    `${order.quantity - order.delivered} ${escape(order.product)} from ${escape(person(order.seller))} / ${escape(order.status.replaceAll("_", " "))} / ${due(order.deadline)}`));
  const requestMarkup = list("Requested supplies / awaiting acceptance", requests.map(notice =>
    `${notice.quantity} ${escape(notice.subject)} from ${escape(notice.recipients.join(", "))} / ${escape(notice.author)} requested / ${notice.due_at === null ? "no due time" : due(notice.due_at)}`));
  const productionMarkup = list("Production demand and materials", [...production.map(order => {
    const plan = order.production_plan;
    if (plan?.output) {
      const inputs = Object.entries(plan).filter(([key]) => key.startsWith("input:"))
        .map(([key, amount]) => `${amount} ${escape(key.slice(6))}`).join(", ");
      return `${escape(order.product)} for ${escape(person(order.buyer))} / ${escape(order.status.replaceAll("_", " "))}`
        + `${inputs ? ` / reserved inputs: ${inputs}` : ""}${plan.finish === undefined ? "" : ` / planned finish ${escape(villageClockTime(plan.finish))} (not guaranteed)`}`;
    }
    return `${escape(order.product)} for ${escape(person(order.buyer))} / blocked${order.reason ? `: ${escape(order.reason)}` : ""}`;
  }), ...requestedOutput.map(notice => {
    const maker = Object.entries(world.village!.roster).find(([id, resident]) =>
      resident.work === locationId && (notice.recipient_ids?.includes(id) ?? notice.recipients.includes(person(id))));
    const inputs = maker?.[1].job && notice.subject in maker[1].job.outputs
      ? Object.entries(maker[1].job.inputs).map(([item, amount]) =>
        `${amount} ${escape(item)} per batch (on site: ${world.village!.stocks[locationId]?.[item] ?? 0}${(world.village!.stocks[locationId]?.[item] ?? 0) < amount ? ", short" : ""})`).join(", ") : "";
    return `${escape(notice.quantity ?? 0)} ${escape(notice.subject)} requested by ${escape(notice.author)} / not accepted or scheduled`
      + `${inputs ? ` / inputs: ${inputs}` : ""}`;
  })]);
  return incomingMarkup + requestMarkup + productionMarkup;
}

export function townMapMarkup(state: State, agents: readonly MapAgent[], options: TownMapOptions = {}): string {
  if (!state.world?.village) return "";
  const { world } = state;
  const scene = options.presentation === "scene";
  const prefix = scene ? "scene" : "town";
  const anchors = scene ? sceneAnchors : townMapAnchors;
  const width = scene ? sceneWidth : mapWidth;
  const height = scene ? sceneHeight : mapHeight;
  const zoom = options.zoom ?? 1;
  const canvasStyle = scene ? `width:${zoom * 100}%;--scene-zoom:${zoom}`
    : `width:${zoom * 100}%`;
  const lines = options.dialogue?.lines ?? [];
  const active = options.dialogue?.active(options.now ?? Date.now()) ?? [];
  const present = agents.filter(agent => !agent.visitor || agent.current);
  const groups = new Map<string, MapAgent[]>();
  const offMap: MapAgent[] = [];
  for (const agent of present) {
    if (!agent.current || !Object.hasOwn(anchors, agent.locationId)
      || world.entities[agent.locationId]?.kind !== "location") {
      offMap.push(agent);
      continue;
    }
    const group = groups.get(agent.locationId) ?? [];
    group.push(agent);
    groups.set(agent.locationId, group);
  }
  const actorButton = (agent: MapAgent, onScene = false) => `<span class="town-map-actor-wrap"><button type="button"
    class="town-map-actor${onScene ? " scene-actor" : ""}${agent.deciding ? " deciding" : ""}${agent.visitor ? " visitor" : ""}"
    ${scene ? "data-scene-actor" : "data-map-actor"}="${escape(agent.id)}" aria-pressed="${agent.id === options.selectedActorId}"
    aria-label="${escape(`${agent.name}, ${agent.location}. Select for details`)}" aria-describedby="${prefix}-actor-${escape(agent.id)}">
    ${onScene ? npcPortrait(agent.id) : npcSprite(agent.id)}
    <span>${escape(agent.name)}</span></button><span id="${prefix}-actor-${escape(agent.id)}" class="actor-details-popover" role="tooltip">
      <strong>${escape(agent.name)}${agent.visitor ? " / Visitor" : ""}</strong>
      ${actorDetailsMarkup(state, agent)}</span></span>`;
  const nameOf = (id: string) => agents.find(agent => agent.id === id)?.name
    ?? world.entities[id]?.name ?? id;
  const lineMarkup = (line: SpokenLine) => `<strong>${escape(nameOf(line.actorId))}</strong>
    <span>${escape(line.text)}</span>`;
  const selected = present.find(agent => agent.id === options.selectedActorId);
  const selectedLine = selected ? lines.filter(line => line.actorId === selected.id).at(-1) : undefined;
  const traderEnquiries = world.village!.events.filter(event => event.actor === "iron-trader"
    && event.action === "visitor_trade_enquiry").slice(-8).reverse();
  return `<section class="town-map${scene ? " town-scene" : ""}" aria-label="Live Millhaven ${scene ? "scene" : "town map"}">
    <div class="town-map-heading"><div><span class="eyebrow">MILLHAVEN / LIVE LOCATIONS</span>
      <h2>${scene ? "Millhaven Scene" : "Town map"}</h2></div>
      <div class="town-map-tools" role="group" aria-label="${scene ? "Scene" : "Map"} zoom">
        <button type="button" class="button" data-map-zoom="out" ${zoom <= 1 ? "disabled" : ""} aria-label="Zoom ${scene ? "scene" : "map"} out">-</button>
        <output aria-label="${scene ? "Scene" : "Map"} zoom level">${Math.round(zoom * 100)}%</output>
        <button type="button" class="button" data-map-zoom="in" ${zoom >= 3 ? "disabled" : ""} aria-label="Zoom ${scene ? "scene" : "map"} in">+</button>
        <button type="button" class="button" data-map-zoom="fit">${scene ? "Fit scene" : "Fit map"}</button>
        ${scene ? '<button type="button" class="button" data-scene-center>Center village</button>' : ""}
      </div>
    </div>
    <p class="town-map-help">${scene ? "Drag the terrain, scroll, or focus the scene and use the arrow keys to explore. Hover or focus a gold marker for the location name and site stock. Select a character for details. Positions are approximate."
      : "Actual recorded locations; markers are grouped at buildings, not exact indoor positions. Hover or focus a location for its site stock, supply requests and outstanding orders; select a character for details. Scroll to explore; zoom for a closer look."}
      Speech bubbles show confirmed spoken words for 18 seconds, never private thoughts.</p>
    <div class="town-map-scroll${scene && zoom === 1 ? " scene-fit" : ""}" tabindex="0" role="region" aria-label="Scrollable ${scene ? "Millhaven scene" : "town map"}">
      <div class="town-map-canvas" style="${canvasStyle}">
        ${scene ? sceneTerrain : `<img src="${escape(mapImage)}" width="${mapWidth}" height="${mapHeight}" alt="Illustrated map of Millhaven, with Torvin's iron mine, village square, farms, workshops, tavern and river." draggable="false">`}
        ${Object.entries(anchors).filter(([id]) => world.entities[id]?.kind === "location")
          .sort(([, a], [, b]) => scene ? a.y - b.y : 0)
          .map(([locationId, anchor]) => {
          const group = groups.get(locationId) ?? [];
          const name = world.entities[locationId].name;
          const stock = world.village!.stocks[locationId];
          const bubbles = active.filter(line => line.locationId === locationId
            && group.some(agent => agent.id === line.actorId));
          return `<div class="town-map-place${anchor.y < height * (240 / 768) ? " bubbles-below" : ""}${anchor.x < width * (160 / 1123) ? " align-left" : anchor.x > width - (scene ? 200 : width * (263 / 1123)) ? " align-right" : ""}"
            style="left:${anchor.x / width * 100}%;top:${anchor.y / height * 100}%"
            data-map-location="${escape(locationId)}">
            ${bubbles.length ? `<div class="town-map-bubbles" aria-live="polite">
              ${bubbles.map(line => `<div class="town-map-bubble" data-map-speech="${escape(line.id)}">${lineMarkup(line)}</div>`).join("")}
            </div>` : ""}
            <span class="town-map-location-wrap${scene ? " scene-location-wrap" : ""}"><button type="button" class="town-map-location${scene ? " scene-location" : ""}"
              data-location-marker="${escape(locationId)}" aria-label="${escape(`${name}. Current site stock`)}" aria-describedby="${prefix}-stock-${escape(locationId)}">
              ${scene ? '<span aria-hidden="true"></span>' : `${escape(name)}${group.length ? ` / ${group.length}` : ""}`}</button>
              <span id="${prefix}-stock-${escape(locationId)}" class="actor-details-popover town-map-stock" role="tooltip">
                <strong>${escape(name)}</strong><span>Current site stock: ${escape(stock ? siteStockText(stock) : "No stock recorded")}</span>
                ${locationSupplyMarkup(state, locationId)}
              </span></span>
            ${group.length ? `<div class="town-map-cast">${group.map(agent => actorButton(agent, scene)).join("")}</div>` : ""}
          </div>`;
        }).join("")}
        ${scene ? "" : Object.entries(townMapFixtureAnchors)
          .filter(([id]) => world.entities[id])
          .map(([id, anchor]) => {
            const fixture = world.entities[id];
            const parent = fixture.location ? world.entities[fixture.location]?.name : undefined;
            return `<div class="town-map-fixture" style="left:${anchor.x / width * 100}%;top:${anchor.y / height * 100}%"
              data-map-fixture="${escape(id)}"><span class="town-map-location-wrap"><button type="button"
              class="town-map-location" data-fixture-marker="${escape(id)}" aria-label="${escape(fixture.name)}"
              aria-describedby="${prefix}-fixture-${escape(id)}">${escape(fixture.name)}</button><span
              id="${prefix}-fixture-${escape(id)}" class="actor-details-popover town-map-stock" role="tooltip">
              <strong>${escape(fixture.name)}</strong><span>${escape(fixture.description ?? "Physical map landmark.")}${parent ? ` Located at ${escape(parent)}.` : ""}</span>
              </span></span></div>`;
          }).join("")}
      </div>
    </div>
    ${offMap.length ? `<div class="town-map-offmap"><strong>Off map / location unverified</strong>
      <p>These locations are not pictured, or have no current authoritative position.</p>
      ${offMap.map(agent => `<div>${actorButton(agent)} <span>${escape(agent.location)}${!agent.current ? " (last observed / not live)" : " (not pictured)"}</span></div>`).join("")}
    </div>` : ""}
    ${selected ? `<section class="town-map-selection" aria-label="Selected character">
      <h3>${npcPortrait(selected.id)} ${escape(selected.name)}${selected.visitor ? " / Visitor" : ""}</h3>
      ${actorDetailsMarkup(state, selected)}
      ${selectedLine ? `<p class="town-map-last-speech"><strong>Last spoken words:</strong> ${escape(selectedLine.text)}</p>` : ""}
    </section>` : ""}
    <section class="town-map-dialogue" aria-label="Recent spoken dialogue">
      <h3>Recent spoken dialogue</h3>
      <p class="muted">Last ${lines.length} confirmed speech actions (up to 40). Past dialogue is not replayed as live bubbles.</p>
      ${lines.length ? `<ol>${lines.slice(-8).reverse().map(line => `<li>
        <small>${escape(line.minute === undefined ? line.timestamp : villageClockTime(line.minute))}
          / ${escape(world.entities[line.locationId]?.name ?? line.locationId)}</small>
        ${lineMarkup(line)}</li>`).join("")}</ol>`
        : '<p class="muted">No confirmed spoken dialogue received yet.</p>'}
      ${traderEnquiries.length ? `<h3>Iron trader enquiries</h3><ol>${traderEnquiries.map(item => `<li>
        <small>${escape(villageClockTime(item.minute))} / ${escape(world.entities["village-square"]?.name ?? "Village square")}</small>
        <strong>Iron trader</strong><span>${escape(item.result)}</span></li>`).join("")}</ol>` : ""}
    </section>
  </section>`;
}
