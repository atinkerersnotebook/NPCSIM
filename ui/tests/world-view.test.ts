import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { test } from "node:test";
import { observationSchema, stateSchema, worldSchema, worldEditProposalSchema, npcChatSchema } from "../src/contracts.ts";
import { actorPerspective, agentPerspectives, discoveredEntities, goalPresentation, inventoryUnitsUsed, locationNetwork, nutritionLabel, teamLocationNetwork } from "../src/world-view.ts";

function fixture() {
  const scenario = JSON.parse(readFileSync(resolve("..", "data", "scenarios", "two-locked-rooms.json"), "utf8"));
  const world = worldSchema.parse({
    world_id: "world", revision: 1, entities: scenario.entities,
    actors: { npc: {
      actor_id: "npc", location_id: "room-one", start_location_id: "room-one",
      inventory: [], visited_locations: ["room-one"], body: scenario.body, needs: scenario.needs
    } }
  });
  const observation = observationSchema.parse({
    contract_version: "1.0", world_id: "world", session_id: "session", observation_revision: "obs-1",
    self: {
      actor_id: "npc", location_id: "room-one", inventory: [], needs: [],
      body: { id: "body", description: "A body.", capabilities: [], carrying: { rule: "item_count", limit: 1, used: 0 } }
    },
    location: { id: "room-one", description: "A stone room." }, narrative: "A stone room.",
    visible_entities: ["door-one", "box", "rock"].map(id => ({ id, description: world.entities[id].description })),
    actions: []
  });
  return { world, observation };
}

test("concealed additions remain off the NPC map until searched and opened", () => {
  const { world, observation } = fixture();
  world.entities.nook = { ...world.entities.box, name: "Secret nook", location: "room-one",
    hidden: true, discovery_target: "box" };
  world.entities["spare-key"] = { ...world.entities["brass-key"], name: "Secret spare key", location: "nook" };
  assert.ok(!discoveredEntities(world, "npc", observation, {}).nook);
  assert.ok(!discoveredEntities(world, "npc", observation, {})["spare-key"]);
  world.entities.nook.hidden = false;
  observation.visible_entities.push({ id: "nook", description: "A nook discovered by searching." });
  assert.ok(discoveredEntities(world, "npc", observation, {}).nook);
  assert.ok(!discoveredEntities(world, "npc", observation, {})["spare-key"]);
  observation.visible_entities.push({ id: "spare-key", description: "A key revealed by opening the nook." });
  assert.ok(discoveredEntities(world, "npc", observation, {})["spare-key"]);
});

test("interaction contracts preserve preview data and require explicitly private answers", () => {
  const proposal = worldEditProposalSchema.parse({
    id: "proposal", revision: "obs-1", instruction: "Add a nook.", status: "pending",
    plan: { summary: "A hidden nook.", additions: {
      nook: { name: "Nook", kind: "container", description: "Recess", location: "room-one",
        hidden: true, discovery_target: "wall", open: false }
    }, key_bindings: { door: "spare-key" } }
  });
  assert.equal(proposal.plan.additions.nook.hidden, true);
  assert.equal(proposal.plan.additions.nook.open, false);
  assert.deepEqual(proposal.plan.key_bindings, { door: "spare-key" });
  assert.throws(() => npcChatSchema.parse({ question: "Hello?", answer: "Hi", private: false }));
});

test("main scene hides undiscovered rooms, doors, locks, keys and destinations", () => {
  const { world, observation } = fixture();
  world.entities["door-one"].open = true;
  const entities = discoveredEntities(world, "npc", observation, {});
  assert.deepEqual(Object.keys(entities), ["room-one", "door-one", "box", "rock"]);
  assert.equal(entities["door-one"].destination, null);
  assert.equal(entities["door-one"].lock_id, null);
  assert.equal(world.entities["door-one"].destination, "room-two");
  assert.ok(!JSON.stringify(entities).includes("room-two"));
});

test("treasure goals never show victory merely for starting or returning home", () => {
  const { world } = fixture();
  world.exit_location = world.actors.npc.location_id;
  assert.deepEqual(goalPresentation(world, "npc", { type: "treasures_returned", status: "active" }),
    { escaped: false, completed: false });
  assert.deepEqual(goalPresentation(world, "npc", { type: "treasures_returned", status: "achieved" }),
    { escaped: false, completed: true });
});

