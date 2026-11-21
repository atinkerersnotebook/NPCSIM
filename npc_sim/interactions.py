import copy
import re
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

from pydantic import Field, field_validator, model_validator

from npc_sim.models import Entity, SecondaryGoal, SecondaryGoalSpec, StrictModel
from npc_sim import secondary_goals

if TYPE_CHECKING:
    from npc_sim.controller import Controller
    from npc_sim.world import World


class ChatRequest(StrictModel):
    run_id: str
    actor_id: str
    question: str = Field(min_length=1, max_length=2000)

    @field_validator("question")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Enter a question.")
        return value.strip()


class EditRequest(StrictModel):
    run_id: str
    instruction: str = Field(min_length=1, max_length=4000)

    @field_validator("instruction")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Describe a world change.")
        return value.strip()


class ApprovalRequest(StrictModel):
    run_id: str
    proposal_id: str


class DungeonAppealResponse(StrictModel):
    run_id: str
    appeal_id: str
    response: Literal["answer", "decline"]
    text: str = Field(default="", max_length=1000)


class NpcReply(StrictModel):
    answer: str = Field(min_length=1, max_length=8000)

    @field_validator("answer")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("The NPC returned an empty answer.")
        return value.strip()


class WorldEditPlan(StrictModel):
    summary: str = Field(min_length=1, max_length=2000)
    additions: dict[str, Entity] = Field(default_factory=dict, max_length=12)
    key_bindings: dict[str, str] = Field(default_factory=dict, max_length=4)
    secondary_goals: dict[str, SecondaryGoalSpec] = Field(default_factory=dict, max_length=6)

    @model_validator(mode="after")
    def nonempty_plan(self) -> "WorldEditPlan":
        if not self.additions and not self.secondary_goals:
            raise ValueError("A proposal must add objects or verifiable side goals.")
        return self


_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "twelve": 12, "sixteen": 16,
}


def _appeal_stock_grant(world: "World", text: str, request: str) -> tuple[str, int, str] | None:
    match = re.fullmatch(
        r"\s*(?:add|provide|give|grant)\s+(?P<quantity>\d+|one|two|three|four|five|six|seven|eight|nine|ten|twelve|sixteen)"
        r"(?:\s+more)?\s+(?P<product>[a-z][a-z -]*?)\s+(?:to|at|in|into)\s+(?:the\s+)?"
        r"(?P<location>[a-z][a-z -]*?)\s*[.!]?\s*", text, re.IGNORECASE)
    if match is None:
        return None
    state = world.state.village
    if state is None:
        raise ValueError("The active village is unavailable.")
    quantity_text = match.group("quantity").lower()
    quantity = int(quantity_text) if quantity_text.isdigit() else _NUMBER_WORDS[quantity_text]
    if quantity < 1 or quantity > 100:
        raise ValueError("DM stock grants must contain between 1 and 100 units.")
    base = world.scenario.village
    products = set(base.spoil_after)
    products.update(product for stock in base.stocks.values() for product in stock)
    products.update(product for stock in state.stocks.values() for product in stock)
    if state.economy is not None and state.economy.config is not None:
        policy = state.economy.config
        products.update(policy.prices.keys())
        products.update(product for recipe in policy.recipes.values() for product in recipe.inputs)
        products.update(product for plan in policy.meal_plans for product in plan.additional_inputs)
        for configured in (policy.reserve_targets, policy.production_targets, policy.stock_capacities):
            products.update(product for stock in configured.values() for product in stock)
    product_text = "-".join(match.group("product").lower().split())
    singular = product_text[:-1] if product_text.endswith("s") else product_text
    request_terms = set(re.findall(r"[a-z]+", request.lower().replace("-", " ")))
    if product_text.replace("-", " ") in request.lower() or singular in request_terms:
        products.update((product_text, singular))
    product = product_text if product_text in products else singular if singular in products else product_text
    if product not in products:
        raise ValueError(f"Unknown village product: {product_text.replace('-', ' ')}.")
    location_text = "-".join(match.group("location").lower().replace("'", "").split())
    aliases = {
        "communal-kitchen": "tavern",
        "elsies-communal-kitchen": "tavern",
        "elsies-tavern": "tavern",
        "adas-farm": "farm",
        "nellas-general-store": "general-store",
    }
    location = aliases.get(location_text, location_text)
    if location not in state.stocks:
        raise ValueError(f"Unknown stock location: {location_text.replace('-', ' ')}.")
    return location, quantity, product


