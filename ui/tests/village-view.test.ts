import assert from "node:assert/strict";
import test from "node:test";
import { villageObservationSchema } from "../src/contracts.ts";
import { villageMarkup } from "../src/village-view.ts";

const view = () => villageObservationSchema.parse({
  minute: 505, day: 1, time: "08:25", coins: 20,
  residents: [{ id: "torvin", name: "Torvin", occupation: "blacksmith", activity: "Wagon repair" }],
  stocks: { iron: 4 }, resource_inventory: { herbs: 1 },
  tasks: [{ id: "task-1", kind: "fetch_shovel", assignee: "torvin",
    status: "blocked", step: "plan", blocker: "Urgent work first" }],
  events: [{ minute: 500, actor: "torvin", action: "talk", target: "visitor",
    result: "I last heard it was at the stable.", witnesses: ["torvin", "visitor"] }],
  policy: "Every action takes five minutes."
});

test("village view shows local people, clock, stock and honest blocked progress", () => {
  const markup = villageMarkup(view());
  for (const text of ["DAY 1", "08:25", "20 coins", "Torvin", "iron: 4", "herbs: 1",
    "fetch shovel", "blocked", "Urgent work first", "stable"]) assert.ok(markup.includes(text));
  assert.ok(!markup.includes("mill-shed"));
});

test("village markup escapes dialogue and names rather than trusting generated content", () => {
  const village = view();
  village.residents[0].name = '<img src=x onerror="alert(1)">';
  village.events[0].result = "<script>bad()</script>";
  const markup = villageMarkup(village);
  assert.ok(!markup.includes("<img"));
  assert.ok(!markup.includes("<script>"));
  assert.ok(markup.includes("&lt;script&gt;"));
});

test("village contract rejects invented progress states and negative stocks", () => {
  assert.equal(villageObservationSchema.safeParse({ ...view(), stocks: { grain: -1 } }).success, false);
  assert.equal(villageObservationSchema.safeParse({
    ...view(), tasks: [{ ...view().tasks[0], status: "claimed_complete" }]
  }).success, false);
});
