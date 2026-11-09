import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { agentProfileSchema, communicationSchema, eventSchema, observationSchema, stateSchema, villageFirewoodSchema, villageObservationSchema, villageResearchSchema } from "../src/contracts.ts";
import { communicationMarkup, firewoodMarkup, villageDashboardMarkup, villageMarkup, villageSystemsMarkup } from "../src/village-view.ts";
import { inventoryUnitsUsed } from "../src/world-view.ts";
import { monitoredAgents, worldMonitorMarkup } from "../src/world-monitor.ts";
import { buildWorldTimeline, locationColour, timelineEventProjection, worldGanttMarkup } from "../src/world-gantt.ts";
import type { RunEvent } from "../src/contracts.ts";
import { budgetSummary, canReopenAdventure } from "../src/view-mode.ts";

test("firewood view distinguishes local observation from operator heating truth", () => {
  const firewood = villageFirewoodSchema.parse({
    enabled: true, season: "winter", supplier: "Rowan", firewood_per_log: 4, split_minutes: 15,
    sites: [{ location: "cottages", buyer: "Iona", stock: 0, reserve: 14, consumed: 3 }],
    households: [{ id: "hearth-1", location: "cottages", members: ["Ada", "Bren"],
      occupied: true, status: "cold", warm_until: 0 }], policy: "Real logs and funded physical deliveries."
  });
  const local = villageMarkup(villageObservationSchema.parse({ ...view(), firewood }));
  assert.ok(local.includes("Local firewood"));
  assert.ok(local.includes("Only fuel stocks and hearths physically here"));
  assert.ok(local.includes("Cold homes have no health penalties"));
  assert.ok(local.includes("FUEL SHORTAGE"));
  assert.ok(local.includes("Ada, Bren"));
  assert.ok(!local.includes("Privileged world view"));
  const state = expanded();
  state.world!.village!.dashboard = {
    firewood, orders: [], products: [], accounts: [], activities: [], projects: [],
    quests: [], visitors: [], incidents: [], time_use: {}
  };
  const operator = villageSystemsMarkup(state);
  assert.ok(operator.includes("Town firewood"));
  assert.ok(operator.includes("Privileged world view; not shared NPC knowledge"));
  assert.ok(operator.includes("funded by Iona"));
  assert.ok(operator.includes("3 consumed"));
  assert.ok(!villageFirewoodSchema.safeParse({ ...firewood,
    sites: [{ ...firewood.sites[0], stock: -1 }] }).success);
  assert.equal(firewoodMarkup({ ...firewood, enabled: false }), "");
});

test("hearth panels escape text and display real warm-until times", () => {
  const unsafe = "<img onerror=alert(1)>";
  const firewood = villageFirewoodSchema.parse({
    enabled: true, season: "autumn", supplier: unsafe, firewood_per_log: 4, split_minutes: 15,
    sites: [{ location: unsafe, buyer: unsafe, stock: 1, reserve: 2, consumed: 1 }],
    households: [{ id: unsafe, location: "cottages", members: [unsafe],
      occupied: true, status: "warm", warm_until: 1505 }], policy: unsafe
  });
  const markup = firewoodMarkup(firewood);
  assert.ok(!markup.includes("<img"));
  assert.ok(markup.includes("&lt;img"));
  assert.ok(markup.includes("warm until day 2 01:05"));
});

test("communication separates operator truth from stale personal receipts with named boxes and appointments", () => {
  const communication = communicationSchema.parse({
    enabled: true, scope: "personal received snapshots", policy: "No remote delivery.",
    notices: [{
      id: "notice-99", version: 2, author: "Torvin", recipients: ["Bren"],
      subject: "Well <repair>", description: "Bring real supplies", type: "meeting",
      status: "accepted", quantity: null, destination: "Village Well", due_at: 3420, updated_at: 3370,
      private: false, response: "I can come.", source: "conversation: Garrick", learned_at: 3380,
      confidence: "secondhand", hops: 1, source_chain: ["noticeboard", "Garrick"],
      freshness: "Snapshot; may be out of date",
      appointment: {
        id: "appointment-notice-99", version: 2, project: "dig_well", members: ["Torvin", "Bren"],
        accepted: ["Torvin", "Bren"], place: "Village Well", due_at: 3420, end_at: 3480, status: "confirmed"
      }, commitment: null
    }],
    boxes: [{ id: "workplace-inbox-torvin", owner: "Torvin", place: "Forge", here: true, can_read: true }],
    share_candidates: []
  });
  const personal = communicationMarkup(communication);
  for (const text of ["Personally received snapshots", "may be stale", "Garrick", "secondhand",
    "Torvin &amp;", "day 3 09:00", "Forge", "Here; yours to read", "No remote delivery",
    "Well &lt;repair&gt;"]) {
    assert.ok(personal.includes(text), text);
  }
  assert.ok(!personal.includes("appointment-notice-99"));
  assert.ok(!personal.includes("workplace-inbox-torvin"));
  const operator = communicationMarkup(communication, true);
  assert.ok(operator.includes("Privileged world view"));
  assert.ok(operator.includes("not shared NPC knowledge"));
  assert.ok(!operator.includes("Here; yours to read"));
});

