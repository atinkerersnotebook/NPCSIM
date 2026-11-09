import assert from "node:assert/strict";
import { test } from "node:test";
import { buildDayNarrative, buildNarrative, buildStory, canCopyStory, copyRecordedStory, copyStory, storyDays, villageTimeline } from "../src/story.ts";
import type { RunEvent } from "../src/contracts.ts";
import { observationSchema } from "../src/contracts.ts";

test("private questions and privileged previews never enter shared narrative", () => {
  const events = [
    event(1, "observation", observation([])),
    event(2, "npc_chat", { question: "PRIVATE_QUESTION", answer: "PRIVATE_ANSWER", private: true }),
    event(3, "world_edit_proposed", { plan: { summary: "HIDDEN_NOOK_PREVIEW" } }),
    event(4, "world_edit_applied", { summary: "HIDDEN_NOOK_APPROVAL" })
  ];
  assert.doesNotMatch(JSON.stringify(buildStory(events)), /PRIVATE_|HIDDEN_NOOK/);
  assert.doesNotMatch(JSON.stringify(buildNarrative(events)), /PRIVATE_|HIDDEN_NOOK/);
});

test("search has readable discovery narration", () => {
  const events = [
    event(1, "observation", observation([])),
    event(2, "action_request", { request_id: "search", action_id: "search", arguments: { target: "wall" } })
  ];
  assert.match(buildNarrative(events).map(paragraph => paragraph.text).join(" "), /searching.*concealed features/);
});

for (const [action, arguments_, text] of [
  ["cast_unlock", {target: "door"}, /I try casting Unlock on/],
  ["cast_shrink", {}, /I try casting Shrink on myself and my equipment/],
  ["cast_restore_size", {}, /I try restoring my original size/]
] as const) {
  test(`${action} has a readable spell intention`, () => {
    const events = [
      event(1, "observation", observation([])),
      event(2, "action_request", {request_id: "spell", action_id: action, arguments: arguments_})
    ];
    assert.match(buildNarrative(events).map(paragraph => paragraph.text).join(" "), text);
  });
}

test("BBQ observation retains the gauge, scoring, flare-ups and temperature history", () => {
  const parsed = observationSchema.parse({
    ...observation([]), bbq: {
      target_f: 225, tolerance_f: 10, duration_seconds: 1800, elapsed_seconds: 30,
      temperature_f: 251, heat_percent: 40, in_band_seconds: 15, in_band_percent: 50,
      minimum_in_band_percent: 80, in_band: false, flare_active: true, flare_count: 1,
      max_deviation_f: 26, status: "tending",
      history: [{seconds: 0, temperature_f: 225, heat_percent: 50},
        {seconds: 30, temperature_f: 251, heat_percent: 40}]
    }
  });
  assert.equal(parsed.bbq?.flare_active, true);
  assert.equal(parsed.bbq?.history[1].temperature_f, 251);
  assert.throws(() => observationSchema.parse({...parsed, bbq: {...parsed.bbq, heat_percent: 101}}));
  assert.throws(() => observationSchema.parse({...parsed, bbq: {...parsed.bbq, temperature_f: NaN}}));
});

for (const [action, arguments_, text] of [
  ["adjust_bbq_heat", {heat_percent: 35}, /I adjust the BBQ heat dial to 35%/],
  ["monitor_bbq", {}, /I watch the BBQ temperature gauge without changing the heat/]
] as const) {
  test(`BBQ ${action} has a readable temperature-tending intention`, () => {
    const events = [
      event(1, "observation", observation([])),
      event(2, "action_request", {request_id: "bbq", action_id: action, arguments: arguments_})
    ];
    assert.match(buildNarrative(events).map(paragraph => paragraph.text).join(" "), text);
  });
}

test("ERP observation retains typed terminal output without physical actions", () => {
  const parsed = observationSchema.parse({
    ...observation([]), erp: {prompt: "erp>", command: "queue",
      output: "INV-1042 | on hold", invoice_status: "on_hold"}
  });
  assert.equal(parsed.erp?.output, "INV-1042 | on hold");
  assert.throws(() => observationSchema.parse({...parsed, erp: {...parsed.erp, invoice_status: "paid"}}));
});

test("ERP pending commands are narrated as terminal input, not a physical action", () => {
  const events = [
    event(1, "observation", observation([])),
    event(2, "action_request", {request_id: "erp", action_id: "erp_terminal",
      arguments: {command: "queue"}})
  ];
  assert.match(buildNarrative(events).map(paragraph => paragraph.text).join(" "),
    /I enter "queue" in the ERP terminal/);
});

test("copying the final story preserves reviewed prose and paragraph breaks only", async () => {
  let text = "";
  await copyStory([{text: "I read the note."}, {text: "I served my sandwich."}],
    {writeText: async value => { text = value; }});
  assert.equal(text, "I read the note.\n\nI served my sandwich.");
});

test("clipboard failures remain explicit rather than pretending the story was copied", async () => {
  await assert.rejects(copyStory([], undefined), /no story/);
  await assert.rejects(copyStory([{text:"Story"}], undefined), /unavailable/);
  await assert.rejects(copyStory([{text:"Story"}], {writeText: async () => {throw Error("Permission denied");}}),
    /Permission denied/);
});

test("story days use actual village minutes, not wall clock dates or missing calendar days", () => {
  const events = [
    {...event(1, "observation", observation([])), village_minute: 1439},
    {...event(2, "action_request", {request_id: "look", action_id: "look", arguments: {}}),
      village_minute: 1440},
    {...event(3, "finished", {status: "succeeded"}), village_minute: 4320}
  ];
  assert.deepEqual(storyDays([...events].reverse()), [1, 2, 4]);
  assert.deepEqual(buildDayNarrative(events, undefined, undefined, {}, 1).map(p => p.id), ["run:1"]);
  assert.deepEqual(buildDayNarrative(events, undefined, undefined, {}, 2).map(p => p.id), ["run:2"]);
  assert.deepEqual(buildDayNarrative(events, undefined, undefined, {}, 3), []);
  assert.deepEqual(buildDayNarrative(events), buildNarrative(events));
  assert.match(buildDayNarrative(events, undefined, undefined, {}, 2)[0].text, /Day 2 00:00/);
});

