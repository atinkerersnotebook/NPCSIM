import assert from "node:assert/strict";
import { test } from "node:test";
import { adventureStatus, canAskQuestions, canExtendBudget, canReopenAdventure, isAdminView, playerCommandVisible, simulationClock, villageClockText, villageClockTime, viewMode, viewNavigation } from "../src/view-mode.ts";
import { budgetExtensionSchema } from "../src/contracts.ts";

test("budget extension requires a saved world and an active or budget-exhausted adventure", () => {
  for (const status of ["ready", "running", "paused", "budget_exceeded"]) {
    assert.equal(canExtendBudget(status, true), true);
    assert.equal(canExtendBudget(status, false), false);
  }
  for (const status of ["idle", "succeeded", "error", "blocked", "stalled", "cancelled"]) {
    assert.equal(canExtendBudget(status, true), false);
  }
});

test("village timeout recovery is offered only for saved MCP timeout worlds", () => {
  const run = {
    status: "error", reason: "Timed out while waiting for response to ClientRequest. Waited 15.0 seconds.",
    goal: {type: "village_restored"}
  };
  assert.equal(canReopenAdventure(run, true), true);
  assert.equal(canReopenAdventure(run, false), false);
  assert.equal(canReopenAdventure({...run, reason: "Another error."}, true), false);
  assert.equal(canReopenAdventure({...run, goal: {type: "other"}}, true), false);
  assert.equal(playerCommandVisible("reopen", "error"), true);
});

test("budget extension payload accepts independent additions and rejects invalid amounts", () => {
  const request = {run_id: "run", add_actions: 0, add_model_calls: 100, add_seconds: 0};
  assert.deepEqual(budgetExtensionSchema.parse(request), request);
  for (const invalid of [
    {...request, add_model_calls: 0}, {...request, add_actions: -1},
    {...request, add_model_calls: 1.5}, {...request, add_seconds: Infinity},
    {...request, add_model_calls: NaN}, {...request, run_id: ""},
    {...request, add_actions: "100"}, {...request, unknown: true}
  ]) assert.equal(budgetExtensionSchema.safeParse(invalid).success, false);
});

test("village clock formatting uses time of day and distinguishes overnight availability", () => {
  assert.equal(villageClockTime(485), "08:05");
  assert.equal(villageClockTime(1440), "00:00 on day 2");
  assert.equal(villageClockText("Available again at minute 485. Wait 30 minutes."),
    "Available again at 08:05. Wait 30 minutes.");
  assert.equal(villageClockText("Sleeping until minute 1800."), "Sleeping until 06:00 on day 2.");
  assert.equal(villageClockText("Minute 485 waits 30 minutes."), "Minute 485 waits 30 minutes.");
});

test("current simulation clock covers village, bakery and BBQ but hides untimed adventures", () => {
  assert.deepEqual(simulationClock({village: {minute: 485}}), {time: "08:05", description: "Day 1"});
  assert.deepEqual(simulationClock({village: {minute: 1800}}), {time: "06:00", description: "Day 2"});
  assert.deepEqual(simulationClock({clock: {seconds: 0}}), {time: "00:00:00", description: "Simulated elapsed time"});
  assert.deepEqual(simulationClock({bbq: {elapsed_seconds: 90}}), {time: "00:01:30", description: "Simulated elapsed time"});
  assert.equal(simulationClock({}), null);
  assert.equal(simulationClock(null), null);
  assert.equal(simulationClock(undefined), null);
});

test("clock follows a newer authoritative observation without inventing future time", () => {
  assert.deepEqual(simulationClock({village: {minute: 1380}}, {village: {minute: 1440}}),
    {time: "00:00", description: "Day 2"});
  assert.deepEqual(simulationClock({village: {minute: 1440}}, {village: {minute: 1380}}),
    {time: "00:00", description: "Day 2"});
  assert.deepEqual(simulationClock(null, {village: {minute: 1440}}),
    {time: "00:00", description: "Day 2"});
});

test("private questions remain available after the adventure ends when saved knowledge exists", () => {
  for (const status of ["ready", "running", "paused", "succeeded", "blocked", "stalled",
    "budget_exceeded", "cancelled", "error"]) {
    assert.equal(canAskQuestions(status, true), true);
    assert.equal(canAskQuestions(status, false), false);
  }
  assert.equal(canAskQuestions("idle", true), false);
  assert.equal(canAskQuestions("unknown", true), false);
});

test("the player view is the default and admin view requires an explicit URL selection", () => {
  assert.equal(isAdminView(""), false);
  assert.equal(isAdminView("?view=player"), false);
  assert.equal(isAdminView("?view=unknown"), false);
  assert.equal(isAdminView("?view=admin"), true);
  assert.equal(isAdminView("?other=value&view=admin"), true);
});

test("three views are addressable without changing the adventure", () => {
  assert.equal(viewMode(""), "player");
  assert.equal(viewMode("?view=unknown"), "player");
  assert.equal(viewMode("?view=admin"), "admin");
  assert.equal(viewMode("?view=world"), "world");
  for (const view of ["player", "admin", "world"] as const) {
    const markup = viewNavigation(view);
    assert.equal((markup.match(/aria-current=/g) ?? []).length, 1);
    assert.ok(markup.includes('href="/"'));
    assert.ok(markup.includes('href="/?view=admin"'));
    assert.ok(markup.includes('href="/?view=world"'));
  }
});

test("player controls expose only the actions relevant to the current adventure", () => {
  const commands = ["start", "run", "pause", "resume", "reset", "step", "assess"];
  const visible = (status: string) => commands.filter(command => playerCommandVisible(command, status));
  assert.deepEqual(visible("idle"), ["start"]);
  assert.deepEqual(visible("ready"), ["start", "run", "reset"]);
  assert.deepEqual(visible("running"), ["start", "pause", "reset"]);
  assert.deepEqual(visible("paused"), ["start", "resume", "reset"]);
  for (const status of ["succeeded", "blocked", "error", "cancelled", "stalled", "budget_exceeded"]) {
    assert.deepEqual(visible(status), ["start", "reset"]);
  }
  assert.equal(playerCommandVisible(undefined, "ready"), false);
});

test("player status distinguishes defeat, interruptions and review errors from success", () => {
  assert.equal(adventureStatus("ready"), "Ready to play");
  assert.equal(adventureStatus("blocked"), "Admitted defeat");
  assert.equal(adventureStatus("error"), "Adventure interrupted");
  assert.equal(adventureStatus("succeeded", { status: "pending", verdict: null }), "Reviewing the outcome");
  assert.equal(adventureStatus("succeeded", { status: "running", verdict: null }), "Reviewing the outcome");
  assert.equal(adventureStatus("succeeded", { status: "completed", verdict: "fail" }), "Adventure not successful");
  assert.equal(adventureStatus("succeeded", { status: "completed", verdict: "pass" }), "Adventure successful");
  assert.equal(adventureStatus("succeeded", { status: "error", verdict: null }), "Outcome review unavailable");
});
