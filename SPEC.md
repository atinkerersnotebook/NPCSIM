# Goal-Driven NPC Simulator Specification

## 1. Purpose

Build a text-adventure world and an autonomous NPC that pursues a goal by discovering its surroundings, choosing actions, observing results, and revising its plan.

The initial goal is **leave the room**. The NPC begins in one room containing a locked door and a closed box on the floor. A key is inside the box.

The puzzle solution must emerge from observations and goal-directed decisions. It must not be encoded in the NPC's prompt, controller, or action sequence.

The world is exposed through Model Context Protocol (MCP). Replacing the world must not require rewriting the NPC planner.

The long-term target is a Colossal Cave Adventure world that the agent can complete through the same MCP interface. The one-room escape puzzle is the first validation stage, not the final scope of the simulator.

## 2. Scope and Proposed Defaults

### MVP

- One deterministic world, one active NPC, and one active root goal for the initial demo; actor identity is designed for multiple independent NPCs in a shared world.
- An LLM-backed decision component inside an application-controlled loop, with all model access through an LLM proxy.
- Local MCP server over stdio; configurable remote Streamable HTTP support.
- Runtime discovery of tool schemas and currently available interactions.
- Partial observability: the NPC sees only what the world exposes to it.
- Sequential actions, explicit failure handling, bounded runs, and persistent transcripts.
- CLI controls to start, step, run, pause, resume, and reset.
- A web-based admin panel with live world state, NPC decision explanations, and the complete interaction timeline.
- TypeScript for the admin UI; Python is the proposed language for the simulator, agent, admin API, and world MCP server.
- Plain-text JSON files for all persistent application data; no database.
- No Azure deployment or specific model provider required; the LLM proxy provides access to the selected model.

TypeScript for the UI and JSON-file persistence are requirements. Python for the agent/world/backend is the proposed default, reflecting the user's preference that these components could be Python. UI/backend frameworks and hosting choices remain open. The LLM proxy's API contract, authentication, and model selection are confirmed before implementation.

### Out of scope for MVP

- Concurrent multi-NPC orchestration in the initial demo, combat, procedural worlds, and real-time simulation. Actor-scoped `self` objects and shared-world isolation are in scope.
- Training or fine-tuning a model.
- A graphical game renderer; the admin dashboard is in scope.
- Arbitrary MCP servers working without a compatible contract or adapter.
- Implementing Colossal Cave Adventure in the initial demo; it is a planned later milestone.

## 3. Architecture

```text
User goal + run configuration
              |
              v
NPC Simulator / Loop Controller
  - run state and budgets
  - observed memory and goal tracking
  - decision validation
  - transcript persistence
              |
              +--> LLM Proxy Client
              |          |
              |          v
              |      LLM Proxy
              |          |
              |          v
              |      Decision Model
              |        selects one action and a short explanation
              |
              v
World Adapter / MCP Client
  - discovers tools and schemas
  - obtains observations and contextual actions
  - validates and executes calls
              |
              v
Replaceable World MCP Server
              |
              v
Authoritative World Engine
  - hidden state
  - rules and state transitions
  - actor-visible observations
  - goal evaluation
```

The world owns truth. The NPC owns its beliefs and tentative plans. The controller owns execution limits and tool access.

The NPC is embodied through a world-owned `self` object. Each actor has its own `self`, containing its identity, body, location, inventory, needs, and observable capabilities and constraints. The body is not a capability definition embedded in the planner. The world enforces restrictions such as carrying capacity, available manipulation capabilities, movement limitations, and enabled needs.

One world instance may contain multiple actor `self` objects. The MCP connection/session context binds each agent to its own actor. The NPC observes `self` as "me"; it cannot choose a different acting identity in an action request. Other actors are exposed only as ordinary visible entities when world rules permit.

The NPC must never import the world engine, inspect scenario definitions, or read hidden state directly. Test code may inspect authoritative state to verify results.

The admin panel connects to a separate simulator admin API for episode controls, authoritative world snapshots, and run events. This privileged view may reveal hidden state for debugging but must never become an input to the NPC or decision model.

### LLM proxy integration

The simulator communicates with the model exclusively through the configured LLM proxy. It does not call model-provider endpoints directly or hold provider credentials. The TypeScript UI calls the simulator admin API, never the LLM proxy directly.

The proxy client supplies:

- The NPC's decision instructions, current goal, actor-visible observation, and bounded memory.
- Discovered action descriptions and argument schemas.
- The requested model or routing alias, when supported by the proxy.
- A run/cycle correlation ID, request deadline, and supported output constraints.

The proxy returns a model decision, or an explicit error. The client normalizes the response into the decision structure defined in section 7. The controller validates it before executing any world action.

If the proxy supports structured output or tool calling, use that capability. Otherwise request JSON and apply the same schema validation and bounded repair policy. Model-returned tool calls are action proposals only: the controller, not the proxy or model, executes MCP calls.

The LLM proxy is located at `127.0.0.1:9901`. The user confirmed an OpenAI-compatible HTTP API using `/v1/models` and `/v1/chat/completions`, with no authentication. Use `http://127.0.0.1:9901/v1` as the default base URL, with a configuration override. Model selection is discovered when the proxy is available.

Proxy configuration includes its endpoint, API/protocol selection, authentication reference, and optional model/routing alias. Do not assume the proxy implements an OpenAI-compatible API until its contract is confirmed. Adapt proxy-specific request and response formats behind the proxy client without changing world adapters or goal-loop logic.

For the local MVP, the simulator must run in a network context that can reach the host's loopback proxy. In a container or remote deployment, `127.0.0.1` refers to that container or remote host, so the endpoint must be configured appropriately rather than reused blindly.

Proxy authentication secrets are resolved externally and excluded from prompts and transcripts. Provider credentials remain managed by the proxy. Proxy errors, rate limits, timeouts, and malformed responses are surfaced explicitly; there is no fallback to a direct provider connection. Requests and retries count against the model-call and wall-time budgets.

During active play, connection failures, timeouts, HTTP 408/429 and HTTP 5xx pause
the adventure rather than ending it. Freeze simulation time and the episode timer,
preserve world state and memories, and show an explicit reason. Retry only after
an operator resumes or steps. A failed decision retains its actor's turn; a failed
supervisor review is retried before another action, never by replaying a committed
action. Malformed decisions and non-retryable HTTP failures remain terminal errors.

## 4. Initial World

### Authoritative initial state

| Entity | Initial state |
|---|---|
| NPC body | In the room; empty inventory; able to inspect, manipulate the box and door, carry the key, and cross the exit |
| Room | Contains a door and a box on the floor |
| Door | Closed and locked; leads to an exit boundary |
| Box | Closed, unlocked, takable; contains the key; 50 health |
| Key | Hidden inside the box; portable; matches the door |
| Rock | Takable, on the floor; deals 25 damage |
| Door lock | Fixed to the door; locked; 75 health |
| Goal | Incomplete |

The exit boundary is a terminal location, not a second playable room.

### Visibility

