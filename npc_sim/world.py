import copy
import threading
import time
from pathlib import Path
from typing import Any

from jsonschema import ValidationError, validate

from npc_sim.models import Actor, Body, ClockRequest, Entity, ERPState, RPSState, Scenario, SimulationClock, TicTacToeState, VoyageState, WorldState
from npc_sim import bakery, bbq, equipment, erp, handling, recipes, rps, skills, spells, tic_tac_toe, village, village_conversation, voyage
from npc_sim.storage import read_json
from npc_sim.world_storage import WorldStorage


class World:
    def __init__(self, scenario: Scenario, path: Path, world_id: str,
                 actor_id: str, session_id: str):
        self.scenario = scenario
        self.path = path
        self.actor_id = actor_id
        self.session_id = session_id
        self._lock = threading.RLock()
        self._owner = self
        self._storage = WorldStorage(path)
        self._defer_save = False
        self._last_tick = time.monotonic()
        if path.exists():
            self.state = self._storage.load()
            if self.state.world_id != world_id or self.state.scenario_id != scenario.id:
                raise ValueError("World identity or scenario does not match saved state")
        else:
            self.state = WorldState(
                world_id=world_id, scenario_id=scenario.id,
                exit_location=scenario.exit_location,
                entities=copy.deepcopy(scenario.entities), actors={},
                clock=SimulationClock(rate=scenario.bakery.rate, mode=scenario.bakery.mode) if scenario.bakery else None,
                ingredient_stock={eid: len(scenario.bakery.bowls) for eid in scenario.bakery.ingredients}
                if scenario.bakery else {},
                voyage=VoyageState() if scenario.voyage else None,
                rps=RPSState() if scenario.rps else None,
                tic_tac_toe=TicTacToeState() if scenario.goal_type == "tic_tac_toe_completed" else None,
                erp=ERPState() if scenario.erp else None,
                bbq=bbq.initial_state(scenario.bbq) if scenario.bbq else None,
                village=village.initial_state(scenario) if scenario.village else None,
            )
        if scenario.voyage is not None and self.state.voyage is None:
            raise ValueError("Saved voyage world is missing its persistent voyage state")
        if scenario.rps is not None and self.state.rps is None:
            raise ValueError("Saved match world is missing its persistent rock-paper-scissors state")
        if scenario.goal_type == "tic_tac_toe_completed" and self.state.tic_tac_toe is None:
            raise ValueError("Saved match world is missing its persistent tic-tac-toe state")
        if scenario.erp is not None and self.state.erp is None:
            raise ValueError("Saved ERP world is missing its persistent ERP state")
        if scenario.bbq is not None and self.state.bbq is None:
            raise ValueError("Saved BBQ world is missing its persistent BBQ state")
        if scenario.village is not None and self.state.village is None:
            raise ValueError("Saved village world is missing its persistent village state")
        if self.state.exit_location is None:
            self.state.exit_location = scenario.exit_location
        if actor_id not in self.state.actors:
            location = self.start_location(actor_id)
            self.state.actors[actor_id] = Actor(
                actor_id=actor_id, session_id=session_id,
                location_id=location, start_location_id=location,
                visited_locations=[location],
                body=copy.deepcopy(self.body_for(actor_id)), needs=copy.deepcopy(scenario.needs),
            )
            self.state.revision += 1
            self.save()
        elif self.state.actors[actor_id].session_id != session_id:
            raise ValueError("Actor session does not match the saved binding")
        if len(self.state.requests) > 1:
            self.save()

    @property
    def state(self) -> WorldState:
        return self._owner._state

    @state.setter
    def state(self, value: WorldState) -> None:
        self._owner._state = value

    def bind(self, actor_id: str, session_id: str) -> "World":
        with self._lock:
            if actor_id not in self.state.actors:
                location = self.start_location(actor_id)
                self.state.actors[actor_id] = Actor(
                    actor_id=actor_id, session_id=session_id,
                    location_id=location, start_location_id=location,
                    visited_locations=[location],
                    body=copy.deepcopy(self.body_for(actor_id)), needs=copy.deepcopy(self.scenario.needs))
                self.state.revision += 1
                self.save()
            elif self.state.actors[actor_id].session_id != session_id:
                raise ValueError("Actor session does not match its binding")
            bound = object.__new__(World)
            bound.scenario, bound.path = self.scenario, self.path
            bound.actor_id, bound.session_id = actor_id, session_id
            bound._lock, bound._owner = self._lock, self._owner
            return bound

    def start_location(self, actor_id: str) -> str:
        agent = next((agent for agent in self.scenario.agents if agent.id == actor_id), None)
        if self.scenario.agents and agent is None:
            raise ValueError("Actor is not declared in this scenario")
        return agent.start_location if agent and agent.start_location else self.scenario.start_location

    def body_for(self, actor_id: str) -> Body:
        agent = next((agent for agent in self.scenario.agents if agent.id == actor_id), None)
        return agent.body if agent and agent.body is not None else self.scenario.body

    @property
    def actor(self) -> Actor:
        return self.state.actors[self.actor_id]

    def save(self) -> None:
        if not self._owner._defer_save:
            self._owner._storage.save(self.state)

    def get_request(self, identity: str) -> dict[str, Any] | None:
        return self._owner._storage.get_request(self.state, identity)

    def export_state(self) -> WorldState:
        with self._lock:
            return self._owner._storage.export(self.state)

    def revision(self) -> str:
        return f"obs-{self.state.revision}"

    def tick(self) -> None:
        with self._lock:
            clock = self.state.clock
            if clock is None:
                return
            now = time.monotonic()
            elapsed = max(0, now - self._owner._last_tick)
            self._owner._last_tick = now
            if clock.running:
                clock.seconds += elapsed * clock.rate
                clock.real_seconds += elapsed
            changed = bakery.advance(self)
            path = self.path.parent / "clock-command.json"
            if path.exists():
                command = read_json(path)
                if command["world_id"] != self.state.world_id:
                    raise ValueError("Clock command belongs to another world")
                if command["id"] not in self.state.admin_requests:
                    request = ClockRequest.model_validate({"rate": command["rate"], "mode": command.get("mode")})
                    if type(command["running"]) is not bool:
                        raise ValueError("Clock running must be boolean")
                    clock.rate, clock.running = request.rate, command["running"]
                    if request.mode is not None:
                        clock.mode = request.mode
                    self.state.admin_requests.append(command["id"])
                    changed = True
            if changed:
                self.state.revision += 1
            if changed or clock.running:
                self.save()

    def visible_ids(self) -> list[str]:
        visible = [eid for eid, entity in self.state.entities.items()
                   if not entity.hidden and (entity.location == self.actor.location_id or
                   (entity.kind == "door" and entity.destination == self.actor.location_id))]
        visible.extend(eid for eid in self.actor.inventory if eid not in visible)
        for eid in visible:
            parent = self.state.entities[eid]
            for cid, child in self.state.entities.items():
                if not child.hidden and child.location == eid and cid not in visible and (
                        (child.kind == "lock" and eid in self.actor.examined) or
                        (child.kind != "lock" and (parent.open or parent.broken))):
                    visible.append(cid)
        return visible

    def in_current_room(self, entity_id: str) -> bool:
        seen = set()
        while entity_id in self.state.entities and entity_id not in seen:
            seen.add(entity_id)
            location = self.state.entities[entity_id].location
            if location in {self.actor.location_id, self.actor.actor_id}:
                return True
            if location is None:
                return False
            entity_id = location
        return False

    def inventory_units_used(self) -> int:
        used = sum(self.state.entities[eid].inventory_units for eid in self.actor.inventory)
        if self.scenario.village and self.scenario.village.ai_controlled and self.state.village:
            used += sum(self.state.village.residents[self.actor_id].cargo.values())
        return used

    def lock_for(self, entity: Entity) -> Entity:
        return self.state.entities[entity.lock_id] if entity.lock_id else entity

    def unlock_entity(self, target: str) -> None:
        entity = self.state.entities[target]
        lock_id = entity.lock_id or target
        self.state.entities[lock_id].locked = False
        entity.locked = False
        for other in self.state.entities.values():
            if other.lock_id == lock_id:
                other.locked = False

    def unusable_exit(self, entity: Entity) -> bool:
        lock = self.lock_for(entity)
        keys = [self.state.entities[eid] for eid in [lock.key_id, *lock.alternative_key_ids]
                if eid in self.state.entities]
        return entity.broken or (not entity.open and (
            lock.broken or bool(lock.locked and keys and all(key.broken for key in keys))))

    def description(self, entity: Entity) -> str:
        text = entity.description
        if entity.broken:
            text += " It is broken."
        elif entity.health is not None and entity.health < entity.max_health:
            text += " It is damaged."
        return text

    def damage_entity(self, entity_id: str, damage: int) -> str:
        entity = self.state.entities[entity_id]
        if entity.health is None or damage == 0:
            return ""
        entity.health = max(0, entity.health - damage)
        if not entity.broken:
            if entity.kind == "lock":
                return f"The lock is damaged but remains {'locked' if entity.locked else 'unlocked'}. Further hits may permanently jam it."
            return f"The {entity.name.lower()} is damaged but not broken."
        text = f"The {entity.name.lower()} breaks."
        if entity.broken_name:
            entity.name = entity.broken_name
            text += f" It is now the {entity.name.lower()}."
        if entity.broken_description:
            entity.description = entity.broken_description
        if entity.kind == "lock":
            text += " The lock is permanently jammed; the key will no longer work."
        location = entity.location
        if location in self.state.actors:
            location = self.state.actors[location].location_id
        for suffix, prototype in entity.break_into.items():
            spawned = prototype.model_copy(deep=True)
            spawned.location = location
            self.state.entities[f"{entity_id}-{suffix}"] = spawned
        return text

    def need_effects(self, name: str) -> list[str]:
        need = self.actor.needs[name]
        if not need.enabled or need.level < need.threshold:
            return []
        return ["Movement is blocked until this need is recovered."]

    def objective_discovered(self) -> bool:
        return self.scenario.goal_note_id is None or self.scenario.goal_note_id in self.actor.read_entities

    def observe(self) -> dict[str, Any]:
        with self._lock:
            self.tick()
            self._admin_controls()
            actor = self.actor
            from npc_sim import secondary_goals
            seen = set(actor.discovered_entities) | set(actor.inspected) | set(actor.examined) | set(self.visible_ids())
            discovered = seen != set(actor.discovered_entities)
            if discovered:
                actor.discovered_entities = sorted(seen)
            goals_changed = secondary_goals.refresh(self)
            if discovered or goals_changed:
                self.save()
            opening = bool(self.scenario.opening_narrative and actor.action_clock == 0
                           and not actor.examined and not actor.read_entities
                           and actor.location_id == actor.start_location_id)
            visited = {actor.start_location_id, actor.location_id, *actor.visited_locations}
            entities = []
            for eid in self.visible_ids():
                entity = self.state.entities[eid]
                facts: dict[str, Any] = {"id": eid, "name": entity.name,
                                         "description": self.description(entity), "kind": entity.kind,
                                         "takable": entity.takable, "damage": entity.damage,
                                         "health": entity.health, "max_health": entity.max_health,
                                         "status": "broken" if entity.broken else "damaged" if entity.health is not None and entity.health < entity.max_health else "intact"}
                if entity.open is not None:
                    facts["open"] = entity.open
                if entity.traversal_capabilities:
                    facts["traversal_capabilities"] = entity.traversal_capabilities.copy()
                if entity.required_challenges:
                    facts["required_work"] = [
                        aid for aid in entity.required_challenges if aid not in self.state.skill_uses]
                if entity.max_body_size is not None:
                    facts["max_body_size"] = entity.max_body_size
                if eid in actor.inspected and entity.locked is not None:
                    facts["locked"] = entity.locked
                if eid in actor.inspected and entity.jammed:
                    facts["jammed"] = True
                if entity.kind == "door" and entity.open:
                    destination = entity.destination if entity.location == actor.location_id else entity.location
                    if destination in visited:
                        facts["leads_to"] = destination
                if entity.food:
                    facts["food"] = True
                    facts["nutrition"] = entity.nutrition.model_dump() if entity.nutrition is not None else None
                    if entity.ingredients:
                        facts["ingredients"] = entity.ingredients.copy()
                if entity.power is not None:
                    facts["power"] = entity.power
                    facts["plugged_into"] = entity.plugged_into
                if entity.kind == "outlet":
                    facts["occupied"] = any(item.plugged_into == eid for item in self.state.entities.values())
                if entity.preparation:
                    facts["preparation"] = dict(entity.preparation)
                if entity.readable_text is not None:
                    facts["readable"] = True
                    if eid in actor.read_entities:
                        facts["text"] = entity.readable_text
                if entity.takable:
                    facts["inventory_units"] = entity.inventory_units
                    facts["carry_grips"] = entity.carry_grips.copy()
                facts["use_grips"] = entity.use_grips.copy()
                if entity.kind != "door" and entity.location is not None:
                    facts["location"] = entity.location
                entities.append(facts)
            return {
                "contract_version": "1.0", "world_id": self.state.world_id,
                "session_id": self.session_id, "observation_revision": self.revision(),
                "self": {
                    "actor_id": actor.actor_id, "location_id": actor.location_id,
                    "body": {
                        "id": f"{actor.actor_id}-body", "description": actor.body.description,
                        "capabilities": actor.body.capabilities,
                        "size": actor.body.size,
                        "grips": actor.body.grips.copy(),
                        "max_held_items": actor.body.max_held_items,
                        "carrying": {"rule": "inventory_units", "limit": actor.body.capacity,
                                     "used": self.inventory_units_used()},
                    },
                    "inventory": [{"id": eid, "name": self.state.entities[eid].name,
                                   "inventory_units": self.state.entities[eid].inventory_units}
                                  for eid in actor.inventory],
                    "needs": [{"id": name, "enabled": need.enabled, "level": need.level,
                               "scale": {"min": 0, "max": 100, "higher_means": f"more_{'hungry' if name == 'food' else 'tired'}"},
                               "effects": self.need_effects(name)}
                              for name, need in actor.needs.items()],
                },
                "location": {"id": actor.location_id,
                             "description": self.state.entities[actor.location_id].description},
                "opening": opening,
                "narrative": self.scenario.opening_narrative if opening else self.state.entities[actor.location_id].description,
                "visible_entities": entities, "actions": self.actions(),
                **({"secondary_goals": secondary_goals.observation_goals(self)}
                   if self.state.secondary_goals else {}),
                **({"role": next(agent.role for agent in self.scenario.agents if agent.id == self.actor_id),
                    "visible_actors": [
                        {"actor_id": other.actor_id,
                         "capabilities": other.body.capabilities.copy(),
                         "inventory": [{"id": eid, "name": self.state.entities[eid].name}
                                       for eid in other.inventory]}
                        for other in self.state.actors.values()
                        if other.actor_id != self.actor_id and other.location_id == actor.location_id],
                    "messages": copy.deepcopy(actor.messages),
                    **({"conversation": {"with": (thread.listener if thread.speaker == self.actor_id else thread.speaker),
                                          "turns_remaining": 4 - thread.turns,
                                          "expires_at": thread.expires_at,
                                          "guidance": "You may reply briefly or leave; hearing is not accepting an order."}}
                       if self.scenario.village and self.scenario.village.ai_controlled
                       and (thread := village_conversation.pending(self))
                       else {})}
                   if self.scenario.agents else {}),
                **(bakery.observation(self) if self.scenario.bakery else {}),
                **voyage.observation(self),
                **skills.observation(self),
                **rps.observation(self),
                **tic_tac_toe.observation(self),
                **erp.observation(self),
                **bbq.observation(self),
                **village.observation(self),
                **({"discovered_goal": {"objective": self.scenario.objective,
                                       "type": self.scenario.goal_type,
                                       "source_id": self.scenario.goal_note_id}}
                   if self.scenario.goal_note_id is not None and self.objective_discovered() else {}),
            }

    def actions(self) -> list[dict[str, Any]]:
        if self.scenario.bbq is not None:
            return bbq.actions(self)
        if self.scenario.erp is not None:
            return erp.actions(self)
        visible = self.visible_ids()
        actions: list[dict[str, Any]] = []

        def add(aid: str, description: str, properties: dict[str, Any],
                required: list[str], targets: list[str], availability: str = "available",
                reason: str | None = None) -> None:
            actions.append({
                "id": aid, "description": description,
                "arguments_schema": {"type": "object", "properties": properties,
                                     "required": required, "additionalProperties": False},
                "targets": targets, "availability": availability, "reason": reason,
            })

        add("look", "Inspect a visible object, or observe your surroundings without a target.",
            {"target": {"type": "string", "enum": visible}} if visible else {}, [], visible)
        search_targets = [self.actor.location_id, *(eid for eid in visible if self.in_current_room(eid))]
        add("search", "Search a local room, wall or visible object for concealed features. "
            "Hidden features are not facts until a search reveals them; contents may need a separate opening action.",
            {"target": {"type": "string", "enum": search_targets}}, ["target"], search_targets)
        peers = [other.actor_id for other in self.state.actors.values()
                 if other.actor_id != self.actor_id and other.location_id == self.actor.location_id]
        if peers:
            conversation = (village_conversation.pending(self)
                            if self.scenario.village and self.scenario.village.ai_controlled else None)
            add("say", "Speak to nearby teammates. Only agents here hear this; coordinate your shared goal."
                + (" A brief reply is welcome; acknowledging speech does not accept a request."
                   if conversation else ""),
                {"message": {"type": "string", "minLength": 1, "maxLength": 500, "pattern": r"\S"},
                 **({"recipient": {"type": "string", "enum": peers}}
                    if self.scenario.village and self.scenario.village.ai_controlled else {})},
                ["message"], peers)
            if self.actor.inventory:
                add("give", "Give a carried item to a nearby teammate if it fits their carrying capacity.",
                    {"target": {"type": "string", "enum": self.actor.inventory},
                     "recipient": {"type": "string", "enum": peers}},
                    ["target", "recipient"], self.actor.inventory)
        readable = [eid for eid in visible if self.state.entities[eid].readable_text is not None
                    and self.in_current_room(eid) and not self.state.entities[eid].broken]
        if readable:
            add("read", "Read the text on a visible local note or other readable object.",
                {"target": {"type": "string", "enum": readable}}, ["target"], readable)
        for verb, description in (
            ("open", "Open a visible container or door. A locked door must be unlocked first."),
            ("unlock", "Unlock a visible lock using a matching item you carry."),
            ("take", "Pick up a visible takable object physically in your current room, including exposed container "
             "contents, if your actual grips can hold it and its inventory units fit your body's remaining capacity. Remembering an item elsewhere "
             "does not make it reachable. Containers retain their contents."),
            ("drop", "Drop an item you carry at your current location."),
            ("go", "Cross a visible exit when it is open and unlocked, your body fits, and you have its required abilities."),
        ):
            if verb == "drop":
                targets = self.actor.inventory
            elif verb == "take":
                targets = [eid for eid in visible if self.state.entities[eid].takable
                           and eid not in self.actor.inventory and self.in_current_room(eid)]
            elif verb == "go":
                targets = [eid for eid in visible if self.state.entities[eid].kind == "door"]
            elif verb == "unlock":
                targets = [eid for eid in visible if self.state.entities[eid].kind in {"door", "lock"}]
            else:
                targets = [eid for eid in visible if self.state.entities[eid].open is not None]
            if not targets:
                continue
            properties: dict[str, Any] = {"target": {"type": "string", "enum": targets}}
            required = ["target"]
            if verb == "unlock":
                properties["item"] = ({"type": "string", "enum": self.actor.inventory}
                                      if self.actor.inventory else {"type": "string"})
                required.append("item")
            availability = "unknown" if verb in {"unlock", "open", "go"} else "available"
            reason = None
            if verb == "take" and not any(
                    self.inventory_units_used() + self.state.entities[eid].inventory_units <= self.actor.body.capacity
                    for eid in targets):
                availability, reason = "blocked", "None of the local items fit your remaining inventory-unit capacity."
            if verb == "unlock" and not self.actor.inventory:
                availability, reason = "blocked", "You must carry an item to try unlocking."
            if verb == "go" and any(self.need_effects(name) for name in self.actor.needs):
                availability, reason = "blocked", "An enabled need prevents movement."
            add(verb, description, properties, required, targets, availability, reason)
            if verb in {"go", "open"}:
                constraints = {
                    eid: skills.barrier_reason(self, self.state.entities[eid], moving=verb == "go")
                    for eid in targets}
                if any(constraints.values()):
                    actions[-1]["target_requirements"] = constraints
                    if all(constraints.values()):
                        actions[-1]["availability"] = "blocked"
                        actions[-1]["reason"] = "; ".join(sorted({value for value in constraints.values() if value}))
        hit_targets = [eid for eid in visible if self.state.entities[eid].health is not None]
        if hit_targets:
            add("hit", "Hit a visible object with an intact item you carry. The item's damage reduces "
                "the target's health, and the target's damage reduces the carried item's health in the same impact. "
                "Either object may break. Broken containers reveal contents; a broken lock can permanently jam its door.",
                {"target": {"type": "string", "enum": hit_targets},
                 "item": {"type": "string", "enum": self.actor.inventory} if self.actor.inventory else {"type": "string"}},
                ["target", "item"], hit_targets,
                "available" if self.actor.inventory else "blocked",
                None if self.actor.inventory else "Carry an item before hitting something.")
        for name, need in self.actor.needs.items():
            if not need.enabled:
                continue
            recovery = self.scenario.recovery[name]
            if name == "sleep":
                actions.append({
                    "id": recovery.action_id, "description": recovery.description,
                    "arguments_schema": recovery.arguments_schema,
                    "targets": [], "availability": "available", "reason": None})
            else:
                foods = [eid for eid in self.actor.inventory if self.state.entities[eid].food]
                if foods:
                    add(recovery.action_id, recovery.description,
                        {"target": {"type": "string", "enum": foods}}, ["target"], foods)
        actions.extend(recipes.actions(self))
        actions.extend(equipment.actions(self))
        actions.extend(bakery.actions(self))
        actions.extend(voyage.actions(self))
        actions.extend(skills.actions(self))
        actions.extend(spells.actions(self))
        actions.extend(rps.actions(self))
        actions.extend(tic_tac_toe.actions(self))
        actions.extend(village.actions(self))
        foods = [eid for eid in visible if self.state.entities[eid].food
                 and self.in_current_room(eid) and not self.state.entities[eid].broken]
        surfaces = [eid for eid in visible if self.state.entities[eid].kind == "container"
                    and self.state.entities[eid].open and not self.state.entities[eid].broken
                    and self.in_current_room(eid)]
        if self.scenario.recipes and foods and surfaces:
            add("serve", "Place visible local food on an open local plate or surface. Food need not be carried.",
                {"target": {"type": "string", "enum": foods},
                 "surface": {"type": "string", "enum": surfaces}}, ["target", "surface"], foods)
        handling.annotate(self, actions)
        if self.scenario.village and self.scenario.village.daily_life:
            from npc_sim import village_life
            for action in actions:
                blocked = village_life.action_blocker(self, action["id"], personal=True)
                if blocked:
                    action["availability"], action["reason"] = "blocked", blocked[1]
            from npc_sim import village_fallback
            village_fallback.annotate(self, actions)
            if self.scenario.village.ai_controlled:
                goal = self.state.village.residents[self.actor_id].fallback.route_goal
                if goal:
                    if village_fallback._route_evidence(self) == goal.evidence:
                        current = self.actor.location_id
                        path = village.route(self, current, goal.target_location,
                                             known_routes=self.state.village.residents[self.actor_id].known_routes)
                        if path:
                            for action in actions:
                                if action["id"] == "go":
                                    for target in action["targets"]:
                                        door = self.state.entities[target]
                                        destination = (door.destination if door.location == current else
                                                       door.location if door.destination == current else None)
                                        if destination != path[0]:
                                            continue
                                        reason = ("This optional search has no new personal evidence; wait for an encounter or appointment."
                                                  if goal.blocked_reason else
                                                  "Repeated travel for this optional search has no new personal evidence."
                                                  if f"{destination}->{current}" in goal.attempted_edges else None)
                                        if reason:
                                            action.setdefault("target_requirements", {})[target] = reason
                                    if (action.get("availability") != "blocked" and action["targets"]
                                            and len(action.get("target_requirements", {})) == len(action["targets"])):
                                        action["availability"], action["reason"] = "blocked", next(iter(action["target_requirements"].values()))
        if self.scenario.village and self.scenario.village.ai_controlled and self.scenario.id == "millhaven-living":
            from npc_sim import village_walking
            for action in actions:
                if action["id"] != "go":
                    continue
                for target in action["targets"]:
                    blocked = village_walking.check_step(self, {"target": target})
                    if blocked:
                        action.setdefault("target_requirements", {})[target] = blocked[1]
                if action["targets"] and len(action.get("target_requirements", {})) == len(action["targets"]):
                    action["availability"] = "blocked"
                    action["reason"] = next(iter(action["target_requirements"].values()))
        if self.state.clock is not None and self.state.clock.mode == "decision_paused":
            for action in actions:
                action["simulation_seconds"] = bakery.action_seconds(action["id"])
        return actions

    def evaluate_goal(self, goal_type: str) -> dict[str, Any]:
        with self._lock:
            self.tick()
            return self._evaluate_goal(goal_type)

    def _evaluate_goal(self, goal_type: str) -> dict[str, Any]:
        if goal_type != self.scenario.goal_type:
            raise ValueError(f"Unsupported goal type: {goal_type}")
        for eid, reason in self.scenario.failure_outputs.items():
            if eid in self.state.entities:
                return {"status": "failed", "reason": reason,
                        "evidence": {"irreversible_output": eid},
                        "observation_revision": self.revision()}
        for eid in self.scenario.required_intact_entities:
            entity = self.state.entities.get(eid)
            if entity is not None and entity.broken:
                return {"status": "failed",
                        "reason": f"The irreplaceable {entity.name.lower()} is broken; the objective is no longer achievable.",
                        "evidence": {"broken_required_entity": eid},
                        "observation_revision": self.revision()}
        if goal_type == "rps_match_completed":
            return rps.evaluate_goal(self)
        if goal_type == "tic_tac_toe_completed":
            return tic_tac_toe.evaluate_goal(self)
        if goal_type == "erp_invoice_released":
            return erp.evaluate_goal(self)
        if goal_type == "bbq_temperature_controlled":
            return bbq.evaluate_goal(self)
        if goal_type == "village_restored":
            return village.evaluate_goal(self)
        if goal_type == "all_actors_at_exit_location":
            checks: dict[str, Any] = {}
            team_ready = True
            for agent in self.scenario.agents:
                actor = self.state.actors.get(agent.id)
                if actor is None:
                    team_ready = False
                    checks[agent.id] = {"status": "active", "reason": "Agent has not joined the world."}
                else:
                    bound = self.bind(agent.id, actor.session_id)
                    discovered = bound.objective_discovered()
                    team_ready = team_ready and discovered
                    checks[agent.id] = (bound._exit_goal() if discovered else {
                        "status": "active", "reason": "The teammate has not discovered the assignment."})
            supported = {capability for agent in self.scenario.agents
                         if agent.id in self.state.actors
                         for capability in self.state.actors[agent.id].body.capabilities}
            missing_work = [challenge for challenge in self.scenario.skill_challenges
                            if challenge.id not in self.state.skill_uses and challenge.capability not in supported]
            if team_ready and missing_work:
                missing_skills = sorted({challenge.capability for challenge in missing_work})
                return {
                    "status": "blocked",
                    "reason": ("The team admits defeat: no assigned agent has the required skills: "
                               + ", ".join(skill.replace("_", " ") for skill in missing_skills)
                               + ". Mandatory cooperative work cannot be completed; retrying will not grant these abilities."),
                    "evidence": {
                        "agents": checks,
                        "missing_required_skills": missing_skills,
                        "impossible_challenges": [
                            {"id": challenge.id, "capability": challenge.capability,
                             "description": challenge.description} for challenge in missing_work],
                        "team_capabilities": {
                            agent.id: self.state.actors[agent.id].body.capabilities.copy()
                            for agent in self.scenario.agents},
                    },
                    "observation_revision": self.revision(),
                }
            completed_work = all(challenge.id in self.state.skill_uses for challenge in self.scenario.skill_challenges)
            achieved = all(check["status"] == "achieved" for check in checks.values()) and completed_work
            failed = any(check["status"] == "failed" for check in checks.values())
            return {"status": "achieved" if achieved else "failed" if failed else "active",
                    "reason": "A teammate's route to the exit is permanently blocked." if failed else "",
                    "evidence": {"agents": checks, **(
                        {"cooperative_work": {challenge.id: self.state.skill_uses.get(challenge.id)
                                              for challenge in self.scenario.skill_challenges}}
                        if self.scenario.skill_challenges else {})},
                    "observation_revision": self.revision()}
        if goal_type == "bread_batches_served":
            config = self.scenario.bakery
            if config is None:
                raise ValueError("Missing bakery configuration")
            checks = {
                bowl: bool(bowl in self.state.batches and self.state.batches[bowl].stage == "served"
                           and not self.state.entities[bowl].broken
                           and self.state.entities[bowl].location == config.display)
                for bowl in config.bowls}
            achieved = all(checks.values()) and not self.state.entities[config.display].broken
            failed = any(batch.stage == "ruined" for batch in self.state.batches.values()) or any(
                self.state.entities[eid].broken for eid in [*config.bowls, config.display])
            return {"status": "achieved" if achieved else "failed" if failed else "active",
                    "reason": "A required batch was ruined or its bowl/display broke." if failed else "",
                    "evidence": {"checks": checks, "batches": {eid: batch.model_dump()
                                                             for eid, batch in self.state.batches.items()}},
                    "observation_revision": self.revision()}
        if not self.objective_discovered():
            return {"status": "active",
                    "reason": "The assigned task has not been discovered by reading its note.",
                    "evidence": {"actor_id": self.actor_id, "checks": {"objective_discovered": False}},
                    "observation_revision": self.revision()}
        if goal_type == "treasures_returned":
            return voyage.evaluate_goal(self)
        if goal_type == "entity_prepared":
            goal_id = self.scenario.goal_entity_id
            goal_location = self.scenario.goal_location_id
            if goal_id is None or goal_location is None:
                raise ValueError("Prepared-item goal has no configured output or destination")
            entity = self.state.entities.get(goal_id)
            plate = self.state.entities.get(goal_location)
            prepared = entity is not None and not entity.broken
            served = bool(prepared and plate is not None and not plate.broken
                          and entity.location == self.scenario.goal_location_id)
            achieved = prepared and served
            return {"status": "achieved" if achieved else "active", "reason": "",
                    "evidence": {"actor_id": self.actor_id,
                                 "checks": {"prepared_item": prepared, "served_on_target": served, **(
                                     {"objective_discovered": True} if self.scenario.goal_note_id is not None else {})}, **(
                        {"entity_id": self.scenario.goal_entity_id,
                         "location_id": entity.location,
                         "ingredients": entity.ingredients.copy(),
                         "nutrition": entity.nutrition.model_dump() if entity.nutrition else None}
                        if achieved else {})},
                    "observation_revision": self.revision()}
        return self._exit_goal()

    def _exit_goal(self) -> dict[str, Any]:
        achieved = self.actor.location_id == self.scenario.exit_location
        route_body = self.actor.body.model_copy(update={"size": "small"}) if (
            "shrink_magic" in self.actor.body.capabilities) else self.actor.body
        reachable = {self.actor.location_id}
        pending = [self.actor.location_id]
        while pending:
            location = pending.pop()
            for eid, entity in self.state.entities.items():
                if entity.kind != "door":
                    continue
                if (not set(entity.traversal_capabilities) <= set(route_body.capabilities)
                        or handling.passage_reason(route_body, entity)):
                    continue
                if self.unusable_exit(entity) and not (
                        any("unlock_magic" in actor.body.capabilities for actor in self.state.actors.values())
                        and spells.unlock_block(self, eid) is None):
                    continue
                if entity.location == location:
                    destination = entity.destination
                elif entity.destination == location:
                    destination = entity.location
                else:
                    continue
                if destination is not None and destination not in reachable:
                    reachable.add(destination)
                    pending.append(destination)
        failed = not achieved and self.scenario.exit_location not in reachable
        return {"status": "achieved" if achieved else "failed" if failed else "active",
                "reason": ("No remaining route to the final exit fits this body's abilities and size "
                           "without an irreversibly blocked door or lock.") if failed else "",
                "evidence": {"actor_id": self.actor_id, "location_id": self.actor.location_id},
                "observation_revision": self.revision()}

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self.state.model_dump(
                mode="json", exclude={"requests", "admin_requests", "request_history_id"})

    def _admin_controls(self) -> None:
        path = self.path.parent / "needs-command.json"
        if not path.exists():
            return
        command = read_json(path)
        if command["world_id"] != self.state.world_id:
            raise ValueError("Admin command belongs to another world")
        if command["id"] not in self.state.admin_requests:
            if type(command["enabled"]) is not bool:
                raise ValueError("Need enabled must be boolean")
            before = self.state.model_copy(deep=True)
            try:
                actor = self.state.actors[command["actor_id"]]
                actor.needs[command["need"]].enabled = command["enabled"]
                self.state.revision += 1
                self.state.admin_requests.append(command["id"])
                self.save()
            except (KeyError, OSError):
                self.state = before
                raise

    def toggle_need(self, actor_id: str, name: str, enabled: bool) -> dict[str, Any]:
        with self._lock:
            if actor_id not in self.state.actors or name not in self.state.actors[actor_id].needs:
                raise ValueError("Unknown actor or need")
            self.state.actors[actor_id].needs[name].enabled = enabled
            self.state.revision += 1
            self.save()
            return self.snapshot()

    def act(self, request_id: str, expected_observation_revision: str,
            action_id: str, arguments: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            request = {"action_id": action_id, "arguments": arguments,
                       "expected_observation_revision": expected_observation_revision}
            identity = f"{self.session_id}:{request_id}"
            previous = self.get_request(identity)
            if previous:
                if previous["request"] != request:
                    return self._result(request_id, "REJECTED", "REQUEST_ID_CONFLICT",
                                        "Request ID was already used with different arguments.")
                return copy.deepcopy(previous["result"])
            self.tick()
            if expected_observation_revision != self.revision():
                return self._record(identity, request, self._result(
                    request_id, "REJECTED", "STALE_OBSERVATION", "The world changed. Observe again."))
            descriptor = next((action for action in self.actions() if action["id"] == action_id), None)
            if descriptor is None:
                return self._record(identity, request, self._result(
                    request_id, "REJECTED", "ACTION_UNAVAILABLE", "This action is not currently advertised."))
            try:
                validate(arguments, descriptor["arguments_schema"])
            except ValidationError as error:
                return self._record(identity, request, self._result(
                    request_id, "REJECTED", "INVALID_ARGUMENTS", error.message))
            before = self.state.model_copy(deep=True)
            try:
                self._owner._defer_save = True
                from npc_sim import village_firewood
                heated = bool(self.state.village and village_firewood.fuel_required(self, action_id, arguments))
                fuel_before = self.state.village.stocks.get(self.actor.location_id, {}).get("firewood", 0) if heated else 0
                outcome, code, explanation, recovery_need = self._apply(action_id, arguments)
                if outcome == "APPLIED":
                    if heated:
                        already_consumed = fuel_before - self.state.village.stocks.get(self.actor.location_id, {}).get("firewood", 0)
                        village_firewood.record_action(self, action_id, max(0, already_consumed))
                    village_minutes = village.after_action(self, action_id, arguments)
                    if village_minutes:
                        if self.scenario.village and self.scenario.village.time_based_turns:
                            explanation += f" Available again at {village.clock_time(self.state.village.residents[self.actor_id].available_at)}."
                        else:
                            explanation += f" {village_minutes} game minutes pass."
                    voyage.advance(self, action_id)
                    duration = bakery.charge_action(self, action_id, arguments)
                    if duration:
                        explanation += f" This action takes {duration} simulated seconds."
                    if action_id not in {"look", "read"}:
                        self.actor.action_clock += 1
                        for name, need in self.actor.needs.items():
                            if need.enabled and name != recovery_need:
                                need.level = min(100, need.level + need.rate)
                    self.state.revision += 1
                elif (outcome == "NO_CHANGE" and self.scenario.village and self.scenario.village.expanded_rules
                      and action_id in {"look", "read"}):
                    self.state.village.free_turn_actor = self.actor_id
                    self.state.revision += 1
                if self.scenario.village and self.scenario.village.ai_controlled:
                    from npc_sim import village_fallback
                    if village_fallback.record_result(self, action_id, arguments, outcome, code, explanation):
                        if outcome != "APPLIED":
                            self.state.revision += 1
                result = self._result(request_id, outcome, code, explanation)
                self._owner._defer_save = False
                return self._record(identity, request, result)
            except (OSError, ValueError):
                self.state = before
                raise
            finally:
                self._owner._defer_save = False

    def _record(self, identity: str, request: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
        previous = self.state.requests.copy()
        self.state.requests[identity] = {"request": copy.deepcopy(request), "result": copy.deepcopy(result)}
        try:
            self.save()
        except (OSError, ValueError):
            self.state.requests = previous
            raise
        return result

    def _result(self, rid: str, outcome: str, code: str, explanation: str) -> dict[str, Any]:
        return {"session_id": self.session_id, "request_id": rid, "outcome": outcome,
                "code": code, "explanation": explanation, "observation": self.observe()}

    def _apply(self, action: str, args: dict[str, Any]) -> tuple[str, str, str, str | None]:
        def reject(code: str, text: str) -> tuple[str, str, str, None]:
            return "REJECTED", code, text, None

        def unchanged(text: str) -> tuple[str, str, str, None]:
            return "NO_CHANGE", "NO_CHANGE", text, None

        target = args.get("target")
        entity = self.state.entities.get(target) if target else None
        if action in {"adjust_bbq_heat", "monitor_bbq"}:
            return bbq.apply(self, args.get("heat_percent"))
        if action == "erp_terminal":
            return erp.apply(self, args["command"])
        if self.scenario.village and self.scenario.village.ai_controlled:
            from npc_sim import village_ai, village_life, village_fallback
            if self.scenario.village.time_based_turns and action not in {
                    "look", "read", "village_stop_route", "village_maintenance_pause", "village_research_pause", "village_mining_pause", "village_livestock_pause"}:
                person = self.state.village.residents[self.actor_id]
                if self.state.village.minute < person.available_at:
                    return reject("ACTOR_BUSY", f"Available again at {village.clock_time(person.available_at)}.")
            blocked = village_fallback.action_blocker(self, action, args)
            if blocked:
                return reject(*blocked)
            blocked = village_life.action_blocker(self, action)
            if blocked:
                return reject(*blocked)
            from npc_sim import village_firewood
            blocked = village_firewood.action_blocker(self, action, args)
            if blocked:
                return reject(*blocked)
            if action in village_ai.VERBS:
                from npc_sim import village_precheck
                blocked = village_precheck.check(self, action, args)
                if blocked:
                    return reject(*blocked)
                return village_ai.apply(self, action, args)
        if self.scenario.village and self.scenario.village.ai_controlled:
            from npc_sim import village_precheck
            blocked = village_precheck.check(self, action, args)
            if blocked:
                return reject(*blocked)
        if action in village.VERBS:
            return village.apply(self, action, args)
        if action == "look":
            if target is None or target in self.actor.examined:
                return unchanged("You observe the visible surroundings." if target is None else self.description(entity))
            self.actor.examined.append(target)
            if target not in self.actor.inspected:
                self.actor.inspected.append(target)
            detail = self.description(entity)
            if entity.locked is not None:
                detail += " It is locked." if entity.locked else " It is unlocked."
            if entity.jammed:
                detail += " Its lid is jammed."
            return "APPLIED", "INSPECTED", detail, None
        if action == "read":
            if (self.scenario.village and self.scenario.village.time_based_turns
                    and target == "noticeboard"):
                if target not in self.actor.read_entities:
                    self.actor.read_entities.append(target)
                return "APPLIED", "READ", village.read_noticeboard(self), None
            if target in self.actor.read_entities:
                return unchanged(entity.readable_text)
            self.actor.read_entities.append(target)
            return "APPLIED", "READ", f"You read the {entity.name.lower()}: {entity.readable_text}", None
        if action == "search":
            revealed = []
            for eid, hidden in self.state.entities.items():
                if hidden.hidden and hidden.discovery_target == target and self.in_current_room(eid):
                    hidden.hidden = False
                    revealed.append(hidden.name)
            if not revealed:
                return unchanged("You search carefully but find no new concealed features.")
            return "APPLIED", "FEATURE_DISCOVERED", "You search and discover " + ", ".join(revealed) + ".", None
        if action == "say":
            message = {"actor_id": self.actor_id, "text": args["message"]}
            if self.scenario.village and self.scenario.village.ai_controlled:
                peers = [other.actor_id for other in self.state.actors.values()
                         if other.actor_id != self.actor_id and other.location_id == self.actor.location_id
                         and not (self.state.village.residents[other.actor_id].life
                                  and self.state.village.residents[other.actor_id].life.sleeping)]
                recipient = village_conversation.recipient(self, args["message"], peers,
                                                           args.get("recipient"))
                if recipient:
                    village_conversation.spoken(self, recipient)
            for other in self.state.actors.values():
                if self.scenario.village and self.scenario.village.daily_life and self.state.village:
                    life = self.state.village.residents[other.actor_id].life
                    if life and life.sleeping:
                        continue
                if other.location_id == self.actor.location_id:
                    other.messages = (other.messages + [message.copy()])[-20:]
            return "APPLIED", "SPOKEN", f'You say: "{args["message"]}"', None
        if action in {challenge.id for challenge in self.scenario.skill_challenges}:
            return skills.apply(self, action)
        if action == "play_rps":
            return rps.apply(self, args["move"])
        if action == "play_tic_tac_toe":
            return tic_tac_toe.apply(self, int(args["cell"]))
        if action in spells.CAPABILITIES:
            return spells.apply(self, action, args)
        capability = "move" if action == "go" or action in voyage.MOVEMENT_VERBS else "manipulate"
        if action not in {recovery.action_id for recovery in self.scenario.recovery.values()}:
            if capability not in self.actor.body.capabilities:
                return reject("BODY_CAPABILITY_REQUIRED", f"Your body cannot {capability}.")
        blocked = handling.action_reason(self, action, args)
        if blocked:
            return reject("BODY_HANDLING_REQUIRED", blocked)
        if action in self.scenario.recipe_actions:
            return recipes.apply(self, action, args)
        if action in voyage.VERBS:
            return voyage.apply(self, action, args)
        if action in equipment.VERBS:
            return equipment.apply(self, action, args)
        if action in bakery.VERBS:
            return bakery.apply(self, action, args)
        if action == "give":
            recipient = self.state.actors[args["recipient"]]
            if target not in self.actor.inventory or recipient.location_id != self.actor.location_id:
                return reject("OUT_OF_REACH", "The item must be carried and the recipient must be here.")
            if self.scenario.village and self.scenario.village.daily_life and self.state.village:
                life = self.state.village.residents[recipient.actor_id].life
                if life and life.sleeping:
                    return reject("RESIDENT_ASLEEP", "The recipient is sleeping; do not interrupt their rest.")
                if life and life.work:
                    return reject("RESIDENT_BUSY", "The recipient must finish their committed batch first.")
            if (self.scenario.village and self.scenario.village.ai_controlled and self.state.village
                    and self.state.village.residents[recipient.actor_id].cargo):
                return reject("CAPACITY_EXCEEDED", "The recipient must deliver their shipment before receiving tools.")
            if target in self.state.batches and self.state.batches[target].stage not in {
                    "measuring", "cooled", "served", "ruined"}:
                return reject("BATCH_IN_PROGRESS", "An active batch cannot be handed over.")
            used = sum(self.state.entities[eid].inventory_units for eid in recipient.inventory)
            if self.scenario.village and self.scenario.village.ai_controlled and self.state.village:
                used += sum(self.state.village.residents[recipient.actor_id].cargo.values())
            if used + entity.inventory_units > recipient.body.capacity:
                return reject("CAPACITY_EXCEEDED", "The recipient does not have enough carrying capacity.")
            self.actor.inventory.remove(target)
            recipient.inventory.append(target)
            entity.location = recipient.actor_id
            return "APPLIED", "GIVEN", f"You give the {entity.name.lower()} to {recipient.actor_id}.", None
        if action in {"take", "drop"} and target in self.state.batches:
            batch = self.state.batches[target]
            if batch.stage not in {"measuring", "cooled", "served", "ruined"}:
                return reject("BATCH_IN_PROGRESS", "Use the batch's handling action instead of moving an active bowl.")
        if action == "serve":
            surface = args["surface"]
            if not self.in_current_room(target) or not self.in_current_room(surface):
                return reject("OUT_OF_REACH", "Food and its destination must be physically in your current room.")
            ancestor = surface
            while ancestor in self.state.entities:
                if ancestor == target:
                    return reject("INVALID_TARGET", "Food cannot be placed inside itself or its contents.")
                ancestor = self.state.entities[ancestor].location
            if entity.location == surface:
                return unchanged("The food is already on that surface.")
            if target in self.actor.inventory:
                self.actor.inventory.remove(target)
            entity.location = surface
            return "APPLIED", "SERVED", f"You serve the {entity.name.lower()} on the {self.state.entities[surface].name.lower()}.", None
        if action == "open":
            blocked = skills.barrier_reason(self, entity)
            if blocked:
                return reject("PREREQUISITE_REQUIRED", blocked)
            if entity.broken:
                return unchanged("The box is broken; its contents are already exposed.") if entity.kind == "container" else reject("ITEM_BROKEN", "This object is broken and cannot be opened.")
            if entity.jammed:
                if target not in self.actor.inspected:
                    self.actor.inspected.append(target)
                    self.state.revision += 1
                return reject("JAMMED", f"The {entity.name.lower()} is jammed and cannot be opened.")
            if self.lock_for(entity).broken:
                return reject("LOCK_BROKEN", "The broken lock has permanently jammed the door.")
            if self.lock_for(entity).locked:
                if target not in self.actor.inspected:
                    self.actor.inspected.append(target)
                    self.state.revision += 1
                return reject("LOCKED", f"The {entity.name.lower()} is locked.")
            if entity.open:
                return unchanged("It is already open.")
            entity.open = True
        elif action == "take":
            permission = village.permission_reason(self, target)
            if permission:
                return reject("PERMISSION_REQUIRED", permission)
            if entity.plugged_into is not None:
                return reject("APPLIANCE_CONNECTED", "Switch off and unplug the appliance before carrying it.")
            if not self.in_current_room(target):
                return reject("OUT_OF_REACH", "You can only pick up items physically in your current room.")
            used = self.inventory_units_used()
            if used + entity.inventory_units > self.actor.body.capacity:
                return reject("CAPACITY_EXCEEDED",
                              f"The {entity.name.lower()} requires {entity.inventory_units} inventory units; "
                              f"your body is using {used} of {self.actor.body.capacity} units.")
            entity.location = self.actor.actor_id
            self.actor.inventory.append(target)
            return "APPLIED", "APPLIED", f"You pick up the {entity.name.lower()}.", None
        elif action == "drop":
            self.actor.inventory.remove(target)
            entity.location = self.actor.location_id
            return "APPLIED", "APPLIED", f"You put down the {entity.name.lower()}.", None
        elif action == "unlock":
            item = args["item"]
            if item not in self.actor.inventory:
                return reject("NOT_CARRIED", "You must carry the item.")
            lock = self.lock_for(entity)
            if lock.broken or entity.broken:
                return reject("LOCK_BROKEN", "The broken lock is permanently jammed. The key cannot repair it.")
            if self.state.entities[item].broken:
                return reject("ITEM_BROKEN", "The item you are using is broken.")
            if item not in {lock.key_id, *lock.alternative_key_ids}:
                return reject("WRONG_ITEM", "That item does not fit this lock.")
            if not lock.locked:
                return unchanged("The door is already unlocked.")
            self.unlock_entity(target)
            if target not in self.actor.inspected:
                self.actor.inspected.append(target)
            return "APPLIED", "APPLIED", f"You unlock the {entity.name.lower()}.", None
        elif action == "hit":
            item = args["item"]
            if item not in self.actor.inventory:
                return reject("NOT_CARRIED", "You must carry the item used to hit.")
            weapon = self.state.entities[item]
            if item == target:
                return reject("INVALID_TARGET", "An item cannot hit itself.")
            if weapon.broken:
                return reject("ITEM_BROKEN", "The item you are using is broken.")
            if entity.broken:
                return unchanged(f"The {entity.name.lower()} is already broken.")
            target_damage = min(weapon.damage, entity.health)
            weapon_damage = min(entity.damage, weapon.health) if weapon.health is not None else 0
            if target_damage == 0 and weapon_damage == 0:
                return unchanged("Neither object takes damage from the impact.")
            text = f"You hit the {entity.name.lower()} with the {weapon.name.lower()}, dealing {target_damage} damage. "
            text += f"The impact also deals {weapon_damage} damage to the {weapon.name.lower()}."
            details = [self.damage_entity(target, target_damage), self.damage_entity(item, weapon_damage)]
            text += "".join(f" {detail}" for detail in details if detail)
            return "APPLIED", "BROKEN" if entity.broken or weapon.broken else "DAMAGED", text, None
        elif action == "go":
            if (self.scenario.village and self.scenario.village.expanded_rules
                    and self.state.village.wagon and self.state.village.wagon.driver == self.actor_id
                    and not entity.village_road):
                return reject("WAGON_ROAD_ONLY", "The wagon cannot use this footpath. Park it before walking here.")
            blocked = skills.barrier_reason(self, entity, moving=True)
            if blocked:
                code = ("BODY_CAPABILITY_REQUIRED" if not set(entity.traversal_capabilities)
                        <= set(self.actor.body.capabilities) else "BODY_SIZE_REQUIRED"
                        if handling.passage_reason(self.actor.body, entity) else "PREREQUISITE_REQUIRED")
                return reject(code, blocked)
            if any(self.need_effects(name) for name in self.actor.needs):
                return reject("NEED_CONSTRAINT", "Food or sleep recovery is required before movement.")
            if self.unusable_exit(entity):
                return reject("EXIT_BROKEN", "The exit is permanently blocked by a broken door or lock.")
            if self.lock_for(entity).locked or not entity.open:
                return reject("EXIT_BLOCKED", "The exit must be unlocked and open.")
            destination = entity.destination if entity.location == self.actor.location_id else entity.location
            if destination is None:
                raise ValueError("Exit has no destination")
            blocked = voyage.movement_block(self, destination, target)
            if blocked:
                return reject("PASSAGE_BLOCKED", blocked)
            self.actor.location_id = destination
            if self.actor.location_id not in self.actor.visited_locations:
                self.actor.visited_locations.append(self.actor.location_id)
            movement = ("swim" if "swim" in entity.traversal_capabilities else
                        "fly" if "fly" in entity.traversal_capabilities else "go")
            return "APPLIED", "APPLIED", f"You {movement} through the {entity.name.lower()}.", None
        else:
            name = next(name for name, recovery in self.scenario.recovery.items() if recovery.action_id == action)
            need = self.actor.needs[name]
            if need.level == 0:
                return unchanged("This need is already satisfied.")
            if name == "food":
                self.actor.inventory.remove(target)
                entity.location = None
            need.level = max(0, need.level - self.scenario.recovery[name].recovery)
            return "APPLIED", "RECOVERED", f"You recover {name}.", name
        return "APPLIED", "APPLIED", f"You open the {entity.name.lower()}.", None
