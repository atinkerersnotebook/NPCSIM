# Engine Change Backlog

This list records implemented, opt-in engine changes. The existing paused
adventure and legacy scenarios are unchanged. The living-village generator
creates a separate configured scenario; backend activation and starting a new
adventure are separate operations.

## Player and Admin UI redesign (implemented)

The current live village episode produces a roughly 20,000-pixel Player page
and 23,000-pixel Admin page at a 1,968-pixel viewport, dominated by stacked
village tracking and team panels. Redesign navigation without removing any
existing information, controls, or the distinction between player-visible and
privileged data. World view remains read-only.

- [x] Replace the long Player and Admin document stacks with explicit tabs:
  Player: Play, Cast, Story; Admin: Live, Cast & setup, Story, Technical.
  Give village tracking and team perspectives a discoverable home without
  recreating the full-page scroll inside a single tab. Keep the active tab in
  the URL hash so refresh and direct links restore it; default to Cast/Cast &
  setup when there is no episode and Play/Live when one exists.
- [x] Keep a compact masthead and command bar accessible while scrolling:
  brand, episode title and live status, game clock, connection state, view
  switcher, scenario picker, and applicable Play, Step,
  Pause, Continue, Save, and Load actions. Remove the icon rail and put Archive
  and Export JSON in the command bar. Preserve state-dependent visibility and
  disabled states, keyboard access, and responsive layouts.
- [x] Preserve the existing theme and fonts (the later design direction
  supersedes the proposed broadsheet/theme toggle). Clamp long goal objectives
  to five lines with an accessible expand/collapse control revealing full text.
- [x] Give the technical log, self panel, proxy settings, and narrative fixed
  layout homes instead of moving DOM nodes on each render; simplify the team
  perspectives rendering accordingly. Preserve panel contents, focus, form
  state, story scroll position, and live updates across tab switches.
- [x] Keep the World view's `?budget=extend` link landing on the visible budget
  editor in Player/Admin after tab navigation. Test no-episode defaults,
  hash refresh/deep links, command availability, goal
  expansion, tab accessibility, privacy boundaries, and desktop/mobile
  scrolling. Verify each tab fits a manageable viewport-level layout without
  hiding lengthy logs, stories, or village data behind an unbounded page.

## Incremental adventure story building (future / open)

The live Story feed and the readable adventure narrative currently rebuild from
the full event history during page rendering, actor/day selection, and copying.
Long-running episodes make opening Story and switching views too slow. Build
and retain the story as the simulation advances instead of replaying every
event when the page is selected. This is separate from the UI redesign above.

- [ ] Maintain an ordered, incremental story projection as events are appended,
  with per-actor and combined views. Preserve the existing observation,
  decision, action-result pairing, team ordering, opening deduplication,
  timeline/day labels, and final reviewed-story text. Allow a result or review
  to revise an earlier paragraph without reprocessing unrelated history.
- [ ] Persist a versioned projection and its last processed event sequence with
  each run. Resume from that cursor after save/load, refresh, reconnect, or
  process restart; detect missing, duplicate, reordered, or replaced events
  and rebuild explicitly when the projection is stale or incompatible. Keep
  legacy runs readable through a one-time migration/rebuild, not a rebuild on
  every visit.
- [ ] Serve the current story to Player/Admin from the projection and update it
  as new events arrive. Actor/day filters and copy/export must use the same
  canonical text, with the final completion review applied once it arrives.
  Avoid rebuilding all team perspectives or replacing the full Story DOM for
  each new event; retain scroll position and expanded details. Do not leak
  privileged events into player-visible output.
- [ ] Bound storage and memory for long episodes (for example, indexed day or
  actor slices and paged retrieval) without dropping story history. Surface
  projection failures explicitly and keep the original event log authoritative
  for recovery and assessment.

### Acceptance checks

- A long-running, multi-actor episode's live and reviewed stories match a
  full-history build in order, wording, day labels, and copy/export output,
  including late action results and duplicate openings.
- Opening Story, switching actor/day, and returning after reload do not replay
  the entire history; measure latency against a representative long episode
  and verify it stays bounded as the event log grows.
- Save/load, reconnect, process restart, and an old-run migration neither lose
  nor duplicate paragraphs; corrupt or outdated projections recover from the
  authoritative event log with a visible error if recovery fails.

## Animated town map and personalized walking NPCs (future / open)

Upgrade the Millhaven World view from static, location-grouped name markers to
an optional animated map. NPCs should walk the existing **predefined routes**
between locations, using recognizable character-specific sprites and clothing.
This is a presentation enhancement, not a change to NPC decisions, world travel
rules, or the authoritative location. Keep existing map zoom, stock tooltips,
selection/details, dialogue, visitor handling, and off-map reporting. Coordinate
with the Player/Admin UI redesign above without making that redesign a prerequisite.

- [ ] Author a versioned visual layer for each supported map image: location
  anchors, walkable route polylines/waypoints, road/footpath distinctions,
  crossings and draw order/occlusion. Map each segment to an actual world route
  edge and validate endpoints/connectivity against the scenario; do not infer
  walkable ground from the illustration or draw straight lines through buildings,
  water, or walls. Unsupported or unmapped edges retain the current location-level
  marker/off-map fallback, with no fabricated movement.