- Initial observation describes the room, a closed door, and a closed box.
- A takable rock is also visible. The attached door lock is initially hidden.
- The key and box contents remain hidden until the box is opened or broken.
- Explicitly looking at the door reveals its attached lock.
- Opening a locked door fails and reveals the door's locked status, but does not reveal the attached lock.
- Looking at the closed box does not reveal its contents.
- An object must be visible and reachable to be targeted.
- Successful actions return updated actor-visible observations.

### MVP verbs and rules

| Verb | Arguments | Behavior |
|---|---|---|
| `look` | Optional visible target | Describe surroundings or inspect an entity |
| `open` | Target | Open the box, or an unlocked door |
| `unlock` | Target and item | Unlock the door using the matching carried key |
| `take` | Target | Move a visible takable object into inventory |
| `drop` | Target | Move a carried item to the current location |
| `go` | Exit target | Cross an open, unlocked exit |
| `hit` | Target and carried item | Apply the item's damage to the target's health |

Opening the box reveals the key. Taking it removes it from the box. Dropping it makes it visible on the room floor. Unlocking does not open the door or consume the key. Opening the door does not move the NPC. Crossing the door marks the NPC as escaped.

Invalid actions leave physical world state and needs unchanged and return an explicit reason. A failed attempt may reveal an observable fact, such as a locked door; updating actor knowledge is not a physical mutation or a needs-clock tick. Actions that are already satisfied, such as opening an open box, return `NO_CHANGE` rather than being counted as progress.

### Object damage and breakage

Every object has a takability flag, damage strength, and current/maximum health. Health zero means broken. Locations are not damageable. Door and lock are not takable; box, rock, and key are. The rock deals 25 damage, the box starts at 50 health, and the separate lock starts at 75 health; these values are configurable in scenario JSON.

Hitting requires an intact carried item and a visible target. Invalid hits, duplicate requests, and attempts to hit an already broken object cannot apply extra damage or advance needs. Breaking containers exposes their existing contents; configured `break_into` prototypes create additional entities exactly once. A carried container keeps its contents and still consumes carrying capacity.

Each impact applies reciprocal damage simultaneously: the carried item's damage reduces the target's health, and the target's damage reduces the carried item's health. Clamp both losses at zero health using pre-impact scores. Either or both objects may break and apply their normal breakage effects. A zero-damage tool may still receive damage; indestructible objects do not lose health. One impact advances the actor's action clock at most once, and an idempotent retry repeats neither damage nor breakage.

Two rock hits break the box and reveal the key plus splinters. Opening the box reveals the same key without damage. Three rock hits permanently break the lock; earlier damage warns the actor, never opens the door, and does not prevent the key from working while the lock remains intact. A broken lock on a closed door cannot be unlocked or opened. Breaking the door also blocks passage. The world reports irreversible exit failure authoritatively and the controller terminates the episode as blocked. An already-open door is not automatically closed by subsequent lock damage.

### Additional scenario: The Unknown Room

Provide a separately selectable `two-locked-rooms` scenario without replacing the original. Its jammed, 50-health box cannot be opened; two hits with the 25-damage rock rename the same entity to **Broken box** and expose silver and brass keys retained inside it. No second box entity is created.

Start with the NPC waking on a cold stone floor without knowing how it arrived. Its objective is only **Find a way out**. The scenario title, initial entity names, and model-facing goal identifier must not disclose the second room or door. Opening a door does not reveal an unvisited destination; crossing it reveals the next room. Discovering the second lock naturally motivates retrieving the remembered brass key, but spare-key preparation is not prohibited or scripted.

The silver key unlocks the first room's door. The brass key unlocks the second room's door to outside. Both locks require explicit examination of their respective doors before becoming visible. Doors can be traversed from either adjoining room after opening. The one-inventory-unit body constraint remains enabled.

Use `actor_at_exit_location` verification: arriving in the second room must not complete the root goal. Broken essential doors or locks that eliminate all routes to the final exit produce authoritative failure. The dashboard shows only discovered rooms and objects, masking unseen door destinations, with full privileged state retained in the JSON inspector. Scenario selection applies to a new episode, while reset retains the active scenario. Persist actor-observed entity facts with their last-seen room to support backtracking, without treating stale observations as current visibility.

### Food preparation, nutrition and the Unfamiliar Kitchen

Provide `sandwich-kitchen`, titled **The Unfamiliar Kitchen**, as a separate scenario with a visible Post-it on the worktop, a recipe note inside the closed bread bin, a cupboard, fruit bin and refrigerator. Store the knife, plate and two closed jars in the cupboard; bread in the bread bin; tomato in the fruit bin; cheese, butter and ham in the refrigerator. Jar contents are peanut butter and jelly (jam). Initial observations must not disclose hidden contents, note text, future prepared outputs or their nutrition. Reading a visible local note reveals its text to that actor and does not advance action-clock needs.

The Post-it asks for a peanut butter and jam sandwich on the plate, encouraging a richer recognizable combination with suitable extras while accepting plain PB&J. Pair optional scenario fields `initial_objective` and `goal_note_id`; validate a nonblank exploration objective and an existing readable task note. Until the actor reads that note, present only the exploration objective and an unrevealed task; omit the hidden evaluator type from model context, keep prior failure feedback deferred, and evaluate only `objective_discovered=false`. Looking at the Post-it or reading the separate recipe does not assign the task. Visible equipment or recipe knowledge may suggest possibilities, but must not be treated as an assignment. A completed sandwich cannot satisfy an undiscovered assignment.

After reading the configured note, actor-scoped world observations supply `discovered_goal` containing `objective`, `type` and `source_id`. The controller validates evidence of reading the source, activates the assigned goal, clears the old exploratory plan, loads previous-attempt feedback and records a chronological `goal_discovered` story event. Initial event payloads remain immutable. Discovery persists per actor, never transfers to another actor, and repeated reads are idempotent. Existing scenarios without a task note retain their initially known goals.

The body retains one inventory unit. A carried intact knife supports slicing, dicing, spreading and adding fillings on visible food physically in the current room, without carrying the ingredients. Slicing the mini loaf produces two equal bread slices. Tomato supports slicing and dicing; cheese, banana and avocado support slicing. Spreads and prepared extras can be layered on either slice. Assembly consumes both bread slices and requires their combined ingredient provenance to include real peanut butter and jelly, blackberry jam or strawberry jam. Almond butter alone must not replace peanut butter.

Add bacon, avocado, almond butter, blackberry jam, strawberry jam, peanuts, banana and honey alongside existing food. The neutral preparation note describes techniques and equipment only, never the best sandwich. Final pass/fail story review evaluates recognizable combinations or coherent familiar variations, accepting plain PB&J and favoring suitable extras rather than ingredient-count stuffing. Preserve separate execution and grading outcomes and failure memory.

Electrical appliances have optional `power` and `plugged_into` state. One outlet accepts one appliance at a time; connecting and powering require intact local equipment. Expose `plug`, `unplug`, `turn_on`, `turn_off` and `place` through contextual MCP schemas. Switch off before unplugging; connected appliances cannot be taken. Carry and place the pan on the stove. Recipe `powered` and `placements` prerequisites gate grinding peanuts and cooking bacon. Raw bacon cannot be added as a sandwich filling.

