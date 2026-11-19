"""Read-only supply planning from a saved village revision."""

from collections import defaultdict
from typing import Any

from npc_sim.models import VillageCommittedWork, VillageConfig, WorldState
from npc_sim.village_economy import ACTIVE, COMMITTED
from npc_sim.village_livestock import SHELF
from npc_sim.village_replenishment import forecast_rows


Lot = tuple[int, int | None]
STARTED, HARVEST, PLANNED_PRODUCTION, DELIVERY = range(4)


def _take(lots: list[Lot], quantity: int, fresh_until: int) -> list[Lot]:
    """Allocate first-expiring eligible units and preserve expiry through delivery."""
    taken = []
    for index, (amount, expiry) in sorted(enumerate(lots), key=lambda pair: (
            pair[1][1] if pair[1][1] is not None else float("inf"))):
        if not quantity:
            break
        if amount and (expiry is None or expiry > fresh_until):
            moved = min(quantity, amount)
            lots[index] = (amount - moved, expiry)
            taken.append((moved, expiry))
            quantity -= moved
    return taken


def _available(lots: list[tuple[int, int | None]], minute: int) -> int:
    return sum(quantity for quantity, expiry in lots if expiry is None or expiry > minute)


def report(world: WorldState, config: VillageConfig | None) -> dict[str, Any] | None:
    state = world.village
    if state is None:
        return None
    now = state.minute
    horizons = [now + hours * 60 for hours in (6, 24, 48)]
    economy = state.economy
    policy = economy.config if economy else None
    reserves = policy.reserve_targets if policy else {}
    targets = policy.production_targets if policy else {}
    lots: dict[tuple[str, str], list[tuple[int, int | None]]] = defaultdict(list)
    stock = {(site, good): amount for site, goods in state.stocks.items() for good, amount in goods.items()}
    for (site, good), amount in stock.items():
        saved = state.stock_lots.get(site, {}).get(good)
        lots[site, good] = ([(lot.quantity, lot.expires_at) for lot in saved] if saved is not None
                            else [(amount, None)])
    for mapping in (reserves, targets):
        for site, goods in mapping.items():
            for good in goods:
                lots[site, good]
    shelf = {**(SHELF if state.livestock else {}), **(config.spoil_after if config else {})}
    if state.research:
        for project in state.research.projects.values():
            if project.product and project.discovered_at is not None and project.proposal.kind in {"food", "crop"}:
                shelf.setdefault(project.product, 1440)

    cargo = defaultdict(list)
    cargo_rows = []
    carriers = [(rid, person.shipments) for rid, person in state.residents.items()]
    if state.wagon:
        carriers.append(("wagon", state.wagon.loads))
    for carrier, shipments in carriers:
        for shipment in shipments:
            cargo_rows.append({"carrier": carrier, "product": shipment.resource, "quantity": shipment.quantity,
                               "destination": shipment.destination, "order_id": shipment.order_id})
            if shipment.order_id:
                cargo[shipment.order_id].extend((lot.quantity, lot.expires_at) for lot in shipment.lots)
                if not shipment.lots and config and not config.expanded_rules:
                    cargo[shipment.order_id].append((shipment.quantity, None))

    orders = sorted(economy.orders.values(), key=lambda order: (order.deadline, order.id)) if economy else []
    production = []
    events: list[tuple[int, int, str, Any]] = []
    risks = []
    active_outputs = defaultdict(set)
    for rid, person in state.residents.items():
        work = person.life.work if person.life else None
        if not work or work.kind != "production":
            continue
        active_outputs[work.location].update(work.outputs)
        equipment = world.entities.get(work.equipment) if work.equipment else None
        worker = world.actors.get(rid)
        viable = not ((equipment and equipment.broken) or (worker and worker.location_id != work.location))
        production.append({"id": f"batch:{rid}", "worker": rid, "location": work.location,
                           "outputs": work.outputs, "inputs": {}, "gathered": {}, "ingredients": work.ingredients,
                           "finish": work.finishes_at, "status": "in_progress",
                           "basis": "Inputs already consumed" if viable else "Worker or equipment unavailable; not forecast"})
        if work.finishes_at >= now and viable:
            events.append((work.finishes_at, STARTED, rid, work))
            for good in work.outputs:
                lots[work.location, good]

    if state.livestock and state.livestock.config.enabled:
        for chunk in state.livestock.chunks:
            if chunk.status != "active" or chunk.operation not in {"milk", "eggs"} or chunk.end < now:
                continue
            worker = world.actors.get(chunk.actor)
            person = state.residents.get(chunk.actor)
            animal_ids = ["farm-cow"] if chunk.operation == "milk" else [
                f"farm-hen-{index:02d}" for index in range(1, state.livestock.config.hens + 1)]
            if (not worker or worker.location_id != chunk.location or not person
                    or (person.life and person.life.sleeping) or chunk.start // 1440 != chunk.end // 1440
                    or any((animal := world.entities.get(key)) is None or animal.kind != "creature"
                           or animal.hidden or animal.broken or animal.location != chunk.location
                           for key in animal_ids)):
                continue
            if any(
                (tool := world.entities.get(key)) is None or tool.hidden or tool.broken
                or (tool.location != chunk.location and key not in worker.inventory)
                for key in chunk.equipment
            ):
                continue
            outputs = {chunk.operation: chunk.quantity}
            active_outputs[chunk.location].update(outputs)
            production.append({"id": f"collection:{chunk.actor}:{chunk.start}", "worker": chunk.actor,
                               "location": chunk.location, "outputs": outputs, "inputs": {}, "gathered": {},
                               "ingredients": [], "finish": chunk.end, "status": "in_progress",
                               "basis": "Collection underway; yield depends on continued care and demand"})
            events.append((chunk.end, STARTED, chunk.actor, VillageCommittedWork(
                location=chunk.location, finishes_at=chunk.end, outputs=outputs)))
            lots[chunk.location, chunk.operation]

    for order in orders:
        plan = order.production_plan
        if order.status in COMMITTED and plan and plan.get("output", 0) > 0:
            finish = plan.get("finish", now)
            superseded = order.status == "in_production" or order.product in active_outputs.get(order.source, ())
            resident = config.residents.get(order.supplier) if config else None
            worker = world.actors.get(order.supplier)
            lacks_skills = bool(resident and worker and set(resident.skills) - set(worker.body.capabilities))
            row = {"id": order.id, "worker": order.supplier, "location": order.source,
                   "outputs": {order.product: plan["output"]}, "ingredients": [],
                   "inputs": {key[6:]: qty for key, qty in plan.items() if key.startswith("input:") and qty > 0},
                   "gathered": {key[7:]: qty for key, qty in plan.items() if key.startswith("gather:") and qty > 0},
                   "finish": finish, "status": order.status,
                   "driver": "harvest" if plan.get("session") and order.product in {"milk", "eggs"}
                   else "planned_production",
                   "basis": ("Overlapping work: only started batches are forecast" if superseded else
                             "Past planned finish; not forecast" if finish < now else
                             "Worker lacks required occupation skills; not forecast" if lacks_skills else
                             "Requires livestock care" if plan.get("requires_care") else "Recorded plan")}
            production.append(row)
            if finish >= now and not superseded and not lacks_skills and not plan.get("requires_care"):
                events.append((finish, PLANNED_PRODUCTION, order.id, row))
                lots[order.source, order.product]
                for good in row["inputs"]:
                    lots[order.source, good]
        if order.status in COMMITTED and order.remaining and order.deadline >= now:
            events.append((order.deadline, DELIVERY, order.id, order))
            lots[order.source, order.product]
            lots[order.destination, order.product]

    replenishment = forecast_rows(world, config)
    for task in replenishment:
        site, product = task["location"], task["product"]
        lots[site, product]
        if task["status"] != "planned" or task["finish"] is None or task["finish"] < now:
            continue
        if task.get("kind") == "production":
            row = {**task, "outputs": {product: task["quantity"]}, "gathered": {}}
            events.append((task["finish"], PLANNED_PRODUCTION, task["id"], row))
            for good in row["inputs"]:
                lots[site, good]
        else:
            events.append((task["finish"], HARVEST, task["id"], task))

    usable = {key: _available(buckets, now) for key, buckets in lots.items()}
    baseline, _, _ = _project(lots, cargo, events, horizons, shelf, reserves, usable, include_plans=False)
    projected, changes, plan_risks = _project(lots, cargo, events, horizons, shelf, reserves, usable, include_plans=True)
    risks.extend(plan_risks)
    blocked = {risk["id"] for risk in plan_risks if risk["kind"] == "production input"}
    for task in replenishment:
        if task["id"] in blocked:
            task.update(status="blocked", quantity=0, finish=None,
                        basis="Not enough unreserved inputs at the planned time")

    inventory = [{"location": site, "product": good, "on_hand": stock.get((site, good), 0),
                  "usable": usable[site, good], "reserve": reserves.get(site, {}).get(good, 0),
                  "target": targets.get(site, {}).get(good), "projected": projected[site, good],
                  "baseline_projected": baseline[site, good], "forecast_changes": changes[site, good]}
                 for site, good in sorted(projected)]
    return _details(world, config, horizons, inventory, orders, cargo_rows, production, replenishment, risks)


