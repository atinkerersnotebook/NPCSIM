# Millhaven Generation 13 - development backlog

**Purpose:** Restore reliable supper and mill repair without losing the working
milk, compost, livestock and research-sharing paths. This is a backlog for a
future implementation and fresh run, not a claim that Generation 13 has passed.
Keep the [Generation 12 plan](GENERATION-12-DEVELOPMENT-PLAN.md) and
[Generation 11 baseline](GENERATION-11-BASELINE.md) as separate records.

## Evidence and comparison boundary

The Generation 12 Day 1 evidence is the persisted run
`c2d75606799a4255aac780ad564dd0c2` and its world
`5458a8c7e3204a3d8ad0325b9d17c855`. The run paused at Day 2 00:00;
its snapshot is **not** a multi-day result or a successful episode. The
Day 1 event window is 05:00-23:59; the supplied review ends at 23:00.
Preserve the frozen scenario and original run rather than treating a new
episode as an exact replay.

| Measure | Gen 11 Day 1 | Gen 12 Day 1 | Gen 13 gate |
| --- | ---: | ---: | --- |
| Breakfasts eaten | 12/12 | 12/12 | 12/12 per active day |
| Daily suppers eaten | 10/12 daily meals recorded; only six after 17:00 | **4/12 at 19:00** | 12/12 in each resident's service window |
| Well completed | 16:10 | 14:20 | Preserve valid crew work; compare timing |
| Mill completed | 17:10 | Not completed by midnight | Complete with confirmed crew and real supplies |
| Wagon usable | 08:25 | 08:20 | Preserve repair; demonstrate an actual load |
| Firewood delivered | 0 | 0 | Funded delivery before a reserve is exhausted |
| Milk delivered to tavern | 0 | 2 at 16:25 | Preserve delivery and test feasible use |
| Movement actions | 552 | 562 | Reduce avoidable trips without missing duties |
| Rejected waits | 31 | 35 `ELIGIBLE_TASK_PENDING` | Report by code and reduce repeats |
| Cow and hen care | Complete | Complete | Complete with real feed and water |
| Sleep at home | 12/12 | 12/12 | Preserve on each active day |

Generation 11 numbers above come from the supplied review, not a reconstructed
matching run. Generation 12 movement, rejected waits and outcomes were checked
against the saved event/request ledgers. The review's nine physical-channel
errors have **not** been independently reconciled to a defined set of request
codes; define the measure before comparing it with Generation 11's seven.

## P0 - feed all residents at supper, without sacrificing breakfast

The tavern cooked 12 morning portions from six bread at 05:30-05:40, then
four portions from two fish at 18:05. Nine unused morning portions spoiled
at 11:30-11:40. At 19:00 Finn, Bren, Ada and Garrick ate; seven other diners
were marked missed at 19:00, and Elsie missed her later 19:30 service. The
tavern retained one bread at midnight. The current service can protect bread
for tomorrow's breakfast; neither that loaf nor spoiled morning portions
should be counted as available supper. This is an observed supply and timing
shortfall, **not** proof that allocation alone is defective.

| Work item | Implementation approach | Acceptance evidence |
| --- | --- | --- |
| Reconcile service stock by deadline | Trace `meal_plan`, `overnight_bread_protected` and `village_eat` in `npc_sim/village_life.py` with lot freshness, cooked portions, protected next-breakfast bread, funded incoming orders, cooking fuel and the two service times. Distinguish physically ready portions from cookable inputs and unaccepted requests; emit the first actionable shortage while there is still time to respond. Do not remove the overnight breakfast guard to make supper appear complete. | A Day 1 ledger explains all 12 breakfast consumptions, nine spoiled cooked portions, four fish-meal consumptions, the protected loaf and eight missed suppers without double-counting. Tests distinguish fresh food, expired food, protected breakfast inputs and merely promised deliveries. |
| Close the bread/fish delivery and cooking schedule | In `npc_sim/village_economy.py` and the innkeeper/supplier decision path, order against the remaining **evening** shortfall early enough for accepted production, loading, travel, payment and cooking. Prefer physically feasible bread or fish supply over optional work when service is at risk; retain the distinct next-day breakfast forecast. Measure real capacity before changing recipe yields or granting starting inventory. | A provisioned controlled two-day fixture records paid 12/12 breakfast **and** 12/12 supper each day, per-person and within the configured windows. Ingredient, fuel, lot, cargo and coin ledgers reconcile; an impossible shortage reports a concrete blocker rather than a false pass. |

