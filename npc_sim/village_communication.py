"""Physical notices, local replies, and bounded, non-live personal knowledge.

Placements reference an immutable version, not the current authoritative record.
Verified work changes the latter, never silently edits a physical piece of paper.
"""

from typing import Any, TYPE_CHECKING

from pydantic import ValidationError

from npc_sim import village, village_economy
from npc_sim.village_communication_models import (
    Appointment, Channel, ChangeNotice, CloseNotice, NoticeKnowledge, NoticeTerms,
    NoticeVersion, PostNotice, ReadInbox, ReadNotices, ReplyNotice, TellNotice,
    UpdateNotice, VillageCommunicationConfig, VillageCommunicationState,
)

if TYPE_CHECKING:
    from npc_sim.world import World

VERBS = {"village_notice_post", "village_notice_read", "village_notice_update",
         "village_notice_reply", "village_notice_close", "village_notice_tell",
         "village_inbox_read", "village_inbox_leave"}
TERMINAL = {"completed", "rejected", "cancelled", "expired", "superseded"}
GUIDANCE = (
    "Knowledge is a received snapshot, not current world truth. Check the physical square board "
    "while passing, or your own workplace box on return; writing/reading requires being there. "
    "Use one notice per unmet need, then continue useful work while awaiting replies. "
    "A board or physical inbox reply can commit funded supply without chasing the buyer. "
    "Acceptance is not delivery: use real production, cargo, unloading and payment. "
    "Share at most the listed relevant candidates with an awake available person actually here; "
    "never travel solely for routine gossip. Older news may be out of date. "
    "Appointments are personal plans, not automatic travel: confirm through a real channel, "
    "arrive at the due site with actual tools and supplies, and each worker must act."
)


def enabled(world: "World") -> bool:
    return world.state.village is not None and world.state.village.communication is not None


def _state(world: "World") -> VillageCommunicationState:
    state = world.state.village.communication
    if state is None:
        raise ValueError("Communication is not enabled")
    return state


def fixture_specs(scenario: Any) -> dict[str, dict[str, Any]]:
    return {f"workplace-inbox-{rid}": {
        "name": f"{scenario.entities[rid].name if rid in scenario.entities else rid.title()}'s workplace inbox",
        "kind": "object", "location": resident.work,
        "description": "A fixed physical letter box. Only its owner may read; leave notes here in person.",
    } for rid, resident in scenario.village.residents.items()}


def validate_config(scenario: Any) -> None:
    policy = scenario.village.communication
    if policy is None:
        return
    if not scenario.village.ai_controlled or not scenario.village.time_based_turns:
        raise ValueError("Communication requires individually timed AI residents")
    board = scenario.entities.get(policy.board_id)
    if board is None or policy.board_id != "noticeboard" or board.location != "village-square":
        raise ValueError("Communication reuses the physical village-square noticeboard")
    if policy.workplace_boxes:
        for key, spec in fixture_specs(scenario).items():
            fixture = scenario.entities.get(key)
            if fixture is None or fixture.location != spec["location"] or fixture.takable or fixture.hidden:
                raise ValueError("Every configured owner needs its actual fixed workplace box")


def initial_state(scenario: Any) -> VillageCommunicationState | None:
    policy = scenario.village.communication
    if policy is None:
        return None
    return VillageCommunicationState(
        config=policy.model_copy(deep=True),
        boxes={key: key.removeprefix("workplace-inbox-") for key in fixture_specs(scenario)}
        if policy.workplace_boxes else {})


def install_scenario(scenario: Any) -> None:
    from npc_sim.models import Entity
    scenario.village.communication = VillageCommunicationConfig()
    scenario.entities.update({key: Entity.model_validate(spec)
                              for key, spec in fixture_specs(scenario).items()})


def configure(world: "World", values: dict[str, Any]) -> None:
    from npc_sim.models import Entity
    policy = VillageCommunicationConfig.model_validate(values)
    candidate = world.scenario.model_copy(deep=True)
    candidate.village.communication = policy
    if policy.workplace_boxes:
        candidate.entities.update({key: Entity.model_validate(spec)
                                   for key, spec in fixture_specs(candidate).items()})
    validate_config(candidate)
    if enabled(world):
        if _state(world).config != policy:
            raise ValueError("An enabled communication ledger cannot change physical channels")
        return
    boxes = fixture_specs(world.scenario) if policy.workplace_boxes else {}
    for key, spec in boxes.items():
        existing = world.state.entities.get(key)
        if existing is not None and existing != Entity.model_validate(spec):
            raise ValueError(f"Fixture collision: {key}")
    world.state.entities.update({key: Entity.model_validate(spec) for key, spec in boxes.items()})
    world.state.village.communication = VillageCommunicationState(
        config=policy, boxes={key: key.removeprefix("workplace-inbox-") for key in boxes})


def _ready(world: "World", actor: str) -> None:
    village_state = world.state.village
    if (village_state.residents[actor].timber_route
            or (village_state.wagon and village_state.wagon.automatic and village_state.wagon.driver == actor)):
        raise ValueError("A resident travelling or working an automatic route is unavailable to talk")
    blocked = village_economy._speaker_blocker(world, actor)
    if blocked:
        raise ValueError(blocked[1])
    from npc_sim import village_extensions
    if village_extensions.blocked_until(world, actor) is not None:
        raise ValueError("That resident is occupied with a real task")