Scenario JSON defines `recipe_actions` descriptions and typed `recipes`: exact argument bindings, carried/powered tools, required placements and preparation states, ingredient-alternative groups, consumed ingredients, preparation updates, output prototypes and an optional nutrition-transfer target. Food `ingredients` labels propagate through cuts, spreads, added fillings and assembly; transformation outputs explicitly label fresh peanut butter and cooked bacon. Contextual schemas contain only visible/local, intact arguments with satisfied preparation prerequisites; hidden rules and future object IDs do not enter model observations. Validate references and action-name conflicts at scenario load. Apply effects atomically within the existing revision/idempotency contract. Recipe outputs appear on a visible local work surface; no new output is silently added to inventory.

Food may have optional `nutrition` with nonnegative finite `serving_grams`, `calories_kcal`, `protein_g`, `carbohydrates_g`, `fat_g`, `fiber_g` and `sugar_g`. Values describe the whole item or measured portion and are illustrative. Non-food entities cannot have nutritional values. Equal food outputs split input totals equally; spreading adds source nutrition to the existing target; assembly sums consumed food. Any unknown input nutrition makes the resulting total unknown rather than inventing a zero. Consumed entities become absent and must not be counted as available food. Persist nutrition and expose it only through discoveries; legacy food without values remains compatible. Existing need-recovery scoring is unchanged.

Use a world-owned `entity_prepared` evaluator with scenario-only `goal_entity_id` and `goal_location_id`. The kitchen goal requires the actual recipe-created sandwich directly on the intact plate, not a goal claim, wrong food, actor location or an unplated output. `serve` moves visible local food onto an open local plate/surface without requiring it to be carried. The controller and MCP evaluation transport remain generic. Active/achieved cooking goals must not render escape or exit markers; use generic goal-completion UI and factual cooking narrative.

Provide **Copy final story** for completed live and archived attempts. Copy the exact reviewed/displayed narrative as plain text with paragraph separators, not HTML, technical events or grading metadata. Disable it while execution/review is unfinished or no story exists; display explicit clipboard support/permission errors. Archived copying must not alter the live episode.

### Body and carrying rules

- The world associates the NPC's acting identity with its `self` object and body; the planner cannot bypass its constraints by selecting another actor.
- Inventory belongs to that actor's `self` and is constrained by its body. Both are authoritative world state.
- Each world defines its own carrying rules, such as item count, weight, volume, slots, or required free manipulators. The planner must not assume a universal capacity formula.
- The MVP body can carry the key and perform every interaction needed to escape. Its scenario explicitly defines and exposes the applicable capacity rule.
- Each item has a positive integer `inventory_units` cost, defaulting to 1 for older JSON. Interpret units as an abstract weight/bulk measure; the body's `capacity` bounds their sum, not item count. Puzzle items and the initial body capacity remain 1 unit. Containers consume their declared units, with contained items charged only when separately taken.
- Pickup requires both current visibility and physical presence in the actor's room, following container ancestry. Previously discovered items elsewhere remain in memory but cannot be remotely picked up. Exposed contents of local or carried containers remain reachable.
- Taking an item checks visibility, reachability, takability, and body capacity before transferring ownership. A failed check leaves ownership and body state unchanged.
- Dropping an item releases its occupied capacity according to world rules.
- Movement and manipulation actions are also checked against the body's current capabilities.
- Body changes update observations and contextual action descriptors. The NPC replans from those changes rather than using a cached capability assumption.
- Only observable body properties are exposed; hidden conditions remain hidden until the world reveals them.

### Optional needs constraints

- `self.needs` contains world-defined needs such as food and sleep. Each need has a stable ID, an enabled flag, an observable level, and descriptions of any observed effects.
- Needs are configured independently per actor. The initial escape demo disables food and sleep constraints so the puzzle remains unchanged.
- Enabling a need makes its configured world rules active. Disabling it suspends progression and effects; the stored level is retained and there is no retroactive catch-up when re-enabled.
- The world defines units, level direction, progression, thresholds, and recovery rules. The planner must not assume that a high value always means good or bad.
- For deterministic MVP simulation, enabled needs progress on a documented actor-local action clock, not wall time or another actor's actions. Rejected, read-only, and no-change actions do not advance that clock. Every response reflects any resulting need changes.
- A state-changing action applies its effects and configured need progression in one atomic transition. The world checks preconditions against current state before that transition.
- Needs may impose penalties or block actions, such as exhaustion preventing strenuous movement. Rejections include an observable reason such as `NEED_CONSTRAINT`, without exposing hidden conditions.
- Recovery uses dynamically discovered world actions, such as eating suitable food or resting in an allowed place. The planner must not hard-code `eat` or `sleep` as universal verbs.
- The NPC can create maintenance subgoals to satisfy enabled needs when they threaten its root goal. Disabled needs do not generate required maintenance work.
- Before enabling a needs configuration, the world validates that its definitions and required recovery mechanics are supported. A supported but unsolvable scenario is reported as blocked during play, never bypassed by silently disabling a constraint.
- Administrators can toggle needs through a privileged, serialized control operation. The change is persisted, recorded as an event, and reflected in actor observations and action descriptors before the next decision. NPCs cannot toggle their own constraints.
- In shared-world mode, toggling one actor's needs does not change another actor's settings or levels.

## 5. Replaceable MCP World Contract

### Two levels of discovery

1. **MCP tool discovery:** use `tools/list` and advertised input schemas to discover callable tools.
2. **World affordance discovery:** obtain descriptions of actions and targets relevant to the current observation.

MCP tool discovery alone does not explain which objects are visible or which interactions are currently relevant.

### Proposed normalized interface

The following names are application-contract proposals, not standard MCP methods. A world may expose them directly as MCP tools or provide an adapter mapping its own tools to these operations.

| Operation | Purpose |
|---|---|
| `world.observe` | Return the actor-visible observation and contextual action descriptors |
| `world.act` | Execute one discovered action against the current observation |
| `world.evaluate_goal` | Return authoritative goal status and permitted supporting evidence |

Session creation, actor registration/binding, and reset belong to the simulator's control plane. They are not tools available to the NPC. Distinguish the shared `world_id`, the world-assigned `actor_id`, and the connection/session binding; separate actors can inhabit the same world without sharing identity or NPC memory.

Each connected world must supply a versioned JSON manifest identifying these operations, session semantics, supported goal types, and retry/idempotency behavior. An incompatible contract fails startup with a useful diagnostic.

### Observation envelope

