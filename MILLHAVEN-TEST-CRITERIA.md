# Millhaven scenario test criteria

This is the consolidated acceptance checklist for `millhaven`,
`millhaven-expanded`, `millhaven-living`, `millhaven-resilient`, and
`millhaven-generations`. It describes **what to test**, not a claim that the
scenarios already pass an end-to-end model benchmark. The authoritative
`village_restored` evaluator is implemented in `npc_sim/village.py` for the
visiting-helper scenario and `npc_sim/village_ai.py` for the AI-resident variants.
Scenario-specific rules come from the frozen scenario and effective runtime
policy; do not assume that optional behavior is enabled in every saved run.

## What counts as a pass

| Level | Pass condition | Evidence |
| --- | --- | --- |
| Engine | The final `evaluate_goal("village_restored")` reports `achieved` and every returned `evidence.checks` value is true. Rejections, retries, save/reload and failed actions preserve the documented invariants. | Final world verification, action results, event ledger, focused tests. |
| Episode | Engine pass **and** completed assessment with `verdict=pass`, final verification `achieved`, and `run.successful=true`. A running, paused, ungraded or reviewer-failed attempt is not a success. | Frozen scenario, `run.json`/`summary.json`, `completion_analysis` and event history. |
| Reliability release | Reproducible Day 1 and seven *active, consecutive* days without resetting opening stock or pending work; all applicable gates below pass, with anomalies and scarcity reported rather than hidden. | Starting fixture/hash, fixed model and settings, timestamped snapshots, daily reconciliation and rerun instructions. |

The generic batch evaluator in `npc_sim/evaluate.py` defaults to 20 attempts at
the selected scenario **plus 10 `sleepy-room` attempts**, with a 90% threshold
and the configured action/time budgets. Its published 18/20 and 9/10 targets
are for the original escape and replacement fixtures, **not an established
Millhaven success-rate target**. Village cycles pause at midnight, may continue
on later days, and can use opt-in unlimited budgets; record those choices and
do not treat one midnight pause as a failed completed episode. No fixed
Millhaven sample size, model-run success percentage or seven-day cost ceiling
has been agreed. Report per-run outcomes, actions, model calls, elapsed time,
game days and spending before proposing such thresholds.

## Scenario-specific authoritative checks

| Scenario | Required verification | Additional test conditions |
| --- | --- | --- |
| `millhaven` | Completed shovel-delivery task, dug well, repaired mill, and actual bread supply after repair. | Visiting actor must receive the shovel with capacity, crews use real tools/supplies and meet in person, and the 14:00 breakdown precedes repair. No claim of full daily-life completion for this scenario. |
| `millhaven-expanded` | Recovered shovel, well, mill, resumed bread delivery, every resident's work contribution, finished committed batches, repaired wagon, evening reached, 12/12 daily supper eaten, 12/12 at their own homes and asleep, horse returned/released/unhitched, routes stopped, and configured replenishment/tea/manure checks. | Actual movement, loaded wagon and foot deliveries, buyer funds, physical crew contributions and animal handling. Breakfast is not a required evaluator check. |
| `millhaven-living` | Expanded daily checks, with demand-led **usable resource reserves** in place of the three replenishment-action flags; tea/manure checks only when runtime policy requires them. Enabled livestock adds completed cow/hen care for the applicable day and both animals at home. | Breakfast is optional for completion but missed meals have their normal consequences. Orders, meal service, finite stock, spoilage, research, maintenance, events and visitors follow their configured rules; hobbies, quests and visitor departures are not victory requirements. |
| `millhaven-resilient` | Same policy-dependent living-village goal checks, including enabled livestock. | In addition, test physical depot/feed-store transfers, ownership, paid withdrawals, capacity/freshness, finite feed and replenishment before care. The handcart and cistern are visible fixtures, **not** functioning transport or water sources; do not grade their use as mandatory. |
| `millhaven-generations` | Same policy-dependent living-village goal checks, including enabled livestock. | Verify actor-local chronicle reading, persistent entries and named non-playable arrivals/departures across days and save/reload. Seeded family histories are not inherited skills; newcomers are not playable residents and fresh adventures do not inherit another run's state. Do not grade unimplemented succession as an existing victory condition. |

For all AI-resident variants, use the evaluator's actual check set from the
frozen scenario and effective policy. The evening meal is evaluated for the
most recent evening, including after midnight. An incomplete day can be
followed by a complete later day; achievement before midnight does not end
the current village cycle. An AI defeat ruling is not by itself authoritative
proof of an unrecoverable world failure.

## Cross-scenario test cases and release gates

