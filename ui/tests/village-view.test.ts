import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { agentProfileSchema, eventSchema, observationSchema, stateSchema, villageObservationSchema } from "../src/contracts.ts";
import { villageDashboardMarkup, villageMarkup } from "../src/village-view.ts";
import { inventoryUnitsUsed } from "../src/world-view.ts";
import { monitoredAgents, worldMonitorMarkup } from "../src/world-monitor.ts";
import { buildWorldTimeline, locationColour, timelineEventProjection, worldGanttMarkup } from "../src/world-gantt.ts";
import type { RunEvent } from "../src/contracts.ts";
import { budgetSummary, canReopenAdventure } from "../src/view-mode.ts";

const view = () => villageObservationSchema.parse({
  minute: 505, day: 1, time: "08:25", coins: 20,
  residents: [{ id: "torvin", name: "Torvin", occupation: "blacksmith", activity: "Wagon repair" }],
  stocks: { iron: 4 }, resource_inventory: { herbs: 1 },
  tasks: [{ id: "task-1", kind: "fetch_shovel", assignee: "torvin",
    status: "blocked", step: "plan", blocker: "Urgent work first" }],
  events: [{ minute: 500, actor: "torvin", action: "talk", target: "visitor",
    result: "I last heard it was at the stable.", witnesses: ["torvin", "visitor"] }],
  policy: "Every action takes five minutes."
});

test("village view shows local people, clock, stock and honest blocked progress", () => {
  const markup = villageMarkup(view());
  for (const text of ["DAY 1", "08:25", "20 coins", "Torvin", "iron: 4", "herbs: 1",
    "fetch shovel", "blocked", "Urgent work first", "stable"]) assert.ok(markup.includes(text));
  assert.ok(!markup.includes("mill-shed"));
});

test("village markup escapes dialogue and names rather than trusting generated content", () => {
  const village = view();
  village.residents[0].name = '<img src=x onerror="alert(1)">';
  village.events[0].result = "<script>bad()</script>";
  const markup = villageMarkup(village);
  assert.ok(!markup.includes("<img"));
  assert.ok(!markup.includes("<script>"));
  assert.ok(markup.includes("&lt;script&gt;"));
});

test("village activities render legacy minute references as clock times", () => {
  const value = view();
  value.residents[0].activity = "Available again at minute 485.";
  assert.ok(villageMarkup(value).includes("Available again at 08:05."));
  const state = expanded();
  state.world!.village!.residents.torvin.activity = "Sleeping until minute 1800.";
  assert.ok(villageDashboardMarkup(state, []).includes("Sleeping until 06:00 on day 2."));
});

test("village contract rejects invented progress states and negative stocks", () => {
  assert.equal(villageObservationSchema.safeParse({ ...view(), stocks: { grain: -1 } }).success, false);
  assert.equal(villageObservationSchema.safeParse({
    ...view(), tasks: [{ ...view().tasks[0], status: "claimed_complete" }]
  }).success, false);
});

function expanded() {
  const scenario = JSON.parse(readFileSync(resolve("..", "data", "scenarios", "millhaven-expanded.json"), "utf8"));
  const config = JSON.parse(readFileSync(resolve("..", "data", "config.json"), "utf8"));
  return stateSchema.parse({
    run: { schema_version: "1.0", id: "run", world_id: "world", session_id: "session",
      actor_id: "torvin", scenario_id: scenario.id, title: scenario.title, status: "paused",
      reason: "", created_at: "2026-01-01", actions: 0, model_calls: 0, cycles: 0,
      elapsed_seconds: 0, model: "test-model", goal: { id: "goal", objective: scenario.objective,
        type: scenario.goal_type, status: "active" },
      agents: Object.fromEntries(scenario.agents.map((agent: { id: string; role: string }) =>
        [agent.id, { role: agent.role, memory: {}, observation: null }])) },
    observation: null, memory: {}, config, in_flight: false, event_count: 0,
    world: {
      world_id: "world", revision: 1, entities: scenario.entities,
      actors: Object.fromEntries(scenario.agents.map((agent: { id: string; start_location: string; body: unknown }) =>
        [agent.id, { actor_id: agent.id, location_id: agent.start_location,
          start_location_id: agent.start_location, inventory: [], body: agent.body, needs: {} }])),
      village: { minute: 480, roster: scenario.village.residents,
        residents: Object.fromEntries(Object.entries(scenario.village.residents).map(([id, value]) => {
          const resident = value as { beliefs?: { subject: string }[]; relationships?: Record<string, number> };
          return [id, {
            beliefs: Object.fromEntries((resident.beliefs ?? []).map(belief => [belief.subject, belief])),
            memories: [], trust: resident.relationships ?? {}, activity: "Starting the day",
            next_work: 480, busy_until: 0, cargo: {}, delivery: null, delivery_cursor: 0
          }];
        })),
        stocks: scenario.village.stocks, coins: scenario.village.coins, tasks: {}, events: [],
        breakdown: false, repaired: false, well_dug: false, bread_after_repair: 0 }
    }
  });
}

