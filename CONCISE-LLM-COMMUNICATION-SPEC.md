# Experimental Concise LLM Communication

**Status:** initial experimental implementation available through the Admin LLM proxy panel. Delta messages, opaque references and provider-dependent two-pass behavior remain out of scope.

## Goal

Add an opt-in development mode that reduces the messages sent to the LLM without changing canonical world state, durable transcripts, action rules or authoritative observations.

The first version is for controlled testing. It must preserve the same decision-relevant facts, not attempt to prove that all natural-language nuance is lossless. When a message cannot be represented safely, the proxy must fall back to the existing unmodified message format and report that fallback explicitly.

## Non-goals

- Do not gzip or otherwise transport-compress messages as a substitute for reducing tokens. The transport may be optimized separately, but the test must measure LLM-visible token usage and prompt bytes.
- Do not summarize, truncate or remove canonical events from persistent history.
- Do not replace personally observed, private NPC state with global or hidden world state.
- Do not enable the mode in existing saved episodes automatically.
- Do not add undocumented abbreviations that the model cannot reliably interpret.
- Do not compress model responses, schemas, credentials or operator configuration in the initial implementation.

## Configuration

Extend proxy configuration with an experimental enum:

```json
{
  "proxy": {
    "message_format": "normal"
  }
}
```

Supported values:

| Value | Behavior |
|---|---|
| `normal` | Existing prompts and JSON payloads; default for all configurations. |
| `compact` | Existing fields and meaning, encoded with deterministic key aliases and compact delimiters. |
| `shorthand` | `compact` plus a documented, deterministic representation for repeated structured context. |
| `fact-shorthand` | `shorthand` plus a labeled, typed projection for explicitly supported memory facts. |

The selection belongs to the episode configuration snapshot. Changing configuration is blocked while an episode is running; saving while paused updates that episode, and saving before a new episode snapshots the setting there. The Admin model/proxy area exposes an **Experimental message compression** control. Request events report the effective mode and byte reduction.

## Safety boundary

Canonical prompt inputs, outputs and complete history remain stored exactly as they are today. Concision is a projection created immediately before the LLM request. The projection must:

1. Operate on a deep copy or immutable serialization of the source.
2. Never mutate observation, memory, review payloads or saved transcripts.
3. Reject hidden-state leakage and retain the actor's existing observation boundary.
4. Include a format marker and schema version in the user message.
5. Include enough information for an LLM to decode every alias without relying on prior requests.
6. Preserve JSON-like values, including arrays, booleans, numbers, `null`, identifiers and exact action schemas.
7. Return the canonical format if a safe projection cannot be produced.

## Representation

Compact mode may shorten known, stable field names with a per-request key dictionary. Repeated dictionary keys must be declared so the model does not have to guess them.

Example canonical input:

```json
{
  "format_version": 1,
  "format": "shorthand",
  "schema": {"act": "actor_id", "loc": "location_id", "av": "available_actions"},
  "state": {
    "act": "arin",
    "loc": "forge",
    "goal": "repair wagon",
    "av": [{"id": "forge", "arguments": {"target": "wagon"}}],
    "constraints": ["use observed tools", "select exactly one action"]
  }
}
```

Long-term-memory facts may use a narrowly defined typed form. For example, a
canonical debt is projected as:

```text
DEBT(debtor=Arin,creditor=Mara,amount=3,currency=silver,source=M-12,day=4,confidence=high)
```

Position alone must never establish a relationship. `Arin|Mara|3 silver` and
`Owes: Arin: Mara: 3 silver` are therefore invalid representations because the
debtor/creditor direction can be misread. Every experimental fact type requires:

- a recognized type name;
- explicitly labeled semantic roles;
- stable units and identifiers;
- provenance linking to canonical memory;
- an optional confidence marker where uncertainty exists;
- an exact decoder mapping supplied in the same request.

Initial eligible fact types are `DEBT`, `OWNS`, `LOCATED`, `KNOWS`, `PROMISED`
and `FEELS_ABOUT`. Unsupported facts remain in normal structured form rather
than being forced into shorthand.

The decoder instruction must state:

- Expand aliases using only the supplied schema.
- Treat omitted optional fields as omitted, not as false or empty.
- Do not infer absent facts.
- Interpret named relation roles literally: for example, `debtor` owes `creditor`.
- Apply the same action and security rules as the normal format.
- Return the existing strict response JSON, not shorthand.

Highly arbitrary prose is not eligible for shorthand. Natural-language descriptions, operator questions and action descriptions remain explicit in the first version. Whitespace, repeated container keys, stable enum labels and duplicated structured metadata are the primary compression targets.

## Eligible request types

Phase 1 applies only to:

1. NPC action selection (`Proxy.context`).
2. Private NPC questions (`Proxy.question_context`).
3. Dungeon Master review (`Proxy.dungeon_master_context`).