## P0 - make mill appointments recoverable and finish the repair

The well completed with Torvin and Bren at 14:20. For the mill, Hobb reserved
a real plank at 17:15 and unloaded it at 17:35; the crew still could not work.
Mill notices expired at 11:05, 15:15 and 18:35, and a later notice was posted
for Day 2. Attempts also failed because a partner was busy or the selected
channel was unavailable. One new proposal was rejected while another booking
was still **open**. The rule in `npc_sim/village_communication.py` already
excludes terminal expired notices from its duplicate-open check: do not
describe this as a universal inability to replace expired appointments.

| Work item | Implementation approach | Acceptance evidence |
| --- | --- | --- |
| Reproduce the actual booking sequence | Use the saved notice versions, due/end times, crew availability, channel and project rejections to identify which transitions prevented confirmation after the plank arrived. Add focused cases in `tests/test_village_communication.py` for an expired notice, an accepted but unconfirmed notice, an open replacement and an already active crew session. Keep the existing rules requiring independent consent, a real channel, both workers, site, tools and materials. | A valid expired booking can be superseded; an actually open booking is not duplicated; an expired or stale confirmation cannot authorize work. The failing Day 1-style sequence has an explicit, recoverable next step rather than a loop of generic appointment errors. |
| Schedule and execute a feasible crew window | Check the decision guidance in `npc_sim/village_ai.py` against partner busy times, breakdown timing, delivery arrival, notice expiry, travel and bedtime. Replan early enough for both workers to confirm and perform the work, or report the earliest actual blocker. | In a controlled broken-mill scenario the real plank, beam and hammer reach the watermill, both workers confirm a current appointment and each contributes; the mill is repaired before the evening cutoff. Include missed window, retry and save/reload cases; do not mark a posted notice as completed work. |

## P1 - sustain and use the working circular economy

| Work item | Implementation approach | Acceptance evidence |
| --- | --- | --- |
| Protect milk and compost exchange | Keep Ada's timed cow/hen care, two-unit milk delivery and Elsie's two compost-material trips (three plus one) as regression fixtures. Verify the fresh input, order, cargo, receipt, payment and Ada's two timed compost operations rather than only testing final stocks. | No free milk, invented compost, duplicated settlement or animal production without feed/water; the same physical exchange still completes when meal and repair planning change. |
| Replenish firewood before the cutoff | Rowan split one log into four firewood at 10:25, but no firewood delivery completed. Trace why requests, supplier acceptance, carrying capacity and deadline feasibility failed; move the funded request early enough for a real trip without raiding protected meal or fuel stocks. | At least one real accepted, loaded, delivered and settled firewood order arrives while the receiving reserve can still be used. If capacity requires multiple trips, show each trip and its funds. Production in the woods alone does not count. |
| Put the wagon to work only for confirmed cargo | Nella's wagon was ready at 08:20, but its final loads were empty. Build a route from accepted, funded shipments that fit its six-unit capacity and actual pickup/delivery windows; do not invent orders merely to exercise it. | A controlled farm-to-mill or depot delivery shows load, horse/driver, route, unload, payment and empty return, with stock and cargo conserved. Infeasible or missing shipments remain explicit. |
| Convert ingredients and discoveries into feasible products | Two milk reached the tavern, but no milk meal was made; Jory's flatbread, Iona's infusion and Finn's fish-supper recipes were discovered, and two recipes were shared in person. Check demand, recipe inputs/equipment, food safety, work time and physical knowledge before scheduling production. Treat discovery and sharing as knowledge, not inventory or mandatory output. | A funded, feasible recipe is made from real inputs, then sold/served or stored with a valid lot; an infeasible recipe reports its blocker. Existing discovery/sharing remains intact. |
| Cut information and channel churn | Segment the 562 `go` events, 35 rejected waits and channel-related rejections by actor, purpose and unchanged information. Cache only personally known, versioned information and plan a route around real obligations; preserve physical message receipt and site visits when facts change. | Compare like-for-like per-day movements, wait rejections and a **defined** channel-error code set with Gen 12. Show fewer avoidable repeats without worse meals, deliveries, repair, maintenance, livestock care or sleep. |