test("budget summary uses frozen adventure limits and exhausted World stays read-only", () => {
  const state = expanded();
  state.run!.config = structuredClone(state.config);
  state.run!.config.budgets.max_actions = 600;
  state.run!.config.budgets.max_model_calls = 750;
  state.run!.config.budgets.max_seconds = 14400;
  state.run!.actions = state.run!.model_calls = 600;
  state.run!.status = "budget_exceeded";
  state.run!.elapsed_seconds = 5245;
  assert.equal(budgetSummary(state), "600 / 600 actions · 600 / 750 calls · 87.4 / 240.0 active minutes");
  const markup = worldMonitorMarkup(state);
  assert.ok(markup.includes("Budget limit reached"));
  assert.ok(markup.includes('href="/?budget=extend"'));
  assert.ok(!markup.includes("<form"));
  assert.ok(!markup.includes("data-command"));
});

test("only failed village completions offer reopening and World links without controls", () => {
  const state = expanded();
  state.run!.status = "succeeded";
  state.run!.goal.type = "actor_at_exit_location";
  state.run!.completion_analysis = {status: "completed", verdict: "fail", findings: [], story: [],
    final_verification: null, story_review: null, model: null, model_calls: 1, elapsed_seconds: 0,
    completed_at: null, error: null};
  assert.equal(canReopenAdventure(state.run, true), false);
  state.run!.goal.type = "village_restored";
  assert.equal(canReopenAdventure(state.run, true), true);
  assert.equal(canReopenAdventure(state.run, false), false);
  const markup = worldMonitorMarkup(state);
  assert.ok(markup.includes("Continue the unfinished day in Player view"));
  assert.ok(!markup.includes("data-command"));
  state.run!.completion_analysis.verdict = "pass";
  assert.equal(canReopenAdventure(state.run, true), false);
  state.run!.completion_analysis.verdict = "fail";
  state.run!.status = "cancelled";
  assert.equal(canReopenAdventure(state.run, true), false);
});

    test("world monitor shows all twelve actors at actual locations without private plans or simulation controls", () => {
      const state = expanded();
      state.world!.actors.torvin.location_id = "village-square";
      state.world!.entities["village-square"] = { name: "Actual square", kind: "location", description: "" };
      state.run!.agents!.torvin.memory = { subgoal: "PRIVATE PLAN", plan: ["SECRET TASK"] };
      const agents = monitoredAgents(state);
      assert.equal(agents.length, 12);
      assert.equal(agents.find(agent => agent.id === "torvin")!.location, "Actual square");
      const markup = worldMonitorMarkup(state);
      assert.equal((markup.match(/data-monitor-agent=/g) ?? []).length, 12);
      for (const text of ["Actual square", "08:00", "Day 1", "Paused", "12 agents", "Read-only"]) {
        assert.ok(markup.includes(text));
      }
      for (const text of ["PRIVATE PLAN", "SECRET TASK", "<textarea", "<details", "data-command"]) {
        assert.ok(!markup.includes(text));
      }
    });

    test("world monitor prioritizes real sleep and committed work over stale activity", () => {
      const state = expanded();
      const resident = state.world!.village!.residents.torvin;
      resident.activity = "Old activity";
      resident.life = { hungry: false, missed_meals: 0, meals: {}, sleeping: true,
        sleep_until: 1800, last_woke: 0, work: null };
      assert.equal(monitoredAgents(state)[0].activity, "Sleeping until 06:00 on day 2");
      resident.life.sleeping = false;
      resident.life.work = { location: "cottages", finishes_at: 510, outputs: { bread: 2 },
        equipment: null, kind: "production" };
      assert.equal(monitoredAgents(state)[0].activity, "Completing a batch until 08:30");
      resident.life.work = null;
      state.in_flight = true;
      assert.equal(monitoredAgents(state)[0].activity, "Deciding what to do");
      state.run!.status = "error";
      assert.equal(monitoredAgents(state)[0].deciding, false);
      assert.ok(worldMonitorMarkup(state).includes("Last recorded world state"));
    });

    test("world monitor handles missing worlds and never invents a clock or current location", () => {
      const state = expanded();
      state.world = null;
      assert.equal(monitoredAgents(state).length, 12);
      const markup = worldMonitorMarkup(state);
      assert.ok(markup.includes("Untimed world"));
      assert.ok(markup.includes("World snapshot unavailable"));
      assert.ok(markup.includes("Location not yet available"));
      state.run = null;
      assert.ok(worldMonitorMarkup(state).includes("No agents to monitor yet"));
    });

    test("world monitor supports generic single agents and labels completed actions honestly", () => {
      const state = expanded();
      state.world!.village = null;
      state.run!.agents = {};
      state.world!.actors = { torvin: state.world!.actors.torvin };
      state.world!.entities.torvin = { name: "<script>Torvin</script>", kind: "npc", description: "" };
      state.memory = { recent: [{ action_id: "open", explanation: "PRIVATE RESULT" }] };
      state.observation = null;
      state.run!.agents = { torvin: { role: "Agent", memory: state.memory, observation: null } };
      const markup = worldMonitorMarkup(state);
      assert.ok(markup.includes("1 agent"));
      assert.ok(markup.includes("Last action: open"));
      assert.ok(markup.includes("&lt;script&gt;Torvin&lt;/script&gt;"));
      assert.ok(!markup.includes("<script>"));
      assert.ok(!markup.includes("PRIVATE RESULT"));
    });