- [ ] Define the minimum authoritative travel feed before animating: actor ID,
  origin, destination/edge, route or edge ID, travel start/end village minutes,
  completion/cancellation, and current authoritative location. If existing move
  events only report arrival, add compatible, bounded travel metadata at the
  world/API boundary rather than deriving a departure or duration from a later
  snapshot. Do not expose hidden intentions, private plans or privileged data to
  player-facing views. Preserve existing save/replay and old scenario contracts.
- [ ] Interpolate position along the authored polyline from simulation time,
  not elapsed wall time alone. Stop or resync on pause, speed changes, reconnect,
  seek/replay, route closure, cancelled travel, and new authoritative snapshots;
  late or missing events must snap to the last verified location rather than
  guessing. Distinguish an in-transit actor from one at a site: no early arrival,
  stock/location claims, speech bubble, or indoor interaction at the destination.
- [ ] Add a small, locally bundled sprite set with idle and directional walk
  frames. Use an explicit per-character appearance profile (e.g. hair, clothing
  palette/garment, accessory), stable across runs and reloads, with a generic
  visitor/default sprite when no profile exists. Art must be original or licensed
  for redistribution, with provenance recorded; avoid external asset requests.
  Keep readable names and selection targets independent of sprite artwork.
- [ ] Implement smooth direction changes and legible walking speed while keeping
  the map usable at all zoom levels and narrow screens. Handle overlapping
  walkers, crossings, clustered destinations and speech without covering site
  controls. Respect reduced-motion preferences (static location/in-transit
  labels), provide keyboard selection and accessible status text, and keep the
  current static map as a fallback for unsupported browsers or maps.
- [ ] Introduce this as an opt-in view for Millhaven first; do not alter the
  village clock, routing, travel costs, NPC knowledge or other scenarios.
  Measure frame smoothness with the expected resident/visitor count and avoid
  rebuilding every marker on each animation frame. Keep an easy static/animated
  toggle without resetting the episode.

### Acceptance checks

- A resident taking multiple connected route edges follows the correct authored
  path and reaches each waypoint only when the corresponding authoritative
  travel permits it; an interrupted or blocked edge never shows a completed trip.
- Pause, speed change, refresh, reconnect, saved-run reopen and historical replay
  cannot make sprites drift, teleport to a guessed destination or duplicate a
  journey. Static mode and legacy/non-village worlds retain today's behavior.
- Two distinct residents display stable, recognizable clothing/appearances;
  missing profiles and unpictured routes degrade visibly and safely. Selection,
  stock, dialogue, privacy, reduced motion and keyboard use remain correct.

## Demand-driven supplier orders

- [x] Add an order system so businesses request supplies based on demand instead
  of accepting continuous, unsolicited production.

### First use case: the Millhaven inn

Elsie plans upcoming meal service and places orders with:

- Jory, the baker: bread for planned cooking and any configured bread service.
- Finn, the fisher: fish for planned fish meals, delivered close to cooking time.
- Iona, the herbalist: prepared tea, or herbs if the inn supports tea preparation.

### Requirements

- Calculate order quantities from planned recipe requirements and a small stock
  buffer, minus usable stock and confirmed incoming deliveries; never order a
  negative quantity.
- Count stock as usable only if it remains fresh through its intended use.
- Treat bread-based and fish-based meals as alternatives in the meal plan, so
  both suppliers are not independently ordered to cover the same diners.
- Record buyer, supplier, destination, product, quantity, agreed price, delivery
  deadline, and required freshness.
- Track order proposal, supplier acceptance or rejection, delivery progress,
  fulfillment, cancellation, and failure explicitly.
- Let accepted orders guide supplier production and carrier delivery planning.
- [x] Require the buyer to find and meet the actual supplier, then state the order
  requirements through the in-person ordering action. Demand planning must not
  create remote orders, including upstream ingredient purchases.
- Prevent duplicate commitments and deliveries, including after retries or
  recovery; track remaining quantities for partial fulfillment.
- Check affordability, including delivery fees, before committing purchases.
- Pay for accepted delivered quantities exactly once; surface insufficient
  funds, stale goods, shortages, and missed deadlines explicitly.
- Support consolidated shipments without losing individual order accounting.
- Expose the NPC's own buying/selling orders in observations and all orders in
  privileged monitoring.
- Preserve real production, travel, stock movement, perishability, meals,
  sleep, and animal-care rules.

### Acceptance criteria

- Existing usable inventory and confirmed incoming goods reduce new orders.
- Goods expiring before service do not satisfy that service's requirements.
- Repeated planning or retried delivery cannot duplicate an order or payment.
- Partial, late, rejected, or cancelled deliveries have visible outcomes.
- Suppliers can stop production once accepted demand and reserve targets are met.
- Remote or sleeping-supplier order attempts create no orders or financial
  commitments; a successful in-person request records the spoken requirements.

### Next-day supply negotiation (open)

A same-day egg request can miss the remaining collection window. The physical
reply API now permits a conditional next-day counteroffer, but Ada and Elsie do
not yet reliably complete that negotiation on their own. A counteroffer is not
an accepted order, and today's supper demand must not silently move to tomorrow.

