# Millhaven Generation 12 - development plan

**Purpose:** Address the meal and continuity failures in the paused, saved-copy
episode `570307993a35409ebe8ae3cba2b77fdd`. This is a plan for a future
implementation and fresh run, not a claim that Generation 12 has passed. Keep
the [Generation 11 baseline](GENERATION-11-BASELINE.md) and its
[development plan](GENERATION-11-DEVELOPMENT-PLAN.md) as separate comparison
records. Preserve the existing run and its frozen world; a new run is not an
exact replay of the saved copy unless it starts from the same frozen state.

## Observed before-state

The saved copy inherited Day 1 from `a7c5a33735ca4e19909ab2b1be1183fd`.
It then played one additional day and paused at Day 3 00:00 with 3,597 actions,
3,604 model calls and an active goal. At Day 2 23:00, the evaluator had 14/16
checks true; `everyone_fed` and `every_resident_contributed` were false. Nella
and Iona had no recorded role contribution. The pause is not a completed or
graded episode.

| Measure | Inherited Day 1 | Newly played Day 2 | Target in a provisioned fresh run |
| --- | ---: | ---: | ---: |
| Breakfasts actually eaten | 12/12 | 0/12 | 12/12 each day |
| Daily suppers actually eaten | 10/12 | 4/12 | 12/12 each day |
| Movement decisions | 552 | 609 | Compare against 609, without suppressing necessary travel |
| Rejected actions | 116 | 73 | Report by reason, not just total |
| Assigned maintenance | 8/8 | 8/8 | Maintain each day's applicable jobs |
| Cow and hen care | Complete | Complete | Maintain daily care with real feed and water |

The Day 2 tavern and bakery had **zero bread and zero prepared meals at
midnight**. No bread was produced or delivered on Day 2, although the bakery
ended with two flour. Elsie received two fish and two eggs but prepared only
four fish-meal portions for supper. The tavern had fuel; merely increasing
oven speed would not have supplied missing bread or authorized a delivery.
No firewood was delivered despite four units produced in the woods on Day 1;
no funded wagon shipment was completed. The farm feed store ended Day 2 with
zero grain. These are run observations, not proof that all underlying engine
paths are defective. Use the archived run's `run.json`, frozen `scenario.json`,
event ledger and final world state for the exact figures and timing.

## P0 - make tomorrow's breakfast physically possible

| Work item | Implementation approach | Acceptance evidence |
| --- | --- | --- |
| Reserve fresh overnight inputs | In `npc_sim/village_economy.py`, forecast the next breakfast while the bakery and ordering channels are still open. Distinguish bread physically at the tavern, bakery stock, funded confirmed incoming orders and unconfirmed requests. For the configured 12 diners and two portions per bread, plan **six usable bread at the tavern by bedtime** plus a one-bread contingency if affordable and genuinely available. Validate lot expiry against the breakfast service and consumption time; do not turn a forecast or request into stock. | Before midnight, record six usable bread (or equivalent valid, separately accounted breakfast portions), real supplier stock, flour/fuel, accepted order, carrier, delivery and payment. Demonstrate the six-bread threshold with a controlled two-day fixture; an unfulfilled request fails. |
| Protect the breakfast allocation | Keep tomorrow's physical breakfast inputs separate from today's supper and optional daytime service in `npc_sim/village_economy.py` and `npc_sim/village_life.py`. Do not reserve yesterday's cooked meals if they expire before breakfast. Permit surplus to be used only after the breakfast obligation is covered or explicitly impossible. | Tests in `tests/test_village_meal_planning.py` and `tests/test_village_economy.py` show supper/daytime eating cannot consume tomorrow's six usable bread; low stock, expiry, cancellation and save/reload never double-allocate or conjure food. |
| Cook and serve at the right time | In `npc_sim/village_fallback.py` and the innkeeper's service guidance, schedule cooking after waking and before individual breakfast deadlines, subject to real fuel, labor, recipe and equipment. Prioritize a confirmed breakfast delivery over unrelated travel and waiting. Preserve actual paid meals and the fair diner queue. | A fully provisioned controlled two-day run records six valid two-portion batches (or 12 valid directly served portions), **12/12 paid breakfasts on Day 2**, per-resident due/window, and no premature `MEAL_MISSED`. Test an arriving late delivery and an honestly impossible breakfast separately. |

