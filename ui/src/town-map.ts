import { z } from "zod";
import type { RunEvent, State } from "./contracts.ts";
import { escape } from "./html.ts";
import { villageClockTime } from "./view-mode.ts";
import { actorDetailsMarkup } from "./actor-details.ts";

const mapImage = new URL("./assets/millhaven.png", import.meta.url).href;
const bubbleDuration = 18_000;

// Pixel anchors on the supplied 1024 x 768 illustration, not simulated indoor coordinates.
export const townMapAnchors: Readonly<Record<string, { x: number; y: number }>> = {
  "village-square": { x: 402, y: 430 },
  forge: { x: 135, y: 333 },
  stable: { x: 112, y: 218 },
  watermill: { x: 852, y: 280 },
  "mill-shed": { x: 977, y: 320 },
  farm: { x: 480, y: 126 },
  "general-store": { x: 364, y: 280 },
  workshop: { x: 141, y: 438 },
  tavern: { x: 496, y: 650 },
  bakery: { x: 620, y: 480 },
  cottages: { x: 145, y: 566 },
  woods: { x: 908, y: 118 },
  bridge: { x: 706, y: 356 },
  riverbank: { x: 750, y: 176 },
  "tavern-yard": { x: 672, y: 648 }
};

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
  zoom?: number;
  selectedActorId?: string;
  dialogue?: TownDialogue;
  now?: number;
}

export function townMapMarkup(state: State, agents: readonly MapAgent[], options: TownMapOptions = {}): string {
  if (!state.world?.village) return "";
  const { world } = state;
  const zoom = options.zoom ?? 1;
  const lines = options.dialogue?.lines ?? [];
  const active = options.dialogue?.active(options.now ?? Date.now()) ?? [];
  const present = agents.filter(agent => !agent.visitor || agent.current);
  const groups = new Map<string, MapAgent[]>();
  const offMap: MapAgent[] = [];
  for (const agent of present) {
    if (!agent.current || !Object.hasOwn(townMapAnchors, agent.locationId)
      || world.entities[agent.locationId]?.kind !== "location") {
      offMap.push(agent);
      continue;
    }
    const group = groups.get(agent.locationId) ?? [];
    group.push(agent);
    groups.set(agent.locationId, group);
  }
  const actorButton = (agent: MapAgent) => `<span class="town-map-actor-wrap"><button type="button"
    class="town-map-actor${agent.deciding ? " deciding" : ""}${agent.visitor ? " visitor" : ""}"
    data-map-actor="${escape(agent.id)}" aria-pressed="${agent.id === options.selectedActorId}"
    aria-label="${escape(`${agent.name}, ${agent.location}. Select for details`)}" aria-describedby="town-actor-${escape(agent.id)}">
    <span class="town-map-avatar" aria-hidden="true">${escape(agent.name.slice(0, 1))}</span>
    <span>${escape(agent.name)}</span></button><span id="town-actor-${escape(agent.id)}" class="actor-details-popover" role="tooltip">
      <strong>${escape(agent.name)}${agent.visitor ? " / Visitor" : ""}</strong>
      ${actorDetailsMarkup(state, agent)}</span></span>`;
  const nameOf = (id: string) => agents.find(agent => agent.id === id)?.name
    ?? world.entities[id]?.name ?? id;
  const lineMarkup = (line: SpokenLine) => `<strong>${escape(nameOf(line.actorId))}</strong>
    <span>${escape(line.text)}</span>`;
  const selected = present.find(agent => agent.id === options.selectedActorId);
  const selectedLine = selected ? lines.filter(line => line.actorId === selected.id).at(-1) : undefined;
  return `<section class="town-map" aria-label="Live Millhaven town map">
    <div class="town-map-heading"><div><span class="eyebrow">MILLHAVEN / LIVE LOCATIONS</span>
      <h2>Town map</h2></div>
      <div class="town-map-tools" role="group" aria-label="Map zoom">
        <button type="button" class="button" data-map-zoom="out" ${zoom <= 1 ? "disabled" : ""} aria-label="Zoom map out">-</button>
        <output aria-label="Map zoom level">${Math.round(zoom * 100)}%</output>
        <button type="button" class="button" data-map-zoom="in" ${zoom >= 3 ? "disabled" : ""} aria-label="Zoom map in">+</button>
        <button type="button" class="button" data-map-zoom="fit">Fit map</button>
      </div>
    </div>
    <p class="town-map-help">Actual recorded locations; markers are grouped at buildings, not exact indoor positions.
      Select a character for details. Scroll to explore; zoom for a closer look.
      Speech bubbles show confirmed spoken words for 18 seconds, never private thoughts.</p>
    <div class="town-map-scroll" tabindex="0" role="region" aria-label="Scrollable town map">
      <div class="town-map-canvas" style="width:min(${zoom * 100}%,${zoom * 103}vh);min-width:${zoom * 760}px">
        <img src="${escape(mapImage)}" width="1024" height="768" alt="Illustrated map of Millhaven, with its village square, farms, workshops, tavern and river." draggable="false">
        ${[...groups].map(([locationId, group]) => {
          const anchor = townMapAnchors[locationId];
          const bubbles = active.filter(line => line.locationId === locationId
            && group.some(agent => agent.id === line.actorId));
          return `<div class="town-map-place${anchor.y < 240 ? " bubbles-below" : ""}${anchor.x < 160 ? " align-left" : anchor.x > 860 ? " align-right" : ""}"
            style="left:${anchor.x / 1024 * 100}%;top:${anchor.y / 768 * 100}%"
            data-map-location="${escape(locationId)}">
            ${bubbles.length ? `<div class="town-map-bubbles" aria-live="polite">
              ${bubbles.map(line => `<div class="town-map-bubble" data-map-speech="${escape(line.id)}">${lineMarkup(line)}</div>`).join("")}
            </div>` : ""}
            <span class="town-map-location">${escape(group[0].location)} / ${group.length}</span>
            <div class="town-map-cast">${group.map(actorButton).join("")}</div>
          </div>`;
        }).join("")}
      </div>
    </div>
    ${offMap.length ? `<div class="town-map-offmap"><strong>Off map / location unverified</strong>
      <p>These locations are not pictured, or have no current authoritative position.</p>
      ${offMap.map(agent => `<div>${actorButton(agent)} <span>${escape(agent.location)}${!agent.current ? " (last observed / not live)" : " (not pictured)"}</span></div>`).join("")}
    </div>` : ""}
    ${selected ? `<section class="town-map-selection" aria-label="Selected character">
      <h3>${escape(selected.name)}${selected.visitor ? " / Visitor" : ""}</h3>
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
    </section>
  </section>`;
}