`fact-shorthand` is used only for memories passed through these requests. It must
not transform the canonical memory store, diary, transcript, save file or source
event. A receiver may use the compact fact only for the current request and must
retain or retrieve its canonical source ID when exact wording is required.

World-edit proposals, repair-agent prompts and completion assessment stay in normal format until their round-trip equivalence has separate tests.

## Implementation boundary

Add a codec module responsible for projection, validation and size accounting. The existing `prompt_context` filters remain the input to the codec; concise communication does not loosen or replace their current limits.

Expected functions:

```python
def encode_prompt_payload(payload: dict[str, object], mode: str) -> dict[str, object]: ...

def validate_encoded_payload(
    source: dict[str, object], encoded: dict[str, object], mode: str
) -> bool: ...
```

Validation is local and deterministic. It must verify every source value represented by the encoded projection, not merely compare string lengths. The codec should fail loudly on unsupported shapes rather than silently dropping data.

### Additional compression strategies

The current implementation sends standalone requests to an OpenAI-compatible
`/chat/completions` endpoint. Strategies are included only where they can be
implemented at that boundary without assuming hidden provider memory.

| Strategy | Feasibility | Scope |
|---|---|---|
| Relevance-filtered memory | Implementable | Rank canonical memories against the current goal, actor, location, action catalog and recent results; send only the bounded relevant set plus explicit omissions metadata. Retrieval may be deterministic first, with semantic ranking as a later optional experiment. |
| Duplicate/repeated-context removal | Implementable | Deduplicate identical values within one request, introduce request-local references only when the dictionary and reference target are both present, and preserve first occurrence order. |
| Stable instruction separation | Implementable | Separate invariant rules, persona data and changing episode state. Reordering may improve provider caching but cannot omit content from a stateless request. |
| Action-catalog reduction | Implementable with tests | Send all action IDs and concise availability facts, but include full argument schemas for potentially selectable actions only. An unavailable action retains its reason. A compressed catalog must never hide an action that the engine permits. |
| Repeated-event aggregation | Implementable with provenance | Collapse mechanically identical older observations such as unchanged failures into a count, first/last time and source IDs. Keep recent events and any state-changing event unabridged. |
| Prompt-prefix caching | Provider-dependent; optional | Place invariant system content and static dictionaries first so a compatible provider/proxy can cache the prefix. Record cache hits through provider telemetry when available; never assume caching exists. |
| Delta messages | Not implemented | A future protocol may send an episode revision, base hash and changed paths only if the selected proxy contract explicitly retains and authorizes that base. The current standalone endpoint cannot safely assume prior-message state. Each request remains self-contained. |
| Two-pass relevance selection | Not implemented | An extra model call can select relevant fact/action IDs before the decision call, but it adds latency, model calls and failure paths. Implement only if a single-request relevance strategy cannot meet the target, and count both calls in budgets. |
| Server-resolvable opaque references | Not implementable in the current contract | IDs such as `MEMORY-42` are invalid unless the actual model/proxy can resolve them. Do not send references that exist only in local application storage. |

For relevance-filtered memory, omission metadata must state the selection method,
revision, selected source IDs and number of omitted items. Omitting a memory is
not evidence that the fact is false. A deterministic fallback sends the existing
bounded recent-memory window if relevance selection cannot produce or validate
the projection.

Repeated-event aggregation must remain reversible from canonical source IDs.
For example:

```text
REPEATED(code=MISSING_TOOL,count=4,first=14:00,last=14:35,sources=E-41,E-44,E-48,E-53)
```

This is not permission to convert distinct causes, outcomes or changed world
state into one event. Aggregation is limited to records whose engine fields and
semantic outcome are identical apart from timestamp and source ID.

`Proxy._completion` should record request diagnostics separately from model content, including:

- requested mode;
- effective mode after fallback;
- fallback reason, if any;
- normal and sent prompt byte counts;
- normal and sent estimated token counts when available from the proxy tokenizer/reporting path;
- provider-reported input/output/total token usage;
- cached prompt tokens and cache-hit status when exposed by the proxy;
- selected/omitted memory counts and retrieval strategy;
- deduplicated values and aggregated event counts;
- whether response validation succeeded;
- whether the episode required repair/retry validation.

Do not add request payload text to telemetry beyond existing transcript/debug policy.

## Testing criteria

### Unit tests

- Normal mode produces byte-for-byte identical `messages` to current behavior.
- Compact and shorthand modes do not mutate their source observation or memory.
- Every supported structured value round-trips equivalently through the codec.
- Unknown keys and extension fields survive; the codec never drops them by schema omission.
- Exact action IDs, argument schemas, enum restrictions and required fields survive.
- A `DEBT` fact retains debtor, creditor, amount, unit/currency, source and status;
  reversing the parties is a codec failure, not a lossy approximation.