test("commitment UI never describes acceptance as delivery and escapes reply text", () => {
  const communication = communicationSchema.parse({
    enabled: true, scope: "personal received snapshots", policy: "Physical only",
    notices: [{
      id: "notice-1", version: 2, author: "Jory", recipients: ["Mara"], subject: "Flour",
      description: "For the bakery", type: "supply_request", status: "accepted", quantity: 2,
      destination: "Bakery", due_at: 900, updated_at: 510, private: false, response: "<script>reply</script>",
      source: "noticeboard", learned_at: 520, confidence: "direct", hops: 0, source_chain: ["noticeboard"],
      freshness: "Snapshot; may be out of date", appointment: null,
      commitment: { status: "accepted", delivered: 0, paid: 0, buyer: "Jory", supplier: "Mara" }
    }], boxes: [], share_candidates: []
  });
  const markup = communicationMarkup(communication);
  assert.ok(markup.includes("0 units unloaded"));
  assert.ok(markup.includes("0 coins paid"));
  assert.ok(markup.includes("Acceptance alone transfers nothing"));
  assert.ok(markup.includes("&lt;script&gt;reply"));
  assert.ok(!markup.includes("<script>"));
});

test("World view shows enabled empty operator noticeboard and all physical inboxes exactly once", () => {
  const state = expanded();
  const world = state.world!;
  const communication = communicationSchema.parse({
    enabled: true, scope: "operator world view", policy: "Physical channels only.",
    notices: [], share_candidates: [],
    boxes: Object.entries(world.village!.roster).map(([id, resident]) => ({
      id: `workplace-inbox-${id}`, owner: world.entities[id]?.name ?? id[0].toUpperCase() + id.slice(1),
      place: world.entities[resident.work]?.name ?? resident.work,
      here: false, can_read: false, notes: 0
    }))
  });
  assert.equal(communication.boxes.length, 12);
  world.village!.dashboard = {
    communication, orders: [], products: [], accounts: [], activities: [], projects: [],
    quests: [], visitors: [], incidents: [], time_use: {}
  };
  const parsed = stateSchema.parse(state);
  for (const markup of [worldMonitorMarkup(parsed), villageDashboardMarkup(parsed, []),
    villageSystemsMarkup(parsed)]) {
    const heading = "Operator: noticeboard, commitments & appointments";
    assert.equal(markup.split(heading).length - 1, 1);
    for (const text of ["Privileged world view. This is not shared NPC knowledge.",
      "No notices posted yet.", "Physical workplace inboxes", "No remote delivery.",
      "Torvin", "Finn"]) {
      assert.ok(markup.includes(text), text);
    }
    assert.equal(markup.match(/0 physical notes/g)?.length, 12);
    assert.ok(!markup.includes("workplace-inbox-"));
    assert.ok(!markup.includes("Your learned notices"));
  }
  for (const disabled of [undefined, null, { ...communication, enabled: false }]) {
    parsed.world!.village!.dashboard!.communication = disabled;
    assert.ok(!worldMonitorMarkup(parsed).includes("Operator: noticeboard"));
  }
});

