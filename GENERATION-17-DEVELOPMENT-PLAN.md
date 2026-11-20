# Millhaven Generation 17 - development plan

## Consolidation scope

This document is the consolidated backlog for Generation 17. It carries forward
every item still unchecked in `ENGINE-BACKLOG.md`, including feature work,
reliability corrections, autonomous-run verification and optional mechanics that
remain undecided. Checked engine-backlog items are not repeated here. Historical
generation plans remain evidence and may inform acceptance tests, but this file
is the active implementation and sign-off backlog.

## Implementation status (work in progress)

The cumulative `millhaven-generation-17` scenario now merges Complete and Living
Millhaven, adds traversable North Fields, North Pasture, Emergency Cistern and
Communal Kitchen locations, and separates the Communal Granary from the Supply
Depot. Producer reserves, per-product stock capacities, preserved-fish meal
inputs, feasible Nella supplier selection, physical store settlement and a bounded
authoritative travel feed are implemented with focused regressions. Existing
breakfast/daytime/supper allocation and physical order settlement remain the
foundation rather than being duplicated for Generation 17.

The autonomous seven-day gate, representative browser-latency evidence,
incremental Story projection, animated travel presentation and every unchecked
item below still require their own implementation and sign-off evidence. A unit
test or one controlled delivery is not a release pass.

The next reliability slice now has executable decision paths for recurring
meal requests, next-day egg counteroffers, funded milk/firewood/wagon work,
Nella freight, recycling freight and demand-aware managed-storage movements.
Nella prioritizes personally known loadable orders before store prospecting;
depot choices require genuine protected-surplus deposits or a real affordable
workplace shortage for withdrawals. Existing physical channels, freshness,
payment and save/reload ledgers remain authoritative. Fresh multi-day
autonomous verification and the seven-active-day release gate are still open.

## P0: Build producer stockpiles and a general-store fallback

Make higher producer reserves and local resale the primary Generation 17 change. Producers should replenish these reserves even without an open order, rather than producing only when a buyer asks.

| Producer | Stockpile |
| --- | --- |
| Mara | Flour at the watermill |
| Ada | Grain at the farm |
| Iona | Herbs at the herbal workshop |
| Finn | Smoked or otherwise preserved fish, not extra raw fish |
| Rowan | Wood at the woodlot, tracking logs, timber, and firewood separately |

### Planned changes

1. Set higher, configurable reserve targets and storage caps by product. Produce toward those targets with real inputs and available work time, after meeting funded commitments. Keep enough grain for animal feed and enough raw fish for accepted fresh-fish orders. Replenish herbs and preserved fish based on usable stock, not lots that will expire before use.
2. Have Nella place funded purchase orders for some of the producers' **uncommitted surplus above their reserves**. Keep bounded resale stock at the general store. Stock becomes available only after physical delivery; ownership and payment must follow the actual trade.
3. When Elsie forecasts a shortage and a primary supplier cannot deliver in time, let her buy suitable, affordable stock from Nella. Include the time to collect or deliver it and finish any processing. Preserved fish counts toward supper only if an eligible recipe can use it in time. Keep the general store's resale stock distinct from any stock held at the communal granary.

### Acceptance criteria

- Each producer replenishes their configured reserve without an open order and stops at the target. Stockpiling does not divert committed goods, animal feed, or reserved fresh fish.
- Nella buys only affordable surplus within store capacity. Verify physical delivery and payment on restock and on a later sale to Elsie, without double-counting stock.
- In a fresh Tin Cans and String run, make a primary supplier unable to deliver in time. Verify Elsie can cover a real shortage through Nella when affordable, usable stock can arrive before the cutoff. Record producer and store balances, delivery times, and the resulting supper coverage. If neither source can meet the deadline, the forecast still shows the shortfall.

## P0: Prepare and protect supper

**Day 1 evidence:** In the Tin Cans and String run, Elsie cooked 14 portions by 18:50. Six cooked portions were eaten as daytime meals, leaving only eight for 12 supper diners. Torvin, Mara, Nella, and Rowan all arrived and tried to eat. Six loaves remained for the next morning's breakfast. The run did not include the communal granary; Nella's general store had grain and flour at reserve, but no bread. Cooking earlier would give Elsie time to recover, but would not prevent daytime diners from consuming supper stock.

### Planned changes

