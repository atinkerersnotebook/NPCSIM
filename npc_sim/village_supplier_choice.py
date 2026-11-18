"""Rank reachable supply contacts from a buyer's own confirmed outcomes."""

from collections.abc import Sequence
from math import log, sqrt
from typing import TYPE_CHECKING

from npc_sim.village_communication_models import NoticeTerms

if TYPE_CHECKING:
    from npc_sim.world import World


def _short_window(terms: NoticeTerms, minute: int) -> bool:
    return terms.due_at is not None and terms.due_at - minute <= 120


def rank(world: "World", terms: NoticeTerms, suppliers: Sequence[str]) -> list[str]:
    """Use a per-buyer, per-good UCB policy in each deadline bucket."""
    communication = world.state.village.communication
    minute = world.state.village.minute
    short_window = _short_window(terms, minute)
    outcomes: dict[str, list[int]] = {supplier: [] for supplier in suppliers}
    contacts = dict.fromkeys(suppliers, 0)
    for memory in communication.knowledge.get(world.actor_id, {}).values():
        if memory.confidence != "direct":
            continue
        notice = memory.snapshot
        past = notice.terms
        if (notice.author != world.actor_id or past.type != "supply_request"
                or past.subject != terms.subject or past.destination != terms.destination
                or past.due_at is None or notice.revised_from_counteroffer
                or _short_window(past, notice.created_at) != short_window
                or len(past.recipients) != 1 or past.recipients[0] not in outcomes):
            continue
        supplier = past.recipients[0]
        contacts[supplier] += 1
        if notice.status == "completed":
            outcomes[supplier].append(int(notice.updated_at <= past.due_at))
        elif notice.status in {"rejected", "expired"}:
            outcomes[supplier].append(0)

    total = sum(contacts.values())

    def score(supplier: str) -> float:
        attempts = contacts[supplier]
        if not attempts:
            return float("inf")
        samples = outcomes[supplier]
        # Unobserved results affect exploration, not the estimated success rate.
        expected = sum(samples) / len(samples) if samples else 0.5
        return expected + sqrt(2 * log(total) / attempts)

    return sorted(suppliers, key=score, reverse=True)