The engine now persists structured counteroffer terms, checks real next-day hen
feed, care equipment, collection capacity and travel before offering, and gives
Ada and Elsie physically learned fallback choices to propose, revise, decline
or accept. Focused deterministic tests cover absent recipients, feed shortages,
midnight persistence, and unchanged stock/funds before fulfillment. A seeded
multi-resident autonomous model run and full service outcome remain unverified;
do not treat these checks as a release pass.

- [ ] After a personally received supply request fails its deadline/capacity
  check, offer a specific next-day delivery and freshness time when a real
  collection/production and transport window could support it. Use the existing
  physical reply action, not narrated agreement or a repeated failed accept;
  if care, feed, equipment, route or quantity makes tomorrow implausible, explain
  the blocker instead of promising supply.
- [ ] Let the buyer receive that counteroffer through a real conversation or
  inbox/board check and explicitly decide whether the need remains useful the
  next day. For today's supper, seek a feasible same-day alternative or record
  uncovered meals; do not extend today's service deadline just to make the
  request acceptable. If useful, revise the existing uncommitted notice's
  deadline and freshness terms rather than posting a duplicate.
- [ ] After the supplier receives the revised version, recheck actual stock,
  care, freshness, reserved output, funds and travel before accepting. Keep
  proposed, revised, accepted and expired states visible to each resident only
  when physically learned; bound retries and wake decisions on new evidence
  rather than repeated visits or acceptance attempts.
- [ ] Reproduce the observed two-egg request after today's collection session
  is unavailable: Ada proposes tomorrow, Elsie receives and either revises or
  declines based on tomorrow's demand, and Ada accepts only a feasible revised
  request. Test unavailable feed, occupied next-day capacity, stale/expired
  replies, absent recipients, persistence across midnight, and conservation of
  stock and funds. Require no automatic acceptance, duplicate order or phantom
  eggs; verify autonomous choices in a seeded run, not only direct API calls.

## Demand-aware production and reserve targets

- [x] Limit production to accepted orders, forecast consumption, and a small
  maintenance or stock buffer.
- [x] Apply targets to bread, meals, tea, planks, and wagon parts, rather than
  requiring continuous production whenever a cooldown ends.
- [x] Include usable inventory and committed production when calculating unmet
  demand, so concurrent producers cannot independently overfill the same target.
- [x] Allow producers to pause or perform other supported work when demand is met.

## Breakfast and meal planning

- [x] Add configurable breakfast after waking alongside the existing supper.
- [x] Track each meal's due time, consumption, ingredients, and payment separately.
- [x] Plan portions against the expected diners and chosen recipes; breakfast
  should not simply be added on top of already excessive production.
- [x] Preserve existing single-meal scenarios unless they opt into new schedules.

## Sustainable meal pricing

- [x] Align actual meal charges with recipe costs, delivery costs, and intended
  margin; distinguish service charges from tradable product prices.
- [x] Support explicitly funded subsidies if meals should remain below cost,
  rather than silently draining the innkeeper's account.
- [x] Surface unaffordable purchases and meals without hiding unpaid costs.
- [x] Decide prices or subsidy policy before implementation; the suggested
  two-coin bread-based meal is an example, not a fixed engine rule.

## Just-in-time cooking and freshness

- [x] Schedule cooking near meal service instead of accumulating meals all day.
- [x] Prefer the earliest-expiring usable ingredients and portions.
- [x] Forecast what will expire before the next planned use, and expose that
  quantity separately from usable stock.
- [x] Reduce or stop production when existing fresh stock covers service.
- [x] Keep spoilage real and recorded; do not extend shelf lives to disguise
  overproduction.

## Consolidated deliveries and end-of-day cargo

- [x] Group confirmed, compatible orders into capacity-aware delivery runs.
- [x] Avoid redundant small trips and expose their travel and delivery-fee costs.
- [x] Require confirmed demand and affordability before loading goods.
- [x] Resolve remaining cargo by delivery, return, or explicit overnight storage
  with ownership and freshness preserved; stopping a route is not fulfillment.
- [x] Preserve actual travel, unloading, and animal-care requirements.

## Raw-material replenishment

- [x] Define minimum reserves and replenishment triggers for grain, flour, iron,
  logs, hay, and other recipe inputs.
- [x] Protect needed raw materials from conversion into unnecessary finished
  stock, especially spare wagon parts and planks.
- [x] Plan harvesting, processing, procurement, and manure use around the next
  day's needs.
- [x] Respect renewable-resource rules, availability, lead times, and payments;
  never replenish resources by silently creating stock.

## Service schedules and useful off-peak time

- [x] Give the innkeeper explicit planning, preparation, and serving windows.
- [x] Use off-peak time for supported coordination and stock management, or
  legitimate rest, rather than forcing additional cooking.
- [x] Add any new secondary work as explicit capabilities with real outcomes;
  do not count narrative-only cleaning or customer service as completed work.
- [x] Preserve personal meals, bedtimes, and time at home when assigning work.

## Event-driven waiting and fewer model calls

- [x] Skip unnecessary model decisions while an actor awaits a known cooldown,
  delivery, or scheduled service.
- [x] Make actors eligible again at relevant events, including meal deadlines,
  bedtime, supply arrivals, and changed commitments.
- [x] Avoid repeated short wait loops and redundant reads or announcements when
  no actionable information has changed.
- [x] Preserve meaningful decisions, urgent interruptions, recovery safety, and
  chronological action history; do not jump past required activities.