1. Check supper readiness by 15:00, then recalculate after stock, order, or production changes. Flag any shortfall early enough for Elsie to fund a bread order and for Jory to bake and deliver it before preparation ends. Treat forecasted deliveries as provisional until unloaded.
2. Aim to cook between 17:30 and 18:30 and have **12 fresh, funded portions ready and reserved at the tavern by 18:30** for 19:00 supper. Check freshness through each diner's service time, including Elsie's later meal. Count grain and flour as ingredients, not ready portions; count them toward the forecast only when milling, baking, and delivery can finish on time.
3. Protect supper before serving daytime meals. Count only stock usable for supper when determining the surplus, and recheck after each serving. Keep tomorrow's breakfast bread reserved separately; do not count the same food for both services.

### Acceptance criteria

- Reproduce the Day 1 shortfall and verify that daytime requests cannot consume the four additional portions needed for supper. Daytime service still works when there is genuine surplus.
- Verify that a projected shortfall is visible in the afternoon and that undelivered orders, raw ingredients, and food expiring before service do not count as ready portions.
- In a fresh Tin Cans and String run, record the 18:30 stock and allocations, then verify **12/12 supper meals**, no use of protected breakfast bread, and no loss of next-morning breakfast coverage.

## P0: Supply, meal and recovery reliability

### Renew recurring demand and keep services physically distinct

- [ ] Renew funded next-day food demand before the real cutoff. Convert an
  uncovered forecast into a personally communicated, accepted bakery/fish
  order with feasible production, loading, travel and settlement. Escalate or
  report refused, unanswered, stale or impossible requests while another source
  remains feasible.
- [ ] Keep breakfast, daytime meals and supper allocations separate and
  time-bound. Never spend supper portions on overdue breakfast or daytime
  service, and never count tomorrow's protected breakfast bread for today.
- [ ] Make readiness forecasts actionable: report ready, cookable, committed,
  delivered, eaten and uncovered portions separately, with the first blocker
  among request, production, input, freshness, funds, carrier, route, fuel,
  labor or cutoff.
- [ ] Verify 12/12 breakfast and 12/12 supper from real food, money, freshness,
  fuel, labor and cooking capacity. Cover valid late batches, fair allocation,
  exact blockers and honest scarcity without manufacturing food.
- [ ] Reconcile daytime food entitlement against supper without double-counting
  portions or payments, retaining daytime service only where genuine surplus
  exists.

### Next-day supply negotiation

- [ ] After a personally received request fails today's deadline or capacity
  check, offer a specific feasible next-day delivery and freshness time using a
  physical reply. Explain feed, care, equipment, route or quantity blockers
  rather than promising impossible supply.
- [ ] Deliver the counteroffer through a real conversation or readable board
  version. The buyer must decide whether next-day demand remains useful; an
  uncommitted request may be revised, declined or allowed to expire without
  extending today's deadline.
- [ ] Recheck stock, care, freshness, reserved output, funds and travel before
  accepting a revised counteroffer. Keep proposed, revised, accepted and expired
  states visible only when physically learned, with bounded evidence-driven
  retries.
- [ ] Reproduce the observed late two-egg request: Ada proposes tomorrow, Elsie
  revises or declines against next-day demand, and Ada accepts only if feasible.
  Cover unavailable feed, occupied capacity, stale replies, absent recipients,
  midnight persistence, and conservation of stock and funds.

### Crew, milk and logistics correctness

- [ ] Verify well, mill and wagon completion. Crew appointments must recover
  from expired windows, require current consent, tools, materials and both
  workers, and never turn one contribution or a posted notice into false
  completion.
- [ ] Verify the complete funded two-milk chain: accepted order, timed cow care,
  collection, capacity-safe reservation and loading, physical delivery, and
  exactly-once payment. Expose a blocker or renegotiation when a deadline cannot
  be met.
- [ ] Verify firewood delivery uses persisted routes and cargo validators,
  including two real trips totaling three units, funded settlement, late
  delivery and failure accounting.
- [ ] Verify wagon readiness includes repair, horse care/hitching, driver,
  accepted loadable cargo, capacity and return time. At least one shipment must
  complete, unload, settle once and return the horse; no-cargo days must not
  start fictitious circuits.
- [ ] Verify intent-level supply and crew communication resolves legal physical
  channels, versions, deadline revisions and expired successors without leaking
  unread notices or requiring technical-field guesses.