test("observed voyage tides survive parsing while absent local tide stays absent", () => {
  const { observation } = fixture();
  assert.equal(observation.voyage, undefined);
  const voyage = {
    tide: "high", turns_until_change: 3, policy: "Physical actions advance turns.",
    seaworthy: true, crew_aboard: true
  };
  assert.deepEqual(observationSchema.parse({ ...observation, voyage }).voyage, voyage);
  assert.throws(() => observationSchema.parse({ ...observation, voyage: { ...voyage, tide: "rising" } }));
  assert.throws(() => observationSchema.parse({ ...observation, voyage: { ...voyage, turns_until_change: 0 } }));
});

test("second door appears only after arrival and its unseen destination stays hidden", () => {
  const { world, observation } = fixture();
  world.actors.npc.location_id = "room-two";
  world.actors.npc.visited_locations!.push("room-two");
  observation.self.location_id = "room-two";
  observation.visible_entities = ["door-one", "door-two"].map(id => ({ id, description: world.entities[id].description }));
  const entities = discoveredEntities(world, "npc", observation, {
    known_entities: { box: {}, rock: {}, "silver-key": {}, "brass-key": {} }
  });
  assert.ok(entities["room-two"]);
  assert.ok(entities["door-two"]);
  assert.equal(entities["door-one"].destination, "room-two");
  assert.equal(entities["door-two"].destination, null);
  assert.ok(!entities.outside);
  assert.ok(!entities["lock-two"]);
  assert.ok(entities["brass-key"]);
});

test("another actor cannot inherit the active NPC's discoveries", () => {
  const { world, observation } = fixture();
  world.actors.other = { ...world.actors.npc, actor_id: "other", inventory: [] };
  const entities = discoveredEntities(world, "other", observation, {
    known_entities: { "door-two": {} }, visited_locations: { "room-two": "Another room." }
  });
  assert.ok(!entities["door-two"]);
  assert.ok(!entities["room-two"]);
});

test("actor selection uses only that teammate's persisted observations and memory", () => {
  const { world, observation } = fixture();
  const state = stateSchema.parse({
    run: {
      schema_version: "1.0", id: "run", world_id: "world", session_id: "session",
      actor_id: "npc", title: "Team", scenario_id: "team", status: "paused",
      reason: "", created_at: "", actions: 1, model_calls: 1, cycles: 1, elapsed_seconds: 1, model: "test",
      goal: { id: "goal", objective: "Escape together", type: "all_actors_at_exit_location", status: "active" },
      agents: {
        npc: { role: "Scout", observation, memory: { known_entities: { box: {} } } },
        other: { role: "Partner", observation: { ...observation, self: { ...observation.self, actor_id: "other" } },
          memory: { known_entities: { rock: {} } } }
      }
    },
    observation, world, memory: { known_entities: { box: {} } }, in_flight: false, event_count: 1,
    config: {
      schema_version: "1.0",
      proxy: { base_url: "http://localhost", model: "test", token_environment: null, timeout_seconds: 15 },
      world: { transport: "stdio", scenario: "team", command: null, args: [], url: null },
      budgets: { max_actions: 80, max_model_calls: 100, max_seconds: 900, repetition_window: 4 }
    }
  });
  const perspective = actorPerspective(state, "other");
  assert.equal(perspective.observation?.self.actor_id, "other");
  assert.deepEqual(perspective.memory, { known_entities: { rock: {} } });
  assert.deepEqual(actorPerspective(state, "missing"), { observation: null, memory: {} });
  assert.deepEqual(goalPresentation(world, "npc", { type: "all_actors_at_exit_location", status: "active" }),
    { escaped: false, completed: false });
  world.exit_location = world.actors.npc.location_id;
  assert.equal(goalPresentation(world, "npc", { type: "all_actors_at_exit_location", status: "active" }).escaped, true);
});

test("inventory display sums unit costs and reads legacy items as one unit", () => {
  const { world } = fixture();
  world.actors.npc.inventory = ["rock", "box"];
  world.entities.rock.inventory_units = 2;
  assert.equal(inventoryUnitsUsed(world, "npc"), 3);
  delete world.entities.rock.inventory_units;
  assert.equal(inventoryUnitsUsed(world, "npc"), 2);
  world.actors.npc.inventory = ["missing"];
  assert.throws(() => inventoryUnitsUsed(world, "npc"), /missing entity/);
});