def _project(initial_lots, initial_cargo, events, horizons, shelf, reserves, usable, *, include_plans):
    lots = {key: buckets.copy() for key, buckets in initial_lots.items()}
    cargo = defaultdict(list, {key: buckets.copy() for key, buckets in initial_cargo.items()})
    projected = {key: [] for key in lots}
    changes = {key: [] for key in lots}
    previous = usable.copy()
    risks = []

    def put(site: str, good: str, qty: int, minute: int) -> None:
        lifetime = shelf.get(good)
        lots[site, good].append((qty, minute + lifetime if lifetime else None))

    pending = iter(sorted((event for event in events if include_plans or event[1] in (STARTED, DELIVERY)),
                          key=lambda event: event[:3]))
    event = next(pending, None)
    for horizon in horizons:
        movement: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: defaultdict(int))
        while event is not None and event[0] <= horizon:
            minute, kind, event_id, item = event
            if kind == STARTED:
                for good, quantity in item.outputs.items():
                    put(item.location, good, quantity, minute)
                    movement[item.location, good]["production"] += quantity
            elif kind == HARVEST:
                site, good = item["location"], item["product"]
                put(site, good, item["quantity"], minute)
                movement[site, good]["harvest"] += item["quantity"]
            elif kind == PLANNED_PRODUCTION:
                site = item["location"]
                inputs = {good: max(0, quantity - item["gathered"].get(good, 0))
                          for good, quantity in item["inputs"].items()}
                shortages = {good: quantity - max(0, _available(lots[site, good], minute)
                                                  - reserves.get(site, {}).get(good, 0))
                             for good, quantity in inputs.items()}
                for good, missing in shortages.items():
                    if missing > 0:
                        risks.append({"id": event_id, "kind": "production input", "minute": minute,
                                      "location": site, "product": good, "missing": missing})
                if not any(missing > 0 for missing in shortages.values()):
                    for good, quantity in inputs.items():
                        taken = _take(lots[site, good], quantity, minute)
                        movement[site, good]["inputs"] += sum(amount for amount, _ in taken)
                    for good, quantity in item["outputs"].items():
                        put(site, good, quantity, minute)
                        movement[site, good][item.get("driver", "planned_production")] += quantity
            else:
                cutoff = max(minute, item.required_fresh_until)
                incoming = _take(cargo[event_id], min(item.in_transit, item.remaining), cutoff)
                source = lots[item.source, item.product]
                surplus = max(0, _available(source, minute) - reserves.get(item.source, {}).get(item.product, 0))
                loaded = _take(source, min(item.loadable, surplus), cutoff)
                movement[item.source, item.product]["outbound"] += sum(quantity for quantity, _ in loaded)
                incoming += loaded
                moved = sum(quantity for quantity, _ in incoming)
                lots[item.destination, item.product].extend(incoming)
                movement[item.destination, item.product]["inbound"] += moved
                if moved < item.remaining:
                    risks.append({"id": event_id, "kind": "delivery", "minute": minute,
                                  "location": item.destination, "product": item.product, "missing": item.remaining - moved})
            event = next(pending, None)
        for key, checkpoints in projected.items():
            value = _available(lots[key], horizon)
            delta = movement[key]
            # Whatever left usable stock without being allocated expired during this interval.
            gains = delta["production"] + delta["inbound"] + delta.get("harvest", 0) + delta.get("planned_production", 0)
            expired = previous[key] + gains - delta["inputs"] - delta["outbound"] - value
            changes[key].append({**delta, "expired": expired})
            checkpoints.append(value)
            previous[key] = value

    return projected, changes, risks