- [ ] Verify short legal waits, event-driven wakes and bounded review without
  repeated no-op loops or unsafe leisure suggestions.
- [ ] Verify day-keyed animal care, milk, water, horse and maintenance state
  across midnight and restart. Each input is consumed once and shortage is
  explicit.
- [ ] Verify the map, walk planner and advertised `go` targets share one
  authoritative route graph, with stable public site IDs, persisted routes,
  wagon position and carried cargo.

### Scheduler and model-output recovery

- [ ] Exercise duplicate and overlapping scheduler wakes, cancellation, action
  retries and save/reload boundaries. No duplicate settlement, negative
  inventory, lost work or state exception is permitted.
- [ ] Diagnose empty model output separately from invalid nonempty decisions.
  Retry transient empties within a bounded visible budget, then pause safely at
  the last confirmed action boundary with a resumable checkpoint. Never invent
  an NPC action or replay an uncertain request.
- [ ] Preserve useful diagnostics for blocked, failed, no-op, waiting, leisure
  and successful actions, including reason and relevant state evidence.

## P1: Economy, trade and useful downtime

### Local merchant offers

- [ ] When a producer personally encounters a merchant with a relevant offer,
  prefer feasible sale of genuine surplus before optional research, leisure or
  idle waiting, while retaining meal, rest, maintenance, accepted-order and
  reserve priorities.
- [ ] Evaluate an offer only from encounter-local facts: presence and departure
  time, product, price, demand, funds, cargo capacity, unreserved stock and
  freshness through the journey. Recheck terms at settlement and explicitly
  reject, defer or cancel on change.
- [ ] Preserve physical, funded and idempotent trade settlement. Bound quantity
  by surplus and demand, conserve stock and funds, preserve unsold goods and
  unmet demand, and never duplicate payment on retry.
- [ ] Add decision and end-to-end regressions for a valid one-loaf export and
  for reserved/stale stock, insufficient funds/capacity, absent merchant, due
  obligations and speculative imports without real demand.

### Nella's freight, visitor trade and store work

- [ ] Prioritize feasible, personally known freight before optional prospecting.
  Batch compatible errands using accepted orders, ready pickups, capacity,
  deadlines, payment and return time, and record a concrete blocker plus next
  meaningful check when work is unavailable.
- [ ] Permit Nella to seek a known visitor during free work hours only after
  learning an arrival, itinerary or plausible public check-in through a local
  encounter or notice. Revalidate presence, time and offers on arrival and
  abandon a missed encounter rather than tracking or cycling remotely.
- [ ] Support trade intermediation only through paid purchase or authorized
  consignment. Reconcile lot ownership, commission/margin, producer proceeds,
  merchant funds, capacity, freshness, returns and cancellation exactly once.
  Nella may not take proceeds merely for being present.
- [ ] Offer bounded legitimate store work when shipment and visitor options are
  unavailable: reconcile actual lots and commitments, place a valid physical
  procurement request, or inspect/maintain the counter. Do not add free stock,
  repeated no-change checks or speculative production.
- [ ] Validate no-visitor, missed-traveller, consigned-sale, insufficient-funds,
  capacity, expiring-cargo and save/reload outcomes. Reduce avoidable idle and
  travel time without losing deliveries, meals, animal care or sleep.

### Spoilage diversion and compost recycling

- [ ] Plan safe, time-aware diversion of personally known, still-fresh,
  uncommitted plant surplus to the farm before expiry. Respect reservations,
  orders, provenance, permission, ownership, shelf life, capacity and feed
  limits. Never feed fish, dairy, eggs, mixed/unknown ingredients or expired
  goods to animals; grain and cow hay remain required.
- [ ] Expose locally discovered spoiled material as a bounded compost pickup.
  A permitted carrier must load, travel and unload real material; farm compost
  then requires real work and inputs. Fish-origin waste remains outside feed
  pathways.
- [ ] Verify spoilage and recycling across days and saves: fresh diversion,
  missed pickup, successful compost collection and conversion, provenance and
  conservation. Cover no rotten feed, duplicated fertilizer, free grain,
  double payment or lost cargo.

### Resilient storage and transport

- [ ] Add stock- and demand-aware decisions for depot deposits and withdrawals.
  Move only genuine surplus or goods for a real affordable workplace need, and
  reconcile capacity, ownership, payment, cancellation and restart without
  unnecessary shuffling.