def _local_listener(world: "World", listener: str) -> None:
    if (listener == world.actor_id or listener not in world.state.village.residents
            or listener not in village.local_residents(world, world.actor.location_id)):
        raise ValueError("Speak only to a different resident actually here")
    _ready(world, listener)


def _channel(world: "World", request: Channel, terms: NoticeTerms,
             author: str, *, replying: bool = False) -> str | None:
    state = _state(world)
    if request.channel == "board":
        if request.inbox_id is not None or request.listener is not None or terms.private:
            raise ValueError("Private/restricted notes cannot be placed on the public board")
        fixture = state.config.board_id
    elif request.channel == "inbox":
        if request.listener is not None or request.inbox_id not in state.boxes:
            raise ValueError("Choose a real workplace inbox")
        fixture = request.inbox_id
        owner = state.boxes[fixture]
        audience = {author} if replying else set(terms.recipients)
        if owner not in audience:
            raise ValueError("Leave a request at its recipient's box, or a reply at its author's box")
    else:
        if request.inbox_id is not None or request.listener is None:
            raise ValueError("A conversation needs an actual listener")
        _local_listener(world, request.listener)
        if replying and request.listener != author:
            raise ValueError("A conversational response must actually reach the requester")
        if not replying and terms.recipients and request.listener not in terms.recipients:
            raise ValueError("Address this conversation to an intended recipient")
        if terms.private and request.listener not in {author, *terms.recipients}:
            raise ValueError("The listener is outside this note's restricted audience")
        return None
    entity = world.state.entities.get(fixture)
    if (entity is None or entity.hidden or entity.broken
            or entity.location != world.actor.location_id):
        raise ValueError("Go to the actual physical board or box first")
    return fixture


def _validate_terms(world: "World", terms: NoticeTerms, author: str) -> None:
    base, state = village.context(world)
    if len(set(terms.recipients)) != len(terms.recipients) or any(
            rid not in base.residents or rid == author for rid in terms.recipients):
        raise ValueError("Recipients must be distinct other real residents")
    if terms.private and not terms.recipients:
        raise ValueError("Private messages require an explicit audience")
    if terms.destination is not None and (
            terms.destination not in world.state.entities
            or world.state.entities[terms.destination].kind != "location"):
        raise ValueError("Choose an actual destination place")
    if any(value is not None and value <= state.minute for value in (terms.due_at, terms.expires_at)):
        raise ValueError("Deadlines must still be in the future")
    if terms.type == "supply_request":
        from npc_sim import village_research
        if state.economy is None or len(terms.recipients) != 1:
            raise ValueError("A supply request requires economy and one named supplier")
        if (terms.subject not in village_research.prices(world) or terms.quantity is None
                or terms.destination != base.residents[author].work or terms.due_at is None
                or terms.required_fresh_until is None or terms.required_fresh_until < terms.due_at):
            raise ValueError("Supply terms need priced goods, quantity, buyer workplace, deadline and freshness")
        if (terms.unit_price != village_economy.unit_price(world, terms.subject)
                or terms.delivery_fee != village_economy.delivery_fee(world)):
            raise ValueError("The published price/fee no longer matches policy; the author must publish new terms")
        cost = terms.quantity * (village_economy.unit_price(world, terms.subject)
                                 + village_economy.delivery_fee(world))
        if state.coins[author] - village_economy.commitments(world, author) < cost:
            raise ValueError("The buyer cannot fund these goods and reserved delivery fees")
    if terms.type == "meeting":
        if terms.project is None or terms.due_at is None:
            raise ValueError("A project appointment needs a project and due time")
        crew, site = _project(world, terms.project)
        if author not in crew or set(terms.recipients) != set(crew) - {author} or terms.destination != site:
            raise ValueError("The actual project crew must propose its actual work-site appointment")
    elif terms.project is not None:
        raise ValueError("Only a meeting may name a cooperative project")


def _priced_terms(world: "World", terms: NoticeTerms) -> NoticeTerms:
    if terms.type != "supply_request":
        return terms
    return terms.model_copy(update={
        "unit_price": terms.unit_price if terms.unit_price is not None else village_economy.unit_price(world, terms.subject),
        "delivery_fee": terms.delivery_fee if terms.delivery_fee is not None else village_economy.delivery_fee(world)})


def _project(world: "World", project: str) -> tuple[list[str], str]:
    base = world.scenario.village
    return ([base.smith, base.helper], base.well_site) if project == "dig_well" else (
        [base.carpenter, base.miller], base.mill)


def _emit(world: "World", action: str, notice: NoticeVersion, *, listeners: list[str] | None = None,
          text: str | None = None, include_actor: bool = True) -> None:
    from npc_sim.models import VillageEvent
    state = world.state.village
    witnesses = list(dict.fromkeys(([world.actor_id] if include_actor else []) + (listeners or [])))
    index = len(state.events)
    state.events.append(VillageEvent(
        minute=state.minute, actor=world.actor_id, action=action, target=notice.id,
        result=text or f"{notice.terms.subject}: {notice.status} (version {notice.version}).",
        witnesses=witnesses))
    for rid in witnesses:
        state.residents[rid].memories.append(index)