The six-bread minimum is a **service input target**, not a new unconditional
starting-stock grant or a blanket reserve at every site. The frozen policy has
`stock_buffer.bread = 1`, but that buffer is not six protected, delivered
breakfast inputs. Bread lasts up to two days in this scenario, while cooked
meals last six hours: carry fresh bread across midnight and cook the morning
portions on time. Check actual lots, not just aggregate stock.

## P0 - record the Unexpected Adventure as the episode runs

The Story page currently calls `buildDayNarrative` on the entire event list
when it renders, and calls it **again** just to decide whether Copy story is
enabled. `renderNarrative` also runs from state updates and timeline updates;
`buildNarrative` sorts and processes the history, reconstructs the story and
parses observations. Opening or switching the Story page therefore repeats
work over the whole episode (over 33,000 events in the observed two-day copy)
and can stall the browser. The completion review's `build_narrative` is a
separate final assessment path; do not mistake it for the live Story page.

| Work item | Implementation approach | Acceptance evidence |
| --- | --- | --- |
| Append narrative after each turn | Persist the Unexpected Adventure's actual paragraphs in a per-run append-only record as each decision/action result becomes final. Maintain the small per-actor context needed for discoveries, intent/result pairing and team order, rather than replaying all prior events after every turn. Store source event IDs/sequences, actor, day, text and a narrative format version. Keep event append and story checkpoint consistent across crashes/retries; deduplicate by source IDs. Narrative writing must not invoke a model or delay the next resident with a full-history rebuild. | After each turn, a new paragraph or a recorded no-change cursor is durable; reload, save/branch and retry produce the same ordered story with no duplicates or missing results. Existing voice, team, day filtering and copy output match the current narrative on small fixtures. |
| Read recorded pages | Serve stored paragraphs by run, day and bounded cursor/page from a lightweight endpoint; update `ui/src/main.ts` to render only the selected page and append new entries as they arrive. Opening Story, selecting a day or Copy story must **read** recorded content, never call `buildDayNarrative`/`buildStory` over the entire run. Keep the technical event timeline and completion evidence available separately; do not mount tens of thousands of paragraphs in one DOM update. | Instrument an active multi-day run: switching Story/day does no full event replay, no extra model call and no blocking full-history request. Copy retrieves the complete recorded selection without truncation. Validate page boundaries, actor/team views, stable scroll position and accessible loading/error states. |
| Migrate older episodes safely | For pre-Generation-12 runs without a narrative record, run a one-time, resumable background backfill from archived events using the current formatter, preserving source IDs and version. Do not do it synchronously in a Story-page request or repeatedly on every refresh. Keep the old episode and events unchanged; expose progress and errors, and serve completed pages as available. | Reopening the 33,334-event saved copy does not rebuild its narrative on the UI thread; a completed backfill yields the same day-separated output. Restarting midway resumes without duplication. Missing/corrupt records show an explicit error or recovery path, not an apparently empty successful story. |

Before and after, measure Story open time, day-switch latency, peak UI/main
thread blocking, story endpoint response size, memory use and per-turn append
cost on the same recorded run. Set a practical interaction budget (for example,
under two seconds to open or switch a bounded page on the reference machine)
and verify it with a browser performance check, not just unit tests. Do not
put the full rendered narrative into every `run.json`, state snapshot or SSE
message: that would move the stall into the simulation loop.

## P1 - sustain both services and the supply chain

