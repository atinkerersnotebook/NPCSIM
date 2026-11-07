import { z } from "zod";
import type { Nutrition, RunEvent, State, World } from "./contracts.ts";

export function nutritionLabel(nutrition: Nutrition | null | undefined): string {
  if (!nutrition) return "Nutrition unknown";
  const number = (value: number) => Number(value.toFixed(1));
  return `${number(nutrition.serving_grams)} g portion: ${number(nutrition.calories_kcal)} kcal; ` +
    `protein ${number(nutrition.protein_g)} g, carbs ${number(nutrition.carbohydrates_g)} g, ` +
    `fat ${number(nutrition.fat_g)} g, fiber ${number(nutrition.fiber_g)} g, sugar ${number(nutrition.sugar_g)} g`;
}

export function goalPresentation(world: World, actorId: string, goal: { type: string; status: string } | undefined) {
  const actor = world.actors[actorId];
  if (!actor) throw new Error(`Unknown actor ${actorId}`);
  const escapeGoal = goal?.type === "actor_at_exit_location" || goal?.type === "actor_outside_start_location" ||
    goal?.type === "all_actors_at_exit_location";
  const escaped = escapeGoal && (world.exit_location ? actor.location_id === world.exit_location : goal?.status === "achieved");
  return { escaped, completed: escaped || goal?.status === "achieved" };
}

export function actorPerspective(state: State, actorId: string): Pick<State, "observation" | "memory"> {
  if (state.observation?.self.actor_id === actorId) {
    return { observation: state.observation, memory: state.memory };
  }
  const agent = state.run?.agents?.[actorId];
  if (agent) return { observation: agent.observation, memory: agent.memory };
  return {
    observation: state.observation?.self.actor_id === actorId ? state.observation : null,
    memory: state.observation?.self.actor_id === actorId ? state.memory : {}
  };
}

export function agentPerspectives(state: State) {
  const run = state.run;
  const agents = run && !Object.keys(run.agents ?? {}).length ? {
    [run.actor_id]: {
      role: state.observation?.role ?? "Autonomous agent",
      observation: state.observation, memory: state.memory
    }
  } : run?.agents ?? {};
  return Object.entries(agents).map(([actorId, agent]) => {
    const perspective = actorPerspective(state, actorId);
    const observation = perspective.observation;
    const active = run?.actor_id === actorId && (run.status === "running" || state.in_flight);
    const atExit = observation && run?.goal.type === "all_actors_at_exit_location" &&
      observation.self.location_id === state.world?.exit_location;
    const ended = !["ready", "running", "paused"].includes(run?.status ?? "");
    return {
      actorId, role: agent.role, ...perspective, active,
      locationName: observation ?
        state.world?.entities[observation.self.location_id]?.name ?? observation.self.location_id : "Awaiting observation",
      focus: z.string().parse(perspective.memory.subgoal ?? ""),
      status: atExit ? "At exit" : ended ? "Attempt ended" :
        active ? (state.in_flight ? "Deciding" : "Acting") :
        run?.status === "running" ? "Awaiting turn" : run?.status === "paused" ? "Paused" : "Ready"
    };
  });
}

export function inventoryUnitsUsed(world: World, actorId: string): number {
  const actor = world.actors[actorId];
  if (!actor) throw new Error(`Unknown actor ${actorId}`);
  const carried = actor.inventory.reduce((total, id) => {
    const entity = world.entities[id];
    if (!entity) throw new Error(`Inventory references missing entity ${id}`);
    return total + (entity.inventory_units ?? 1);
  }, 0);
  const cargo = world.village?.roster[actorId] ? world.village.residents[actorId]?.cargo : undefined;
  return carried + Object.values(cargo ?? {}).reduce((total, amount) => total + amount, 0);
}

