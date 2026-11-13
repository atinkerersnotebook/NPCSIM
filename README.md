# NPCSIM v0.1

A local goal-driven NPC laboratory: a Python world and agent controller, real MCP actions, TypeScript player, world-monitor and admin views, and readable JSON persistence.

## Start

From PowerShell in this directory:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Set-Location ui
npm install
npm run build
Set-Location ..
.\start.ps1
```

To also serve the built UI and admin API on a trusted LAN, start with your computer's LAN IPv4 address (for example `192.168.1.143`):

```powershell
.\start.ps1 -LanHost 192.168.1.143
```

On Linux, after installing dependencies and building the UI, use:

```bash
NPCSIM_LAN_HOST=192.168.1.143 .venv/bin/python -m uvicorn npc_sim.app:app --host 0.0.0.0 --port 8000
```

### Opt-in automatic repair supervisor (Linux)

Install [GitHub Copilot CLI](https://docs.github.com/en/copilot/how-tos/copilot-cli/set-up-copilot-cli/install-copilot-cli), then run `~/.local/bin/copilot` (or `copilot` if it is on `PATH`) and enter `/login` in a terminal to authenticate it independently of VS Code. Alternatively, configure a user-owned fine-grained token with **Copilot Requests** permission through the CLI's supported environment variables; never save tokens in this repository. Confirm unattended authentication with `~/.local/bin/copilot -p "Reply OK" --silent` before replacing a server. CLI access and authentication are not provided by NPCSIM.

At a safe checkpoint, stop the existing server on port 8000, then launch the supervisor instead of uvicorn:

```bash
.venv/bin/python -m npc_sim.self_heal --repair-command '[".venv/bin/python","-m","npc_sim.repair_agent"]'
```

The bundled adapter reads the repair task from standard input and passes it to Copilot CLI's noninteractive `--prompt` mode, with only source/test edit tools approved. The CLI must be installed as `copilot` on `PATH` or at `~/.local/bin/copilot`; alternatively set `NPCSIM_COPILOT_CLI` to its absolute executable path. Other shell commands and path/URL access are not automatically approved; a repair that requires them will stop for inspection rather than silently granting broader permissions. The configured executable must edit local source/tests and exit with status 0. The supervisor tests the edited code before restarting the server. It attempts an automatic resumable save every five minutes (only when the world supports saves), tries the built-in verified adventure reopen, then the latest valid save for that run, and finally resets and runs a new episode. It never silently rolls back a failed repair, and it refuses to invoke the agent when `npc_sim/` or `tests/` already has uncommitted changes. A repair agent with write access can change code and run commands: only use a trusted executable in an appropriately isolated environment. The supervisor defaults to loopback; to preserve existing LAN access, set `NPCSIM_LAN_HOST` to the server's LAN IPv4 address and pass `--host 0.0.0.0` to the supervisor. Its server log is stored under the selected data directory. Existing running episodes may still be marked interrupted on an unclean server exit unless they were saved at a safe checkpoint; a reset loses progress. Pass `--test-command '[".venv/bin/python","-m","pytest","-q","tests"]'` to override the default full-suite validation command. Stop with Ctrl+C; the supervisor shuts down its server.

Open `http://192.168.1.143:8000` from another device on the LAN. Replace the example address with the server's actual LAN IPv4 address; allow inbound TCP port 8000 in the host firewall if needed. LAN mode grants **unauthenticated full admin access**, including controls and private run data, to anyone who can reach the port. Use only on a trusted network; do not forward the port to the internet. Without LAN mode the server remains loopback-only.

Open **http://127.0.0.1:8000** for the simplified **player view**. Choose a scenario
and your cast, click **New adventure**, then **Play**. Only the relevant
Play/Pause/Continue controls appear; **Restart** recreates the current adventure
with its existing cast and skills policy. Cast/scenario changes apply when you
choose **New adventure**. **Skills & challenge options** retains the NPC-only
ability policy. Watch the discovered map, characters, continuous story and
outcome without configuration editors or technical logs.

**Run mode & call/action budget** in Player or Admin view includes **Run until
complete (ignore budget limits)**. This opt-in setting applies only to the current
adventure and bypasses action, model-call and active-time caps without changing the
configured budgets. Model charges can continue without a spending limit. Village
cycles pause at the next midnight (even if their goal is achieved earlier);
Continue starts the next daily cycle. Other adventures stop at authoritative goal
completion. Manual pause/cancel, bounded per-request timeouts, error handling and
non-progress safeguards remain active. Turning the option off restores the original
limits against the actual accumulated usage. Changing it does not start play.
Budget-exhausted saved local adventures can enable it and reopen paused.
The operator API is `POST /api/adventure/run-mode` with
`{"run_id":"current-run-id","run_until_complete":true}` (use false to disable).

**Add budget** in Player or Admin view adds model calls, action
attempts, or active real-time minutes to the current adventure's frozen limits,
without changing future defaults or resetting its world, clock, cast, or history.
Use **Add budget**, then **Continue** if the adventure stopped at a budget limit.
Every exhausted limit must be extended; extra calls alone cannot fix an exhausted
action limit. Extending never starts a stopped adventure or calls a model.
World view links to this editor when a limit is reached and stays read-only.
Budget-exhausted built-in local worlds can also reconnect from their saved world
and frozen scenario after a server restart. Other interrupted or finished outcomes
cannot be reopened this way. Previous outcome reviews remain in event history;
their now-stale failure feedback is removed when the adventure is reopened.