| Work item | Implementation approach | Acceptance evidence |
| --- | --- | --- |
| Forecast a whole day's funded demand | After reserving breakfast, forecast supper separately. The configured plan calls for eight bread-meal and four fish-meal portions: ordinarily another **four bread and two fish**, with the actual recipe, freshness, fuel, tea policy, buyer funds and cooking window checked. Recompute from remaining edible portions rather than always producing ten bread. | In a provisioned fresh run, 12/12 breakfasts **and** 12/12 evening suppers are actually eaten within their service windows, with distinct per-person records, paid charges, valid lots and no double use of ingredients. If supply is impossible, emit the first specific blocker and measure the shortfall. |
| Produce and deliver to deadlines | Check Jory's flour-to-bread cadence, labor and competing obligations, Mara's flour/grain replenishment, carrier capacity, order acceptance, travel, loading and settlement. Prioritize scheduled production/delivery before optional research or repeated waits; measure throughput before altering recipe yields or work duration. | Show produced, loaded, delivered, received and cooked quantities and times for breakfast and supper on consecutive days. A produced loaf at the bakery or posted request alone does not count. Only increase throughput if the measured schedule cannot meet a physically provisioned demand. |
| Close related resource gaps | Reconcile the feed-store grain required for next-day hens, bakery/inn fuel, and finite funds; retain the [Gen 11 P1 gates](GENERATION-11-DEVELOPMENT-PLAN.md) for three firewood units over two valid trips, a funded farm-to-mill wagon shipment, and any configured milk/egg delivery. | Day-keyed stock, ownership, cargo and coin ledger balances; no hidden restock, unpaid transfer, duplicate settlement or animal care without feed/water. Keep the cistern and handcart out of mandatory checks unless implemented. |
| Finish all resident contributions | Trace Nella's and Iona's missing role contributions separately from travel, research, service and maintenance. Offer legitimate occupational work when inputs/time permit; do not count presence or a plan as contribution. | `every_resident_contributed=true` with actual recorded work by all 12 and no fabricated action. Preserve 12/12 at home/asleep, completed maintenance and animal care. |
| Prevent advertised-action crashes | The errored sibling run `0ad3f5a1d14947a0a9f4b6d726d3690e` advertised `village_daytime_rest`, but its daily-life handler raised `Unknown daily-life action`. Implement its defined physical behavior or stop advertising it consistently; test the action through the world adapter. | A legal use no longer terminates an episode; invalid timing/location returns an ordinary explicit rejection. Do not mask an unknown action as success. |

## P1 - nightly journals become village history

Every playable resident NPC must write one distinct journal entry for each
waking day before going to sleep. The entry should recount that resident's
personally known work, encounters, meals, problems and intentions, including
shortages or failures rather than inventing a successful day. Keep journal
writing short enough not to displace supper, travel home or sleep. The
requirement applies to all 12 resident roles, not just the current successor.
It also applies to visiting and trading NPCs present overnight. These are
currently non-playable arrivals without resident turns, so give them an
explicit authored journaling opportunity and a departure handoff; do not
silently manufacture first-person entries for them. A visitor who departs
before night should record a departure note rather than a fictional overnight
entry.

The engine already offers `village_write_diary`, rejects a resident's sleep
with `JOURNAL_DUE` until they write, and carries each role's private diary into
a successor world. This is not yet a durable **village history**: only the
three newest entries per role survive, diaries are not added to the public
chronicle, and the chronicle itself retains at most 100 prior entries at a
generation handoff. Extend these existing mechanisms rather than introducing
an unrelated log or assuming the current private diary is shared knowledge.

| Work item | Implementation approach | Acceptance evidence |
| --- | --- | --- |
| Ensure nightly authorship | Keep the actor-authored `village_write_diary` action and sleep gate in `npc_sim/village_legacy.py`, `npc_sim/village_life.py` and the fallback. Prompt for a concise reflection after the day's duties and before the individual's bedtime. Use the actual actor, day, time and world identity; reject duplicate entries for the same waking day. Do not generate a generic note at midnight on an absent actor's behalf. | For each of 12 residents, the daily ledger shows exactly one authored entry before their sleep action on every active day; no fabricated note, duplicate on retry or loss on save/reload. An unwritten journal is an explicit blocker, not a successful bedtime or a silent midnight pass. |
| Preserve and publish history | Retain complete dated entries in a durable archive that does not silently discard old days at `DIARY_LIMIT = 3` or truncate them at the 100-entry chronicle handoff. At an explicit nightly archival step, copy the authored entries into the village's historical record with author, original day/world and stable identity, idempotently across midnight, retries and reloads. Preserve the private family diary separately if desired; public records must be deliberately published, not leaked from another actor's private observation. | After two days, the village history contains at least 24 distinct resident journal entries plus entries for applicable visitors, with original text and provenance; a second midnight/reload adds none. Older days remain retrievable after enough days and successive generation handoffs. |
| Include nonresident NPCs | Give overnight visitors and traders an authored nightly action and a durable historical identity; handle early departures with a final departure note. Do not convert them into playable residents or count their journal toward resident work, meal or sleep gates. | A controlled overnight visitor writes once; a departing visitor's note persists; arrival/departure, midnight and reload do not duplicate or attribute an entry to the wrong NPC. |
| Make history available across generations | Carry the archived entries through an explicit generation save/handoff without importing live actor sessions, skills, stock or another run's history. Define how a resident reads the published village chronicle at its physical site, and how a successor reads their own inherited family journal at home; neither action grants unseen current-world facts or automatic skills. | A successor world can read all transferred historical journal entries through the documented access path; an unrelated fresh world cannot. Another resident cannot see a private diary merely by observing, but can read the deliberately published historical entry. Tests cover two handoffs, provenance, long histories and actor-local knowledge. |