def resolve_dungeon_appeal(world: "World", appeal_id: str, response: str, text: str) -> dict[str, Any]:
    if world.state.village is None:
        raise ValueError("The active village is unavailable.")
    appeal = next((item for item in world.state.village.dungeon_appeals
                   if item["id"] == appeal_id), None)
    if appeal is None or appeal["status"] != "pending":
        raise ValueError("Choose a pending Dungeon Master appeal.")
    if appeal["expires_at"] <= world.state.village.minute:
        appeal["status"] = "timed_out"
        world.save()
        raise ValueError("This appeal has timed out.")
    if response == "answer" and not text.strip():
        raise ValueError("Enter a reply or decline the appeal.")
    grant = _appeal_stock_grant(world, text, appeal["request"]) \
        if response == "answer" and appeal["category"] == "resource" else None
    if grant is not None:
        location, quantity, product = grant
        from npc_sim import village_goods
        village_goods.put(world, location, product, quantity, kind="imported")
        appeal["world_changes"] = [{
            "kind": "stock_grant", "location": location, "product": product, "quantity": quantity,
            "inventory_location_note": "Communal kitchen stock is accounted at the tavern."
            if location == "tavern" and "communal kitchen" in text.lower() else None,
        }]
    appeal["status"] = "answered" if response == "answer" else "declined"
    appeal["response"] = text.strip() or None
    if response == "decline":
        appeal["npc_acknowledgement"] = "I understand, Dungeon Master. I will continue with the village's ordinary work."
    elif grant is not None:
        location, quantity, product = grant
        appeal["npc_acknowledgement"] = (
            f"Thank you, Dungeon Master. I see the {quantity} {product} at "
            f"{world.state.entities[location].name}, and I will put them to use.")
    else:
        appeal["npc_acknowledgement"] = "Thank you, Dungeon Master. I will follow your guidance."
    appeal["acknowledgement_expires_at"] = world.state.village.minute + 5
    person = world.state.village.residents.get(appeal["actor"])
    if person is not None:
        person.available_at = min(person.available_at, world.state.village.minute)
    world.state.revision += 1
    world.save()
    return copy.deepcopy(appeal)


def validate_edit(world: "World", plan: WorldEditPlan) -> None:
    from npc_sim import spells

    existing = world.state.entities
    future_ids = {eid for recipe in world.scenario.recipes for eid in recipe.outputs}
    if (set(existing) | future_ids).intersection(plan.additions):
        raise ValueError("A proposal cannot overwrite existing objects.")
    combined = {**existing, **plan.additions}
    for eid, entity in plan.additions.items():
        if not re.fullmatch(r"[A-Za-z0-9_-]+", eid) or eid in world.state.actors:
            raise ValueError("New object IDs must be unique simple names, not actor IDs.")
        if entity.kind not in {"location", "door", "container", "item", "wall", "lock"} or entity.break_into:
            raise ValueError("Live additions support locations, exits, containers, walls, locks and items without breakage spawns.")
        if entity.required_challenges or entity.plugged_into or entity.alternative_key_ids:
            raise ValueError("New objects cannot introduce challenge dependencies, connections or pre-bound spare keys.")
        if entity.kind == "location":
            if entity.location is not None or entity.hidden:
                raise ValueError("A new location cannot be contained or hidden.")
        elif entity.location not in combined or entity.location in world.state.actors:
            raise ValueError("Place new objects in an existing or proposed room/container, never directly in inventory.")
        elif (combined[entity.location].kind not in {"location", "container"}
              and not (entity.kind == "lock" and combined[entity.location].kind == "door")):
            raise ValueError("Only rooms and containers can contain new objects; doors can contain attached locks.")
        for ref in [entity.key_id, entity.lock_id, entity.destination, entity.discovery_target]:
            if ref is not None and ref not in combined:
                raise ValueError(f"Unknown object reference: {ref}")
        if entity.kind == "door" and (entity.destination is None
                                      or combined[entity.destination].kind != "location" or entity.open is None):
            raise ValueError("An added exit needs a location destination and an explicit open/closed state.")
        if entity.lock_id and combined[entity.lock_id].kind != "lock":
            raise ValueError("A lock reference must name a lock.")
        if entity.locked and not (entity.key_id or entity.lock_id):
            raise ValueError("A locked addition needs a matching key or attached lock.")
        if entity.key_id and not combined[entity.key_id].takable:
            raise ValueError("A key reference must name a portable object.")
        if entity.hidden != (entity.discovery_target is not None):
            raise ValueError("Hidden additions need a discovery_target; visible additions must omit it.")
        if entity.discovery_target is not None and (
                entity.discovery_target == eid or combined[entity.discovery_target].hidden):
            raise ValueError("A discovery target must be a different, non-hidden object.")

    def room(eid: str) -> str:
        seen: set[str] = set()
        while eid in combined and eid not in seen:
            seen.add(eid)
            entity = combined[eid]
            if entity.kind == "location":
                return eid
            if entity.location is None:
                break
            eid = entity.location
        raise ValueError("New object placement must lead to a room without cycles.")

    for eid, entity in plan.additions.items():
        location = room(eid)
        if entity.discovery_target and room(entity.discovery_target) != location:
            raise ValueError("A discovery target and its hidden feature must be in the same room.")
    for target, key in plan.key_bindings.items():
        if target not in existing or key not in plan.additions:
            raise ValueError("Spare keys must be new additions bound to existing locks.")
        entity = existing[target]
        if world.lock_for(entity).locked is None:
            raise ValueError("A spare key needs an ordinary keyed lock.")
        blocked = spells.unlock_block(world, target)
        if blocked:
            raise ValueError(blocked[1])
        if not plan.additions[key].takable or plan.additions[key].kind != "item":
            raise ValueError("A spare key must be a portable item.")
    secondary_goals.validate_goals(world, plan.secondary_goals, combined)