test("world monitor shows cooldowns and automatic deliveries rather than raw action results", () => {
  const state = expanded();
  const resident = state.world!.village!.residents.torvin;
  resident.activity = "Go: Cottage lane";
  resident.available_at = 485;
  assert.equal(monitoredAgents(state)[0].activity, "Available again at 08:05");
  resident.available_at = 480;
  assert.equal(monitoredAgents(state)[0].activity, "Ready for next turn");
  assert.ok(!worldMonitorMarkup(state).includes("Go: Cottage lane"));
  state.world!.village!.wagon = { repaired: true, driver: "torvin", automatic: true,
    loads: [], route_index: 0, phase: "travel", next_event_at: 485, blocker: null };
  assert.equal(monitoredAgents(state)[0].activity, "Running the wagon delivery route");
});

test("expanded overview tracks every real villager and their private evidence", () => {
  const state = expanded();
  const markup = villageDashboardMarkup(state, []);
  assert.equal((markup.match(/data-village-resident=/g) ?? []).length, 12);
  for (const id of Object.keys(state.world!.village!.roster)) {
    assert.ok(markup.includes(`data-village-resident="${id}"`));
  }
  for (const text of ["12 AI villagers", "Operator overview", "Torvin", "Bren", "Mara", "Elsie",
    "Hobb", "Jory", "Ada", "Nella", "Rowan", "Garrick", "Iona", "Finn", "Private beliefs",
    "Relationships", "Goals, job", "Village stock", "memory:lent-to-bren", "Mill shed",
    "Residents contributed: 0/12"]) assert.ok(markup.includes(text), text);
  assert.ok(!villageMarkup(view()).includes("memory:lent-to-bren"));
});

test("new expanded villagers all start at their cottage homes, including Elsie", () => {
  const state = expanded();
  const village = state.world!.village!;
  assert.equal(Object.keys(state.world!.actors).length, 12);
  for (const [id, actor] of Object.entries(state.world!.actors)) {
    assert.equal(actor.location_id, "cottages", id);
    assert.equal(actor.start_location_id, village.roster[id].home, id);
  }
  assert.equal(village.roster.elsie.home, "cottages");
  assert.equal(village.roster.elsie.work, "tavern");
});

