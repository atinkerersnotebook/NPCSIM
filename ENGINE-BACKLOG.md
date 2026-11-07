# Engine Change Backlog

This list records proposed engine changes. Unchecked items are not implemented.

## Demand-driven supplier orders

- [ ] Add an order system so businesses request supplies based on demand instead
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
- Prevent duplicate commitments and deliveries, including after retries or
  recovery; track remaining quantities for partial fulfillment.
- Check affordability, including delivery fees, before committing purchases.
- Pay for accepted delivered quantities exactly once; surface insufficient
  funds, stale goods, shortages, and missed deadlines explicitly.
- Support consolidated shipments without losing individual order accounting.
- Expose orders and their status in NPC observations and the monitoring UI.
- Preserve real production, travel, stock movement, perishability, meals,
  sleep, and animal-care rules.

### Acceptance criteria

- Existing usable inventory and confirmed incoming goods reduce new orders.
- Goods expiring before service do not satisfy that service's requirements.
- Repeated planning or retried delivery cannot duplicate an order or payment.
- Partial, late, rejected, or cancelled deliveries have visible outcomes.
- Suppliers can stop production once accepted demand and reserve targets are met.

## Demand-aware production and reserve targets

- [ ] Limit production to accepted orders, forecast consumption, and a small
  maintenance or stock buffer.
- [ ] Apply targets to bread, meals, tea, planks, and wagon parts, rather than
  requiring continuous production whenever a cooldown ends.
- [ ] Include usable inventory and committed production when calculating unmet
  demand, so concurrent producers cannot independently overfill the same target.
- [ ] Allow producers to pause or perform other supported work when demand is met.

## Breakfast and meal planning

- [ ] Add configurable breakfast after waking alongside the existing supper.
- [ ] Track each meal's due time, consumption, ingredients, and payment separately.
- [ ] Plan portions against the expected diners and chosen recipes; breakfast
  should not simply be added on top of already excessive production.
- [ ] Preserve existing single-meal scenarios unless they opt into new schedules.

## Sustainable meal pricing

- [ ] Align actual meal charges with recipe costs, delivery costs, and intended
  margin; distinguish service charges from tradable product prices.
- [ ] Support explicitly funded subsidies if meals should remain below cost,
  rather than silently draining the innkeeper's account.
- [ ] Surface unaffordable purchases and meals without hiding unpaid costs.
- [ ] Decide prices or subsidy policy before implementation; the suggested
  two-coin bread-based meal is an example, not a fixed engine rule.

## Just-in-time cooking and freshness

- [ ] Schedule cooking near meal service instead of accumulating meals all day.
- [ ] Prefer the earliest-expiring usable ingredients and portions.
- [ ] Forecast what will expire before the next planned use, and expose that
  quantity separately from usable stock.
- [ ] Reduce or stop production when existing fresh stock covers service.
- [ ] Keep spoilage real and recorded; do not extend shelf lives to disguise
  overproduction.

## Consolidated deliveries and end-of-day cargo

- [ ] Group confirmed, compatible orders into capacity-aware delivery runs.
- [ ] Avoid redundant small trips and expose their travel and delivery-fee costs.
- [ ] Require confirmed demand and affordability before loading goods.
- [ ] Resolve remaining cargo by delivery, return, or explicit overnight storage
  with ownership and freshness preserved; stopping a route is not fulfillment.
- [ ] Preserve actual travel, unloading, and animal-care requirements.

## Raw-material replenishment

- [ ] Define minimum reserves and replenishment triggers for grain, flour, iron,
  logs, hay, and other recipe inputs.
- [ ] Protect needed raw materials from conversion into unnecessary finished
  stock, especially spare wagon parts and planks.
- [ ] Plan harvesting, processing, procurement, and manure use around the next
  day's needs.
- [ ] Respect renewable-resource rules, availability, lead times, and payments;
  never replenish resources by silently creating stock.

## Service schedules and useful off-peak time

- [ ] Give the innkeeper explicit planning, preparation, and serving windows.
- [ ] Use off-peak time for supported coordination and stock management, or
  legitimate rest, rather than forcing additional cooking.
- [ ] Add any new secondary work as explicit capabilities with real outcomes;
  do not count narrative-only cleaning or customer service as completed work.
- [ ] Preserve personal meals, bedtimes, and time at home when assigning work.

## Event-driven waiting and fewer model calls

- [ ] Skip unnecessary model decisions while an actor awaits a known cooldown,
  delivery, or scheduled service.