test("shared World systems use operator communication only, never selected NPC receipts", () => {
  const state = expanded();
  const personal = communicationSchema.parse({
    enabled: true, scope: "personal received snapshots", policy: "Only actual receipts",
    notices: [], boxes: [], share_candidates: []
  });
  state.observation = observationSchema.parse({
    contract_version: "1.0", world_id: "world", session_id: "session", observation_revision: "r1",
    self: { actor_id: "torvin", location_id: "forge", inventory: [], needs: [],
      body: { id: "human", description: "Human", capabilities: [],
        carrying: { rule: "slots", limit: 2, used: 0 } } },
    location: { id: "forge", description: "Forge" }, narrative: "", visible_entities: [], actions: [],
    village: { ...view(), communication: personal }
  });
  state.world!.village!.dashboard = {
    communication: { ...personal, scope: "operator world view", notices: [{
      id: "notice-operator-only", version: 1, author: "Finn", recipients: [],
      subject: "Unreceived operator-only warning", description: "Not personally learned",
      type: "warning", status: "posted", quantity: null, unit_price: null, delivery_fee: null,
      destination: null, due_at: null, updated_at: 480, private: false, response: null,
      source: "authoritative world", learned_at: null, last_verified_at: null,
      confidence: null, hops: 0, source_chain: [], freshness: "World state",
      appointment: null, commitment: null
    }] },
    orders: [], products: [], accounts: [], activities: [], projects: [],
    quests: [], visitors: [], incidents: [], time_use: {}
  };
  assert.ok(worldMonitorMarkup(state).includes("Unreceived operator-only warning"));
  const learned = villageMarkup(state.observation.village!);
  assert.ok(learned.includes("Your learned notices, commitments & appointments"));
  assert.ok(learned.includes("No notices received yet."));
  assert.ok(!learned.includes("Unreceived operator-only warning"));
  assert.ok(!learned.includes("Privileged world view"));
  delete state.world!.village!.dashboard!.communication;
  assert.ok(!worldMonitorMarkup(state).includes("Operator: noticeboard"));
  assert.ok(!worldMonitorMarkup(state).includes("Your learned notices"));
});

test("visitor rows retain history but departed visitors leave current location cards", () => {
  const state = expanded();
  state.world!.village!.minute = 700;
  state.world!.village!.dashboard = {
    orders: [], products: [], accounts: [], activities: [], projects: [], quests: [], incidents: [], time_use: {},
    visitors: [{ id: "merchant-1", name: "Cora", location: "tavern", arrival: 500, departure: 650,
      status: "departed", activities: [{ actor: "merchant-1", label: "Trading", location: "tavern",
        start: 510, end: 640, status: "completed" }] }]
  };
  const agents = monitoredAgents(state);
  const visitor = agents.find(agent => agent.id === "merchant-1")!;
  assert.equal(visitor.name, "Cora");
  assert.equal(visitor.current, false);
  const timeline = buildWorldTimeline(state, [], agents)!;
  const row = timeline.rows.find(item => item.id === visitor.id)!;
  assert.equal(row.occupation, "Visitor");
  assert.ok(row.spans.some(span => span.label === "Trading" && span.end === 640));
  assert.ok(row.locations.every(location => location.end <= 650));
  const markup = worldMonitorMarkup(state);
  assert.ok(!markup.includes('data-monitor-agent="merchant-1"'));
  assert.ok(markup.includes("Cora"));
});

test("personal activities clip at now and economy reporting escapes generated labels", () => {
  const state = expanded();
  state.world!.village!.minute = 600;
  state.world!.village!.dashboard = {
    orders: [{ id: "order", buyer: "elsie", seller: "jory", product: "bread", quantity: 4,
      delivered: 2, status: "accepted", deadline: 1100 }],
    products: [{ location: "forge", product: "iron", quantity: 0, usable: 0, expiring: 0, reserve: 2 }],
    accounts: [{ owner: "elsie", sales: 12, purchases: 8, fees: -1, subsidies: 0,
      net_cash: 3, inventory_value: 20 }],
    activities: [{ actor: "iona", label: "Writing <book>", location: "cottages",
      start: 580, end: 640, status: "active" }],
    projects: [{ id: "book", owner: "iona", title: "<script>book</script>", progress: 1,
      required: 5, status: "active" }], quests: [], visitors: [], incidents: [], time_use: {}
  };
  const timeline = buildWorldTimeline(state, [], monitoredAgents(state))!;
  assert.equal(timeline.rows.find(row => row.id === "iona")!.spans.at(-1)!.end, 600);
  const markup = villageDashboardMarkup(state, []);
  for (const text of ["2 delivered", "SHORTAGE", "not net profit", "&lt;script&gt;book"]) {
    assert.ok(markup.includes(text), text);
  }
  assert.ok(!markup.includes("<script>"));
});

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