def apply_edit(world: "World", request_id: str, expected_revision: str, plan: WorldEditPlan) -> dict[str, Any]:
    with world._lock:
        identity = f"operator:{request_id}"
        request = {"revision": expected_revision, "plan": plan.model_dump(mode="json")}
        previous = world.get_request(identity)
        if previous:
            if previous["request"] != request:
                raise ValueError("An intervention ID cannot be reused for a different change.")
            return copy.deepcopy(previous["result"])
        world.tick()
        if expected_revision != world.revision():
            raise ValueError("The world changed after preview. Request a fresh proposal.")
        validate_edit(world, plan)
        before = world.state.model_copy(deep=True)
        try:
            world.state.entities.update({eid: entity.model_copy(deep=True) for eid, entity in plan.additions.items()})
            for target, key in plan.key_bindings.items():
                lock = world.lock_for(world.state.entities[target])
                if key not in lock.alternative_key_ids:
                    lock.alternative_key_ids.append(key)
            world.state.secondary_goals.update({
                gid: SecondaryGoal(**goal.model_dump(), condition_met=[False] * len(goal.conditions))
                for gid, goal in plan.secondary_goals.items()})
            world.state.revision += 1
            secondary_goals.refresh(world)
            result = {"revision": world.revision(), "added_ids": list(plan.additions),
                      "added_goal_ids": list(plan.secondary_goals)}
            return world._record(identity, request, result)
        except (OSError, ValueError):
            world.state = before
            raise


async def prepare(controller: "Controller", run_id: str, *, allow_ended: bool = False) -> None:
    from npc_sim.controller import TERMINAL

    if not controller.run or controller.run["id"] != run_id:
        raise ValueError("This interaction needs the current active adventure.")
    if allow_ended and controller.run["status"] in TERMINAL:
        return
    if not controller.world or controller.run["status"] not in {"ready", "paused", "running"}:
        raise ValueError("This interaction needs the current active adventure.")
    if controller.run["status"] == "running":
        controller.run["status"] = "paused"
        await controller._set_clock(False)
        controller._pause_budget()
        controller.emit("control", {"command": "pause", "reason": "Paused for an operator interaction."})
    controller.checkpoint()


async def model(controller: "Controller", *, followup: bool = False) -> str:
    if controller.run is None:
        raise ValueError("No current adventure.")
    if not followup and not controller.run_until_complete and controller.run["model_calls"] >= controller.budgets.max_model_calls:
        raise ValueError("The adventure's model-call budget is exhausted.")
    selected = controller.run["model"] or (controller.config.proxy.model if followup else None)
    if not selected:
        models = await controller.proxy.models()
        if len(models) != 1:
            raise ValueError("Select a model in Admin view before interacting.")
        selected = models[0]
    if followup:
        controller.run["followup_model_calls"] = controller.run.get("followup_model_calls", 0) + 1
    else:
        controller.run["model"] = selected
        controller.run["model_calls"] += 1
    return selected