def _details(world, config, horizons, inventory, orders, cargo_rows, production, replenishment, risks):
    state = world.village
    assert state is not None
    communication = state.communication
    requests = []
    notes = []
    if communication:
        for fixture, papers in communication.placements.items():
            entity = world.entities.get(fixture)
            if entity is None or entity.location is None:
                continue
            for notice_id, version in papers.items():
                history = communication.notices.get(notice_id, [])
                if not 0 < version <= len(history):
                    continue
                paper, latest = history[version - 1], history[-1]
                notes.append({"id": notice_id, "fixture": fixture, "location": entity.location,
                              "author": paper.author, "title": paper.terms.subject, "text": paper.terms.description,
                              "minute": paper.updated_at, "version": version, "status": paper.status,
                              "latest_version": latest.version, "latest_status": latest.status})
        for history in communication.notices.values():
            if history and history[-1].terms.type in {"supply_request", "work_request"}:
                notice = history[-1]
                requests.append({"id": notice.id, "kind": notice.terms.type, "owner": notice.author,
                                 "subject": notice.terms.subject, "quantity": notice.terms.quantity,
                                 "destination": notice.terms.destination, "status": notice.status,
                                 "due": notice.terms.due_at, "order_id": notice.linked_order_id,
                                 "detail": notice.response or notice.terms.description})
    needs = [{"id": key, **need.model_dump()} for person in state.residents.values()
             for key, need in person.fallback.supply_needs.items()]
    board = communication.config.board_id if communication else "noticeboard"
    for event in state.notices:
        fixture = world.entities.get(board)
        notes.append({"id": None, "fixture": board, "location": (fixture.location if fixture else None),
                      "author": event.actor, "title": "Public bell notice", "text": event.result,
                      "minute": event.minute, "status": None, "version": None,
                      "latest_version": None, "latest_status": None})
    for key, entity in world.entities.items():
        if not entity.hidden and not entity.takable and entity.readable_text and entity.location in world.entities:
            if world.entities[entity.location].kind == "location":
                notes.append({"id": key, "fixture": key, "location": entity.location, "author": None,
                              "title": entity.name, "text": entity.readable_text, "minute": None, "status": None,
                              "version": None, "latest_version": None, "latest_status": None})

    projects = []
    if config:
        for key, title, site, complete in (("dig_well", "Dig the well", config.well_site, state.well_dug),
                                            ("repair_mill", "Repair the mill", config.mill, state.repaired)):
            tasks = [task for task in state.tasks.values() if task.kind == key]
            progress = sum(state.project_contributions.get(key, {}).values())
            session = state.crew_sessions.get(key)
            deadline = state.deadlines.get(key)
            blocker = next((task.blocker for task in tasks if task.status == "blocked" and task.blocker), None)
            status = ("completed" if complete else "blocked" if any(task.status == "blocked" for task in tasks)
                      else "in_progress" if progress or (session and session.status == "active")
                      or any(task.status == "in_progress" for task in tasks)
                      else "upcoming" if key == "repair_mill" and not state.breakdown else "pending")
            projects.append({"id": key, "title": title, "location": site, "status": status,
                             "due": deadline.due if deadline else None, "progress": progress if config.expanded_rules else None,
                             "blocker": blocker})
    wagon_entity = world.entities.get("wagon")
    if state.wagon and wagon_entity and wagon_entity.location:
        wagon_repair = next((person.life.work for person in state.residents.values()
                             if person.life and person.life.work and person.life.work.kind == "wagon_repair"), None)
        projects.append({"id": "repair_wagon", "title": "Repair the wagon", "location": wagon_entity.location,
                         "status": "completed" if state.wagon.repaired else "in_progress" if wagon_repair else "pending",
                         "due": None, "progress": None, "blocker": None})
    dashboard = state.dashboard
    maintenance = [job.model_dump() for job in dashboard.maintenance] if dashboard else []
    upcoming_maintenance = []
    upkeep = state.maintenance
    maintenance_config = upkeep.config if upkeep else config.maintenance if config else None
    if maintenance_config and maintenance_config.enabled:
        tomorrow = state.minute // 1440 + 1
        for template, definition in sorted(maintenance_config.tasks.items()):
            previous = [job for job in upkeep.jobs.values() if job.template == template] if upkeep else []
            if any(job.day == tomorrow for job in previous):
                continue
            unfinished = next((job for job in previous if job.completed_at is None), None)
            if unfinished is not None:
                continue
            upcoming_maintenance.append({
                "id": f"preview:maintenance:{tomorrow}:{template}", "title": definition.title,
                "assignee": definition.assignee, "location": definition.location, "day": tomorrow + 1,
                "release_at": tomorrow * 1440 + definition.release_minute,
                "due_at": tomorrow * 1440 + definition.due_minute, "worked_minutes": 0,
                "required_minutes": definition.duration, "supplies": definition.supplies,
                "status": "upcoming", "completed_at": None, "blocker": None,
            })
    return {"minute": state.minute, "revision": world.revision, "horizons": horizons, "inventory": inventory,
            "orders": [{**order.model_dump(), "remaining": order.remaining, "open": order.status in ACTIVE}
                       for order in orders],
            "requests": requests, "needs": needs, "cargo": cargo_rows, "production": production,
            "replenishment": replenishment,
            "risks": risks, "maintenance": maintenance, "upcoming_maintenance": upcoming_maintenance,
            "projects": projects, "notes": notes,
            "sites": {key: entity.name for key, entity in world.entities.items() if entity.kind == "location"},
            "fixtures": {note["fixture"]: world.entities[note["fixture"]].name for note in notes
                         if note["fixture"] in world.entities},
            "residents": {**{key: world.entities[key].name if key in world.entities else key.replace("-", " ").title()
                             for key in state.residents}, **(state.legacy.current if state.legacy else {})}}