```json
{
  "contract_version": "1.0",
  "world_id": "world-001",
  "session_id": "session-001",
  "observation_revision": "obs-001",
  "self": {
    "actor_id": "npc-001",
    "location_id": "room",
    "body": {
      "id": "npc-body",
      "description": "Your body can move and manipulate nearby objects.",
      "capabilities": ["move", "manipulate"],
      "carrying": {"rule": "inventory_units", "limit": 1, "used": 0}
    },
    "inventory": [],
    "needs": [
      {
        "id": "food",
        "enabled": false,
        "level": 0,
        "scale": {"min": 0, "max": 100, "higher_means": "more_hungry"},
        "effects": []
      },
      {
        "id": "sleep",
        "enabled": false,
        "level": 0,
        "scale": {"min": 0, "max": 100, "higher_means": "more_tired"},
        "effects": []
      }
    ]
  },
  "location": {"id": "room", "description": "A small room."},
  "narrative": "A closed door and a closed box are visible.",
  "visible_entities": [
    {"id": "door", "description": "A closed door."},
    {"id": "box", "description": "A closed box on the floor."}
  ],
  "actions": [
    {
      "id": "inspect",
      "description": "Look at your surroundings or inspect a visible entity.",
      "arguments_schema": {
        "type": "object",
        "properties": {"target": {"type": "string"}},
        "additionalProperties": false
      },
      "targets": ["door", "box"],
      "availability": "available"
    }
  ]
}
```

The example shows one descriptor, not the complete initial action list. Its one-unit capacity is an illustrative MVP scenario setting, not a requirement for every body. `self` is the canonical source for the acting NPC's identity, location, and inventory; the outer `location` describes the currently observed place, not a second actor-position field. The body envelope has a stable identity and description, with world-defined observable capability and constraint data; adapters preserve these semantics without imposing a universal body model. Visible properties are reported only when observed. Omitted facts mean unknown, not false.

Action IDs are opaque, session-scoped identifiers. The planner uses their descriptions and schemas, not hard-coded verb names. Actions may accept arbitrary schema-defined arguments, not only a target and item.

Descriptors distinguish `available`, `blocked`, and `unknown` applicability. Blocked reasons are supplied only when known to the actor. Uncertain attempts are allowed where the world advertises them; hidden prerequisites and solutions must not leak through action metadata.

### Action request and response

```json
{
  "session_id": "session-001",
  "request_id": "request-001",
  "expected_observation_revision": "obs-001",
  "action_id": "inspect",
  "arguments": {"target": "door"}
}
```

The response contains:

- Matching session and request IDs.
- Outcome: `APPLIED`, `NO_CHANGE`, or `REJECTED`.
- A machine-readable code and human-readable explanation.
- The resulting observation, including its revision and contextual actions.

Expected domain codes include `LOCKED`, `NOT_VISIBLE`, `NOT_REACHABLE`, `NOT_PORTABLE`, `NOT_CARRIED`, `CAPACITY_EXCEEDED`, `BODY_CAPABILITY_REQUIRED`, `NEED_CONSTRAINT`, `WRONG_ITEM`, `NO_CHANGE`, and `STALE_OBSERVATION`. More complex worlds may introduce new codes without planner changes.

Transport failures, MCP tool errors, invalid output schemas, and game-rule rejections remain distinguishable. None are converted into successful observations.

The MVP world deduplicates mutation requests by actor-bound session and request ID. Reusing an ID with different arguments is rejected. Duplicate requests return the recorded outcome without applying the mutation again.

### Independent actors in a shared world

- Shared objects and rooms belong to the world; actor-local body, location, and inventory belong to each `self`.
- Each NPC has separate goals, memory, decision context, budgets, and transcript. One actor's private memory or inventory contents are not automatically visible to another.
- Actor sessions are resolved through trusted server-side bindings, not an actor ID supplied by the model. Session handles must not authorize impersonation from an untrusted caller.
- The world serializes mutations across actors and checks current shared-state preconditions atomically. If two actors try to take the same key, exactly one may acquire it.
- Shared changes invalidate affected observations. Refresh stale observations before replanning; never execute a cached action as if another actor's changes had not occurred.
- Goal verification uses the requesting actor's `self`: another NPC escaping does not satisfy this NPC's goal.
- Pausing an actor pauses only its loop. Resetting a shared world is a distinct privileged operation requiring confirmation because it affects all attached actors.
- The first demo runs one actor. Contract tests exercise two actor bindings in one world; a multi-agent scheduler and fairness policy can be added later without changing `self` semantics.

## 6. Goal Representation

A root goal includes:

- ID and natural-language objective.
- Priority; MVP supports one active root goal.
- A world-supported goal type and its parameters.
- Status: `pending`, `active`, `achieved`, `blocked`, or `failed`.
- Optional agent-generated subgoals with dependencies and observed evidence.

For the first scenario:

```json
{
  "id": "escape-room",
  "objective": "Leave the room.",
  "priority": 1,
  "verification": {
    "type": "actor_outside_start_location",
    "parameters": {}
  },
  "status": "active"
}
```

The control plane binds the verification to the actor's `self` and starting location. This goal type is part of the proposed world contract, not a universal predicate for arbitrary worlds.

The NPC may infer subgoals such as investigating the exit or finding a way to unlock it. Subgoals are hypotheses, not preloaded puzzle steps. They can be revised or abandoned after new evidence.

Only authoritative verification may mark the root goal achieved. Opening or unlocking the door is insufficient. Unsupported goal types are rejected at startup; unverifiable objectives cannot produce a success result.

## 7. Goal-Driven Loop

Each cycle:

1. Check cancellation, pause state, and remaining budgets.
2. Obtain or use the latest validated observation and refresh stale capabilities.
3. Evaluate the root goal against the world.
4. Update observed facts, attempted actions, and tentative subgoals.
5. Ask the model through the LLM proxy to select one discovered action.
6. Validate the selection against current descriptors and input schemas.
7. Persist the intended call and execute it through MCP.
8. Persist the result and update memory from its observation.
9. Evaluate completion and repeated non-progress; continue or terminate.

```text
while run is active and within budgets:
    observation = latest_valid_observation_or_observe()
    goal_status = world.evaluate_goal(goal)
    if goal_status is achieved:
        finish succeeded

    decision = llm_proxy_client.choose(goal, observation, memory, actions)
    validate decision against discovered actions
    result = execute exactly one action
    persist and incorporate result
    apply repetition and failure policy
```

All terminal paths produce a durable run summary. If a budget is exhausted immediately after an action, perform one bounded final goal check before assigning `budget_exceeded`; reserve capacity for that check.

### Decision output

- Active root goal and selected subgoal, if any.
- Discovered action ID.
- Schema-valid arguments.
- Short action justification grounded in observed facts.
- Expected observable outcome.

Record concise decision explanations, not private chain-of-thought. The admin panel exposes the complete recorded decision and interaction history: goals, tentative subgoals/plans, observed facts, selected actions, explanations, expected outcomes, actual results, and revisions. It must not claim to display the model's internal reasoning or fabricate reasoning that the model did not provide.

### Decision policy

- Prefer actions that advance the goal or reduce relevant uncertainty.
- Explore plausible leads before declaring the goal blocked.
- Treat plans as tentative; reassess after each result.
- Do not invent objects, commands, state changes, or successful outcomes.
- Account for observed body constraints when choosing actions; acquiring an item may require freeing capacity first.
- Account for enabled needs and their observed effects; pursue recovery when needed to continue toward the root goal, then reassess that goal.
- Do not repeat a failed action under unchanged relevant conditions.
- A dropped item or an exploratory action is allowed; the agent need not follow an optimal sequence.

## 8. Memory, Controls, and Limits

### Post-attempt completion review

