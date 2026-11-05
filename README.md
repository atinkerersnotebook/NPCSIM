# NPCSIM v0.1

A local goal-driven NPC laboratory: a Python world and agent controller, real MCP actions, TypeScript player and admin views, and readable JSON persistence.

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

Open **http://127.0.0.1:8000** for the simplified **player view**. Choose a scenario
and your cast, click **New adventure**, then **Play**. Only the relevant
Play/Pause/Continue controls appear; **Restart** recreates the current adventure
with its existing cast and skills policy. Cast/scenario changes apply when you
choose **New adventure**. **Skills & challenge options** retains the NPC-only
ability policy. Watch the discovered map, characters, continuous story and
outcome without configuration editors, model budgets or technical logs.

Use **Admin view** in the header (or **http://127.0.0.1:8000/?view=admin**) for
setup, detailed perspectives, archives and technical inspection. **Player view**
returns to the simplified screen. Switching views does not stop or reset the
live episode; unsaved form selections are reloaded. These are presentation modes,
not authentication or authorization: both use the same local API, and the admin
view remains accessible without a login. The service is loopback-only.

### Private questions and live world changes

Both views include **Ask an NPC** and **Dungeon Master intervention**:

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

Interactions need the current active adventure. They pause running play at a
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

No puzzle solution is embedded in the controller or model instructions. Scripted decisions exist only in automated tests. A missing proxy produces an explicit error, never simulated success. Start a fresh episode after a terminal error.

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
An AI defeat ruling is an explicitly labeled judgment, not a proof of
unsolvability. Missing mandatory team skills are first checked deterministically
by the world and stop the attempt without a supervisor model call.

The supervisor cannot change the world, approve an illegal action or declare
success. Its privileged review context and rulings are not copied into NPC
memories. Unknown evidence references or unauthorized rulings receive one repair
attempt; persistent invalid output or proxy failure ends with an explicit error.
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
latest 20 heard messages in each agent's observations. Remote observations and private
plans are not shared. Roles suggest responsibilities but do not script actions.
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

All application data is JSON; no database is used.

- `data\config.json`: proxy, world transport, and execution budgets.
- `data\scenarios\*.json`: scenario definitions and body/need settings.
- `data\worlds\<world-id>\world-state.json`: authoritative entities and actor `self` objects.
- `data\worlds\<world-id>\needs-command.json`: private admin needs command, processed idempotently by the world.
- `data\runs\<run-id>\`: run, goal, NPC memory, transcript index, event documents, and terminal summary.
- `data\runs\<run-id>\scenario.json`: frozen scenario for new attempts; completion analysis is included in run/summary JSON.
- `data\scenario-memory\<scenario-id>.json`: persisted, evidence-linked failure feedback for subsequent attempts.

Events are immutable numbered JSON documents. Replaceable documents use atomic replacement, with short, logged retries for transient Windows file-handle conflicts. Persistent write failures remain explicit errors. The transcript index is rebuilt from events on restart. Interrupted episodes are archived for inspection, not automatically resumed. Use the archive button to inspect previous runs and **Export JSON** for the current full transcript.

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