test("midnight results are not merged into the previous day's attempted action", () => {
  const events = [
    event(1, "observation", observation([])),
    {...event(2, "action_request", {request_id: "wait", action_id: "village_wait",
      arguments: {minutes: 1}}), village_minute: 1439},
    {...event(3, "action_result", {request_id: "wait", outcome: "APPLIED",
      explanation: "You finish waiting.", observation: observation([])}), village_minute: 1440},
    event(4, "finished", {status: "succeeded"})
  ];
  const day1 = buildDayNarrative(events, undefined, undefined, {}, 1);
  const day2 = buildDayNarrative(events, undefined, undefined, {}, 2);
  assert.deepEqual(day1.map(p => p.id), ["run:1", "run:2"]);
  assert.deepEqual(day2.map(p => p.id), ["run:3", "run:4"]);
  assert.doesNotMatch(day1.map(p => p.text).join(" "), /finish waiting|achieved/);
  assert.match(day2[0].text, /Day 2 00:00.*finish waiting/);
});

test("day filtering preserves reviewed names, event order and excludes technical events", () => {
  const events = [
    {...event(1, "observation", {...observation([]), role: "A scout."}, "npc-001"), village_minute: 1439},
    {...event(2, "proxy_request", {message: "TECHNICAL_ONLY"}, "npc-001"), village_minute: 1440},
    {...event(3, "observation", {...observation([]), role: "A builder."}, "npc-002"), village_minute: 1440}
  ];
  const reviewed = [{id: "run:3", text: "npc-002: I look around."},
    {id: "run:1", text: "npc-001: I read the note."}];
  const members = {"npc-001": {name: "Avery"}, "npc-002": {name: "Robin"}};
  const original = JSON.stringify({events, reviewed});
  assert.deepEqual(buildDayNarrative(events, undefined, reviewed, members).map(p => p.id), ["run:1", "run:3"]);
  const day2 = buildDayNarrative(events, undefined, reviewed, members, 2);
  assert.equal(day2.length, 1);
  assert.match(day2[0].text, /Robin looks around/);
  assert.doesNotMatch(JSON.stringify(buildDayNarrative(events, undefined, undefined, members, 2)), /TECHNICAL_ONLY/);
  assert.equal(JSON.stringify({events, reviewed}), original);
});

test("nonvillage stories remain unchanged and copy is available while paused or running", () => {
  const events = [event(1, "observation", observation([]))];
  const paragraphs = buildNarrative(events);
  assert.deepEqual(storyDays(events), []);
  assert.deepEqual(buildDayNarrative(events), paragraphs);
  assert.deepEqual(buildDayNarrative(events, undefined, undefined, {}, 2), paragraphs);
  for (const status of ["ready", "paused", "running", "succeeded", "cancelled"]) {
    assert.equal(canCopyStory({status}, paragraphs), true);
    assert.equal(canCopyStory({status}, []), false);
  }
  assert.equal(canCopyStory(null, paragraphs), false);
});

test("copying fetches the full recorded story beyond 100 events and respects the selected day", async () => {
  const events = [
    {...event(1, "observation", observation([])), village_minute: 480},
    ...Array.from({length: 110}, (_, i) =>
      ({...event(i + 2, "proxy_request", {message: "TECHNICAL_ONLY"}), village_minute: 480})),
    {...event(112, "finished", {status: "succeeded"}), village_minute: 1440}
  ];
  let loads = 0;
  let copied = "";
  const load = async () => { loads++; return {run_id: "run", events}; };
  const clipboard = {writeText: async (text: string) => { copied = text; }};
  await copyRecordedStory(load, "run", clipboard);
  assert.equal(loads, 1);
  assert.match(copied, /A room[\s\S]*achieved my goal/);
  assert.doesNotMatch(copied, /TECHNICAL_ONLY/);
  await copyRecordedStory(load, "run", clipboard, undefined, undefined, {}, 2);
  assert.match(copied, /Day 2 00:00.*achieved my goal/);
  assert.doesNotMatch(copied, /A room/);
  await assert.rejects(copyRecordedStory(load, "other-run", clipboard), /adventure changed/);
  await assert.rejects(copyRecordedStory(load, "run", {writeText: async () => {
    throw Error("Permission denied");
  }}), /Permission denied/);
});

function observation(ids = ["door", "box", "rock"], location = "room") {
  return {
    contract_version: "1.0", world_id: "world", session_id: "session", observation_revision: "obs-1",
    self: {
      actor_id: "npc", location_id: location, inventory: [], needs: [],
      body: { id: "body", description: "A body.", capabilities: [], carrying: { rule: "item_count", limit: 1, used: 0 } }
    },
    location: { id: location, description: "A room." },
    narrative: location === "room" ? "A room." : "You have left the room.",
    visible_entities: ids.map(id => ({ id, name: id === "key" ? "Brass key" : id, description: id })),
    actions: []
  };
}
function event(sequence: number, kind: string, payload: unknown, actor = "npc"): RunEvent {
  return { id: `run:${sequence}`, run_id: "run", session_id: "session", sequence, kind,
    cycle: Math.max(0, sequence - 2), actor_id: actor, timestamp: "2026-11-02T23:00:00Z", payload };
}