Keep the event chronicle and first-person journals distinct: a journal is the
resident's account, not proof that its claims occurred. Compare its claims
with world events where applicable, preserve contradictions as perspective,
and never use journal text to satisfy an objective or mint resources.

## P2 - prove continuity, not just a good snapshot

1. Freeze the scenario/hash, effective policy, model/proxy settings, action and
   time budgets or unlimited mode, initial seed/state and exact evidence window.
   Keep the inherited Day 1 distinct from newly played days. Capture opening
   plus each consecutive midnight from the **same world**; use
   `npc_sim.village_release_evidence` for the offline stock/coin ledger and the
   action log for model calls, failed actions and timing.
2. Run a fresh, provisioned controlled Day 1 and then **three consecutive
   active days** without reseeding opening stock. Reconcile each site's and
   owner's opening + production/receipts - consumption/shipments/spoilage to
   closing inventory, including lots, reservations, funds, cargo, animal feed,
   meals, maintenance, nightly journals and home sleep. Distinguish true scarcity from missing
   production, late orders, missed delivery or service scheduling.
3. Compare daily breakfasts, suppers, movements, rejected waits and channel
   errors with the table above and the Generation 11 baseline. Record costs,
   actions and model calls per day; do not apply the generic escape-room
   18/20 or 9/10 targets to Millhaven. A midnight pause is a checkpoint, not a
   pass. The [seven-day reliability release gate](MILLHAVEN-TEST-CRITERIA.md)
   remains open until seven active days and an achieved, graded episode are
   actually evidenced. Report journal coverage per resident and confirm the
   published history survives each tested handoff without duplicating entries.

## Execution and sign-off

**Implementation checkpoint (not release sign-off):** The current worktree has
incremental, per-run SQLite story pages, an asynchronous resumable event
backfill, a bounded Story UI and complete-page copy. The economy forecasts
next-day breakfast while ordering remains possible, and actual supper cooking
and non-breakfast eating reject consumption of bread needed at the next
breakfast. The advertised 15-minute at-home daytime rest action now has a
physical handler and once-per-day guard. Resident journals survive saves and
handoffs and publish at midnight with provenance. These are engine and focused
test results, not proof of 12/12 service on a two-day autonomous run.

**Open gates:** The controlled two-day paid 12/12 breakfast/supper demonstration,
fresh three-day autonomous stock/funds reconciliation, seven-day graded release,
browser Story latency measurement on the reference run, and actor-authored
visitor/trader journals have not been established. Visitor/trader chronicle
events and overnight presence records are *not* first-person journals; those
NPCs still lack a journaling turn. Do not claim Generation 12 has passed until
these are implemented and measured.

First record the Story-page performance baseline and add focused tests for
incremental narrative persistence, backfill and pagination; ship the read-only
Story path before another very long model run. Then add focused failing tests
for overnight forecast/reservation, morning
service and expiry; make the smallest engine and decision changes that pass
them. Then run the controlled two-day meal test, the targeted economy/life/
fallback tests, and a fresh model episode. Proceed to throughput tuning only
if measured production remains the limiting factor with confirmed inputs,
labor, carrier and funding. Report both successes and unresolved blockers;
tests alone are not autonomous benchmark results. Add focused legacy tests for
all-resident nightly authorship, publication, save/reload and two-generation
retention before claiming the journal requirement is delivered.