- [ ] Implement and verify reproducible multi-day Resilient Village continuity
  without daily stock reset, tracking site/owner quantities, spoilage, cargo,
  feed consumption, reserves, payments, stockouts, delayed returns and depleted
  storage.
- [ ] Decide and, if accepted, implement the handcart as an opt-in physical
  mechanic with ownership, loading, capacity, route access, parking, unloading
  and recovery. Until then it remains a fixture that carries nothing.
- [ ] Decide and, if accepted, implement the farm cistern as an opt-in finite
  water source with real filling, capacity, draw/consumption and livestock
  integration. Until then livestock continues to use the well.

## P1: Story and map presentation

### Incremental Story projection

- [ ] Maintain an ordered incremental Story projection with per-actor and
  combined views, preserving observation/decision/action-result pairing, team
  ordering, opening deduplication, day labels and final reviewed text.
- [ ] Persist a versioned projection and last event cursor. Resume after
  save/load, refresh, reconnect and restart; detect missing, duplicate,
  reordered, replaced or incompatible events and rebuild explicitly from the
  authoritative log.
- [ ] Serve Player/Admin Story from the projection and update it incrementally.
  Actor/day filters, copy and export must use canonical text, and applying a late
  review must not replay unrelated history or leak privileged events.
- [ ] Bound long-run storage and memory with indexed pages or actor/day slices
  without dropping history. Retain scroll/detail state and surface projection
  failures visibly with an explicit recovery path.
- [ ] Verify long-episode parity, bounded open/switch latency, save/restart
  continuity, old-run migration and failure recovery against the full-history
  formatter.

### Animated town map

- [ ] Author and validate a versioned visual route layer for each supported map:
  anchors, polylines, road/footpath distinctions, crossings, occlusion and
  scenario edge connectivity. Unmapped edges retain the static marker/off-map
  fallback.
- [ ] Add an authoritative bounded travel feed containing actor, origin,
  destination/edge, route or edge ID, start/end village time, completion or
  cancellation, and current location. Do not expose private intent or derive
  authoritative movement from a later snapshot.
- [ ] Interpolate movement from simulation time. Pause, speed, reconnect, replay,
  seek, closure, cancellation and snapshots must resync without guessed arrival,
  destination stock/location claims or destination interaction while in transit.
- [ ] Add locally bundled, redistributable original/licensed sprites with stable
  per-character appearance and provenance, directional walk/idle frames and a
  visitor/default fallback. Names and selection remain independent of artwork.
- [ ] Support smooth direction changes, overlap/crossing handling and legible
  movement at all zoom levels without unnecessary redraws. Provide reduced
  motion, keyboard selection, accessible status text and static fallback.
- [ ] Deliver the feature as an opt-in Millhaven view that does not alter clock,
  routing, travel cost, NPC knowledge or other scenarios, and does not require
  resetting the episode to toggle it.

## P2: Release verification and evidence

### Reproducible state and matched comparisons

- [ ] Save a reproducible Generation 7 starting-state/seed fixture with
  time-stamped inventory, orders, reservations, crew sessions, cargo and action
  diagnostics. Capture matched failures before changing rules; keep identified
  historical evidence separate from current deterministic fixtures.
- [ ] Add low-stock, late-delivery, delayed-crew, cancelled-sale, missing-input,
  expired-shipment and configured-weather/adverse cases. Scarcity may fail
  honestly, but undocumented creation, stranded cargo, double payment and state
  mismatch must fail.
- [ ] Replay the actual multi-day outcome with a bounded day-keyed ledger of
  opening stock, production, transfer, spoilage, allocation, consumption, missed
  deadlines, funds, feed and closing reserves. Report waits, rejected actions,
  wasted travel and usable capacity alongside service results.
- [ ] Run through at least the second midnight using a reproducible saved start,
  asserting 12/12 breakfasts and suppers when provisioned, three observed days
  of care and maintenance, inventory/payment conservation, no expired lots
  counted, and explicit outcomes for impossible provision.

### Seven-day reliability gate

- [ ] Run seven active consecutive days without resetting stocks, pending work or
  orders. Reconcile each site and owner from opening + production/receipts to
  consumption/shipment/spoilage and closing quantity; every adjustment must map
  to an event and every shipment to a funded settlement.
