import assert from "node:assert/strict";
import { test } from "node:test";
import { episodeLoadRequestSchema, episodeSaveRequestSchema, episodeSavesSchema } from "../src/contracts.ts";
import { savedEpisodesMarkup } from "../src/episode-saves.ts";

const save = {
  id: "a".repeat(32), name: '<script>alert("save")</script>', saved_at: "2026-11-07T13:00:00Z",
  source_run_id: "b".repeat(32), title: "Millhaven & friends", scenario_id: "millhaven-living",
  status: "paused", actions: 123, model_calls: 124, resumable: true,
  village_minute: 1440, simulation_seconds: null
};

test("named save requests reject blank names and unsafe identities", () => {
  assert.equal(episodeSaveRequestSchema.parse({run_id: "a".repeat(32), name: "  Evening  "}).name, "Evening");
  for (const body of [
    {run_id: "../outside", name: "Save"}, {run_id: "a".repeat(32), name: " "},
    {run_id: "a".repeat(32), name: "x".repeat(121)}
  ]) assert.equal(episodeSaveRequestSchema.safeParse(body).success, false);
  assert.equal(episodeLoadRequestSchema.safeParse({save_id: "../outside", current_run_id: null}).success, false);
  assert.equal(episodeLoadRequestSchema.safeParse({save_id: "a".repeat(32), current_run_id: null}).success, true);
});

test("save list shows exact village time, copy behavior and escaped names", () => {
  const saves = episodeSavesSchema.parse({saves: [save]}).saves;
  const markup = savedEpisodesMarkup(saves);
  assert.ok(markup.includes("00:00 on day 2"));
  assert.ok(markup.includes("Load paused copy to resume or ask NPCs"));
  assert.ok(markup.includes("Millhaven &amp; friends"));
  assert.ok(!markup.includes("<script>"));
  assert.ok(markup.includes(`data-load-save="${save.id}"`));
});

test("ended saves offer questions rather than promise resumption", () => {
  const markup = savedEpisodesMarkup([{...save, status: "succeeded", resumable: false, village_minute: null}]);
  assert.ok(markup.includes("Load copy for NPC questions"));
  assert.ok(!markup.includes("Load paused copy to resume"));
  assert.ok(savedEpisodesMarkup([]).includes("No named save points"));
});