## Demand-led completion and daily care

- [x] Ensure hay replenishment and animal readiness are scheduled and verified
  alongside meals, homecoming, sleep, and safe route shutdown.
- [x] Review mandatory tea use: make it a meaningful need or an explicit
  scenario objective, rather than forcing consumption solely to clear a flag.
- [x] Review production goals against actual demand and reserve needs so success
  does not reward unnecessary output.
- [x] Keep intentional completion-rule changes explicit and scenario-controlled.

## Economy and efficiency reporting

- [x] Show produced, consumed, delivered, spoiled, and remaining quantities by
  product and business, including cargo and upcoming expiry.
- [x] Report gross sales, purchases, delivery fees, subsidies, net cash change,
  and inventory value separately; define valuation before reporting profit.
- [x] Distinguish requested wait time, production cooldowns, travel, actual work,
  rest, and sleep instead of labeling every wait as wasted time.
- [x] Show pending orders, reserve shortages, projected meal coverage, and
  overproduction warnings.
- [x] Track model calls spent on repetitive waiting and coordination to measure
  the benefit of event-driven scheduling.

## Personal interests, relationships, and evening leisure

- [x] Give each villager distinct interests, starting with one solo interest and
  one social interest rather than identical evening routines.
- [x] Support time-consuming solo activities such as reading, gardening, and
  crafting, and social activities such as visiting, games, storytelling, and
  shared tea.
- [x] Track preferred companions and relationship changes from actual shared
  activities, not unsupported narrative claims.
- [x] Support small personal projects and skill development across days.
- [x] Choose leisure according to interests, available time, companions, fatigue,
  resources, and affordability; legitimate rest remains a valid choice.
- [x] Represent activities explicitly in observations, history, and the Gantt,
  with duration, location, participants, and any real resource consumption.
- [x] Schedule completion and meaningful interruptions without repeated model
  calls during an unchanged activity.
- [x] Allow evening outings while preserving real travel home by each person's
  bedtime, adequate sleep, meals, and animal care.
- [x] Make new leisure behavior scenario-controlled; hobbies are optional, not
  production quotas or mandatory completion conditions.

### Proposed personal project: Iona's herbal book

- [x] Give Iona, the herbalist, a multi-day project to write a book about local
  plants, their identification, and Millhaven's herbal traditions.
- [x] Let her use free time for field observations, organizing notes, drafting,
  and revising, while preserving supply commitments, meals, and sleep.
- [x] Persist completed observations, notes, and chapter progress across days;
  repeated activity must not duplicate progress.
- [x] Connect research to actual exploration and encounters, and allow her to
  discuss her work with interested villagers.
- [x] Show writing sessions and project milestones in observations, the story,
  and the Gantt instead of treating writing as generic waiting.
- [x] Keep the project optional and gradual; do not require a finished book each
  day or treat fictional herbal lore as real-world medical advice.

## Evening band at the inn

- [x] Allow interested villagers to form a small band and arrange evening
  performances at the inn, with Elsie coordinating the venue.
- [x] Define willing members, musical roles, skills, and available instruments;
  leave the cast and instrument choices open until scenario design.
- [x] Model rehearsals and performances as coordinated activities with explicit
  commitments, shared start and end times, and physical attendance.
- [x] Check member availability and instrument access before confirming a show;
  surface cancellations or missing participants rather than inventing a
  completed performance.
- [x] Let other villagers choose to attend according to interests and
  relationships; do not make attendance compulsory.
- [x] Coordinate music with supper service so performers and listeners can eat,
  and finish early enough for everyone to travel home before bedtime.
- [x] Record actual participation and any defined enjoyment, relationship, or
  skill outcomes; attendance must not automatically consume food or tea.
- [x] Make any performer fees or tips explicit, funded transactions if included;
  do not assume the cash-strapped inn can pay for entertainment.
- [x] Show rehearsals, performances, and attendance in the story and Gantt.

## Minor village events and DM configuration

- [x] Add small, recoverable disruptions and opportunities that create real
  tasks, delays, orders, or discoveries rather than narration-only outcomes.
- [x] Start with passing travellers, brief weather changes, minor equipment
  repairs, and personal discoveries.
- [x] Extend the catalogue with safely escaped animals, disappointing fishing,
  seasonal harvest opportunities, visiting merchants, misplaced tools,
  birthdays, band-member unavailability, and requests for neighbourly help.
- [x] Use a suggested default of zero to two minor events per day, usually
  affecting one to three villagers for 15-60 game minutes; quiet days are valid.
- [x] Expose events through local observations and actual communication rather
  than giving every NPC immediate knowledge.
- [x] Let affected villagers adapt plans, ask for help, substitute supplies, or
  decline optional requests.
- [x] Prevent event stacking from making required meals, animal care, safe
  homecoming, and adequate sleep impossible.
- [x] Persist event selection, activation, progress, and resolution so retries,
  recovery, and resuming do not duplicate events or their effects.
- [x] Show affected actors, schedule changes, and outcomes in observations,
  the story, and the Gantt.

### DM controls and additional event types

- [x] Let the DM enable or disable event types and configure frequency, daily
  limits, intensity, duration, eligible time windows, affected locations and
  residents, and cooldowns between events.
- [x] Allow the DM to raise event limits when appropriate, while keeping
  feasibility safeguards and making configuration changes explicit.
