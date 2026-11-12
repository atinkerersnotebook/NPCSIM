import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";
import { eventSchema, stateSchema } from "../src/contracts.ts";
import { TownDialogue, townMapAnchors, townMapMarkup } from "../src/town-map.ts";
import { monitoredAgents, worldMonitorMarkup } from "../src/world-monitor.ts";
import type { MapAgent } from "../src/town-map.ts";

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

test("map anchors cover every pictured town location using the supplied illustration's bounds", () => {
  const state = fixture();
  for (const [id, entity] of Object.entries(state.world!.entities)) {
    if (entity.kind === "location" && id !== "iron-mine") assert.ok(townMapAnchors[id], id);
  }
  for (const anchor of Object.values(townMapAnchors)) {
    assert.ok(anchor.x > 0 && anchor.x < 1024);
    assert.ok(anchor.y > 0 && anchor.y < 768);
  }
  assert.equal(Object.hasOwn(townMapAnchors, "iron-mine"), false);
  assert.equal(readFileSync(new URL("../src/assets/millhaven.png", import.meta.url)).subarray(1, 4).toString(), "PNG");
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

test("NPC positions follow the authoritative world, not a stale observation or memory", () => {
  const state = fixture();
  state.world!.actors.torvin.location_id = "forge";
  const agents = monitoredAgents(state);
  const torvin = agents.find(agent => agent.id === "torvin")!;
  const markup = townMapMarkup(state, [torvin]);
  assert.ok(markup.includes('data-map-location="forge"'));
  state.world!.actors.torvin.location_id = "tavern";
  const moved = townMapMarkup(state, [monitoredAgents(state).find(agent => agent.id === "torvin")!]);
  assert.ok(moved.includes('data-map-location="tavern"'));
  assert.ok(!moved.includes('data-map-location="forge"'));
});

test("unpictured and unverified locations stay explicitly off map and departed visitors are omitted", () => {
  const state = fixture();
  const torvin = monitoredAgents(state)[0];
  const agents: MapAgent[] = [
    { ...torvin, locationId: "iron-mine", location: "Millhaven iron mine" },
    { ...torvin, id: "stale", locationId: "forge", current: false },
    { ...torvin, id: "visitor", visitor: true, locationId: "tavern", location: "Tavern" },
    { ...torvin, id: "departed", visitor: true, current: false }
  ];
  const markup = townMapMarkup(state, agents);
  assert.ok(markup.includes("Millhaven iron mine (not pictured)"));
  assert.ok(markup.includes("(last observed / not live)"));
  assert.ok(!markup.includes('data-map-location="iron-mine"'));
  assert.ok(!markup.includes('data-map-location="forge"'));
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
  assert.ok(markup.includes('style="width:min(200%,206vh);min-width:1520px"'));
  assert.ok(!markup.includes("<script>"));
  assert.ok(!markup.includes("<img src=x"));
  assert.ok(!markup.includes("PRIVATE PLAN"));
});
