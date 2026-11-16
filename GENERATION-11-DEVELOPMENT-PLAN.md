# Millhaven Generation 11 - development plan

**Purpose:** Use the [Generation 11 Day 1 assessment](GENERATION-11-BASELINE.md)
as the before-state for the next implementation and model run. Generation 11
itself is already observed; this plan does not rename a future run Generation
11 or claim that the observed run passed. Apply fixes to a new scenario/run,
preserving the baseline and existing saved worlds.

## 0. Freeze comparable evidence

- Preserve the actual Day 1 action log, saved world, scenario JSON/hash, run ID,
  model/proxy/settings, budgets and effective policy if available. The review
  alone is not a replay fixture. Keep the 05:00-23:00 log distinct from the
  later Day 2 00:00 World view snapshot.
- Capture day-keyed breakfast and supper consumption, prepared portions and
  allocations; crew notice versions and attendance; orders, cargo, lots,
  payments, fuel and animal care. Record attempted/rejected actions and model
  calls. Use `npc_sim/village_release_evidence.py` for offline saved-world
  captures without mutating the live run.
- Establish a reproducible controlled scenario for regressions before changing
  behavior. If the original run cannot be recovered, label subsequent results
  *scenario-matched* rather than an exact replay.

## P0 - service and repair correctness

| Work item | Implementation path | Acceptance gate |
| --- | --- | --- |
| Separate breakfast, daytime food and supper | Audit service allocation and due-time handling in `npc_sim/village_life.py`, `npc_sim/village_economy.py` and the resident fallback. Preserve valid daytime eating, but never let it discharge the configured evening supper or spend a reserved supper portion as a late breakfast. | Tests in `tests/test_village_meal_planning.py` and `tests/test_village_life.py` cover early eating, simultaneous diners, late breakfast, limited stock and midnight. Per-resident ledger counts actual supper within the configured window; no retroactive or double allocation. |
| Unblock expired crew appointments | Follow notice versioning and crew session state in `npc_sim/village_communication.py` and `npc_sim/village_ai.py`. Permit an expired booking to be closed/superseded by an independently confirmed eligible booking without bypassing presence, consent, tools, materials or breakdown timing. | Extend `tests/test_village_communication.py` for expired well and mill meetings, both partners ready, failed replacement and save/reload. Work begins when the valid crew is ready; old notices cannot authorize work or trap a new appointment. |
| Make supper readiness actionable | Wire the existing stock-backed forecast in `npc_sim/village_economy.py` into innkeeper ordering, cooking and resident decision prompts. Distinguish ready, cookable, funded incoming and merely requested portions; consider fuel, freshness, route and service cutoff. | Extend `tests/test_village_economy.py` for recoverable early shortage, impossible late shortage, odd final two-portion batch and no duplicate incoming supply. Report a precise blocker when 12/12 is infeasible; do not conjure meals or count promises as deliveries. |

**P0 integration gate:** A provisioned controlled Day 1 produces 12/12 actual
suppers in the service window and completes well and mill repair with valid
crew evidence. Record the actual completion times against 16:10 and 17:10;
do not treat those times as guaranteed thresholds under a different model run.
If the gate fails, retain the observed ledger and diagnose the first missing
ingredient, allocation or booking transition before moving to P1.

## P1 - sustainable supply and coordination