def _receive(world: "World", actor: str, notice: NoticeVersion, source: str,
             speaker: str | None = None, previous: NoticeKnowledge | None = None) -> bool:
    state = _state(world)
    memories = state.knowledge.setdefault(actor, {})
    old = memories.get(notice.id)
    if old and old.snapshot.version > notice.version:
        return False
    if old and old.snapshot.version == notice.version and (
            previous is not None or old.confidence == "direct"):
        return False
    chain = [source] if previous is None else [
        previous.source_chain[0], *previous.source_chain[1:], speaker]
    memories[notice.id] = NoticeKnowledge(
        snapshot=notice.model_copy(deep=True), heard_at=world.state.village.minute,
        source_type=source, source_actor=speaker,
        confidence="secondhand" if previous else "direct",
        source_chain=[chain[0], *chain[-3:]] if len(chain) > 4 else chain,
        hops=previous.hops + 1 if previous else 0,
        last_verified_at=previous.last_verified_at if previous else world.state.village.minute)
    return True


def _publish(world: "World", notice: NoticeVersion, request: Channel, fixture: str | None) -> None:
    state = _state(world)
    if fixture:
        state.placements.setdefault(fixture, {})[notice.id] = notice.version
    else:
        _receive(world, request.listener, notice, "conversation", world.actor_id)
        world.state.village.residents[request.listener].available_at = world.state.village.minute + 5
    _receive(world, world.actor_id, notice, "author")


def _new_version(world: "World", notice: NoticeVersion, **changes: Any) -> NoticeVersion:
    value = notice.model_copy(deep=True, update={
        "version": notice.version + 1, "updated_at": world.state.village.minute, **changes})
    _state(world).notices[notice.id].append(value)
    return value


def _need_key(author: str, terms: NoticeTerms) -> tuple:
    return author, terms.type, terms.subject, terms.destination, terms.project


def _known_current(world: "World", request: ChangeNotice, *, allow_closed: bool = False) -> NoticeVersion:
    state = _state(world)
    knowledge = state.knowledge.get(world.actor_id, {}).get(request.notice_id)
    history = state.notices.get(request.notice_id)
    if (not history or knowledge is None or knowledge.snapshot.version != request.expected_version
            or history[-1].version != request.expected_version):
        raise ValueError("A compatible personally received version is required; check through a real channel")
    notice = history[-1]
    if not allow_closed and (notice.status in TERMINAL or _overdue(world, notice)):
        raise ValueError("This request is closed or its deadline has passed")
    return notice


def _overdue(world: "World", notice: NoticeVersion) -> bool:
    return any(value is not None and world.state.village.minute > value
               for value in (notice.terms.due_at, notice.terms.expires_at))