Every ended attempt must receive a separate pass/fail assessment, regardless of whether execution ended through verified completion, a stall, cancellation, an error or a budget limit. Keep execution status distinct from graded success. After ending, obtain a fresh actor-visible observation and authoritative goal verification, then review the resulting factual story through the configured LLM proxy. Freeze the exact submitted story and display it in completed/archived prose. Assessment reasons must cite supplied actor-scoped story paragraphs or the final verification event; reject invented or other-actor evidence references.

A failed authoritative condition is a hard veto: an unplated sandwich fails even if the story or reviewer claims completion. Record world-check failures and supported model failure findings as reasons; do not save a contradicted model success claim as a lesson. A model review may fail independently of execution status. `successful` is true/false only after a completed pass/fail review; pending/error reviews remain ungraded. Explicitly log, persist and show reviewer/network/evidence errors, with manual reassessment rather than fabricated fallback grades.

Persist assessment state, reviewed story, fresh verification, model review, reasons/evidence, model calls and elapsed review time in run/summary JSON and immutable assessment events. Assessment calls are separate from execution budgets; freeze task elapsed time on ending. Batch evaluations must wait for assessment and count only passing reviews with achieved final verification within action/time thresholds. New attempts freeze their scenario definition so later verification cannot silently use changed rules; legacy-current-definition checks require a visible warning.

Save failed assessments in scenario-specific application JSON. Reassessment replaces an attempt's feedback, and a superseding pass removes its failure entry. Load up to 12 previous failure reports for future attempts of the same scenario, explicitly labelled prior experience/hypotheses, never current discoveries. Load them at the beginning for known tasks; defer them until assignment discovery for note-driven tasks. An attempt ending before assignment discovery is graded against that discovery check, not undisclosed task requirements. Preserve discovery-only factual memory: lessons do not populate known entities, current visibility, visited locations or action targets. Current observations and schemas override stale prior experience. Do not preload hidden solutions or expose privileged world snapshots to the reviewer or NPC.

### NPC memory

Keep current observed facts, the actor's observed `self` state and constraints, inventory, known exits, discoveries, recent action results, and goal/subgoal status. Memory is actor-scoped even when several actors share a world. Clearly distinguish observations from hypotheses.

Populate factual entity memory, discovery IDs and visited-room memory solely from actor-visible observations, starting with the initial observation. Retain these discoveries when they leave the current view, but never prepopulate them from scenario JSON or privileged snapshots. Tentative model plans are not evidence that an object exists.

Build proxy requests from non-mutating short-term projections, leaving complete
saved memories and events intact. NPC decisions and private questions include
the latest six actions, conversation exchanges, incoming messages and village
news entries, and three previous-attempt failure reports. Preserve current goals,
full advertised action schemas, physical constraints, known discoveries, maps and
active plans. Supply the character profile once rather than repeating it in memory.
Progress reviews keep all teammates' latest observed state and up to 40 compact
evidence records with their original IDs. Do not repeat full snapshots or action
schemas in those records; retain authoritative outcomes, verification evidence
and timed-work state. Deduplicate public rules and recent notices. Mark result
explanations shortened to 600 characters and explicitly prohibit treating omitted
history as proof of absence or futility. Compaction requires no model calls and
must not change citation validation, saved history or simulation scheduling.

Longer transcripts may be summarized for model context, but the complete transcript remains persisted. Summaries must preserve unresolved failures and supporting evidence.

### Simulator controls

- `start`: create a fresh isolated world session and assign a goal.
- `step`: execute at most one decision/action cycle.
- `run`: continue until paused or terminal.
- `pause`: stop scheduling new cycles; retain any in-flight outcome.
- `resume`: continue the same session and goal.
- `reset`: explicitly create a fresh world and clear NPC episode memory.

Only one action may be in flight for an actor. MVP pause/resume is within the running simulator process; crash recovery is not implied.

### Proposed default budgets

- 30 action attempts per episode, including rejected attempts.
- 40 model calls, including decision repairs.
- 120 seconds total wall time.
- 15-second timeout per model or MCP call, capped by remaining time.
- One schema-repair attempt per invalid model decision.
- At most two retries for safe reads or explicitly idempotent requests.
- Three repetitions of an equivalent action/argument/outcome pattern without new information trigger replanning.
- A second such repetition window terminates as `stalled`.

Terminal run statuses are `succeeded`, `blocked`, `stalled`, `budget_exceeded`, `cancelled`, and `error`. `blocked` reflects the agent's evidence-based assessment, not proof that a puzzle is impossible.

`POST /api/budget/extend` accepts `run_id` and nonnegative additions
`add_actions`, `add_model_calls`, and `add_seconds`, with at least one positive
addition. It extends only the identified current adventure's frozen limits.
Active runs retain their status. A `budget_exceeded` run reopens paused only if
all exhausted limits receive enough capacity; no automatic continuation or model
call is permitted. Preserve world, elapsed/game time, actor bindings, memory,
history, and pending turns/reviews. Invalidate the obsolete completion grade and
its failure feedback, retaining immutable review events and prior review-call
counts. Reject other terminal states and stale run IDs. A saved budget-exhausted
built-in local world may reconnect using its existing state and frozen scenario,
without recreating either. Player/Admin provide the extension form; World remains
read-only and links to it.

Daily-life village completion must include an evening meal for every resident,
everyone physically at their configured home and asleep, and no committed work.
Expanded rules also require the horse released at its stable, unhitched,
automatic routes stopped, renewable resource production and actual manure/tea
use. Repairs and daytime trade alone cannot terminate the adventure. Verification
exports this complete checklist to the story reviewer, including historical
service evidence; absence from the prose is not proof of failure. Use the most
recent evening after the scenario's starting day, including after midnight, so a
later complete day can recover from a missed earlier meal.

### Opt-in living-village systems

Existing village scenarios default to their original meal, production and
completion rules. A separate generated living scenario enables:

- Supplier orders with acceptance, partial fulfillment, cancellation, deadlines,
  freshness requirements, affordability commitments and exactly-once payments.
  Order creation requires an in-person requirements conversation with the actual
  available supplier at the buyer's location; neither demand planning nor ordinary
  dialogue creates a remote commitment. Upstream input orders follow the same rule.
  NPC order observations are limited to their own buying/selling commitments;
  privileged monitoring retains all orders.
- Demand-limited production including committed batches, protected raw-material
  reserves, consolidated real deliveries and explicit cargo returns.
- Breakfast after waking and supper at 19:00, independently tracked and charged.
  Subsidies must name a real finite funder; stock is consumed only for funded meals.
- Persistent interests, timed leisure, relationships, personal projects, shared
  band rehearsals/performances and optional evidence-verified sidequests.
- Bounded deterministic village events and named temporary visitors with real
  presence, planned physical routes, finite personal merchant stock/money/capacity
  and protected, fresh surplus exports. Merchants ask available local residents
  about trade, but only explicit funded transactions transfer goods. Visitors at
  the tavern may buy a real meal during breakfast or supper service. Travellers
  without a planned meal stop walk to the tavern during meal service and resume
  their visit after eating.
- Typed monitoring projections for orders, inventory, accounts, projects, quests,
  events, activity durations, time use and historical visitor Gantt rows.