- [x] Support scheduled or condition-triggered events alongside random events,
  with reproducible random selection for testing and replay.
- [x] Let the DM define additional event templates with prerequisites,
  discoverability, bounded effects, available responses, resolution conditions,
  and expiry or cancellation behavior.
- [x] Validate custom templates against supported engine actions and state
  changes; reject unsupported effects explicitly instead of inventing outcomes
  or executing arbitrary code.
- [x] Require stock and money changes to follow explicit accounting rules, and
  new visitors or resources to have recorded sources.
- [x] Make event configuration and changes inspectable, and preserve existing
  scenarios unless they opt into the event system.

## Temporary visitors and timeline presence

- [x] Give visiting travellers and merchants named identities and a Visitor
  label, distinct from permanent residents.
- [x] Add a temporary Gantt row showing actual arrival, departure, travel,
  trading, meals, visits, resident interactions, and overnight stays.
- [x] Retain departed visitors in historical timeline views while removing them
  from the current people-in-town list only after actual departure.
- [x] Keep overnight guests present until they leave, and record lodging
  commitments and any payments explicitly.
- [x] Apply appropriate visitor needs without automatically assigning resident
  homecoming or village-work completion requirements.
- [x] Persist visitor identity and presence across recovery so an event retry
  cannot create duplicate people or erase their history.

## Travelling merchants and surplus exports

- [x] Let merchants buy genuine surplus from producers and offer scarce imports,
  with product interests defined per merchant rather than universal purchasing.
- [x] Give each merchant finite funds, cargo capacity, available stock, offered
  prices, and an arrival and departure schedule.
- [x] Let producers accept or reject offers; protect local consumption, accepted
  orders, and reserve targets before treating stock as exportable surplus.
- [x] Require enough remaining shelf life for the intended journey and resale;
  nearly expired meals must not become automatically saleable exports.
- [x] Complete transactions through actual goods transfer, loading, and funded
  payment before departure, without duplicating payments after retries.
- [x] Handle unmet trade commitments explicitly when time, stock, capacity, or
  funds run out; do not silently mark them fulfilled.
- [x] Allow scenario-defined merchants to buy goods such as spare planks, wagon
  parts, tea, or fresh bread, and bring inputs such as iron or grain.
- [x] Keep merchant demand occasional and bounded so export opportunities do
  not become guaranteed sales that reward continuous overproduction.
- [x] Expose offers, trades, cargo, and departure deadlines to relevant NPCs and
  monitoring views; include export income and import costs in economy reports.
- [x] Let the DM configure merchant visits and trade profiles through the event
  system, with explicit sources for incoming goods and funds.

### Local merchant offers versus optional research (open)

In a Millhaven run, Tamsin Reed asked Jory about trade at his bakery at 11:00.
The bakery held one bread and Tamsin sought bread at three coins each, but Jory
chose flatbread research and no trade was settled. An enquiry is not a purchase:
the resident must deliberately use the local merchant-trade action. Improve
that choice without making every visit a guaranteed sale or treating iron as a
useful bakery purchase.

- [ ] When a producer personally encounters a merchant with a relevant offer,
  consider a feasible sale of *genuine surplus* before optional research,
  leisure or idle waiting. Due meals, rest, maintenance, accepted orders,
  protected reserves and other time-critical obligations still take precedence.
  A purchase of imports requires a real local use or committed demand; do not
  buy iron merely because the merchant offers it or the resident has coins.
- [ ] Evaluate the offer using only information available at the encounter:
  merchant presence and departure time, offered product and price, remaining
  demand, funds and cargo capacity, local unreserved stock, and freshness through
  journey and resale. Recheck all terms at the actual trade action; reject or
  defer with a specific reason if any term changes. Do not infer a merchant's
  remote whereabouts or replace the resident's choice with automatic transfer.
- [ ] Keep the existing physical, funded, idempotent trade settlement. Bound
  quantity by surplus and demand (one loaf in the observed example), debit the
  merchant and credit the actual producer exactly once, move real stock, and
  preserve unsold goods and unmet demand if the offer is declined or expires.
- [ ] Add focused decision and end-to-end regressions: with Jory and Tamsin
  co-located and one unreserved fresh loaf, the resident sees the offer and
  chooses/settles a one-loaf export before optional research; with reserved or
  stale bread, insufficient merchant funds/capacity, absent merchant, or due
  obligations, no sale occurs. Verify that no useful demand means no speculative
  iron import, retries do not duplicate stock/payment, and other merchants and
  existing scenarios retain their intended behavior.

## Nella's merchant downtime (open)

Nella's deliveries and the visitor trade action exist, but a quiet delivery
ledger can leave her waiting at the store. A visitor currently trades against
stock at the encounter site; Nella cannot sell a producer's goods merely by
meeting a traveller elsewhere. Keep random visitors occasional, and do not
turn an idle spell into guaranteed demand or invisible commerce.

- [ ] Prioritize feasible, personally known freight before optional prospecting:
  check accepted orders, ready pickups, wagon/foot capacity, delivery deadlines,
  payment and return time; batch compatible errands. When stock or a response
  is missing, record the specific blocker and next meaningful check instead of
  repeating empty trips between the square, farm and store. Count useful
  delivery, failed travel, and genuine downtime separately.
