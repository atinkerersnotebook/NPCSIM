import assert from "node:assert/strict";
import { test } from "node:test";
import { effectiveBody, profileText, slotLabel, validateAssignments } from "../src/agent-profiles.ts";
import { agentProfileSchema, runSchema, skillModeSchema, dungeonMasterConfigSchema, dungeonMasterRulingSchema } from "../src/contracts.ts";

const raven = agentProfileSchema.parse({
  schema_version: "1.0", id: "raven", name: "Raven", identity: "A scout.",
  personality: "Observant and witty.", operating_rules: ["Inspect first."],
  constraints: ["Cannot swim."],
  body: { description: "A small bird.", capabilities: ["fly"], capacity: 1 },
  scenario_bodies: {
    "storm-signal": { description: "Light worker.", capabilities: ["fly", "light_work"], capacity: 1 }
  }
});

test("the default single-agent role is displayed as Player without changing slot IDs", () => {
  assert.equal(slotLabel("npc-001"), "Player");
  assert.equal(slotLabel("raven"), "raven");
  assert.equal(slotLabel("lead_builder"), "lead builder");
  assert.doesNotThrow(() => validateAssignments(["npc-001"], { "npc-001": "raven" }, [raven]));
});

test("assignment previews use the character's task-specific body or its default", () => {
  assert.deepEqual(effectiveBody(raven, "storm-signal").capabilities, ["fly", "light_work"]);
  assert.deepEqual(effectiveBody(raven, "escape-room").capabilities, ["fly"]);
});

test("anatomy contracts preserve old bodies and validate independent grip limits", () => {
  assert.equal(raven.body.size, "medium");
  assert.deepEqual(raven.body.grips, ["hand"]);
  assert.equal(raven.body.max_held_items, null);
  const bird = agentProfileSchema.parse({ ...raven, body: {
    ...raven.body, grips: ["beak", "claw"], max_held_items: 1, size: "small"
  } });
  assert.deepEqual(effectiveBody(bird, "storm-signal", "npc_only").grips, ["beak", "claw"]);
  assert.equal(effectiveBody(bird, "storm-signal", "npc_only").max_held_items, 1);
  assert.equal(effectiveBody(bird, "storm-signal", "npc_only").size, "small");
  for (const changes of [{ size: "tiny" }, { grips: ["tentacle"] }, { max_held_items: -1 }, { max_held_items: 1.5 }, { max_held_items: true }]) {
    assert.equal(agentProfileSchema.safeParse({ ...bird, body: { ...bird.body, ...changes } }).success, false);
  }
});

test("live Markdown persona previews retain legacy archive support without granting abilities", () => {
  assert.equal(profileText(raven, "SOUL.md"), raven.personality);
  const profile = agentProfileSchema.parse({ ...raven, documents: {
    "SOUL.md": "# Soul\n\nQuiet, deliberate and thoughtful.\n",
    "AGENTS.md": "# Guidance\n\n## Rules\n\n- Inspect first.\n",
    "people/INDEX.md": "# Relationships\n\nNone authored.\n"
  } });
  assert.equal(profileText(profile, "SOUL.md"), "Quiet, deliberate and thoughtful.");
  assert.equal(profileText(profile, "AGENTS.md"), "Rules\n\n- Inspect first.");
  assert.deepEqual(effectiveBody(profile, "escape-room").capabilities, ["fly"]);
  assert.deepEqual(raven.documents, {});
});

test("Markdown document contracts reject unsupported paths and case aliases", () => {
  for (const documents of [
    { "../outside.md": "No." }, { "people/../../outside.md": "No." },
    { "people\\outside.md": "No." }, { "identity.md": "No." },
    { "people/index.md": "No." }, { "people/Guide.md": "One", "people/guide.md": "Two" }
  ]) {
    assert.equal(agentProfileSchema.safeParse({ ...raven, documents }).success, false);
  }
});

test("NPC-only previews ignore scenario additions and keep the NPC's own carrying limit", () => {
  const profile = {
    ...raven,
    scenario_bodies: { "sealed-vault": { description: "Scenario thief.", capabilities: ["lockpicking"], capacity: 3 } }
  };
  assert.deepEqual(effectiveBody(profile, "sealed-vault").capabilities, ["lockpicking"]);
  const body = effectiveBody(profile, "sealed-vault", "npc_only");
  assert.deepEqual(body.capabilities, ["fly"]);
  assert.equal(body.capacity, 1);
  assert.ok(runSchema.shape.skill_mode.isOptional());
  assert.equal(skillModeSchema.safeParse("npc_only").success, true);
  assert.equal(skillModeSchema.safeParse("invented").success, false);
});

test("every task slot requires a known, distinct profile", () => {
  const badger = { ...raven, id: "badger" };
  assert.doesNotThrow(() => validateAssignments(["a", "b"], { a: "raven", b: "badger" }, [raven, badger]));
  assert.throws(() => validateAssignments(["a", "b"], { a: "raven" }, [raven]), /every task slot/);
  assert.throws(() => validateAssignments(["a"], { a: "unknown" }, [raven]), /every task slot/);
  assert.throws(() => validateAssignments(["a", "b"], { a: "raven", b: "raven" }, [raven]), /different agents/);
});

test("supervisor contracts distinguish AI judgments from world enforcement and reject invented powers", () => {
  const config = {
    schema_version: "1.0", enabled: true, name: "Oracle", identity: "Supervisor",
    personality: "Fair", capabilities: ["observe_team", "review_progress"],
    operating_rules: ["Cite evidence."], constraints: ["Cannot grant skills."]
  };
  assert.equal(dungeonMasterConfigSchema.safeParse(config).success, true);
  assert.equal(dungeonMasterConfigSchema.safeParse({ ...config, capabilities: ["grant_skills"] }).success, false);
  assert.equal(dungeonMasterConfigSchema.safeParse({ ...config, capabilities: ["review_progress"] }).success, false);
  assert.equal(dungeonMasterRulingSchema.parse({
    name: "Oracle", scope: "episode", verdict: "defeat", reason: "Repeated failure",
    evidence_ids: ["run:1"], source: "ai"
  }).source, "ai");
  assert.ok(runSchema.shape.dungeon_master.isOptional());
});

test("profile editor rejects invalid configurations and old archives need no profile fields", () => {
  assert.equal(agentProfileSchema.safeParse({ ...raven, id: "../bad" }).success, false);
  assert.equal(agentProfileSchema.safeParse({ ...raven, body: { ...raven.body, capacity: -1 } }).success, false);
  assert.equal(agentProfileSchema.safeParse({ ...raven, personality: "" }).success, false);
  assert.equal(agentProfileSchema.safeParse({ ...raven, body: { ...raven.body, capacty: 2 } }).success, false);
  assert.ok(runSchema.shape.agent_profiles.isOptional());
  assert.ok(runSchema.shape.agent_assignments.isOptional());
});
