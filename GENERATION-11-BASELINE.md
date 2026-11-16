# Millhaven Generation 11 - Day 1 assessment baseline

See the [development plan](GENERATION-11-DEVELOPMENT-PLAN.md) for prioritized
follow-up and measurable comparison gates.

**Status:** Retained comparison baseline, not a passing reliability release or a
replayable frozen-world fixture. This record captures the supplied Day 1 review
and the paused World view inspected on 2026-11-16. The reviewed action log covers
Day 1, 05:00-23:00; it does not establish Day 2 or Day 3 behavior. The live World
view subsequently showed a pause at Day 2 00:00. Do not combine the two evidence
windows into a multi-day result.

## Outcome and comparison

| Measure | Generation 10 | Generation 11 | Interpretation |
| --- | ---: | ---: | --- |
| Breakfast | 12/12 | 12/12 | Maintained. |
| Daily meals recorded | 4/12 | 10/12 | Improved, but only six were eaten after 17:00. |
| Major repairs | - | 3/3 | Well, mill, and wagon completed. |
| Well completed | 12:25 | 16:10 | Slower; expired booking delayed work. |
| Mill completed | 15:35 | 17:10 | Slower; expired booking delayed work. |
| Wagon usable | 09:55 | 08:25 | Faster; no completed wagon shipment. |
| Firewood delivered | 2 units | 0 recorded | Replenishment regressed. |
| Cow and hen care | Completed | Completed | Maintained. |
| Movement actions | 502 | 552 | 10% more. |
| Rejected waits | 35 | 31 | Improved. |
| Physical-channel errors | 9 | 7 | Improved. |
| Overnight sleep at home | 12/12 | 12/12 | Maintained in the supplied review. |

These counts and Generation 10 comparisons are from the supplied review, not
independently recomputed from an archived action log in this record. The World
view corroborated completed well and mill appointments, fulfilled fish, bread,
flour and grain orders, and day-keyed livestock activity, but is a later
snapshot rather than the Day 1 log.

## What worked

- Finn delivered two fish and Jory delivered bread to Elsie's tavern; Elsie
  cooked four fish-meal portions at 18:00. Mara delivered flour to Nella, and
  Ada supplied grain to Nella and the mill. These were physical deliveries,
  not merely accepted requests.
- Torvin repaired the wagon by 08:25 and bought iron from a visiting trader.
  The well and mill were eventually repaired after replacement appointments.
- Ada completed cow and hen care. Finn's smoked-fish, Jory's flatbread, and
  Iona's herbal research reached discovery in the supplied review.

## Open failures and risks

1. **Supper is not distinct from daytime food.** Ada ate at 09:50, Torvin at
   10:35, and Bren at 14:15; these count toward the ten recorded daily meals.
   Jory ate at 17:35. Only six meals were recorded after 17:00, and Nella and
   Rowan missed a daily meal. Measure the actual supper service window and
   individual allocations, not just the daily-meal flag.
2. **Expired crew bookings block ready workers.** Torvin spent the morning
   retrieving a shovel from the mill shed. The old well appointment then
   prevented work until a replacement was confirmed. Hobb and Mara similarly
   had the materials and crew for the mill but could not use or close an
   expired appointment. The replacement bookings succeeded, too late to
   preserve the earlier completion times.
3. **Supply continuity is not demonstrated.** No completed firewood delivery
   was recorded; Iona's six-unit cottage request remained open at bedtime.
   Bakery and forge fuel reserves declined, the wagon made no real shipment,
   and milk/egg supply did not meet the tavern's deadlines. The later World
   view showed zero milk and eggs at the farm and an expired milk notice.
4. **Coordination costs remain high.** Ada made 82 movements and Elsie 69 in
   the supplied review, including repeat checks for information that had not
   changed. Garrick's episode reportedly emitted a budget-exhaustion message
   at noon despite later actions; verify budget accounting against the log.
5. **Mine identity is inconsistent.** Generated names such as "Millhaven iron
   Ada's" and "Millhaven iron Bren's" should resolve to one stable location
   identity and display name, **Torvin's Iron Mine**.

## Next comparison gates

| Priority | Change | Evidence required in the next run |
| --- | --- | --- |
| P0 | Separate supper from daytime meal consumption. | Count 12 individual evening meals within the service window, independently of breakfast and earlier snacks. |
| P0 | Replace or close expired crew appointments without blocking an eligible new booking. | Record crew, tools, materials, old/new booking states and actual work start; compare well and mill completion times. |
| P0 | Forecast meal readiness and schedule cooking before service. | Reconcile fresh inputs, funded deliveries, fuel, portions and allocations by deadline. |
| P1 | Replenish firewood; execute a funded wagon shipment. | Show produced, loaded, delivered and settled quantities, not plans or starting stock. |
| P1 | Reduce unchanged-information trips; plan milk and eggs ahead of deadlines. | Compare movements, channel errors and actual delivered produce against this baseline. |
| P1 | Stabilize mine ID and name. | Verify every generated map/location reference uses the same identity. |
| P2 | Exercise depot/cistern services and a continuous three-day economy run. | Capture consecutive daily ledgers without resetting stock or treating a midnight pause as completion. |

The existing `data/fixtures/generation8-baseline` is a deterministic engine
starting state, not the source of these Generation 11 model-run outcomes. A
replayable Generation 11 fixture would additionally require the frozen
scenario/hash, saved world, action log, model/settings and run identifier.
