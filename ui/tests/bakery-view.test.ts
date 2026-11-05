import assert from "node:assert/strict";
import { test } from "node:test";
import { batchTiming, clockHelp, clockTime } from "../src/bakery-view.ts";
import { breadBatchSchema, clockSchema } from "../src/contracts.ts";

test("clock formats scaled elapsed time without wrapping at 24 hours", () => {
  assert.equal(clockTime(7200), "02:00:00");
  assert.equal(clockTime(90061.9), "25:01:01");
});

test("batch display distinguishes countdowns, handling windows, serving and failures", () => {
  const batch = breadBatchSchema.parse({
    ingredients: ["flour", "water", "yeast", "salt"], stage: "rising",
    equipment: null, ready_at: 7200, deadline: 9000, failure: null
  });
  assert.equal(batchTiming(batch, 3600), "Ready in 01:00:00");
  assert.equal(batchTiming({ ...batch, stage: "risen" }, 7200), "READY - handle within 00:30:00");
  assert.equal(batchTiming(batch, 9001), "Handling deadline missed");
  assert.equal(batchTiming({ ...batch, stage: "served" }, 9001), "Served on display");
  assert.equal(batchTiming({ ...batch, stage: "ruined", failure: "Burned" }, 9001), "Burned");
});

test("clock contract rejects unsupported speeds and nonfinite time", () => {
  assert.ok(!clockSchema.safeParse({ seconds: 0, real_seconds: 0, running: true, rate: 601 }).success);
  assert.ok(!clockSchema.safeParse({ seconds: Infinity, real_seconds: 0, running: true, rate: 120 }).success);
});

test("legacy clocks retain continuous mode and policy help distinguishes decision-paused time", () => {
  const legacy = clockSchema.parse({ seconds: 0, real_seconds: 0, running: false, rate: 120 });
  assert.equal(legacy.mode, "continuous");
  assert.match(clockHelp(legacy), /Continuous challenge/);
  const clock = clockSchema.parse({ ...legacy, mode: "decision_paused" });
  assert.match(clockHelp(clock), /Decisions and controller processing freeze bakery time/);
  assert.match(clockHelp(clock), /60.0 real clock-running seconds/);
  assert.ok(!clockSchema.safeParse({ ...clock, mode: "invalid" }).success);
});