- [ ] Make actors eligible again at relevant events, including meal deadlines,
  bedtime, supply arrivals, and changed commitments.
- [ ] Avoid repeated short wait loops and redundant reads or announcements when
  no actionable information has changed.
- [ ] Preserve meaningful decisions, urgent interruptions, recovery safety, and
  chronological action history; do not jump past required activities.

## Demand-led completion and daily care

- [ ] Ensure hay replenishment and animal readiness are scheduled and verified
  alongside meals, homecoming, sleep, and safe route shutdown.
- [ ] Review mandatory tea use: make it a meaningful need or an explicit
  scenario objective, rather than forcing consumption solely to clear a flag.
- [ ] Review production goals against actual demand and reserve needs so success
  does not reward unnecessary output.
- [ ] Keep intentional completion-rule changes explicit and scenario-controlled.

## Economy and efficiency reporting

- [ ] Show produced, consumed, delivered, spoiled, and remaining quantities by
  product and business, including cargo and upcoming expiry.
- [ ] Report gross sales, purchases, delivery fees, subsidies, net cash change,
  and inventory value separately; define valuation before reporting profit.
- [ ] Distinguish requested wait time, production cooldowns, travel, actual work,
  rest, and sleep instead of labeling every wait as wasted time.
- [ ] Show pending orders, reserve shortages, projected meal coverage, and
  overproduction warnings.
- [ ] Track model calls spent on repetitive waiting and coordination to measure
  the benefit of event-driven scheduling.

## Personal interests, relationships, and evening leisure

- [ ] Give each villager distinct interests, starting with one solo interest and
  one social interest rather than identical evening routines.
- [ ] Support time-consuming solo activities such as reading, gardening, and
  crafting, and social activities such as visiting, games, storytelling, and
  shared tea.
- [ ] Track preferred companions and relationship changes from actual shared
  activities, not unsupported narrative claims.
- [ ] Support small personal projects and skill development across days.
- [ ] Choose leisure according to interests, available time, companions, fatigue,
  resources, and affordability; legitimate rest remains a valid choice.
- [ ] Represent activities explicitly in observations, history, and the Gantt,
  with duration, location, participants, and any real resource consumption.
- [ ] Schedule completion and meaningful interruptions without repeated model
  calls during an unchanged activity.
- [ ] Allow evening outings while preserving real travel home by each person's
  bedtime, adequate sleep, meals, and animal care.
- [ ] Make new leisure behavior scenario-controlled; hobbies are optional, not
  production quotas or mandatory completion conditions.

### Proposed personal project: Iona's herbal book

- [ ] Give Iona, the herbalist, a multi-day project to write a book about local
  plants, their identification, and Millhaven's herbal traditions.
- [ ] Let her use free time for field observations, organizing notes, drafting,
  and revising, while preserving supply commitments, meals, and sleep.
- [ ] Persist completed observations, notes, and chapter progress across days;
  repeated activity must not duplicate progress.
- [ ] Connect research to actual exploration and encounters, and allow her to
  discuss her work with interested villagers.
- [ ] Show writing sessions and project milestones in observations, the story,
  and the Gantt instead of treating writing as generic waiting.
- [ ] Keep the project optional and gradual; do not require a finished book each
  day or treat fictional herbal lore as real-world medical advice.

## Evening band at the inn

- [ ] Allow interested villagers to form a small band and arrange evening
  performances at the inn, with Elsie coordinating the venue.
- [ ] Define willing members, musical roles, skills, and available instruments;
  leave the cast and instrument choices open until scenario design.
- [ ] Model rehearsals and performances as coordinated activities with explicit
  commitments, shared start and end times, and physical attendance.
- [ ] Check member availability and instrument access before confirming a show;
  surface cancellations or missing participants rather than inventing a
  completed performance.
- [ ] Let other villagers choose to attend according to interests and
  relationships; do not make attendance compulsory.
- [ ] Coordinate music with supper service so performers and listeners can eat,
  and finish early enough for everyone to travel home before bedtime.
- [ ] Record actual participation and any defined enjoyment, relationship, or
  skill outcomes; attendance must not automatically consume food or tea.
- [ ] Make any performer fees or tips explicit, funded transactions if included;
  do not assume the cash-strapped inn can pay for entertainment.
- [ ] Show rehearsals, performances, and attendance in the story and Gantt.

## Minor village events and DM configuration

- [ ] Add small, recoverable disruptions and opportunities that create real
  tasks, delays, orders, or discoveries rather than narration-only outcomes.