Activities occupy time without repeated model decisions and remain interruptible
for essential commitments. Events are locally discovered; configuration does not
grant all residents private or unwitnessed knowledge. Optional leisure, quests
and visitor departures do not become resident victory conditions. Scenario policy
can replace forced resource-production checkboxes with verified reserve readiness
and can make tea/manure use optional; homecoming, sleep and animal care remain.

Privileged `world_configure_village` and `POST /api/village/configure` validate
configuration sections, reject stale revisions and conflicting request IDs, and
persist effective settings without rewriting a frozen scenario. The operator API
requires a paused connected adventure and matching run ID. A DM progress review
may propose configuration only with explicit `configure_village` capability.
Templates are restricted to supported effects and evidence conditions, never
arbitrary executable code or narrative-only success.

`POST /api/adventure/reopen` accepts the current `run_id`. Only a succeeded village
attempt with a completed failing review and a fresh authoritative
`full_village_day` verification still marked active may reopen. Reconnect only
the saved built-in local world and frozen scenario. Reopen paused, retain the
pending actor and all committed progress/budgets/timing, archive previous review
events and charges, and remove obsolete current-attempt failure feedback.
Never resume automatically or call a model during reopening.

The same endpoint may recover a local village MCP response timeout only at a
confirmed free-action boundary: every action request has a matching result, and
the persisted free-turn actor matches the latest free action's actor. Reject
uncertain mutations or other interrupted states. Optional `until_midnight: true`
records the next midnight as a stop boundary without changing budgets. Keep
simulating after early goal completion, process intervening night events, and
pause exactly at that boundary rather than scheduling an actor the next morning.

Recovery may also preserve an already persisted next actor after a completed
non-free action, provided every request is confirmed and the controller has not
switched to that new actor. Do not schedule again or repeat the completed action.

Live inspection caches request-free world snapshots by file identity, size and
modification time. The UI displays 100 technical events initially and loads raw
JSON only on demand. `/api/events?compact=true` and `/api/stream?compact=true`
omit model request messages without changing saved events. Original events remain
available at `/api/events/{sequence}?run_id=...` and in full exports. SSE accepts
`run_id` and `after` as an initial cursor, honors Last-Event-ID on reconnect, yields
during history replay, and sends snapshots only when their content changes.

An ambiguous mutation timeout is retried only with the same request ID when deduplication is supported. Otherwise stop with `error` and report the uncertain outcome rather than risk applying the action twice.

## 9. Observability and Trust Boundaries

Persist machine-readable JSON event files, an ordered JSON transcript manifest, and a concise JSON terminal summary containing:

- Run, session, proxy/model routing, scenario, contract, and configuration identifiers, plus the resolved model when reported by the proxy.
- Goal and authoritative final verification.
- Observations, action descriptors, selected actions, and brief justifications.
- Request IDs, results, error classifications, timestamps, and latency.
- Model-call and action counts; token usage when the provider supplies it.
- Terminal status and stopping reason.

The CLI shows the current location, observed body constraints, known inventory, active goal, last action/result, and elapsed steps.

World narrative, object text, and tool output are untrusted data, not instructions. They cannot override the goal or authorize additional infrastructure tools. The NPC can access only the configured world's approved tool surface, not shell, filesystem, or unrelated MCP tools.

Credentials remain outside transcripts and source code. Sessions are isolated and reset access remains outside the NPC's tool allowlist.

### JSON-file persistence

All persistent application-owned data is stored as readable UTF-8 JSON documents, including configuration, scenario definitions, authoritative world state, NPC memory, goals, events, and run summaries. No SQL/NoSQL database, binary state store, or JSONL format is required. Secrets remain externally supplied rather than persisted as application data.

Proposed layout under a configurable data directory:

```text
data\
  config.json
  scenarios\
    escape-room.json
  runs\
    <run-id>\
      run.json
      goal.json
      npc-memory.json
      world-state.json
      transcript.json
      summary.json
      events\
        000001.json
        000002.json
```

- Each JSON document includes a schema version; run-scoped data includes world/actor/run/session IDs and applicable revisions.
- Store each shared world's authoritative state once in a world-owned JSON document, including its collection of actor `self` objects. The per-run `world-state.json` layout above applies to the single-actor demo; shared-world mode uses `data\worlds\<world-id>\world-state.json` rather than duplicating authoritative state across actor runs.
- Scenario JSON defines entities, initial body/carrying settings, needs definitions and per-actor enabled settings, and goal parameters. The world engine enforces transition rules; scenarios are not exposed directly to the NPC.
- Each immutable event file contains one event. `transcript.json` indexes their stable IDs and sequence numbers; the UI can export a combined JSON transcript.
- Use one owning writer per data domain: the world server writes authoritative world state; the simulator writes run state, NPC memory, and events. The UI never accesses or writes files directly.
- Mutations are serialized. Publish an event only after its JSON file is durable; write replaceable JSON documents through same-directory temporary files and atomic replacement.
- Do not assume a collection of JSON files updates atomically. Persist mutation request IDs/results with world transitions so deduplication survives state persistence. Events are the source for rebuilding the transcript index; snapshots carry revisions for reconciliation.
- A world transition committed before the simulator records its outcome is reconciled using the stored request ID/result, not by executing a new mutation.
- Validate documents against versioned schemas when loading. Missing required files, corrupt JSON, and unsupported versions produce explicit errors, not silent defaults or automatic resets.
- Configuration defaults may initialize a new data directory explicitly. Reset in the single-actor demo creates a new run/session and preserves previous run files. In shared-world mode, starting a new actor run does not reset the shared world or other actors.
- Persist checkpoints after each completed cycle. On process restart, expose prior runs for inspection and mark uncertain incomplete episodes as interrupted pending reconciliation. Explicitly saved safe paused checkpoints may reconnect paused; automatic crash-resume remains outside MVP.
- Provide named episode save points containing the frozen scenario/cast, world,
  every NPC's private memory/observation, complete transcript, usage counters,
  active commitments and scheduler state. Serialize at a paused safe boundary
  without model calls. Loading creates a distinct episode/world/session copy,
  remaps transcript evidence identities consistently, and preserves the original
  history and immutable save point. Saved active episodes load paused; completed
  or uncertain-action saves are questions-only without automatic replay/regrading.
  Loaded-copy questions use private saved knowledge and separately counted
  follow-up calls until simulation resumes; never reset budgets or advance time.
  Reject unsupported adapters, missing/corrupt data and stale load requests;
  failed restoration must preserve/reconnect the original world.
- Keep hidden world files accessible only to trusted backend/admin components. Do not serve the data directory as static web content.

## 10. World Replacement

The same NPC controller must support:

- Added verbs such as `push`, `read`, `talk`, `combine`, or `use`.
- New argument shapes, entity types, rooms, inventory rules, and prerequisites.
- Different bodies and changing body capabilities or carrying constraints.
- New needs, different progression/recovery rules, and configurable need-driven constraints.
- Different action IDs and descriptions.
- Capability changes during an episode.

A replacement world must implement the normalized contract or supply a tested adapter for observation, action dispatch, and goal verification. MCP transport compatibility alone is insufficient.