Use **Admin view** in the header (or **http://127.0.0.1:8000/?view=admin**) for
setup, detailed perspectives, archives and technical inspection. **Player view**
returns to the simplified screen. Switching views does not stop or reset the
live episode; unsaved form selections are reloaded. These are presentation modes,
not authentication or authorization: both use the same local API, and the admin
view remains accessible without a login. The service is loopback-only.

Player, World, and Admin views fill the available window width, keeping their
normal edge padding and responsive layouts rather than a fixed maximum width.

### Live town map

World view displays the supplied Millhaven illustration with named residents and
present visitors grouped at their authoritative building/location. Select a marker
for activity details; use the map zoom buttons and scroll to explore. Placement is
location-level, not precise indoor coordinates or continuous walking. Locations
not pictured (such as the iron mine) and unverified positions are explicitly listed
off map. On wider screens the World timeline sits to the right of the town map;
on narrower screens it stacks below. Other, non-village adventures retain their existing World view.

Confirmed successful `say` actions appear as speech bubbles for 18 real-time seconds,
with a recent spoken-dialogue panel below the map. Requested/rejected actions,
private questions, memories and model reasoning never become bubbles. Historical
events populate recent dialogue without replaying on initial load or reconnect.
Bubbles expire even while paused and stop following a speaker who changes location.
The map uses a bundled local image; no external mapping service is required.

### Named episode save points

Use **Save episode** in Player or Admin view, enter a name, and save. The controller
waits for the current turn to finish, pauses, and captures the exact world, frozen
scenario/cast, private NPC memories, complete transcript, timers, goals, budgets,
usage counters and next-turn state. Saving does not call a model or reset progress.
Save points are persisted under `data\saves` and remain unchanged when play continues.
Episode saves are local runtime data and excluded from Git; copy them separately
when moving to another machine. A repository clone includes scenario definitions,
not saved episode progress.

**Load saved episode** opens that saved moment as a separate episode and world
with new session identities. The original episode/history and named save are never
overwritten. Active saved episodes load paused; use **Continue** to resume. Completed
episodes load with their existing result/review for questions, not automatic replay.
Unconfirmed action boundaries are explicitly questions-only and cannot be resumed.
Missing/corrupt saves and unsupported external world adapters are rejected.

Loaded NPCs answer privately from their own saved observations and memories.
Until you resume/step, questions use separate `followup_model_calls`, even if the
saved decision budget is exhausted. Questions can incur model charges but do not
advance the clock, consume actions, or alter the saved point. Resuming restores normal
interaction accounting and retains the actual accumulated adventure budget usage.
Explicitly saved paused checkpoints reconnect paused after an app restart without
replaying actions or automatically reviewing them; uncertain crash boundaries retain
the existing interrupted/reconciliation safeguards.

Operator API: `GET /api/saves`, `POST /api/saves` with `run_id` and `name`, and
`POST /api/saves/load` with `save_id` and the expected `current_run_id` (null if idle).
Saving/loading requires the built-in local stdio world and makes no model calls.

### Living Millhaven

The engine supports opt-in supplier orders, demand-limited production,
breakfast, funded meal pricing/subsidies, reserve and freshness forecasts,
personal activities, multi-day projects, bands, optional quests, minor events,
temporary visitors, and bounded merchant imports/exports. Existing scenarios
and frozen adventures keep their previous behavior.

Generate the configured living variant before starting a backend with the new
code, then select **Millhaven Living - Work, Friends and Visitors** for a new
adventure:

```powershell
.\.venv\Scripts\python.exe -m npc_sim.living_village
```

The generator creates a separate scenario and never advances or replaces a saved
adventure. Regeneration requires `--force`. Restarting an already running backend
is a separate operation; do not restart a valuable paused adventure without
preserving its recovery state.

To upgrade a legacy village already paused at midnight, run the migration helper
against the still-running local backend:

```powershell
.\.venv\Scripts\python.exe -m npc_sim.village_migration
```

It backs up the raw original files and validates any deterministic overnight
clock reconciliation against the current observation. It creates named original
and upgraded saves, then stages a **separate paused copy** for the next backend
startup. Restart the backend after successful preparation; do not continue the old
episode during preparation. Runnable NPCs or inconsistent observations abort the
operation rather than inventing turns. The copy retains goods, money, repairs,
shipments, private memories, history and budget usage; no models are called.
New projects/time-use start at the boundary, and one-off visitors/quests target
the upcoming day rather than replaying yesterday. Existing living episodes cannot
be reinitialized. The current budget mode is preserved, so unlimited execution
still requires the explicit **Run until complete** option.

NPC observations advertise only enabled actions and locally available knowledge.
Living Millhaven also includes a **Town maintenance checklist**. Torvin inspects
the well fittings and services forge tools; Hobb inspects the mill woodwork and
services his workshop. Each has two 30-minute jobs, released at 08:00 and due at
17:00. These are required daily duties rather than optional hobbies.

Workers must travel to the actual site and use real configured equipment and
supplies. `village_maintain` reserves up to 15 productive minutes per turn, counts
as work, and protects meals, shared equipment and walking home before bedtime.
`village_maintenance_pause` retains only earned progress; any materials already
consumed are not refunded or charged again when work resumes. Routine upkeep
records completed service, not imaginary production or automatic structural repair.

At midnight, completed jobs recur for the next day. Unfinished jobs carry forward
with their progress and original requirements, without stacking duplicate jobs or
preventing the midnight pause. Saves retain the entire checklist and work history.
Legacy scenarios remain opt-out. The DM/operator can configure the `maintenance`
section of the existing village-configuration API, including task assignments,
sites, work duration, daily release/deadline, equipment and material requirements.
Configuration changes apply to future jobs; existing unfinished jobs retain their
original definition. Pause active maintenance before changing its configuration.

Living Millhaven also enables **Occupational research and discoveries**. When
ordinary work, funded orders, meals, horse care and maintenance leave real free
workday time, NPCs propose new occupation-related recipes instead of long idle
waits. Examples include a baker's new loaf, a farmer's edible crop variety, or a
carpenter's repair kit. Proposals are automatically validated against actual
skills, finite supported effects, known ingredients and real equipment; arbitrary
code, imaginary resources and unsupported abilities are not executable discoveries.

`village_research` advances study in 15 productive-minute chunks, only during the
workday and finishing by 17:00. Each 60-minute experiment consumes its real inputs
once and can fail. Defaults require **240 study minutes and three successful
trials**. Experience improves trial odds, but success is never a narrative claim.
Progress, consumed trial materials, failures and discoveries persist across days
and episode saves. `village_research_pause` keeps only earned study; interrupted
production/use discards the unfinished batch without refunding materials.

Successful research adds a readable discovery to the world and unlocks
`village_research_make`: actual inputs, tools and recipe duration produce one real
stock unit, with no free discovery inventory. Food/crop stock lasts one day and
can be ordered, physically delivered to the tavern, and prepared with
`village_research_use` into two meal portions on the real hearth. Crops need at
least 60 minutes to grow. Tools are consumable repair kits: each restores 25 health
on a real damaged occupational object; they do not bypass structural mill
repairs. New materials are usable inputs in subsequent researched recipes.
Production meets accepted orders or keeps a single demonstration unit rather
than creating unlimited surplus. Research and discovered production count as work
and reserve shared equipment.

NPCs learn verified recipes through `village_research_share` in person or by
reading the actual discovery at its site; private research and remote stock
quantities are not automatically shared. Knowing a
recipe does not grant its required production skill. The town/NPC research
checklists show progress, trial outcomes, blockers and actual unlocked recipes.
The World view also shows the active researcher's project title, earned study minutes
and successful trials beside their location. Finn can also complete a recorded
20-minute riverbank cleanup once per day; it creates no fictional salvage. Optional
site rests are limited to one completed session per resident, site and day; ordinary
sleep is unaffected.
The revision-checked village-configuration API accepts `research` with `enabled`,
`required_minutes` and `required_successes`. Changes affect new projects, not
existing requirements; pause active research/batches before configuring it.
Legacy scenarios and previously saved episodes remain unchanged unless explicitly
enabled on a restored/current run.

Living Millhaven has two real iron supply routes:

- **Daily imports:** the iron trader walks in from the mine road (or bridge in the expanded scenario) just before noon, brings four units to the square at noon, and walks out at 18:00 with unsold stock.
  With mandatory orders enabled, the smith
  uses `village_buy_iron` face-to-face to create a funded, accepted consignment
  and collect actual cargo. Carry it to the forge and unload to pay; ordering
  never teleports stock. Another carrier may collect an already accepted order,
  but cannot silently place a purchase on the smith's account. Existing cash,
  carrying limits and commitments still apply.
- **Renewable local mine:** a real mine road connects the square to the iron
  mine, which starts with **zero ore**. The smith uses the actual pick and
  `village_mine_ore` for a 30-minute extraction, then `village_load_ore`, real
  travel and unloading to bring the ore to the forge. Owned ore is not an
  invented purchase: it transfers without goods payments or delivery fees.
  `village_smelt_iron` consumes **one ore and one log** in the real furnace,
  taking another 30 minutes to produce one usable iron. Procure fuel from the
  forester in person. Mining/smelting obey needs, body/tools, shared equipment,
  hunger/fatigue, meals and bedtime. Interrupted batches create no output and
  consumed smelting inputs are not refunded. Stock, cargo and in-flight work
  count toward demand so mining does not create unlimited surplus.

The other primary supplies are already repeatable: Ada grows grain/hay, Finn
catches fish, Iona gathers herbs, and Rowan fells logs. Manure comes from feeding
the horse hay. Milling makes flour from grain, forestry makes timber from logs,
carpentry makes planks from timber, and cooking/tea use their real ingredients.
Tools and one-off structural quest objects are distinct from bulk raw stock.
The mine intentionally uses renewable logs rather than introducing an
unreplenishable coal resource. Previously saved episodes remain unchanged;
enable `mining` through the revision-checked village configuration API on a
current/restored run. Its fields are `miner`, `enabled`, `mining_minutes` and
`smelting_minutes`; pause committed mining/smelting before reconfiguration.

With communication disabled, supply orders must be placed **in person**: buyers find their actual supplier,
meet them while they are available to talk, and use the ordering action to state
the requirements. Demand suggestions do not send remote orders. This also applies
upstream (inn to baker, baker to miller, miller to farmer). The supplier separately
accepts or rejects the proposed commitment. NPCs see their own buying/selling
orders; the monitoring dashboard retains the global ledger. Deliveries still
require real goods, collection and travel.

#### Physical village communication (Phases 1 and 2)

New **Millhaven Living** scenarios enable physical communication by default.
Existing worlds and named saves remain opt-out; loading one never installs
fixtures or fabricates retrospective receipts. The shared engine advertises
`village_notice_post/read/update/reply/close/tell` and
`village_inbox_leave/read`. Posting, reading and replying cost **five minutes**
per actor; a real conversational listener also spends five minutes. The
ordinary free map read remains map-only; it does not remotely read notices.

The existing `noticeboard` at Village Square holds public paper versions.
Every resident has a fixed `workplace-inbox-<resident>` at their actual workplace.
Anyone may write a directed note **while at the recipient's box**; only its
owner can read it there. A supplier replying via an inbox must physically
visit the requester's box. Notes do not move between boxes, enter inventories,
or appear remotely: portable letters/couriers are not implemented. Check a box
explicitly on a normal workplace visit; there is no remote or automatic read.
Private/restricted notes cannot be posted publicly or relayed.

World notice history, paper placements, independently received snapshots and
speaker/listener deduplication are separate. A reply is not automatically
known by its requester. Old paper and memories stay stale until a new
version is actually published/read or a real event is witnessed. Provenance
keeps the original author, latest speaker, at most four chain entries and
eight relay hops; repetition never improves credibility. Up to three relevant
novel encounter candidates are ranked deterministically. This does not
automatically speak, send anyone travelling, or share with absent/busy sleepers.
Global bell broadcasts and live noticeboard stock reports are disabled for
opted-in worlds. Existing legacy memories are not erased.

Supply acceptance creates a normal funded economy order, never goods or coins.
Intent-level `village_request_supply`, `village_respond_to_request`,
`village_update_request`, `village_check_messages`, `village_share_known_notice`
and `village_propose_meeting` fill in engine-known notice fields while preserving
local channel, ownership, and witnessed-conversation requirements. Personally
posted supply requests persist as private `supply_needs` in the author's fallback
state; an open request is not a reason to chase its supplier without new local
evidence. Repeated visits to a supplier for that unresolved personal need raise a
search-loop signal; locally observed stock or personally learned notice changes
reset the signal, without blocking ordinary travel or work. These records do not
give other residents remote knowledge.
New in-person ordering uses the same received-notice/reply path rather than
leaving a remote-status bypass; one physical conversation proposes one need.
It checks freshness, deadlines, feasible real supply, prior commitments and
reserves. A stock-backed order uses uncommitted fresh goods; a production-backed
order reserves feasible inputs and a scheduled production slot rather than
pretending its output already exists. Infeasible supply is countered or declined
with a reason. Public repair jobs can instead authorize a physical material
transfer without a commercial purchase, while retaining ownership, reserves and
carrying checks. Physical loading uses the same reserve-aware allocation;
production, cargo capacity, unloading and payment remain the ordinary engine mechanics. Coordination-linked
order observations, suggestions, cash commitments and action menus use received
snapshots rather than leaking unreceived replies. Older pre-installation orders
retain their original behavior. A verified status can be published with
`village_notice_update` **without** a `notice` field; it still requires a local
channel and an exactly compatible personally received version.

Well/mill meetings are versioned appointments proposed by the actual project
crew at the actual site, with an explicit future `due_at`. The initial proposer
signs only for themselves; the partner accepts through a real channel, and both
must personally receive confirmation. Attendance is a personal plan, not
automatic movement. Work is permitted during the one-hour appointment window
only with both actual workers at the site and the original real supplies; each
worker still contributes their own actions. A partner's ongoing project turn
counts as participation, not as an unrelated availability conflict; travel and
other commitments still block work. Changing unaccepted meeting terms
revokes prior signatures; committed meetings must be explicitly cancelled
before rescheduling.

To opt an **existing paused** timed AI village into both phases, send
`POST /api/village/configure` with a fresh observation revision:

```json
{
  "run_id": "<connected paused run>",
  "request_id": "<unique installation ID>",
  "expected_revision": "<fresh obs-N>",
  "configuration": {
    "communication": {
      "board_id": "noticeboard",
      "workplace_boxes": true,
      "max_hops": 8,
      "cooldown_minutes": 15
    }
  }
}
```

This adds only the twelve fixed inbox fixtures for `ada`, `bren`, `elsie`,
`finn`, `garrick`, `hobb`, `iona`, `jory`, `mara`, `nella`, `rowan`, and `torvin`.
The new `village.communication` contains that owner mapping in `boxes`, the
configuration above, `next_notice: 1`, and **empty** `notices`, `knowledge`,
`placements`, `shares`, `cooldowns`, `failed_contacts`, and `failed_replies`. Stocks, positions,
coins, clocks, schedules, existing funded orders, runs and named saves are not
reset. Configuration is revision-checked, idempotent by request ID, and
transactional on fixture collisions. An installed ledger cannot be silently
reconfigured or removed through this endpoint.

The operator village dashboard shows authoritative notices, commitments,
appointments and physical box counts. The selected NPC view instead labels
their own received versions with source, receipt time, confidence and freshness;
operator truth never enters the compact NPC prompt.
The read-only World route (`/?view=world`) includes the same shared
**Operator: noticeboard, commitments & appointments** panel and
**Physical workplace inboxes** summary, even when enabled with no notices.

Deterministic evidence: `tests/test_village_communication.py` includes a paired
Day-3-like scripted replay using a **constructed matched fixture** from the
expanded scenario, starting at **minute 3360 (Day 3 08:00)**, **not** an archived
Day 3 snapshot. Its initial stocks, positions, schedules, prices and constraints
match between arms (only empty capability state/fixtures differ).
Control → treatment: fruitless contact trips **2 → 0**, person-search walking
**35 → 0 minutes**, all walking **75 → 65 minutes**, blocked actions **2 → 0**,
world actions **24 → 22**. Both deliver **2 units**, fulfill **1 order**, pay
**4 goods coins + 1 delivery fee**, bake **2 bread**, and eat **1 meal**, with
**0 missed meals** at the common cutoff and **670 total conserved coins**.
There are **zero model calls**. This demonstrates engine feasibility, not
universal AI behavior improvement or a replay of the original AI day.
An additional appointment replay runs twice from identical initial state:
Torvin and Bren start apart, use **12 real `go` actions**, retrieve the existing
well supplies, and receive confirmation through an actual local conversation.
Work rejects missing personal confirmation and an absent coworker; six real
contributions (three each) consume the original timber/rope and complete the
well by 11:55, including save/restart verification. No travel is automatic,
no actor positions are assigned during replay, and no model calls are made.
The separate `test_real_day3_midnight_backup_paired_supply_compatibility_replay`
is a reusable offline driver for the **actual minute-2880 midnight baseline**.
It reads only `baseline.json` and adjacent frozen `scenario.json`, hash-checks
both unchanged, and reconstructs their real actors, entities, inventories,
stocks/lots, coins, resident life/schedules, reserves and existing contracts.
Control/treatment initially match completely except empty communication
state/twelve fixtures; no stock, money, position or policy is made easier.
Both workers wake normally, physically try the empty tavern at breakfast,
record their two missed meals, and transport one existing workshop plank to
the forge: **1 delivered unit, 3 goods coins + 1 fee** in each arm, with a common
09:40 cutoff and zero model calls. This is a short compatibility/delivery
script, not a full-day AI replay or evidence of improved meals.
The driver defaults to
`data\backups\livestock-3b3f16b2d9cc4d3787062c60bac114c1\baseline.json`;
set `NPCSIM_DAY3_BASELINE` to another compatible archived baseline path.
Run `.\.venv\Scripts\python.exe -m pytest -q -s tests\test_village_communication.py -k real_day3_midnight_backup`.
Without the default archive it explicitly skips; an invalid explicitly supplied
path fails. It never reads/copies the large `world-state.json` or writes backups.
The separately archived original partial Day 3 (00:00–17:10) had 999 actions,
617 movement events and 3,220 resident-minutes travelling; its 97 explicitly
named-person contact intentions are only a lower-bound intention metric,
**not** an instrumented fruitless-trip count. These unequal-duration records
are not a causal comparison, and no new paid AI replay was run.
Committed leisure and performances reserve actual time; the scheduler does not
request a model decision every five minutes. Meals, travel home, sleep, and animal
care take priority. Optional interests and sidequests do not gate village victory.
World/Admin monitoring includes orders, reserve shortages, cash flows,
price-valued inventory (not net profit), projects, events, time use, and visitor
history. Visitor Gantt rows end at actual departure and remain in historical views.

To let the Dungeon Master adjust village policies or add validated event/quest
templates, add `configure_village` to its capabilities in the Admin configuration
editor. This authority does not permit arbitrary code, invented stock, teleporting,
or granting victory. Without that capability, configuration proposals are rejected.

Living Millhaven now includes **one physical cow and twelve adult hens**, not
extra AI residents. There is no starting milk, egg, feed or water grant.
Ada carries `livestock-bucket` to the completed, usable well and draws two
care portions in five minutes, then carries it back to the farm. Daily cow
care takes 15 minutes and consumes one real hay; hen care takes ten minutes
and consumes `ceil(hens / 4)` real grain (three for twelve hens). The real
brush, bucket and housing must be usable, and the actual animals must be
present. Horse hay, reserves and accepted grain deliveries remain protected;
feed needs enter Ada's demand-led harvest/hay planning. Only completed care
of fed animals produces manure, one unit per housing.
Required feed can finish an indivisible two-unit harvest batch for a one-unit
care shortfall; this cannot trigger extra batches once demand is covered.
Native production rounding remains unchanged when livestock is disabled.

Fed, watered, cleaned cows permit one 15-minute milking in each daytime
window, 08:00–13:00 and 13:00–18:00, at most six milk each. Hens permit one
ten-minute collection per day, at most one egg each. Output is further bounded
by at least four game hours between milking starts, preventing boundary-adjacent
double milking. Production is bounded
by actual reserves/accepted orders, current usable stock and committed work;
capacity is not a requirement to create unsold food. Interrupted collections
earn no output and still use their daily session. Interrupted cleaning retains
earned work and already consumed feed/water; after midnight unfinished cleaning
can resume but animals require today's feed/water, with no retroactive yield.
Meals, bedtime, real travel, bodies, tools and active reservations are enforced.
Care precedes optional research/leisure; feasible urgent funded grain precedes
optional milk/egg work.

Milk and eggs each cost two coins; milk lasts 24 game hours and eggs 48.
The inn's planner suggests modest milk/egg supplies without changing existing
bread/fish commitments. Elsie must meet Ada, place funded orders, obtain her
acceptance and arrange actual cargo delivery through standard goods logistics.
`village_cook_dairy` consumes one delivered milk and one egg at the real hearth
in 15 minutes to make two ordinary paid meals. No butter/cheese work is added.
Validated occupational food trials may also use actual milk/eggs.

New Living scenarios install livestock by default. Legacy midnight upgrades
remain opt-out; add `--livestock` to the migration helper to opt in. For an
existing paused Living world, the safe configuration request is:

```json
{
  "run_id": "<connected paused run>",
  "request_id": "<unique installation ID>",
  "expected_revision": "<fresh observed revision>",
  "configuration": {"livestock": {"enabled": true, "keeper": "ada", "hens": 12}}
}
```

Food recycling is enabled with this same optional livestock module. Deliberate
`village_make_plant_scraps` takes a product and quantity (one to three), consumes
the actual unexpired **plant-only surplus**, and earns the same number of scrap
units after five minutes per unit. Provenance rejects fish, milk, eggs, animal
meals, ambiguous old meals and rotten/mouldy food; current plant-only meal and
discovery recipes retain real ingredient evidence. Native meals, balanced feed,
reserves, raw inputs needed for production and accepted orders are protected.
Scraps expire after at most 720 minutes, never later than the source food.
For twelve hens, one real usable scrap can replace one of three grain, leaving
at least two grain; generally substitution is capped at one third of the
grain ration. It never replaces cow hay.

Actual food spoilage preserves an equal, finite quantity of `compost-material`
at that physical storage/cargo location, never feed. Reprocessing spoilage cannot
duplicate it. Both scraps and material use ordinary one-coin, funded, in-person
orders and real hauling; recycled goods at non-workplace locations can be
contracted there with a present supplier. At the farm, `village_compost` consumes
two uncommitted `compost-material` or safe `plant-scraps`, uses the actual
`compost-pile` and `compost-fork`, and earns one nonperishable compost fertilizer
after 20 minutes, capped at a two-unit buffer. Interrupted composting earns
nothing and does not refund inputs. `village_fertilize_compost` consumes one
actual compost for the existing three-faster-harvest benefit; it is never
recorded as animal manure. All three recycling resources cost one coin and
begin with empty stock/lots at every existing stock location.

Post to `/api/village/configure`. This installs only new animals/tools/housing
and empty milk/egg stock/lot keys at farm, bakery and tavern, plus the three
empty recycling ledgers at existing stock locations; no resident, stock,
coin, clock, task, run or named save is reset. The standalone livestock audit
event does not invent resident memories. Repeated request IDs are idempotent;
stale revisions and fixture conflicts abort atomically. Pause active work before
reconfiguration; installed flock counts cannot be changed through this API.
Fixtures are `farm-cow`, `farm-hen-01` through `farm-hen-12`, `cow-stall`,
`hen-coop`, `livestock-bucket`, `livestock-brush`, `milking-pail`, `egg-basket`.
Recycling adds only `compost-pile` and `compost-fork`.
Care state, water, daily counters and work chunks persist in world/episode saves.
The village panel shows care status and water; ordinary stock and work monitors
show milk/eggs and clock-earned livestock work.

Operators can configure a **paused, connected** village using
`POST /api/village/configure` with `run_id`, a unique `request_id`,
`expected_revision` from the current observation, and `configuration`. Supported
configuration sections are `economy`, `social`, `events`, `maintenance`, `research`,
`mining`, `livestock`, `communication`, `firewood`, and `day_policy`; schemas
are available in the API's OpenAPI document. Each supplied section replaces that
section's configuration, rather than silently merging unknown fields. Runtime
settings persist in the world without changing the frozen scenario. The privileged
MCP equivalent is `world_configure_village`; it is never advertised as an NPC action.
Conflicting request reuse and stale revisions are rejected.

### Firewood and shared household fires (opt-in)

The checked-in `millhaven-living` scenario now enables firewood with three shared cottage hearths and zero starter fuel; existing saved adventures retain their own frozen configuration. New scenarios generated without `--firewood` remain opt-in.

Generate a **future** Living Millhaven scenario with
`python -m npc_sim.living_village --firewood --anti-idle --output data\scenarios\millhaven-firewood.json`.
This does not regenerate, restart or enable fuel rules in an existing adventure.
Preserve the current episode before restarting the backend to load Python changes.
Existing scenarios/saves without `firewood` keep their previous fuel rules.

For an already connected, paused living village, the configuration API above accepts
this complete `firewood` section (it requires the existing funded economy):

```json
{
  "firewood": {
    "supplier": "rowan", "household_buyer": "iona",
    "households": {
      "hearth-1": ["bren", "finn", "garrick", "hobb"],
      "hearth-2": ["iona", "jory", "mara", "nella"],
      "hearth-3": ["rowan", "torvin"]
    },
    "businesses": ["torvin", "elsie", "jory"],
    "season": "autumn"
  }
}
```

These are shared-hearth groups at the existing `cottages` home location, not newly
invented individual houses or family relationships. Every cottage resident belongs
to exactly one group; residents living at the inn or farm do not use cottage hearths. Iona works at cottages and buys fuel for its communal physical
woodpile from her existing purse; no money or starter firewood is granted.
Business owners fund their own workplace fuel. Requests use real quoted prices
(default one coin per firewood), conservative existing delivery-fee commitments,
supplier acceptance and actual foot/wagon cargo, travel, unloading and payment.
Rowan can explicitly save and advance a feasible grouped fuel route with
`village_execute_fuel_delivery` (one real load, door traversal, or paid delivery
per action); the action never starts or advances a route automatically.
Blocked routes retain cargo for later resumption.
Fuel planning requests only the affordable part of a shortage; empty purses do not
silently create credit. Buyers must inspect local supplies before using physical
communication channels; remote stock, unread orders and whereabouts are not shared.

Rowan's `village_split_firewood` consumes **one actual log**, uses his actual axe
and commits **15 minutes** to produce **four firewood**. These defaults are
configurable. Log reserves and the logs needed for construction timber remain
protected. Felling targets account for real pending fuel production, avoiding a
reserve/replenishment deadlock. Firewood production is capped by accepted orders
plus one splitting-batch reserve; indivisible batches can leave at most one batch
minus one unit of additional stock. Committed output counts toward the cap.
The axe is reserved while splitting; failed batches lose their actual inputs.

Each successful bakery, tavern cooking (bread, fish or dairy), or forge batch
burns **one firewood**, not one per portion. Smelting consumes ore plus firewood
**instead of** ore plus logs, never both. Heated research pays fuel when trial
materials are first consumed and when making/using a heated discovered recipe;
continuing an already-funded trial does not burn again. Recipe-specified firewood
counts toward fuel rather than being charged twice. Rejections and replayed requests
do not burn extra fuel. Missing fuel explicitly blocks hot production.

Between **18:00 and 07:00**, a household with at least one member physically home
automatically lights/tends its shared hearth from the local pile: one unit gives
four hours in spring/autumn or two in winter, capped at 07:00. Summer households
do not heat; business cooking still burns fuel. Unoccupied groups burn nothing.
Cold homes are visible but impose **no new health penalties**. There is no
backdated heating debt. `season` is an explicit operator setting, not automatic
weather/calendar simulation; rate changes affect the next fuel burn.

A late firewood shipment remains loaded and unpaid until its buyer physically
accepts or rejects it at the destination with `village_respond_late_fuel`.
Acceptance permits one real unload and settlement; rejection leaves cargo with
its carrier for physical return. Neither response creates fuel or advances the
carrier automatically.

Local observations expose only physically present fuel stocks/hearths. The World
observatory additionally shows privileged town-wide household warmth, shortages,
consumption and responsible buyers. Configurations, warming intervals and fuel
ledgers persist in ordinary world/episode saves. Runtime configuration preserves
the frozen scenario, existing goods, money and progress; installed participants
and household memberships cannot be replaced through a later patch.

### Purposeful downtime (opt-in)

Generate a **new** Living Millhaven scenario with `python -m npc_sim.living_village --purposeful-downtime --output PATH`. This enables anti-idle planning and its shorter daytime reviews, physical noticeboard checks and mill rendezvous suggestions. It also installs finite furniture, tackle and smoked-fish crafts, catch-and-release fishing, Iona's herbal garden and gardening aptitude (without farming privileges), and a scheduled visiting merchant with explicit offers for those goods. Garden tending consumes actual manure carried from the cared-for hens and yields one herb only after a day; checking the woods and harvesting herbs remain separate actions. Bread-delivery and blocked-order suggestions require real stock, routes, knowledge and deadlines. Activities, visiting sales and repair are not automatic: residents must execute eligible actions. Existing saved episodes are unchanged; generate into a new path rather than overwriting them. See [the enhancement spec](ANTI-IDLE-ENHANCEMENT-SPEC.md) for limitations.

### Occupation fallback / anti-idle planning (Phase 1)

This is opt-in. Set `day_policy.anti_idle` to `true` using the paused-world
configuration API above, **copying all existing day-policy values** because that
section is replaced, not merged. It requires an AI-controlled, timed daily-life
village. Alternatively, generate a future Living Millhaven scenario with
`python -m npc_sim.living_village --anti-idle` (existing output requires `--force`).
Neither code changes nor regeneration enable or restart a running/frozen adventure.
Restart the backend with the updated code only after preserving the current episode.

The engine supplies a small eligibility-filtered list of actual occupation actions,
due funded meals/rest and a five-minute local workplace/tool inspection using the
existing `village_observe` action. It never creates new chores, goods, maintenance
benefits, tools, food or automatic travel. Ordinary production still uses the same
stock, reserve, capability, equipment and cooldown validators at execution time.
Required maintenance, research restrictions and existing commitments retain their
existing engine guards. Occupation-specific new task catalogs remain Phase 2.

Per-actor blocked-action records preserve the real rejection, a structured reason,
arguments and a relevant-context fingerprint. An unchanged retry is explicitly
rejected as `RETRY_SUPPRESSED`; ordinary world-revision increments do not reopen it.
A live meal forecast shows expected diners, real prepared portions, funding,
shortfall, input demand and a configurable preparation cutoff. When stock is
scarce, a deterministic resident allocation reserves real portions through the
meal grace window; a missed diner can still recover when food or funds arrive.
After grace, an arriving diner may still eat unclaimed, fresh, funded tavern food
before the inn closes at 22:00; no reservation or extra portion is created.
The tavern cook prioritizes feasible meal preparation over optional research.
Empty-meal retries depend on the meal, food and funding, not unrelated chatter or
tool observations. An empty/unfunded attempt can mark a meal missed, and a
later real, paid meal can recover that outcome. Meal preparation now has a
configurable cutoff; the forecast never creates stock or guarantees attendance.

Useful inspections are limited until visible conditions change or the configured
review interval passes. With anti-idle enabled, an idle resident away from their
main workplace returns there by a real, traversable walk before waiting, provided
there is time before meal and rest commitments. Existing committed routes are
respected; an early diner may remain at the tavern for breakfast. Once at work,
or when no safe route is available, `village_wait_event` records its reason and
wake triggers, preserving all other villagers' independent turns. With anti-idle
enabled, repeated short idle `village_wait` polling is rejected.
An event wait initially lasts at most five minutes; if the next decision is another
wait, it lasts at most ten minutes. A different successful action resets that interval.
`idle_review_minutes` defaults to 60 (15–240, multiples of five) for inspection
reviews; waits are also bounded by known events and walking/action time before meals and bedtime.
Local stock/gear changes, real encounters, personally received notices and needs
can interrupt a wait; hidden remote stock alone cannot. A resident in an event wait
can be contacted in person for a conversation or supplier order, but cannot take
another action until their next turn; sleep, committed work and remote contact remain
blocked. Scheduler ticks do not call
the model. No guarantee of constant work or extra timber is made.

In Living Millhaven, ordinary walking now requires `village_plan_walk` with a final
location and concrete purpose before taking a `go` step. The resident's personal
`walking_plan` records the origin, destination, ordered doors and stops along the
shortest traversable route (fewest door crossings). Intermediate stops do
not reset the plan: the next `go` must follow the next door, preventing reversals
like a store–square–store loop. A newly received message, due meal, rest,
schedule change or blocked door permits replanning; arrival clears the plan.
Walking itineraries persist in episode saves and do not replace automatic wagon
or timber routes. Residents use the public village road and footpath map to plan ordinary walks;
hidden doors and blocked passages are excluded.
Existing running episodes require a backend restart before this engine change takes
effect; restarting the process does not itself reset their saved state.

Planning state persists in world and episode saves. The personal `village.fallback`
observation contains blocked tasks, candidates, the selected decision and wait state.
Private structured village events include `TASK_BLOCKED`, `FALLBACK_SELECTED`,
`FALLBACK_STARTED`, `FALLBACK_COMPLETED`, `WAIT_STARTED`, `WAIT_INTERRUPTED` and
`TASK_REEVALUATED`. These are engine evidence, not NPC speech.
Deterministic Rowan Day 3 regression tests are in `tests\test_village_fallback.py`;
they are not a claim that a paid, nondeterministic historical model replay was run.
Need-aware response guidance, intent-level notices and personal repeated-supplier
visit detection are available. An optional meeting/search goal can be recorded with
`plan_optional_route`; a visit defers repeat trips along the known route until
personally received evidence changes. The guard does not constrain ordinary travel,
deliveries, patrols or tool collection. It does not infer a goal from every move or
suppress a real task simply because a resident once visited its destination.
A local engine pre-check rejects known invalid patrol, capacity, channel and
research attempts before time is spent; authoritative execution still validates
private facts. Per-resident/day time-use reports distinguish work, waiting and
suspected no-progress reversals. These are diagnostic counters, not a claim that
all movement is waste. Automatic optional-goal assignment, broader task catalogs
and matched multi-day replay comparisons remain later phases.

### Private questions and live world changes

Use **World view** in the header (or **http://127.0.0.1:8000/?view=world**) for
read-only monitoring: current game time and every agent grouped by actual location,
with current activity and clearly labeled last actions. It updates live without
model calls, simulation controls, configuration, private plans, or transcripts. Stopped
adventures show their last recorded world state; unavailable live positions are
explicitly labeled. Switching views never pauses or resets the adventure.

Timed worlds also show a live **World timeline** Gantt chart: one row per agent,
location-coloured bands, taller rounded action/availability tiles, free-action diamonds, sleeping
and wagon-route bands, public milestones, and a current-time line. The 64-pixel-high
tiles show an activity icon, duration, clock range, and a recorded-result summary as space
allows; zoom in to reveal more detail in shorter activities. Hover or focus a tile
for a rounded white detail card showing the agent, activity, status (when recorded),
time range, duration, location, and any distinct recorded result.
Collapse the Town map to give the timeline the full available width, and show it again when needed. Unknown history stays blank;
scheduled intentions are never drawn as completed work. The chart follows the
latest activity until you scroll back, and keeps agent names visible while you
scroll horizontally. Hover or focus a name to see the resident's location, occupation,
activity, carried items, site stock and available coin balance; Town-map names show
the same details. Visitor details omit unknown personal inventory and funds. The
timeline matches the other gray panels. Use **+** and
**-** to zoom its timescale from 100% to 800%, or **Fit** to show the full recorded
period at the available width. Tick spacing adapts to the zoom level; zooming never
changes simulation time. It replays saved events when opened or reconnected without
calling the model or changing the world. Completed merchant transfers appear as
markers on both the resident's and visitor's timeline rows, with paid quantities
and amounts; the World view also lists the latest settled trade notes. Standing
"Trading" activity and accepted but unpaid iron orders are not settled trades.
Confirmed spoken words appear separately in the Town map only when a speech action
was actually recorded. The location cards remain below the timeline.

Untimed adventures, including **The Unknown Room**, use the same chart with a
**turn-based axis** instead of clock times. Each recorded action attempt across
the cast occupies one turn, including inspections and rejected attempts; Turn 0
is the initial observation. Action tiles and location bands show discoveries,
item handling and backtracking; red diamonds mark rejected attempts. Turns are
not elapsed time or the NPC's needs clock. Only confirmed results enter the
timeline, and missing history is not reconstructed from the current location.
Existing adventures work without a scenario upgrade or restart.

Player and Admin views include **Ask an NPC** and **Dungeon Master intervention**:

- Select an NPC and ask a private question. It answers in character using only its
  observation, own goal and own memory, and remembers the exchange. Other NPCs do
  not receive it. Questions are communication, not verified world facts, and cannot
  execute actions or grant skills.
- Describe a world change in plain English, then choose **Preview change**.
  Review the summary, added objects, placement, concealment, spare-key bindings
  and full configuration. Nothing changes until **Approve change**.
- For example: "Add a concealed wall nook with a spare door key inside; reveal
  the nook only when the wall is searched." The wall is visible, the nook is not.
  An NPC must **search** the wall, then **open** the nook to see the key.
  Search is also available on ordinary visible local objects and the current room,
  so the presence of the action does not disclose where a secret is.
- Spare keys preserve the original matching key. Existing anatomy, handling,
  carrying limits and protected puzzle mechanisms still apply.
- Add **secondary goals** through the same preview/approval form, without adding
  objects if none are requested. For example: "Give Raven a side goal to find the
  key" or "Give Otter a side goal to bring the bowl to Badger." Cast names resolve
  to the adventure's actual NPC role IDs.
- Each side goal has explicit NPC/team assignments and completion conditions.
  The preview states whether any or every assigned NPC must satisfy an actor
  condition. All listed conditions must hold together. Supported checks are
  discovering an object by observation, carrying an item, arriving at a location,
  reading text, placing an item in a room/container, opening an object, or
  unlocking a lock. A delivery to an NPC is verified by that recipient carrying
  the item, not by an NPC claiming to have delivered it.
- The **Side goals** list shows independently verified progress. Completion
  stays recorded after the qualifying state changes, and emits an explained
  audit event. Goals are stored in the episode world; existing goals cannot
  be overwritten or marked complete by the model.
- Only assigned NPCs receive the side task and its authoritative status.
  Task instructions do not count as discovery or reveal concealed placements.
  Side goals remain optional: they do not replace the main objective, override
  body/skill limits, prolong an adventure after the main goal ends it, or
  resurrect a finished run. Unsupported free-form success criteria need a new
  deterministic condition type rather than an invented success check.

Private questions remain available for the current adventure after it ends,
including after restarting the app. Follow-ups use only that NPC's saved final
observation and own memory, never fresh world actions. They persist as private
conversation without changing the result, grade, action count or game time.
User-requested follow-ups are counted separately as `followup_model_calls` and
are not blocked by the finished adventure's model-call budget; each still makes
one model call with the configured timeout. Older adventures in History remain
read-only.

World changes need the current active adventure. They pause running play at a
serialized safe point and leave it paused until you continue. Private questions
and proposal generation count against the existing model-call budget; approvals
do not call the model. Questions and previews do not advance simulated time.
Model failures are reported rather than replaced by invented answers.

Approved interventions are logged and saved in this episode's world state, not
the source scenario or frozen starting configuration. They add objects and optional side goals, not actor
skills, direct inventory items or completed challenges. Approval uses the stored
proposal and its observed world revision; changes after preview require a fresh
proposal. Dismiss hides a preview without applying it. Completed/interrupted
episodes cannot be resurrected through this feature. Live edits require the
built-in local world adapter. The ordinary supervising Dungeon Master's authority
is unchanged; this is a separate, explicitly operator-approved channel.

### Millhaven: living-village adventure

Choose **Millhaven - A Village That Carries On**, then **New adventure** and
**Play**. The visiting agent (Avery by default) cooperates with **12 persistent
residents**, across **15 connected places**, with **53 tracked physical objects**
and 12 occupations. The village panel shows game time, nearby residents, local
resource stock, purchased resources, your requests and personally witnessed events.
Admin world's JSON includes the complete village audit, private resident beliefs
and their sources, memory event references, relationships, schedules' current
activities, stock, coins, carried delivery cargo and task evidence.

- Ask Torvin about the shovel, then request `fetch_shovel`. He first finishes
  urgent work, searches the stable using his old belief, asks Bren, walks to the
  mill shed, takes the real tool and returns. **Stay at the agreed meeting place
  with carrying space** to receive it. A blocked delivery is not completion.
- Drop the delivered shovel at Torvin's location and request `dig_well`.
  Torvin meets Bren, gathers the shovel, reserved lining timber and safety rope,
  walks with Bren to the tavern yard, and spends 30 minutes working. Completion
  consumes the timber and rope and records the dug well.
- The mill drive fails at **14:00 on the first day**. Request `repair_mill`
  from Hobb. He meets Mara, gathers his hammer and replacement beam, and brings
  the crew to the mill. Repair consumes the beam and restores the actual drive.
  The adventure finishes only after shovel delivery, the well, the repair and
  a resumed bread delivery are all engine-verified.

The advertised actions `village_ask` (shovel/work/news), `village_request`,
`village_tell`, `village_trade`, `village_cancel` and `village_wait` work through
the same validated MCP interface as other adventures. Reports can create false
beliefs but cannot move objects. Protected tools require their specialist;
production and delivery trades transfer real resource quantities and coins.
Buying resources uses a separate visitor resource ledger, not portable inventory.
Private questions in **Ask an NPC** still address the visiting cast member, not
the rules-driven residents; resident conversations use the advertised village actions.

**Time and scope:** This is a deterministic, action-driven living-village slice,
not every proposed feature in the old master spec. Each applied ordinary action
advances five game minutes; looking and reading are free. `village_wait` advances
5-1440 minutes in five-minute increments, processing every intervening schedule,
task, production job and delivery without additional model calls. Decision time,
pauses and a closed app do not advance the village. Residents gather at the tavern
at 19:00 and return home at 22:00; cooperative construction runs 08:00-18:00.
Personal news and beliefs can spread through actual evening meetings.
Grain, flour and bread depend on local stock, operating equipment, physical courier
travel, buyer funds and paid deliveries. The mill outage therefore causes baker
shortages, not just story text. Jobs and resources are intentionally small rules-based
systems; unrestricted natural-language NPC planning, arbitrary projects, theft,
weather and regional simulation are not implemented. Existing JSON saves persist
resident locations, beliefs, memories, tasks, consumed supplies, stocks and events;
no separate database service is needed.

### Millhaven Expanded: twelve independent AI villagers

Completion requires the full daily cycle, not just repairs: everyone must eat
the evening tavern meal and be asleep at their own home, the horse must be
released at its stable and unhitched, and automatic routes must be stopped.
The village must also demonstrate replenishing hay/herbs/logs and using manure
and tea. These are authoritative world checks shared with the completion reviewer.
After midnight, the previous evening's meal counts; a missed day can recover
with a complete later day rather than becoming permanently unwinnable.

An older village adventure that stopped as succeeded but received a failed
review offers **Continue unfinished day**. It rechecks the saved world against
the full daily requirements, then reopens **paused** only if work remains.
It preserves progress, budgets, time, identities and history, removes obsolete
failure feedback, and makes no model calls. Press **Continue** to resume.
Other completed adventures cannot be reopened this way.

A local village stopped by an MCP response timeout can also use **Recover
interrupted day** when all action requests have confirmed results and the saved
actor still has its free-action turn, or the next actor is already durably
scheduled but has not acted. Recovery refuses uncertain actions rather
than replaying them. If a dispatched action timed out after its result was saved,
recovery verifies the exact request, NPC/session, cycle, current revision and
durable response, then records that original result without executing it again
or charging another action/model call. Missing or conflicting results are refused.
It preserves the world, history and existing budgets and
reopens paused. The same `/api/adventure/reopen` request accepts
`until_midnight: true` to simulate normally until the next midnight and pause
there, including when everyone sleeps before then. This does not fast-forward,
increase budgets or automatically resume; existing budget limits still apply
unless **Run until complete** is enabled. A saved village stopped at the
18:00 iron-trader departure by a missing trader activity record can also be
reopened after the corrected world server is running. Recovery validates the
saved trader state and advances an isolated copy past the failure before
accepting a confirmed action boundary; it never invents or replays an NPC action.

The current clock and Gantt endpoint follow the newest recorded village time,
including a midnight observation newer than the inspection file. The scheduler
saves the world when stopping at its time boundary, even when no actor wakes.
The chart shows only elapsed rest, not the remaining scheduled overnight sleep.

The live technical log initially shows the newest 100 events; **Show 100 older
events** reveals earlier entries. Original technical JSON loads only when opened.
Large model request messages stay out of the live feed but remain available on
demand and in the full JSON export. Reconnecting does not resend history already
loaded by the page. Unchanged world files are cached for inspection, and unchanged
snapshots are not repeatedly sent to the browser.

Choose **Millhaven Expanded - Twelve Lives**, then **New adventure**. This is a
separate scenario; the original visiting-helper adventure is unchanged. Torvin,
Bren, Mara, Elsie, Hobb, Jory, Ada, Nella, Rowan, Garrick, Iona and Finn are all real
cast members, with separate AI turns, bodies, inventories, private observations,
plans and memories. **Ask an NPC** addresses any of them, including after the
current adventure ends.

New expanded adventures begin at **05:00**, one hour before the first wake-up.
Ten villagers begin asleep at the cottages; Elsie lives and sleeps at the inn
(`tavern`), and Ada lives and sleeps at the farm. Bren, Jory and Ada wake at 06:00;
the others follow their own times. Sleeping actors skip model turns. On waking,
villagers living away from work walk there before optional errands. Everyone
returns to their own home to sleep.

The **Village-wide tracking** dashboard in both views shows all twelve residents'
actual locations, carried tools, shipment cargo, coins, activities, scheduled
commitments, roles, skills, plans, latest decisions, private beliefs and sources,
relationships and witnessed memories. It also tracks construction contributions,
mill health, bread deliveries, every resident's useful-work contribution, local
stocks and the event ledger. This is operator knowledge, not shared NPC input.

- `village_work` executes the actor's own job at its workplace, with capability,
  equipment, input-stock, personal waking-time and cooldown checks.
- Torvin makes real wagon parts and uses `village_fit_wagon` to fit them in a
  committed 30-minute repair. Nella collects the working six-unit wagon and
  accepts `village_start_route`: farm -> mill -> bakery -> tavern -> square ->
  farm. The standing order performs real movement, batch loading/unloading and
  buyer/seller payments; it never creates stock or money. Meals, rest, blocked
  paths or unaffordable deliveries park the wagon with its cargo intact.
  `village_drive_wagon` takes manual control, so subsequent `go` actions move the
  physical wagon too. `village_load_wagon` supports mixed cargo and `village_unload_wagon` settles all
  cargo for that destination in one action. Bread need not wait for mill repairs.
- Garrick uses `village_patrol` at his scheduled post.
- `village_project` requires both named partners physically at the site, actual
  usable tools/materials, six five-minute contributions and at least two from
  each partner. Materials are consumed only on verified completion.
- `village_observe`, `village_ask` and `village_tell` maintain personal knowledge;
  unverified testimony cannot create supplies or complete projects.
- Milling, baking and cooking process up to two inputs per action. Each bread
  makes two meals; Elsie can also use `village_cook_fish` with the same yield.
  Opening stock includes six grain at the mill, six flour and four bread at the
  bakery, and twelve bread at the tavern.
- `village_eat` consumes **one daily meal at the tavern**. Once work is
  finished, a diner already at the inn may eat before the 19:00 gathering if
  real food is available; their portion still counts toward that day's supper.
  Elsie serves the others first and eats at 19:30; the meal plan reserves one
  real portion for her when stock permits, without creating food or waiving payment.
  The inn serves meals only while Elsie is awake and physically at the tavern;
  diners can retry when she returns. A prepared meal, bread, or fish with tea
  counts; tea alone does not. There is
  no automatic home pantry. An empty-tavern attempt records hunger once without
  forbidding work. Meals still pending at midnight count as missed. Missing a
  meal doubles the following day's work cooldown; eating before midnight cancels
  that day's missed-meal penalty. A later meal never retroactively erases it.
- `village_sleep` requires physically returning home. After completing supper,
  a resident can choose to sleep before the scheduled bedtime; they still wake
  at their normal morning time. Each NPC keeps the existing
  bedtime and first work-start time as their sleep/wake schedule. In the living
  village, Elsie sleeps 23:00-05:30 to prepare the inn; Bren/Jory/Ada wake at
  06:00, and residents who previously woke at 08:00 now wake at 07:00.
  Sleeping early keeps the normal morning wake time; sleeping after bedtime
  still requires the full scheduled rest duration. Waiting through the night
  without sleeping does not satisfy rest.
  The living scenario retains 12 opening tavern bread units for the planned
  breakfast and supper demand (7 + 5, including each meal's stock buffer).
  Its established opening storerooms hold 12 tavern, 10 bakery, 10 forge and
  24 shared cottage firewood units (24-unit cottage capacity); bakery flour,
  mill grain, workshop timber and farm hay also begin stocked. These are
  scenario-configured one-time starting goods, not daily replenishment: the
  persistent stock ledger, production, delivery and payment rules apply thereafter.
  Elsie can inspect a tavern-local meal-readiness forecast showing prepared and
  cookable portions, missing ingredients, fuel and projected unserved diners;
  this forecast does not cook, import or allocate meals. Firewood replenishment
  uses site-specific reorder points and targets, never automatic transfers.
  Existing saved worlds are never silently seeded when loading this scenario;
  use an explicit new-world reset for its opening inventory.
- Due meals and rest block new work, patrols, deliveries and further construction
  contributions. Travel home and interruptible projects can pause at any action
  boundary without losing their recorded progress, cargo or unconsumed supplies.
  Sleeping villagers cannot work, receive tools, answer local questions or learn
  from nearby activity; private operator questions remain separate.
- **Baking is non-interruptible.** Starting a batch reserves real flour, then
  commits Jory to 30 village minutes at the oven, even across dinner or bedtime.
  He cannot leave, eat, sleep or start another job until it finishes. The shared
  clock completes only this already-started batch, producing bread and recording
  work exactly once; a broken oven explicitly ruins it instead. Meals and sleep
  take priority again immediately afterward.

Schedules are guidance, not automatic travel or work. There are no duplicate
rules-driven resident entities moving behind the AI cast. The controller selects
the **earliest available villager**, advancing the shared clock only when nobody
is ready. Work/travel reserve that actor's time rather than charging everyone.
`village_wait` reserves 5-60 minutes for that person, capped at 15 while other
work or routes are active (or less until a committed batch finishes). Sleeping,
committed work and accepted wagon/timber routes skip model turns. Looking and
reading do not advance the clock or end that villager's turn. The clock processes
the timed mill failure, sleep/wake and meal times, and
already-committed baking and explicitly accepted wagon/timber standing routes; it
never starts unrelated jobs or moves other villagers.
AI decisions use model calls and do not
guarantee cooperation or success. Play ends only after shovel recovery, the well,
the mill repair, a working wagon, a bread delivery (before or after repair) and
useful work by all twelve, with no committed batch abandoned.
World and per-actor run state persist through the existing JSON save mechanism;
restarting the server interrupts an unfinished run rather than silently continuing it.
Daily routines apply to **new adventures**; existing episodes retain their frozen
scenario and do not acquire food, needs or revised job rules retroactively.

The square's noticeboard has a free, readable map of every connection. Important
tool discoveries, agreed project rendezvous (`village_meeting`) and the 14:00
mill failure are posted there and announced by the bell. Awake villagers hear
these deliberately public reports; sleepers do not gain private knowledge.
Ordinary speech never replays raw action or belief audit strings. New expanded
events record game minute and member name; live logs, stories and copied final
narratives display those timestamps without changing internal evidence IDs.
Timed adventures also show a **current game-time clock** in the header in both
views. Village availability, sleep and work messages use 24-hour clock times
(for example, `08:05`), adding the day for overnight times. Bakery and BBQ
adventures show simulated elapsed time; untimed adventures hide the clock.
Older village story text is formatted for display without changing saved
technical records.
The remaining P2/P3 rules also apply to new expanded adventures:

- Bren feeds the actual horse at the stable, leads it to the wagon and hitches
  it for morning deliveries; at night he unhitches, returns and releases it
  before sleeping. The wagon uses roads only, never the five marked footpaths.
  Ada and Jory may drive a parked wagon when Nella is away or busy.
- Foot consignments hold at most three units, counting held tools and body
  limits. Rowan can accept an automatic timber route from woodlot to workshop.
  Helpers can transport project supplies while leads collect their tools.
  Buyers must afford all delivered goods plus a **one-coin fee per unloading
  action**, paid to the actual carrier; rejected deliveries change nothing.
- Every daily meal costs **one coin to Elsie**. Insufficient funds or food
  records hunger, while payment and eating later that evening can recover it.
  Awake villagers at **02:00** incur next-day tiredness: 2x cooldown, stacking
  with hunger to 4x. Actual tea cancels today's tiredness factor only.
  Well/mill crew members must explicitly choose `village_urgent_work` to
  override bedtime for that project until 02:00, not ordinary production.
- Production starts after actual waking through **19:30 inclusive** at other
  workplaces, including inputs arriving in that grace period. The inn remains
  open for supper and tavern production until **22:00** (last action starts
  before closing); late meals require real food and payment before closing.
  Elsie remains at her inn home for her 23:00 bedtime. Already-started baking still completes.
  Ada grows two hay, Iona gathers two herbs and Rowan fells two logs per action.
  A trader brings four iron to the square at noon, at three coins each, and
  leaves at 18:00 with unsold stock. Payment occurs on actual forge delivery.
- Mill repair also consumes one actual plank delivered to the mill. One manure
  fertilization halves the next three grain-harvest cooldowns.
- Stocks are not capped. FIFO lots retain their original expiry through storage,
  loading, transport, unloading and saves: **meals/fish 12h, bread 24h, and
  grain/flour/hay/herbs/tea 72h**. Other materials do not spoil.
  Additional opening stock is farm grain 6, mill flour 2, forge iron 4,
  stable hay 6, woodlot logs 10, workshop timber 4 and cottage herbs 10.
- Walked paths are remembered and shareable; route suggestions use shortest
  known topology without exposing remote changing conditions. The noticeboard
  shows stock reports, shared project progress `/6` and completion announcements.
  Bread is due by day-one 19:00; delivery then is on time, an overdue warning
  permits later recovery. Hourly workplace stock/coin snapshots are persisted,
  and free look/read entries are labeled separately from subsequent actions.

Frozen earlier adventures retain their original rules and records.

### Initial setup

For initial setup, open **Admin view**:

1. Choose a **Scenario**, select a profile for each slot under **Choose your agents**, then click **New episode** to create its world.
   The default single-agent role is displayed as **Player**; its internal `npc-001` ID is unchanged.
2. Start your LLM proxy at **127.0.0.1:9901**.
3. Click **Discover**, select a model, and **Save settings**.
4. Click **Step** for one decision/action cycle or **Run agent** for continuous execution.
5. Inspect world state, the NPC's separate observed view, concise decision explanations, requests, results, and goal verification.

The **NPC's story** panel retells observations, attempted actions and confirmed outcomes chronologically. Single-agent stories use first person; multi-agent stories name the acting agent and use "we" for the team's confirmed success. Decision explanations are also instructed to name the actor, reserving "we" for statements that apply to everyone. Expand **Why this action?** for the decision explanation. The technical log remains below it, and archived episodes and copied stories use the same narration. Stories are reconstructed from persisted JSON events without extra model calls or hidden-state spoilers. Archived team prose is displayed with named actors; the saved original grading evidence is not modified.

Adventure prose, decision explanations, archived stories and copied stories use
the member names saved with that episode (for example, Avery), not internal actor
IDs such as `npc-001`. Technical logs and original grading evidence retain their IDs.

The third column presents **An unexpected adventure** as continuous story prose: discovered descriptions, carrying limits, recorded reasons, attempted actions and confirmed results. Archived episodes include the same rewritten narrative. It uses no additional LLM calls and does not invent private thoughts or turn expected outcomes into facts. Narrow screens stack this reading column below the other panels.

**Copy story** works while an adventure is paused, running, or completed. Village
adventures offer **Story by day**, defaulting to **All days**, in both live and
archived stories. Choose a recorded day to read and copy only that day's narrative.
Live copies fetch the complete recorded story, not just the visible technical-log
page. Other adventures keep their continuous story; clipboard errors are reported.

Cooperative actions use their observed plain-language descriptions in the story,
not command IDs or JSON arguments. Object IDs are shown as discovered names and
capability codes as readable words. This also applies to archived story displays
and copied prose; the original event log and saved grading evidence remain unchanged.
Per-object inventory-unit costs are omitted from narrative stories, including
archived displays and copied prose. They remain available in agent observations
and technical inventory details; carrying limits and actual capacity failures
are unchanged.
Successful actions are narrated once from their confirmed result, rather than
repeating an attempt followed by the same action. Pickups read "I picked up..."
(or name the acting teammate). Recorded reasons and discoveries are retained;
failed, unchanged and pending actions remain attempts. Archived display and
copied prose apply the same cleanup without modifying saved grading evidence.

Authored opening scenes retain their paragraph breaks and stand alone instead of
having an inventory dump appended. Room descriptions retain their sentence structure;
they are not blindly prefixed with "I am in". Raven & Badger opens with a shared
courtyard scene explaining their different abilities and a possible division of labor,
without claiming that either animal has already acted or found the missing pin.
Its regular observations describe the courtyard, balcony and tools in plain language,
with physical names such as "Gap to the balcony" rather than action labels such as
"High balcony flight". The observation panel shows object names, with technical IDs
available in tooltips and JSON. Scenario prose changes apply to new episodes; existing
attempts retain their frozen scenario and original observations.

The **location network** numbers rooms in discovery order and links them with actual
door connections. Open routes are solid teal; closed/locked and broken routes are
dashed. A `?` marks an observed exit into an unexplored area, without revealing the
hidden room. Click or keyboard-select a node for its description, and expand **Route
details** to match `D1`, `D2`, etc. to door names and states. The current agent and
nearby observed teammates appear on their room. The map scrolls for larger worlds
and follows the selected actor's discoveries rather than sharing hidden knowledge.

On wide screens, **LIVE TEAM** sits to the right of **01 / DISCOVERED**;
the **Technical Log** sits directly beneath Discovered in the same column,
followed by **02 / EMBODIMENT**, then **Connection / LLM proxy**.
The team dashboard uses the full available width, with the adventure story
in a third column to the right of LIVE TEAM. The Completion Review sits directly above
LIVE TEAM in the middle column, including on narrower screens. On wide screens,
the live agent cards fill the space below the review, and all three
columns share an aligned bottom edge. Narrower screens
move the story below the overview and stack the map above the team.
The story uses the dashboard's standard panel colors, sans-serif typography and
compact paragraph spacing, including archived story displays.
For single- and multi-agent episodes, **All agent perspectives** shows every agent in a
separate, live card: current location, inventory/carrying limit, task focus,
turn status, role/constraints, observations, local cooperative work and an
independently scrolling action feed. The active agent is highlighted; turns
remain sequential. The actor selector controls the detailed constraints inspector,
not which agents or story are visible. Cards share the middle column width evenly
for any number of agents (including one or two), and stack on small screens.

The **consolidated team map** shows every agent together, including agents in
different rooms and companions who meet in the same room. It combines only the
team's discovered rooms and routes, keeps unknown destinations anonymous, and
labels ability-restricted routes. This is an admin overview, not shared agent
knowledge: private observations and memories still remain separate.

**An unexpected adventure** follows all agents in chronological order, names the
acting character, and includes a shared opening only once. Live prose, archived
adventures, copied final stories and new completion reviews cover the entire team.
Existing saved grading evidence is not changed by the combined archive display.

No puzzle solution is embedded in the controller or model instructions. Scripted decisions exist only in automated tests. An unreachable or timed-out LLM proxy, HTTP 408/429, or HTTP 5xx pauses the adventure with an explicit message, preserving its world and memories. The simulation clock and episode timer stop; use **Resume** when the LLM is available. There is no automatic retry, scripted decision, or direct-provider fallback. Invalid model output and non-retryable HTTP errors remain terminal errors. Headless batch evaluations record paused episodes as ungraded and close their worlds without making a completion-review call.

### Anatomy-aware handling

Bodies declare `grips` (`hand`, `paw`, `claw`, `mouth`, `beak`) and an optional
`max_held_items`. Objects separately declare `carry_grips` and `use_grips`.
At least one actual body grip must match the object's accepted grips. Generic
`manipulate`, strength, tools and spare inventory space never grant hands.
Held-object limits and inventory-unit capacity are enforced independently.

For example, Badger has `"grips": ["paw", "mouth"]` and `"max_held_items": 2`.
An ordinary bowl defaults to `"carry_grips": ["hand"]` and
`"use_grips": ["hand"]`: Badger cannot pick it up, receive it from a teammate,
or operate it through a recipe or bakery action while it sits on a bench.
An explicitly adapted prop can instead allow `"carry_grips": ["hand", "mouth"]`.
Operating it also needs an appropriate `use_grips` setting.

Raven, Badger, Otter and Fox have explicit anatomy in their own profiles and
native scenario bodies. The Fallen Observatory, Storm Signal and River Crossing
use explicitly animal-adapted grips and controls, preserving their intended
solutions without granting missing skills, extra carrying capacity or new routes.
Other ordinary tools and cookware remain hand-only unless authored otherwise.
This is an affordance model, not a complete limb-by-limb physics simulation.

Old configurations default to hand grips and no additional held-object count
limit, preserving humanoid behavior. Cast previews and live body details show the
effective grips. Changes apply to **new adventures**; existing episodes retain
their frozen configuration.

The **Locked Room** also has a small open window high in the wall and a small
flooded drain, both leading outside. The window requires flight; the drain
requires swimming. Both admit only a `small` body. Raven and Sprite are small
flyers; Otter is a small swimmer. Larger NPCs still need the existing door-and-key
route, even if they gain flight or swimming.

Body `size` is explicitly configured as `small`, `medium` or `large` (default
`medium` for old configurations). Routes may specify `max_body_size`; omitted
limits preserve existing routes. Size is independent of skills, grips and carrying
capacity and is enforced for movement in either direction. Observations, cast
previews and map route details explain these restrictions. Start a new adventure
to use the updated Locked Room rather than its previously frozen version.

### Individual agent configurations

**Archmage** is a separate selectable character; the original Mage is unchanged.
Archmage has arcane, fire and freezing magic, plus verbal `cast_unlock`,
`cast_shrink` and `cast_restore_size` actions. Elemental and arcane challenges
still require their authored tools and prior work. Unlock works on ordinary
local locks without keys, but neither opens doors nor bypasses protected puzzle
mechanisms, jams or broken locks. Shrink scales only the caster and carried
equipment to small size; Restore Size uses the adventure's frozen original size.
Neither changes abilities, grips or carrying limits. Archmage cannot fly, swim
or teleport, and fire magic does not grant heat protection. Like every NPC,
Archmage has its own live nine-document Markdown bundle and isolated memories.

Every character has a persistent JSON configuration under `data/agents` and its
own **live Markdown template folder**, including Raven, Otter and Badger:

```text
data/agents/
  raven.json
  raven/
    IDENTITY.md
    SOUL.md
    AGENTS.md
    USER.md
    MEMORY.md
    ALIGNMENT_SYNTHESIS.md
    PROACTIVE_PREFERENCES.md
    README.md
    people/
      INDEX.md
```

The same layout is provisioned for every NPC, including user-created profiles.
Missing standard files are created without overwriting existing ones.
The folder structure follows the supplied multi-document template, but all
content is authored for the NPCs: no real user profile, personal memories,
preferences or third-party relationship records are imported.

**Markdown is live persona configuration.** Identity, temperament, operating
guidance, fictional operator expectations, authored background, alignment,
initiative and optional `people/*.md` relationship notes enter only that NPC's
decision context. `README.md` is documentation and never enters model context;
catalog IDs and other scenarios' body configurations remain excluded.
Background memory/relationship notes are not current-world evidence and never
share another NPC's private state. `MEMORY.md` is manually authored background,
not an automatically updated transcript; existing isolated episode memory and
scenario failure feedback keep their current behavior.

JSON remains authoritative for enforceable body abilities, carrying limits and
scenario-specific body variations. Markdown cannot grant flight, lockpicking,
access to private observations or any other world capability.
JSON identity/personality/rule fields remain legacy summaries. Editing those
summaries updates the corresponding Markdown only while it is still the untouched
default; customized files must be edited as Markdown to avoid losing authored work.

In **Admin view**, expand **Edit individual agent configurations**, select an
NPC and a **Markdown file**, then **Save Markdown document**. Direct UTF-8 file
edits also take effect when the catalog is reloaded or a new adventure starts.
Each adventure freezes the complete document bundle with its selected profiles,
so existing and archived adventures never change retroactively.
The existing profile PUT API accepts a `documents` map; partial maps update only
the supplied files, and omitted files remain intact. Empty text intentionally
clears a document; removing a key does not delete its file.

Missing starter profiles are generated from the scenario roster without
overwriting existing configurations. Each JSON profile defines
an identity, personality, operating rules, constraints, a physical body
(capabilities and carrying capacity), and optional `scenario_bodies` for that
character's existing task-specific ability variations. Raven is observant and
wry, Badger deliberate and dependable, and Otter playful and inventive.
The generated Markdown identity and soul preserve those individual personas,
with their operating guidance separated into the matching template files.

Use the JSON editor for machine settings; change its ID to save a new character
with its own Markdown folder. Task assignment previews show the
selected agent's effective abilities, personality and rules. Each slot needs
a different agent; starting locations and technical actor IDs stay with the
task slot, while the selected character brings its own body and persona.
The **Skills policy** selector offers:

- **Scenario-specific profile skills** (default): use the selected profile's
  `scenario_bodies` entry when present, otherwise its base body. Required skill
  coverage is checked before starting. This never borrows the original slot
  character's abilities.
- **NPC skills only (allow impossible tasks)**: use only the selected NPC's
  base `body`, ignoring all scenario overrides. Missing challenge skills do not
  prevent the episode from starting. The puzzle, prerequisites and goals remain
  unchanged, so the task can become intractable. For example, Raven in the thief
  slot of Thief & Mage cannot pick the gallery lock: possessing lockpicks or
  filling the thief slot does not grant lockpicking.

For cooperative shared-exit goals, all declared skill challenges are mandatory.
Once every assigned agent has joined and discovered the objective, the world
checks the team's actual abilities against unfinished mandatory challenges.
If nobody has a required skill, verification returns **blocked** with an
explicit admission of defeat, the missing skills, affected challenges and team
ability evidence. The controller ends the attempt before another decision or
action and includes the reason in the story and completion review. Fox replacing
the thief therefore cannot retry indefinitely without lockpicking/trap disarming.
Missing tools, unfinished prerequisites, absent teammates or already-completed
challenges do not by themselves trigger this missing-skill defeat condition.
This is a proof for mandatory skills, not a general puzzle-solvability solver.

Specialized ERP/BBQ body requirements are still checked in both modes.
Neither policy is a full solvability proof: route restrictions and inventory
limits can also prevent completion.
Body capabilities and capacity are world-enforced; personality and prose
operating constraints guide model behavior, not a separate authorization system.
Profiles cannot override the world or base decision rules.

Each episode freezes its selected profiles and scenario, preserving private
per-agent memories. Editing a profile affects only new episodes. **Reset** keeps
the previous profile assignments and skills policy but reloads their latest configurations;
**New episode** uses the selections currently shown.

The API exposes `GET /api/agents`, `PUT /api/agents/{id}`, and
`agent_slots` on `GET /api/scenarios`. Start/reset accept a complete
`agent_assignments` map, for example
`{"scenario_id":"storm-signal","agent_assignments":{"raven":"raven","badger":"badger","otter":"otter"}}`.
Omitting assignments keeps scenario defaults (or the previous assignments on reset).
The CLI also supports repeated `--agent SLOT=PROFILE` options.
Start/reset also accept `"skill_mode":"npc_only"` or `"skill_mode":"scenario"`.
Omitting the policy uses scenario defaults, or retains it when resetting the
same scenario. The CLI equivalent is `--skill-mode npc_only`.

### Dungeon Master: independent hybrid supervisor

The **Dungeon Master** is a separate agent, not a task slot or teammate. Its
editable configuration lives in `data/dungeon-master.json`, with identity,
personality, operating rules, constraints, capabilities and an enabled flag.
Rename it Oracle or Overseer if preferred. Use the supervisor panel or
`GET/PUT /api/dungeon-master`; changes apply to new episodes and are frozen in
each run alongside the NPC profiles.

The world adjudicates every dispatched action using actual abilities,
inventory, prerequisites and action schemas. The supervisor records allowed
and rejected actions with links to their world-result event IDs; malformed
proposals also receive rejection rulings. Neither an AI ruling nor a task-slot
name can grant a skill or override a world restriction.

After each round (one action per roster member, or each action for a solo NPC),
the Dungeon Master uses the selected proxy model to review the team's combined
observations and recent authoritative world events. It may rule **continue**
or **defeat**, with reasons citing supplied evidence IDs. It must not mistake
exploration, missing-but-findable tools or legitimate timed waits for futility.
In Living village cycles, breakfast is optional for completion; missed breakfasts
retain their ordinary hunger and next-day work effects. Supper, actual supplies,
and daily work still matter. Recoverable daily-life checks cannot justify an AI
defeat: the original review is retained, and an explicit world-authority continuation
ruling records why it was deferred. Authoritative world failures still stop the run.
An older AI-stopped Living cycle can be reopened only after the saved world confirms
recoverable daily requirements; no meal history, inventory, stock, or clock is reset.
An AI defeat ruling is an explicitly labeled judgment, not a proof of
unsolvability. Missing mandatory team skills are first checked deterministically
by the world and stop the attempt without a supervisor model call.

The supervisor cannot change the world, approve an illegal action or declare
success. Its privileged review context and rulings are not copied into NPC
memories. Unknown evidence references or unauthorized rulings receive one repair
attempt; persistent invalid output ends with an explicit error. An unavailable
proxy pauses the adventure instead; resuming retries the pending supervisor
review before another NPC acts, without repeating an already committed action.
Requests use compact short-term context, not repeated full observation histories:
the supervisor receives every teammate's latest observed state and summaries of
the latest 40 evidence events, with original citation IDs. Repeated action schemas,
static descriptions, old route descriptions and personal beliefs are omitted from
supervisor requests; world policies and recent public notices are shared once.
Long result explanations are explicitly marked when shortened. Missing history
must never be treated as proof of futility.

NPC decisions and private questions receive the last six action summaries,
conversation exchanges, incoming messages and village news items, plus the latest
three previous-attempt failure reports. Current goals, action schemas, body and
inventory constraints, observed discoveries, known entities, maps and active plans
remain available. Character profiles are supplied once, in the system instructions.
These are request-only projections: full events, memories, transcripts, saved
adventures and the World timeline are unchanged. No extra summarization model
calls are made.

AI reviews add separately displayed model calls (at most one per round plus
validation repairs), share the episode wall-time budget, and do not consume
the NPC decision-call allowance. Decision and action budgets still bound the
number of possible reviews. The live panel, archive and transcript preserve
the rulings and distinguish world enforcement from AI judgment.

Evidence citations in Dungeon Master rulings, completion findings, archived
episodes and technical-log entries include readable explanations from the
referenced world event or the exact reviewed story paragraph. World checks
explain recorded missing skills, team capabilities, required work and completion
conditions when those facts are available. IDs remain under **Technical
reference** for tracing, rather than serving as the explanation. Missing records
are explicitly labeled unavailable. This display needs no extra model calls and
does not rewrite saved evidence, reviewed prose or original technical JSON.

### BBQ pitmaster: steady at 225 F

Choose **BBQ Pitmaster - Steady at 225 F**, create a new episode, and run the
agent. Its only job is watching the Fahrenheit gauge and adjusting the heat dial
to maintain **225 F** through occasional simulated grease flare-ups. There are no
ingredients, movement, inventory or needs. The dashboard shows the gauge, heat
setting, temperature history, flare-up count and time in the target band.

Each adjustment or monitoring action advances **30 simulated seconds**, including
gradual heating/cooling and intermittent heat spikes. Observation, model thinking
and pauses do not advance time. The session ends after **30 simulated minutes**
(60 actions), with no new flare-ups in the final two minutes so the NPC can let
the temperature settle. Success requires at least **80%** of simulated seconds within
**215-235 F**, plus a final temperature inside that band. Persistent state includes
the heat setting, readings, scoring and randomized flare-up sequence, so reloads
and duplicate requests do not reroll disturbances. This is not a real-grill controller.

### ERP terminal: blocked supplier invoice

Choose **ERP Terminal - The Blocked Supplier Invoice**, create a new episode,
and run the agent. The NPC is an ERP operations analyst working entirely through
a simulated business-system terminal, with no physical movement, inventory or needs.
It must investigate a blocked supplier invoice, compare the purchase order and
receiving evidence, reconcile a supported receipt, complete three-way matching
and release the invoice hold. Similar-looking receiving records require checking
document references and approval status.

The `erp_terminal` action accepts one command at a time; `help` documents the
available commands. The dashboard shows the latest terminal command and output.
Receipts, match/release status and the command audit trail persist with the world.
Duplicate, unapproved, unrelated and excessive receipts are rejected. Invoice
release requires a successful quantity, supplier, item, price and currency match;
completion is verified from the business state, not the NPC's claim.
This is a local sandbox, not an operating-system shell or live ERP connection.
It cannot edit source documents, override matching controls or make payments.

### Two-agent rock-paper-scissors

Choose **Rock, Paper, Scissors - Two-Agent Showdown**, create a new episode,
and click **Run agent**. Nova and Atlas independently choose moves through the
configured local LLM proxy, with separate memories and alternating turns.
Each `play_rps` move is locked and hidden from the opponent until both players
commit. Revealed moves, round results and the running score appear in observations
and the adventure story. The admin world snapshot can inspect pending moves;
agent observations cannot inspect an opponent's pending move.
Five rounds are played, including draws; most round wins takes the match, with
an overall tie allowed. Episode success means all five rounds were completed,
not that both competitors won. Match state and results persist with the episode.

### Two-agent tic-tac-toe

Choose **Tic-Tac-Toe - Two-Agent Showdown**, create a new episode, and click
**Run agents**. Nova (X) and Atlas (O) independently choose moves through the
configured local LLM proxy. Nova starts; the world enforces alternating turns,
empty cells, and the end of the game. Cells 1-9 run left to right, top to bottom.
The public board, marks, turn, move history, and win/draw result appear in
observations; moves and results also appear in the adventure story.
Three marks in a row, column, or diagonal wins. A full board without a winner
is a draw. Either result completes the episode goal; it does not mean both
players won. Match history persists with the episode.

### Completion review and failure learning

**An attempt finishing is not necessarily success.** Every ended attempt, including cancellation, stalls, budget exhaustion and execution errors, gets a separate **pass/fail** completion review.

- After the loop ends, observe the final world again and rerun its authoritative goal check. Review the resulting first-person story through the **same local LLM proxy**, with concise reasons citing supplied story/final-check evidence.
- A failed world check always vetoes a story's success claim. A sandwich made but not served on the designated intact plate **fails**. The review cannot invent extra requirements or treat intentions as completed actions.
- The dashboard's **Completion review** panel and episode archive show the grade and reasons. Completed prose uses the exact story snapshot submitted for review. **Reassess story** or `python -m npc_sim.cli assess` repeats the final check/review without replaying actions.
- `run.status` retains the execution outcome. `run.successful` is `true` only for a passing assessment, `false` for failure, and `null` when not yet graded or review failed. `completion_analysis` persists the reviewed story, fresh verification, reasons, provenance and review model/call/time metadata.
- Review/network/malformed-evidence errors are explicitly **NOT GRADED**, never success or fabricated scenario lessons; resolve the issue and reassess.
- Failure reasons are persisted in `data\scenario-memory\<scenario-id>.json`. Future attempts load the latest 12 failure reports for **that scenario**, labelled as previous experience rather than current-world discoveries. Known tasks load these at the start; undiscovered tasks defer them until the assignment is read, so previous failures cannot spoil the goal. Current observed facts/action schemas still take precedence; no objects are pre-added to discovery memory.
- Reassessment replaces that attempt's feedback rather than duplicating it. A corrected passing assessment removes its superseded failure report. New episodes freeze their scenario definition for later verification; legacy episodes without a frozen copy explicitly warn when checked against the current definition.
- Story generation remains deterministic, without narrator calls. Completion review adds one bounded proxy call per assessment, separate from the decision/action budget; task timing freezes when the attempt ends. Batch success statistics require a passing grade, verified completion and the configured action/time thresholds. Batch episodes retain isolated memory, unlike successive attempts in the shared dashboard.

### Proxy contract

The confirmed proxy API is OpenAI-compatible:

- Base URL: `http://127.0.0.1:9901/v1`
- `GET /models`: `{"data": [{"id": "your-model"}]}`
- `POST /chat/completions`: standard non-streaming `model` and `messages`.
- Response: `choices[0].message.content` containing the requested JSON decision.
- No authentication by default. Optional authentication uses the environment-variable name in `proxy.token_environment`; do not store a token in configuration.

The model is selected explicitly if discovery returns multiple models. The first version requests text JSON rather than assuming optional tool-calling/structured-output features. Invalid decisions get one repair attempt and are never dispatched unchecked.

## Controls and needs

### The Two-Agent Escape

Choose **The Two-Agent Escape**, then **New episode**. The **scout** and **locksmith**
are two autonomous agents sharing the same workroom, doors, keys and courtyard.
Each has its own model decisions, memory, observations, inventory and carrying limit.
They take alternating turns in one owning world process; **Step** performs one agent's
turn and **Run agents** continues the team. Agents already at the courtyard stop taking
turns while their partner catches up. The goal succeeds only when **both** arrive.

The agents can **say** messages to teammates in the same room and **give** carried
items to a nearby teammate with enough capacity. Messages are persisted, with the
latest 20 heard messages in each agent's observations. In timed AI villages, speech
addressed by name (or with the optional `recipient` argument) opens a short, optional
conversation with the nearby listener. The listener sees a reply opportunity on their
next available turn; neither hearing nor replying accepts a supply order. A thread
ends after four spoken turns or 20 game minutes, or when either participant leaves
or sleeps. The pair cannot reopen it for 30 game minutes after the turn limit.
Remote observations and private plans are not shared. Roles suggest responsibilities
but do not script actions.
The actor selector switches the observed view, discoveries, constraints and story;
the technical log labels every event's actor. Completion review checks the team goal
against both agents' stories. Action, model-call and time budgets apply to the team.
Existing scenarios remain single-agent. Multi-agent orchestration currently requires
the bundled stdio adapter; it never starts competing writer processes.

### Five asymmetric cooperative adventures

The scenario menu also offers five adventures with **mechanically enforced** differences:

| Scenario | First partner | Second partner | Required cooperation |
|---|---|---|---|
| **Otter & Fox - The River Crossing** | Otter swims; carries one small item | Fox rigs rope and repairs machines; cannot swim | Prepare and hand over a rope, anchor it across the river, and repair a ferry for the non-swimmer |
| **Thief & Mage - The Sealed Vault** | Thief picks locks and disarms physical traps | Mage dispels wards and activates runes | Alternate mechanical and magical work to open the route |
| **Raven & Badger - The Fallen Observatory** | Raven flies and retrieves light parts | Badger rigs a lift, shifts rubble and digs; cannot fly | Retrieve and hand over a high-up pin, install a heavy beam, then clear and dig an escape |
| **Merfolk & Engineer - The Flooded Lighthouse** | Merfolk swims and works underwater | Engineer repairs electrical controls; cannot swim | Open submerged valves, release a lens, pump out the water and restore the beacon |
| **Fire Sprite & Ice Golem - The Divided Temple** | Sprite flies through heat and uses fire magic | Golem freezes water and lifts heavy objects; cannot cross raw flames | Share a frost seal, cool a stair, secure a sluice, freeze a bridge and light the final beacon |

Select one, then **New episode**. **Self & constraints** shows the selected character's
actual body, abilities and carrying limit. Nearby teammates expose their public abilities;
**Local cooperative work** explains each discovered mechanism's required skill, missing
tools/prerequisites and who completed it. Map route details distinguish skill-gated paths.
No ability transfers with an item or message: handing the mage lockpicks does not grant
lockpicking. Non-swimmers cannot use swimming routes, and non-flyers cannot use flight routes.

All required skill challenges **and both characters reaching the exit** are verified.
Neither character has every required skill. Work, installed items and its performing actor
persist in world JSON; installed parts cannot be taken back to invalidate a completed repair.
Ordinary opening, movement, repeated requests and role claims cannot bypass skill gates.
Existing scenarios and saved bodies retain their original behavior. New definitions allow
an optional `body` per agent, `skill_challenges`, and door `traversal_capabilities` and
`required_challenges`. These rules are world mechanics, not scripted model decisions.

### Raven, Badger & Otter - The Storm Signal

Choose **Raven, Badger & Otter - The Storm Signal**, then **New episode**.
The three agents start **in different locations**: Raven in the cliff rookery,
Badger in the stone quarry, and Otter at the river landing. Their paths converge
at the central signal yard; observations, inventories and memories remain separate,
and speech and item handoffs require meeting in the same room.

Raven flies to release an optical cache and can focus the beacon. Badger carries
the heavy mast counterweight, braces the mast and mounts the lens. Otter swims
through a submerged service channel, releases a cable chest and repairs wet wiring.
Restoring the beacon requires all three skill sets and sharing recovered parts.
The refuge gate cannot be bypassed, and success requires **all three agents**
inside the refuge after every required repair is complete.

Team scenarios support two or three agents with optional individual `start_location`
and `body` settings. Turns rotate across the team in one world process, skipping
agents already at the shared exit without sharing private discoveries.

### The Clockwork Bakery

Choose **The Clockwork Bakery**, then **New episode**. Explore the bakery and ingredient
storeroom, open the bowl/ingredient cupboards, and read the recipe board. Three separate
bowls share one mixer and two electric ovens. Ingredients are finite: each bowl needs
one measured portion each of flour, water, yeast and salt. Bowls can be carried between
rooms; measuring, equipment operation and serving require physically reachable objects.

The **Bakery clock & batches** panel shows simulated elapsed time, real running time,
the adjustable **1-600x** multiplier, and discovered batches' stages/countdowns.
The default **120x** makes a two-hour rise take **60 real clock-running seconds**.
New bakery episodes default to **Pause during decisions** (`decision_paused`): model
calls, repairs, discovery and controller processing never consume bakery time. The
clock runs in real time during explicit waits. Successful physical actions advance it
by fixed simulated durations: measuring and mixer setup take 60 seconds, shaping
takes 180 seconds, and other physical actions take 30 seconds. Inspection, reading,
rejected/no-change actions and idempotent retries cost no simulated time. Advertised
actions include `simulation_seconds`, and results record the applied duration.
Other batches progress during an action; newly started process timers begin after
setup completes. A handling deadline is checked when its action starts.

Select **Continuous clock (challenge)** to retain the original behavior: the clock runs
during decisions and actions have no extra fixed-time charge. Saved older episodes
without a clock mode retain continuous behavior; new episodes use the new default.
**Pause**, ready episodes and ended attempts freeze both modes. **Step** executes one
action and its fixed time cost; a requested wait only runs once the episode is running.
Resume preserves elapsed time, and reset starts fresh. Paused time does not consume the
episode's wall-time budget. Model/network latency still counts toward the separate
real episode budget, but cannot burn or overproof bread in decision-paused mode.
Process downtime is not replayed into a saved bakery.

Each batch mixes for 10 simulated minutes, rises for 2 hours, proofs for 45 minutes,
bakes for 30 minutes and cools for 20 minutes. Shape within 30 minutes after rising,
load an oven within 20 minutes after proofing, and unload within 10 minutes after baking.
Missed deadlines ruin the required batch and fail the order. The mixer remains occupied
until the rise starts; an oven remains occupied until unloading. Switching off or
disconnecting occupied equipment ruins its batch. Interleave useful tasks during other
batches' timers. The advertised `wait` action waits in real time (scaled by the multiplier),
waking at the next readiness/deadline without wasting model calls or triggering repetition
stalls. In continuous mode, higher speeds also shorten the real decision windows, so
lower the multiplier if your model needs more time.

The bakery uses a per-episode budget of 100 actions, 120 decision calls and 600 active
real seconds, without changing other scenarios' configured budgets. Success requires
all three batches cooled and served on the intact display, not just a timer completing.
Clock state, ingredient stock, batches and deadlines persist in world JSON; clock-rate
changes are recorded in the event log. `PUT /api/clock` with
`{"rate": 120, "mode": "decision_paused"}` changes the active bakery's clock settings.
Omitting `mode` preserves the current policy.

- **Step** executes at most one decision and one world action.
- **Pause** stops new cycles after the current cycle completes. Resume continues the same episode.
- **Reset/New episode** creates a fresh world and retains old JSON history.
- Food and sleep are independently enabled through the **Self & constraints** switches. Levels are retained when disabled.
- Needs progress only on successful state-changing non-inspection actions by that actor. Read-only actions, failures, and duplicate requests do not advance needs.
- At level 80, an enabled need blocks movement. Sleeping can be recovered with the advertised rest action. Food recovery requires a carried edible item; the initial puzzle contains no food, so enabling hunger can eventually make a prolonged run blocked. The world never silently disables needs.
- The initial body has **1 inventory unit** of capacity. The key must be carried to unlock the door.

### Inventory units and local pickup

- Each item has a positive integer `inventory_units` cost, an abstract measure of its weight/bulk. Puzzle items currently cost **1 unit** each.
- `body.capacity` limits the sum of carried items' unit costs, not their count. An item costing 2 units cannot be picked up by the current 1-unit body, even with an empty inventory.
- Observations expose each visible item's cost and `self.body.carrying` reports the `inventory_units` rule, limit and used total. The dashboard and narrative use the same units.
- Only visible, reachable items physically in the actor's current room can be picked up, including exposed contents of containers there. Remembering an item in another room does not make it a pickup target; return to that room first.
- Carried containers use their declared unit cost; contents are not charged separately until taken out. This preserves the existing box behavior. Dropping an item releases its units.
- Older JSON without `inventory_units` defaults to 1 per item; archived `item_count` observations remain readable.

The dashboard shows recorded explanations and explicit plans, not private model chain-of-thought. Its privileged hidden-state view never enters model requests.

## Objects, damage and breakage

- Every object has `takable`, `damage`, `health`, and `max_health` settings in scenario JSON. Locations are not damageable.
- The door and its separate lock are fixed; the box, key and rock can be picked up.
- Locks are attached to doors and hidden until the NPC explicitly looks at that door. A failed opening attempt reports a locked door but does not reveal its attached lock.
- `hit` requires a visible target and an intact carried item. Its damage score reduces the target's health, clamped at zero.
- An impact also applies the target's damage score to the carried item's health. Both damage amounts are determined before either object breaks; either or both can break, rename, reveal contents or spawn debris in the same action. Retries do not repeat either side's damage. Indestructible items retain their health, and a zero-damage tool can still receive damage.
- The rock deals **25** damage. The box has **50** health: two hits break it, reveal the key, and create wooden splinters. Opening the box also reveals the key, without damage or debris.
- The lock has **75** health: three rock hits break it permanently. Earlier hits warn about damage but never open the door. A broken lock on a closed door prevents opening/unlocking it, even with the key.
- Breaking the door itself blocks the exit; it is not an alternative escape route. A lock broken after its door was already opened does not magically close that door.
- `break_into` defines additional objects created once on breakage. Container contents remain inside the container but become visible/reachable when it is open or broken, including when carried.
- Carrying a box uses the body's single inventory unit. Drop it to free capacity before taking its key. The same capacity rule applies when exchanging the rock for the key.
- The world reports permanent exit failure immediately, and the agent loop stops rather than pretending to solve an impossible state.
- Older JSON files using `portable` remain readable as an alias of `takable`; new state uses `takable`.

Use a fresh episode after updating scenario JSON; existing episodes retain their original state.

### The Unknown Room (`two-locked-rooms`)

This is a separate scenario; **The Locked Room** remains available and the configured default is unchanged.

- A jammed box cannot be opened. Two rock hits turn the same entity into **Broken box** and reveal a **Silver key** and a **Brass key**.
- The NPC wakes on a cold stone floor with no memory of arriving. Its goal is simply **Find a way out**, with no advance knowledge of extra rooms or doors.
- The first door requires the silver key; the second room's door requires the brass key.
- The body still has one inventory unit, so exchanging items and backtracking through open doors may be necessary.
- Reaching the second room is progress, not victory. The goal is achieved only upon reaching **outside** through both rooms.
- The NPC remembers previously observed objects and their last-seen room, without receiving hidden scenario data.
- Factual memory and discovery lists are populated only from actor-visible observations, including the initial view. Discovered facts remain remembered after leaving a room; unseen objects and rooms never enter these lists. Plans are tentative intentions, not discovered facts.
- Only crossing the first door reveals the second room and its door; opening a door does not reveal an unvisited destination. Discovering the brass-colored lock provides a reason to return for the spare key. Backtracking is naturally motivated, not forced: the NPC may choose to bring the spare key earlier.
- The main scene shows only discovered rooms and objects, masking unseen door destinations. The complete authoritative snapshot remains available under **Inspect full world JSON**.
- **Reset** retains the active scenario; **New episode** uses the scenario dropdown.

### The Moonwake Treasures (`moonwake-treasures`)

Choose **The Moonwake Treasures**, then **New episode**. This separate, original
19-location adventure draws on classic pirate-adventure puzzle ideas, with new
names, prose and treasures. **The Saltglass Passage** and the default scenario
remain unchanged.

Explore a harbor home, an island's caves and tidal pools, then a second island.
Read the resonant journal to learn travel between two fixed sites. Recover tools,
clear the wide cave route, build a cutter from five materials using a claw mallet,
recruit a navigator and sail out and back. The narrow fissure needs a carried storm
lantern and rejects items larger than one inventory unit; bulky supplies need the
wide route. Read the chart for the buried treasure's bearing and use the trained
bird to clear the observatory approach.

- Carry **8 inventory units**. Supplies can be staged locally; remembered remote
  objects cannot be used. The ship, navigator and gangway move consistently between
  docks, while loose deck cargo stays aboard.
- Tides alternate every **6 successful physical actions**, not wall-clock seconds.
  Model calls, inspection, reading, rejected actions, no-change actions and retried
  request IDs never advance them. **`wait_tide`** advances directly to the next
  phase, with no real-time delay. The observed tide and remaining turns appear in
  the NPC perspective when the sea is visible.
- Enter flooded lagoon/pool routes at high tide only with the carried cork belt.
  Safe shelves prevent drowning when the tide changes; wait there or retreat.
  The buried anchor can be dug up only at low tide.
- Sailing requires the seaworthy cutter, hired navigator, carried chart and
  **high tide**. Leave the resonant journal ashore, not in a bag or on deck.
  Recruitment covers both voyages; sailing never teleports you onto the shore.
- Victory requires **both intact treasures put down directly in the harbor home**
  and the traveler back home. Building the ship, reaching the far island, or merely
  carrying the treasures does not win.

The scenario allows **400 actions, 480 decision calls and 5,400 seconds**. Tide,
excavations, bearings, crew, ship position and discoveries persist across reloads.
All mechanics run through the normal MCP actions and authoritative goal check;
the controller receives no scripted solution. Start a fresh episode to use it.

### The Saltglass Passage (`saltglass-passage`)

An original pirate-themed adventure inspired by early text adventures,
not a reproduction of *Pirate Adventure*'s rooms, wording or walkthrough.

The original **sail loft**, **tide cellar** and **hidden cove** now lead to ten more
locations. Carry **8 inventory units**, rather than one. Supplies may be dropped and
retrieved; opened routes remain usable in both directions. Keep earlier discoveries:
several puzzles reuse the same tools, and the final boat needs supplies from the loft.

| New location | Puzzle |
| --- | --- |
| Wreck beach | Recover cargo with the original mallet; rig the loft's rope to a salvaged hook. |
| Mangrove walk | Sharpen the wreck's blade and clear the thorn curtain. |
| Smugglers' camp | Reuse the cellar's shore key to recover the watch token and supplies. |
| Cliff stairs | Release a lens box with the original marlinspike; reuse the grappling line. |
| Ruined watchtower | Reuse the watch token; combine camp and wreck supplies for a lantern. |
| Lantern gallery | Align the cliff lens and loft reflector foil in the prism jig. |
| Echo cavern | Reuse the mallet; tune a fork with wreck wire and the sharpened knife. |
| Captain's study | Decode a chart using the camp cipher and gallery prism. |
| Treasure vault | Cast a seal using camp wax, the tower mould and the lit lantern. |
| Escape quay | Rig the skiff with loft canvas and the grappling line, carrying spike and knife. |

Read local descriptions and notes to discover contextual actions and what to bring
back. Gate mechanisms use `unlock` with the appropriate carried tool, token or crafted
item, followed by `open` and `go`. Crafting requires intact, exposed local materials;
knowing an item elsewhere does not make it reachable. Craft outputs must be picked
up before they can be used as carried tools.

Choose **The Saltglass Passage**, then **New episode**. Reaching the cove or even the
final quay is no longer victory: the **seaworthy escape skiff must be completed in
the launch slip**. The scenario supplies episode limits of **300 actions, 360 decision
calls and 3,600 seconds**, leaving room for exploration and return trips. Saving proxy
settings preserves these episode limits; global defaults and other scenarios are
unchanged. Scenario JSON may declare optional `budgets`, and gates may reference
declared crafting outputs as keys without revealing those outputs before creation.

Existing saved episodes retain their frozen original scenario and carrying capacity;
start a new episode to use the extended map. No scenario-specific agent planner or
extra narration model calls are added.

### The Forgotten Workshop (`forgotten-workshop`)

An unfamiliar workshop, an aching stomach, and tools that have seen better days. This scenario enables hunger from the start while keeping the body's **1-unit** carrying limit.

Bulky containers must be investigated where they stand. A fragile mallet introduces simultaneous tool-and-target breakage, and broken tools still occupy inventory space until dropped. New routes remain hidden until reached. The goal is still simply **Find a way out**.

Choose **The Forgotten Workshop** in the scenario dropdown and create a new episode. It uses the existing world mechanics and unchanged agent—no walkthrough or scenario-specific planner logic.

### The Unfamiliar Kitchen (`sandwich-kitchen`)

The NPC starts without knowing its assigned task. A visible Post-it asks for a **peanut butter and jam sandwich on the plate**, encouraging a richer recognizable combination with suitable extras while accepting plain PB&J. The assignment becomes known only when that NPC reads the Post-it, not when looking at it or learning a recipe. Arriving in the kitchen or assembling an unplated sandwich does not complete the goal.

- Read the Post-it to discover the task; the story records this discovery chronologically. A separate neutral preparation note is inside the closed bread bin: no best sandwich, preferred fillings or ranked combinations are disclosed.
- Prior-attempt feedback stays hidden until the task is discovered. Ending without reading the Post-it fails the discovery check rather than disclosing hidden sandwich requirements.
- The body still carries **1 unit**. Hold the knife and prepare visible local food in place rather than carrying the ingredients.
- Storage holds peanut butter, jelly, blackberry jam, strawberry jam, almond butter, roasted peanuts, honey, banana, avocado, tomato, cheese, butter, ham and raw bacon. Jar contents remain hidden until opened. Banana, avocado, tomato and cheese can be sliced; tomato can also be diced.
- Discover contextual `read`, `slice`, `dice`, `spread`, `add`, `assemble` and `serve` commands through MCP. Layer spreads and prepared fillings on either bread slice. Any available jam works; real peanut butter is still required, so almond butter can be an extra but not a substitute.
- A working peanut butter maker and compact electric stove share **one socket**. Use `plug`, `turn_on`, `turn_off` and `unplug` to reuse the outlet. `grind` transforms roasted peanuts into fresh peanut butter. Carry the frying pan and `place` it on the stove, then `cook` bacon with the stove connected and switched on. Raw bacon is not an available sandwich filling.
- Recipes live in scenario JSON, not the agent prompt. They validate intact local ingredients/tools and preparation states, consume ingredients, and create or update real objects. Serving moves the finished food onto its destination.
- Food has approximate **per-item/per-portion** nutrition: serving mass in grams, calories in kcal, and protein, carbohydrates, fat, fiber and sugar in grams. These are illustrative game values, not precise product labels.
- Equal cuts divide every nutritional value equally. Spreading and adding fillings transfer consumed portions to the bread; assembly sums the prepared slices. Grinding and cooking conserve the illustrative portion nutrition (no simulated cooking losses). The original plain sandwich totals **120 g, 378 kcal, 12 g protein, 57 g carbs, 12 g fat, 3.8 g fiber and 16 g sugar**; variants reflect their actual ingredients.
- Ingredient provenance persists into the sandwich, final world verification and story. Pass/fail review accepts plain PB&J and evaluates recognizable richer variations, praising appropriate extras rather than an arbitrary pile of ingredients. No numeric quality score is added.
- Nutrition appears only when the food is discovered, in observations, memory, object statistics and story prose. Missing legacy values remain **unknown**, including after processing; they are not treated as zero. Non-food objects have no nutrition.
- Nutrition is informational for now: existing hunger/recovery mechanics are unchanged, and kitchen needs start disabled. Duplicate requests cannot consume ingredients or nutrition twice.

Select **The Unfamiliar Kitchen**, then create a fresh episode. The persistent default and existing escape scenarios are unchanged. Previously saved attempts retain their original frozen scenario and reviewed story.

Use **Copy final story** above the narrative after an attempt and its review finish, or when viewing a finished archived episode. It copies the exact displayed story as plain text with paragraph breaks, without technical logs or grading metadata. Clipboard permission errors are shown explicitly.

### The Glassworks Relay - Precision Assembly

Choose **The Glassworks Relay - Precision Assembly**, then create a fresh episode.
This single-agent reasoning challenge has two rooms, distributed written clues,
a two-unit carrying limit, bulky assemblies, reusable reference tooling and two
machines competing for one socket. The controller and agent prompt contain no
solution. Commissioning, not merely assembling or transporting a relay, wins.

**Mistakes can end the episode immediately.** There are no spares or undo:
staking the wrong spring, sealing before calibration, testing before zeroing,
pressing without the glass-tipped tooling, or discarding either glass reference
consumes irreplaceable resources and produces an authoritative terminal failure.
Breaking essential tools, parts or machines also fails. Read the work order,
tooling card and reference record before committing; the tolerance must be
deduced rather than selected by appearance. Ordinary capacity, power and
out-of-reach errors still reject the action without consuming parts.

Scenario authors can declare `failure_outputs` (recipe-output IDs mapped to
failure reasons) and `required_intact_entities` (initial or recipe-output IDs).
Goal verification checks these before success or assignment discovery, so a
destructive action cannot be hidden by an unread assignment. Failure output
existence remains terminal even if its wreckage is subsequently moved or consumed.
Consumed intact ingredients do not count as broken. Both declarations default to
empty, preserving other scenarios and frozen existing episodes.
The existing controller labels an impossible objective **blocked**, stops immediately
and records a **fail** completion review. A discovered assignment remains known
after leaving the room containing its previously read work order.

CLI controls use the same server/controller:

```powershell
.\.venv\Scripts\python.exe -m npc_sim.cli start
.\.venv\Scripts\python.exe -m npc_sim.cli start --scenario two-locked-rooms
.\.venv\Scripts\python.exe -m npc_sim.cli start --scenario saltglass-passage
.\.venv\Scripts\python.exe -m npc_sim.cli start --scenario moonwake-treasures
.\.venv\Scripts\python.exe -m npc_sim.cli start --scenario forgotten-workshop
.\.venv\Scripts\python.exe -m npc_sim.cli start --scenario sandwich-kitchen
.\.venv\Scripts\python.exe -m npc_sim.cli start --scenario glassworks-relay
.\.venv\Scripts\python.exe -m npc_sim.cli step
.\.venv\Scripts\python.exe -m npc_sim.cli run
.\.venv\Scripts\python.exe -m npc_sim.cli pause
.\.venv\Scripts\python.exe -m npc_sim.cli status
.\.venv\Scripts\python.exe -m npc_sim.cli assess
```

## JSON data

Application documents use JSON. Older world action responses use a per-world
SQLite retry archive (Python's standard library; no database service is needed).

- `data\config.json`: proxy, world transport, and execution budgets.
- `data\scenarios\*.json`: scenario definitions and body/need settings.
- `data\worlds\<world-id>\world-state.json`: authoritative entities and actor `self` objects.
- `data\worlds\<world-id>\world-state.requests.sqlite3`: durable older action/intervention
  responses, looked up by request ID rather than loaded into every world snapshot.
- `data\worlds\<world-id>\needs-command.json`: private admin needs command, processed idempotently by the world.
- `data\runs\<run-id>\`: run, goal, NPC memory, transcript index, event documents, and terminal summary.
- `data\runs\<run-id>\scenario.json`: frozen scenario for new attempts; completion analysis is included in run/summary JSON.
- `data\scenario-memory\<scenario-id>.json`: persisted, evidence-linked failure feedback for subsequent attempts.

Events are immutable numbered JSON documents. Replaceable documents use atomic replacement, with short, logged retries for transient Windows file-handle conflicts. Persistent write failures remain explicit errors. The transcript index is rebuilt from events on restart. Interrupted episodes are archived for inspection, not automatically resumed. Use the archive button to inspect previous runs and **Export JSON** for the current full transcript.

The live world checkpoint retains only the newest retry result, committed atomically
with its world changes. Previously committed results are durably archived before
being removed from that checkpoint; retries still return the exact original
response, including after a restart. Legacy inline histories migrate automatically
without changing village progress. Missing or mismatched archives are explicit
errors. Pause before backing up the entire world directory, not just its JSON checkpoint.
Named episode saves remain self-contained JSON, including all retry history, and
restore into independent worlds. Existing save files are not rewritten.
Restoring prepares the copied world's retry archive before connecting its MCP
process, so large histories do not consume the connection's startup timeout.

Never expose the data directory as static web content: it contains hidden world state.

## World MCP contract

The bundled server is launched as a separate Python process over MCP stdio. It owns all actor bindings for an episode and advertises:

- `world_manifest`: contract version, operation names, supported goals, idempotency.
- `world_observe`: actor-local `self`, visible entities, contextual action descriptions and JSON schemas.
- `world_act`: one validated action with a request ID and expected observation revision.
- `world_evaluate_goal`: authoritative actor-specific verification.
- `world_select_actor`: controller-only selection of a pre-bound actor/session for the next turn; the manifest lists supported bindings.

The model sees only contextual actions, not infrastructure MCP tools. Admin inspection/toggles use private local JSON files, not NPC tools.

The engine supports independent bound `self` objects and atomic competing actions within one owning world process. Scenarios may declare up to two agents with unique IDs, roles and optional starting locations. The controller schedules autonomous turns sequentially, persisting each agent's memory and loop state in the run. Simultaneous model/action execution is not implemented. Do not launch multiple writer processes against the same world file.

### Second-world fixture

Choose `sleepy-room` in the scenario dropdown, or start with `--scenario sleepy-room`. This replaces the key puzzle with an exhausted traveler and an open archway. A new `nap` action accepts a `duration` argument. The planner/controller code remains unchanged. To change the persistent default, set `world.scenario` in `data\config.json` and restart.

For another MCP world, configure `world.command` and `world.args`, or use `streamable_http` with `world.url`. A replacement must implement the manifest and normalized contracts. **Admin-enabled startup additionally requires a compatible privileged snapshot/control adapter**; the bundled first version supports the local world JSON format, not arbitrary remote admin inspection. Transport support alone does not make any MCP server a compatible world.

Custom stdio arguments can use `{scenario}`, `{state}`, `{world_id}`, `{actor_id}`, and `{session_id}` placeholders. The server must write its compatible privileged snapshot to the supplied state path. Do not use these placeholders inside literal JSON arguments without escaping braces.

## Development and validation

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
Set-Location ui
npm run build
npm test
npm run dev
```

The Vite development UI at `127.0.0.1:5173` proxies admin requests to the Python service at `127.0.0.1:8000`.

Tests cover hidden-state isolation, puzzle transitions, carrying constraints, needs, independent actor identities, competing ownership, request deduplication, atomic persistence, MCP stdio round-trips, invalid-decision repair, loop termination, proxy errors, and the replacement fixture. Real-model success-rate evaluations require the running proxy and a selected model.

Run the opt-in real-model evaluation (20 escape episodes and 10 replacement-world episodes; consumes proxy/model requests):

```powershell
.\.venv\Scripts\python.exe -m npc_sim.evaluate --model claude-haiku-5.5
```

Evaluation episodes use separate JSON data directories under `data\evaluations\`; they do not change the dashboard's active world. Each report checks authoritative completion, the action/time budgets, and a 90% success threshold.

See [SPEC.md](SPEC.md) for the broader acceptance criteria and the Colossal Cave Adventure roadmap. Full-game integration, automatic crash-resume, arbitrary remote-world admin adapters, and simultaneous multi-agent execution are not implemented in v0.1.