1. **Goal and evidence:** Verify each required flag independently: removing a
   meal, home sleep, a real contribution, the working wagon, or a required
   supply keeps the goal active. Narrative claims, plans or a completed repair
   alone cannot pass. Record the final world check and separate assessment.
2. **Day 1 service (Living variants):** In a fully provisioned reference run, target **12/12
   breakfasts and 12/12 suppers** using real fresh food, fuel, labor, time and
   funded payments; breakfast remains optional to the goal. Measure meal
   reservations, cooked portions, eating and misses per resident. Distinguish
   genuine shortages from premature misses, cooking-window and allocation
   defects; never create food to satisfy the target. Cover `MEAL-01..06`.
3. **Repairs and supply routes:** Confirm shovel handoff, well and post-breakdown
   mill repair with real crew/tool/material evidence; one physical farm-to-mill
   wagon shipment, horse return, three firewood units over two valid trips,
   and the funded two-milk chain when those features are configured. Track
   accepted orders, loads, travel, unloads and exactly-once settlement. Cover
   `CREW-01..06`, `MILK-01..06`, `FUEL-01..05`, `WAGON-01..04`.
4. **Daily continuity:** On each of seven consecutive active days, reconcile
   opening + production/receipts - consumption/outbound shipments/spoilage to closing
   stock by site and product. Reconcile cargo, reservations, lots, ownership,
   coins, service demand, replenishment lead times, animal feed/water/care,
   maintenance, 12/12 home sleep, and failed/blocked actions. Opening stock is
   seeded once, not renewed at midnight. A documented shortage may prevent
   a service target, but unexplained stock, lost cargo, negative balances or
   duplicate payment is a failure. Cover `ECON-01..05`, `CARE-01..05`.
5. **Scheduling and local knowledge:** Exercise overlapping wakes, retries,
   cancellations, midnight/restart, interrupted or expired appointments,
   legal physical communication, planned walking paths and event-driven waits.
   No double work or settlement, inaccessible notice knowledge, teleportation,
   infinite no-op polling, or unreported blocker. Cover `SCHED-01..03`,
   `COMM-01..05`, `PATH-01..05`, `WAIT-01..05`.
6. **Adversarial fixtures:** From controlled saved states, test low stock,
   missing ingredients, late/returned/expired cargo, delayed crew, cancelled
   sales, depleted feed store, insufficient buyer funds, and save/reload in
   transit. For Resilient Village reconcile each owner's depot lots and feed
   store quantities before/after each transfer and care action. For Generations
   verify that only a resident who actually reads the chronicle learns its
   entries, and that newcomers do not acquire actor turns or silently inherit
   skills.

The Generation 8 gates above remain open in `ENGINE-BACKLOG.md`; an isolated
passing unit test is not a verified multi-day NPC outcome. In particular,
12/12 meal service and autonomous depot trading have not been established by
the available evidence.

The [Generation 11 Day 1 assessment baseline](GENERATION-11-BASELINE.md)
records improved deliveries and 10/12 daily meals, but only six evening meals,
delayed crew bookings and no recorded firewood delivery. It is a comparison
record, not a frozen replay fixture or evidence of multi-day reliability.

## Reproducible evidence and existing checks

- Record scenario ID and frozen JSON/hash, baseline world and seed if present,
  model/proxy/configuration, effective policy, budgets or unlimited mode,
  start/end game time, and exact rerun command. Preserve separate scenarios
  and memory; do not overwrite a live or previously captured world.
- At each failure save a timestamped world copy and action result, plus site
  stock, lots, orders, reservations, crew sessions and cargo. Report Day 1
  before/after metrics and a seven-day ledger with unresolved blockers and
  confirmed root causes. The baseline and capture procedure are in the
  README's **Living Millhaven** section; the supplied Generation 8 baseline is
  a deterministic *current-engine* starting point, not an archived Generation 7
  run or a real-model replay.
- For an intermediate three-day continuity check, use four saved checkpoints
  (opening plus three consecutive midnights) with the offline
  `npc_sim.village_release_evidence --checkpoints` command documented in the
  README. Its stock/activity result is engine evidence, not proof of autonomous
  play or the seven-day release gate; retain the run log and effective model
  settings for that comparison.
- Focused regression entry points: `tests/test_village_completion.py`,
  `tests/test_village_economy.py`, `tests/test_village_livestock.py`,
  `tests/test_village_depot.py`, `tests/test_village_legacy.py`, and
  `tests/test_village_release_evidence.py`. Run them with
  `.\.venv\Scripts\python.exe -m pytest` followed by those paths. These prove
  specific world behavior, not a population-wide model success rate.

See `SPEC.md` for general application acceptance and `ENGINE-BACKLOG.md` for
the detailed, still-open Generation 8 and variant-specific release work.