Capability changes are detected through supported MCP notifications or observation-provided capability revisions; descriptors are refreshed before the next decision. Removed actions cannot be executed from a cached plan.

No planner branch may mention the initial box, key, door IDs, or escape sequence. Scenario-specific rules and goal evaluators belong to the world or its adapter.

### Admin world inspection compatibility

Each world or adapter must provide a privileged snapshot operation for the admin control plane, separate from actor-visible observation. Snapshots include a revision and authoritative state sufficient to inspect entities, containment, body state, and puzzle progress. This operation is never exposed to the NPC.

The panel renders arbitrary world-defined properties using a generic structured-data inspector, with optional richer views. Replacing a world must not require hard-coded UI support for its verbs or entity types. A missing or incompatible privileged inspection capability fails admin-enabled startup explicitly; actor observations must not be presented as a complete world snapshot.

## 11. Admin Panel

### Episode overview and controls

- Implement the browser UI in TypeScript. Its typed API models follow the versioned JSON contracts, with runtime validation at API boundaries.
- The proposed Python admin API serves JSON snapshots/history and streamed JSON event payloads. Framework selection remains open.
- Show run/session IDs, connection status, proxy/model routing, root goal, run status, budgets, and stopping reason.
- Provide start, step, run, pause, resume, and reset using the same controller semantics as the CLI.
- Require confirmation before resetting an active episode.
- Serialize control requests with action execution so simultaneous CLI/panel requests cannot start duplicate cycles or corrupt an episode.
- Show control failures explicitly and disable actions that do not apply to the current run status.

### World state view

- Show all world entities and their properties, containment relationships, and each actor's `self`, including location, inventory, body capabilities, and carrying constraints.
- Show each actor's needs, enabled flags, levels/scales, and active effects. Provide independent food/sleep toggles and generic toggles for other supported needs.
- Toggle failures are explicit. The panel waits for the authoritative result rather than presenting an unconfirmed toggle as applied.
- Provide actor selection for inspecting separate goals, observed views, and timelines. In the initial demo, the selector contains one actor.
- For the initial puzzle, show door lock/open state, box open state, key location, NPC body/inventory, and escape status.
- Clearly label privileged hidden facts, such as the key inside the unopened box.
- Provide a separate actor-visible observation view so the administrator can compare what is true with what the NPC knows.
- Display snapshot revision and freshness; never imply that a stale snapshot is current.
- Admin inspection is read-only in MVP except for the explicit needs-enable controls and existing episode controls. Arbitrary state/need-level editing is out of scope.

### Decision and interaction timeline

Show every recorded cycle in order, including:

1. Actor-visible observation and available action descriptors.
2. Active goal, tentative subgoals, and any explicit plan updates.
3. Proxy request context, excluding secrets, and the returned decision or error.
4. Short decision justification and expected observable outcome.
5. Proposed action, validation outcome, dispatched MCP request, and request ID.
6. Actual response, resulting observation, and authoritative state changes when available.
7. Goal verification, retries, errors, repetition detection, and stopping events.

State changes are based on authoritative snapshots/events, not model claims. Explicit plan updates are optional decision fields; their absence is displayed honestly rather than reconstructed as a supposed thought process.

Allow expanding structured requests/responses, filtering by cycle/event type, and exporting the persisted transcript. Distinguish model proposals, executed actions, and confirmed effects visually.

### First-person story

Provide a separate chronological narrative above the technical log: observed surroundings, attempted actions, and confirmed results. For example: "I try opening the door. The door is locked." Show concise recorded decision explanations optionally, not invented internal thoughts.

Add a third dashboard column containing a continuous, book-like narrative. Integrate newly observed descriptions and carrying constraints with recorded decision explanations, dispatched attempts and confirmed results. Expected outcomes and undispatched proposals must not appear as accomplishments. Derive this prose from persisted actor-visible events without extra model calls, render it safely, preserve the reader's scroll position, and use the same projection in episode archives. Stack the reading column on narrower screens.

Derive the story from actor-visible persisted events, not privileged world state or additional LLM calls. Hidden items appear only upon discovery. Invalid proposals are not narrated as executed attempts; failures and breakage are reported honestly. Replay must not duplicate entries, and archived runs use the same story renderer.

### Live updates and consistency

- Serve initial snapshots/history through the admin API and stream ordered run events through Server-Sent Events or an equivalent reconnectable mechanism.
- Give events stable IDs and per-run sequence numbers. Reconnection retrieves missed persisted events and deduplicates replayed events.
- Correlate events and snapshots by session, cycle, and revision. A reset starts a new session; late events cannot overwrite the new session's view.
- While the panel is connected, publish a completed cycle's events and latest snapshot within one second after the simulator records them, excluding network outages.
- The simulator persists events before publishing them. Panel disconnection does not stop or change the NPC loop.
- Clearly show disconnected, stale, and partially loaded states. Do not silently replace failed loads with empty data.

### Access and data handling

- Bind the MVP admin service to loopback by default. Non-loopback exposure requires explicit configuration and authenticated access.
- Do not expose administrative inspection or control endpoints through the NPC tool allowlist.
- Redact credentials, authorization headers, and other secrets from stored and displayed proxy/MCP exchanges.
- Render world/model text as untrusted text; do not execute embedded HTML or scripts.
- Downloads contain the same redacted data available in the transcript.

## 12. Acceptance Criteria

### Deterministic engine and contract tests

1. Initial observations do not disclose the key or hidden world state.
2. Opening the box reveals the key; taking and dropping it update ownership correctly.
3. Unlocking without the carried matching key fails without mutation.
4. Opening the locked door and crossing a closed door fail.
5. Unlocking, opening, and crossing are distinct transitions.
6. Goal verification succeeds only after the NPC crosses the exit.
7. Invalid targets, malformed arguments, stale observations, and duplicate request IDs behave as specified.
8. Every advertised MVP verb has successful and applicable rejection/no-change tests.
9. Reset restores the exact initial state and clears episode memory.
10. Separate sessions cannot see or modify each other's state.
11. Taking an item beyond body capacity fails atomically with `CAPACITY_EXCEEDED`; dropping a carried item frees capacity.
12. An action requiring an unavailable body capability is rejected without mutation, even if submitted from an older descriptor.
13. Body changes are reflected in the actor-visible view and contextual actions; requests cannot impersonate another body.
14. Two actor bindings in one world return distinct `self` objects and isolated inventory/observations; acquiring an item updates shared ownership without giving it to both actors.
15. Competing takes of the same key result in exactly one successful transfer; stale or rejected requests leave the losing actor's inventory unchanged.
16. One actor escaping does not mark another actor's goal achieved, and new actor runs do not reset existing shared state.
17. Disabled needs neither progress nor restrict actions, and the original escape scenario remains solvable unchanged.
18. Enabled needs progress exactly on configured actor-local state-changing actions; reads, rejections, retries of deduplicated requests, and other actors' actions do not advance them.
19. Need thresholds enforce configured effects atomically; a discovered recovery action changes the relevant need and restores eligibility as defined by world rules.
20. Toggling a need persists its level/settings, updates descriptors, records an event, and affects only the selected actor without retroactive progression.