test("overview uses actual positions and inventories rather than stale saved observations", () => {
  const state = expanded();
  state.world!.actors.torvin.location_id = "tavern-yard";
  state.world!.actors.torvin.inventory = ["shovel"];
  state.world!.village!.residents.nella.cargo = { grain: 1 };
  assert.equal(inventoryUnitsUsed(state.world!, "nella"), 1);
  state.world!.village!.role_contributions = { torvin: 3 };
  state.world!.village!.project_contributions = { dig_well: { torvin: 2, bren: 1 } };
  state.world!.village!.breakdown = true;
  const markup = villageDashboardMarkup(state, []);
  assert.match(markup, /Location:<\/b> Ground behind the tavern/);
  assert.match(markup, /Carrying:<\/b> Torvin&#39;s shovel/);
  assert.ok(markup.includes("grain: 1"));
  assert.ok(markup.includes("Mill: broken"));
  assert.ok(markup.includes("Torvin 10 work min, Bren 5 work min"));
  assert.ok(markup.includes("Residents contributed: 1/12"));
});

test("dashboard renders each NPC's latest decision, plan and explicit outcome safely", () => {
  const state = expanded();
  state.run!.agents!.torvin.memory = {
    subgoal: "Bring supplies", plan: ["Recover shovel", "Meet Bren"],
    recent: [{ explanation: "Both named partners must physically be at the work site." }]
  };
  const events = ["Old decision", 'Find Bren <script>bad()</script>'].map((justification, index) =>
    eventSchema.parse({ id: String(index), sequence: index, run_id: "run", session_id: "session",
      actor_id: "torvin", kind: "decision", cycle: index, timestamp: "2026-01-01",
      payload: { action_id: "village_project", justification } }));
  const markup = villageDashboardMarkup(state, events);
  assert.ok(markup.includes("Bring supplies"));
  assert.ok(markup.includes("Recover shovel; Meet Bren"));
  assert.ok(markup.includes("Last outcome:"));
  assert.ok(markup.includes("Both named partners"));
  assert.ok(markup.includes("&lt;script&gt;"));
  assert.ok(!markup.includes("<script>") && !markup.includes("Old decision"));
});

test("dashboard resolves custom member names and escapes beliefs and memories", () => {
  const state = expanded();
  state.run!.agent_profiles = {
    torvin: agentProfileSchema.parse({
      schema_version: "1.0", id: "torvin", name: '<img src=x onerror="bad()">',
      identity: "Smith", personality: "Practical", operating_rules: ["Be honest"],
      constraints: ["Use own knowledge"], body: state.world!.actors.torvin.body, scenario_bodies: {}
    })
  };
  state.world!.village!.residents.torvin.beliefs.shovel.source = "<script>belief</script>";
  state.world!.village!.events.push({
    minute: 480, actor: "torvin", action: "say", target: "bren",
    result: "<script>memory</script>", witnesses: ["torvin"]
  });
  state.world!.village!.residents.torvin.memories.push(0);
  const markup = villageDashboardMarkup(state, []);
  assert.ok(!markup.includes("<img") && !markup.includes("<script>"));
  assert.ok(markup.includes("&lt;img") && markup.includes("&lt;script&gt;memory"));
});

test("overview is absent in original scenarios and rejects corrupt memory references", () => {
  const state = expanded();
  state.world!.village!.residents.torvin.memories = [999];
  assert.throws(() => villageDashboardMarkup(state, []), /Invalid village memory/);
  state.world!.village!.roster = {};
  assert.equal(villageDashboardMarkup(state, []), "");
  state.world!.village = null;
  assert.equal(villageDashboardMarkup(state, []), "");
});

test("dashboard explicitly shows actors still binding during streamed startup", () => {
  const state = expanded();
  delete state.world!.actors.iona;
  const markup = villageDashboardMarkup(state, []);
  assert.equal((markup.match(/data-village-resident=/g) ?? []).length, 12);
  assert.ok(markup.includes("Initializing actor: awaiting world binding."));
  assert.ok(markup.includes('data-village-resident="iona"'));
});

test("daily tracking shows the tavern meal, hunger and actual sleeping deadlines", () => {
  const state = expanded();
  state.world!.village!.residents.torvin.life = {
    hungry: true, missed_meals: 1, sleeping: true, sleep_until: 1920, last_woke: 480,
    meals: { "0:daily": { kind: "daily", due: 1140, status: "missed", source: null } },
    work: null
  };
  state.world!.village!.minute = 1350;
  const parsed = stateSchema.parse(state);
  const markup = villageDashboardMarkup(parsed, []);
  for (const text of ["Sleeping until day 2 08:00", "Daily meal:</b> 19:00 at the tavern", "Hungry",
    "missed meals: 1", "daily: missed", "Wake: 08:00 / bedtime: 22:00"]) {
    assert.ok(markup.includes(text), text);
  }
});

test("committed baking has a real deadline and is distinct from interruptible projects", () => {
  const state = expanded();
  state.world!.village!.residents.jory.life = {
    hungry: false, missed_meals: 0, sleeping: false, sleep_until: null, last_woke: 360,
    meals: { "0:daily": { kind: "daily", due: 1140,
      status: "eaten", source: "<script>fake source</script>" } },
    work: { location: "bakery", finishes_at: 575, outputs: { bread: 2 }, equipment: "oven", kind: "production" }
  };
  const markup = villageDashboardMarkup(stateSchema.parse(state), []);
  assert.ok(markup.includes("Must finish at day 1 09:35 before leaving, eating or sleeping"));
  assert.ok(markup.includes("Batch duration: 30 minutes; must finish once started"));
  assert.ok(markup.includes("&lt;script&gt;fake source"));
  assert.ok(!markup.includes("<script>"));
});

test("daily-life contract rejects negative missed meals", () => {
  const state = expanded();
  const life = { hungry: false, missed_meals: -1, sleeping: false,
    sleep_until: null, last_woke: 480, meals: {}, work: null };
  const residents = { ...state.world!.village!.residents, torvin: { ...state.world!.village!.residents.torvin, life } };
  assert.equal(stateSchema.safeParse({
    ...state, world: { ...state.world, village: { ...state.world!.village, residents } }
  }).success, false);
});

test("dashboard shows mixed wagon cargo, standing route and public bell notices", () => {
  const state = expanded();
  state.world!.village!.wagon = {
    repaired: true, driver: "nella", automatic: true, route_index: 2, phase: "travel",
    next_event_at: 510, blocker: null, loads: [
      { resource: "flour", quantity: 2, seller: "mara", buyer: "jory", destination: "bakery", price: 2 },
      { resource: "grain", quantity: 3, seller: "ada", buyer: "mara", destination: "watermill", price: 1 }
    ]
  };
  state.world!.village!.notices = [{
    minute: 840, actor: "village-bell", action: "breakdown", target: "mill-drive",
    result: "Mill drive failed at 14:00.", witnesses: ["hobb", "mara"]
  }];
  const markup = villageDashboardMarkup(stateSchema.parse(state), []);
  for (const text of ["Six-unit wagon / Working", "Automatic supply circuit active", "5/6 units",
    "Public noticeboard", "Mill drive failed at 14:00", "no repair prerequisite"]) {
    assert.ok(markup.includes(text), text);
  }
});

test("full-rule dashboard retains horse, freshness, deadlines, hourly stock and stacked fatigue", () => {
  const state = expanded();
  const village = state.world!.village!;
  village.minute = 1560;
  village.residents.torvin.life = {
    hungry: true, missed_meals: 1, sleeping: false, sleep_until: null, last_woke: 480, work: null,
    meals: { "0:daily": { kind: "daily", due: 1140, status: "missed", source: null } },
    fatigue_days: [1], tea_day: -1, urgent_project: "dig_well", urgent_until: 1565
  };
  village.wagon = { repaired: true, driver: "ada", automatic: false, loads: [],
    hitched: true, route_index: 0, phase: "travel", next_event_at: 0, blocker: null };
  village.deadlines = { bread_delivery: { due: 1140, completed_at: null, overdue: true } };
  village.stock_history = [{ minute: 1500, stocks: { forge: { iron: 4 } }, coins: { torvin: 20 } }];
  village.stock_lots = { tavern: { bread: [{quantity: 2, produced_at: 480, expires_at: 1920}] } };
  const markup = villageDashboardMarkup(stateSchema.parse(state), []);
  for (const text of ["4x (missed yesterday&#39;s meal + tiredness)", "Explicit bedtime override",
    "Horse: Hitched", "Driver: Ada / roads only", "Overdue; recovery allowed",
    "Hourly workplace stock records (1)", "Day 2 01:00", "iron: 4", "expire day",
    "Batch freshness and spoilage"]) {
    assert.ok(markup.includes(text) || markup.includes(text.replace("&#39;", "'")), text);
  }
});

test("personal view retains saved stock reports and known routes without showing the global ledger", () => {
  const value = villageObservationSchema.parse({ ...view(), personal: {
    occupation: "blacksmith", personality: "Practical", skills: [], beliefs: {}, relationships: {},
    schedule: [], routine: {minute: 480, location: "forge", activity: "Work"},
    job: null, next_work: 480, busy_until: 0, deliveries: [], active_delivery: null,
    routes: [{destination: "watermill", path: ["village-square", "watermill"], steps: 2}],
    stock_report: { bakery: { flour: 6 } }, stock_report_at: 500, shipments: [], timber_route: false,
    life: { hungry: true, missed_meals: 1, sleeping: false, sleep_until: null, last_woke: 480,
      meals: {}, work: null, fatigue_days: [0], tea_day: -1, cooldown_factor: 4,
      rest_due: false, wake_minute: 480, bed_minute: 1320, home: "cottages", tavern: "tavern" }
  } });
  const markup = villageMarkup(value);
  for (const text of ["Shortest known routes", "2 steps", "Stocks last read", "08:20", "flour: 6"]) {
    assert.ok(markup.includes(text), text);
  }
  assert.ok(!markup.includes("Hourly workplace"));
});

function timelineEvent(sequence: number, kind: string, minute: number, payload: unknown,
  actor = "torvin", runId = "run"): RunEvent {
  return { id: `${runId}:${sequence}`, sequence, run_id: runId, kind, actor_id: actor,
    session_id: "session", cycle: 1, timestamp: "2026-01-01", village_minute: minute, payload };
}

function timelineObservation(minute: number, location = "cottages", available = minute) {
  return { self: { actor_id: "torvin", location_id: location },
    village: { minute, personal: { available_at: available } } };
}

test("Gantt reconstructs real positions and action durations rather than scheduled workplaces", () => {
  const state = expanded();
  state.world!.village!.minute = 500;
  state.world!.actors.torvin.location_id = "forge";
  const events = [
    timelineEvent(1, "observation", 480, timelineObservation(480)),
    timelineEvent(2, "action_request", 485, { request_id: "move", action_id: "go", arguments: { target: "square-cottages" } }),
    timelineEvent(3, "action_result", 485, { request_id: "move", outcome: "APPLIED",
      explanation: "Walked.", observation: timelineObservation(485, "village-square", 490) }),
    timelineEvent(4, "observation", 495, timelineObservation(495, "forge"))
  ];
  const before = JSON.stringify(events);
  const model = buildWorldTimeline(state, events, monitoredAgents(state))!;
  const row = model.rows.find(row => row.id === "torvin")!;
  assert.deepEqual(row.locations, [
    { start: 480, end: 485, location: "cottages" },
    { start: 485, end: 495, location: "village-square" },
    { start: 495, end: 500, location: "forge" }
  ]);
  assert.equal(row.spans[0].kind, "travel");
  assert.equal(row.spans[0].end - row.spans[0].start, 5);
  assert.equal(model.rows.length, 12);
  assert.equal(JSON.stringify(events), before);
  assert.equal(buildWorldTimeline(state, [...events].reverse(), monitoredAgents(state))!.start, 480);
});

test("Gantt keeps free and rejected actions instantaneous and clips committed work at now", () => {
  const state = expanded();
  state.world!.village!.minute = 500;
  const events = [timelineEvent(1, "observation", 480, timelineObservation(480))];
  for (const [index, action, outcome, available] of [
    [2, "read", "APPLIED", 510], [4, "village_work", "REJECTED", 510],
    [6, "village_work", "APPLIED", 530]
  ] as const) {
    events.push(timelineEvent(index, "action_request", 485, {
      request_id: String(index), action_id: action, arguments: {}
    }), timelineEvent(index + 1, "action_result", 485, {
      request_id: String(index), outcome, explanation: "Recorded result.",
      observation: timelineObservation(485, "cottages", available)
    }));
  }
  const spans = buildWorldTimeline(state, events, monitoredAgents(state))!.rows[0].spans;
  assert.equal(spans[0].kind, "free");
  assert.equal(spans[0].end, spans[0].start);
  assert.equal(spans[1].kind, "rejected");
  assert.equal(spans[1].end, spans[1].start);
  assert.equal(spans[2].end, 500);
  assert.ok(spans.every(span => span.end <= 500));
});

test("Gantt includes actual rest, automatic wagon movement, route spans and public milestones", () => {
  const state = expanded();
  const village = state.world!.village!;
  village.minute = 520;
  state.world!.actors.torvin.location_id = "farm";
  const first = { ...timelineObservation(480), village: { minute: 480, personal: {
    life: { hungry: false, missed_meals: 0, meals: {}, sleeping: true, sleep_until: 490,
      last_woke: 0, work: null, rest_due: true, wake_minute: 490, bed_minute: 1320,
      home: "cottages", tavern: "tavern" }
  } } };
  const events = [
    timelineEvent(1, "observation", 480, first),
    timelineEvent(2, "action_request", 490, { request_id: "route", action_id: "village_start_route", arguments: {} }),
    timelineEvent(3, "action_result", 490, { request_id: "route", outcome: "APPLIED", explanation: "Started route.",
      observation: timelineObservation(490, "village-square", 495) })
  ];
  village.events.push({ minute: 500, actor: "torvin", action: "wagon_drive", target: "farm",
    result: "Driving to farm.", witnesses: ["torvin"] });
  village.notices.push({ minute: 505, actor: "village-bell", action: "wagon_ready", target: "wagon",
    result: "The wagon is repaired.", witnesses: [] });
  const model = buildWorldTimeline(state, events, monitoredAgents(state))!;
  assert.ok(model.rows[0].spans.some(span => span.kind === "sleep" && span.start === 480 && span.end === 490));
  assert.ok(model.rows[0].spans.some(span => span.kind === "route" && span.start === 490 && span.end === 520));
  assert.ok(model.rows[0].locations.some(band => band.location === "farm" && band.start === 500));
  assert.ok(model.milestones.some(mark => mark.label === "Wagon repaired" && mark.at === 505));
  village.events.push({ minute: 510, actor: "torvin", action: "wagon_paused", target: "wagon",
    result: "Driver stopped.", witnesses: [] });
  assert.ok(buildWorldTimeline(state, events, monitoredAgents(state))!.rows[0].spans
    .some(span => span.kind === "route" && span.end === 510));
});

test("Gantt handles midnight, stale runs, unknown history, escaping and untimed worlds", () => {
  const state = expanded();
  state.world!.village!.minute = 1450;
  const events = [timelineEvent(1, "observation", 1430, timelineObservation(1430)),
    timelineEvent(2, "observation", 200, timelineObservation(200), "torvin", "old-run")];
  const agents = monitoredAgents(state);
  agents[0].name = '<img src=x onerror="bad()">';
  const markup = worldGanttMarkup(state, events, agents);
  assert.ok(markup.includes("00:10 on day 2"));
  assert.ok(markup.includes("&lt;img"));
  assert.ok(!markup.includes("<img"));
  assert.equal((markup.match(/data-gantt-agent=/g) ?? []).length, 12);
  assert.equal(buildWorldTimeline(state, events, agents)!.start, 1430);
  assert.ok(buildWorldTimeline(state, [], agents)!.rows.every(row => row.locations.length === 0));
  assert.equal(locationColour("farm"), locationColour("farm"));
  assert.match(locationColour("unknown-place"), /^#[0-9a-f]{6}$/i);
  state.world!.village = null;
  assert.equal(worldGanttMarkup(state, events, agents), "");
});

test("midnight observation extends the paused chart and shows Elsie's recorded sleep", () => {
  const state = expanded();
  state.run!.status = "paused";
  state.world!.village!.minute = 1380;
  state.world!.actors.elsie.location_id = "cottages";
  state.observation = observationSchema.parse({
    contract_version: "1.0", world_id: "world", session_id: "session", observation_revision: "r1",
    self: {actor_id: "elsie", location_id: "cottages", inventory: [], needs: [],
      body: {id: "human", description: "Human", capabilities: [],
        carrying: {rule: "slots", limit: 2, used: 0}}},
    location: {id: "cottages", description: "Home"}, narrative: "Sleeping at home.",
    visible_entities: [], actions: [], village: {...view(), minute: 1440, day: 2, time: "00:00"}
  });
  const observed = {self: {actor_id: "elsie", location_id: "cottages"},
    village: {minute: 1380, personal: {life: {
      hungry: false, missed_meals: 0, meals: {}, sleeping: true, sleep_until: 1860,
      last_woke: 420, work: null, rest_due: true, wake_minute: 420, bed_minute: 1380,
      home: "cottages", tavern: "tavern"
    }}}};
  const events = [timelineEvent(1, "observation", 1380, observed, "elsie")];
  const model = buildWorldTimeline(state, events, monitoredAgents(state))!;
  assert.equal(model.now, 1440);
  assert.ok(model.rows.find(row => row.id === "elsie")!.spans.some(span =>
    span.kind === "sleep" && span.start === 1380 && span.end === 1440));
  const markup = worldMonitorMarkup(state, events);
  assert.ok(markup.includes("CURRENT GAME TIME"));
  assert.ok(markup.includes("Paused 00:00 on day 2"));
  assert.ok(markup.includes("Sleeping"));
  assert.equal(state.world!.village!.minute, 1380);
});

test("Gantt uses simulated elapsed time for other timed worlds and never the event wall timestamp", () => {
  const state = expanded();
  state.world!.village = null;
  state.world!.clock = { seconds: 90, rate: 1, running: false, mode: "decision_paused" };
  const observed = (seconds: number) => ({
    self: { actor_id: "torvin", location_id: "cottages" }, clock: { seconds }
  });
  const events = [
    timelineEvent(1, "observation", 0, observed(0)),
    timelineEvent(2, "action_request", 0, { request_id: "work", action_id: "mix", arguments: {} }),
    timelineEvent(3, "action_result", 0, { request_id: "work", outcome: "APPLIED",
      explanation: "Mixed.", observation: observed(30) })
  ];
  const model = buildWorldTimeline(state, events, monitoredAgents(state))!;
  assert.equal(model.now, 1.5);
  assert.equal(model.rows[0].spans[0].end, 0.5);
  assert.ok(worldGanttMarkup(state, events, monitoredAgents(state)).includes("00:01:30"));
});

test("Gantt combines consecutive waits but preserves production batches as separate actions", () => {
  const state = expanded();
  state.world!.village!.minute = 510;
  const events = [timelineEvent(1, "observation", 480, timelineObservation(480))];
  for (const [sequence, minute, action] of [
    [2, 480, "village_wait"], [4, 490, "village_wait"], [6, 500, "village_work"], [8, 505, "village_work"]
  ] as const) {
    events.push(timelineEvent(sequence, "action_request", minute, {
      request_id: String(sequence), action_id: action, arguments: {}
    }), timelineEvent(sequence + 1, "action_result", minute, {
      request_id: String(sequence), outcome: "APPLIED", explanation: "Recorded.",
      observation: timelineObservation(minute, "cottages", minute + (action === "village_wait" ? 10 : 5))
    }));
  }
  const spans = buildWorldTimeline(state, events, monitoredAgents(state))!.rows[0].spans;
  assert.equal(spans.filter(span => span.kind === "wait").length, 1);
  assert.equal(spans[0].end - spans[0].start, 20);
  assert.equal(spans.filter(span => span.kind === "action").length, 2);
  const markup = worldGanttMarkup(state, events, monitoredAgents(state));
  assert.match(markup, /class="gantt-cell-title"[^>]*>Waiting<\/text>/);
  assert.match(markup, /class="gantt-cell-time"[^>]*>20 min \/ 08:00 - 08:20<\/text>/);
});

test("Gantt stores only relevant timing facts rather than model requests or private memories", () => {
  const event = timelineEvent(1, "observation", 480, {
    ...timelineObservation(480), narrative: "PRIVATE NARRATIVE", memory: { plan: "PRIVATE PLAN" }
  });
  const before = JSON.stringify(event);
  const projected = timelineEventProjection(event)!;
  assert.ok(!JSON.stringify(projected).includes("PRIVATE"));
  assert.equal(JSON.stringify(event), before);
  assert.equal(timelineEventProjection(timelineEvent(2, "proxy_request", 480, { messages: "PRIVATE" })), null);
});

test("Gantt fits the available width and zooms only the display with adaptive clock ticks", () => {
  const state = expanded();
  state.world!.village!.minute = 660;
  const events = [timelineEvent(1, "observation", 300, timelineObservation(300))];
  const before = JSON.stringify({ state, events });
  const fit = worldMonitorMarkup(state, events, { viewportWidth: 900, zoom: 1 });
  const zoomed = worldMonitorMarkup(state, events, { viewportWidth: 900, zoom: 2 });
  assert.ok(fit.includes('width="900"'));
  assert.ok(zoomed.includes('width="1800"'));
  assert.match(fit, /data-timeline-zoom="out"[^>]* disabled/);
  assert.match(zoomed, /<output[^>]*>200%<\/output>/);
  assert.ok(zoomed.includes('data-timeline-zoom="fit"'));
  assert.ok((zoomed.match(/class="gantt-gridline"/g) ?? []).length >
    (fit.match(/class="gantt-gridline"/g) ?? []).length);
  assert.equal((zoomed.match(/data-gantt-agent=/g) ?? []).length, 12);
  assert.ok(!zoomed.includes("data-command"));
  assert.equal(JSON.stringify({ state, events }), before);
});

test("Gantt timescale bounds are 100% to 800% and fit respects mobile plot width", () => {
  const state = expanded();
  const agents = monitoredAgents(state);
  const minimum = worldGanttMarkup(state, [], agents, { zoom: -1 });
  const maximum = worldGanttMarkup(state, [], agents, { zoom: 99 });
  const mobile = worldGanttMarkup(state, [], agents, { viewportWidth: 188 });
  assert.match(minimum, /<output[^>]*>100%<\/output>/);
  assert.match(maximum, /<output[^>]*>800%<\/output>/);
  assert.match(maximum, /data-timeline-zoom="in"[^>]* disabled/);
  assert.ok(maximum.includes('width="7200"'));
  assert.ok(mobile.includes('width="188"'));
});

test("Gantt uses taller aligned rows and rounded tiles without changing recorded timings", () => {
  const state = expanded();
  state.world!.village!.minute = 500;
  const events = [
    timelineEvent(1, "observation", 480, timelineObservation(480)),
    timelineEvent(2, "action_request", 485, { request_id: "work", action_id: "village_work", arguments: {} }),
    timelineEvent(3, "action_result", 485, { request_id: "work", outcome: "APPLIED",
      explanation: "Recorded work.", observation: timelineObservation(485, "cottages", 495) })
  ];
  const agents = monitoredAgents(state);
  const before = buildWorldTimeline(state, events, agents);
  const markup = worldGanttMarkup(state, events, agents);
  assert.equal((markup.match(/style="height:100px"/g) ?? []).length, 12);
  assert.ok(markup.includes('viewBox="0 0 900 1288"'));
  assert.match(markup, /<rect[^>]*height="64" rx="7"/);
  assert.match(markup, /<rect[^>]*height="82" rx="8"/);
  assert.ok(markup.includes('id="gantt-tile"'));
  assert.ok(markup.includes("Recorded work."));
  assert.deepEqual(buildWorldTimeline(state, events, agents), before);
});

test("Gantt cells show recorded quantities, duration and clock range with escaped summaries", () => {
  const state = expanded();
  state.world!.village!.minute = 500;
  const events = [
    timelineEvent(1, "observation", 480, timelineObservation(480)),
    timelineEvent(2, "action_request", 485, { request_id: "work", action_id: "village_work", arguments: {} }),
    timelineEvent(3, "action_result", 485, { request_id: "work", outcome: "APPLIED",
      explanation: "Used 2 iron; made 2 wagon parts. <img src=x>",
      observation: timelineObservation(485, "forge", 495) })
  ];
  const agents = monitoredAgents(state);
  const markup = worldGanttMarkup(state, events, agents);
  assert.match(markup, /class="gantt-cell-title"[^>]*>Forge work<\/text>/);
  assert.match(markup, /class="gantt-cell-time"[^>]*>10 min \/ 08:05 - 08:15<\/text>/);
  assert.match(markup, /class="gantt-cell-detail"[^>]*>Used 2 iron; made 2 wagon parts\. &lt;img src=x&gt;<\/text>/);
  assert.ok(markup.includes("Torvin&#39;s forge"));
  assert.ok(!markup.includes("<img"));
  const narrow = worldGanttMarkup(state, events, agents, { viewportWidth: 188 });
  assert.ok(!narrow.includes('class="gantt-cell-detail"'));
  assert.ok(narrow.includes("Used 2 iron; made 2 wagon parts. &lt;img src=x&gt;"));
  const zoomed = worldGanttMarkup(state, events, agents, { viewportWidth: 188, zoom: 8 });
  assert.ok(zoomed.includes('class="gantt-cell-detail"'));
});
