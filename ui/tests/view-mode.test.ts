import assert from "node:assert/strict";
import { test } from "node:test";
import { adventureStatus, isAdminView, playerCommandVisible } from "../src/view-mode.ts";

test("the player view is the default and admin view requires an explicit URL selection", () => {
  assert.equal(isAdminView(""), false);
  assert.equal(isAdminView("?view=player"), false);
  assert.equal(isAdminView("?view=unknown"), false);
  assert.equal(isAdminView("?view=admin"), true);
  assert.equal(isAdminView("?other=value&view=admin"), true);
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