test("livestock contracts show today's real care, retained work and escaped policy", () => {
  const value = view();
  const care = { day: 0, fed: true, watered: true, cleaned: false, progress: 5, eggs: 0, milk_sessions: [] };
  value.livestock = { enabled: true, keeper: "ada", hen_count: 12, bucket_water: 1,
    cow: care, hens: { ...care, fed: false }, policy: "<script>real care</script>" };
  const markup = villageMarkup(villageObservationSchema.parse(value));
  for (const text of ["1 cow + 12 hens", "fed yes", "fed due", "5 carried work min", "Bucket water: 1", "&lt;script&gt;"]) {
    assert.ok(markup.includes(text), text);
  }
  assert.ok(!markup.includes("<script>"));
  value.minute = 1440;
  const tomorrow = villageMarkup(value);
  assert.ok(!tomorrow.includes("fed yes"));
  assert.throws(() => villageObservationSchema.parse({ ...value,
    livestock: { ...value.livestock, bucket_water: -1 } }));
});

test("recycling stocks and provenance survive safe UI contracts without becoming manure", () => {
  const value = view();
  value.stocks = { "plant-scraps": 1, "compost-material": 2, compost: 1, manure: 0, milk: 0, eggs: 0 };
  const markup = villageMarkup(value);
  for (const text of ["plant-scraps: 1", "compost-material: 2", "compost: 1", "manure: 0"]) {
    assert.ok(markup.includes(text), text);
  }
  const state = expanded();
  state.world!.village!.stock_lots = { farm: { "plant-scraps": [
    { quantity: 1, produced_at: 600, expires_at: 1200, ingredients: ["flour"], source_resource: "bread" }
  ] } };
  assert.deepEqual(stateSchema.parse(state).world!.village!.stock_lots!.farm["plant-scraps"][0].ingredients, ["flour"]);
});

test("World view shows enabled unworked livestock from live run observation without dashboard livestock", () => {
  const state = expanded();
  state.world!.village!.minute = 2880;
  state.world!.village!.dashboard = {
    research: [], orders: [], products: [], accounts: [], activities: [], projects: [], quests: [],
    visitors: [], incidents: [], time_use: {}
  };
  state.world!.village!.stocks.farm = {
    grain: 4, manure: 0, milk: 0, eggs: 0, "plant-scraps": 0, "compost-material": 0, compost: 0
  };
  const care = { day: -1, fed: false, watered: false, cleaned: false, progress: 0, eggs: 0, milk_sessions: [] };
  state.run!.observation = {
    contract_version: "1.0", world_id: "world", session_id: "session", observation_revision: "r1",
    self: { actor_id: "torvin", location_id: "forge", inventory: [], needs: [],
      body: { id: "human", description: "Human", capabilities: [],
        carrying: { rule: "slots", limit: 2, used: 0 } } },
    location: { id: "forge", description: "Forge" }, narrative: "", visible_entities: [], actions: [],
    village: { ...view(), minute: 2880, day: 3, time: "00:00",
      livestock: { enabled: true, keeper: "ada", hen_count: 12, cow: care, hens: care, bucket_water: 0,
        policy: "<script>Real daily care and safe recycling</script>" } }
  };
  const parsed = stateSchema.parse(state);
  const markup = worldMonitorMarkup(parsed);
  for (const text of ["Farm livestock", "1 cow + 12 hens", "Cow: fed due", "Hens: fed due",
    "Bucket water: 0", "Food recycling", "plant-scraps: 0", "compost-material: 0", "compost: 0",
    "&lt;script&gt;Real daily care"]) {
    assert.ok(markup.includes(text), text);
  }
  assert.ok(!markup.includes("<script>"));
  assert.ok(!markup.includes("fed yes"));
  parsed.observation = observationSchema.parse(parsed.run!.observation);
  assert.ok(worldMonitorMarkup(parsed).includes("Farm livestock"));
  delete parsed.world!.village!.dashboard;
  assert.ok(worldMonitorMarkup(parsed).includes("Food recycling"));
  parsed.observation!.village!.livestock!.enabled = false;
  assert.ok(!worldMonitorMarkup(parsed).includes("Farm livestock"));
});

const researchProject = () => villageResearchSchema.parse({
  id: "research:torvin", title: "Hardy shovel", description: "Trial a stronger shovel for town repairs.",
  owner: "torvin", location: "forge", product: "hardy_shovel", kind: "tool",
  inputs: { iron: 2, timber: 1 }, equipment: ["anvil"], duration: 35,
  worked_minutes: 90, required_minutes: 240, attempts: 4, successes: 2,
  required_successes: 3, discovered_at: null, status: "blocked", blocker: "Missing iron"
});