test("old village availability messages use clock time without rewriting saved events", () => {
  const events = [
    {...event(1, "observation", observation([]), "torvin"), village_minute: 480},
    {...event(2, "action_result", {request_id: "wait", outcome: "APPLIED",
      explanation: "Waiting 5 minutes. Available again at minute 485.", observation: observation([])}, "torvin"),
      village_minute: 480}
  ];
  const saved = JSON.stringify(events);
  const story = buildStory(events, undefined, {torvin: {name: "Torvin"}});
  assert.match(story.at(-1)!.text, /Available again at 08:05/);
  assert.doesNotMatch(story.at(-1)!.text, /minute 485/);
  assert.match(buildNarrative(events).at(-1)!.text, /Available again at 08:05/);
  assert.equal(JSON.stringify(events), saved);
});

test("free observations stay separately labeled from the following work action", () => {
  const events = [
    event(1, "observation", observation(["noticeboard"]), "torvin"),
    { ...event(2, "action_request", {request_id: "read", action_id: "read",
      arguments: {target: "noticeboard"}}, "torvin"), free_action: true, village_minute: 480 },
    { ...event(3, "action_result", {request_id: "read", outcome: "APPLIED",
      explanation: "Village map (free).", observation: observation(["noticeboard"])}, "torvin"),
      free_action: true, village_minute: 480 },
    { ...event(4, "action_request", {request_id: "wait", action_id: "village_wait",
      arguments: {minutes: 5}}, "torvin"), free_action: false, village_minute: 480 },
    { ...event(5, "action_result", {request_id: "wait", outcome: "APPLIED",
      explanation: "Waiting 5 minutes.", observation: observation(["noticeboard"])}, "torvin"),
      free_action: false, village_minute: 480 }
  ];
  const members = {torvin: {name: "Torvin"}};
  const story = buildStory(events, undefined, members);
  assert.equal(story.filter(entry => entry.freeAction).length, 2);
  const paragraphs = buildNarrative(events, undefined, undefined, members);
  assert.ok(paragraphs.some(paragraph => paragraph.text.includes("FREE LOOK/READ")));
  assert.ok(paragraphs.some(paragraph => paragraph.text.includes("Waiting 5 minutes") &&
    !paragraph.text.includes("FREE LOOK/READ")));
});