### Agent integration tests

1. The model receives the goal and visible observations only, not the solution or hidden state.
2. In 20 fresh episodes using a recorded fixed model/configuration, at least 18 end in world-verified success within 30 action attempts and 120 seconds.
3. Successful runs show discovery of the key before taking it; no exact action sequence is required.
4. A failed exploratory action can lead to a revised decision.
5. A deliberately repeating model terminates under the repetition/budget policy.
6. Malformed model decisions never reach the world as unchecked calls.
7. Tool failures and ambiguous outcomes never produce false success.
8. Pause schedules no further actions, and resume preserves state.
9. Each terminal episode produces a parseable transcript and summary.
10. All real-model decisions and repair requests pass through the LLM proxy; no direct provider calls occur.
11. Proxy authentication failures, rate limits, timeouts, and malformed responses follow the bounded failure policy and cannot trigger a direct-provider fallback or false success.
12. A proxy-client contract test verifies request/response mapping against the confirmed proxy protocol; unit tests use a fake proxy without provider credentials.
13. In a carrying-constraint fixture, the NPC observes a full inventory and frees capacity before acquiring a required item, then achieves the goal without bypassing world rules.
14. In a needs-enabled fixture with advertised recovery actions, the NPC addresses a blocking need and then achieves its root goal using unchanged planner code; it cannot disable needs itself.

### Extensibility test

Replace the server with a second contract-compatible fixture featuring renamed entities, a different exit puzzle, and at least one new action with a new argument schema. Reuse the controller and planner unchanged; change only world connection/scenario configuration and a compatible goal.

The agent must discover and use the new action and achieve authoritative success in at least 9 of 10 episodes within the same budgets. This is a required demonstration, not a claim that every future world is solvable.

### Admin panel tests

1. The initial authoritative view shows the hidden key in the box, while the actor view and model request omit it until discovered.
2. Each completed cycle appears in the timeline with its observation, decision explanation, action/result, and verification events; no private-reasoning fields are required or invented.
3. The world view reflects opening the box, taking/dropping the key, unlocking/opening the door, and crossing the exit using authoritative revisions.
4. Fake-event tests verify publication within one second of persistence and recovery without missing or duplicate timeline entries after disconnection.
5. Start/step/run/pause/resume/reset work consistently from both interfaces; concurrent control requests do not cause extra actions.
6. Reset requires confirmation and late events from the previous session cannot alter the current world view.
7. Hidden-state inspection and admin controls are absent from every NPC/proxy tool definition and prompt.
8. Credentials are redacted from the panel and exports, and script-like world/model text renders inertly.
9. The replacement-world fixture renders through the generic state inspector without panel code changes.
10. Panel disconnects and inspection failures are visible and do not fabricate world state or alter the agent's behavior.
11. Per-actor needs toggles display confirmed settings and effects, survive state reload, and cannot race an in-flight world transition or affect a different actor.

### Persistence and implementation tests

1. The UI passes its TypeScript type check/build and never requires direct filesystem or model-proxy access.
2. Every persisted application data file parses as a JSON document; no database is required to run or inspect an episode.
3. Round-trip tests preserve world state, body constraints, inventory, goals, and NPC memory.
4. Atomic-write failure tests leave either the previous valid document or the new valid document, never a truncated live file.
5. Restart/reconciliation tests rebuild a stale transcript index from immutable event files and do not reapply a committed mutation.
6. Corrupt or unsupported-version data is reported explicitly without destroying prior run files.

## 13. Implementation Deliverables

1. Deterministic world engine with the initial JSON-defined scenario, proposed in Python.
2. MCP world server, proposed in Python, and versioned JSON contract schemas.
3. MCP client/adapter with schema validation and session handling.
4. Goal-driven NPC controller and configurable LLM proxy client for decision-model access.
5. CLI episode controls and persistent transcripts.
6. Unit, contract, failure-path, and agent evaluation tests.
7. Second-world extensibility fixture.
8. Documentation for setup, run configuration, goal verification, and implementing a compatible replacement world.
9. TypeScript web admin panel, proposed Python privileged admin API, world snapshot integration, and persistent live event stream.
10. Panel interaction, event replay, hidden-state isolation, and rendering tests.
11. JSON-file persistence with atomic writes, schema validation, request reconciliation, and persistence tests.

Implement in this order: world rules and tests; MCP contract and server; controller with a deterministic fake decision model; admin API and panel; real model integration; escape evaluations; replacement-world and panel evaluations.

This document specifies the proposed system. It does not provision services or implement the agent.

## 14. Roadmap: Colossal Cave Adventure

### Progression

1. Complete the one-room escape demo and its admin/persistence tests.
2. Demonstrate an interchangeable second world with new verbs and argument schemas.
3. Add multi-room navigation, persistent discovered maps, longer-horizon goal decomposition, and scalable context management.
4. Integrate a selected Colossal Cave Adventure implementation behind the world MCP contract.
5. Evaluate the agent against that edition's authoritative completion condition.

### Edition and completion contract

Before integration, select the exact edition/implementation, record its provenance and license, and confirm permitted use. Obtain game content from an appropriately licensed source rather than assuming every edition has identical content or permissions.

Define what "complete" means for the selected edition: for example, reaching its winning endgame, achieving a specified score, or attaining its maximum score. These are not interchangeable. Record the chosen completion rule, scenario/version, randomness settings, and initial conditions in JSON configuration.

The world evaluates completion from authoritative game state. The model's claim of victory, reaching an exit, or collecting some treasures is not sufficient unless it meets the selected completion rule.

### Required capabilities

- Expose locations, visible objects, applicable commands, and command argument schemas without leaking undiscovered areas or puzzle solutions.
- Support that edition's parser semantics through action descriptions/adapters rather than adding game-specific branches to the NPC controller.
- Maintain an actor-local discovered map, object knowledge, unresolved leads, and revisable subgoals. Unknown connections remain unknown until observed.
- Keep complete events in JSON files while loading bounded recent context and relevant observed memories for each proxy request.
- Handle edition-specific resources, hazards, death, score, and irreversible choices as world rules with explicit observations and outcomes.
- Keep optional simulator needs disabled by default unless deliberately included in the evaluation; distinguish those needs from the game's native resource rules.
- Scale run budgets beyond the demo's 30-action/120-second defaults through recorded configuration. Select measurable success-rate and cost/action thresholds after establishing a baseline for the chosen edition.
- If the selected engine supports only one player, use one NPC per world instance. Do not imply shared-world multiplayer without implementing and testing the required semantics.
- Extend checkpoint reconciliation to verified long-running resume before relying on it for full-game episodes.

### Evaluation principles

Use the same controller and planner with world/adapter and configuration changes only. No walkthrough, hidden game state, or scripted solution may enter the NPC's decision context. The privileged admin panel can inspect full game state but remains isolated from the agent.

Evaluate complete episodes and report authoritative outcome, score where applicable, actions, proxy calls, elapsed time, stopping reason, and reproducible configuration. A full-game milestone is complete only when the selected victory condition and its agreed evaluation thresholds are met, not merely when the agent can explore the cave.
