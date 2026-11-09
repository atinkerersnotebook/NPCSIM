import type { EpisodeSaveSummary } from "./contracts";
import { escape } from "./html.ts";
import { villageClockTime } from "./view-mode.ts";

export function savedEpisodesMarkup(saves: EpisodeSaveSummary[]): string {
  if (!saves.length) return '<p class="empty">No named save points yet. Use Save episode to create one.</p>';
  return saves.map(save => {
    const clock = save.village_minute !== null ? ` · ${villageClockTime(save.village_minute)}` :
      save.simulation_seconds !== null ? ` · ${save.simulation_seconds.toFixed(1)} game seconds` : "";
    return `<button class="history-run" data-load-save="${escape(save.id)}">
      <strong>${escape(save.name)}</strong>
      <span>${escape(save.title)}${escape(clock)} · ${save.actions} actions · ${escape(save.status)}</span>
      <small>${escape(new Date(save.saved_at).toLocaleString())} · ${save.resumable ? "Load paused copy to resume or ask NPCs" : "Load copy for NPC questions"}</small>
    </button>`;
  }).join("");
}
