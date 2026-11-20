import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";
import { eventSchema, stateSchema } from "../src/contracts.ts";
import { TownDialogue, townMapAnchors, townMapMarkup } from "../src/town-map.ts";
import { monitoredAgents, worldMonitorMarkup } from "../src/world-monitor.ts";
import type { MapAgent } from "../src/town-map.ts";
import { actorDetailsMarkup } from "../src/actor-details.ts";
import { npcPortrait, npcSprite } from "../src/npc-sprite.ts";
import { sceneAnchors, sceneHeight, sceneTerrain, sceneWidth } from "../src/millhaven-scene.ts";

function fixture() {
  const scenario = JSON.parse(readFileSync(resolve("..", "data", "scenarios", "millhaven-expanded.json"), "utf8"));
  const config = JSON.parse(readFileSync(resolve("..", "data", "config.json"), "utf8"));
  return stateSchema.parse({
    run: { schema_version: "1.0", id: "run", world_id: "world", session_id: "session",
      actor_id: "torvin", scenario_id: scenario.id, title: scenario.title, status: "running",
      reason: "", created_at: "2026-01-01", actions: 0, model_calls: 0, cycles: 0,
      elapsed_seconds: 0, model: null, goal: { id: "goal", objective: scenario.objective,
        type: scenario.goal_type, status: "active" } },
    observation: null, memory: {}, config, in_flight: false, event_count: 0,
    world: {
      world_id: "world", revision: 1, entities: scenario.entities,
      actors: Object.fromEntries(scenario.agents.map((agent: { id: string; start_location: string; body: unknown }) =>
        [agent.id, { actor_id: agent.id, location_id: agent.start_location,
          start_location_id: agent.start_location, inventory: [], body: agent.body, needs: {} }])),
      village: { minute: 480, roster: scenario.village.residents,
        residents: Object.fromEntries(Object.keys(scenario.village.residents).map(id => [id, {
          beliefs: {}, memories: [], trust: {}, activity: "Starting the day",
          next_work: 480, busy_until: 0, cargo: {}, delivery: null, delivery_cursor: 0
        }])),
        stocks: scenario.village.stocks, coins: scenario.village.coins, tasks: {}, events: [],
        breakdown: false, repaired: false, well_dug: false, bread_after_repair: 0 }
    }
  });
}

function event(sequence: number, kind: string, payload: unknown, actorId = "torvin") {
  return eventSchema.parse({
    id: `run:${sequence}`, sequence, run_id: "run", session_id: "session",
    actor_id: actorId, kind, cycle: 1, timestamp: "2026-11-09T14:00:00Z",
    village_minute: 485, payload
  });
}

function speak(dialogue: TownDialogue, sequence = 1, live = true, now = 1_000,
  text = "Meet me at the square.", actorId = "torvin", locationId = "forge") {
  dialogue.record(event(sequence, "action_request", {
    request_id: `say-${sequence}`, action_id: "say", arguments: { message: text }
  }, actorId), live, now);
  dialogue.record(event(sequence + 1, "action_result", {
    request_id: `say-${sequence}`, outcome: "APPLIED", code: "SPOKEN",
    observation: { self: { actor_id: actorId, location_id: locationId } }
  }, actorId), live, now);
}

test("map anchors cover every town location using the extended illustration's bounds", () => {
  const state = fixture();
  for (const scenarioId of ["millhaven-expanded", "millhaven-living", "millhaven-resilient", "millhaven-complete"]) {
    const scenario = JSON.parse(readFileSync(resolve("..", "data", "scenarios", `${scenarioId}.json`), "utf8"));
    if (scenarioId !== "millhaven-expanded") {
      assert.equal(scenario.entities["iron-mine"].name, "Torvin's Iron Mine");
    }
    for (const [id, entity] of Object.entries(scenario.entities) as [string, { kind: string }][]) {
      if (entity.kind === "location") assert.ok(townMapAnchors[id], `${scenarioId}: ${id}`);
    }
  }
  for (const anchor of Object.values(townMapAnchors)) {
    assert.ok(anchor.x > 0 && anchor.x < 1123);
    assert.ok(anchor.y > 0 && anchor.y < 768);
  }
  assert.ok(townMapAnchors["iron-mine"]);
  const generations = JSON.parse(readFileSync(resolve("..", "data", "scenarios", "millhaven-generations.json"), "utf8"));
  assert.equal(generations.entities["iron-mine"].name, "Torvin's Iron Mine");
  assert.deepEqual(townMapAnchors["village-square"], { x: 552, y: 443 });
  assert.ok(townMapAnchors["herbal-workshop"].x !== townMapAnchors.cottages.x);
  assert.deepEqual(townMapAnchors["supply-depot"], { x: 737, y: 378 });
  assert.deepEqual(townMapAnchors.woods, { x: 1015, y: 190 });
  const image = readFileSync(new URL("../src/assets/millhaven-extended.png", import.meta.url));
  assert.equal(image.subarray(1, 4).toString(), "PNG");
  assert.equal(image.readUInt32BE(16), 1123);
  assert.equal(image.readUInt32BE(20), 768);
  assert.match(townMapMarkup(state, []), /width="1123" height="768"/);
});