test("preparation goals do not show escape or victory just for being in the kitchen", () => {
  const { world } = fixture();
  world.exit_location = world.actors.npc.location_id;
  assert.deepEqual(goalPresentation(world, "npc", { type: "entity_prepared", status: "active" }),
    { escaped: false, completed: false });
  assert.deepEqual(goalPresentation(world, "npc", { type: "entity_prepared", status: "achieved" }),
    { escaped: false, completed: true });
  assert.deepEqual(goalPresentation(world, "npc", { type: "actor_at_exit_location", status: "achieved" }),
    { escaped: true, completed: true });
});

test("nutrition labels are per portion and unknown values do not turn into zeros", () => {
  assert.match(nutritionLabel({
    serving_grams: 120, calories_kcal: 378, protein_g: 12, carbohydrates_g: 57,
    fat_g: 12, fiber_g: 3.8, sugar_g: 16
  }), /120 g portion: 378 kcal; protein 12 g, carbs 57 g, fat 12 g, fiber 3.8 g, sugar 16 g/);
  assert.equal(nutritionLabel(undefined), "Nutrition unknown");
});

test("discovered readable objects do not disclose their text until this actor reads", () => {
  const { world, observation } = fixture();
  world.entities.box.readable_text = "A secret recipe.";
  assert.equal(discoveredEntities(world, "npc", observation, {}).box.readable_text, null);
  world.actors.npc.read_entities = ["box"];
  assert.equal(discoveredEntities(world, "npc", observation, {}).box.readable_text, "A secret recipe.");
});

test("numbered network shows only visited rooms and anonymous unexplored exit stubs", () => {
  const { world, observation } = fixture();
  const map = locationNetwork(world, "npc", observation, {});
  assert.deepEqual(map.nodes.map(node => [node.id, node.number, node.name]), [
    ["room-one", 1, "Stone room"], ["frontier:door-one", null, "Unexplored"]
  ]);
  assert.equal(map.edges.length, 1);
  assert.equal(map.edges[0].source, "room-one");
  assert.equal(map.edges[0].target, "frontier:door-one");
  assert.equal(map.edges[0].status, "closed");
  assert.ok(!JSON.stringify(map).includes("room-two"));
  assert.ok(!JSON.stringify(map).includes("silver-key"));
  assert.ok(!JSON.stringify(map).includes("outside"));
  assert.equal(map.nodes[0].current, true);
  assert.deepEqual(map.nodes[0].actors, ["npc"]);
});

test("map blocks an open small passage for a larger flyer and explains its size limit", () => {
  const { world, observation } = fixture();
  const route = world.entities["door-one"];
  route.open = true;
  route.max_body_size = "small";
  route.traversal_capabilities = ["fly"];
  world.actors.npc.body.capabilities.push("fly");
  const blocked = locationNetwork(world, "npc", observation, {}).edges[0];
  assert.equal(blocked.status, "blocked");
  assert.ok(blocked.requirements.includes("Maximum body size: small"));
  world.actors.npc.body.size = "small";
  assert.equal(locationNetwork(world, "npc", observation, {}).edges[0].status, "open");
});

test("team route eligibility accounts for actual body size as well as flight", () => {
  const { state, events } = stormTeam();
  const world = state.world!;
  const route = world.entities["rookery-path"];
  route.max_body_size = "small";
  route.traversal_capabilities = ["fly"];
  world.actors.badger.body.capabilities.push("fly");
  const edge = teamLocationNetwork(state, events).edges.find(edge => edge.id === "rookery-path")!;
  assert.ok(edge.requirements.includes("Requires fly; usable by raven"));
  assert.ok(edge.requirements.some(reason => reason.startsWith("Maximum body size: small;")));
  world.actors.raven.body.size = "large";
  const blocked = teamLocationNetwork(state, events).edges.find(edge => edge.id === "rookery-path")!;
  assert.ok(blocked.requirements.includes("Requires fly; usable by no teammate"));
});

