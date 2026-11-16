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
  assert.ok(markup.includes('class="monitor-visuals with-town-map"'));
  assert.ok(markup.indexOf('aria-label="Live Millhaven town map"') < markup.indexOf('class="world-gantt"'));
  assert.ok(markup.includes("not exact indoor positions"));
  assert.ok(markup.includes('data-map-zoom="fit"'));
  assert.ok(!markup.includes("data-command"));
  assert.equal(JSON.stringify(state), before);
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