test("Iona appears at her distinct workshop on a new living village map", () => {
  const state = fixture();
  const scenario = JSON.parse(readFileSync(resolve("..", "data", "scenarios", "millhaven-complete.json"), "utf8"));
  state.world!.entities = scenario.entities;
  state.world!.village!.stocks = scenario.village.stocks;
  const markup = townMapMarkup(state, [{ id: "iona", name: "Iona", locationId: "herbal-workshop",
    location: "Herbal Workshop", activity: "Preparing tea", current: true, deciding: false }]);
  assert.match(markup, /data-map-location="herbal-workshop"[\s\S]*?Iona/);
  assert.match(markup, /data-map-location="cottages"/);
});

test("resilient and living scenarios use the extended map and place depot visitors on-map", () => {
  const state = fixture();
  state.run!.scenario_id = "millhaven-resilient";
  state.world!.entities["supply-depot"] = {
    ...state.world!.entities["village-square"], name: "Communal granary and supply depot"
  };
  const markup = townMapMarkup(state, [{ id: "nella", name: "Nella", locationId: "supply-depot",
    location: "Communal granary and supply depot", activity: "Counting stock", current: true, deciding: false }]);
  assert.match(markup, /millhaven-extended\.png/);
  assert.match(markup, /data-map-location="supply-depot"/);
  assert.ok(!markup.includes("Off map / location unverified"));
  state.run!.scenario_id = "millhaven-living";
  assert.match(townMapMarkup(state, []), /millhaven-extended\.png/);
});

test("every pictured location exposes its live site stock even without a visitor", () => {
  const state = fixture();
  state.world!.village!.stocks["mill-shed"] = { grain: 3 };
  state.world!.village!.stocks.forge.iron = 7;
  const markup = townMapMarkup(state, []);
  assert.equal((markup.match(/data-map-location=/g) ?? []).length,
    Object.keys(townMapAnchors).filter(id => state.world!.entities[id]?.kind === "location").length);
  assert.match(markup, /data-map-location="mill-shed"[\s\S]*?Current site stock: grain: 3/);
  assert.match(markup, /data-map-location="forge"[\s\S]*?Current site stock: iron: 7/);
  assert.match(markup, /data-map-location="tavern-yard"[\s\S]*?No stock recorded/);
  assert.match(markup, /aria-describedby="town-stock-mill-shed"/);

  state.world!.entities["farm-feed-store"] = {
    name: "Feed <shed>", kind: "location", description: "" };
  state.world!.village!.stocks["farm-feed-store"] = { "hay<script>": 2 };
  const secondary = townMapMarkup(state, []);
  assert.match(secondary, /data-map-location="farm-feed-store"[\s\S]*?hay&lt;script&gt;: 2/);
  assert.ok(!secondary.includes("<script>"));
  state.world!.village!.stocks["farm-feed-store"]["hay<script>"] = 1;
  assert.match(townMapMarkup(state, []), /hay&lt;script&gt;: 1/);
});

