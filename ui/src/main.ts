import { z } from "zod";
import {
  stateSchema, eventSchema, eventsSchema, modelsSchema, scenariosSchema, historySchema, historyDetailSchema,
  agentsSchema, agentProfileSchema, skillModeSchema, dungeonMasterConfigSchema, dungeonMasterRulingSchema,
  completionAnalysisSchema, npcChatSchema
} from "./contracts";
import type { AgentProfile, CompletionAnalysis, DungeonMasterConfig, SkillMode, State, RunEvent } from "./contracts";
import { effectiveBody, profileText, slotLabel, validateAssignments } from "./agent-profiles";
import { evidenceReferences, explainEvidence } from "./evidence";
import { buildNarrative, buildStory, copyStory, isTeamStory } from "./story";
import type { StoryEntry, StoryMembers } from "./story";
import { actorPerspective, agentPerspectives, discoveredEntities, goalPresentation, inventoryUnitsUsed, locationNetwork, nutritionLabel, teamLocationNetwork } from "./world-view";
import { batchTiming, clockHelp, clockTime } from "./bakery-view";
import { villageMarkup } from "./village-view";
import { adventureStatus, isAdminView, playerCommandVisible } from "./view-mode";
import { conditionText } from "./secondary-goals";
import "./style.css";

const app = document.querySelector<HTMLDivElement>("#app");
if (!app) throw new Error("Missing application root");
const adminView = isAdminView(location.search);
app.className = adminView ? "admin-view" : "player-view";
document.title = adminView ? "NPCSIM · World Observatory" : "NPCSIM · Adventures";

app.innerHTML = `
  <aside class="rail" data-admin-only>
    <a class="brand" href="/" aria-label="NPCSIM home">N<span>·</span></a>
    <div class="rail-icon active" title="World observatory">◈</div>
    <button class="rail-icon" id="history-button" title="Episode history" aria-label="Episode history">↺</button>
    <a class="rail-icon" href="/api/transcript" title="Export JSON transcript" aria-label="Export transcript">↓</a>
    <span class="rail-bottom">V.01</span>
  </aside>
  <main id="observatory">
    <header>
      <div><div class="eyebrow">NPCSIM / ${adminView ? "LOCAL SIMULATION" : "CHARACTERS WITH A MIND OF THEIR OWN"}</div><h1>${adminView ? "World observatory" : "Every adventure has a story"}<span class="dot">.</span></h1></div>
      <div class="header-right"><span class="live-dot"></span><span id="connection">Connecting</span><span class="tag">${adminView ? "ADMIN VIEW" : "PLAYER VIEW"}</span><a id="view-switch" class="button subtle" href="${adminView ? "/" : "/?view=admin"}">${adminView ? "Player view" : "Admin view"}</a></div>
    </header>
    <div id="notice" class="notice" role="alert" hidden></div>
    <section class="toolbar">
      <div class="episode"><label class="eyebrow" for="scenario-select">SCENARIO</label><select id="scenario-select" aria-label="Choose scenario"><option value="">Loading scenarios...</option></select><div id="episode-name">No episode yet</div></div>
      <div class="controls" id="controls">
        <button data-command="start" class="button subtle">＋ ${adminView ? "New episode" : "New adventure"}</button>
        <button data-command="step" class="button" data-admin-only>Step →</button>
        <button id="run-command" data-command="run" class="button primary">▶ Run agent</button>
        <button data-command="pause" class="button">Ⅱ Pause</button>
        <button data-command="resume" class="button">↗ ${adminView ? "Resume" : "Continue"}</button>
        <button data-command="reset" class="button subtle">↺ ${adminView ? "Reset" : "Restart"}</button>
      </div>
    </section>
    <section class="panel agent-setup">
      <div class="panel-heading"><div><span class="eyebrow">${adminView ? "CAST / TASK ASSIGNMENTS" : "YOUR NEXT ADVENTURE"}</span><h2>${adminView ? "Choose your agents" : "Choose your cast"}</h2></div><span class="tag">${adminView ? "NEXT EPISODE" : "NEXT ADVENTURE"}</span></div>
      <p class="timeline-note">${adminView ? "Each agent brings its own personality, rules and physical abilities. Slots retain their task starting locations, not their original character's skills." : "Choose a world and its characters, then click New adventure and Play. Each character brings their own personality and abilities. Your choices apply to the next adventure."}</p>
      <details class="adventure-options" ${adminView ? "open" : ""}><summary>Skills & challenge options</summary>
      <div class="agent-editor">
        <label for="skill-mode">Skills policy</label>
        <select id="skill-mode"><option value="scenario">Scenario-specific profile skills (check required skills)</option><option value="npc_only">NPC skills only (allow impossible tasks)</option></select>
        <p id="skill-mode-help" class="muted" role="status"></p>
      </div>
      </details>
      <div id="agent-assignments" class="agent-assignment-grid"></div>
      <details class="inspector">
        <summary>Edit individual agent configurations</summary>
        <div class="agent-editor">
          <label for="profile-select">Agent profile</label><select id="profile-select" aria-label="Choose profile to edit"></select>
          <h3>Live Markdown configuration</h3>
          <p class="muted">Each NPC has its own template folder: identity, soul, operating guidance, operator expectations, authored background memory, alignment, proactive preferences and relationships. Changes affect new adventures only. Markdown cannot grant abilities or access to other NPCs' private state.</p>
          <label for="agent-document-select">Markdown file</label><select id="agent-document-select" aria-label="Choose agent Markdown file"></select>
          <p id="agent-document-path" class="muted"></p>
          <label for="agent-document-text">Document content (Markdown)</label><textarea id="agent-document-text" rows="16" spellcheck="false"></textarea>
          <button id="save-agent-document" class="button">Save Markdown document</button><span id="agent-document-message" role="status"></span>
          <h3>Machine settings & legacy summaries</h3>
          <p class="muted">JSON defines enforceable body abilities, size, grips, held-object limits, carrying limits and scenario_bodies. Markdown is the live persona; JSON identity/personality/rules remain legacy summaries. Editing those summaries updates only untouched default documents; edit customized guidance above. Change the ID to save a new agent.</p>
          <label for="profile-json">Agent machine configuration (JSON)</label><textarea id="profile-json" rows="20" spellcheck="false"></textarea>
          <button id="save-agent" class="button">Save agent configuration</button><span id="profile-message" role="status"></span>
        </div>
      </details>
    </section>
    <section class="panel agent-setup" id="dungeon-master-panel" data-admin-only>
      <div class="panel-heading"><div><span class="eyebrow">SUPERVISOR / NOT AN NPC</span><h2 id="dungeon-master-name">Dungeon Master</h2></div><span class="tag" id="dungeon-master-status">HYBRID / AI + WORLD</span></div>
      <p class="timeline-note">The world adjudicates every action. The Dungeon Master reviews the whole team's observed progress after each round and can declare defeat with cited evidence. Its privileged view is never shared with NPCs.</p>
      <div id="dungeon-master-rulings" class="dm-rulings" aria-label="Dungeon Master rulings" aria-live="polite"></div>
      <details class="inspector"><summary>Edit supervisor configuration (next episode)</summary>
        <div class="agent-editor"><p class="muted">Rename it Oracle, Overseer or Dungeon Master. Configure its personality, rules, capabilities or enabled status. Current episodes keep their frozen configuration. AI reviews use the selected proxy model and incur additional calls and episode time.</p>
          <label for="dungeon-master-json">Supervisor configuration (JSON)</label><textarea id="dungeon-master-json" rows="16" spellcheck="false"></textarea>
          <button id="save-dungeon-master" class="button">Save supervisor configuration</button><span id="dungeon-master-message" role="status"></span>
        </div>
      </details>
    </section>
    <section class="panel bakery-panel" id="bakery-panel" hidden>
      <div class="panel-heading"><div><span class="eyebrow">LIVE SIMULATION</span><h2>Bakery clock & batches</h2></div><span class="tag" id="clock-status"></span></div>
      <div class="bakery-clock">
        <div><span class="eyebrow">BAKERY TIME</span><strong id="bakery-time">00:00:00</strong></div>
        <div data-admin-only><span class="eyebrow">REAL CLOCK-RUNNING TIME</span><strong id="bakery-real-time">00:00:00</strong></div>
        <div data-admin-only><label for="clock-mode">Clock policy</label><select id="clock-mode"><option value="decision_paused">Pause during decisions (recommended)</option><option value="continuous">Continuous clock (challenge)</option></select></div>
        <div data-admin-only><label for="clock-rate">Time multiplier (1-600x)</label><input id="clock-rate" type="number" min="1" max="600" step="any" value="120" /><button id="save-clock" class="button">Apply clock settings</button></div>
      </div>
      <p class="timeline-note" id="clock-help" data-admin-only></p>
      <div class="batch-grid" id="bread-batches" aria-label="Bread batch timers"></div>
    </section>
    <section class="metrics" id="metrics"></section>
    <section id="team-overview" class="team-overview" aria-label="Team overview" hidden>
      <div class="team-left">
        <div id="team-map-host"></div>
      </div>
      <section class="team-live" id="team-live" aria-label="Live team">
        <div id="team-review-host"></div>
        <div class="team-perspectives-content">
        <div class="team-perspectives-heading"><div><span class="eyebrow">${adminView ? "LIVE TEAM / SEPARATE PERSPECTIVES" : "ON THE ADVENTURE"}</span><h2>${adminView ? "All agent perspectives" : "Your characters"}</h2></div><span class="tag green" data-admin-only>OBSERVATIONS STAY PRIVATE</span></div>
        <p class="timeline-note" data-admin-only>Watch every agent's surroundings, task focus and actions together. Turns remain sequential; private observations are not shared with teammates.</p>
        <div id="agent-perspectives" class="agent-perspectives" aria-label="Agent perspectives"></div>
        </div>
      </section>
      <div id="team-story-host"></div>
    </section>
    <div class="workspace" id="workspace" data-admin-only>
      <div class="left-column" id="left-column">
        <section class="panel world-panel">
          <div class="panel-heading"><div><span class="eyebrow">01 / DISCOVERED</span><h2 id="world-title">The world</h2></div><span id="map-scope" class="tag green">DISCOVERED MAP</span></div>
          <div id="world-scene"></div>
          <div class="world-footer"><span><i class="legend-dot"></i> ${adminView ? "Actor" : "Character"}</span><span><i class="legend-dot orange"></i> Interactable</span><span>${adminView ? "Undiscovered objects stay hidden; full JSON is privileged" : "Only discovered places and objects appear here."}</span></div>
          <details class="inspector"><summary>Inspect full world JSON <span id="world-revision"></span></summary><pre id="world-json"></pre></details>
        </section>
        <section class="panel self-panel" id="self-panel" data-admin-only>
          <div class="panel-heading"><div><span class="eyebrow">02 / EMBODIMENT</span><h2>Self & constraints</h2></div><select id="actor-select" aria-label="Select actor"></select></div>
          <div id="self-content"></div>
        </section>
        <section class="panel proxy-panel" id="connection-panel" data-admin-only>
          <div class="panel-heading"><div><span class="eyebrow">CONNECTION</span><h2>LLM proxy</h2></div><span id="proxy-status" class="tag">NOT CHECKED</span></div>
          <label for="proxy-url">OpenAI-compatible base URL</label><input id="proxy-url" value="http://127.0.0.1:9901/v1" />
          <div class="model-row"><div><label for="model-select">Decision model</label><select id="model-select"><option value="">Discover a model</option></select></div><button id="discover" class="button">Discover</button></div>
          <div class="proxy-actions"><span>No direct-provider fallback</span><button id="save-proxy" class="button">Save settings</button></div>
          <p id="proxy-message" class="muted"></p>
        </section>
      </div>
      <div class="right-column" id="right-column">
        <section class="panel observation-panel" id="observation-panel">
          <div class="panel-heading"><div><span class="eyebrow">03 / NPC PERSPECTIVE</span><h2>What the agent sees</h2></div><span class="tag green">OBSERVED ONLY</span></div>
          <div id="observation-content"></div>
        </section>
        <section class="panel story-panel" id="story-panel">
          <div class="panel-heading"><div><span class="eyebrow">04 / THE ADVENTURE</span><h2>The NPC's story</h2></div><span class="tag green">FIRST PERSON</span></div>
          <p class="timeline-note">Observations, attempted actions and actual outcomes, in reading order.</p>
          <div id="story" class="story-feed" aria-label="NPC story"></div>
        </section>
        <section class="panel timeline-panel" id="technical-log" data-admin-only>
          <div class="panel-heading"><div><span class="eyebrow">05 / TECHNICAL LOG</span><h2>Decisions & interactions <span id="event-count" class="count">0</span></h2></div><a class="text-link" href="/api/transcript">Export JSON ↗</a></div>
          <p class="timeline-note">Recorded plans, concise explanations and confirmed actions — not private model chain-of-thought.</p>
          <div class="timeline-tools"><select id="event-filter" aria-label="Filter events"><option value="all">All interactions</option><option value="decision">Decisions</option><option value="action_result">Action results</option><option value="error">Errors & validation</option><option value="proxy_request">Proxy requests</option><option value="verification">Goal verification</option></select><span id="timeline-status">Waiting for an episode</span></div>
          <div id="timeline"></div>
        </section>
      </div>
      <div class="narrative-column" id="narrative-column">
        <section class="panel narrative-panel">
          <div class="panel-heading"><div><span class="eyebrow">${adminView ? "06 / THE STORY" : "THE STORY SO FAR"}</span><h2>An unexpected adventure</h2></div><button id="copy-story" class="button subtle" disabled>Copy final story</button></div>
          <p class="narrative-note" id="narrative-note">A continuous account of what the NPC discovers, attempts and explains.</p>
          <div id="narrative" class="narrative-prose" aria-label="Adventure narrative"></div>
        </section>
        <section class="panel completion-panel" id="completion-panel">
          <div class="panel-heading"><div><span class="eyebrow">${adminView ? "07 / COMPLETION REVIEW" : "THE OUTCOME"}</span><h2>${adminView ? "Finished is not always successful" : "How did it turn out?"}</h2></div><button class="button subtle" data-command="assess" data-admin-only>Reassess story</button></div>
          <div id="completion-analysis" aria-live="polite"></div>
        </section>
        <section class="panel interaction-panel" aria-labelledby="npc-chat-heading">
          <h2 id="npc-chat-heading">Ask an NPC</h2>
          <p>Private conversation. Only the selected NPC remembers it, and answers from its own knowledge. No world actions are performed.</p>
          <form id="npc-chat-form">
            <label for="chat-actor">Speak privately with</label>
            <select id="chat-actor"></select>
            <label for="npc-question">Your question</label>
            <textarea id="npc-question" rows="3" maxlength="2000" required placeholder="What have you tried? What might you search next?"></textarea>
            <button type="submit" class="button">Ask privately</button>
          </form>
          <div id="npc-chat-history" class="interaction-chat" aria-live="polite"></div>
        </section>
        <section class="panel interaction-panel" aria-labelledby="world-edit-heading">
          <h2 id="world-edit-heading">Dungeon Master intervention</h2>
          <p>Describe a world change or side goal in plain English. Preview and approve it for this adventure only; the main objective stays unchanged.</p>
          <form id="world-edit-form">
            <label for="world-instruction">Proposed world change or secondary goal</label>
            <textarea id="world-instruction" rows="4" maxlength="4000" required placeholder="Give Otter a side goal to bring the bowl to Badger. Or add a concealed wall nook with a spare door key inside."></textarea>
            <button type="submit" class="button">Preview change</button>
          </form>
          <p class="timeline-note">Questions and proposals use the adventure's model-call budget. Interacting pauses play at a safe point; resume when ready.</p>
          <div id="world-edit-preview" aria-live="polite"></div>
          <h3>Side goals</h3>
          <p class="timeline-note">Optional tasks. All listed conditions must hold together; achieved goals stay achieved. The main objective still ends the adventure normally.</p>
          <div id="secondary-goals" aria-live="polite"></div>
        </section>
      </div>
    </div>
    <footer><span>${adminView ? "WORLD → OBSERVE → DECIDE → ACT → VERIFY" : "Their skills. Their choices. Their story."}</span><span data-admin-only>Python world · MCP actions · TypeScript UI · JSON persistence</span></footer>
  </main>
  <dialog id="history-dialog"><div class="dialog-heading"><h2>Episode archive</h2><button id="close-history" class="button">Close</button></div><div id="history-content"></div></dialog>
`;