export function discoveredEntities(
  world: World, actorId: string, observation: State["observation"], memory: State["memory"]
): World["entities"] {
  const actor = world.actors[actorId];
  if (!actor) throw new Error(`Unknown actor ${actorId}`);
  const visited = new Set([actor.start_location_id, actor.location_id, ...(actor.visited_locations ?? [])]);
  const known = new Set([...actor.inventory, ...(actor.inspected ?? []), ...(actor.examined ?? [])]);
  if (observation?.self.actor_id === actorId) {
    observation.visible_entities.forEach(entity => known.add(entity.id));
    Object.keys(z.record(z.string(), z.unknown()).parse(memory.known_entities ?? {})).forEach(id => known.add(id));
    Object.keys(z.record(z.string(), z.string()).parse(memory.visited_locations ?? {})).forEach(id => visited.add(id));
  }
  return Object.fromEntries(Object.entries(world.entities)
    .filter(([id, entity]) => entity.kind === "location" ? visited.has(id) : known.has(id))
    .map(([id, entity]) => [id, entity.kind === "door" ? {
      ...entity,
      location: entity.location && visited.has(entity.location) ? entity.location : null,
      destination: entity.destination && visited.has(entity.destination) ? entity.destination : null,
      lock_id: entity.lock_id && known.has(entity.lock_id) ? entity.lock_id : null
    } : { ...entity, readable_text: actor.read_entities?.includes(id) ? entity.readable_text : null }]));
}

interface MapLocation {
  id: string;
  number: number | null;
  name: string;
  description: string;
  current: boolean;
  unexplored: boolean;
  actors: string[];
}

interface MapRoute {
  id: string;
  name: string;
  source: string;
  target: string;
  status: string;
  requirements: string[];
}

function bodyFitsPassage(body: World["actors"][string]["body"], passage: World["entities"][string]): boolean {
  const sizes = ["small", "medium", "large"];
  return passage.max_body_size == null || sizes.indexOf(body.size) <= sizes.indexOf(passage.max_body_size);
}

function layoutNetwork(nodes: MapLocation[], edges: MapRoute[]) {
  return {
    nodes: nodes.map((node, index) => {
      const row = Math.floor(index / 3);
      const column = row % 2 === 0 ? index % 3 : 2 - index % 3;
      return { ...node, x: 110 + column * 220, y: 58 + row * 180 };
    }),
    edges,
    width: 660,
    height: Math.max(175, Math.ceil(nodes.length / 3) * 180)
  };
}

export function locationNetwork(
  world: World, actorId: string, observation: State["observation"], memory: State["memory"]
) {
  if (observation?.self.actor_id !== actorId) {
    observation = null;
    memory = {};
  }
  const entities = discoveredEntities(world, actorId, observation, memory);
  const actor = world.actors[actorId];
  const locationIds = [...new Set([
    actor.start_location_id, ...(actor.visited_locations ?? []),
    ...Object.keys(z.record(z.string(), z.string()).parse(memory.visited_locations ?? {})),
    actor.location_id
  ])].filter(id => entities[id]?.kind === "location");
  const nodes = locationIds.map<MapLocation>((id, index) => ({
    id, number: index + 1, name: entities[id].name,
    description: entities[id].description, current: id === actor.location_id,
    unexplored: false,
    actors: id === actor.location_id ? [
      actorId, ...(observation?.self.actor_id === actorId ? observation.visible_actors?.map(peer => peer.actor_id) ?? [] : [])
    ] : []
  }));
  const known = z.record(z.string(), z.unknown()).parse(memory.known_entities ?? {});
  const edges = Object.entries(entities).flatMap(([id, door]) => {
    if (door.kind !== "door") return [];
    const source = door.location && locationIds.includes(door.location) ? door.location : null;
    const target = door.destination && locationIds.includes(door.destination) ? door.destination : null;
    const endpoint = source ?? target;
    if (!endpoint) return [];
    const facts = observation?.visible_entities.find(entity => entity.id === id) ??
      z.record(z.string(), z.unknown()).parse(known[id] ?? {});
    const broken = door.health === 0 || (door.lock_id && entities[door.lock_id]?.health === 0 && !door.open);
    const requiredAbilities = door.traversal_capabilities ?? [];
    const requiredWork = z.array(z.string()).parse(facts.required_work ?? []);
    const requirements = [...requiredAbilities.map(skill => `Ability: ${skill}`),
      ...(door.max_body_size ? [`Maximum body size: ${door.max_body_size}`] : []),
      ...requiredWork.map(work => `Work: ${work}`)];
    const blocked = !bodyFitsPassage(actor.body, door) ||
      requiredAbilities.some(skill => !actor.body.capabilities.includes(skill)) || requiredWork.length > 0;
    const status = broken ? "broken" : blocked ? "blocked" : door.open ? "open" : facts.locked === true ? "locked" : "closed";
    if (!source || !target) {
      const frontier = `frontier:${id}`;
      nodes.push({
        id: frontier, number: null, name: "Unexplored", description: `Beyond the ${door.name.toLowerCase()}.`,
        current: false, unexplored: true, actors: []
      });
      return [{ id, name: door.name, source: endpoint, target: frontier, status, requirements }];
    }
    return [{ id, name: door.name, source, target, status, requirements }];
  });
  return layoutNetwork(nodes, edges);
}

