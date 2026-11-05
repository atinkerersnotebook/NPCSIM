import assert from "node:assert/strict";
import { test } from "node:test";
import { secondaryConditionSchema, secondaryGoalSchema, secondaryGoalSpecSchema, worldEditProposalSchema } from "../src/contracts.ts";
import { conditionText } from "../src/secondary-goals.ts";
import { explainEvidence } from "../src/evidence.ts";

const entities = { key: { name: "Silver key" }, room: { name: "Courtyard" }, bowl: { name: "Adapted bowl" } };
const actors = { recipient: { name: "Otter" } };

for (const [kind, expected] of [
  ["discovered", "Any assigned NPC has discovered Silver key through observation."],
  ["carrying", "Any assigned NPC is carrying Silver key."],
  ["at_location", "Any assigned NPC is at Silver key."],
  ["read", "Any assigned NPC has read Silver key."],
  ["opened", "Silver key is open."],
  ["unlocked", "Silver key is unlocked."],
] as const) {
  test(`preview explains ${kind} without command JSON`, () => {
    const condition = secondaryConditionSchema.parse({ kind, target: "key" });
    assert.equal(conditionText(condition, entities, actors), expected);
  });
}

test("team quantifiers and recipient deliveries have explicit human-readable conditions", () => {
  assert.equal(conditionText(secondaryConditionSchema.parse({
    kind: "discovered", target: "key", quantifier: "all"
  }), entities, actors), "Every assigned NPC has discovered Silver key through observation.");
  assert.equal(conditionText(secondaryConditionSchema.parse({
    kind: "carrying", target: "bowl", actor_id: "recipient"
  }), entities, actors), "Otter is carrying Adapted bowl.");
  assert.equal(conditionText(secondaryConditionSchema.parse({
    kind: "item_at", target: "bowl", location_id: "room"
  }), entities, actors), "Adapted bowl is placed at Courtyard.");
});

test("goal-only previews preserve assignments and conditions without inventing world additions", () => {
  const proposal = worldEditProposalSchema.parse({
    id: "proposal", revision: "obs-1", instruction: "Ask Otter to collect the key.", status: "pending",
    plan: { summary: "A side task.", additions: {}, key_bindings: {}, secondary_goals: {
      collect: { title: "Collect the key", description: "An optional task.", assigned_actor_ids: ["recipient"],
        conditions: [{ kind: "carrying", target: "key" }] }
    } }
  });
  assert.deepEqual(proposal.plan.additions, {});
  assert.deepEqual(proposal.plan.secondary_goals.collect.assigned_actor_ids, ["recipient"]);
  assert.equal(proposal.plan.secondary_goals.collect.conditions[0].quantifier, "any");
  assert.throws(() => secondaryGoalSpecSchema.parse({
    ...proposal.plan.secondary_goals.collect, status: "achieved"
  }));
  assert.throws(() => secondaryConditionSchema.parse({ kind: "execute_script", target: "key" }));
});

test("world progress and evidence distinguish completion from the main objective", () => {
  const goal = secondaryGoalSchema.parse({
    title: "Collect the key", description: "An optional task.", assigned_actor_ids: ["recipient"],
    conditions: [{ kind: "carrying", target: "key" }], condition_met: [true], status: "achieved",
    completed_revision: "obs-9"
  });
  assert.equal(goal.status, "achieved");
  assert.deepEqual(goal.condition_met, [true]);
  const explained = explainEvidence(["goal-event"], [{
    id: "goal-event", sequence: 1, run_id: "run", actor_id: "recipient", session_id: "session",
    timestamp: "2026-11-05T16:00:00Z", cycle: 1, kind: "secondary_goal_achieved",
    payload: { title: goal.title, reason: "The world verified every completion condition.", verification_revision: "obs-9" }
  }]);
  assert.equal(explained[0].title, "Side goal completed: Collect the key");
  assert.match(explained[0].explanation, /world verified/);
  assert.match(explained[0].details[0], /obs-9/);
});