def _create_order(world: "World", notice: NoticeVersion) -> dict[str, Any]:
    """Create only after all checks; this reuses the real economy/cargo ledger."""
    from npc_sim.village_economy_models import VillageSupplierOrder
    base, village_state = village.context(world)
    state = village_state.economy
    terms = notice.terms
    supplier = world.actor_id
    if supplier not in terms.recipients:
        raise ValueError("Only the named supplier can accept")
    _validate_terms(world, terms, notice.author)
    source = base.residents[supplier].work
    approach = village.route(world, world.actor.location_id, source)
    delivery = village.route(world, source, terms.destination)
    if approach is None or delivery is None:
        raise ValueError("There is no physical route for this supply")
    from npc_sim import village_services
    capacity = village_services.foot_capacity(world, supplier)
    if capacity < 1:
        raise ValueError("The supplier needs real free carrying capacity before accepting a delivery")
    trips = (terms.quantity + capacity - 1) // capacity
    transport_minutes = len(approach) * 5 + trips * (len(delivery) * 5 + 10) + (
        trips - 1) * len(delivery) * 5
    if village_state.minute + transport_minutes > terms.due_at:
        raise ValueError("The deadline leaves no time for real collection, travel and unloading")
    job = base.residents[supplier].job
    produces = (job is not None and terms.subject in job.outputs) or (
        village_economy.config(world).suppliers.get(terms.subject) == supplier)
    stock = village_economy.usable_stock(world, source, terms.subject, terms.required_fresh_until)
    protected = village_economy.protected_quantity(world, source, terms.subject)
    potential = 0
    work_end = min(terms.due_at - transport_minutes, village_state.minute // 1440 * 1440 + 1170,
                   village_state.minute // 1440 * 1440 + base.residents[supplier].bed_minute)
    if produces and job and terms.subject in job.outputs:
        from npc_sim import village_life, village_research
        interval = max(5, job.interval, job.duration) * village_life.cooldown_factor(world, supplier)
        start = max(village_state.minute, village_state.residents[supplier].next_work)
        batches = max(0, (work_end - start) // interval)
        for resource, amount in job.inputs.items():
            available = village_economy.usable_stock(world, source, resource, terms.due_at)
            reserved = village_economy.protected_quantity(world, source, resource)
            batches = min(batches, max(0, available - reserved) // amount)
        if job.equipment and world.state.entities[job.equipment].broken:
            batches = 0
        shelf = village_research.shelf_life(world, terms.subject)
        if shelf is not None and work_end + shelf <= terms.required_fresh_until:
            batches = 0
        potential = batches * job.outputs[terms.subject]
    from npc_sim import village_firewood
    if terms.subject == "firewood" and village_firewood.enabled(world):
        potential = max(potential, village_firewood.production_potential(world, work_end))
    from npc_sim import village_services, village_life
    gathering = next(((skill, tool) for skill, product, tool in village_services.JOBS.values()
                      if product == terms.subject), None)
    if gathering and gathering[0] in base.residents[supplier].skills:
        tool = world.state.entities.get(gathering[1]) if gathering[1] else None
        if gathering[1] is None or (tool is not None and not tool.broken and not tool.hidden):
            interval = max(5, job.interval if job else 30) * village_life.cooldown_factor(world, supplier)
            potential = max(potential, max(0, work_end - village_state.minute) // interval * 2)
    livestock = village_state.livestock
    if (livestock and livestock.config.enabled and supplier == livestock.config.keeper
            and terms.subject in {"milk", "eggs"}):
        from npc_sim import village_livestock
        operation = "milk" if terms.subject == "milk" else "eggs"
        care = livestock.cow if operation == "milk" else livestock.hens
        duration = (15 if operation == "milk" else 10) * village_life.cooldown_factor(world, supplier)
        clock = village_state.minute % 1440
        session = "morning" if clock < 780 else "afternoon"
        tool = world.state.entities.get(village_livestock.PAIL if operation == "milk" else village_livestock.BASKET)
        prior = next((chunk for chunk in reversed(livestock.chunks) if chunk.operation == "milk"
                      and chunk.start // 1440 == village_state.minute // 1440), None)
        if (care.day == village_state.minute // 1440 and care.fed and care.watered and care.cleaned
                and village_livestock._animals(world, operation)
                and tool is not None and not tool.hidden and not tool.broken
                and 480 <= clock and clock + duration <= (780 if session == "morning" and operation == "milk" else 1080)
                and village_state.minute + duration <= work_end
                and (operation != "milk" or (session not in care.milk_sessions
                                             and (prior is None or village_state.minute >= prior.start + 240)))
                and (operation != "eggs" or not care.eggs)):
            potential = max(potential, 6 if operation == "milk" else livestock.config.hens)
    if stock + potential - protected < terms.quantity:
        raise ValueError("Uncommitted fresh stock cannot cover this supply without violating reserves")
    # Production commitments can use a real supplier's capacity, not conjured inventory.
    # Stock-only commitments must leave every configured reserve and prior promise intact.
    if any(order.buyer == notice.author and order.supplier == supplier
           and order.product == terms.subject and order.destination == terms.destination
           and order.status in village_economy.ACTIVE for order in state.orders.values()):
        raise ValueError("An equivalent active supplier order already covers this need")
    order = VillageSupplierOrder(
        id=f"order-{state.next_order}", request_key=f"notice:{notice.id}",
        buyer=notice.author, supplier=supplier, source=source, destination=terms.destination,
        product=terms.subject, quantity=terms.quantity,
        price=terms.unit_price, delivery_fee=terms.delivery_fee, deadline=terms.due_at,
        required_fresh_until=terms.required_fresh_until, created_at=village_state.minute,
        status="accepted", coordination_notice_id=notice.id)
    state.orders[order.id] = order
    state.next_order += 1
    return {"linked_order_id": order.id, "order_snapshot": order.model_dump(mode="json")}


def apply(world: "World", action: str, args: dict[str, Any]) -> tuple[str, str, str, None]:
    if not enabled(world):
        return "REJECTED", "COMMUNICATION_DISABLED", "Physical communication is not enabled.", None
    models = {
        "village_notice_post": PostNotice, "village_inbox_leave": PostNotice,
        "village_notice_read": ReadNotices, "village_inbox_read": ReadInbox,
        "village_notice_update": UpdateNotice, "village_notice_reply": ReplyNotice,
        "village_notice_close": CloseNotice, "village_notice_tell": TellNotice,
    }
    try:
        request = models[action].model_validate(args)
        _ready(world, world.actor_id)
        if (action not in {"village_notice_read", "village_inbox_read", "village_notice_tell"}
                and request.channel != "conversation"
                and "manipulate" not in world.actor.body.capabilities):
            raise ValueError("Writing a physical note requires actual manipulation capability")
        result = _apply(world, action, request)
        return "APPLIED", result[0], result[1], None
    except (ValueError, ValidationError) as error:
        return "REJECTED", "COMMUNICATION_INVALID", str(error), None


def _apply(world: "World", action: str, request: Any) -> tuple[str, str]:
    state, minute, actor = _state(world), world.state.village.minute, world.actor_id
    if action in {"village_notice_read", "village_inbox_read"}:
        fixture = request.inbox_id if action == "village_inbox_read" else state.config.board_id
        if action == "village_inbox_read":
            if state.boxes.get(fixture) != actor:
                raise ValueError("Only the owner can read a workplace inbox")
        entity = world.state.entities.get(fixture)
        if entity is None or entity.hidden or entity.broken or entity.location != world.actor.location_id:
            raise ValueError("The physical board or owned box must actually be here")
        learned = []
        placements = state.placements.get(fixture, {})
        def rank_paper(nid: str) -> tuple:
            notice = state.notices[nid][placements[nid] - 1]
            memory = state.knowledge.get(actor, {}).get(nid)
            already_read = bool(memory and memory.snapshot.version >= notice.version
                                and memory.confidence == "direct")
            return (already_read, actor not in {notice.author, *notice.terms.recipients},
                    notice.terms.priority != "urgent", -notice.updated_at, -int(nid.removeprefix("notice-")))
        ids = request.notice_ids or sorted(placements, key=rank_paper)[:12]
        for nid in ids:
            version = placements.get(nid)
            if version is None:
                continue
            notice = state.notices[nid][version - 1]
            if notice.terms.private and actor not in {notice.author, *notice.terms.recipients}:
                continue
            if _receive(world, actor, notice, "inbox" if action == "village_inbox_read" else "noticeboard"):
                learned.append(notice)
                _emit(world, "notice_read", notice)
        return "NOTICES_READ", f"Read {len(learned)} new versions. Older paper may be out of date."
    if action == "village_notice_tell":
        _local_listener(world, request.listener)
        memory = state.knowledge.get(actor, {}).get(request.notice_id)
        if memory is None or memory.snapshot.version != request.known_version:
            raise ValueError("Tell only a version you personally received")
        if memory.snapshot.terms.private:
            raise ValueError("Restricted/private messages cannot be spread")
        if memory.hops >= state.config.max_hops:
            raise ValueError("This secondhand source chain has reached its hop limit")
        if request.notice_id not in [item["notice_id"] for item in candidates(world, request.listener)]:
            raise ValueError("No relevant novel candidate; do not repeat or interrupt")
        key = f"{actor}:{request.listener}:{request.notice_id}"
        if state.shares.get(key, 0) >= request.known_version:
            raise ValueError("This unchanged version was already shared")
        if minute < state.cooldowns.get(f"tell:{actor}:{request.listener}", 0):
            raise ValueError("Allow this listener time before another routine update")
        _receive(world, request.listener, memory.snapshot, "conversation", actor, memory)
        state.shares[key] = request.known_version
        state.cooldowns[f"tell:{actor}:{request.listener}"] = minute + state.config.cooldown_minutes
        world.state.village.residents[request.listener].available_at = minute + 5
        _emit(world, "notice_shared", memory.snapshot, listeners=[request.listener],
              text=f"{village.resident_name(world, actor)} shared news of {memory.snapshot.terms.subject}; "
                   f"learned at {village.clock_time(memory.heard_at)}, may be out of date.")
        _emit(world, "notice_heard", memory.snapshot, listeners=[request.listener])
        return "NOTICE_SHARED", "Shared a personally known snapshot, explicitly warning it may be out of date."
    if action in {"village_notice_post", "village_inbox_leave"}:
        if action == "village_inbox_leave" and request.channel != "inbox":
            raise ValueError("Leaving an inbox note requires channel inbox")
        terms = _priced_terms(world, request.notice)
        _validate_terms(world, terms, actor)
        fixture = _channel(world, request, terms, actor)
        previous = [versions[-1] for versions in state.notices.values()
                    if _need_key(versions[-1].author, versions[-1].terms) == _need_key(actor, terms)]
        if any(notice.status not in TERMINAL for notice in previous):
            raise ValueError("Update the existing equivalent open need instead of posting a duplicate")
        if any(notice.terms == terms for notice in previous):
            raise ValueError("No material change since the prior failed/closed request")
        if minute < state.cooldowns.get(f"post:{actor}", 0):
            raise ValueError("Post/check activity has a cooldown; continue useful work")
        nid = f"notice-{state.next_notice}"
        appointment = None
        if terms.type == "meeting":
            members, site = _project(world, terms.project)
            if any(versions[-1].appointment and versions[-1].terms.project == terms.project
                   and versions[-1].status not in TERMINAL for versions in state.notices.values()):
                raise ValueError("This project already has an open appointment")
            appointment = Appointment(
                id=f"appointment-{nid}", version=1, project=terms.project, members=members,
                accepted=[actor], place=site, due_at=terms.due_at, end_at=terms.due_at + 60)
        notice = NoticeVersion(id=nid, version=1, author=actor, terms=terms,
                               created_at=minute, updated_at=minute, appointment=appointment)
        state.notices[nid] = [notice]
        state.next_notice += 1
        state.cooldowns[f"post:{actor}"] = minute + state.config.cooldown_minutes
        _publish(world, notice, request, fixture)
        _emit(world, "notice_posted", notice, listeners=[request.listener] if request.listener else [])
        return "NOTICE_POSTED", f"Posted {nid}; absent recipients have not learned it."
    notice = _known_current(world, request, allow_closed=(
        action == "village_notice_update" and request.notice is None))
    fixture = _channel(world, request, notice.terms, notice.author, replying=actor != notice.author)
    changes: dict[str, Any] = {}
    if action == "village_notice_update":
        if request.notice is None:
            if actor not in {notice.author, notice.responder}:
                raise ValueError("Only the author or committed responder may publish verified status")
            if fixture and state.placements.get(fixture, {}).get(notice.id) == notice.version:
                raise ValueError("This unchanged version is already present here")
            _publish(world, notice, request, fixture)
            _emit(world, "notice_updated", notice,
                  listeners=[request.listener] if request.listener else [])
            return "NOTICE_PUBLISHED", "Published the personally known verified version at this physical channel."
        if actor != notice.author or notice.linked_order_id or notice.status not in {"posted", "seen"}:
            raise ValueError("Only the author can change uncommitted terms; cancel commitments explicitly")
        request.notice = _priced_terms(world, request.notice)
        _validate_terms(world, request.notice, actor)
        _channel(world, request, request.notice, actor)
        if any(other[-1].id != notice.id and other[-1].status not in TERMINAL
               and _need_key(other[-1].author, other[-1].terms) == _need_key(actor, request.notice)
               for other in state.notices.values()):
            raise ValueError("These changed terms would duplicate another open need")
        if request.notice == notice.terms:
            raise ValueError("A new version requires a meaningful change")
        if notice.appointment and (request.notice.type != "meeting"
                                   or request.notice.project != notice.terms.project):
            raise ValueError("An appointment cannot change its project")
        changes.update(terms=request.notice, status="posted", responder=None, response=None)
        if notice.appointment:
            changes["appointment"] = notice.appointment.model_copy(update={
                "version": notice.appointment.version + 1, "accepted": [actor], "status": "proposed",
                "due_at": request.notice.due_at, "end_at": request.notice.due_at + 60})
    elif action == "village_notice_close":
        if actor != notice.author:
            raise ValueError("Only the author can cancel a notice")
        if notice.linked_order_id:
            order = world.state.village.economy.orders[notice.linked_order_id]
            if order.in_transit or order.delivered_quantity:
                raise ValueError("Resolve physical cargo/partial delivery before cancelling")
            order.status, order.reason = "cancelled", request.reason
            changes["order_snapshot"] = order.model_dump(mode="json")
        changes.update(status=request.status, response=request.reason)
        if notice.appointment:
            changes["appointment"] = notice.appointment.model_copy(update={
                "version": notice.appointment.version + 1, "status": "cancelled"})
    elif action == "village_notice_reply":
        if actor == notice.author or (notice.terms.recipients and actor not in notice.terms.recipients):
            raise ValueError("Only an intended other recipient may respond")
        if notice.status not in {"posted", "seen"}:
            raise ValueError("A committed request cannot be accepted or rejected again")
        changes.update(responder=actor, response=request.reason or request.response)
        if request.response == "accept":
            if notice.terms.type == "supply_request":
                village_state = world.state.village
                source = world.scenario.village.residents[actor].work
                key = f"{actor}:{notice.id}:{notice.version}"
                fingerprint = repr((village_state.stocks.get(source), village_state.coins[notice.author],
                                    village_economy.commitments(world, notice.author),
                                    village_state.economy.config.model_dump(),
                                    tuple(world.actor.inventory), world.actor.body.capacity,
                                    [(eid, entity.health, entity.broken, entity.hidden, entity.location)
                                     for eid, entity in world.state.entities.items()
                                     if entity.location == source or eid in world.actor.inventory],
                                    (village_state.livestock.cow.model_dump(),
                                     village_state.livestock.hens.model_dump(),
                                     minute // 1440, minute % 1440 >= 780)
                                    if village_state.livestock else None))
                if state.failed_replies.get(key) == fingerprint:
                    raise ValueError("This supply check already failed; wait for changed stock, funding or terms")
                try:
                    changes.update(_create_order(world, notice))
                except ValueError:
                    state.failed_replies[key] = fingerprint
                    raise
            if notice.appointment:
                accepted = list(dict.fromkeys([*notice.appointment.accepted, actor]))
                changes["appointment"] = notice.appointment.model_copy(update={
                    "version": notice.appointment.version + 1, "accepted": accepted,
                    "status": "confirmed" if set(accepted) == set(notice.appointment.members) else "proposed"})
            changes["status"] = "accepted"
        elif request.response == "reject":
            if not request.reason:
                raise ValueError("A rejection needs a reason")
            changes["status"] = "rejected"
            if notice.appointment:
                changes["appointment"] = notice.appointment.model_copy(update={
                    "version": notice.appointment.version + 1, "status": "cancelled"})
        elif not request.reason:
            raise ValueError("A proposal must explain the changed terms; the author must publish a new version")
        else:
            changes["status"] = "seen"
    value = _new_version(world, notice, **changes)
    _publish(world, value, request, fixture)
    _emit(world, "notice_reply_posted" if action == "village_notice_reply" else "notice_updated",
          value, listeners=[request.listener] if request.listener else [])
    if changes.get("status") == "accepted":
        _emit(world, "commitment_accepted", value, listeners=[request.listener] if request.listener else [])
    return "NOTICE_UPDATED", f"Recorded {value.id} version {value.version}; others learn only by receipt."


def candidates(world: "World", listener: str) -> list[dict[str, Any]]:
    if not enabled(world):
        return []
    try:
        _ready(world, world.actor_id)
        _local_listener(world, listener)
    except ValueError:
        return []
    state, ranked = _state(world), []
    base = world.scenario.village
    resident = base.residents[listener]
    job = resident.job
    products = set(job.outputs if job else {}) | set(job.inputs if job else {})
    if world.state.village.economy:
        products |= {product for product, supplier in village_economy.config(world).suppliers.items()
                     if supplier == listener}
    for nid, knowledge in state.knowledge.get(world.actor_id, {}).items():
        notice = knowledge.snapshot
        other = state.knowledge.get(listener, {}).get(nid)
        if (notice.terms.private or knowledge.hops >= state.config.max_hops
                or (other and other.snapshot.version >= notice.version)
                or state.shares.get(f"{world.actor_id}:{listener}:{nid}", 0) >= notice.version):
            continue
        direct = listener in notice.terms.recipients or listener == notice.author
        match = (notice.terms.subject in products or notice.terms.subject in resident.skills
                 or (listener == base.courier and notice.linked_order_id is not None))
        urgent = notice.terms.type == "warning" or notice.terms.priority == "urgent"
        if not (direct or match or urgent):
            continue
        due = notice.terms.due_at if notice.terms.due_at is not None else 10**9
        score = (not direct, not match, not urgent, due, knowledge.confidence != "direct", nid)
        ranked.append((score, {"notice_id": nid, "known_version": notice.version,
                               "subject": notice.terms.subject, "learned_at": knowledge.heard_at,
                               "freshness": "May be out of date; verify through a local channel"}))
    return [item for _, item in sorted(ranked, key=lambda pair: pair[0])[:3]]


def learned_orders(world: "World") -> list[dict[str, Any]]:
    return [knowledge.snapshot.order_snapshot.copy()
            for knowledge in _state(world).knowledge.get(world.actor_id, {}).values()
            if knowledge.snapshot.linked_order_id and knowledge.snapshot.order_snapshot]


def can_use_order(world: "World", order: Any) -> bool:
    if not enabled(world) or not order.coordination_notice_id:
        return True
    knowledge = _state(world).knowledge.get(world.actor_id, {}).get(order.coordination_notice_id)
    return bool(knowledge and knowledge.snapshot.order_snapshot.get("status") == "accepted")


def project_blocker(world: "World", project: str) -> str | None:
    if not enabled(world):
        return None
    state, minute = _state(world), world.state.village.minute
    plans = [versions[-1].appointment for versions in state.notices.values()
             if versions[-1].appointment and versions[-1].terms.project == project]
    for plan in reversed(plans):
        if plan.status != "confirmed" or not plan.due_at <= minute <= plan.end_at:
            continue
        for member in plan.members:
            memories = state.knowledge.get(member, {}).values()
            if not any(k.snapshot.appointment and k.snapshot.appointment.id == plan.id
                       and k.snapshot.appointment.version == plan.version
                       and k.snapshot.appointment.status == "confirmed" for k in memories):
                return "Both workers must personally receive the confirmed appointment"
            if village.resident_location(world, member) != plan.place:
                return "Both actual workers must be at the agreed site"
            person = world.state.village.residents[member]
            if person.life and (person.life.sleeping or person.life.work):
                return "An actual worker is sleeping or tending a batch"
            if person.available_at > minute or person.busy_until > minute:
                return "An actual worker has not finished arriving or is still occupied"
            from npc_sim import village_extensions
            if village_extensions.blocked_until(world, member) is not None:
                return "An actual worker is occupied with another physical task"
        return None
    return "Propose and confirm a real appointment, receive the confirmation, and attend at its due time"


def sync_order(world: "World", order: Any, event: str, location: str) -> None:
    if not enabled(world) or not order.coordination_notice_id:
        return
    history = _state(world).notices.get(order.coordination_notice_id)
    if not history:
        return
    notice = history[-1]
    if notice.order_snapshot == order.model_dump(mode="json"):
        return
    status = ("completed" if order.status == "fulfilled" else
              "cancelled" if order.status == "cancelled" else
              "expired" if order.status == "failed" else
              "rejected" if order.status == "rejected" else
              "in_progress" if order.in_transit or order.delivered_quantity else "accepted")
    if order.status == "fulfilled":
        delivered = _new_version(world, notice, status="delivered", order_snapshot=order.model_dump(mode="json"))
        notice = delivered
    value = _new_version(world, notice, status=status, order_snapshot=order.model_dump(mode="json"))
    witnesses = [rid for rid in village.local_residents(world, location)
                 if not (world.state.village.residents[rid].life
                         and world.state.village.residents[rid].life.sleeping)
                 and (not notice.terms.private or rid in {notice.author, notice.responder, *notice.terms.recipients})]
    for rid in witnesses:
        _receive(world, rid, value, "witness")
    # No placement update: absent buyers/suppliers and the old paper remain stale.
    _emit(world, event, value, listeners=witnesses, include_actor=False)


def advance(world: "World") -> None:
    if not enabled(world):
        return
    village_state = world.state.village
    for history in list(_state(world).notices.values()):
        notice = history[-1]
        if notice.status in TERMINAL:
            continue
        if notice.appointment:
            completed = village_state.well_dug if notice.terms.project == "dig_well" else village_state.repaired
            if completed:
                value = _new_version(world, notice, status="completed", appointment=notice.appointment.model_copy(
                    update={"version": notice.appointment.version + 1, "status": "completed"}))
                for rid in village.local_residents(world, value.appointment.place):
                    if (not (village_state.residents[rid].life and village_state.residents[rid].life.sleeping)
                            and (not notice.terms.private or rid in notice.appointment.members)):
                        _receive(world, rid, value, "witness")
                _emit(world, "job_completed", value, include_actor=False,
                      listeners=[rid for rid in value.appointment.members
                                 if village.resident_location(world, rid) == value.appointment.place
                                 and not (village_state.residents[rid].life
                                          and village_state.residents[rid].life.sleeping)])
                continue
        limit = notice.appointment.end_at if notice.appointment else None
        expired = village_state.minute > limit if limit is not None else _overdue(world, notice)
        if expired:
            if notice.linked_order_id:
                order = village_state.economy.orders[notice.linked_order_id]
                if order.status in village_economy.ACTIVE:
                    order.status, order.reason = "failed", "MISSED_DEADLINE"
                sync_order(world, order, "notice_expired", order.destination)
            else:
                changes = {"status": "expired"}
                if notice.appointment:
                    changes["appointment"] = notice.appointment.model_copy(update={
                        "version": notice.appointment.version + 1, "status": "expired"})
                value = _new_version(world, notice, **changes)
                _emit(world, "notice_expired", value, include_actor=False)


def _view(world: "World", notice: NoticeVersion, knowledge: NoticeKnowledge | None = None) -> dict[str, Any]:
    terms = notice.terms
    names = {rid: village.resident_name(world, rid) for rid in world.scenario.village.residents}
    places = {eid: entity.name for eid, entity in world.state.entities.items() if entity.kind == "location"}
    return {
        "id": notice.id, "version": notice.version, "author": names[notice.author],
        "recipients": [names[rid] for rid in terms.recipients],
        "subject": terms.subject, "description": terms.description, "type": terms.type,
        "status": notice.status, "quantity": terms.quantity,
        "unit_price": terms.unit_price, "delivery_fee": terms.delivery_fee,
        "destination": places.get(terms.destination) if terms.destination else None,
        "due_at": terms.due_at, "updated_at": notice.updated_at,
        "private": terms.private, "response": notice.response,
        "source": (knowledge.source_type + (f": {names.get(knowledge.source_actor, knowledge.source_actor)}"
                                          if knowledge.source_actor else "")) if knowledge else "operator world state",
        "learned_at": knowledge.heard_at if knowledge else None,
        "last_verified_at": knowledge.last_verified_at if knowledge else None,
        "confidence": knowledge.confidence if knowledge else None,
        "hops": knowledge.hops if knowledge else 0,
        "source_chain": [names.get(item, item) for item in knowledge.source_chain] if knowledge else [],
        "freshness": "Snapshot; may be out of date" if knowledge else "Authoritative; not NPC knowledge",
        "appointment": ({
            **notice.appointment.model_dump(), "members": [names[rid] for rid in notice.appointment.members],
            "accepted": [names[rid] for rid in notice.appointment.accepted],
            "place": places[notice.appointment.place],
        } if notice.appointment else None),
        "commitment": ({"status": notice.order_snapshot.get("status"),
                        "delivered": notice.order_snapshot.get("delivered_quantity", 0),
                        "paid": notice.order_snapshot.get("paid", 0),
                        "supplier": names.get(notice.order_snapshot.get("supplier"), ""),
                        "buyer": names.get(notice.order_snapshot.get("buyer"), "")}
                       if notice.linked_order_id else None),
    }


def observation(world: "World") -> dict[str, Any]:
    state = _state(world)
    memories = state.knowledge.get(world.actor_id, {})
    ordered = sorted(memories.values(), key=lambda item: (
        not (item.snapshot.status not in TERMINAL and (
            item.snapshot.linked_order_id or item.snapshot.appointment
            or world.actor_id in item.snapshot.terms.recipients)),
        item.snapshot.terms.priority != "urgent",
        item.snapshot.terms.due_at if item.snapshot.terms.due_at is not None else 10**9,
        -item.heard_at, item.snapshot.id))
    return {"communication": {
        "enabled": True, "scope": "personal received snapshots", "policy": GUIDANCE,
        "notices": [_view(world, item.snapshot, item) for item in ordered[:12]],
        "boxes": [{"id": box, "owner": village.resident_name(world, owner),
                   "place": world.state.entities[world.state.entities[box].location].name,
                   "here": world.state.entities[box].location == world.actor.location_id,
                   "can_read": owner == world.actor_id and world.state.entities[box].location == world.actor.location_id}
                  for box, owner in state.boxes.items()
                  if owner == world.actor_id or world.state.entities[box].location == world.actor.location_id],
        "share_candidates": [{"listener": listener, "listener_name": village.resident_name(world, listener),
                              "candidates": candidates(world, listener)}
                             for listener in village.local_residents(world, world.actor.location_id)
                             if listener != world.actor_id and candidates(world, listener)],
    }}


def monitor(world: "World") -> dict[str, Any]:
    state = _state(world)
    return {"communication": {
        "enabled": True, "scope": "privileged operator world view", "policy": GUIDANCE,
        "notices": [_view(world, history[-1]) for history in state.notices.values()],
        "boxes": [{"id": box, "owner": village.resident_name(world, owner),
                   "place": world.state.entities[world.state.entities[box].location].name,
                   "here": False, "can_read": False, "notes": len(state.placements.get(box, {}))}
                  for box, owner in state.boxes.items()],
        "share_candidates": [],
    }}


def actions(world: "World") -> list[dict[str, Any]]:
    result = []
    descriptions = {
        "village_notice_post": (PostNotice, "Post one need at the local board, box, or real conversation; then do useful work."),
        "village_notice_read": (ReadNotices, "Read up to twelve paper versions at the local square board (five minutes)."),
        "village_notice_update": (UpdateNotice, "Publish received verified status, or author changes uncommitted terms, at a real channel."),
        "village_notice_reply": (ReplyNotice, "Reply through a physical channel. Acceptance validates funding; never delivers goods."),
        "village_notice_close": (CloseNotice, "Author cancels via a real channel, not a remote change."),
        "village_notice_tell": (TellNotice, "Tell one listed relevant new candidate to an actually present available listener."),
        "village_inbox_read": (ReadInbox, "Read only your owned physical workplace box while actually there."),
        "village_inbox_leave": (PostNotice, "Write a directed request at its actual recipient's box (channel inbox); no teleporting."),
    }
    for verb, (model, text) in descriptions.items():
        result.append({"id": verb, "description": text, "targets": [],
                       "arguments_schema": model.model_json_schema(),
                       "availability": "available", "reason": None})
    return result
