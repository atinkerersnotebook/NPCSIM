import assert from "node:assert/strict";
import { test } from "node:test";
import { evidenceReferences, explainEvidence } from "../src/evidence.ts";
import type { RunEvent } from "../src/contracts";

function event(id: string, kind: string, payload: unknown): RunEvent {
  return {
    schema_version: "1.0", id, kind, payload, sequence: 1, run_id: "run", session_id: "session",
    actor_id: "thief", cycle: 3, timestamp: "2026-11-05T13:00:00Z"
  };
}

test("world action evidence explains the actual result, actor and cycle", () => {
  const source = [event("run:1", "action_result", {
    outcome: "REJECTED", explanation: "Requires lockpicking; your body does not have this skill."
  })];
  const [evidence] = explainEvidence(["run:1"], source);
  assert.equal(evidence.explanation, "Requires lockpicking; your body does not have this skill.");
  assert.equal(evidence.actorId, "thief");
  assert.equal(evidence.cycle, 3);
  assert.equal(evidence.id, "run:1");
  assert.match(evidence.title, /World action result/);
});

test("defeat evidence explains missing skills, mandatory work and the team's actual abilities", () => {
  const source = [event("run:2", "verification", {
    status: "blocked", reason: "The team admits defeat because nobody can pick the lock.",
    evidence: {
      missing_required_skills: ["lockpicking", "trap_disarming"],
      impossible_challenges: [{ id: "pick_gallery_lock", capability: "lockpicking", description: "Pick the gallery lock." }],
      team_capabilities: { thief: ["move", "rigging", "repair"], mage: ["arcane_magic"] }
    }
  })];
  const [evidence] = explainEvidence(["run:2"], source);
  assert.equal(evidence.explanation, "The team admits defeat because nobody can pick the lock.");
  assert.ok(evidence.details.includes("Missing required skills: lockpicking, trap disarming."));
  assert.ok(evidence.details.some(detail => detail.includes("Pick the gallery lock.") && detail.includes("requires lockpicking")));
  assert.ok(evidence.details.includes("thief has: move, rigging, repair."));
  assert.ok(evidence.details.includes("mage has: arcane magic."));
});

test("successful final checks explain per-agent arrival and completed cooperative work", () => {
  const source = [event("run:3", "completion_verification", {
    status: "achieved", reason: "", evidence: {
      agents: { thief: { status: "achieved", reason: "", evidence: { location_id: "courtyard" } } },
      cooperative_work: { pick_gallery_lock: "thief", activate_runes: "mage" },
      checks: { prepared_item: true, served_on_target: false }
    }
  })];
  const [evidence] = explainEvidence(["run:3"], source);
  assert.match(evidence.explanation, /required goal conditions were satisfied/);
  assert.ok(evidence.details.includes("thief: achieved at courtyard."));
  assert.ok(evidence.details.includes("pick gallery lock: completed by thief."));
  assert.ok(evidence.details.includes("activate runes: completed by mage."));
  assert.ok(evidence.details.includes("prepared item: satisfied."));
  assert.ok(evidence.details.includes("served on target: not satisfied."));
});

test("completion citations prefer the exact reviewed story rather than a raw attempted action", () => {
  const source = [event("run:4", "action_request", { action_id: "open" })];
  const [evidence] = explainEvidence(["run:4"], source, [{ id: "run:4", text: "Fox opened the supply box." }]);
  assert.equal(evidence.title, "Recorded story evidence");
  assert.equal(evidence.explanation, "Fox opened the supply box.");
  const [attempt] = explainEvidence(["run:4"], source);
  assert.match(attempt.explanation, /attempt, not a confirmed outcome/);
});

test("missing and foreign-run references are explicitly unavailable, never invented", () => {
  const archive = [event("archive:1", "action_result", { outcome: "APPLIED", explanation: "Mage opened the exit." })];
  const [missing] = explainEvidence(["live:1"], archive);
  assert.equal(missing.title, "Evidence record unavailable");
  assert.match(missing.explanation, /not present in the loaded episode/);
  assert.equal(missing.actorId, undefined);
  const [savedStory] = explainEvidence(["legacy:1"], [], [{ id: "legacy:1", text: "The recorded story remains available." }]);
  assert.equal(savedStory.explanation, "The recorded story remains available.");
});

test("nested citations across rulings and assessments resolve once and preserve reference order", () => {
  const payload = {
    evidence_ids: ["run:1", "run:2"], findings: [{ evidence_ids: ["run:2", "run:3"] }],
    story_review: { findings: [{ evidence_ids: ["run:4"] }] }, extra: { evidence_id: "run:5" },
    request_id: "not-an-evidence-reference"
  };
  assert.deepEqual(evidenceReferences(payload), ["run:1", "run:2", "run:3", "run:4", "run:5"]);
});

test("schema validation evidence never pretends that an action executed", () => {
  const source = [
    event("run:1", "validation", { valid: true, action_id: "pick_gallery_lock" }),
    event("run:2", "validation", { valid: false, error: "Target is not advertised." }),
    event("run:3", "dungeon_master_validation", { valid: false })
  ];
  assert.match(explainEvidence(["run:1"], source)[0].explanation, /not proof of execution/);
  assert.equal(explainEvidence(["run:2"], source)[0].explanation, "Target is not advertised.");
  assert.match(explainEvidence(["run:3"], source)[0].explanation, /failed validation/);
});

test("observations, supervisor reasons and unknown events retain only recorded explanations", () => {
  const source = [
    event("run:1", "observation", { narrative: "The gallery door is still shut." }),
    event("run:2", "dungeon_master_review", { findings: [{ reason: "Repeated attempts changed nothing." }] }),
    event("run:3", "future_event", {})
  ];
  assert.equal(explainEvidence(["run:1"], source)[0].explanation, "The gallery door is still shut.");
  assert.equal(explainEvidence(["run:2"], source)[0].explanation, "Repeated attempts changed nothing.");
  assert.match(explainEvidence(["run:3"], source)[0].explanation, /No readable explanation was recorded/);
});

test("display explanations do not rewrite archived evidence or reviewed prose", () => {
  const source = [event("run:1", "verification", { status: "active", evidence: { checks: { exit_reached: false } } })];
  const before = JSON.stringify(source);
  const result = explainEvidence(["run:1", "run:1"], source);
  assert.equal(result.length, 1);
  assert.equal(JSON.stringify(source), before);
  assert.match(result[0].explanation, /not yet been achieved/);
});