- [ ] Let Nella seek a *known* visitor during free work hours: learn of an
  arrival or itinerary through a local encounter, a read notice, or a planned
  public check-in, then walk to a plausible meeting site. Revalidate the
  visitor's actual presence, remaining time and offers on arrival; abandon or
  defer the search after a missed encounter rather than tracking visitors
  remotely or walking a perpetual circuit. Routine deliveries, meals and rest
  take precedence. An ordinary traveller may have a specific funded request,
  but is not automatically a merchant or a buyer of all goods.
- [ ] Make Nella a legitimate trade intermediary only through explicit terms:
  acquire unreserved producer stock by paid purchase or authorized consignment,
  physically carry it to the encounter, and settle the visitor's finite offer
  from that stock. Record lot ownership, agreed commission or margin, producer
  proceeds, merchant funds, capacity, freshness, unsold returns and cancellations
  exactly once. Alternatively, an owner may trade directly when co-located;
  Nella must not collect their proceeds simply for being present. Handle imports
  with the same funded, physical route to a real buyer or store demand.
- [ ] Offer bounded store work when no shipment or credible visitor lead is
  feasible: reconcile the local ledger against actual lots and commitments,
  arrange a needed procurement request through a valid physical channel, or
  inspect/maintain the counter. Only add stocking, sorting or packaging as
  productive work with an authorized source, real inputs, time, capacity and
  measurable demand; no free stock, repeated no-change checks or speculative
  research that consumes reserved grain.
- [ ] Validate an opt-in multi-day scenario with no visitor, a missed traveller,
  a successful consigned sale, insufficient funds/capacity, expiring cargo and
  save/reload during a trip. Compare Nella's wait and wasted-travel minutes to
  the same seeded baseline; require fewer avoidable idle/travel minutes without
  losing required deliveries, meals or sleep. Reconcile stock, ownership and
  payments across every outcome, and leave existing scenarios unchanged unless
  the new behavior is enabled.

## Optional NPC sidequests

- [x] Connect optional sidequests to personal interests, relationships, visitors,
  and village events rather than adding mandatory daily work.
- [x] Record each quest's giver or origin, eligible participants, objectives,
  prerequisites, progress, any deadline, and explicit rewards.
- [x] Let NPCs accept, decline, defer, or abandon quests according to interests,
  relationships, available time, fatigue, resources, and existing commitments.
- [x] Track offered, accepted, deferred, completed, abandoned, expired, and failed
  outcomes explicitly; support multi-day progress without blocking ordinary
  village completion.
- [x] Verify progress and completion through actual supported actions and world
  state, not dialogue claims; reuse personal-project and event progress where
  relevant instead of counting the same achievement twice.
- [x] Support rewards such as relationship changes, knowledge, skill progress,
  items, or funded payments, with explicit accounting and eligibility rules.
- [x] Persist progress and grant rewards exactly once across retries and
  recovery.
- [x] Protect meals, accepted work and delivery commitments, animal care, travel
  home, and adequate sleep when planning quest activities.
- [x] Expose quest offers, choices, progress, and outcomes in relevant NPC
  observations and monitoring views; show actual quest activities in the Gantt.
- [x] Let the DM configure or introduce quests with validated objectives,
  supported actions, bounded effects, and achievable rewards.

### Proposed Millhaven examples

- Iona documents an unusual plant for her herbal book.
- Jory develops a special bread recipe for the band's first performance.
- Torvin repairs a traveller's instrument before departure.
- Finn scouts a new fishing spot and shares his findings.
- Elsie arranges an evening gathering with musicians and refreshments.

The living variant includes these examples with their required capabilities and
fixtures. Completion still requires verified actions, never automatic narrative
success.

## Implemented feature groups

1. Supplier orders and demand-aware production.
2. Breakfast planning, meal economics, and just-in-time cooking.
3. Delivery consolidation and raw-material reserves.
4. Service schedules and event-driven waiting.
5. Completion-rule review and economy reporting.
6. Personal interests and social activities, followed by the evening band.
7. Minor village events and DM-configurable event templates.
8. Temporary visitor timelines and travelling-merchant trade.
9. Optional NPC sidequests linked to interests, events, and visitors.

All feature groups are implemented behind explicit scenario/runtime configuration.
Breakfast and meal-pricing policies remain separate from supplier ordering.
Prices, subsidies, event frequency, interests, band membership, and quest
templates remain configurable design choices, not universal engine defaults.
This implementation does not migrate, reset, resume, or advance the saved run.

Validation covers engine behavior, the real MCP configuration tool, existing
village/DM/API regressions, visitor and activity timeline rendering, and the
TypeScript production build. See README for generating the living variant and
granting the DM configuration authority.

## Generation 8 reliability release gates (open)

These are outstanding *verification and any resulting corrections*, not a claim
that each behavior is broken. The Generation 8 developer specification is in
`NPCSIM_Generation_8_Reliability_and_Village_Operations_Developer_Spec.md`.
Confirm root causes from reproducible state snapshots and tests before changing
engine rules. Preserve local NPC knowledge, physical movement and ownership,
funded transactions, one-time initial stock, and existing successful repairs,
deliveries, maintenance, and home sleep. Do not add professions, goods, or free
daily replenishment to meet the targets.