| Work item | Implementation path | Acceptance gate |
| --- | --- | --- |
| Firewood production and delivery | Check Rowan's real splitting stock/tool/time and the funded physical delivery flow in `npc_sim/village_firewood.py` and `npc_sim/village_economy.py`; surface cottage, bakery and forge deficits early enough to fulfill them. | `tests/test_village_firewood.py` plus route/economy tests show production, two valid trips totaling at least three delivered units, settlement once, and no use of committed logs. Compared with Generation 11's zero recorded deliveries, the next run must show actual replenishment. |
| Real wagon shipment | Audit loadable accepted pickups, driver, horse, capacity, routes and return in `npc_sim/village_wagon.py` and resident planning. Do not start a no-cargo circuit. | `tests/test_village_wagon_route.py` and `tests/test_village_p1.py` show one funded farm-to-mill load, travel, unload and payment, followed by horse return/release; no duplicate settlement after retry or reload. |
| Deadline-aware milk and egg chain | Use the day-keyed care and collection rules in `npc_sim/village_livestock.py`, then funded ordering and delivery to the tavern; distinguish feed, water, collected, reserved and transported stock. | Livestock/economy tests cover care before production, timed collection, enough lead time, actual tavern receipt, and an explicit late-order blocker. No claim of supply based only on a posted request. |
| Reduce repeated information trips | Reuse personally observable notice versions and decision wake conditions in `npc_sim/village_communication.py`, `npc_sim/village_fallback.py` and `npc_sim/village_walking.py`; keep physical knowledge rules. | Communication/walking/scheduler tests preserve privacy and legal routes. On a matched run, compare movement actions (552), Ada (82), Elsie (69), rejected waits (31) and physical-channel errors (7); report both improvements and regressions rather than hiding necessary travel. |
| Stabilize mine identity | Reconcile `npc_sim/village_mining.py` with generated scenario and map/location labels. Keep a single ID and display name, **Torvin's Iron Mine**, without breaking saved-world references. | `tests/test_village_mining.py` verifies generated references, routes, map labels and save/load identity; no resident-name-derived variant. |

Check Garrick's noon budget-exhaustion message against the recorded budget and
subsequent actions during this phase. Fix only if the message or enforcement is
incorrect; add a focused regression for the verified cause.

Archived run `a7c5a33735ca4e19909ab2b1be1183fd` records a budget-exceeded
event at Day 1 12:00 after 600 actions (the then-effective action cap was 600).
The operator subsequently extended the cap by 100 actions and 100 model calls;
later actions therefore do not contradict that earlier exhaustion message.
No budget-enforcement change is indicated by this archive.

## P2 - continuity and release evidence

- Exercise the communal depot and emergency cistern only where the scenario
  actually implements those services; the existing test criteria describe
  nonfunctional fixtures and must not be silently treated as delivered features.
- Run a controlled **three consecutive active day** episode without renewing
  opening stock. At each midnight reconcile opening + production/receipts -
  consumption/shipments/spoilage = closing inventory by site and product;
  include funds, reservations, animal feed/care, maintenance, meals, fuel,
  cargo and expired orders. A paused midnight is a checkpoint, not a pass.
- Compare the next run with the Generation 11 table and document unavoidable
  shortages separately from scheduling/agent failures. Three days are an
  intermediate reliability gate; the seven-day release gate in
  [Millhaven test criteria](MILLHAVEN-TEST-CRITERIA.md) remains open.

**P2 implementation status:** The resilient/complete depot and feed store have
physical transfer and ownership tests; the cistern is still a nonfunctional
fixture. `npc_sim.village_release_evidence --scenario ... --checkpoints OPEN
MIDNIGHT1 MIDNIGHT2 MIDNIGHT3 --output ...` reads four saved states from one
world, rejects nonconsecutive checkpoints, and reports site/product movements,
funds, cargo, ownership, meals, care and maintenance without changing them.
A deterministic controlled three-day regression exercises the accounting and
save/reload path, not autonomous resident decisions. No three-day model episode
or seven-day reliability release is claimed; run settings, failed actions and
shortage-versus-scheduling comparisons require a separately captured episode.

## Execution and sign-off

Implement P0 in the order shown: meal accounting, crew replacement, then
readiness decisions; run focused tests after each coherent change. Proceed to
P1 only with the P0 controlled integration ledger, then run related logistics
tests and a fresh model episode. Finally validate the multi-day ledger and
relevant full village tests. A passing unit suite proves invariants, not that
autonomous residents achieved 12/12 supper or sustainable supply. Publish the
scenario/hash, model/settings, exact evidence window, per-day results and
remaining blockers with each comparison.