let state: State | null = null;
let events: RunEvent[] = [];
let activeRun: string | null = null;
let busy = false;
let interactionBusy = false;
let chatOptions = "";
let chatMarkup = "";
let proposalMarkup = "";
let goalMarkup = "";
const dismissedProposals = new Set<string>();
let configured = false;
let filter = "all";
let selectedActor = "";
let selectedMapLocation = "";
let selectedScenario = "";
let profiles: AgentProfile[] = [];
let scenarioChoices: z.infer<typeof scenariosSchema>["scenarios"] = [];
let assignments: Record<string, string> = {};
let skillMode: SkillMode = "scenario";
let dungeonMasterConfig: DungeonMasterConfig | null = null;
let archivedStory: { text: string }[] = [];
const expanded = new Set<string>();

function element<T extends HTMLElement>(id: string): T {
  const node = document.getElementById(id);
  if (!node) throw new Error(`Missing ${id}`);
  return node as T;
}
function escape(value: unknown): string {
  return String(value ?? "").replace(/[&<>"']/g, char => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[char] ?? char));
}
function pretty(value: unknown): string { return JSON.stringify(value, null, 2); }
function record(value: unknown): Record<string, unknown> {
  return z.record(z.string(), z.unknown()).parse(value);
}
function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
function notice(message: string): void {
  const node = element<HTMLDivElement>("notice");
  node.textContent = message;
  node.hidden = !message;
}
async function api(path: string, method = "GET", body?: unknown): Promise<unknown> {
  const response = await fetch(`/api${path}`, {
    method, headers: { "Content-Type": "application/json" },
    ...(body === undefined ? {} : { body: JSON.stringify(body) })
  });
  const payload: unknown = await response.json();
  if (!response.ok) {
    const failure = z.object({ detail: z.unknown() }).safeParse(payload);
    throw new Error(failure.success ? String(failure.data.detail) : `HTTP ${response.status}`);
  }
  return payload;
}

function applyState(next: State): void {
  state = next;
  const id = next.run?.id ?? null;
  if (id !== activeRun) {
    activeRun = id;
    selectedActor = "";
    selectedMapLocation = "";
    events = [];
    dismissedProposals.clear();
    element<HTMLSelectElement>("chat-actor").value = "";
    expanded.clear();
    selectedScenario = next.run?.scenario_id ?? next.config.world.scenario;
    element<HTMLSelectElement>("scenario-select").value = selectedScenario;
    assignments = next.run?.agent_assignments ? { ...next.run.agent_assignments } : {};
    skillMode = next.run?.skill_mode ?? "scenario";
    renderAssignments();
    renderTimeline();
  }
  if (!configured) {
    element<HTMLInputElement>("proxy-url").value = next.config.proxy.base_url;
    if (next.config.proxy.model) {
      element<HTMLSelectElement>("model-select").innerHTML = `<option value="${escape(next.config.proxy.model)}">${escape(next.config.proxy.model)}</option>`;
    }
    configured = true;
  }
  element("episode-name").textContent = next.run ? (adminView ?
    `${next.run.title} / ${next.run.id.slice(0, 8)}${next.run.agents ? ` / ${Object.keys(next.run.agents).length} agents / active: ${next.run.actor_id}` : ""}` :
    `Current adventure: ${next.run.title}`) : adminView ? "Create an episode to begin" : "Create an adventure to begin";
  element<HTMLButtonElement>("run-command").textContent = adminView ? (next.run?.agents ? "▶ Run agents" : "▶ Run agent") : "▶ Play";
  renderMetrics();
  renderBakery();
  renderSelf();
  renderTeamPerspectives();
  renderWorld();
  renderObservation();
  renderCompletionAnalysis();
  renderNarrative();
  renderControls();
  renderDungeonMaster();
  renderInteractions();
  element("timeline-status").textContent = next.run?.completion_analysis?.status === "running" ? "Reviewing completed story…" :
    next.in_flight ? "Agent is deciding…" : (next.run?.status ?? "No active episode");
}

function renderMetrics(): void {
  if (!state) return;
  const run = state.run;
  const status = run?.status ?? "idle";
  if (!adminView) {
    element("metrics").innerHTML = `
      <div class="metric goal"><span class="eyebrow">${run?.goal.revealed === false ? "A TASK TO DISCOVER" : "THE QUEST"}</span><strong>${escape(run?.goal.objective ?? "A new world is waiting.")}</strong><span class="muted">${escape(run?.goal.status ?? "Choose a scenario and your cast to get started.")}</span></div>
      <div class="metric"><span class="eyebrow">YOUR ADVENTURE</span><strong class="status ${escape(status)}">${escape(adventureStatus(status, run?.completion_analysis))}</strong><span class="muted">${run ? `${run.actions} actions · ${run.elapsed_seconds.toFixed(1)} seconds${state.in_flight ? " · A character is taking a turn" : ""}` : "Create an adventure, then press Play."}</span></div>`;
    return;
  }
  const budgets = run?.config ? record(record(run.config).budgets) : state.config.budgets;
  element("metrics").innerHTML = `
    <div class="metric goal"><span class="eyebrow">${run?.goal.revealed === false ? "UNKNOWN TASK" : "ROOT GOAL"}</span><strong>${escape(run?.goal.objective ?? "Leave the room.")}</strong><span class="muted">${escape(run?.goal.status ?? "Waiting to start")}</span></div>
    <div class="metric"><span class="eyebrow">ATTEMPT STATUS</span><strong class="status ${escape(status)}">${run?.completion_analysis ? "finished" : escape(status.replaceAll("_", " "))}</strong><span class="muted">${run?.completion_analysis ? `Execution: ${escape(status.replaceAll("_", " "))} · Grade: ${escape(run.completion_analysis.verdict ?? run.completion_analysis.status)}` : state.in_flight ? "One cycle in progress" : "Sequential action loop"}</span></div>
    <div class="metric"><span class="eyebrow">ACTIONS / BUDGET</span><strong>${run?.actions ?? 0}<small> / ${escape(budgets.max_actions)}</small></strong><span class="muted">${run?.model_calls ?? 0} decision calls${run?.dungeon_master ? ` + ${run.dungeon_master.model_calls} supervisor` : ""}${run?.completion_analysis ? ` + ${run.completion_analysis.model_calls} review` : ""}</span></div>
    <div class="metric"><span class="eyebrow">ELAPSED</span><strong>${(run?.elapsed_seconds ?? 0).toFixed(1)}<small>s</small></strong><span class="muted">${escape(budgets.max_seconds)}s episode limit</span></div>`;
}

function renderAssignments(): void {
  element<HTMLSelectElement>("skill-mode").value = skillMode;
  element("skill-mode-help").textContent = skillMode === "npc_only" ?
    "Only each NPC's base abilities and carrying capacity apply. Scenario ability overrides are ignored. Missing skills are allowed: the task may be impossible, and blocked actions stay blocked." :
    "Use each profile's scenario-specific body when configured. Teams missing required challenge skills are rejected before starting.";
  const scenario = scenarioChoices.find(choice => choice.id === selectedScenario);
  if (!scenario || !profiles.length) return;
  for (const slot of scenario.agent_slots) assignments[slot.id] ||= slot.profile_id;
  element("agent-assignments").innerHTML = scenario.agent_slots.map((slot, index) => {
    const profile = profiles.find(profile => profile.id === assignments[slot.id]);
    const body = profile ? effectiveBody(profile, scenario.id, skillMode) : null;
    return `<article class="agent-assignment">
      <label for="assignment-${index}">${escape(slotLabel(slot.id))}${adminView ? ` / starts at ${escape(slot.start_location)}` : " role"}</label>
      <select id="assignment-${index}" data-agent-slot="${escape(slot.id)}" aria-label="Agent for ${escape(slotLabel(slot.id))}">
        ${profiles.map(profile => `<option value="${escape(profile.id)}" ${profile.id === assignments[slot.id] ? "selected" : ""}>${escape(profile.name)}${adminView ? ` (${escape(profile.id)})` : ""}</option>`).join("")}
      </select>
      <p>${escape(profile ? profileText(profile, "SOUL.md") : "Profile not found. Select an available agent.")}</p>
      ${body ? `<p class="muted" data-admin-only>${escape(body.description)}</p><p><strong>Abilities:</strong> ${escape(body.capabilities.map(skill => adminView ? skill : skill.replaceAll("_", " ")).join(", ") || "None")} / ${adminView ? "capacity" : "carry limit"} ${body.capacity}</p><p><strong>Body size:</strong> ${escape(body.size)} / <strong>Grips:</strong> ${escape(body.grips.join(", ") || "None")}${body.max_held_items !== null ? ` / at most ${body.max_held_items} held objects` : ""}. Objects must support these grips.</p>` : ""}
      <details><summary>Identity, rules & constraints</summary><p>${escape(profile ? profileText(profile, "IDENTITY.md") : "")}</p>
        <p class="agent-guidance">${escape(profile ? profileText(profile, "AGENTS.md") : "")}</p></details>
    </article>`;
  }).join("");
}

function renderProfileEditor(selectedId?: string): void {
  const select = element<HTMLSelectElement>("profile-select");
  const id = selectedId ?? select.value;
  select.innerHTML = profiles.map(profile => `<option value="${escape(profile.id)}">${escape(profile.name)} (${escape(profile.id)})</option>`).join("");
  select.value = profiles.some(profile => profile.id === id) ? id : profiles[0]?.id ?? "";
  const profile = profiles.find(profile => profile.id === select.value);
  element<HTMLTextAreaElement>("profile-json").value = profile ?
    pretty(Object.fromEntries(Object.entries(profile).filter(([key]) => key !== "documents"))) : "";
  renderDocumentEditor();
}

function renderDocumentEditor(): void {
  const profile = profiles.find(profile => profile.id === element<HTMLSelectElement>("profile-select").value);
  const select = element<HTMLSelectElement>("agent-document-select");
  const previous = select.value;
  const names = Object.keys(profile?.documents ?? {});
  select.innerHTML = names.map(name => `<option value="${escape(name)}">${escape(name)}</option>`).join("");
  select.value = names.includes(previous) ? previous : names[0] ?? "";
  element<HTMLTextAreaElement>("agent-document-text").value = profile?.documents[select.value] ?? "";
  element("agent-document-path").textContent = profile && select.value ?
    `data\\agents\\${profile.id}\\${select.value.replaceAll("/", "\\")}` : "No Markdown documents loaded.";
  element<HTMLButtonElement>("save-agent-document").disabled = !profile || !select.value;
  element("agent-document-message").textContent = "";
}

function dungeonMasterMarkup(source: readonly RunEvent[]): string {
  const rulings = source.filter(event => event.kind === "dungeon_master_ruling").slice(-20).reverse();
  if (!rulings.length) return '<p class="muted">No rulings yet. New episodes record action adjudications and round-by-round progress judgments here.</p>';
  return rulings.map(event => {
    const ruling = dungeonMasterRulingSchema.parse(event.payload);
    const title = { allowed: "Action allowed", rejected: "Action rejected", continue: "Continue", defeat: "Admit defeat" }[ruling.verdict];
    return `<article class="dm-ruling"><strong>${escape(ruling.name)}: ${title}</strong>
      <span class="tag ${ruling.verdict === "defeat" || ruling.verdict === "rejected" ? "amber" : "green"}">${ruling.source === "ai" ? "AI JUDGMENT" : "WORLD / VALIDATION"}</span>
      <p>${escape(ruling.reason)}</p><details><summary>Evidence / cycle ${event.cycle} / ${escape(event.actor_id)}</summary>${evidenceMarkup(ruling.evidence_ids, source)}</details></article>`;
  }).join("");
}

function evidenceMarkup(ids: readonly string[], source: readonly RunEvent[],
  story: readonly { id: string; text: string }[] = []): string {
  return `<div class="evidence-explanations">${explainEvidence(ids, source, story).map(evidence =>
    `<article class="evidence-explanation"><strong>${escape(evidence.title)}</strong>
      ${evidence.actorId !== undefined ? `<span class="muted"> / ${escape(evidence.actorId)} / cycle ${evidence.cycle}</span>` : ""}
      <p>${escape(evidence.explanation)}</p>
      ${evidence.details.length ? `<ul>${evidence.details.map(detail => `<li>${escape(detail)}</li>`).join("")}</ul>` : ""}
      <details class="evidence-reference"><summary>Technical reference</summary><code>${escape(evidence.id)}</code></details>
    </article>`).join("")}</div>`;
}

function renderDungeonMaster(): void {
  const active = state?.run?.dungeon_master;
  const config = active?.config ?? dungeonMasterConfig;
  element("dungeon-master-name").textContent = config?.name ?? "Dungeon Master";
  element("dungeon-master-status").textContent = active ? (config?.enabled ?
    config.capabilities.includes("review_progress") ? "HYBRID / AI + WORLD" : "WORLD RULES ONLY" : "DISABLED FOR EPISODE") :
    state?.run ? "LEGACY EPISODE / NO SUPERVISOR" : "NEXT EPISODE";
  updateReadingPane(element("dungeon-master-rulings"), dungeonMasterMarkup(events));
}

function renderBakery(): void {
  const world = state?.world;
  const clock = world?.clock;
  element("bakery-panel").hidden = !clock;
  if (!clock || !world || !state) return;
  element("bakery-time").textContent = clockTime(clock.seconds);
  element("bakery-real-time").textContent = clockTime(clock.real_seconds);
  const status = clock.running ? (clock.mode === "decision_paused" ? "WAITING" : "RUNNING") :
    state.run?.status === "running" && clock.mode === "decision_paused" ? "DECISION TIME PAUSED" : "FROZEN";
  element("clock-status").textContent = `${status} / ${clock.rate}x`;
  const input = element<HTMLInputElement>("clock-rate");
  const mode = element<HTMLSelectElement>("clock-mode");
  if (document.activeElement !== input) input.value = String(clock.rate);
  if (document.activeElement !== mode) mode.value = clock.mode;
  const terminal = !["ready", "running", "paused"].includes(state.run?.status ?? "");
  input.disabled = terminal || busy;
  mode.disabled = terminal || busy;
  element<HTMLButtonElement>("save-clock").disabled = terminal || busy;
  element("clock-help").textContent = clockHelp(clock);
  const perspective = actorPerspective(state, selectedActor);
  const actor = perspective.observation?.self.actor_id;
  if (!actor) return;
  const known = discoveredEntities(world, actor, perspective.observation, perspective.memory);
  const batches = Object.entries(known).filter(([id]) => /^batch-[abc]$/.test(id));
  element("bread-batches").innerHTML = batches.length ? batches.map(([id, entity]) => {
    const batch = world.batches?.[id] ?? {
      stage: "measuring" as const, ingredients: [], equipment: null, ready_at: null, deadline: null, failure: null
    };
    return `<div class="batch-card ${batch.stage === "ruined" ? "batch-ruined" : ""}">
      <strong>${escape(entity.name)}</strong><span class="tag">${escape(batch.stage.toUpperCase())}</span>
      <p>${escape(batchTiming(batch, clock.seconds))}</p>
      <small>${escape(world.entities[batch.equipment ?? entity.location ?? ""]?.name ?? "In bowl")}</small>
    </div>`;
  }).join("") : '<p class="muted">Discover the bowls to track individual batches.</p>';
}

function renderControls(): void {
  const status = state?.run?.status ?? "idle";
  const terminal = ["idle", "succeeded", "error", "blocked", "stalled", "cancelled", "budget_exceeded"].includes(status);
  document.querySelectorAll<HTMLButtonElement>("[data-command]").forEach(button => {
    const command = button.dataset.command;
    button.hidden = !adminView && !playerCommandVisible(command, status);
    button.disabled = busy || interactionBusy || Boolean(state?.in_flight) ||
      ((command === "step" || command === "run") && (terminal || status === "running")) ||
      (command === "pause" && status !== "running") ||
      (command === "resume" && status !== "paused") ||
      (command === "assess" && (!terminal || status === "idle"));
  });
  if (state?.run?.completion_analysis?.error) notice(state.run.completion_analysis.error);
  else if (state?.run?.reason) notice(state.run.reason);
}

function renderInteractions(): void {
  const run = state?.run;
  const enabled = Boolean(run && state?.world && ["ready", "paused", "running"].includes(run.status));
  const disabled = !enabled || busy || interactionBusy || Boolean(state?.in_flight);
  const selector = element<HTMLSelectElement>("chat-actor");
  const selected = selector.value || run?.actor_id || "";
  const options = Object.keys(state?.world?.actors ?? {}).map(id => {
    const name = run?.agent_profiles?.[id]?.name ?? slotLabel(id);
    return `<option value="${escape(id)}">${escape(name)}</option>`;
  }).join("");
  if (options !== chatOptions) {
    selector.innerHTML = options;
    chatOptions = options;
  }
  selector.value = selected;
  document.querySelectorAll<HTMLButtonElement | HTMLTextAreaElement | HTMLSelectElement>(
    "#npc-chat-form button, #npc-chat-form textarea, #npc-chat-form select, #world-edit-form button, #world-edit-form textarea"
  ).forEach(node => { node.disabled = disabled; });
  const chats = events.filter(event => event.kind === "npc_chat" && event.actor_id === selector.value);
  const nextChat = chats.length ? chats.map(event => {
    const chat = npcChatSchema.parse(event.payload);
    return `<article><strong>You</strong><p>${escape(chat.question)}</p><strong>NPC · private</strong><p>${escape(chat.answer)}</p></article>`;
  }).join("") : `<p class="timeline-note">${enabled ? "No private messages with this NPC yet." : "Start an active adventure to ask questions or propose changes."}</p>`;
  if (nextChat !== chatMarkup) {
    updateReadingPane(element("npc-chat-history"), nextChat);
    chatMarkup = nextChat;
  }
  const proposals = Object.values(run?.world_edit_proposals ?? {}).filter(proposal => !dismissedProposals.has(proposal.id));
  const entityNames = state?.world?.entities ?? {};
  const actorNames = run?.agent_profiles ?? {};
  const actorName = (id: string): string => actorNames[id]?.name ?? slotLabel(id);
  const checksMarkup = (conditions: Parameters<typeof conditionText>[0][], checks?: boolean[],
    names: Parameters<typeof conditionText>[1] = entityNames): string =>
    `<ul>${conditions.map((condition, index) => `<li>${checks ? (checks[index] ? "Complete: " : "Pending: ") : ""}${escape(conditionText(condition, names, actorNames))}</li>`).join("")}</ul>`;
  const nextProposals = proposals.map(proposal => {
    const stale = proposal.revision !== state?.observation?.observation_revision;
    return `<article class="intervention-preview">
      <h3>${proposal.status === "applied" ? "Approved change" : "Proposed change"}</h3>
      <p>${escape(proposal.plan.summary)}</p>
      <ul>${Object.entries(proposal.plan.additions).map(([id, entity]) =>
        `<li><strong>${escape(entity.name)}</strong> (${escape(entity.kind)}): ${escape(entity.description)}
        <br>Place: ${escape(entity.location ?? "new location")}${entity.destination ? ` → ${escape(entity.destination)}` : ""}
        ${entity.hidden ? `<br>Concealed until an NPC searches ${escape(entity.discovery_target)}.` : ""}
        <span class="timeline-note"> [${escape(id)}]</span></li>`).join("")}</ul>
      ${Object.entries(proposal.plan.key_bindings).map(([lock, key]) =>
        `<p>Spare key ${escape(key)} will also unlock ${escape(lock)}; the original key still works.</p>`).join("")}
      ${Object.values(proposal.plan.secondary_goals).map(goal => `<section>
        <h4>Side goal: ${escape(goal.title)}</h4><p>${escape(goal.description)}</p>
        <p>Assigned to: ${escape(goal.assigned_actor_ids.map(actorName).join(", "))}</p>
        <p>Complete when all these conditions hold:</p>
        ${checksMarkup(goal.conditions, undefined, { ...entityNames, ...proposal.plan.additions })}
      </section>`).join("")}
      <details><summary>Full proposed configuration</summary><pre>${escape(pretty(proposal.plan))}</pre></details>
      ${proposal.status === "pending" ? `<p>${stale ? "World changed since preview. Dismiss this and request a fresh proposal." : "No change has been applied yet."}</p>
        <button class="button" data-approve-edit="${escape(proposal.id)}" ${disabled || stale ? "disabled" : ""}>Approve change</button>
        <button class="button subtle" data-dismiss-edit="${escape(proposal.id)}" ${interactionBusy ? "disabled" : ""}>Dismiss</button>` : ""}
    </article>`;
  }).join("");
  if (nextProposals !== proposalMarkup) {
    updateReadingPane(element("world-edit-preview"), nextProposals);
    proposalMarkup = nextProposals;
  }
  const goals = Object.values(state?.world?.secondary_goals ?? {});
  const nextGoals = goals.length ? goals.map(goal => `<article class="intervention-preview">
    <h4>${escape(goal.title)} <span class="tag ${goal.status === "achieved" ? "green" : ""}">${goal.status === "achieved" ? "ACHIEVED" : "ACTIVE"}</span></h4>
    <p>${escape(goal.description)}</p><p>Assigned to: ${escape(goal.assigned_actor_ids.map(actorName).join(", "))}</p>
    ${checksMarkup(goal.conditions, goal.condition_met)}
    ${goal.status === "achieved" ? "<p>The world verified every completion condition.</p>" : ""}
  </article>`).join("") : '<p class="timeline-note">No side goals approved yet. Describe one above to get started.</p>';
  if (nextGoals !== goalMarkup) {
    updateReadingPane(element("secondary-goals"), nextGoals);
    goalMarkup = nextGoals;
  }
}

async function interact(operation: "chat" | "propose" | "approve", payload: Record<string, string>): Promise<void> {
  const runId = state?.run?.id;
  if (!runId || interactionBusy) return;
  interactionBusy = true;
  notice("");
  renderControls();
  renderInteractions();
  try {
    applyState(stateSchema.parse(await api(`/interactions/${operation}`, "POST", { run_id: runId, ...payload })));
    const recorded = eventsSchema.parse(await api("/events"));
    if (activeRun === runId) {
      events = recorded.events;
      renderTimeline();
    }
    if (operation === "chat") element<HTMLTextAreaElement>("npc-question").value = "";
  } catch (error) { notice(errorMessage(error)); }
  finally {
    interactionBusy = false;
    renderControls();
    renderInteractions();
  }
}

element("chat-actor").addEventListener("change", renderInteractions);
element("npc-chat-form").addEventListener("submit", event => {
  event.preventDefault();
  void interact("chat", { actor_id: element<HTMLSelectElement>("chat-actor").value,
    question: element<HTMLTextAreaElement>("npc-question").value });
});
element("world-edit-form").addEventListener("submit", event => {
  event.preventDefault();
  void interact("propose", { instruction: element<HTMLTextAreaElement>("world-instruction").value });
});
element("world-edit-preview").addEventListener("click", event => {
  const button = event.target instanceof Element ? event.target.closest<HTMLButtonElement>("button") : null;
  if (button?.disabled) return;
  if (button?.dataset.approveEdit) void interact("approve", { proposal_id: button.dataset.approveEdit });
  if (button?.dataset.dismissEdit) {
    dismissedProposals.add(button.dataset.dismissEdit);
    renderInteractions();
  }
});

function renderWorld(): void {
  const snapshot = state;
  const world = snapshot?.world;
  const scene = element("world-scene");
  if (!snapshot || !world) {
    scene.innerHTML = '<div class="empty scene-empty"><span class="empty-icon">◈</span><h3>A world waiting to exist</h3><p>Create a new episode. The world starts with a locked door and a closed box.</p></div>';
    element("world-json").textContent = "No authoritative snapshot loaded.";
    return;
  }
  const actor = world.actors[selectedActor] ?? Object.values(world.actors)[0];
  if (!actor) throw new Error("World snapshot has no actor");
  const perspective = actorPerspective(snapshot, actor.actor_id);
  if (perspective.observation?.bbq) {
    const bbq = perspective.observation.bbq;
    const angle = Math.PI * (Math.max(0, Math.min(1, (bbq.temperature_f - 75) / 300)) - 1);
    const needleX = 120 + 78 * Math.cos(angle), needleY = 120 + 78 * Math.sin(angle);
    const targetAngle = Math.PI * ((bbq.target_f - 75) / 300 - 1);
    const low = Math.min(200, ...bbq.history.map(reading => reading.temperature_f)) - 10;
    const high = Math.max(250, ...bbq.history.map(reading => reading.temperature_f)) + 10;
    const y = (temperature: number) => 100 - 90 * (temperature - low) / (high - low);
    const points = bbq.history.map(reading =>
      `${10 + 280 * reading.seconds / bbq.duration_seconds},${y(reading.temperature_f)}`).join(" ");
    scene.innerHTML = `<section class="bbq-station" aria-label="BBQ temperature control">
      <div class="network-heading"><span class="eyebrow">BBQ / LOW AND SLOW</span><span class="tag ${bbq.flare_active || !bbq.in_band ? "amber" : "green"}">${bbq.flare_active ? "FLARE-UP" : bbq.in_band ? "IN TARGET BAND" : "ADJUSTING"}</span></div>
      <svg class="bbq-gauge" viewBox="0 0 240 175" role="img" aria-label="BBQ temperature ${bbq.temperature_f.toFixed(1)} degrees Fahrenheit; target ${bbq.target_f} degrees Fahrenheit">
        <path class="bbq-gauge-track" d="M30 120 A90 90 0 0 1 210 120"/>
        <path class="bbq-target" d="M${120 + 102 * Math.cos(targetAngle)} ${120 + 102 * Math.sin(targetAngle)} L${120 + 80 * Math.cos(targetAngle)} ${120 + 80 * Math.sin(targetAngle)}"/>
        <path class="bbq-needle" d="M120 120 L${needleX} ${needleY}"/>
        <circle cx="120" cy="120" r="5" fill="currentColor"/>
        <text x="120" y="156" text-anchor="middle">${bbq.temperature_f.toFixed(1)}&#176;F</text>
        <text class="bbq-scale" x="30" y="140" text-anchor="middle">75</text>
        <text class="bbq-scale" x="210" y="140" text-anchor="middle">375</text>
        <text class="bbq-scale" x="120" y="12" text-anchor="middle">TARGET ${bbq.target_f}&#176;F</text>
      </svg>
      <div class="bbq-readouts">
        <div><span class="eyebrow">HEAT DIAL</span><strong>${bbq.heat_percent.toFixed(1)}%</strong><meter min="0" max="100" value="${bbq.heat_percent}" aria-label="Heat dial percentage"></meter></div>
        <div><span class="eyebrow">SIMULATED TIME</span><strong>${clockTime(bbq.elapsed_seconds)}</strong><small>of ${clockTime(bbq.duration_seconds)}</small></div>
        <div><span class="eyebrow">TIME IN BAND</span><strong>${bbq.in_band_percent.toFixed(1)}%</strong><small>${bbq.minimum_in_band_percent}% required</small></div>
        <div><span class="eyebrow">FLARE-UPS</span><strong>${bbq.flare_count}</strong><small>${bbq.flare_active ? "Brief heat spike" : "No active flare-up"}</small></div>
      </div>
      <svg class="bbq-history" viewBox="0 0 300 110" role="img" aria-label="BBQ temperature history; dashed line is the target">
        <rect x="10" y="${y(bbq.target_f + bbq.tolerance_f)}" width="280" height="${y(bbq.target_f - bbq.tolerance_f) - y(bbq.target_f + bbq.tolerance_f)}" class="bbq-band"/>
        <path d="M10 ${y(bbq.target_f)} H290" class="bbq-target-line"/>
        <polyline points="${points}" class="bbq-temperature-line"/>
      </svg>
      <p class="muted">Target ${bbq.target_f}&#176;F &#177;${bbq.tolerance_f}&#176;F. Each NPC action advances 30 simulated seconds; thinking and pausing do not advance time. ${bbq.status === "tending" ? "The NPC adjusts the heat or watches the gauge." : `Session ${bbq.status}. Final temperature must also be in band.`}</p>
    </section>`;
    element("world-json").textContent = pretty(world);
    element("world-revision").textContent = `REV ${world.revision}`;
    return;
  }
  if (perspective.observation?.erp) {
    const terminal = perspective.observation.erp;
    scene.innerHTML = `<section class="erp-terminal" aria-label="ERP terminal">
      <div class="network-heading"><span class="eyebrow">ERP / ACCOUNTS PAYABLE</span><span class="tag">${escape(terminal.invoice_status.replaceAll("_", " ").toUpperCase())}</span></div>
      <p class="muted">Simulated business system / NPC-controlled session / no live ERP connection</p>
      <pre tabindex="0" aria-label="Latest ERP terminal output">${escape(terminal.prompt)} ${escape(terminal.command)}\n\n${escape(terminal.output)}</pre>
      <p class="muted">The NPC issues one ERP command per turn. Inspect the action log for previous commands and validation errors.</p>
    </section>`;
    element("world-json").textContent = pretty(world);
    element("world-revision").textContent = `REV ${world.revision}`;
    return;
  }
  const entities = discoveredEntities(world, actor.actor_id, perspective.observation, perspective.memory);
  const team = Object.keys(snapshot.run?.agents ?? {}).length > 1;
  const network = team ? teamLocationNetwork(snapshot, events) :
    locationNetwork(world, actor.actor_id, perspective.observation, perspective.memory);
  const mapNodes = new Map(network.nodes.map(node => [node.id, node]));
  const inspected = mapNodes.get(selectedMapLocation) ?? mapNodes.get(actor.location_id);
  const mapScroll = scene.querySelector(".network-scroll");
  const scrollTop = mapScroll?.scrollTop ?? 0;
  const scrollLeft = mapScroll?.scrollLeft ?? 0;
  const focusedLocation = document.activeElement instanceof HTMLElement ? document.activeElement.dataset.mapLocation : undefined;
  const routesOpen = scene.querySelector<HTMLDetailsElement>(".network-routes")?.open ?? false;
  const doors = Object.values(entities).filter(entity => entity.kind === "door");
  const nearbyDoors = doors.filter(door => door.location === actor?.location_id || door.destination === actor?.location_id);
  const boxEntry = Object.entries(entities).find(([, entity]) => entity.kind === "container");
  const [boxId, box] = boxEntry ?? ["", undefined];
  const nearbyBox = box && (box.location === actor?.location_id || actor?.inventory.includes(boxId)) ? box : undefined;
  const items = Object.entries(entities).filter(([, entity]) => entity.kind === "item");
  const { escaped, completed } = goalPresentation(world, actor.actor_id, snapshot.run?.goal);
  const doorStatus = (door: typeof doors[number]) => {
    const lock = door.lock_id ? entities[door.lock_id] : undefined;
    return door.health === 0 || (lock?.health === 0 && !door.open) ? "BROKEN" : door.locked ? "LOCKED" : door.open ? "OPEN" : "UNLOCKED";
  };
  scene.innerHTML = `
    ${perspective.observation?.village ? villageMarkup(perspective.observation.village) : ""}
    <div class="network-heading"><span class="eyebrow">${team ? "CONSOLIDATED TEAM MAP" : "LOCATION NETWORK"}</span><span>${network.nodes.filter(node => !node.unexplored).length} discovered / ${network.edges.length} routes${team ? ` / ${Object.keys(snapshot.run?.agents ?? {}).length} agents` : ""}</span></div>
    <div class="network-scroll" tabindex="0" role="region" aria-label="${team ? "Consolidated team location network" : "Numbered location network"}">
      <div class="network-canvas" style="height:${network.height}px">
        <svg class="network-links" viewBox="0 0 ${network.width} ${network.height}" preserveAspectRatio="none" aria-label="Discovered routes">
          ${network.edges.map((edge, index) => {
            const from = mapNodes.get(edge.source);
            const to = mapNodes.get(edge.target);
            if (!from || !to) throw new Error(`Map route references a missing node: ${edge.id}`);
            const parallels = network.edges.filter(other =>
              (other.source === edge.source && other.target === edge.target) ||
              (other.source === edge.target && other.target === edge.source));
            const offset = (parallels.indexOf(edge) - (parallels.length - 1) / 2) * 55 *
              (edge.source < edge.target ? 1 : -1);
            const distance = Math.hypot(to.x - from.x, to.y - from.y) || 1;
            const cx = (from.x + to.x) / 2 - (to.y - from.y) / distance * offset;
            const cy = (from.y + to.y) / 2 + (to.x - from.x) / distance * offset;
            const labelX = (from.x + 2 * cx + to.x) / 4;
            const labelY = (from.y + 2 * cy + to.y) / 4;
            return `<g class="network-edge ${edge.status}"><title>D${index + 1}: ${escape(edge.name)} / ${edge.status}${edge.requirements.length ? ` / ${escape(edge.requirements.join(", "))}` : ""}</title>
              <path d="M ${from.x} ${from.y} Q ${cx} ${cy} ${to.x} ${to.y}" />
              <rect x="${labelX - 14}" y="${labelY - 9}" width="28" height="18" rx="5" />
              <text x="${labelX}" y="${labelY + 3}">D${index + 1}</text></g>`;
          }).join("")}
        </svg>
        ${network.nodes.map(node => `<button class="network-node ${node.current ? "current" : ""} ${node.unexplored ? "unexplored" : ""} ${node.id === inspected?.id ? "inspected" : ""}"
          style="left:${node.x / network.width * 100}%;top:${node.y}px" data-map-location="${escape(node.id)}"
          aria-label="${node.number ?? "Unknown"}: ${escape(node.name)}${node.current ? team ? `, ${escape(node.actors.join(", "))}` : ", your location" : ""}" aria-pressed="${node.id === inspected?.id}">
          <span class="network-number">${node.number ?? "?"}</span><strong>${escape(node.name)}</strong>
          ${node.actors.length ? `<small>${node.actors.map(id => `<span class="map-actor" data-map-actor="${escape(id)}">${escape(id)}</span>`).join(" + ")}${node.current && !team ? " / YOU" : ""}</small>` : ""}
        </button>`).join("")}
      </div>
    </div>
    <div class="network-legend"><span class="open">Open route</span><span class="closed">Closed / locked / skill-gated</span><span class="broken">Broken</span><span>? Unexplored</span></div>
    ${inspected ? `<div class="network-location"><strong>${inspected.number ? `${inspected.number}. ` : "? "}${escape(inspected.name)}</strong><p>${escape(inspected.description)}</p></div>` : ""}
    <details class="network-routes" ${routesOpen ? "open" : ""}><summary>Route details (${network.edges.length})</summary>${network.edges.map((edge, index) =>
      `<div><span class="route-number">D${index + 1}</span><strong>${escape(edge.name)}</strong><span>${mapNodes.get(edge.source)?.number ?? "?"} ↔ ${mapNodes.get(edge.target)?.number ?? "?"}</span><b class="${edge.status}">${edge.status.toUpperCase()}</b>${edge.requirements.length ? `<small>${escape(edge.requirements.join(" / "))}</small>` : ""}</div>`).join("") || '<p class="muted">No routes discovered yet.</p>'}</details>
    ${team ? "" : `<div class="room-caption"><span class="location-marker">⌖</span><strong>${escape(world.entities[actor?.location_id ?? "room"]?.name ?? actor?.location_id)}</strong><span>WORLD #${world.world_id.slice(0, 6)}</span></div>
    <div class="room-stage ${escaped ? "escaped" : ""}">
      <div class="room-grid"></div>
      ${nearbyDoors.map((door, index) => `<div class="door-object ${door.open ? "is-open" : ""} ${nearbyDoors.length > 1 && index === 0 ? "left-door" : ""}"><div class="door-frame"><div class="door-leaf"><span class="door-knob"></span></div></div><span class="object-label">${escape(door.name)} <b>${doorStatus(door)}</b></span></div>`).join("")}
      <div class="actor-object"><span class="actor-halo"></span><span class="actor-body">◉</span><span class="object-label">${escape(actor?.actor_id ?? "NPC")}<b>${escaped ? "ESCAPED" : completed ? "GOAL COMPLETE" : "IN ROOM"}</b></span></div>
      ${nearbyBox ? `<div class="box-object ${nearbyBox.open || nearbyBox.health === 0 ? "is-open" : ""}"><div class="box-lid"></div><div class="box-base">◇</div><span class="object-label">${escape(nearbyBox.name)} <b>${nearbyBox.health === 0 ? "BROKEN" : nearbyBox.open ? "OPEN" : "CLOSED"}</b></span></div>` : ""}
      ${completed ? `<div class="victory-label">${snapshot.run?.successful === false ? "ASSESSMENT FAILED" : escaped ? "EXIT REACHED ↗" : "GOAL ACHIEVED"}</div>` : ""}
    </div>
    <div class="entity-strip">${items.map(([id, entity]) => {
      const hidden = entity.location === boxId && !box?.open && box?.health !== 0;
      return `<div class="entity-pill"><span class="key-icon">${entity.name.toLowerCase().includes("key") ? "⚿" : "◇"}</span><div><strong>${escape(entity.name)}</strong><small>${escape(hidden ? "Hidden inside box" : entity.location ? `At ${entity.location}` : "Consumed / absent")}</small></div><span class="tag ${hidden ? "amber" : ""}">${hidden ? "HIDDEN" : escape(id)}</span></div>`;
    }).join("")}</div>
    <div class="object-stats">${Object.entries(entities).filter(([, entity]) => entity.kind !== "location").map(([, entity]) => `<div><strong>${escape(entity.name)}</strong><span>${entity.takable ? "Takable" : "Fixed"}</span>${entity.takable ? `<span>${escape(entity.inventory_units ?? 1)} inventory units</span>` : ""}<span>Damage ${escape(entity.damage ?? 0)}</span><span class="${entity.health === 0 ? "broken-stat" : ""}">${entity.health === 0 ? "BROKEN" : entity.health == null ? "Indestructible" : `Health ${escape(entity.health)} / ${escape(entity.max_health)}`}</span>${entity.food ? `<span>${escape(nutritionLabel(entity.nutrition))}</span>` : ""}</div>`).join("")}</div>`}`;
  element("world-json").textContent = pretty(world);
  element("world-revision").textContent = `REV ${world.revision}`;
  const nextScroll = scene.querySelector(".network-scroll");
  if (nextScroll) {
    nextScroll.scrollTop = scrollTop;
    nextScroll.scrollLeft = scrollLeft;
  }
  if (focusedLocation) {
    Array.from(scene.querySelectorAll<HTMLButtonElement>("[data-map-location]"))
      .find(button => button.dataset.mapLocation === focusedLocation)?.focus({ preventScroll: true });
  }
}

function renderSelf(): void {
  const world = state?.world;
  const select = element<HTMLSelectElement>("actor-select");
  if (!world) {
    element("self-content").innerHTML = '<p class="empty compact">No self object loaded.</p>';
    return;
  }
  const actors = Object.keys(world.actors);
  if (!actors.includes(selectedActor)) selectedActor = actors[0] ?? "";
  select.innerHTML = actors.map(id => `<option value="${escape(id)}" ${id === selectedActor ? "selected" : ""}>${escape(id)}</option>`).join("");
  const actor = world.actors[selectedActor];
  if (!actor) return;
  if (state && actorPerspective(state, selectedActor).observation?.bbq) {
    element("self-content").innerHTML = `<div class="self-identity"><div><strong>${escape(actor.actor_id)}</strong><p>${escape(actor.body.description)}</p></div></div>
      <div class="capabilities"><span class="capability">BBQ heat control</span></div>
      <p class="muted">Only temperature tending: adjust the heat dial or monitor the gauge. No inventory, movement or needs. Simulated BBQ only.</p>`;
    return;
  }
  if (state && actorPerspective(state, selectedActor).observation?.erp) {
    element("self-content").innerHTML = `<div class="self-identity"><div><strong>${escape(actor.actor_id)}</strong><p>${escape(actor.body.description)}</p></div></div>
      <div class="capabilities"><span class="capability">ERP terminal</span></div>
      <p class="muted">System role: receipt reconciliation and invoice matching. No physical inventory, movement or needs. Payments and control overrides are not permitted.</p>`;
    return;
  }
  element("self-content").innerHTML = `
    <div class="self-identity"><div class="avatar">◉</div><div><strong>${escape(actor.actor_id)}</strong><p>${escape(actor.body.description)}</p></div></div>
    <div class="capabilities">${actor.body.capabilities.map(cap => `<span class="capability">${escape(cap)}</span>`).join("")}<span class="capacity">${inventoryUnitsUsed(world, actor.actor_id)} / ${actor.body.capacity} units carried</span></div>
    <p class="handling-summary">Body size: ${escape(actor.body.size)} / Grips: ${escape(actor.body.grips.join(", ") || "None")}${actor.body.max_held_items !== null ? ` / ${actor.inventory.length} of ${actor.body.max_held_items} held objects` : ""}. Carrying space does not grant hand-like control.</p>
    <div class="inventory"><span class="eyebrow">INVENTORY</span><div>${actor.inventory.length ? actor.inventory.map(id => `<span class="inventory-item">${escape(world.entities[id]?.name ?? id)} · ${escape(world.entities[id]?.inventory_units ?? 1)} u</span>`).join("") : '<span class="muted">Empty — capacity available</span>'}</div></div>
    <div class="needs-heading"><span class="eyebrow">OPTIONAL NEEDS</span><span>World-enforced constraints</span></div>
    ${Object.entries(actor.needs).map(([name, need]) => `
      <div class="need-row ${need.enabled ? "" : "disabled"}">
        <div class="need-icon">${name === "food" ? "♧" : "☾"}</div>
        <div class="need-info"><strong>${escape(name)}</strong><span>${need.enabled ? (need.level >= need.threshold ? "Movement blocked — recovery needed" : `${need.level} / 100 · threshold ${need.threshold}`) : "Disabled · level retained"}</span><div class="need-track"><div style="width:${Math.max(0, Math.min(100, need.level))}%"></div></div></div>
        <button class="toggle ${need.enabled ? "on" : ""}" data-need="${escape(name)}" data-enabled="${!need.enabled}" role="switch" aria-checked="${need.enabled}" aria-label="Enable ${escape(name)} constraint" ${busy || state?.in_flight || !["ready", "paused", "running"].includes(state?.run?.status ?? "") ? "disabled" : ""}><span></span></button>
      </div>`).join("")}`;
}

function renderObservation(): void {
  const obs = state ? actorPerspective(state, selectedActor).observation : null;
  element("observation-content").innerHTML = observationMarkup(obs);
}

function observationMarkup(obs: State["observation"], identity = true): string {
  if (!obs) {
    return '<div class="empty compact"><p>Awaiting this agent’s own observation from the world MCP.</p></div>';
  }
  return `
    ${identity ? `<p class="muted">${escape(obs.self.actor_id)}${obs.role ? ` / ${escape(obs.role)}` : ""}</p>` : ""}
    <blockquote>${escape(obs.narrative)}</blockquote>
    ${obs.village ? villageMarkup(obs.village) : ""}
    ${obs.voyage ? `<div class="observed-facts"><span class="eyebrow">TIDE & VOYAGE</span><p>${escape(obs.voyage.tide)} tide / ${obs.voyage.turns_until_change} successful physical actions until the next change${obs.voyage.seaworthy !== undefined ? ` / cutter ${obs.voyage.seaworthy ? "seaworthy" : "unfinished"} / navigator ${obs.voyage.crew_aboard ? "aboard" : "not aboard"}` : ""}</p><p class="muted">${escape(obs.voyage.policy)}</p></div>` : ""}
    ${obs.visible_actors ? `<div class="observed-facts"><span class="eyebrow">NEARBY TEAMMATES</span><div>${obs.visible_actors.map(actor => `<span class="fact-chip">${escape(actor.actor_id)} / ${escape(actor.inventory.map(item => item.name).join(", ") || "empty hands")}${actor.capabilities ? ` / skills: ${escape(actor.capabilities.join(", "))}` : ""}</span>`).join("") || '<span class="muted">None here</span>'}</div></div>` : ""}
    ${obs.messages?.length ? `<div class="observed-facts"><span class="eyebrow">HEARD MESSAGES</span>${obs.messages.map(message => `<p><strong>${escape(message.actor_id)}:</strong> ${escape(message.text)}</p>`).join("")}</div>` : ""}
    <div class="observed-facts"><span class="eyebrow">VISIBLE ENTITIES</span><div>${obs.visible_entities.map(entity => `<span class="fact-chip" title="${escape(entity.id + (entity.carry_grips ? ` / carry grips: ${entity.carry_grips.join(", ") || "none"}` : "") + (entity.use_grips ? ` / control grips: ${entity.use_grips.join(", ") || "none"}` : ""))}">${escape(typeof entity.name === "string" ? entity.name : entity.id)}</span>`).join("") || '<span class="muted">None</span>'}</div></div>
    ${obs.skill_challenges?.length ? `<div class="observed-facts"><span class="eyebrow">LOCAL COOPERATIVE WORK</span>${obs.skill_challenges.map(challenge =>
      `<div class="skill-challenge"><strong>${escape(challenge.id.replaceAll("_", " "))}</strong><span class="tag ${challenge.completed_by ? "green" : "amber"}">${escape(challenge.capability)}</span><p>${escape(challenge.description)}</p><p class="muted">${escape(challenge.completed_by ? `Completed by ${challenge.completed_by}` : challenge.reason ?? "Your skills can do this work now.")}</p></div>`).join("")}</div>` : ""}
    <div class="observed-facts"><span class="eyebrow">AVAILABLE INTERACTIONS</span><div>${obs.actions.map(action => `<span class="action-chip ${action.availability === "blocked" ? "blocked" : ""}" title="${escape(action.description + (action.reason ? ` ${action.reason}` : "") + Object.entries(action.target_requirements ?? {}).filter(([, reason]) => reason).map(([id, reason]) => ` ${id}: ${reason}`).join(""))}">${escape(action.id)}${action.availability === "blocked" ? " · blocked" : ""}</span>`).join("")}</div></div>
    <details class="inspector"><summary>Actor-visible JSON <span>${escape(obs.observation_revision)}</span></summary><pre>${escape(pretty(obs))}</pre></details>`;
}

function updateReadingPane(node: HTMLElement, html: string): void {
  if (node.innerHTML === html) return;
  const scroll = node.scrollTop;
  const open = Array.from(node.querySelectorAll("details")).map(details => details.open);
  node.innerHTML = html;
  node.querySelectorAll("details").forEach((details, index) => { details.open = open[index] ?? false; });
  node.scrollTop = scroll;
}

function renderTeamPerspectives(): void {
  const agents = state ? agentPerspectives(state) : [];
  const team = agents.length > 1;
  const perspectivesVisible = !adminView || agents.length > 0;
  element("observatory").classList.toggle("team-layout", perspectivesVisible);
  element("team-overview").hidden = !perspectivesVisible;
  element("observation-panel").hidden = perspectivesVisible;
  element("story-panel").hidden = perspectivesVisible;
  document.querySelector(".workspace")?.classList.toggle("team-mode", perspectivesVisible);
  element("world-title").textContent = adminView ? (team ? "The whole team, one map" : "The world") : "Their world";
  element("map-scope").textContent = adminView && team ? "COMBINED DISCOVERIES" : "DISCOVERED MAP";
  element("narrative-note").textContent = team ?
    "One chronological adventure following every agent's discoveries, attempts and confirmed outcomes." :
    "A continuous account of what the NPC discovers, attempts and explains.";
  const worldPanel = element("world-title").closest<HTMLElement>(".world-panel");
  if (!worldPanel) throw new Error("Missing world panel");
  const host = element(perspectivesVisible ? "team-map-host" : "left-column");
  if (worldPanel.parentElement !== host) host.prepend(worldPanel);
  if (!adminView) {
    const liveTeam = element("team-live");
    const mapHost = element("team-map-host");
    if (mapHost.nextElementSibling !== liveTeam) mapHost.after(liveTeam);
  }
  const technicalLog = element("technical-log");
  if (worldPanel.nextElementSibling !== technicalLog) worldPanel.after(technicalLog);
  const selfPanel = element("self-panel");
  if (technicalLog.nextElementSibling !== selfPanel) technicalLog.after(selfPanel);
  const connectionPanel = element("connection-panel");
  if (selfPanel.nextElementSibling !== connectionPanel) selfPanel.after(connectionPanel);
  const narrativeColumn = element("narrative-column");
  const storyHost = element(perspectivesVisible ? "team-story-host" : "workspace");
  if (narrativeColumn.parentElement !== storyHost) storyHost.append(narrativeColumn);
  const completionPanel = element("completion-panel");
  const reviewHost = element(perspectivesVisible ? "team-review-host" : "narrative-column");
  if (completionPanel.parentElement !== reviewHost) reviewHost.append(completionPanel);
  const grid = element("agent-perspectives");
  grid.style.setProperty("--agent-count", String(Math.max(1, agents.length)));
  if (!perspectivesVisible) {
    grid.replaceChildren();
    delete grid.dataset.agents;
    return;
  }
  if (!agents.length) {
    grid.innerHTML = '<p class="player-empty">Your characters will appear here when you create an adventure.</p>';
    delete grid.dataset.agents;
    return;
  }
  const ids = agents.map(agent => agent.actorId).join(",");
  if (grid.dataset.agents !== ids) {
    grid.dataset.agents = ids;
    grid.innerHTML = agents.map(agent => `<article class="panel agent-perspective" data-agent-id="${escape(agent.actorId)}" aria-label="${escape(agent.actorId)} perspective">
      <div class="agent-heading"><h3>${escape(agent.actorId)}</h3><span class="tag" data-agent-status></span></div>
      <div class="agent-summary" data-agent-summary></div>
      <h4 class="agent-section-heading" data-admin-only>What this agent sees</h4>
      <div class="agent-observation" data-agent-observation data-admin-only tabindex="0" role="region" aria-label="${escape(agent.actorId)} observations"></div>
      <h4 class="agent-section-heading" data-admin-only>Observations & actions</h4>
      <div class="story-feed agent-story" data-agent-story data-admin-only tabindex="0" role="region" aria-label="${escape(agent.actorId)} actions"></div>
    </article>`).join("");
  }
  for (const agent of agents) {
    const card = Array.from(grid.querySelectorAll<HTMLElement>("[data-agent-id]"))
      .find(node => node.dataset.agentId === agent.actorId);
    if (!card) throw new Error(`Missing perspective card: ${agent.actorId}`);
    const status = card.querySelector<HTMLElement>("[data-agent-status]");
    const summary = card.querySelector<HTMLElement>("[data-agent-summary]");
    const observation = card.querySelector<HTMLElement>("[data-agent-observation]");
    const feed = card.querySelector<HTMLElement>("[data-agent-story]");
    if (!status || !summary || !observation || !feed) throw new Error("Incomplete perspective card");
    card.classList.toggle("taking-turn", agent.active);
    status.textContent = agent.status;
    status.className = `tag ${agent.active || agent.status === "At exit" ? "green" : ""}`;
    const self = agent.observation?.self;
    const profile = state?.run?.agent_profiles?.[agent.actorId];
    const heading = card.querySelector("h3");
    if (heading) heading.textContent = profile ? `${profile.name}${adminView ? ` / ${agent.actorId}` : ""}` : agent.actorId;
    updateReadingPane(summary, `<p class="agent-location">⌖ ${escape(agent.locationName)}</p>
      ${agent.observation?.erp ? `<p class="agent-inventory"><strong>ERP session:</strong> ${escape(agent.observation.erp.invoice_status.replaceAll("_", " "))} / terminal only</p>` : `<p class="agent-inventory"><strong>Carrying:</strong> ${escape(self?.inventory.map(item => item.name).join(", ") || "Empty hands")}${self ? ` · ${self.body.carrying.used} / ${self.body.carrying.limit} units` : ""}</p>`}
      <p class="agent-focus"><strong>Current focus:</strong> ${escape(agent.focus || "No task decision recorded yet.")}</p>
      ${self ? `<p><strong>Body size:</strong> ${escape(self.body.size)} / <strong>Grips:</strong> ${escape(self.body.grips.join(", ") || "None")}${self.body.max_held_items !== null ? ` / at most ${self.body.max_held_items} held objects` : ""}</p>` : ""}
      <details class="agent-role" data-admin-only><summary>Role & constraints</summary><p>${escape(agent.role)}</p>
      ${profile ? `<p>${escape(profileText(profile, "IDENTITY.md"))}</p><p>${escape(profileText(profile, "SOUL.md"))}</p><p class="agent-guidance">${escape(profileText(profile, "AGENTS.md"))}</p>` : ""}
        ${self ? `<p>${escape(self.body.description)}</p><p>${escape(self.body.capabilities.map(skill => skill.replace(/_/g, " ")).join(", "))}</p>
        ${self.needs.map(need => `<p>${escape(need.id)}: ${need.enabled ? `${need.level} / 100` : "disabled"}</p>`).join("")}` : ""}
      </details>`);
    updateReadingPane(observation, observationMarkup(agent.observation, false));
    renderStoryFeed(feed, buildStory(events, agent.actorId, state?.run?.agent_profiles), true);
  }
}

function eventSummary(event: RunEvent): { title: string; text: string; tone: string } {
  const payload = record(event.payload);
  switch (event.kind) {
    case "decision": return { title: `${String(payload.action_id)} · ${String(payload.subgoal || "Goal-directed decision")}`, text: String(payload.justification ?? ""), tone: "teal" };
    case "action_request": return { title: `MCP → ${String(payload.action_id)}`, text: pretty(payload.arguments), tone: "amber" };
    case "action_result": return { title: `${String(payload.outcome)} · ${String(payload.code)}`, text: String(payload.explanation), tone: payload.outcome === "REJECTED" ? "red" : "teal" };
    case "error": return { title: "Execution error", text: String(payload.message), tone: "red" };
    case "finished": return { title: String(payload.status), text: String(payload.reason), tone: payload.status === "succeeded" ? "teal" : "red" };
    case "verification": return { title: "Authoritative goal check", text: String(payload.status), tone: payload.status === "achieved" ? "teal" : "neutral" };
    case "secondary_goal_achieved": return { title: `Side goal completed: ${String(payload.title)}`, text: String(payload.reason), tone: "teal" };
    case "completion_verification": return { title: "Post-run world recheck", text: String(payload.status), tone: payload.status === "achieved" ? "teal" : "red" };
    case "assessment_result": return { title: `Completion grade: ${String(payload.verdict).toUpperCase()}`, text: completionAnalysisSchema.shape.findings.parse(payload.findings).map(finding => finding.reason).join(" "), tone: payload.verdict === "pass" ? "teal" : "red" };
    case "dungeon_master_review": return { title: `Dungeon Master: ${String(payload.verdict)}`, text: z.array(z.object({ reason: z.string() })).parse(payload.findings).map(finding => finding.reason).join(" "), tone: payload.verdict === "defeat" ? "red" : "teal" };
    case "assessment_error": return { title: "Completion review failed", text: String(payload.message), tone: "red" };
    case "observation": return { title: "World observation", text: String(payload.narrative), tone: "neutral" };
    case "goal_discovered": return { title: "Task discovered", text: pretty(payload.goal), tone: "teal" };
    case "proxy_request": return { title: "LLM proxy request", text: `Model: ${String(payload.model)}${payload.repair ? " · decision repair" : ""}`, tone: "neutral" };
    case "validation": return { title: payload.valid ? "Decision validated" : "Invalid decision", text: String(payload.error ?? payload.action_id), tone: payload.valid ? "neutral" : "red" };
    case "need_toggle": return { title: `Need ${String(payload.need)} ${payload.enabled ? "enabled" : "disabled"}`, text: `Actor ${String(payload.actor_id)}`, tone: "amber" };
    default: return { title: event.kind.replaceAll("_", " "), text: String(payload.reason ?? payload.command ?? ""), tone: "neutral" };
  }
}

function storyMarkup(entries: StoryEntry[], team = false): string {
  return entries.map(entry => `<article class="story-entry ${entry.kind}" data-story-id="${escape(entry.id)}">
      <div class="story-marker">${entry.kind === "intent" ? "→" : entry.kind === "ending" ? "◇" : "·"}</div>
      <div><span class="story-label">${entry.kind === "intent" ? (team ? "ACTION ATTEMPT" : "I TRY") : entry.kind === "result" ? "WHAT HAPPENS" : entry.kind === "ending" ? "THE OUTCOME" : (team ? "OBSERVATION" : "I OBSERVE")}${entry.cycle ? ` · STEP ${entry.cycle}` : ""}</span><p>${escape(entry.text)}</p>
      ${entry.explanation ? `<details><summary>Why this action?</summary><p class="story-explanation">${escape(entry.explanation)}</p></details>` : ""}</div>
    </article>`).join("");
}

function renderStory(): void {
  renderStoryFeed(element("story"), buildStory(events, selectedActor || state?.run?.actor_id,
    state?.run?.agent_profiles), isTeamStory(events));
}

function renderStoryFeed(node: HTMLElement, entries: StoryEntry[], team: boolean): void {
    const html = entries.length ? storyMarkup(entries, team) : '<div class="empty compact"><p>The story begins when an episode starts. Only what the NPC actually observes will be narrated.</p></div>';
    if (node.innerHTML === html) return;
    const atEnd = node.scrollHeight - node.scrollTop - node.clientHeight < 36;
    const scroll = node.scrollTop;
    const openEntries = new Set(Array.from(node.querySelectorAll<HTMLElement>("article:has(details[open])")).map(entry => entry.dataset.storyId));
    node.innerHTML = html;
    node.querySelectorAll<HTMLElement>("article").forEach(entry => {
      const details = entry.querySelector("details");
      if (details && openEntries.has(entry.dataset.storyId)) details.open = true;
    });
  node.scrollTop = atEnd ? node.scrollHeight : scroll;
}

function narrativeMarkup(source: readonly RunEvent[], actorId?: string, analysis?: CompletionAnalysis,
  members?: StoryMembers): string {
  const paragraphs = buildNarrative(source, actorId, analysis?.story, members);
  return paragraphs.map(paragraph =>
    `<p data-narrative-id="${escape(paragraph.id)}">${escape(paragraph.text)}</p>`).join("");
}

function completionMarkup(analysis?: CompletionAnalysis, source: readonly RunEvent[] = []): string {
  if (!analysis) return '<p class="muted">The story will be reviewed after this attempt ends.</p>';
  if (analysis.status === "pending" || analysis.status === "running") {
    return '<p class="muted">Attempt finished. Rechecking the world and reviewing the resulting story…</p>';
  }
  if (analysis.status === "error") return `<p class="assessment-fail">${adminView ? "NOT GRADED" : "Outcome review unavailable"}: ${escape(analysis.error)}</p><p class="muted">${adminView ? "No success or new failure lesson is assumed. Reassess after resolving the error." : "Open Admin view to resolve the review error. No successful outcome is assumed."}</p>`;
  return `<p class="assessment-${analysis.verdict}"><strong>${escape(adminView ? analysis.verdict?.toUpperCase() : analysis.verdict === "pass" ? "Adventure complete" : "Adventure ended")}</strong>${adminView ? ` · Attempt finished · ${analysis.verdict === "pass" ? "Successful" : "Not successful"}` : ""}</p>
    <ul>${analysis.findings.map(finding => `<li>${escape(finding.reason)}<details><summary>Evidence explained</summary>${evidenceMarkup(finding.evidence_ids, source, analysis.story)}</details></li>`).join("")}</ul>
    <p class="muted">${analysis.verdict === "fail" ? (adminView ? "Failure reasons saved to scenario memory for future attempts." : "Not every adventure succeeds. Try another cast or a different approach.") : "The story and final world state confirm completion."}</p>
    <p class="muted" data-admin-only>Review: ${escape(analysis.model)} · ${analysis.elapsed_seconds.toFixed(1)}s · ${analysis.model_calls} proxy call</p>`;
}

function renderCompletionAnalysis(): void {
  updateReadingPane(element("completion-analysis"), completionMarkup(state?.run?.completion_analysis, events));
}

function renderNarrative(): void {
  const run = state?.run;
  if (run && state && state.event_count > 0 && !events.length) {
    element<HTMLButtonElement>("copy-story").disabled = true;
    element("narrative").innerHTML = "<p>Loading the recorded adventure...</p>";
    return;
  }
  const actorId = run?.agents ? undefined : selectedActor || run?.actor_id;
  const analysis = run?.completion_analysis;
  element<HTMLButtonElement>("copy-story").disabled = !run ||
    ["ready", "running", "paused"].includes(run.status) ||
    ["pending", "running"].includes(run.completion_analysis?.status ?? "") ||
    !buildNarrative(events, actorId, analysis?.story, run?.agent_profiles).length;
  const node = element("narrative");
  const html = narrativeMarkup(events, actorId, analysis, run?.agent_profiles) ||
    "<p>The story will unfold here as the NPC discovers the world.</p>";
  if (node.innerHTML === html) return;
  const hasStory = node.querySelector("[data-narrative-id]") !== null;
  const atEnd = node.scrollHeight - node.scrollTop - node.clientHeight < 36;
  const scroll = node.scrollTop;
  node.innerHTML = html;
  node.scrollTop = hasStory ? (atEnd ? node.scrollHeight : scroll) : 0;
}

async function copyFinalStory(paragraphs: readonly { text: string }[]): Promise<void> {
  try {
    await copyStory(paragraphs, navigator.clipboard);
    notice("Final story copied to the clipboard.");
  } catch (error) {
    notice(`Could not copy the final story: ${errorMessage(error)}`);
  }
}

element("copy-story").addEventListener("click", () => {
  if (!state?.run || element<HTMLButtonElement>("copy-story").disabled) return;
  void copyFinalStory(buildNarrative(events, state.run.agents ? undefined : selectedActor || state.run.actor_id,
    state.run.completion_analysis?.story, state.run.agent_profiles));
});

function renderTimeline(): void {
  renderInteractions();
  renderDungeonMaster();
  renderCompletionAnalysis();
  renderTeamPerspectives();
  renderStory();
  renderNarrative();
  element("event-count").textContent = String(events.length);
  const shown = events.filter(event => filter === "all" || event.kind === filter || (filter === "error" && ["validation", "finished"].includes(event.kind)));
  if (!shown.length) {
    element("timeline").innerHTML = '<div class="empty"><span class="empty-icon">⌁</span><h3>Watch a goal become action</h3><p>Start an episode, connect the proxy, then step or run the agent.</p></div>';
    return;
  }

  element("timeline").innerHTML = shown.slice().reverse().map(event => {
    const summary = eventSummary(event);
    const payload = record(event.payload);
    const evidence = evidenceReferences(event.payload);
    const story = event.kind === "assessment_result" ?
      completionAnalysisSchema.shape.story.optional().parse(payload.story) ?? [] : [];
    return `<details class="event ${summary.tone}" data-event-id="${escape(event.id)}" ${expanded.has(event.id) ? "open" : ""}>
      <summary><span class="event-marker"></span><div class="event-body"><div class="event-meta">CYCLE ${event.cycle.toString().padStart(2, "0")} <span>${escape(event.actor_id)}</span><span>${new Date(event.timestamp).toLocaleTimeString()}</span><span>#${event.sequence}</span></div><strong>${escape(summary.title)}</strong><p>${escape(summary.text)}</p></div><span class="expand-icon">＋</span></summary>
      <div class="event-detail">${event.kind === "decision" ? `<div class="expected"><span class="eyebrow">EXPECTED OUTCOME</span><p>${escape(payload.expected_outcome)}</p></div>` : ""}${evidence.length ? `<h4>Evidence explained</h4>${evidenceMarkup(evidence, events, story)}` : ""}<details><summary>Original technical JSON</summary><pre>${escape(pretty(event.payload))}</pre></details></div>
    </details>`;
  }).join("");
}

async function command(name: string): Promise<void> {
  if (busy) return;
  if ((name === "reset" || name === "start") && state?.run &&
      ["ready", "running", "paused"].includes(state.run.status) &&
      !confirm(adminView ? "Replace the active episode with a fresh world? Existing JSON history will be kept." :
        "Replace the current adventure with a fresh start? Its saved history will be kept.")) return;
  busy = true;
  notice("");
  renderControls();
  try {
    const scenario = name === "reset" ? state?.run?.scenario_id : selectedScenario;
    if (name === "start") {
      const choice = scenarioChoices.find(choice => choice.id === scenario);
      if (!choice) throw new Error("Choose a scenario before starting.");
      validateAssignments(choice.agent_slots.map(slot => slot.id), assignments, profiles);
    }
    applyState(stateSchema.parse(await api(`/control/${name}`, "POST",
      (name === "start" || name === "reset") && scenario ? {
        scenario_id: scenario, ...(name === "start" ? { agent_assignments: assignments, skill_mode: skillMode } : {})
      } : {})));
  } catch (error) { notice(errorMessage(error)); }
  finally { busy = false; renderControls(); renderSelf(); }
}
element("controls").addEventListener("click", event => {
  if (!(event.target instanceof Element)) return;
  const button = event.target.closest<HTMLButtonElement>("[data-command]");
  if (button?.dataset.command) void command(button.dataset.command);
});
element("self-content").addEventListener("click", event => {
  if (!(event.target instanceof Element)) return;
  const button = event.target.closest<HTMLButtonElement>("[data-need]");
  if (!button?.dataset.need || !state || busy) return;
  busy = true;
  renderControls();
  void api(`/actors/${encodeURIComponent(selectedActor)}/needs/${encodeURIComponent(button.dataset.need)}`, "PUT", {
    enabled: button.dataset.enabled === "true"
  }).then(payload => applyState(stateSchema.parse(payload))).catch(error => notice(errorMessage(error))).finally(() => {
    busy = false; renderSelf(); renderControls();
  });
});
element("actor-select").addEventListener("change", () => {
  selectedActor = element<HTMLSelectElement>("actor-select").value;
  selectedMapLocation = "";
  renderSelf(); renderWorld(); renderObservation(); renderBakery(); renderStory(); renderNarrative();
});
element("world-scene").addEventListener("click", event => {
  if (!(event.target instanceof Element)) return;
  const button = event.target.closest<HTMLButtonElement>("[data-map-location]");
  if (!button?.dataset.mapLocation) return;
  selectedMapLocation = button.dataset.mapLocation;
  renderWorld();
});
element("scenario-select").addEventListener("change", () => {
  selectedScenario = element<HTMLSelectElement>("scenario-select").value;
  assignments = {};
  renderAssignments();
});
element("skill-mode").addEventListener("change", () => {
  skillMode = skillModeSchema.parse(element<HTMLSelectElement>("skill-mode").value);
  renderAssignments();
});
element("agent-assignments").addEventListener("change", event => {
  if (!(event.target instanceof HTMLSelectElement) || !event.target.dataset.agentSlot) return;
  assignments[event.target.dataset.agentSlot] = event.target.value;
  renderAssignments();
});
element("profile-select").addEventListener("change", () => renderProfileEditor());
element("agent-document-select").addEventListener("change", () => renderDocumentEditor());
element("save-agent-document").addEventListener("click", () => {
  void (async () => {
    const button = element<HTMLButtonElement>("save-agent-document");
    button.disabled = true;
    element("agent-document-message").textContent = "";
    try {
      const profile = profiles.find(profile => profile.id === element<HTMLSelectElement>("profile-select").value);
      const name = element<HTMLSelectElement>("agent-document-select").value;
      if (!profile || !Object.hasOwn(profile.documents, name)) throw new Error("Choose an agent and a Markdown document.");
      const saved = agentProfileSchema.parse(await api(`/agents/${encodeURIComponent(profile.id)}`, "PUT", {
        ...profile, documents: { [name]: element<HTMLTextAreaElement>("agent-document-text").value }
      }));
      profiles = profiles.map(profile => profile.id === saved.id ? saved : profile);
      renderProfileEditor(saved.id);
      renderAssignments();
      element("agent-document-message").textContent = `${name} saved. Applies to new adventures.`;
    } catch (error) { notice(errorMessage(error)); }
    finally { button.disabled = false; }
  })();
});
element("save-agent").addEventListener("click", () => {
  void (async () => {
    const button = element<HTMLButtonElement>("save-agent");
    button.disabled = true;
    element("profile-message").textContent = "";
    try {
      const profile = agentProfileSchema.parse(JSON.parse(element<HTMLTextAreaElement>("profile-json").value));
      const saved = agentProfileSchema.parse(await api(`/agents/${encodeURIComponent(profile.id)}`, "PUT", profile));
      profiles = [...profiles.filter(profile => profile.id !== saved.id), saved].sort((a, b) => a.id.localeCompare(b.id));
      renderProfileEditor(saved.id);
      renderAssignments();
      element("profile-message").textContent = `${saved.name} saved. Applies to new episodes.`;
    } catch (error) { notice(errorMessage(error)); }
    finally { button.disabled = false; }
  })();
});
element("save-dungeon-master").addEventListener("click", () => {
  void (async () => {
    const button = element<HTMLButtonElement>("save-dungeon-master");
    button.disabled = true;
    element("dungeon-master-message").textContent = "";
    try {
      const config = dungeonMasterConfigSchema.parse(JSON.parse(element<HTMLTextAreaElement>("dungeon-master-json").value));
      dungeonMasterConfig = dungeonMasterConfigSchema.parse(await api("/dungeon-master", "PUT", config));
      element<HTMLTextAreaElement>("dungeon-master-json").value = pretty(dungeonMasterConfig);
      element("dungeon-master-message").textContent = "Supervisor saved. Applies to new episodes.";
      renderDungeonMaster();
    } catch (error) { notice(errorMessage(error)); }
    finally { button.disabled = false; }
  })();
});
element("save-clock").addEventListener("click", () => {
  const input = element<HTMLInputElement>("clock-rate");
  if (!input.reportValidity() || busy) return;
  const rate = input.valueAsNumber;
  const mode = element<HTMLSelectElement>("clock-mode").value;
  busy = true;
  renderBakery();
  void api("/clock", "PUT", { rate, mode })
    .then(payload => applyState(stateSchema.parse(payload)))
    .catch(error => notice(errorMessage(error)))
    .finally(() => { busy = false; renderBakery(); renderControls(); });
});
element("event-filter").addEventListener("change", () => {
  filter = element<HTMLSelectElement>("event-filter").value;
  renderTimeline();
});
element("timeline").addEventListener("toggle", event => {
  if (!(event.target instanceof HTMLDetailsElement)) return;
  const id = event.target.dataset.eventId;
  if (id) event.target.open ? expanded.add(id) : expanded.delete(id);
}, true);

element("discover").addEventListener("click", () => {
  const button = element<HTMLButtonElement>("discover");
  button.disabled = true;
  element("proxy-message").textContent = "Checking the configured proxy…";
  void api("/models").then(payload => {
    const models = modelsSchema.parse(payload).models;
    const select = element<HTMLSelectElement>("model-select");
    select.innerHTML = models.map(model => `<option value="${escape(model)}">${escape(model)}</option>`).join("");
    if (state?.config.proxy.model && models.includes(state.config.proxy.model)) select.value = state.config.proxy.model;
    element("proxy-status").textContent = "CONNECTED";
    element("proxy-status").className = "tag green";
    element("proxy-message").textContent = `${models.length} model(s) available. Save your selection before running.`;
  }).catch(error => {
    element("proxy-status").textContent = "UNREACHABLE";
    element("proxy-status").className = "tag amber";
    element("proxy-message").textContent = errorMessage(error);
  }).finally(() => { button.disabled = false; });
});
element("save-proxy").addEventListener("click", () => {
  if (!state) return;
  const config = structuredClone(state.config);
  config.proxy.base_url = element<HTMLInputElement>("proxy-url").value.trim();
  config.proxy.model = element<HTMLSelectElement>("model-select").value || null;
  void api("/config", "PUT", config).then(payload => {
    applyState(stateSchema.parse(payload));
    element("proxy-message").textContent = "Saved to data/config.json.";
  }).catch(error => notice(errorMessage(error)));
});
element("history-button").addEventListener("click", () => {
  element<HTMLDialogElement>("history-dialog").showModal();
  element("history-content").textContent = "Loading saved JSON episodes…";
  void api("/history").then(payload => {
    const runs = historySchema.parse(payload).runs;
    element("history-content").innerHTML = runs.length ? runs.map(run => `<button class="history-run" data-run-id="${escape(run.id)}"><strong>${escape(run.title)}</strong><span>${escape(run.status)} · Grade: ${escape(run.completion_analysis?.verdict ?? "not graded")} · ${run.actions} actions · ${escape(new Date(run.created_at).toLocaleString())}</span><small>${escape(run.id)}</small></button>`).join("") : '<p class="empty">No saved episodes.</p>';
  }).catch(error => { element("history-content").textContent = errorMessage(error); });
});
element("history-content").addEventListener("click", event => {
  if (!(event.target instanceof Element)) return;
  if (event.target.closest("[data-copy-archive]")) {
    void copyFinalStory(archivedStory);
    return;
  }
  const button = event.target.closest<HTMLButtonElement>("[data-run-id]");
  if (!button?.dataset.runId) return;
  void api(`/history/${button.dataset.runId}`).then(payload => {
    const detail = historyDetailSchema.parse(payload);
    const narrativeActor = detail.run.agents ? undefined : detail.run.actor_id;
    const members = detail.run.agent_profiles;
    archivedStory = buildNarrative(detail.events, narrativeActor, detail.run.completion_analysis?.story, members);
    const final = !["ready", "running", "paused"].includes(detail.run.status) &&
      !["pending", "running"].includes(detail.run.completion_analysis?.status ?? "") && archivedStory.length > 0;
    const actorStories = Object.keys(detail.run.agents ?? { [detail.run.actor_id]: {} }).map(actorId =>
      `<h3>${escape(members?.[actorId]?.name ?? slotLabel(actorId))}'s story</h3><div class="story-feed">${storyMarkup(buildStory(detail.events, actorId, members), isTeamStory(detail.events))}</div>`).join("");
    element("history-content").innerHTML = `<p class="muted">Archived episode — does not change the live world.</p><h3>Completion review</h3>${completionMarkup(detail.run.completion_analysis, detail.events)}<h3>Dungeon Master rulings</h3>${dungeonMasterMarkup(detail.events)}<h3>The adventure${detail.run.agents ? " / whole team" : ""}</h3><button class="button subtle" data-copy-archive ${final ? "" : "disabled"}>Copy final story</button><div class="narrative-prose">${narrativeMarkup(detail.events, narrativeActor, detail.run.completion_analysis, members)}</div>${actorStories}<details class="inspector"><summary>Full technical JSON</summary><h4>Evidence explained</h4>${evidenceMarkup(evidenceReferences(detail), detail.events, detail.run.completion_analysis?.story)}<pre>${escape(pretty(detail))}</pre></details>`;
  }).catch(error => { element("history-content").textContent = errorMessage(error); });
});
element("close-history").addEventListener("click", () => element<HTMLDialogElement>("history-dialog").close());

async function initialize(): Promise<void> {
  try {
    applyState(stateSchema.parse(await api("/state")));
    const [scenarioPayload, agentPayload, supervisorPayload] = await Promise.all([
      api("/scenarios"), api("/agents"), api("/dungeon-master")]);
    const choices = scenariosSchema.parse(scenarioPayload).scenarios;
    scenarioChoices = choices;
    profiles = agentsSchema.parse(agentPayload).agents;
    dungeonMasterConfig = dungeonMasterConfigSchema.parse(supervisorPayload);
    element<HTMLTextAreaElement>("dungeon-master-json").value = pretty(dungeonMasterConfig);
    renderDungeonMaster();
    const select = element<HTMLSelectElement>("scenario-select");
    select.innerHTML = choices.map(choice => `<option value="${escape(choice.id)}">${escape(choice.title)}</option>`).join("");
    selectedScenario ||= state?.run?.scenario_id ?? state?.config.world.scenario ?? "";
    select.value = selectedScenario;
    renderAssignments();
    renderProfileEditor();
    const history = eventsSchema.parse(await api("/events"));
    if (history.run_id === activeRun) events = history.events;
    renderTimeline();
  } catch (error) {
    notice(`Could not load the simulation: ${errorMessage(error)}`);
  }
  const stream = new EventSource("/api/stream");
  stream.onopen = () => {
    element("connection").textContent = "Live connection";
    document.querySelector(".live-dot")?.classList.add("connected");
  };
  stream.onerror = () => {
    element("connection").textContent = "Disconnected · retrying";
    document.querySelector(".live-dot")?.classList.remove("connected");
  };
  stream.addEventListener("snapshot", (event: MessageEvent<string>) => {
    try { applyState(stateSchema.parse(JSON.parse(event.data))); }
    catch (error) { notice(`Invalid state update: ${errorMessage(error)}`); }
  });
  stream.addEventListener("run-event", (message: MessageEvent<string>) => {
    try {
      const event = eventSchema.parse(JSON.parse(message.data));
      if (event.run_id !== activeRun || events.some(item => item.id === event.id)) return;
      events.push(event);
      renderTimeline();
    } catch (error) { notice(`Invalid event update: ${errorMessage(error)}`); }
  });
}
void initialize();