**Verification snapshot (2026-11-15):** Focused village completion, economy,
livestock, depot, legacy, release-evidence, communication and meal-planning tests
passed (250 passed, 1 skipped). Additional firewood, wagon-route, scheduler,
fallback, event and legacy tests passed (144 passed). The UI tests passed
(214 passed), and the TypeScript production build succeeded. These are isolated
regressions, not a matched Generation 7 replay, a completed autonomous
12/12-service episode, or a seven-active-day stock reconciliation. Available
local living/resilient episode summaries are cancelled, errored or paused; none
records a successful completed village episode. The one skipped test requires
an unavailable archived Day 3 baseline. No release checkbox below was closed
from test counts alone. Do not modify the paused episode to manufacture evidence.

### P0: Day-one outcomes and engine correctness

- [ ] Save a reproducible Generation 7 starting-state/seed fixture and capture
  time-stamped inventory, orders, meal reservations, crew sessions, cargo, and
  action diagnostics at each reported failure. Compare the same state and model
  settings where possible; do not infer a root cause from narrative logs.
  A deterministic current `millhaven-living` baseline and offline evidence
  capture utility now exist in `data/fixtures/generation8-baseline` and
  `npc_sim/village_release_evidence.py`. Neither is an identified Generation 7
  starting snapshot; no reported failure has yet been captured from a matched
  run. Keep this gate open until those missing observations are supplied.
- [ ] Verify 12/12 breakfast and 12/12 supper with real food, money, freshness,
  fuel, labor, and cooking capacity. Forecast ready versus cookable portions and
  uncovered diners early enough to act; count each ingredient or incoming order
  once. Allow a final valid batch to finish before service, distinguish a true
  shortage from a cooking-window or fair-allocation defect, and report exact
  blockers without creating food. Cover MEAL-01 through MEAL-06.
  An archived day-one run shows two portions still short at 19:00, with some
  earlier attempts marked missed before their due time despite later recovery.
  Early unallocated supper now remains pending; the 12/12 outcome, cooking
  window and supply chain still need matched end-to-end verification. At 18:55
  the archived inn observation had seven meals already eaten or prepared and
  three bread at the inn, but no fish; its readiness forecast counted only one
  cookable portion from a whole two-portion bread batch. An isolated, persisted
  regression confirmed that odd demand incorrectly blocked the last valid
  batch. Batch limits and readiness now count that batch and its real inputs;
  the archived run cannot be replayed from observation alone, and missing fish
  and actual NPC decisions remain unverified blockers to 12/12.
- [ ] Verify well, mill, and wagon completion. Accepted crew appointments must
  start promptly once consenting workers, supplies, and tools are at the site;
  expired notices must be supersedable, and one partner's contribution must not
  make the other falsely busy. Preserve physical consent and actual breakdown
  timing. Cover CREW-01 through CREW-06.
- [ ] Verify the complete funded two-milk chain: accepted order, cow care,
  collection into farm stock, reservation, load after capacity is freed, real
  transport, tavern unload, and exactly-once payment. Keep on-hand, reserved,
  in-transit, delivered, and spoiled quantities distinct; expose an actionable
  blocker or renegotiation when a deadline becomes impossible. Cover MILK-01
  through MILK-06.
- [ ] Exercise duplicate and overlapping scheduler wake events, cancellation,
  action retries, and save/reload boundaries. No exceptions, duplicate
  settlements, negative inventory, or lost work; classify blocked, failed,
  no-op, waiting, leisure, and successful actions separately. Cover SCHED-01
  through SCHED-03 with useful action/state diagnostics.

### P1: Logistics and actionable planning

- [ ] Verify `village_execute_fuel_delivery` follows the same persisted walking
  route and cargo validators as ordinary `go`, including loaded routes, valid
  replans, late delivery, and two trips totaling three real firewood units.
  Preserve cargo and payment accounting on failure. Cover FUEL-01 through
  FUEL-05.
- [ ] Verify wagon readiness exposes repair, horse care/hitching, driver, real
  accepted and loadable pickups, capacity, and return time. No-cargo days must
  not repeatedly start a fictitious circuit; at least one farm-to-mill shipment
  must load, travel, unload, settle once, and end with the horse returned and
  released. Cover WAGON-01 through WAGON-04.
- [ ] Verify intent-level supply and crew communication resolves legal physical
  posting/reply locations, versions, deadline revisions, and expired successors
  without revealing unread notices or forcing technical-field guesses. Cover
  COMM-01 through COMM-05.
- [ ] Verify short legal waits near commitments, event-driven wakes on personally
  accessible changes, and bounded review without repeated five-minute no-op
  loops or unsafe leisure suggestions. Cover WAIT-01 through WAIT-05.
- [ ] Verify day-keyed cow, hen, horse, milk, water, and maintenance status across
  midnight and restart. Care consumes real inputs once per day; collection and
  funded demand remain separate. Cover CARE-01 through CARE-05.
- [ ] Verify map, `village_plan_walk`, and advertised `go` targets share one
  authoritative path graph; stable public site IDs and names must not vary by
  observer. Persist routes, wagon position, and carried cargo. Cover PATH-01
  through PATH-05.

### Release evidence and seven-day economy

