# Engine Change Backlog

This list records implemented, opt-in engine changes. The existing paused
adventure and legacy scenarios are unchanged. The living-village generator
creates a separate configured scenario; backend activation and starting a new
adventure are separate operations.

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