## P2 - prove continuity and retain existing Generation 12 gates

1. Freeze scenario/hash, effective policy, model settings and budgets; record
   opening inventory, lots, funds and request states. Run a fresh controlled
   episode for three consecutive **active** days, carrying the same world's
   stock, orders, money and crew state across midnight. A midnight pause is a
   checkpoint, not success. Record actual breakfasts and suppers, repairs,
   animal care, shipments, spoilage, research outputs, movement and rejected
   actions for **each** day.
2. Reconcile opening + produced + received - consumed - shipped - spoiled to
   closing stock at each site, plus ownership, cargo and coins. For any failed
   meal or delivery, identify whether the first blocker was shortage, freshness,
   funding, acceptance, travel, worker time or service cutoff. Verify the
   seven-day graded reliability gate separately in
   [Millhaven test criteria](MILLHAVEN-TEST-CRITERIA.md); do not infer it from
   a good Day 1 or a three-day checkpoint.
3. Keep the still-open [Generation 12 plan](GENERATION-12-DEVELOPMENT-PLAN.md)
   gates visible rather than silently declaring them complete: browser Story
   latency, visitor/trader-authored journals, controlled two-day meals and the
   fresh multi-day reconciliation. Do not conflate an incremental story
   implementation or resident journal tests with autonomous episode evidence.

## Execution order

First add targeted reproductions for the supper stock timeline and mill notice
transitions. Fix the earliest demonstrated blockers, then run focused meal,
communication, economy and crew tests. Follow with the controlled two-day
service/repair run, and only then tune supplier throughput, firewood, wagon and
research production if the measured constraints demand it. Finally perform the
same-world three-day run and report failures alongside successes.

## First P0 implementation checkpoint (not sign-off)

- The supper forecast in `npc_sim/village_life.py` now excludes fresh bread
  protected for the next breakfast and prepared food that expires before
  service. `npc_sim/village_economy.py` no longer advertises protected bread
  as cookable supper inputs. Actual protected-bread consumption remains
  rejected; no stock or meals were granted.
- The communication path now expires a missed crew window when a replacement
  is requested, even if the periodic notice advance has not run. An accepted
  still-open booking instead supplies an explicit confirmation/cancellation
  path, without bypassing consent or physical channels. The mill guidance
  names the real materials, availability and confirmation steps.
- Focused meal, economy and communication tests pass. Neither 12/12 paid
  supper in an autonomous run nor a completed mill in a fresh episode has
  been demonstrated. The delivery/cooking schedule and controlled two-day
  service/repair gates above remain open.
- Follow-up: breakfast now serves already cooked, short-lived portions before
  shelf-stable bread. A controlled twelve-diner breakfast consumes twelve real
  cooked portions without spoiling them; a provisioned, timed evening fixture
  cooks and serves twelve paid meals while retaining six fresh loaves for the
  next breakfast. This is a controlled service result, **not** an autonomous
  supplier-delivery or two-day continuity result. Prioritize a fresh run and
  trace any remaining supply and crew scheduling blockers there.