test("network preserves discovery numbers and links real routes instead of a fabricated chain", () => {
  const { world, observation } = fixture();
  world.actors.npc.location_id = "outside";
  world.actors.npc.visited_locations = ["room-one", "room-two", "outside"];
  observation.self.location_id = "outside";
  observation.visible_entities = ["door-two"].map(id => ({
    id, description: world.entities[id].description, locked: true
  }));
  const memory = { known_entities: { "door-one": {}, "door-two": {} } };
  const map = locationNetwork(world, "npc", observation, memory);
  assert.deepEqual(map.nodes.map(node => [node.id, node.number]), [
    ["room-one", 1], ["room-two", 2], ["outside", 3]
  ]);
  assert.deepEqual(map.edges.map(edge => [edge.source, edge.target]), [
    ["room-one", "room-two"], ["room-two", "outside"]
  ]);
  assert.equal(map.edges[1].status, "locked");
  world.entities["door-two"].open = true;
  assert.equal(locationNetwork(world, "npc", observation, memory).edges[1].status, "open");
  world.entities["door-two"].health = 0;
  assert.equal(locationNetwork(world, "npc", observation, memory).edges[1].status, "broken");
  world.entities.shortcut = {
    name: "Shortcut", kind: "door", description: "A shortcut.", location: "room-one", destination: "outside",
    open: true
  };
  const branched = locationNetwork(world, "npc", observation, {
    known_entities: { ...memory.known_entities, shortcut: {} }
  });
  assert.equal(branched.edges.length, 3);
  assert.equal(branched.edges[2].source, "room-one");
  assert.equal(branched.edges[2].target, "outside");
  assert.deepEqual(branched.nodes.map(node => node.number), [1, 2, 3]);
});

test("network shows observed colocated teammates without leaking remote agents", () => {
  const { world, observation } = fixture();
  world.actors.other = { ...world.actors.npc, actor_id: "other", location_id: "room-two" };
  assert.deepEqual(locationNetwork(world, "npc", observation, {}).nodes[0].actors, ["npc"]);
  observation.visible_actors = [{ actor_id: "friend", inventory: [] }];
  assert.deepEqual(locationNetwork(world, "npc", observation, {}).nodes[0].actors, ["npc", "friend"]);
  const remote = locationNetwork(world, "other", observation, {
    known_entities: { "door-two": {} }, visited_locations: { outside: "Hidden." }
  });
  assert.ok(!remote.nodes.some(node => node.id === "outside"));
  assert.ok(!remote.edges.some(edge => edge.id === "door-two"));
});

test("network lays out large explored worlds with unique positions and stable numbers", () => {
  const { world, observation } = fixture();
  world.actors.npc.visited_locations = ["room-one"];
  for (let index = 0; index < 12; index++) {
    const id = `room-${index + 10}`;
    world.entities[id] = { name: id, kind: "location", description: `Visited ${id}.` };
    world.actors.npc.visited_locations.push(id);
  }
  const map = locationNetwork(world, "npc", observation, {});
  assert.equal(map.nodes.filter(node => node.number !== null).length, 13);
  assert.equal(new Set(map.nodes.map(node => `${node.x}:${node.y}`)).size, map.nodes.length);
  assert.ok(map.nodes.every(node => node.x > 0 && node.x < map.width && node.y < map.height));
  assert.ok(map.height > 440);
});

test("skill-gated network routes differ for a swimmer and a non-swimmer", () => {
  const { world, observation } = fixture();
  world.entities["door-one"].open = true;
  world.entities["door-one"].traversal_capabilities = ["swim"];
  const blocked = locationNetwork(world, "npc", observation, {});
  assert.equal(blocked.edges[0].status, "blocked");
  assert.deepEqual(blocked.edges[0].requirements, ["Ability: swim"]);
  world.actors.npc.body.capabilities.push("swim");
  assert.equal(locationNetwork(world, "npc", observation, {}).edges[0].status, "open");
  observation.visible_entities = observation.visible_entities.map(entity => entity.id === "door-one"
    ? { ...entity, required_work: ["repair_ferry"] } : entity);
  const pending = locationNetwork(world, "npc", observation, {});
  assert.equal(pending.edges[0].status, "blocked");
  assert.ok(pending.edges[0].requirements.includes("Work: repair_ferry"));
  assert.ok(!JSON.stringify(pending).includes("room-two"));
});

test("observed teammate skills and local work survive the dashboard contract", () => {
  const { observation } = fixture();
  const parsed = observationSchema.parse({
    ...observation,
    visible_actors: [{ actor_id: "fox", inventory: [], capabilities: ["repair", "rigging"] }],
    skill_challenges: [{
      id: "prepare_rope", description: "Prepare a safe line.", target: "bench", capability: "rigging",
      completed_by: null, reason: "Your body does not have rigging."
    }],
    actions: [{
      id: "go", description: "Cross a route.", arguments_schema: {}, availability: "blocked",
      target_requirements: { river: "Requires swim." }
    }]
  });
  assert.deepEqual(parsed.visible_actors?.[0].capabilities, ["repair", "rigging"]);
  assert.equal(parsed.skill_challenges?.[0].capability, "rigging");
  assert.equal(parsed.actions[0].target_requirements?.river, "Requires swim.");
});