test("team speech retains I and you while surrounding narration uses member names", () => {
  const events = [
    event(1, "observation", { ...observation([]), role: "A stable keeper." }, "bren"),
    event(2, "action_request", { request_id: "say", action_id: "say",
      arguments: { message: "I have the shovel. Are you ready, Torvin?" } }, "bren"),
    event(3, "action_result", { request_id: "say", outcome: "APPLIED",
      explanation: 'Bren says: "I have the shovel. Are you ready, Torvin?"', observation: observation([]) }, "bren")
  ];
  const members = { bren: { name: "Bren" } };
  const story = buildStory(events, undefined, members);
  assert.match(story[1].text, /Bren says, "I have the shovel\. Are you ready, Torvin\?"/);
  assert.match(story[2].text, /Bren says: "I have the shovel\. Are you ready, Torvin\?"/);
  assert.doesNotMatch(JSON.stringify(story), /Bren has the shovel|Are Bren|Are I/);
  assert.match(buildNarrative(events, undefined, undefined, members).at(-1)!.text, /"I have the shovel\. Are you ready/);
});

test("game time and member name survive live, reviewed and copied narratives", async () => {
  const events = [
    { ...event(1, "observation", observation([]), "torvin"), village_minute: 1925, actor_name: "Torvin" },
    { ...event(2, "action_request", { request_id: "look", action_id: "look", arguments: {} }, "torvin"),
      village_minute: 1925, actor_name: "Torvin" }
  ];
  assert.equal(villageTimeline(events[0]), "Day 2 08:05 / Torvin");
  assert.equal(buildStory(events)[0].timeline, "Day 2 08:05 / Torvin");
  const narrative = buildNarrative(events);
  assert.ok(narrative.every(paragraph => paragraph.text.startsWith("[Day 2 08:05 / Torvin] ")));
  const reviewed = buildNarrative(events, undefined, [{ id: events[1].id, text: "I look around." }]);
  assert.match(reviewed[0].text, /^\[Day 2 08:05 \/ Torvin\]/);
  let copied = "";
  await copyStory(narrative, { writeText: async text => { copied = text; } });
  assert.match(copied, /Day 2 08:05 \/ Torvin/);
  assert.equal(villageTimeline(event(3, "observation", observation([]))), undefined);
});

test("adventure prose and decision reasons use the selected member name without changing events", () => {
  const events = [
    event(1, "observation", observation([]), "npc-001"),
    { ...event(2, "decision", { action_id: "look", justification: "npc-001 checks the room." }, "npc-001"), cycle: 1 },
    { ...event(3, "action_request", { request_id: "look", action_id: "look", arguments: {} }, "npc-001"), cycle: 1 }
  ];
  const original = JSON.stringify(events);
  const members = { "npc-001": { name: "Avery" } };
  assert.match(JSON.stringify(buildStory(events, "npc-001", members)), /Avery checks the room/);
  const prose = buildNarrative(events, "npc-001", undefined, members).map(p => p.text).join(" ");
  assert.match(prose, /Avery checks the room/);
  assert.doesNotMatch(prose, /npc-001/);
  assert.equal(JSON.stringify(events), original);
});

test("team narration uses assigned names rather than role IDs", () => {
  const events = [
    event(1, "observation", { ...observation([]), role: "Scout" }, "raven"),
    event(2, "action_request", { request_id: "look", action_id: "look", arguments: {} }, "raven")
  ];
  const text = buildNarrative(events, undefined, undefined, { raven: { name: "Robin" } })
    .map(p => p.text).join(" ");
  assert.match(text, /Robin looks around/);
  assert.doesNotMatch(text, /\bRaven\b/);
});

test("reviewed and copied archives use that episode's names, preserving saved evidence", async () => {
  const reviewed = [{ id: "archive:1", text: "npc-001 asks Bren for help. NPC-001's shovel is nearby." }];
  const original = JSON.stringify(reviewed);
  const paragraphs = buildNarrative([], undefined, reviewed, { "npc-001": { name: "Avery" } });
  let copied = "";
  await copyStory(paragraphs, { writeText: async text => { copied = text; } });
  assert.equal(copied, "Avery asks Bren for help. Avery's shovel is nearby.");
  assert.equal(JSON.stringify(reviewed), original);
});

test("member name replacement respects identifier boundaries and literal name punctuation", () => {
  const reviewed = [{ id: "archive:1", text: "npc-001 sees npc-001-body, npc-0012 and other-npc-001." }];
  const paragraphs = buildNarrative([], undefined, reviewed, { "npc-001": { name: "Avery $&" } });
  assert.equal(paragraphs[0].text, "Avery $& sees npc-001-body, npc-0012 and other-npc-001.");
});

test("tic-tac-toe story names the actor and cell without a technical action ID", () => {
  const initial = {...observation(), role: "A tic-tac-toe player."};
  const events = [
    event(1, "observation", initial, "nova"),
    event(2, "action_request", {request_id: "move", action_id: "play_tic_tac_toe", arguments: {cell: 5}}, "nova"),
    event(3, "action_result", {request_id: "move", outcome: "APPLIED", explanation: "nova places X in cell 5.",
      observation: initial}, "nova")
  ];
  const text = buildNarrative(events).map(paragraph => paragraph.text).join(" ");
  assert.doesNotMatch(text, /tries placing/);
  assert.match(text, /Nova places X in cell 5/);
  assert.doesNotMatch(text, /play.tic.tac.toe/);
});

function wiringEvents(applied = true): RunEvent[] {
  const initial = { ...observation(["wiring-box"]), role: "An underwater engineer.",
    skill_challenges: [{ id: "repair_wiring", target: "wiring-box", capability: "water_engineering",
      description: "Install the insulated wire coil and safely reconnect the wet beacon wiring.",
      completed_by: null, reason: null }] };
  initial.visible_entities[0].name = "Wet beacon wiring box";
  initial.visible_entities[0].description = "A wet wiring box.";
  return [
    event(1, "observation", initial, "otter"),
    { ...event(2, "decision", { action_id: "repair_wiring",
      justification: "otter can repair_wiring at wiring-box." }, "otter"), cycle: 1 },
    { ...event(3, "action_request", { request_id: "repair", action_id: "repair_wiring",
      arguments: { target: "wiring-box" } }, "otter"), cycle: 1 },
    { ...event(4, "action_result", { request_id: "repair", outcome: applied ? "APPLIED" : "REJECTED",
      explanation: applied ?
        "You use water_engineering: Install the insulated wire coil and safely reconnect the wet beacon wiring." :
        "Prior work still needed: mount_lens.", observation: initial }, "otter"), cycle: 1 }
  ];
}

for (const applied of [true, false]) {
  test(`cooperative prose removes technical identifiers without inventing success (${applied})`, () => {
    const events = wiringEvents(applied);
    const original = JSON.stringify(events);
    const entries = buildStory(events, "otter");
    const text = buildNarrative(events, "otter").map(paragraph => paragraph.text).join(" ");
    if (applied) assert.doesNotMatch(text, /Otter tries to install/);
    else assert.match(text, /Otter tries to install the insulated wire coil/);
    assert.match(text, /Otter can repair wiring at wet beacon wiring box\./);
    assert.doesNotMatch(JSON.stringify(entries), /repair_wiring|water_engineering|mount_lens|wiring-box/);
    assert.doesNotMatch(text, /[{}]/);
    if (applied) assert.match(text, /Otter uses underwater engineering to install/);
    else {
      assert.match(text, /Prior work still needed: mount lens\./);
      assert.doesNotMatch(text, /Otter uses underwater engineering/);
    }
    assert.equal(JSON.stringify(events), original);
  });
}

test("unknown actions have readable attempts rather than serialized arguments", () => {
  const initial = observation(["pressure-valve"]);
  initial.visible_entities[0].name = "Brass pressure valve";
  const story = buildStory([
    event(1, "observation", initial),
    event(2, "action_request", { request_id: "pressure", action_id: "adjust_pressure",
      arguments: { target: "pressure-valve", level: 3 } })
  ]);
  assert.equal(story[1].text, "I try to adjust pressure at the brass pressure valve with level set to 3.");
});

test("rock-paper-scissors narrates private commitments and the revealed score", () => {
  const initial = { ...observation([]), role: "A competing player.",
    self: { ...observation().self, actor_id: "atlas" } };
  const events = [
    event(1, "observation", initial, "atlas"),
    { ...event(2, "action_request", { request_id: "move", action_id: "play_rps",
      arguments: { move: "rock" } }, "atlas"), cycle: 1 },
    { ...event(3, "action_result", { request_id: "move", outcome: "APPLIED",
      explanation: "Round 5: nova played paper, atlas played rock. nova wins the round. Score: nova 3, atlas 1. Match complete. nova wins the match.",
      observation: initial }, "atlas"), cycle: 1 }
  ];
  const text = buildNarrative(events).map(paragraph => paragraph.text).join(" ");
  assert.doesNotMatch(text, /Atlas tries committing/);
  assert.match(text, /Score: nova 3, atlas 1/);
  assert.match(text, /nova wins the match/);
  assert.doesNotMatch(text, /play_rps|play rps|move set to/);
});

test("archived technical prose is cleaned for display and copying without modifying grading evidence", async () => {
  const events = wiringEvents();
  const reviewed = [{ id: "run:3", text:
    'Otter tries "repair_wiring" with {"target":"wiring-box"}. Otter uses water_engineering: Install the insulated wire coil.' }];
  const original = JSON.stringify(reviewed);
  const rendered = buildNarrative(events, "otter", reviewed);
  assert.doesNotMatch(rendered[0].text, /Otter tries to install/);
  assert.match(rendered[0].text, /Otter uses underwater engineering to install the insulated wire coil/);
  assert.doesNotMatch(rendered[0].text, /[{}_]|wiring-box/);
  let copied = "";
  await copyStory(rendered, { writeText: async value => { copied = value; } });
  assert.equal(copied, rendered[0].text);
  assert.equal(JSON.stringify(reviewed), original);
});

test("team stories name individuals and reserve we for the shared outcome", () => {
  const otter = { ...observation(["rope"]), role: "A swimmer.",
    self: { ...observation().self, actor_id: "otter" } };
  const fox = { ...observation(["toolbox"]), role: "A repairer.",
    self: { ...observation().self, actor_id: "fox" } };
  const request = event(5, "action_request", {
    request_id: "rope", action_id: "take", arguments: { target: "rope" }
  }, "otter");
  const decision = { ...event(4, "decision", { action_id: "take",
    justification: "I need my rope. We must cross together." }, "otter"), cycle: request.cycle };
  const events = [
    event(1, "observation", otter, "otter"),
    event(2, "observation", fox, "fox"),
    event(3, "started", { goal: { objective: "Cross together." } }, "otter"),
    decision, request,
    event(6, "action_result", { request_id: "rope", outcome: "APPLIED",
      explanation: "You pick up the rope. You have room in your inventory.", observation: otter }, "otter"),
    event(7, "finished", { status: "succeeded" }, "otter")
  ];
  const story = buildStory(events, "otter");
  assert.match(story[0].text, /A room\. Otter can see/);
  assert.match(story[0].details!.join(" "), /Otter can carry/);
  assert.match(story.find(entry => entry.kind === "intent")!.text, /Otter tries to pick up the rope/);
  assert.equal(story.find(entry => entry.kind === "intent")!.explanation,
    "Otter needs Otter's rope. We must cross together.");
  const text = buildNarrative(events, "otter").map(paragraph => paragraph.text).join(" ");
  assert.match(text, /Our goal is to cross together/);
  assert.match(text, /Otter picked up the rope. Otter has room in Otter's inventory/);
  assert.match(text, /we have achieved our goal/);
  assert.doesNotMatch(text, /\b(I|my|My|You|you)\b|toolbox/);
  assert.match(buildNarrative(events, "fox")[0].text, /A room\. Fox can see/);
});

for (const outcome of ["APPLIED", "REJECTED", "NO_CHANGE", undefined]) {
  test(`narrative only replaces attempts with confirmed successes (${outcome})`, () => {
    const initial = observation(["zero-coupon"]);
    initial.visible_entities[0].name = "Zero coupon";
    const events = [
      event(1, "observation", initial),
      { ...event(2, "decision", { action_id: "take", justification: "I need the coupon." }), cycle: 1 },
      { ...event(3, "action_request", { request_id: "coupon", action_id: "take",
        arguments: { target: "zero-coupon" } }), cycle: 1 }
    ];
    if (outcome) events.push({ ...event(4, "action_result", {
      request_id: "coupon", outcome, explanation: outcome === "APPLIED" ?
        "You pick up the zero coupon." : "The coupon is out of reach.", observation: initial
    }), cycle: 1 });
    const original = JSON.stringify(events);
    const text = buildNarrative(events, "npc").at(-1)!.text;
    if (outcome === "APPLIED") assert.equal(text, "I need the coupon. I picked up the zero coupon.");
    else {
      assert.match(text, /I need the coupon\. I try to pick up the zero coupon\./);
      assert.doesNotMatch(text, /picked up/);
    }
    assert.equal(JSON.stringify(events), original);
  });
}

test("archived successful attempts collapse for display and copying without changing evidence", async () => {
  const initial = observation(["zero-coupon"]);
  initial.visible_entities[0].name = "Zero coupon";
  const events = [
    event(1, "observation", initial),
    event(2, "action_request", { request_id: "coupon", action_id: "take", arguments: { target: "zero-coupon" } }),
    event(3, "action_result", { request_id: "coupon", outcome: "APPLIED",
      explanation: "You pick up the zero coupon.", observation: initial })
  ];
  const reviewed = [{ id: "run:2", text: "I need the coupon. I try to pick up the zero coupon. I pick up the zero coupon." }];
  const original = JSON.stringify(reviewed);
  const rendered = buildNarrative(events, "npc", reviewed);
  assert.equal(rendered[0].text, "I need the coupon. I picked up the zero coupon.");
  let copied = "";
  await copyStory(rendered, { writeText: async value => { copied = value; } });
  assert.equal(copied, rendered[0].text);
  assert.equal(JSON.stringify(reviewed), original);
});

test("completed replay displays the exact story submitted for grading", () => {
  const reviewed = [{ id: "graded:1", text: "I made the sandwich, but it is still on the worktop." }];
  assert.deepEqual(buildNarrative([event(1, "observation", observation())], "npc", reviewed), reviewed);
  assert.notStrictEqual(buildNarrative([], "npc", reviewed), reviewed);
  assert.ok(buildNarrative([event(1, "observation", observation())], "npc", []).length);
});

test("archived team prose and copies name each actor without changing saved grading evidence", async () => {
  const events = [
    event(1, "observation", { ...observation(), role: "A thief." }, "thief"),
    event(2, "observation", { ...observation(), role: "A mage." }, "mage"),
    event(3, "finished", { status: "succeeded" }, "mage")
  ];
  const reviewed = [
    { id: "run:1", text: "thief: I try opening the vault." },
    { id: "run:2", text: "mage: I use my magic." },
    { id: "run:3", text: "mage: The world confirms that I have achieved my goal." }
  ];
  const rendered = buildNarrative(events, "mage", reviewed);
  assert.equal(rendered[0].text, "Thief tries opening the vault.");
  assert.equal(rendered[1].text, "Mage uses Mage's magic.");
  assert.equal(rendered[2].text, "The world confirms that we have achieved our goal.");
  assert.equal(reviewed[0].text, "thief: I try opening the vault.");
  let copied = "";
  await copyStory(rendered, { writeText: async value => { copied = value; } });
  assert.doesNotMatch(copied, /\bI\b/);
});

test("the story knows no assigned task until the Post-it is read", () => {
  const objective = "Make a peanut butter and jam sandwich and serve it on the plate.";
  const initial = observation(["postit-note"]);
  initial.visible_entities[0].name = "Post-it note";
  const before = [
    event(1, "started", { goal: { objective: "Explore and find instructions.", revealed: false } }),
    event(2, "observation", initial)
  ];
  assert.ok(!JSON.stringify(buildNarrative(before)).includes("sandwich"));
  const after = buildStory([...before,
    event(3, "action_request", { request_id: "read", action_id: "read", arguments: { target: "postit-note" } }),
    event(4, "action_result", { request_id: "read", outcome: "APPLIED", explanation: `You read the Post-it: ${objective}`, observation: initial }),
    event(5, "goal_discovered", { goal: { objective }, source_id: "postit-note" })
  ]);
  assert.match(after.at(-1)!.text, /After reading the post-it note, I learn my task:/);
  assert.ok(after.at(-1)!.text.includes(objective));
});

test("cooking intentions read as story prose rather than raw command arguments", () => {
  const commands = [
    ["read", { target: "note" }, "I try reading the note."],
    ["slice", { target: "bread", item: "knife" }, "I try slicing the bread with the knife."],
    ["dice", { target: "tomato", item: "knife" }, "I try dicing the tomato with the knife."],
    ["spread", { target: "bread", ingredient: "jam", item: "knife" }, "I try spreading the jam on the bread with the knife."],
    ["assemble", { first: "bread", second: "slice" }, "I try putting the bread and the slice together."],
    ["serve", { target: "sandwich", surface: "plate" }, "I try serving the sandwich on the plate."]
  ] as const;
  for (const [action, arguments_, text] of commands) {
    const story = buildStory([
      event(1, "observation", observation(["note", "bread", "knife", "tomato", "jam", "slice", "sandwich", "plate"])),
      event(2, "action_request", { request_id: "cook", action_id: action, arguments: arguments_ })
    ]);
    assert.equal(story[1].text, text);
  }
});

test("nutrition is narrated only from observations, and changed portions update the story", () => {
  const initial = observation(["bread"]);
  const nutrition = { serving_grams: 40, calories_kcal: 105, protein_g: 3.5, carbohydrates_g: 20, fat_g: 1, fiber_g: 1, sugar_g: 1.5 };
  const first = { ...initial, visible_entities: [{ ...initial.visible_entities[0], food: true, nutrition }] };
  const changed = { ...first, visible_entities: [{ ...first.visible_entities[0], nutrition: { ...nutrition, calories_kcal: 223 } }] };
  const narrative = buildNarrative([
    event(1, "observation", first),
    event(2, "observation", first),
    event(3, "observation", changed)
  ]);
  assert.equal(narrative.length, 2);
  assert.match(narrative[0].text, /105 kcal/);
  assert.ok(!narrative[0].text.includes("223 kcal"));
  assert.match(narrative[1].text, /223 kcal/);
});

test("voyage intentions use prose without claiming attempted travel succeeded", () => {
  const commands = [
    ["teleport", { word: "MOONWAKE" }, "I try speaking the journal's travel word."],
    ["recruit", { target: "navigator", item: "provisions" }, "I try recruiting the navigator with the provisions."],
    ["sail", {}, "I try sailing to the other island."],
    ["wait_tide", {}, "I wait safely for the next tide."],
    ["pace", { target: "ground", count: 24 }, "I measure 24 paces at the ground."],
    ["dig", { target: "ground", item: "spade" }, "I try digging at the ground with the spade."]
  ] as const;
  for (const [action, arguments_, text] of commands) {
    const story = buildStory([
      event(1, "observation", observation(["navigator", "provisions", "ground", "spade"])),
      event(2, "action_request", { request_id: "voyage", action_id: action, arguments: arguments_ })
    ]);
    assert.equal(story[1].text, text);
  }
});

test("story reads from observation to attempt to confirmed outcome without hidden facts", () => {
  const events = [
    event(1, "started", { goal: { objective: "Leave the room." } }),
    event(2, "observation", observation()),
    event(3, "action_request", { request_id: "r1", action_id: "open", arguments: { target: "door" } }),
    event(4, "action_result", { request_id: "r1", outcome: "REJECTED", explanation: "The door is locked.", observation: observation() }),
    event(5, "observation", observation()),
    event(6, "action_request", { request_id: "r2", action_id: "open", arguments: { target: "box" } }),
    event(7, "action_result", { request_id: "r2", outcome: "APPLIED", explanation: "You open the box.", observation: observation(["door", "box", "rock", "key"]) }),
    event(8, "finished", { status: "succeeded", reason: "Verified." })
  ];
  const story = buildStory(events);
  assert.equal(story[1].text, "A room. I can see the door, the box and the rock.");
  assert.equal(story[2].text, "I try opening the door.");
  assert.equal(story[3].text, "The door is locked.");
  assert.equal(story[5].text, "I open the box. I discover the brass key.");
  assert.ok(story.slice(0, 5).every(entry => !entry.text.includes("key")));
  assert.equal(story.length, 7);
});

test("replay is chronological and deduplicated and other actors do not leak", () => {
  const first = event(1, "observation", observation());
  const second = event(2, "observation", observation(["secret"]), "another-npc");
  assert.deepEqual(buildStory([second, first, first], "npc"), buildStory([first], "npc"));
});

test("invalid proposals are not narrated as attempted actions", () => {
  const story = buildStory([
    event(1, "observation", observation()),
    event(2, "decision", { action_id: "invented", justification: "Invalid idea." }),
    event(3, "validation", { valid: false, error: "Unavailable." }),
    event(4, "proxy_request", { messages: [] })
  ]);
  assert.equal(story.length, 1);
});

test("damage, breakage and failure remain facts rather than imagined victory", () => {
  const story = buildStory([
    event(1, "observation", observation(["door", "lock", "rock"])),
    event(2, "action_request", { request_id: "hit", action_id: "hit", arguments: { target: "lock", item: "rock" } }),
    event(3, "action_result", { request_id: "hit", outcome: "APPLIED", explanation: "You hit the lock with the rock. The lock breaks.", observation: observation(["door", "lock", "rock"]) }),
    event(4, "finished", { status: "blocked", reason: "The broken lock permanently blocks the exit." })
  ]);
  assert.equal(story[1].text, "I try hitting the lock with the rock.");
  assert.match(story[2].text, /^I hit/);
  assert.match(story[3].text, /cannot complete my goal/);
  assert.ok(story.every(entry => !entry.text.includes("achieved")));
});

test("unknown commands preserve their actual arguments and text is not interpreted as HTML", () => {
  const story = buildStory([
    event(1, "observation", observation()),
    event(2, "action_request", { request_id: "nap", action_id: "nap", arguments: { duration: 2 } }),
    event(3, "action_result", { request_id: "nap", outcome: "APPLIED", explanation: "<script>alert('world')</script>", observation: observation() })
  ]);
  assert.match(story[1].text, /nap.*duration.*2/);
  assert.equal(story[2].text, "<script>alert('world')</script>");
});

test("entering the second room is narrated before discoveries without declaring victory", () => {
  const arrival = observation(["door-one", "door-two"], "room-two");
  arrival.narrative = "You are in a second stone room.";
  const story = buildStory([
    event(1, "observation", observation(["door-one"], "room-one")),
    event(2, "action_request", { request_id: "go", action_id: "go", arguments: { target: "door-one" } }),
    event(3, "action_result", {
      request_id: "go", outcome: "APPLIED", explanation: "You go through the first door.", observation: arrival
    })
  ]);
  assert.equal(story[2].text, "I go through the first door. I am in a second stone room. I discover the door-two.");
  assert.ok(story.every(entry => !/achieved|escaped|outside/.test(entry.text)));
});

test("breaking the box reveals keys and subsequent intentions use its new name", () => {
  const broken = observation(["door", "box", "rock", "silver-key", "brass-key"]);
  broken.visible_entities.find(entity => entity.id === "box")!.name = "Broken box";
  broken.visible_entities.find(entity => entity.id === "silver-key")!.name = "Silver key";
  broken.visible_entities.find(entity => entity.id === "brass-key")!.name = "Brass key";
  const story = buildStory([
    event(1, "observation", observation()),
    event(2, "action_result", {
      request_id: "hit", outcome: "APPLIED",
      explanation: "You hit the box with the rock. It is now the broken box.", observation: broken
    }),
    event(3, "action_request", { request_id: "take", action_id: "take", arguments: { target: "box" } })
  ]);
  assert.ok(!story[0].text.includes("key"));
  assert.match(story[1].text, /broken box.*discover the silver key and the brass key/);
  assert.equal(story[2].text, "I try to pick up the broken box.");
});

test("the opening starts with waking up before the spoiler-free goal", () => {
  const initial = {
    ...observation(), opening: true,
    narrative: "You wake up on a cold stone floor, with no memory of how you got here."
  };
  const story = buildStory([
    event(1, "started", { goal: { objective: "Find a way out." } }),
    event(2, "observation", initial),
    event(3, "observation", initial),
    event(4, "observation", observation())
  ]);
  assert.equal(story[0].text, "I wake up on a cold stone floor, with no memory of how I got here.");
  assert.equal(story[1].text, "My goal is to find a way out.");
  assert.equal(story.filter(entry => entry.text.includes("wake up")).length, 1);
});

test("complete room descriptions are not mistaken for noun phrases", () => {
  const description = "A broken lift sits below a high balcony. A heavy beam lies nearby.";
  const initial = { ...observation([]), narrative: description };
  const text = buildNarrative([event(1, "observation", initial)])[0].text;
  assert.ok(text.startsWith(description));
  assert.doesNotMatch(text, /I am in a broken lift/);
});

test("authored team openings preserve paragraphs without appending an inventory dump", () => {
  const narrative = "Raven and Badger stand beneath a high balcony.\n\nNow they need to find that pin.";
  const initial = { ...observation(), role: "A flying raven.", opening: true, narrative };
  const story = buildStory([
    event(1, "started", { goal: { objective: "Restore the lift together." } }, "raven"),
    event(2, "observation", initial, "raven"),
    event(3, "observation", initial, "raven")
  ], "raven");
  assert.equal(story[0].text, narrative);
  assert.equal(story[0].details, undefined);
  assert.equal(story.filter(entry => entry.text.includes("find that pin")).length, 1);
  assert.equal(buildNarrative([event(1, "observation", initial, "raven")], "raven")[0].text, narrative);
});

test("continuous narrative combines recorded reasoning, attempts and results without invented outcomes", () => {
  const decision = { ...event(3, "decision", {
    action_id: "open", justification: "I need to test the door before exploring",
    expected_outcome: "The door opens and I escape."
  }), cycle: 1 };
  const request = { ...event(4, "action_request", { request_id: "open", action_id: "open", arguments: { target: "door" } }), cycle: 1 };
  const result = { ...event(5, "action_result", {
    request_id: "open", outcome: "REJECTED", explanation: "The door is locked.", observation: observation()
  }), cycle: 1 };
  const entries = [event(1, "observation", observation()), decision, request, result];
  const narrative = buildNarrative(entries, "npc");
  assert.equal(narrative[1].text, "I need to test the door before exploring. I try opening the door. The door is locked.");
  assert.match(narrative[0].text, /carry at most 1 item/);
  assert.ok(narrative.every(paragraph => !/escape|key/.test(paragraph.text)));
  assert.deepEqual(buildNarrative([...entries].reverse().concat(request), "npc"), narrative);
});

test("narrative includes new visible descriptions only after discovery and excludes other actors", () => {
  const after = observation(["door", "box", "rock", "key"]);
  after.visible_entities.find(entity => entity.id === "key")!.description = "A small brass key, warm to the touch.";
  const narrative = buildNarrative([
    event(1, "observation", observation()),
    event(2, "observation", observation(["secret"]), "another-npc"),
    event(3, "action_result", {
      request_id: "box", outcome: "APPLIED", explanation: "You open the box.", observation: after
    })
  ], "npc");
  assert.ok(!narrative[0].text.includes("key"));
  assert.match(narrative[1].text, /small brass key, warm to the touch/);
  assert.ok(narrative.every(paragraph => !paragraph.text.includes("secret")));
});

test("narrative keeps capacity but omits per-item inventory-unit bookkeeping", () => {
  const initial = observation(["rock"]);
  initial.self.body.carrying.rule = "inventory_units";
  const observed = { ...initial, visible_entities: initial.visible_entities.map(entity => ({ ...entity, inventory_units: 2 })) };
  const original = JSON.stringify(observed);
  const narrative = buildNarrative([event(1, "observation", observed)]);
  assert.match(narrative[0].text, /at most 1 inventory unit/);
  assert.doesNotMatch(JSON.stringify(buildStory([event(1, "observation", observed)])), /takes \d+ inventory units?/);
  assert.doesNotMatch(narrative[0].text, /takes \d+ inventory units?/);
  assert.ok(!narrative[0].text.includes("key"));
  assert.equal(JSON.stringify(observed), original);
});

test("archived and copied prose omits item costs without changing reviewed evidence", async () => {
  const events = [event(1, "observation", observation(["arbor"]))];
  const reviewed = [
    { id: "run:1", text: "A soft arbor lies nearby. The soft arbor takes 1 inventory unit. I examine it." },
    { id: "legacy:2", text: "The relay takes 2 inventory units." }
  ];
  const original = JSON.stringify(reviewed);
  const rendered = buildNarrative(events, "npc", reviewed);
  assert.deepEqual(rendered, [{ id: "run:1", text: "A soft arbor lies nearby. I examine it." }]);
  let copied = "";
  await copyStory(rendered, { writeText: async value => { copied = value; } });
  assert.equal(copied, rendered[0].text);
  assert.equal(JSON.stringify(reviewed), original);
});

test("team narration interleaves all actors without sharing names or observation state", () => {
  const raven = { ...observation(["key"]), role: "A scout." };
  const badger = { ...observation(["box"], "quarry"), role: "A builder." };
  const otter = { ...observation(["rope"], "landing"), role: "A swimmer." };
  const events = [
    event(1, "observation", raven, "raven"),
    event(2, "observation", badger, "badger"),
    event(3, "observation", otter, "otter"),
    event(4, "action_request", { request_id: "r", action_id: "take", arguments: { target: "key" } }, "raven"),
    event(5, "action_request", { request_id: "b", action_id: "take", arguments: { target: "key" } }, "badger"),
    event(6, "action_request", { request_id: "o", action_id: "take", arguments: { target: "rope" } }, "otter"),
    event(7, "finished", { status: "succeeded" }, "raven")
  ];
  const original = JSON.stringify(events);
  const story = buildStory([...events].reverse().concat(events[0]));
  assert.deepEqual(story.map(entry => entry.actorId), ["raven", "badger", "otter", "raven", "badger", "otter", "raven"]);
  assert.equal(story[3].text, "Raven tries to pick up the brass key.");
  assert.equal(story[4].text, "Badger tries to pick up the key.");
  assert.match(story[1].details!.join(" "), /Badger can carry/);
  assert.doesNotMatch(buildStory(events, "badger").map(entry => entry.text).join(" "), /brass|rope/);
  const narrative = buildNarrative(events);
  assert.deepEqual(narrative.map(paragraph => paragraph.id), events.map(event => event.id));
  assert.match(narrative.at(-1)!.text, /we have achieved our goal/);
  assert.equal(JSON.stringify(events), original);
});

test("the shared team opening appears once and actor-local views retain it", () => {
  const opening = { ...observation([]), role: "A teammate.", opening: true,
    narrative: "Three friends face a storm.\n\nThey must work together." };
  const events = ["raven", "badger", "otter"].map((actor, index) =>
    event(index + 1, "observation", opening, actor));
  assert.equal(buildNarrative(events).length, 1);
  for (const actor of ["raven", "badger", "otter"]) {
    assert.equal(buildNarrative(events, actor)[0].text, opening.narrative);
  }
});

test("archived team adventure and copies combine all actors in event order", async () => {
  const events = [
    event(1, "observation", { ...observation([]), role: "A scout." }, "raven"),
    event(2, "observation", { ...observation([]), role: "A builder." }, "badger"),
    event(3, "observation", { ...observation([]), role: "A swimmer." }, "otter")
  ];
  const reviewed = [
    { id: "run:3", text: "otter: I swim through the channel." },
    { id: "run:1", text: "raven: I fly to the yard." },
    { id: "run:2", text: "badger: I carry the weight." }
  ];
  const original = JSON.stringify(reviewed);
  const result = buildNarrative(events, undefined, reviewed);
  assert.deepEqual(result.map(paragraph => paragraph.id), ["run:1", "run:2", "run:3"]);
  let copied = "";
  await copyStory(result, { writeText: async value => { copied = value; } });
  assert.match(copied, /Raven flies[\s\S]*Badger carries[\s\S]*Otter swims/);
  assert.equal(JSON.stringify(reviewed), original);
});