test("town research shows named researchers, sites, real recipe and honest trial progress", () => {
  const state = expanded();
  state.run!.agent_profiles = {
    torvin: agentProfileSchema.parse({
      schema_version: "1.0", id: "torvin", name: "Torvin Smith",
      identity: "Smith", personality: "Practical", operating_rules: ["Be honest"],
      constraints: ["Use own knowledge"], body: state.world!.actors.torvin.body, scenario_bodies: {}
    })
  };
  state.world!.entities.forge.name = "Village Forge";
  state.world!.entities.anvil.name = "Smith's Anvil";
  state.world!.village!.dashboard = { research: [researchProject()],
    orders: [], products: [], accounts: [], activities: [], projects: [], quests: [],
    visitors: [], incidents: [], time_use: {} };
  const markup = villageSystemsMarkup(state);
  for (const text of ["Town research & discoveries", "Torvin Smith", "Village Forge", "Hardy shovel",
    "90/240 min", "2/4 successful/total trials", "3 successes required", "blocked", "Missing iron",
    "Proposed recipe (not unlocked)", "2 iron, 1 timber", "→ 1 hardy shovel / 35 min",
    "Equipment: Smith&#39;s Anvil", "repairs actual damaged objects"]) assert.ok(markup.includes(text), text);
  assert.ok(!markup.includes("Unlocked recipe"));
  assert.ok(villageDashboardMarkup(state, []).includes("Town research & discoveries"));
});

test("discoveries display unlocked recipes, discovery clocks and product-specific uses", () => {
  const state = expanded();
  const projects = (["food", "crop", "material", "tool"] as const).map(kind => ({
    ...researchProject(), id: `research:${kind}`, kind, product: `${kind}_discovery`,
    status: "discovered" as const, blocker: null, discovered_at: 1505,
    worked_minutes: 240, successes: 3, attempts: 5
  }));
  state.world!.village!.dashboard = {
    research: projects, orders: [], products: [], accounts: [], activities: [], projects: [],
    quests: [], visitors: [], incidents: [], time_use: {}
  };
  const markup = villageSystemsMarkup(state);
  assert.equal(markup.match(/Unlocked recipe/g)?.length, 4);
  assert.equal(markup.match(/Use: 2 meal portions/g)?.length, 2);
  for (const text of ["240/240 min", "3/5 successful/total trials", "Discovered day 2", "01:05",
    "ingredient for further recipes", "repairs actual damaged objects", "→ 1 material discovery / 35 min"]) {
    assert.ok(markup.includes(text), text);
  }
  assert.ok(!markup.includes("not unlocked"));
});

test("NPC known research escapes every proposal string and policy in both views", () => {
  const payload = '<img src=x onerror="bad()">&\'';
  const project = { ...researchProject(), title: payload, description: payload, owner: payload,
    location: payload, product: payload, inputs: { [payload]: 2 }, equipment: [payload], blocker: payload };
  const village = villageObservationSchema.parse({
    ...view(), research: { enabled: true, projects: [project], policy: payload }
  });
  const state = expanded();
  state.world!.village!.dashboard = {
    research: [project], orders: [], products: [], accounts: [], activities: [], projects: [],
    quests: [], visitors: [], incidents: [], time_use: {}
  };
  for (const markup of [villageMarkup(village), villageSystemsMarkup(state)]) {
    assert.ok(!markup.includes("<img"));
    assert.ok(markup.includes("&lt;img src=x onerror=&quot;bad()&quot;&gt;&amp;&#39;"));
    assert.equal(markup.match(/&lt;img/g)?.length, markup.includes("Your known research") ? 9 : 8);
  }
  assert.ok(villageMarkup(village).includes("Your known research projects & discoveries"));
  village.research!.projects = [researchProject()];
  assert.ok(villageMarkup(village).includes("Torvin"));
});

test("research contracts retain both optional views and reject invented statuses", () => {
  const project = researchProject();
  for (const status of ["researching", "in_progress", "blocked", "discovered"]) {
    assert.equal(villageResearchSchema.safeParse({ ...project, status }).success, true);
  }
  const invalid = { ...project, status: "claimed_complete" };
  assert.equal(villageResearchSchema.safeParse(invalid).success, false);
  assert.equal(villageObservationSchema.safeParse({
    ...view(), research: { enabled: true, projects: [invalid], policy: "" }
  }).success, false);
  const state = expanded();
  state.world!.village!.dashboard = {
    research: [project], orders: [], products: [], accounts: [], activities: [], projects: [],
    quests: [], visitors: [], incidents: [], time_use: {}
  };
  assert.deepEqual(stateSchema.parse(state).world!.village!.dashboard!.research, [project]);
  const badState = structuredClone(state);
  Object.assign(badState.world!.village!.dashboard!.research![0], { status: invalid.status });
  assert.equal(stateSchema.safeParse(badState).success, false);
  const observation = villageObservationSchema.parse({
    ...view(), research: { enabled: true, projects: [project], policy: "Research after funded work." }
  });
  assert.deepEqual(observation.research!.projects, [project]);
});