def visitor_context(snapshot: dict[str, Any], actor_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    village = snapshot.get("village") or {}
    incidents = village.get("incidents") or {}
    visitor = (incidents.get("visitors") or {}).get(actor_id)
    if visitor is not None:
        instance = (incidents.get("instances") or {}).get(visitor["incident"])
        if instance is None:
            raise ValueError("The traveller's visit is unavailable.")
        goal = {"objective": instance["template"]["title"], "revealed": True}
        details = {"status": visitor["status"], "activity": visitor["activity"],
                   "coins": visitor["coins"], "stock": visitor["stock"], "plan": visitor["plan"]}
    else:
        dashboard = village.get("dashboard") or {}
        visitor = next((item for item in dashboard.get("visitors", [])
                        if item["id"] == actor_id and actor_id == "iron-trader"), None)
        if visitor is None:
            raise ValueError("Unknown traveller.")
        goal = {"objective": "Visit the village to trade", "revealed": True}
        details = {"status": visitor["status"], "activity": visitor["activities"][-1]["label"]
                   if visitor["activities"] else "Visiting", "activities": visitor["activities"][-6:]}
        stocks = village.get("stocks") or {}
        if "iron-trader" in stocks:
            details["stock"] = stocks["iron-trader"]
        if "iron-trader" in (village.get("coins") or {}):
            details["coins"] = village["coins"]["iron-trader"]
    location = visitor["location"]
    place = snapshot["entities"].get(location)
    description = place["description"] if place else "Outside the mapped village"
    return ({"self": {"actor_id": actor_id, "name": visitor["name"], "role": "traveller"},
             "location": {"id": location, "description": description},
             "village": {"minute": village["minute"], "visitor": details}}, goal)


async def handle(controller: "Controller", name: str, args: dict[str, Any]) -> None:
    request = (ChatRequest.model_validate(args) if name == "npc_chat" else
               EditRequest.model_validate(args) if name == "propose_edit" else
               DungeonAppealResponse.model_validate(args) if name == "respond_dungeon_appeal"
               else ApprovalRequest.model_validate(args))
    from npc_sim.controller import TERMINAL

    persisted_appeal = isinstance(request, DungeonAppealResponse)
    await prepare(controller, request.run_id,
                  allow_ended=isinstance(request, ChatRequest) or persisted_appeal)
    if persisted_appeal and controller.world is None:
        await controller._connect_saved_world()
    world, run = controller.world, controller.run
    if run is None:
        raise ValueError("No current adventure.")
    followup = isinstance(request, ChatRequest) and (
        run["status"] in TERMINAL or run.get("snapshot_questions", False))
    controller.in_flight = True
    active = run["actor_id"]
    try:
        if isinstance(request, ChatRequest):
            from npc_sim.contracts import observation
            saved = controller.agents.get(request.actor_id)
            snapshot = controller.status()["world"] if saved is None and (
                request.actor_id.startswith("visitor:") or request.actor_id == "iron-trader") else None
            village = (snapshot or {}).get("village") or {}
            incidents = village.get("incidents") or {}
            visitor = (incidents.get("visitors") or {}).get(request.actor_id)
            if visitor is None and request.actor_id == "iron-trader":
                dashboard = village.get("dashboard") or {}
                visitor = next((item for item in dashboard.get("visitors", [])
                                if item["id"] == request.actor_id), None)
            if visitor is not None:
                fresh, goal = visitor_context(snapshot, request.actor_id)
                if visitor["status"] == "departed" and not followup:
                    raise ValueError("This traveller has departed.")
                memory = run.setdefault("visitor_memories", {}).setdefault(request.actor_id, {
                    "schema_version": "1.0", "operator_conversation": []})
            elif followup:
                if request.actor_id not in (controller.agents or {run["actor_id"]: None}):
                    raise ValueError("Unknown NPC.")
                final = saved.get("observation") if saved else controller.observation
                if final is None:
                    raise ValueError("This NPC has no saved observation for follow-up questions.")
                fresh = observation(copy.deepcopy(final))
            else:
                if world is None:
                    raise ValueError("No active world.")
                if request.actor_id not in world.manifest.get("actor_sessions", {}):
                    raise ValueError("Unknown NPC.")
                fresh = observation(await world.select_actor(request.actor_id), world.session_id)
            if visitor is None:
                memory = saved["memory"] if saved else controller.memory
                goal = saved["goal"] if saved else run["goal"]
            controller.emit("npc_chat_request", {"question": request.question}, request.actor_id)
            question_memory = {**memory, "episode_outcome": {
                "status": run["status"], "reason": run["reason"]}} if followup else memory
            messages = controller.proxy.question_context(goal, fresh, question_memory, request.question)
            reply, metadata = await controller.proxy.answer_question(
                await model(controller, followup=followup), messages, f"{run['id']}:chat:{uuid4().hex}",
                controller.config.proxy.timeout_seconds)
            exchange = {"question": request.question, "answer": reply.answer}
            memory.setdefault("operator_conversation", []).append(exchange)
            if saved:
                saved["observation"] = fresh
            controller.emit("npc_chat", {**exchange, **metadata, "private": True}, request.actor_id)
        elif isinstance(request, DungeonAppealResponse):
            if world is None:
                raise ValueError("The active village is unavailable.")
            tool = world.manifest.get("dungeon_master_appeal_response")
            if not tool or tool not in world.tools:
                raise ValueError("This world does not support Dungeon Master appeal responses.")
            appeal = await world.call(tool, {
                "appeal_id": request.appeal_id, "response": request.response, "text": request.text})
            controller.emit("dungeon_master_appeal_resolved", appeal, appeal["actor"])
            if appeal["actor"] in controller.agents:
                memory = controller.agents[appeal["actor"]]["memory"]
                memory.setdefault("operator_conversation", []).append({
                    "question": appeal["request"],
                    "answer": appeal["response"] or "No intervention was granted. Continue ordinary work.",
                    "npc_acknowledgement": appeal["npc_acknowledgement"]})
        elif isinstance(request, EditRequest):
            if world is None:
                raise ValueError("No active world.")
            if (world.config.transport != "stdio" or world.config.command
                    or world.manifest.get("admin_intervention") not in world.tools
                    or world.manifest.get("admin_validate_intervention") not in world.tools):
                raise ValueError("Live world editing requires the built-in local world adapter.")
            fresh = await world.operation("observe", {})
            snapshot = controller.status()["world"]
            if snapshot is None:
                raise ValueError("The live world snapshot is unavailable.")
            snapshot["actor_names"] = {
                actor_id: profile["name"] for actor_id, profile in run.get("agent_profiles", {}).items()}
            messages = controller.proxy.world_edit_context(request.instruction, snapshot)
            plan, metadata = await controller.proxy.propose_world_edit(
                await model(controller), messages, f"{run['id']}:proposal:{uuid4().hex}",
                controller.config.proxy.timeout_seconds)
            await world.call(world.manifest["admin_validate_intervention"], {
                "expected_revision": fresh["observation_revision"], "plan": plan.model_dump(mode="json")})
            proposal_id = uuid4().hex
            proposal = {"id": proposal_id, "revision": fresh["observation_revision"], "instruction": request.instruction,
                        "plan": plan.model_dump(mode="json"), "status": "pending"}
            run.setdefault("world_edit_proposals", {})[proposal_id] = proposal
            controller.emit("world_edit_proposed", {**proposal, **metadata})
        else:
            if world is None:
                raise ValueError("No active world.")
            proposal = run.get("world_edit_proposals", {}).get(request.proposal_id)
            if not proposal or proposal["status"] != "pending":
                raise ValueError("Choose a pending proposal from this adventure.")
            if (world.config.transport != "stdio" or world.config.command
                    or world.manifest.get("admin_intervention") not in world.tools):
                raise ValueError("Live world editing requires the built-in local world adapter.")
            applied = await world.call(world.manifest["admin_intervention"], {
                "request_id": request.proposal_id, "expected_revision": proposal["revision"], "plan": proposal["plan"]})
            proposal["status"] = "applied"
            controller.emit("world_edit_applied", {"proposal_id": request.proposal_id, "summary": proposal["plan"]["summary"],
                                                  **applied})
            controller.patterns, controller.replan_count = [], 0
            for saved in controller.agents.values():
                saved["patterns"], saved["replan_count"] = [], 0
    except Exception as error:
        controller.emit("operator_interaction_error", {"operation": name, "message": controller.safe_error(error)})
        raise
    finally:
        from npc_sim.contracts import observation
        try:
            if not followup and world is not None and name == "approve_edit":
                for actor_id, saved in controller.agents.items():
                    saved["observation"] = observation(await world.select_actor(actor_id), world.session_id)
            if not followup and world is not None:
                controller.observation = observation(await world.select_actor(active), world.session_id)
        finally:
            controller.in_flight = False
            controller.checkpoint()