- [ ] Start with passing travellers, brief weather changes, minor equipment
  repairs, and personal discoveries.
- [ ] Extend the catalogue with safely escaped animals, disappointing fishing,
  seasonal harvest opportunities, visiting merchants, misplaced tools,
  birthdays, band-member unavailability, and requests for neighbourly help.
- [ ] Use a suggested default of zero to two minor events per day, usually
  affecting one to three villagers for 15-60 game minutes; quiet days are valid.
- [ ] Expose events through local observations and actual communication rather
  than giving every NPC immediate knowledge.
- [ ] Let affected villagers adapt plans, ask for help, substitute supplies, or
  decline optional requests.
- [ ] Prevent event stacking from making required meals, animal care, safe
  homecoming, and adequate sleep impossible.
- [ ] Persist event selection, activation, progress, and resolution so retries,
  recovery, and resuming do not duplicate events or their effects.
- [ ] Show affected actors, schedule changes, and outcomes in observations,
  the story, and the Gantt.

### DM controls and additional event types

- [ ] Let the DM enable or disable event types and configure frequency, daily
  limits, intensity, duration, eligible time windows, affected locations and
  residents, and cooldowns between events.
- [ ] Allow the DM to raise event limits when appropriate, while keeping
  feasibility safeguards and making configuration changes explicit.
- [ ] Support scheduled or condition-triggered events alongside random events,
  with reproducible random selection for testing and replay.
- [ ] Let the DM define additional event templates with prerequisites,
  discoverability, bounded effects, available responses, resolution conditions,
  and expiry or cancellation behavior.
- [ ] Validate custom templates against supported engine actions and state
  changes; reject unsupported effects explicitly instead of inventing outcomes
  or executing arbitrary code.
- [ ] Require stock and money changes to follow explicit accounting rules, and
  new visitors or resources to have recorded sources.
- [ ] Make event configuration and changes inspectable, and preserve existing
  scenarios unless they opt into the event system.

## Temporary visitors and timeline presence

- [ ] Give visiting travellers and merchants named identities and a Visitor
  label, distinct from permanent residents.
- [ ] Add a temporary Gantt row showing actual arrival, departure, travel,
  trading, meals, visits, resident interactions, and overnight stays.
- [ ] Retain departed visitors in historical timeline views while removing them
  from the current people-in-town list only after actual departure.
- [ ] Keep overnight guests present until they leave, and record lodging
  commitments and any payments explicitly.
- [ ] Apply appropriate visitor needs without automatically assigning resident
  homecoming or village-work completion requirements.
- [ ] Persist visitor identity and presence across recovery so an event retry
  cannot create duplicate people or erase their history.

## Travelling merchants and surplus exports

- [ ] Let merchants buy genuine surplus from producers and offer scarce imports,
  with product interests defined per merchant rather than universal purchasing.
- [ ] Give each merchant finite funds, cargo capacity, available stock, offered
  prices, and an arrival and departure schedule.
- [ ] Let producers accept or reject offers; protect local consumption, accepted
  orders, and reserve targets before treating stock as exportable surplus.
- [ ] Require enough remaining shelf life for the intended journey and resale;
  nearly expired meals must not become automatically saleable exports.
- [ ] Complete transactions through actual goods transfer, loading, and funded
  payment before departure, without duplicating payments after retries.
- [ ] Handle unmet trade commitments explicitly when time, stock, capacity, or
  funds run out; do not silently mark them fulfilled.
- [ ] Allow scenario-defined merchants to buy goods such as spare planks, wagon
  parts, tea, or fresh bread, and bring inputs such as iron or grain.
- [ ] Keep merchant demand occasional and bounded so export opportunities do
  not become guaranteed sales that reward continuous overproduction.
- [ ] Expose offers, trades, cargo, and departure deadlines to relevant NPCs and
  monitoring views; include export income and import costs in economy reports.
- [ ] Let the DM configure merchant visits and trade profiles through the event
  system, with explicit sources for incoming goods and funds.

## Suggested implementation order

1. Supplier orders and demand-aware production.
2. Breakfast planning, meal economics, and just-in-time cooking.
3. Delivery consolidation and raw-material reserves.
4. Service schedules and event-driven waiting.
5. Completion-rule review and economy reporting.
6. Personal interests and social activities, followed by the evening band.
7. Minor village events and DM-configurable event templates.
8. Temporary visitor timelines and travelling-merchant trade.

All items remain planned, not implemented. Breakfast and meal-pricing changes
are separate from supplier ordering. This backlog does not change the saved run.