- [ ] Produce a Day 1 before/after report for meals, repairs, three firewood,
  two milk, wagon shipments, movement, communication errors, waits, animal care
  and 12/12 home sleep.
- [ ] Produce a seven-day report covering inventory, unmet demand, replenishment
  lead times, fuel, shipments, payments, stockouts and honest shortages.
  Include the saved fixture, rerun command, tests, confirmed root causes and
  remaining issues. Unit-test success or one unconstrained model run is not a
  release pass.
- [ ] Verify browser Story latency on a representative long episode, including
  Story open, actor/day switch, copy/export, reload, recovery and peak UI blocking.
- [ ] Demonstrate a fresh autonomous multi-day episode; controlled or teleported
  fixtures do not substitute for real movement, supplier scheduling and crew
  decisions.

## P2: Village history and optional expansion

### Persistent journals and village history

- [ ] Require each playable resident to author a distinct concise journal entry
  for each waking day before sleep, with real work, encounters, meals, problems
  and intentions. Reject duplicates and fabricated actor-at-midnight entries.
- [ ] Preserve complete journal entries in a durable published historical
  archive with author, original day/world and stable identity; retain the private
  family diary separately and do not leak it through public observations.
- [ ] Give overnight visitors/traders an authored journaling opportunity and a
  departure handoff; early departures receive a departure note rather than a
  fabricated overnight entry.
- [ ] Verify cross-generation history retention and physical read paths without
  importing unrelated live state, granting omniscience or treating journal text
  as authoritative completion evidence.

### Future trade and population decisions

- [ ] Extend mill and outside trade beyond grain-to-flour and temporary merchant
  visits only if a concrete route and demand model justifies it. Define finite
  goods, carrier capacity, lead times, prices and payment reconciliation and test
  conservation/retries across days.
- [ ] Decide whether named newcomers become physical residents. If accepted,
  implement homes, meals, work, relationships, scheduler/cast lifecycles and save
  migration together. Until then they remain chronicle/population entries, not
  simulated bodies.

## P3: Core-engine and game-integration roadmap

- [ ] Support multi-room navigation, persistent discovered maps, longer-horizon
  decomposition and bounded relevant context for a selected future adventure.
- [ ] Select and record a properly licensed/provenanced Colossal Cave Adventure
  implementation and its exact completion rule, version, randomness and initial
  conditions.
- [ ] Integrate the selected adventure behind the existing world contract
  without exposing undiscovered areas or solutions and without game-specific
  controller branches.
- [ ] Extend persistence and checkpoint reconciliation for verified long-running
  resume, and evaluate complete authoritative episodes with recorded score,
  actions, calls, elapsed time, stop reason and reproducible configuration.
- [ ] Evaluate automatic crash-resume, arbitrary remote-world admin adapters and
  simultaneous multi-agent execution as explicit core features; implement them
  only with compatible world/process and ownership contracts and appropriate
  atomicity, isolation and recovery tests.

## P3: Anti-idle gaps retained from the enhancement specification

- [ ] Decide and implement a general cross-occupation assistance commitment
  system so neighbor-help events can create durable, physically feasible work
  rather than only narrative/local assistance.
- [ ] Record garden manure permission explicitly where access or consent is
  required; do not assume carrying and use permission from co-location alone.
- [ ] Replace the fixed speculative stock cap with a scenario-configurable,
  demand/freshness-aware policy where supported.
- [ ] Add and pass a full scripted end-to-end replay of the originally inspected
  failed day, distinguishing engine recovery from a seeded/control-fixture pass.

## Generation 17 execution order

1. Add failing reproductions for producer-reserve/store fallback, supper
   protection, recurring food demand and the late egg counteroffer.
2. Correct the earliest food, order, crew, route and scheduler blockers without
   bypassing physical knowledge, stock, freshness, payment or consent.
3. Integrate and verify spoilage recycling, merchant choices and Nella's freight
   and downtime decisions against controlled ledgers.
4. Complete incremental Story and optional animated-map work with presentation
   parity, privacy, accessibility and long-run performance checks.
5. Run controlled same-world multi-day fixtures, then fresh autonomous episodes;
   reconcile meals, deliveries, maintenance, care, journals, stock and funds.
6. Complete the seven-active-day release gate and generate the before/after and
   remaining-risk reports. Optional P3 mechanics do not block a verified Millhaven
   reliability release unless explicitly promoted into Generation 17 scope.