test("absent, empty and disabled research preserve legacy views", () => {
  const village = view();
  const legacy = villageMarkup(village);
  assert.equal(village.research, undefined);
  assert.equal(villageMarkup({ ...village, research: undefined }), legacy);
  assert.equal(villageMarkup({ ...village, research: { enabled: false, projects: [researchProject()], policy: "" } }), legacy);
  assert.ok(villageMarkup({ ...village, research: { enabled: true, projects: [], policy: "" } })
    .includes("No research projects assigned or observed here."));
  const state = expanded();
  assert.equal(villageSystemsMarkup(state), "");
  state.world!.village!.dashboard = {
    orders: [], products: [], accounts: [], activities: [], projects: [], quests: [],
    visitors: [], incidents: [], time_use: {}
  };
  const dashboardLegacy = villageSystemsMarkup(state);
  assert.equal(stateSchema.parse(state).world!.village!.dashboard!.research, undefined);
  state.world!.village!.dashboard.research = [];
  assert.equal(villageSystemsMarkup(state), dashboardLegacy);
  assert.ok(!legacy.includes("research"));
  assert.ok(!dashboardLegacy.includes("research"));
});

test("enabled town research is visible before proposals while legacy dashboards stay unchanged", () => {
  const state = expanded();
  state.world!.village!.dashboard = {
    research: [], orders: [], products: [], accounts: [], activities: [], projects: [], quests: [],
    visitors: [], incidents: [], time_use: {}
  };
  const legacy = villageSystemsMarkup(state);
  const legacyDashboard = villageDashboardMarkup(state, []);
  assert.equal(villageSystemsMarkup(state, [], false), legacy);
  state.observation = observationSchema.parse({
    contract_version: "1.0", world_id: "world", session_id: "session", observation_revision: "r1",
    self: { actor_id: "torvin", location_id: "forge", inventory: [], needs: [],
      body: { id: "human", description: "Human", capabilities: [],
        carrying: { rule: "slots", limit: 2, used: 0 } } },
    location: { id: "forge", description: "Forge" }, narrative: "",
    visible_entities: [], actions: [],
    village: { ...view(), research: { enabled: true, projects: [], policy: "" } }
  });
  state.observation!.village!.research!.enabled = false;
  const legacyMonitor = worldMonitorMarkup(state);
  assert.ok(!legacyMonitor.includes("Town research & discoveries"));
  state.observation!.village!.research!.enabled = true;
  const parsed = stateSchema.parse(state);
  const markup = villageSystemsMarkup(parsed, [], parsed.observation?.village?.research?.enabled);
  assert.ok(markup.includes("Town research & discoveries"));
  assert.ok(markup.includes("Occupational research is enabled. No research projects proposed or discoveries yet."));
  assert.ok(!markup.includes('class="maintenance-list research-list"'));
  assert.ok(villageDashboardMarkup(parsed, []).includes("Town research & discoveries"));
  assert.ok(worldMonitorMarkup(parsed).includes("Town research & discoveries"));
  delete parsed.world!.village!.dashboard!.research;
  assert.equal(villageSystemsMarkup(parsed, [], true), markup);
  parsed.observation!.village!.research!.enabled = false;
  assert.equal(villageDashboardMarkup(parsed, []), legacyDashboard);
  assert.equal(worldMonitorMarkup(parsed), legacyMonitor);
  delete parsed.observation!.village!.research;
  assert.equal(villageDashboardMarkup(parsed, []), legacyDashboard);
  assert.equal(worldMonitorMarkup(parsed), legacyMonitor);
});