- [ ] Run seven *active, consecutive* days without resetting stocks or pending
  work. Record each site's daily opening, produced, received, consumed, shipped,
  spoiled, and closing quantities; reconcile every adjustment to an event and
  every shipment to an owner and funded settlement. Reservations do not change
  on-hand stock. The existing clock-advance/persistence test alone does not
  establish replenishment, lead times, meal coverage, or stockout behavior.
  Cover ECON-01 through ECON-04.
- [ ] Add low-stock, late delivery, delayed crew, cancelled sale, missing input,
  and expired-shipment cases (and cold or adverse weather only when configured).
  Permit honest scarcity but fail on undocumented creation, stranded cargo,
  double payment, or state mismatch. Cover ECON-05.
- [ ] Produce a before/after Day 1 report for meals, repairs, three firewood,
  two milk, actual wagon shipments, movement, channel errors, waits, animal
  care, and 12/12 home sleep. Produce a seven-day report of inventory, unmet
  demand, replenishment lead times, fuel, shipments, payments, and stockouts.
  Include the saved fixture, rerun command, acceptance-test results, confirmed
  root causes, and prioritized remaining issues. Do not mark the release done
  solely because unit tests pass or one unconstrained NPC run differs.

## Resilient Village storage and fixtures (open)

The `millhaven-resilient` scenario has physical depot and feed-store transfers,
owner-accounted lots, paid withdrawals, finite livestock feed, and a depot
firewood limit. These tasks concern autonomous management and unimplemented
fixtures; do not treat the presence of an action as evidence that NPCs use it
reliably. Preserve local knowledge, real transport, reserve protection,
freshness, and funded payments.

- [ ] Add stock- and demand-aware NPC decisions for depot deposits and
  withdrawals. Deposit only genuinely unreserved workplace surplus; withdraw
  only useful, affordable goods for a real workplace need. Account for other
  committed cargo and depot capacity, and avoid repeated failed trips or
  unnecessary stock shuffling. Verify actual travel, unloading, owner payment,
  and conservation of goods across cancellation and restart.
- [x] Add Ada's feed-store replenishment decisions using locally available
  grain and hay, forecast cow/hen feed needs and farm reserves. Require physical
  carrying and unloading before the animals can consume the feed; expose a
  shortage when supply or time is insufficient instead of creating stock.
- [ ] Run a reproducible multi-day Resilient Village test with no daily stock
  reset. Track opening/closing depot and feed-store quantities, each owner's
  lots, spoilage, in-transit cargo, animal feed consumption, reserve levels,
  payments, and stockouts. Include delayed or returned cargo and a depleted
  feed store; fail on mismatched ownership, lost goods, or duplicate payment.
  This complements the seven-day economy release evidence above.
- [ ] Decide and implement the handcart as an opt-in physical transport
  mechanic if desired: ownership, loading capacity, route access, parking,
  unloading, and recovery must be explicit. Until then, it is only a visible
  fixture; do not imply it carries depot shipments.
- [ ] Decide and implement the farm cistern as an opt-in finite water source
  if desired: filling from a real supply, storage capacity, draw/consumption,
  and livestock-care integration must be accounted for. Until then, livestock
  water continues to use the existing well rules.
- [x] Update the Resilient Village README description to reflect that depot
  withdrawals now exist, while retaining the distinction between usable
  transfer actions and unverified autonomous store management.

## Millhaven generations and village memory (open)

The opt-in `millhaven-generations` scenario seeds family histories, records
selected events in a persistent in-world chronicle, and tracks named
non-playable arrivals/departures. Existing watermill production and finite
merchant trading remain the only implemented mill and external-trade mechanics.
The chronicle survives days and episode saves. A fresh adventure imports nothing
unless started with a validated midnight generation save; selected verified
lessons can now influence local decisions and bounded family succession.

- [x] Make family inheritance an actual intergenerational mechanic: define
  kinship, succession, births/deaths or retirement, and bounded transfer of
  learned skills, obligations and possessions. Reconcile cast/controller
  identities, ownership, work assignments and saves before permitting a
  playable resident to be replaced. Do not equate seeded biographies with
  inheritance.
- [x] Connect village memory to decisions without granting omniscience: let
  residents consult the square chronicle or receive physically shared stories,
  then use verified lessons to adjust plans for recurring shortages, failed
  deliveries and successful repairs. Record provenance and stale knowledge;
  test that unread events cannot influence a resident's choices.
- [ ] Extend the mill and outside trade beyond the existing grain-to-flour
  job and temporary merchant visits only if a concrete route and demand model
  needs it. Specify finite goods, carrier capacity, travel, lead times,
  settlements, prices and payment reconciliation; test conservation and
  retries across day boundaries.
- [ ] Decide whether named newcomers become physical residents with homes,
  meals, work and relationships. Current arrivals/departures are chronicle
  entries and a population ledger, not actors or simulated bodies. If they
  become playable, update scheduler, cast limits, observations, controller
  lifecycle and save migration together.
- [x] Design explicit continuity between separately started generations.
  Fresh adventures currently start from the scenario's fixed founding state;
  do not silently import a prior run's chronicle, assets, people or failures.
  Require an opt-in handoff with provenance, validated state and a reversible
  checkpoint, and test that unrelated runs remain isolated.
- [x] Show the chronicle and named population in an appropriate World/Admin
  view while respecting private resident knowledge; validate multi-day
  behavior with a saved-and-reloaded run, not only isolated unit tests.