test("location hover separates requested supplies, accepted deliveries and unscheduled production", () => {
  const state = fixture();
  const village = state.world!.village!;
  village.dashboard = {
    orders: [
      { id: "order-1", buyer: "jory", seller: "mara", source: "watermill", destination: "bakery",
        product: "flour", quantity: 3, delivered: 1, status: "accepted_planned", deadline: 750,
        production_plan: { finish: 700, output: 2, "input:grain": 2 } },
      { id: "order-2", buyer: "elsie", seller: "jory", source: "bakery", destination: "tavern",
        product: "bread", quantity: 2, delivered: 0, status: "blocked", deadline: 900,
        reason: "Insufficient uncommitted flour inputs above reserves" },
      { id: "order-3", buyer: "elsie", seller: "jory", source: "bakery", destination: "tavern",
        product: "bread", quantity: 1, delivered: 1, status: "fulfilled", deadline: 700 }
    ],
    communication: { enabled: true, scope: "operator", policy: "", boxes: [], share_candidates: [],
      notices: [{ id: "notice-1", version: 1, author: "Elsie", recipients: ["Jory"],
        subject: "bread", description: "Request more bread", type: "supply_request", status: "posted",
        quantity: 2, unit_price: null, delivery_fee: null, destination: "Elsie's tavern", due_at: 870,
        updated_at: 480, private: false, response: null, source: "operator world state", learned_at: null,
        confidence: null, hops: 0, source_chain: [], freshness: "Authoritative", appointment: null,
        commitment: null }] }
  } as typeof village.dashboard;
  const markup = townMapMarkup(state, []);
  const bakery = markup.split('data-map-location="bakery"')[1].split('data-map-location="cottages"')[0];
  assert.match(bakery, /Outstanding orders \/ not guaranteed arrivals/);
  assert.match(bakery, /2 flour from Mara \/ accepted planned \/ due 12:30/);
  assert.match(bakery, /2 bread requested by Elsie \/ not accepted or scheduled/);
  assert.match(bakery, /flour per batch \(on site:/);
  assert.match(bakery, /flour per batch \(on site: 6\)/);
  const tavern = markup.split('data-map-location="tavern"')[1].split('data-map-location="bakery"')[0];
  assert.match(tavern, /2 bread from Jory \/ blocked \/ due 15:00/);
  assert.match(tavern, /2 bread from Jory \/ Elsie requested \/ due 14:30/);
  assert.doesNotMatch(tavern, /1 bread from Jory \/ fulfilled/);
  assert.match(bakery, /Insufficient uncommitted flour inputs above reserves/);
  assert.doesNotMatch(markup, /guaranteed arrival at/);
  village.stocks.bakery.flour = 0;
  const short = townMapMarkup(state, []).split('data-map-location="bakery"')[1].split('data-map-location="cottages"')[0];
  assert.match(short, /flour per batch \(on site: 0, short\)/);
  village.dashboard.orders[0].status = "fulfilled";
  const completed = townMapMarkup(state, []).split('data-map-location="bakery"')[1].split('data-map-location="cottages"')[0];
  assert.doesNotMatch(completed, /Outstanding orders \/ not guaranteed arrivals/);
});

test("World view renders each resident once on the map without changing state or adding simulation controls", () => {
  const state = fixture();
  const before = JSON.stringify(state);
  const markup = worldMonitorMarkup(state);
  assert.equal((markup.match(/data-map-actor=/g) ?? []).length, 12);
  assert.ok(markup.indexOf('aria-label="Live Millhaven town map"') < markup.indexOf('class="world-gantt"'));
  assert.ok(markup.includes("not exact indoor positions"));
  assert.ok(markup.includes('data-map-zoom="fit"'));
  assert.ok(!markup.includes("data-command"));
  assert.equal(JSON.stringify(state), before);
});

test("Millhaven Scene projects every illustrated location on the same pair of isometric axes", () => {
  const origin = townMapAnchors["village-square"];
  const center = sceneAnchors["village-square"];
  const east = sceneAnchors.forge.x - center.x;
  const south = sceneAnchors.forge.y - center.y;
  const scaleX = east / ((townMapAnchors.forge.x - origin.x) - (townMapAnchors.forge.y - origin.y));
  const scaleY = south / ((townMapAnchors.forge.x - origin.x) + (townMapAnchors.forge.y - origin.y));
  assert.ok(scaleX > 0 && scaleY > 0);
  assert.equal(Object.keys(sceneAnchors).length, Object.keys(townMapAnchors).length);
  for (const [id, point] of Object.entries(townMapAnchors)) {
    const dx = point.x - origin.x;
    const dy = point.y - origin.y;
    const projected = sceneAnchors[id];
    assert.ok(projected.x > 0 && projected.x < sceneWidth && projected.y > 0 && projected.y < sceneHeight, id);
    assert.ok(Math.abs(projected.x - center.x - scaleX * (dx - dy)) < 1e-8, id);
    assert.ok(Math.abs(projected.y - center.y - scaleY * (dx + dy)) < 1e-8, id);
  }
});

test("Millhaven Scene projects its ground materials on the landmark grid", () => {
  const fills = [...sceneTerrain.matchAll(/(?:fill|stroke)="url\(#(scene-tile-[\w-]+)\)"/g)].map(([, id]) => id);
  const patterns = [...sceneTerrain.matchAll(/<pattern id="(scene-tile-[\w-]+)"[^>]+patternTransform="matrix\(([^)]+)\)"/g)];
  assert.deepEqual(new Set(fills), new Set(patterns.map(([, id]) => id)));
  for (const material of ["grass", "stone", "furrows", "dirt", "water", "planks"]) {
    assert.ok(fills.includes(`scene-tile-${material}`), material);
  }
  const square = townMapAnchors["village-square"];
  for (const [, id, transform] of patterns) {
    const [a, b, c, d, x, y] = transform.split(" ").map(Number);
    assert.ok(Math.abs((a * square.x + c * square.y) / 56 + x - sceneAnchors["village-square"].x) < 1e-8, id);
    assert.ok(Math.abs((b * square.x + d * square.y) / 56 + y - sceneAnchors["village-square"].y) < 1e-8, id);
  }
});

test("road and bridge edges follow the two isometric axes", () => {
  for (const [layer, material] of [["roads", "dirt"], ["bridge", "planks"]]) {
    const group = sceneTerrain.split(`<g class="scene-${layer}">`)[1].split("</g>")[0];
    const footprints = [...group.matchAll(new RegExp(`<polygon points="([^"]+)" fill="url\\(#scene-tile-${material}\\)"`, "g"))];
    assert.ok(footprints.length > 0, layer);
    for (const [, points] of footprints) {
      const vertices = points.split(" ").map(point => point.split(",").map(Number));
      assert.equal(vertices.length, 4);
      for (const [i, [x, y]] of vertices.entries()) {
        const [nextX, nextY] = vertices[(i + 1) % vertices.length];
        assert.ok(Math.abs((nextY - y) / (nextX - x) - (i % 2 === 0 ? .5 : -.5)) < 1e-8, layer);
      }
    }
  }
});

test("Millhaven Scene leaves clear space between building sprites", () => {
  const lots = [...sceneTerrain.matchAll(/<image href="[^"]*scene-(?!trees)(\w+)\.png" x="([^"]+)" y="([^"]+)" width="(\d+)" height="(\d+)"/g)]
    .map(([, kind, x, y, width, height]) => ({ kind, x: Number(x), y: Number(y), width: Number(width), height: Number(height) }));
  assert.ok(lots.length > 1);
  for (const [i, a] of lots.entries()) {
    for (const b of lots.slice(i + 1)) {
      assert.ok(a.x + a.width <= b.x || b.x + b.width <= a.x || a.y + a.height <= b.y || b.y + b.height <= a.y,
        `${a.kind} overlaps ${b.kind}`);
    }
  }
});

test("Millhaven Scene follows recorded locations using full bodies without changing the map", () => {
  const state = fixture();
  state.world!.actors.torvin.location_id = "forge";
  const markup = worldMonitorMarkup(state, [], {}, { selectedActorId: "torvin" });
  const [map, scene] = markup.split('id="monitor-scene"');
  assert.match(map, /data-map-actor="torvin"[\s\S]*?torvin-head\.png/);
  assert.match(map, /millhaven-extended\.png/);
  assert.match(scene, /<img class="scene-terrain"[^>]+millhaven-map\.png/);
  assert.match(scene, /class="town-map-scroll scene-fit"/);
  assert.match(scene, /class="town-map-canvas" style="width:100%;/);
  const zoomed = townMapMarkup(state, monitoredAgents(state), { presentation: "scene", zoom: 1.5 });
  assert.doesNotMatch(zoomed, /class="town-map-scroll scene-fit"/);
  assert.match(zoomed, /class="town-map-canvas" style="width:150%;/);
  assert.match(scene, /data-map-zoom="fit">Fit scene/);
  assert.doesNotMatch(map, /millhaven-map\.png/);
  assert.doesNotMatch(scene, /<svg class="scene-terrain"/);
  assert.doesNotMatch(scene, /millhaven-extended\.png/);
  assert.match(scene, /data-scene-center/);
  const forge = scene.split('data-map-location="forge"')[1].split('data-map-location=')[0];
  assert.match(forge, /data-scene-actor="torvin" aria-pressed="true"[^]*?torvin-body\.png/);
  const backdrop = readFileSync(new URL("../src/assets/millhaven-map.png", import.meta.url));
  const width = backdrop.readUInt32BE(16);
  const height = backdrop.readUInt32BE(20);
  assert.match(scene, new RegExp(`<img class="scene-terrain"[^>]+width="${width}" height="${height}"`));
  for (const [id, x, y] of [["forge", 268, 352], ["farm", 614, 293], ["bridge", 685, 416],
    ["tavern", 459, 575], ["riverbank", 746, 310], ["watermill", 787, 432], ["mill-shed", 880, 432]] as const) {
    const placement = scene.match(new RegExp(`style="left:([\\d.]+)%;top:([\\d.]+)%"\\s+data-map-location="${id}"`));
    assert.ok(placement, id);
    assert.ok(Math.abs(Number(placement[1]) / 100 * 989 - x) < 1e-8, id);
    assert.ok(Math.abs(Number(placement[2]) / 100 * 768 - y) < 1e-8, id);
  }
  assert.match(scene, /aria-describedby="scene-actor-torvin"/);
  assert.doesNotMatch(scene, /id="town-actor-torvin"|id="town-stock-/);
  assert.match(scene, /data-location-marker="forge"[^>]+aria-describedby="scene-stock-forge"/);
  const emptyScene = townMapMarkup(state, [], { presentation: "scene" });
  assert.match(emptyScene, /data-location-marker="forge"/);
  assert.match(emptyScene, /id="scene-stock-forge"[^>]*>\s*<strong>[^<]+<\/strong><span>Current site stock:/);
  assert.doesNotMatch(emptyScene, /data-scene-actor=/);

  state.world!.actors.torvin.location_id = "village-square";
  const updated = townMapMarkup(state, monitoredAgents(state), { presentation: "scene" });
  const square = updated.split('data-map-location="village-square"')[1].split('data-map-location=')[0];
  assert.match(square, /data-scene-actor="torvin"/);
  assert.equal((updated.match(/data-scene-actor="torvin"/g) ?? []).length, 1);
});

test("Millhaven Scene uses full-body visitor art and leaves unverified residents off scene", () => {
  const state = fixture();
  const scene = townMapMarkup(state, [
    { id: "visitor:visiting-merchant:735", name: "Trader", locationId: "forge", location: "Forge",
      activity: "Visiting", current: true, deciding: false, visitor: true },
    { id: "torvin", name: "Torvin", locationId: "forge", location: "Forge", activity: "Unknown", current: false, deciding: false }
  ], { presentation: "scene" });
  const [onScene, offScene] = scene.split("Off map / location unverified");
  assert.match(onScene, /general-trader-body\.png/);
  assert.doesNotMatch(onScene, /data-scene-actor="torvin"/);
  assert.match(offScene, /data-scene-actor="torvin"[^]*?last observed \/ not live/);
});

test("each resident has distinct artwork across the map and world view", () => {
  const state = fixture();
  const ids = Object.keys(state.world!.village!.roster);
  assert.equal(new Set(ids.map(npcSprite)).size, ids.length);
  assert.equal(new Set(ids.map(npcPortrait)).size, ids.length);
  const markup = worldMonitorMarkup(state);
  for (const id of ids) {
    assert.match(markup, new RegExp(`data-map-actor="${id}"[\\s\\S]*?data-sprite="${id}"`), id);
    assert.ok(markup.includes(`data-portrait="${id}"`), id);
  }
  assert.match(townMapMarkup(state, monitoredAgents(state), { selectedActorId: ids[0] }),
    new RegExp(`aria-label="Selected character"[\\s\\S]*?data-portrait="${ids[0]}"`));
  assert.match(npcPortrait("torvin"), /<img class="npc-portrait npc-portrait-image" src="[^"]*torvin-torso\.png" alt="" aria-hidden="true"/);
  assert.match(npcSprite("torvin"), /<img class="npc-sprite npc-sprite-image" src="[^"]*torvin-head\.png" alt="" aria-hidden="true"/);
  for (const id of ["bren", "mara", "elsie", "hobb", "jory", "ada", "nella", "rowan", "garrick", "iona", "finn"]) {
    assert.match(npcPortrait(id), new RegExp(`src="[^"]*${id}-torso\\.png"`));
    assert.match(npcSprite(id), new RegExp(`src="[^"]*${id}-head\\.png"`));
  }
  for (const [id, artwork] of [
    ["iron-trader", "iron-trader"],
    ["visitor:visiting-merchant:735", "general-trader"],
    ["visitor:passing-traveller:540", "visitor-woman"],
    ["pack-trader", "pack-trader"],
    ["visitor-man", "visitor-man"]
  ]) {
    assert.match(npcPortrait(id), new RegExp(`src="[^"]*${artwork}-torso\\.png"`));
    assert.match(npcSprite(id), new RegExp(`src="[^"]*${artwork}-head\\.png"`));
  }
  assert.match(npcPortrait("visitor"), /viewBox="0 0 40 48"/);
  assert.match(npcSprite("visitor"), /viewBox="0 0 20 30"/);
  assert.match(npcSprite('<img src=x>'), /data-sprite="&lt;img src=x&gt;"/);
  assert.match(npcPortrait('<img src=x>'), /data-portrait="&lt;img src=x&gt;"/);
  assert.ok(!npcSprite('<img src=x>').includes('<img'));
  assert.ok(!npcPortrait('<img src=x>').includes('<img'));
});

test("town actor hover and selection show actual location stock and money without private fields", () => {
  const state = fixture();
  state.world!.actors.torvin.location_id = "forge";
  state.world!.actors.torvin.inventory = ["tongs"];
  state.world!.village!.residents.torvin.cargo = { iron: 2 };
  state.world!.village!.stocks.forge.iron = 7;
  state.world!.village!.coins.torvin = 41;
  state.world!.village!.roster.torvin.personality = "PRIVATE-OPERATOR-PERSONALITY";
  const markup = townMapMarkup(state, monitoredAgents(state), { selectedActorId: "torvin" });
  assert.match(markup, /Stock at [^<]*[Ff]orge \(site, not personal\)/);
  assert.match(markup, /iron: 7/);
  assert.match(markup, /41 coins/);
  assert.match(markup, /iron ×2 \(cargo\)/);
  assert.match(markup, /class="actor-details-popover" role="tooltip"/);
  assert.ok(!markup.includes("PRIVATE-OPERATOR-PERSONALITY"));
  const visitor = townMapMarkup(state, [{ id: "visitor", name: "Visitor", locationId: "forge",
    location: "Forge", activity: "Visiting", current: true, deciding: false, visitor: true }],
  { selectedActorId: "visitor" });
  assert.match(visitor, /Stock at [^<]*[Ff]orge \(site, not personal\)/);
  assert.ok(!visitor.includes("<dt>Carried</dt>"));
  assert.ok(!visitor.includes("<dt>Money</dt>"));
  assert.ok(!visitor.includes("coins"));
  const torvin = monitoredAgents(state).find(agent => agent.id === "torvin")!;
  state.world!.actors.torvin.location_id = "tavern";
  state.world!.village!.stocks.tavern.bread = 9;
  const moved = actorDetailsMarkup(state, torvin);
  assert.match(moved, /Stock at [^<]*[Tt]avern \(site, not personal\)/);
  assert.match(moved, /bread: 9/);
  assert.ok(!moved.includes("iron: 7"));
  assert.ok(!moved.includes("Workplace stock"));
  assert.match(moved, /41 coins/);
  state.world!.entities.tongs.name = '<img src=x onerror="bad()">';
  const escaped = townMapMarkup(state, monitoredAgents(state));
  assert.ok(escaped.includes('&lt;img src=x onerror=&quot;bad()&quot;&gt;'));
  assert.ok(!escaped.includes('<img src=x onerror="bad()">'));
});

test("NPC positions follow the authoritative world, not a stale observation or memory", () => {
  const state = fixture();
  state.world!.actors.torvin.location_id = "forge";
  const agents = monitoredAgents(state);
  const torvin = agents.find(agent => agent.id === "torvin")!;
  const markup = townMapMarkup(state, [torvin]);
  assert.ok(markup.includes('data-map-location="forge"'));
  assert.match(markup, /data-map-location="forge"[\s\S]*?data-map-actor="torvin"/);
  state.world!.actors.torvin.location_id = "tavern";
  const moved = townMapMarkup(state, [monitoredAgents(state).find(agent => agent.id === "torvin")!]);
  assert.ok(moved.includes('data-map-location="tavern"'));
  assert.match(moved, /data-map-location="tavern"[\s\S]*?data-map-actor="torvin"/);
  assert.equal((moved.match(/data-map-actor="torvin"/g) ?? []).length, 1);
});

test("Torvin's mine is on-map while unverified locations stay off map and departed visitors are omitted", () => {
  const state = fixture();
  const living = JSON.parse(readFileSync(resolve("..", "data", "scenarios", "millhaven-living.json"), "utf8"));
  state.world!.entities["iron-mine"] = living.entities["iron-mine"];
  const torvin = monitoredAgents(state)[0];
  const agents: MapAgent[] = [
    { ...torvin, locationId: "iron-mine", location: "Millhaven iron mine" },
    { ...torvin, id: "stale", locationId: "forge", current: false },
    { ...torvin, id: "visitor", visitor: true, locationId: "tavern", location: "Tavern" },
    { ...torvin, id: "departed", visitor: true, current: false }
  ];
  const markup = townMapMarkup(state, agents);
  assert.match(markup, /data-map-location="iron-mine"[\s\S]*?data-map-actor="torvin"/);
  assert.ok(markup.includes("(last observed / not live)"));
  assert.ok(!markup.includes("Millhaven iron mine (not pictured)"));
  assert.ok(markup.includes('data-map-location="forge"'));
  assert.ok(markup.includes('data-map-actor="visitor"'));
  assert.ok(!markup.includes('data-map-actor="departed"'));
});

test("other adventures and unavailable world snapshots never borrow Millhaven's geography", () => {
  const state = fixture();
  state.world!.village = null;
  assert.equal(townMapMarkup(state, monitoredAgents(state)), "");
  state.world = null;
  assert.equal(townMapMarkup(state, monitoredAgents(state)), "");
});

test("only confirmed APPLIED SPOKEN results become dialogue, never requests, rejections or private events", () => {
  const dialogue = new TownDialogue();
  dialogue.record(event(1, "action_request", {
    request_id: "rejected", action_id: "say", arguments: { message: "Not spoken" }
  }), true, 1_000);
  assert.equal(dialogue.lines.length, 0);
  dialogue.record(event(2, "action_result", {
    request_id: "rejected", outcome: "REJECTED", code: "BUSY"
  }), true, 1_000);
  for (const kind of ["observation", "proxy_response", "npc_chat", "dungeon_master_response"]) {
    dialogue.record(event(3, kind, { text: "PRIVATE", messages: [{ text: "Repeated old message" }] }), true, 1_000);
  }
  dialogue.record(event(4, "action_result", { request_id: "unmatched", outcome: "APPLIED", code: "SPOKEN" }), true, 1_000);
  assert.equal(dialogue.lines.length, 0);
  speak(dialogue, 5);
  assert.equal(dialogue.lines.length, 1);
  assert.equal(dialogue.lines[0].text, "Meet me at the square.");
  assert.equal(dialogue.lines[0].locationId, "forge");
});

test("historical or reconnect speech enters history but is never replayed as a bubble", () => {
  const dialogue = new TownDialogue();
  speak(dialogue, 1, false);
  assert.equal(dialogue.lines.length, 1);
  assert.deepEqual(dialogue.active(1_000), []);
  speak(dialogue, 3, true);
  assert.equal(dialogue.active(1_000).length, 1);
  assert.equal(dialogue.active(18_999).length, 1);
  assert.equal(dialogue.active(19_000).length, 0);
  assert.equal(dialogue.lines.length, 2);
});

test("concurrent speakers get individual bubbles; a speaker's newest live words replace their older bubble", () => {
  const dialogue = new TownDialogue();
  speak(dialogue, 1, true, 1_000, "First");
  speak(dialogue, 3, true, 2_000, "Second");
  speak(dialogue, 5, true, 2_000, "Reply", "bren", "forge");
  assert.deepEqual(dialogue.active(2_000).map(line => line.text), ["Second", "Reply"]);
  const state = fixture();
  state.world!.actors.torvin.location_id = "forge";
  state.world!.actors.bren.location_id = "forge";
  const markup = townMapMarkup(state, monitoredAgents(state), { dialogue, now: 2_000 });
  assert.equal((markup.match(/data-map-speech=/g) ?? []).length, 2);
});

test("speech stays at its confirmed location and disappears when its speaker moves or its timer expires", () => {
  const state = fixture();
  state.world!.actors.torvin.location_id = "forge";
  const dialogue = new TownDialogue();
  speak(dialogue);
  const markup = () => townMapMarkup(state, monitoredAgents(state), { dialogue, now: 1_000 });
  assert.ok(markup().includes('data-map-speech="run:2"'));
  state.world!.actors.torvin.location_id = "tavern";
  assert.ok(!markup().includes("data-map-speech="));
  assert.ok(markup().includes("Meet me at the square."));
  state.world!.actors.torvin.location_id = "forge";
  assert.ok(!townMapMarkup(state, monitoredAgents(state), { dialogue, now: 19_000 }).includes("data-map-speech="));
});

test("iron trader enquiries show as recorded trade invitations, not NPC speech or sales", () => {
  const state = fixture();
  state.world!.village!.events.push({ minute: 725, actor: "iron-trader",
    action: "visitor_trade_enquiry", target: "torvin",
    result: 'The iron trader asks Torvin about <iron>. A purchase is still required.', witnesses: ["torvin"] });
  const markup = townMapMarkup(state, []);
  assert.match(markup, /Iron trader enquiries/);
  assert.match(markup, /asks Torvin about &lt;iron&gt;/);
  assert.match(markup, /Last 0 confirmed speech actions/);
  assert.doesNotMatch(markup, /data-map-speech=/);
});

test("dialogue is bounded and duplicate results do not duplicate speech", () => {
  const dialogue = new TownDialogue();
  for (let i = 1; i < 100; i += 2) speak(dialogue, i);
  assert.equal(dialogue.lines.length, 40);
  const duplicate = event(100, "action_result", {
    request_id: "say-99", outcome: "APPLIED", code: "SPOKEN"
  });
  dialogue.record(duplicate, true, 1_000);
  assert.equal(dialogue.lines.length, 40);
  assert.equal(new TownDialogue().lines.length, 0);
});

test("mismatched dialogue identities fail explicitly instead of attributing speech to another NPC", () => {
  const dialogue = new TownDialogue();
  dialogue.record(event(1, "action_request", {
    request_id: "say", action_id: "say", arguments: { message: "Hello" }
  }), true, 1_000);
  assert.throws(() => dialogue.record(event(2, "action_result", {
    request_id: "say", outcome: "APPLIED", code: "SPOKEN",
    observation: { self: { actor_id: "bren", location_id: "forge" } }
  }), true, 1_000), /does not match/);
});

test("map, bubble and selection text is escaped and only public speech is exposed", () => {
  const state = fixture();
  state.world!.actors.torvin.location_id = "forge";
  state.world!.entities.torvin = { name: "<script>Torvin</script>", kind: "npc", description: "" };
  state.world!.entities.forge.name = 'Forge " onmouseover="evil';
  state.memory = { plan: "PRIVATE PLAN" };
  const dialogue = new TownDialogue();
  speak(dialogue, 1, true, 1_000, '<img src=x onerror="evil">');
  const markup = townMapMarkup(state, monitoredAgents(state), {
    dialogue, now: 1_000, selectedActorId: "torvin", zoom: 2
  });
  assert.ok(markup.includes("&lt;script&gt;Torvin&lt;/script&gt;"));
  assert.ok(markup.includes("&lt;img src=x onerror=&quot;evil&quot;&gt;"));
  assert.ok(markup.includes('aria-label="Selected character"'));
  assert.ok(markup.includes('aria-pressed="true"'));
  assert.ok(markup.includes('style="width:min(200%,226vh);min-width:1668px"'));
  assert.ok(!markup.includes("<script>"));
  assert.ok(!markup.includes("<img src=x"));
  assert.ok(!markup.includes("PRIVATE PLAN"));
});