export function teamLocationNetwork(state: State, events: readonly RunEvent[] = []) {
  const world = state.world;
  if (!world) return layoutNetwork([], []);
  const perspectives = agentPerspectives(state);
  const networks = perspectives.map(agent =>
    locationNetwork(world, agent.actorId, agent.observation, agent.memory));
  const rooms = new Map(networks.flatMap(network => network.nodes.filter(node => !node.unexplored))
    .map(node => [node.id, node]));
  const order = new Set<string>();
  for (const event of [...events].sort((a, b) => a.sequence - b.sequence)) {
    if (event.kind !== "observation" && event.kind !== "action_result") continue;
    const payload = z.record(z.string(), z.unknown()).parse(event.payload);
    const observed = z.object({ self: z.object({ location_id: z.string() }) })
      .parse(event.kind === "observation" ? payload : payload.observation);
    if (rooms.has(observed.self.location_id)) order.add(observed.self.location_id);
  }
  for (const id of rooms.keys()) order.add(id);
  const nodes: MapLocation[] = [...order].map((id, index) => {
    const room = rooms.get(id);
    if (!room) throw new Error(`Missing discovered team room: ${id}`);
    const actors = perspectives.filter(agent => world.actors[agent.actorId]?.location_id === id)
      .map(agent => agent.actorId);
    return { ...room, number: index + 1, current: actors.length > 0, actors };
  });
  const completed = z.record(z.string(), z.string()).parse(world.skill_uses ?? {});
  const known = new Set(perspectives.flatMap(agent =>
    Object.keys(discoveredEntities(world, agent.actorId, agent.observation, agent.memory))));
  const routes = new Set(networks.flatMap(network => network.edges.map(edge => edge.id)));
  const edges: MapRoute[] = [...routes].map(id => {
    const door = world.entities[id];
    if (!door) throw new Error(`Missing discovered team route: ${id}`);
    let source = door.location && rooms.has(door.location) ? door.location : null;
    let target = door.destination && rooms.has(door.destination) ? door.destination : null;
    const endpoint = source ?? target;
    if (!endpoint) throw new Error(`Team route has no discovered endpoint: ${id}`);
    if (!source || !target) {
      const frontier = `frontier:${id}`;
      nodes.push({
        id: frontier, number: null, name: "Unexplored",
        description: `Beyond the ${door.name.toLowerCase()}.`,
        current: false, unexplored: true, actors: []
      });
      source = endpoint;
      target = frontier;
    }
    const abilities = door.traversal_capabilities ?? [];
    const capable = perspectives.filter(agent =>
      bodyFitsPassage(world.actors[agent.actorId].body, door) &&
      abilities.every(ability => world.actors[agent.actorId].body.capabilities.includes(ability)));
    const pending = (door.required_challenges ?? []).filter(work => !(work in completed));
    const lock = door.lock_id && known.has(door.lock_id) ? world.entities[door.lock_id] : undefined;
    const broken = door.health === 0 || (lock?.health === 0 && !door.open);
    const restricted = capable.length < perspectives.length;
    return {
      id, name: door.name, source, target,
      status: broken ? "broken" : pending.length || restricted ? "blocked" :
        door.open ? "open" : networks.some(network =>
          network.edges.some(edge => edge.id === id && edge.status === "locked")) ? "locked" : "closed",
      requirements: [
        ...(abilities.length ? [`Requires ${abilities.join(", ")}; usable by ${capable.map(agent => agent.actorId).join(", ") || "no teammate"}`] : []),
        ...(door.max_body_size ? [`Maximum body size: ${door.max_body_size}; fits ${perspectives.filter(agent =>
          bodyFitsPassage(world.actors[agent.actorId].body, door)).map(agent => agent.actorId).join(", ") || "no teammate"}`] : []),
        ...pending.map(work => `Work: ${work.replace(/_/g, " ")}`)
      ]
    };
  });
  return layoutNetwork(nodes, edges);
}