function stormTeam() {
  const scenario = JSON.parse(readFileSync(resolve("..", "data", "scenarios", "storm-signal.json"), "utf8"));
  const ids = ["raven", "badger", "otter"];
  const world = worldSchema.parse({
    world_id: "storm", revision: 1, exit_location: scenario.exit_location,
    entities: scenario.entities, skill_uses: {},
    actors: Object.fromEntries(ids.map((id, index) => {
      const spec = scenario.agents[index];
      return [id, {
        actor_id: id, location_id: spec.start_location, start_location_id: spec.start_location,
        inventory: [], visited_locations: [spec.start_location], body: spec.body, needs: {}
      }];
    }))
  });
  const observations = Object.fromEntries(ids.map(id => {
    const actor = world.actors[id];
    return [id, observationSchema.parse({
      contract_version: "1.0", world_id: "storm", session_id: `${id}-session`, observation_revision: "obs-1",
      self: {
        actor_id: id, location_id: actor.location_id, inventory: [], needs: [],
        body: { id: `${id}-body`, description: actor.body.description, capabilities: actor.body.capabilities,
          carrying: { rule: "inventory_units", limit: actor.body.capacity, used: 0 } }
      },
      location: { id: actor.location_id, description: world.entities[actor.location_id].description },
      narrative: world.entities[actor.location_id].description, role: `${id}'s role`, messages: [],
      visible_entities: Object.entries(world.entities)
        .filter(([, entity]) => entity.location === actor.location_id)
        .map(([id, entity]) => ({ id, name: entity.name, description: entity.description })),
      actions: []
    })];
  }));
  const state = stateSchema.parse({
    run: {
      schema_version: "1.0", id: "storm-run", world_id: "storm", session_id: "raven-session",
      actor_id: "raven", title: scenario.title, scenario_id: scenario.id, status: "ready",
      reason: "", created_at: "", actions: 0, model_calls: 0, cycles: 0, elapsed_seconds: 0, model: "test",
      goal: { id: "goal", objective: scenario.objective, type: scenario.goal_type, status: "active" },
      agents: Object.fromEntries(ids.map(id => [id, {
        role: `${id}'s role`, observation: observations[id],
        memory: { subgoal: `${id}'s task`, known_entities: {} }
      }]))
    },
    observation: observations.raven, memory: { subgoal: "raven's task" },
    world, in_flight: false, event_count: 0,
    config: JSON.parse(readFileSync(resolve("..", "data", "config.json"), "utf8"))
  });
  const events = ids.map((actor, index) => ({
    id: `storm:${index}`, sequence: index, run_id: "storm-run", session_id: `${actor}-session`,
    actor_id: actor, kind: "observation", cycle: 0, timestamp: "", payload: observations[actor]
  }));
  return { state, events };
}

test("all three perspectives expose only their own observations, inventory and task focus", () => {
  const { state } = stormTeam();
  const original = JSON.stringify(state);
  const agents = agentPerspectives(state);
  assert.deepEqual(agents.map(agent => [agent.actorId, agent.observation?.self.location_id, agent.focus]), [
    ["raven", "rookery", "raven's task"], ["badger", "quarry", "badger's task"],
    ["otter", "river-landing", "otter's task"]
  ]);
  assert.ok(agents.every(agent => agent.status === "Ready"));
  assert.ok(!JSON.stringify(agents[0].observation).includes("mast-weight"));
  assert.ok(!JSON.stringify(agents[1].observation).includes("optical-cache"));
  assert.equal(JSON.stringify(state), original);
  state.run!.status = "running";
  state.run!.actor_id = "otter";
  state.in_flight = true;
  assert.deepEqual(agentPerspectives(state).map(agent => agent.status), ["Awaiting turn", "Awaiting turn", "Deciding"]);
  state.run!.agents!.badger.observation = null;
  assert.equal(agentPerspectives(state)[1].observation, null);
});

test("single-agent runs without a team roster expose their own live perspective", () => {
  const { state } = stormTeam();
  for (const roster of [undefined, {}]) {
    state.run!.agents = roster;
    const agents = agentPerspectives(state);
    assert.equal(agents.length, 1);
    assert.equal(agents[0].actorId, "raven");
    assert.equal(agents[0].role, "raven's role");
    assert.equal(agents[0].observation, state.observation);
    assert.equal(agents[0].focus, "raven's task");
  }
  state.run = null;
  assert.deepEqual(agentPerspectives(state), []);
});