- Unknown fact types and unsupported relation shapes use normal structured form
  and emit an explicit `unencoded_fact` diagnostic.
- Fact shorthand round-trips to the same canonical fact graph; no semantic role or
  provenance is recovered through model inference.
- Relevance selection never crosses the actor's observation boundary or drops a
  direct current-goal fact, current action fact or recent authoritative result.
- Deduplicated request-local references resolve to exactly one source value, and
  invalid or stale references trigger normal-format fallback.
- Repeated-event aggregation contains only semantically identical canonical
  events and retains every source ID.
- A compact action catalog cannot suppress an engine-permitted action or alter
  a required argument schema.
- Private-state boundaries, goal reveal rules and existing prompt-context truncation remain unchanged.
- Ambiguous aliases, invalid schema versions and unsupported value types fail explicitly and trigger normal-format fallback.
- Response parsing remains strict and accepts the existing JSON response schema only.

### Integration tests

- Freeze representative fixture prompts and verify that all eligible request methods send the chosen format.
- Run the same deterministic episode in `normal`, `compact`, `shorthand` and `fact-shorthand` modes, recording actions, refusals, repairs and authoritative world results.
- Compare relevance filtering and event aggregation independently before
  combining them, so regressions can be attributed to a specific strategy.
- Verify that a matched request containing a changed only-one-value state does
  not use a delta unless the configured proxy contract explicitly supports
  retained baselines; the default standalone request must remain self-contained.
- When cache telemetry is available, verify cache hits do not affect prompt
  reconstruction or token-validity accounting.
- Compare telemetry across modes without treating identical actions as proof of complete semantic equivalence.
- Verify saves/reloads retain the episode's frozen mode and old saves default to `normal`.

### Acceptance thresholds

Before broader testing, the implementation must demonstrate:

1. At least 15% fewer LLM-visible prompt bytes on the representative NPC decision fixture for `compact`.
2. At least 25% fewer LLM-visible prompt bytes on the same fixture for `shorthand`.
3. No loss of canonical decision facts detected by codec round-trip validation.
4. No increase in invalid model responses or action-validation retries over an agreed baseline batch.
5. No observed action that violates a fact, schema or private-information boundary present only because of compression.
6. On a matched memory fixture, `fact-shorthand` sends at least 15% fewer bytes
   than `compact` while preserving every relation, qualifier and provenance ID.
7. A behavioral comparison fixture distinguishes "Arin owes Mara 3 silver" from
   "Mara owes Arin 3 silver" and the model does not reverse either relation.
8. Relevance filtering plus deterministic deduplication reduces total input
   tokens by at least 20% on a long-memory fixture without increasing invalid
   decisions, repairs or authoritative goal failures.
9. Any two-pass or delta experiment reports total tokens and calls across the
   complete decision, not savings from one favorable request.

The percentage targets are test fixtures, not universal guarantees. If token savings do not correlate with byte savings, token usage remains the decisive metric.

## Rollout

1. Land codec and disabled configuration parsing.
2. Add codec, round-trip and frozen fixtures.
3. Add deterministic relevance selection, deduplication and safe event aggregation
   as independently selectable experiments.
4. Add opt-in Admin setting and episode snapshot behavior.
5. Run controlled comparisons across each strategy without affecting existing scenarios.
6. Enable prompt-prefix caching only after confirming proxy telemetry/support.
7. Review delta and two-pass protocols only as later experiments with explicit
   retained-state or total-cost evidence.
8. Review invalid-response rate, token savings and authoritative world outcomes.
9. Keep the options experimental until sufficient results show a measurable benefit without unacceptable behavioral drift.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Alias ambiguity | Include a self-contained schema and validate it locally. |
| Omitted optional field interpreted as a negative fact | State explicit semantics and never treat omission as `false`. |
| More invalid model outputs | Strict response parsing, bounded retry policy and normal-format fallback. |
| Behavioral drift | Compare authoritative episode outcomes and validation errors across matched seeds. |
| Compression removes important narrative nuance | Restrict the first version to deterministic structured transformations. |
| Token savings lower than byte savings | Measure provider usage and tokenizer estimates; do not ship on byte count alone. |
| Configuration leaks into an old episode | Snapshot mode at episode start and default missing fields to `normal`. |
| Relevant fact excluded by ranking | Keep direct goal/action/current-result facts mandatory, test boundary cases and fall back to bounded normal context. |
| Distinct failures aggregated incorrectly | Aggregate only exact semantic signatures and retain source IDs for audit. |
| Delta or reference cannot be resolved | Keep requests self-contained by default; require an explicit retained-state contract before enabling those modes. |
| Two-pass selection costs more than it saves | Measure complete decision tokens, calls, latency and retry rate before adoption. |