test("town maintenance checklist shows assignments, carryover and escaped blockers", () => {
  const state = expanded();
  state.world!.village!.minute = 1440;
  state.world!.village!.dashboard = {
    orders: [], products: [], accounts: [], activities: [], projects: [], quests: [],
    visitors: [], incidents: [], time_use: {},
    maintenance: [{
      id: "maintenance:0:forge", title: "Forge <inspection>", assignee: "torvin", location: "forge",
      day: 1, release_at: 480, due_at: 1020, worked_minutes: 15, required_minutes: 30,
      supplies: { iron: 1 }, status: "blocked", blocker: "Missing <iron>", completed_at: null
    }]
  };
  const markup = villageDashboardMarkup(state, []);
  for (const text of ["Town maintenance checklist", "carried from day 1", "15/30 min",
    "Missing &lt;iron&gt;", "Forge &lt;inspection&gt;", "1 iron"]) assert.ok(markup.includes(text), text);
  assert.ok(!markup.includes("<inspection>"));
  assert.equal(stateSchema.safeParse({
    ...state, world: { ...state.world, village: { ...state.world!.village,
      dashboard: { ...state.world!.village!.dashboard, maintenance: [{
        ...state.world!.village!.dashboard!.maintenance![0], status: "claimed_complete"
      }] } } }
  }).success, false);
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

test("run until complete shows usage without enforced limits and the village cycle boundary", () => {
  const state = expanded();
  state.run!.run_until_complete = true;
  state.run!.actions = 1500;
  state.run!.model_calls = 1600;
  state.run!.elapsed_seconds = 3600;
  assert.equal(budgetSummary(state),
    "1500 actions · 1600 calls · 60.0 active minutes · No budget limits · Cycle ends at midnight");
  state.world!.village = null;
  assert.ok(budgetSummary(state).endsWith("Until verified completion"));
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
  assert.equal(buildWorldTimeline(state, events, agents)!.turns, true);
  assert.ok(worldGanttMarkup(state, events, agents).includes("Turn 0"));
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

function untimedRoom() {
  const state = expanded();
  const scenario = JSON.parse(readFileSync(resolve("..", "data", "scenarios", "two-locked-rooms.json"), "utf8"));
  const actor = state.world!.actors.torvin;
  state.world!.village = null;
  state.world!.entities = scenario.entities;
  state.world!.actors = {
    finch: { ...actor, actor_id: "finch", location_id: "room-one", start_location_id: "room-one" }
  };
  state.run!.actor_id = "finch";
  state.run!.agents = { finch: { role: "Explorer", memory: {}, observation: null } };
  state.run!.title = scenario.title;
  state.run!.scenario_id = scenario.id;
  return state;
}

function roomObservation(location = "room-one", actor = "finch") {
  return { self: { actor_id: actor, location_id: location } };
}

function roomEvent(sequence: number, kind: string, payload: unknown, actor = "finch", runId = "run") {
  const event = timelineEvent(sequence, kind, 9999, payload, actor, runId);
  delete event.village_minute;
  return event;
}

test("untimed Gantt shows Unknown Room actions and backtracking on integer turns, never wall time", () => {
  const state = untimedRoom();
  const events = [roomEvent(1, "observation", roomObservation())];
  for (const [index, action, target, location, outcome] of [
    [2, "look", "door-one", "room-one", "OBSERVED"],
    [4, "take", "silver-key", "room-one", "APPLIED"],
    [6, "unlock", "door-one", "room-one", "APPLIED"],
    [8, "go", "door-one", "room-two", "APPLIED"],
    [10, "go", "door-one", "room-one", "APPLIED"],
    [12, "drop", "silver-key", "room-one", "APPLIED"],
    [14, "take", "brass-key", "room-one", "APPLIED"],
    [16, "go", "door-one", "room-two", "APPLIED"]
  ] as const) {
    events.push(roomEvent(index, "action_request", {
      request_id: String(index), action_id: action, arguments: { target }
    }), { ...roomEvent(index + 1, "action_result", {
      request_id: String(index), outcome, explanation: `Recorded ${action}. <script>`,
      observation: roomObservation(location)
    }), free_action: action === "look" });
  }
  state.world!.actors.finch.location_id = "room-two";
  state.run!.elapsed_seconds = 5000;
  const before = JSON.stringify({ state, events });
  const projected = events.map(timelineEventProjection).filter(event => event !== null);
  const agents = monitoredAgents(state);
  const model = buildWorldTimeline(state, projected, agents)!;
  assert.equal(model.turns, true);
  assert.equal(model.now, 8);
  assert.equal(model.start, 0);
  assert.equal(model.rows.length, 1);
  assert.deepEqual(model.rows[0].spans.map(span => [span.start, span.end]), [
    [0, 1], [1, 2], [2, 3], [3, 4], [4, 5], [5, 6], [6, 7], [7, 8]
  ]);
  assert.deepEqual(model.rows[0].locations, [
    { start: 0, end: 4, location: "room-one" },
    { start: 4, end: 5, location: "room-two" },
    { start: 5, end: 8, location: "room-one" }
  ]);
  assert.equal(model.rows[0].spans.filter(span => span.kind === "travel").length, 3);
  const markup = worldMonitorMarkup(state, projected);
  assert.ok(markup.includes("World timeline"));
  assert.ok(markup.includes("Turn 8"));
  assert.ok(markup.includes("1 action attempt"));
  assert.ok(markup.includes("not elapsed time"));
  assert.ok(markup.includes("&lt;script&gt;"));
  assert.ok(!markup.includes("<script>"));
  assert.ok(!markup.includes(" sec"));
  assert.ok(!markup.includes(" min"));
  assert.ok(!markup.includes("Wagon route"));
  assert.equal(JSON.stringify({ state, events }), before);
  assert.deepEqual(buildWorldTimeline(state, [...projected].reverse(), agents), model);
  const zoomed = worldGanttMarkup(state, projected, agents, { zoom: 8, viewportWidth: 188 });
  assert.ok(zoomed.includes('width="1504"'));
  assert.ok(!/Turn \d+\.\d/.test(zoomed));
});

test("untimed Gantt counts rejected attempts once, ignores stale runs and unconfirmed requests", () => {
  const state = untimedRoom();
  const rejected = roomEvent(3, "action_result", {
    request_id: "locked", outcome: "REJECTED", explanation: "The door is locked.",
    observation: roomObservation()
  });
  const events = [
    roomEvent(1, "observation", roomObservation()),
    roomEvent(2, "action_request", { request_id: "locked", action_id: "open", arguments: { target: "door-one" } }),
    rejected, { ...rejected, id: "retry", sequence: 4 },
    roomEvent(5, "action_request", { request_id: "pending", action_id: "look", arguments: {} }),
    roomEvent(6, "action_result", { request_id: "missing", outcome: "APPLIED", explanation: "No request.",
      observation: roomObservation("outside") }),
    roomEvent(7, "observation", roomObservation("outside"), "finch", "old-run")
  ];
  const model = buildWorldTimeline(state, events, monitoredAgents(state))!;
  assert.equal(model.now, 1);
  assert.equal(model.rows[0].spans.length, 1);
  assert.equal(model.rows[0].spans[0].kind, "rejected");
  assert.equal(model.rows[0].spans[0].end, 0);
  assert.ok(model.rows[0].locations.every(band => band.location === "room-one"));
  const markup = worldGanttMarkup(state, events, monitoredAgents(state));
  assert.ok(markup.includes('class="gantt-marker rejected"'));
  assert.ok(markup.includes("Turn 1 / open: The door is locked."));
  assert.ok(!markup.includes("Outside"));
});

test("untimed Gantt leaves unavailable history blank and handles idle and stopped adventures", () => {
  const state = untimedRoom();
  state.run!.status = "completed";
  const agents = monitoredAgents(state);
  const model = buildWorldTimeline(state, [], agents)!;
  assert.equal(model.now, 0);
  assert.deepEqual(model.rows[0].locations, []);
  assert.deepEqual(model.rows[0].spans, []);
  const markup = worldGanttMarkup(state, [], agents, { viewportWidth: 188 });
  assert.ok(markup.includes("Waiting for recorded history"));
  assert.ok(markup.includes("Stopped Turn 0"));
  assert.ok(markup.includes('width="188"'));
  state.world = null;
  assert.equal(buildWorldTimeline(state, [], agents)!.now, 0);
  state.run = null;
  assert.equal(worldGanttMarkup(state, [], []), "");
});

test("untimed Gantt uses one shared action sequence across agents and keeps waits distinct", () => {
  const state = untimedRoom();
  const second = { ...state.world!.actors.finch, actor_id: "guest" };
  state.world!.actors.guest = second;
  const events = [
    roomEvent(1, "observation", roomObservation()),
    roomEvent(2, "observation", roomObservation("room-one", "guest"), "guest")
  ];
  for (const [sequence, actor] of [[3, "finch"], [5, "guest"], [7, "finch"], [9, "finch"]] as const) {
    events.push(roomEvent(sequence, "action_request", {
      request_id: String(sequence), action_id: "wait", arguments: { seconds: 600 }
    }, actor), roomEvent(sequence + 1, "action_result", {
      request_id: String(sequence), outcome: "APPLIED", explanation: "Waited.",
      observation: roomObservation("room-one", actor)
    }, actor));
  }
  const model = buildWorldTimeline(state, events, monitoredAgents(state))!;
  assert.equal(model.now, 4);
  assert.deepEqual(model.rows.find(row => row.id === "finch")!.spans.map(span => [span.start, span.end]), [
    [0, 1], [2, 3], [3, 4]
  ]);
  assert.deepEqual(model.rows.find(row => row.id === "guest")!.spans.map(span => [span.start, span.end]), [[1, 2]]);
});