test("the active perspective uses its freshest state without borrowing another actor's memory", () => {
  const { state } = stormTeam();
  const current = state.observation!;
  current.observation_revision = "obs-new";
  state.memory.subgoal = "A fresh task";
  const view = agentPerspectives(state);
  assert.equal(view[0].observation!.observation_revision, "obs-new");
  assert.equal(view[0].focus, "A fresh task");
  assert.equal(view[1].focus, "badger's task");
});

test("consolidated map shows separated agents and shared routes without hidden rooms", () => {
  const { state, events } = stormTeam();
  const original = JSON.stringify(state);
  const map = teamLocationNetwork(state, events);
  const rooms = map.nodes.filter(node => !node.unexplored);
  assert.deepEqual(rooms.map(node => [node.id, node.actors]), [
    ["rookery", ["raven"]], ["quarry", ["badger"]], ["river-landing", ["otter"]]
  ]);
  assert.equal(map.edges.length, 4);
  assert.equal(new Set(map.edges.map(edge => edge.id)).size, 4);
  assert.ok(!JSON.stringify(map).includes("signal-yard"));
  assert.ok(!JSON.stringify(map).includes("submerged-chamber"));
  assert.ok(!JSON.stringify(map).includes("refuge-gate"));
  assert.match(map.edges.find(edge => edge.id === "service-channel")!.requirements.join(" "), /usable by otter/);
  assert.equal(JSON.stringify(state), original);
});

test("team map combines discovered endpoints, follows every agent and preserves discovery numbers", () => {
  const { state, events } = stormTeam();
  const world = state.world!;
  world.actors.raven.location_id = "signal-yard";
  world.actors.raven.visited_locations!.push("signal-yard");
  const current = state.observation!;
  current.self.location_id = "signal-yard";
  current.location = { id: "signal-yard", description: world.entities["signal-yard"].description };
  current.visible_entities = ["rookery-path", "quarry-track", "landing-path", "refuge-gate"].map(id => ({
    id, name: world.entities[id].name, description: world.entities[id].description
  }));
  events.push({ ...events[0], id: "storm:3", sequence: 3, payload: current });
  const map = teamLocationNetwork(state, events);
  assert.deepEqual(map.nodes.filter(node => !node.unexplored).map(node => [node.id, node.number]), [
    ["rookery", 1], ["quarry", 2], ["river-landing", 3], ["signal-yard", 4]
  ]);
  assert.deepEqual(map.edges.find(edge => edge.id === "quarry-track")!.target, "signal-yard");
  assert.equal(map.nodes.filter(node => node.unexplored).length, 2);
  assert.deepEqual(map.nodes.find(node => node.id === "signal-yard")!.actors, ["raven"]);
  world.actors.badger.location_id = "signal-yard";
  world.actors.badger.visited_locations!.push("signal-yard");
  const together = teamLocationNetwork(state, events);
  assert.deepEqual(together.nodes.find(node => node.id === "signal-yard")!.actors, ["raven", "badger"]);
  assert.deepEqual(together.nodes.filter(node => !node.unexplored).map(node => node.number), [1, 2, 3, 4]);
  const own = actorPerspective(state, "raven");
  assert.ok(!locationNetwork(world, "raven", own.observation, own.memory).nodes.some(node => node.id === "quarry"));
  assert.equal(together.edges.find(edge => edge.id === "refuge-gate")!.status, "blocked");
  world.skill_uses = { focus_signal: "raven" };
  world.entities["refuge-gate"].open = true;
  assert.equal(teamLocationNetwork(state, events).edges.find(edge => edge.id === "refuge-gate")!.status, "open");
});

test("the team map does not reveal the condition of an undiscovered attached lock", () => {
  const { state, events } = stormTeam();
  const world = state.world!;
  world.entities["rookery-path"].open = false;
  world.entities["rookery-path"].lock_id = "hidden-lock";
  world.entities["hidden-lock"] = {
    name: "Hidden lock", kind: "lock", description: "An undiscovered lock.", health: 0
  };
  assert.equal(teamLocationNetwork(state, events).edges.find(edge => edge.id === "rookery-path")!.status, "closed");
  state.memory.known_entities = { "hidden-lock": {} };
  assert.equal(teamLocationNetwork(state, events).edges.find(edge => edge.id === "rookery-path")!.status, "broken");
});
