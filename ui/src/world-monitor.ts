import { eventSchema, stateSchema } from "./contracts.ts";
import type { RunEvent, State } from "./contracts.ts";
import { timelineEventProjection, worldGanttMarkup } from "./world-gantt.ts";
import type { GanttScale } from "./world-gantt.ts";
import { slotLabel } from "./agent-profiles.ts";
import { actorPerspective } from "./world-view.ts";
import { canReopenAdventure, simulationClock, villageClockText, villageClockTime, viewNavigation } from "./view-mode.ts";
import { villageSystemsMarkup } from "./village-view.ts";
import { TownDialogue, townMapMarkup } from "./town-map.ts";
import type { TownMapOptions } from "./town-map.ts";

const escape = (text: string) => text.replaceAll("&", "&amp;").replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#39;");

export function monitoredAgents(state: State) {
  const { world, run } = state;
  const village = world?.village;
  const ids = new Set([
    ...Object.keys(world?.actors ?? {}), ...Object.keys(run?.agents ?? {}),
    ...Object.keys(village?.residents ?? {}), ...(run ? [run.actor_id] : [])
  ]);
  const residents = [...ids].map(id => {
    const actor = world?.actors[id];
    const resident = village?.residents[id];
    const entity = world?.entities[id];
    const { observation, memory } = actorPerspective(state, id);
    const locationId = actor?.location_id ?? entity?.location ?? observation?.self.location_id;
    const current = Boolean(actor || entity?.location);
    const location = locationId ? world?.entities[locationId]?.name
      ?? locationId.replaceAll("-", " ").replaceAll("_", " ") : "Location not yet available";
    const deciding = Boolean(state.in_flight && run?.actor_id === id
      && (run.status === "running" || run.status === "paused"));
    let activity = deciding ? "Deciding what to do" : current ? "Waiting for next turn" : "Activity not yet available";
    if (resident && village) {
      const life = resident.life;
      if (life?.sleeping) {
        activity = life.sleep_until === null ? "Sleeping" : `Sleeping until ${villageClockTime(life.sleep_until)}`;
      } else if (life?.work && life.work.finishes_at > village.minute) {
        activity = `${life.work.kind === "wagon_repair" ? "Repairing the wagon" : "Completing a batch"} until ${villageClockTime(life.work.finishes_at)}`;
      } else if (village.wagon?.automatic && village.wagon.driver === id) {
        activity = "Running the wagon delivery route";
      } else if (!deciding) {
        const available = Math.max(resident.available_at ?? 0, resident.busy_until);
        const leisure = village.dashboard?.activities.filter(item => item.actor === id
          && item.start <= village.minute && item.end > village.minute
          && !["completed", "cancelled", "interrupted", "failed"].includes(item.status)).at(-1);
        activity = !actor ? villageClockText(resident.activity || "Waiting for next turn")
          : leisure ? `${leisure.label} until ${villageClockTime(leisure.end)}`
          : available > village.minute ? `Available again at ${villageClockTime(available)}` : "Ready for next turn";
      }
    }
    const recent = Array.isArray(memory.recent) ? memory.recent.at(-1) : undefined;
    const action = recent && typeof recent === "object" && "action_id" in recent
      && typeof recent.action_id === "string" ? recent.action_id : "";
    const lastAction = action === "go" ? "Travel" : action.replace(/^village_/, "").replaceAll("_", " ");
    return {
      id, name: run?.agent_profiles?.[id]?.name ?? entity?.name ?? slotLabel(id),
      locationId: locationId ?? "", location, current, activity, lastAction, deciding, visitor: false
    };
  });
  const visitors = (village?.dashboard?.visitors ?? []).filter(visitor =>
    visitor.arrival <= (village?.minute ?? 0) && visitor.status !== "scheduled").map(visitor => ({
      id: visitor.id, name: visitor.name, locationId: visitor.location,
      location: world?.entities[visitor.location]?.name ?? visitor.location.replaceAll("-", " "),
      current: visitor.departure === null, activity: visitor.departure === null
        ? visitor.activities.at(-1)?.label ?? "Visiting" : "Departed",
      lastAction: visitor.activities.at(-1)?.label ?? "", deciding: false, visitor: true
    }));
  return [...residents, ...visitors];
}

export function worldMonitorMarkup(
  state: State, events: readonly RunEvent[] = [], scale: GanttScale = {}, map: TownMapOptions = {}
): string {
  const agents = monitoredAgents(state);
  const clock = simulationClock(state.world ?? state.observation, state.observation);
  const status = state.run?.status ?? "idle";
  const active = ["ready", "running", "paused"].includes(status);
  const statuses: Record<string, string> = {
    idle: "No adventure", ready: "Ready", running: "Running", paused: "Paused"
  };
  const statusText = status === "budget_exceeded" ? "Budget limit reached" : statuses[status] ?? "Stopped";
  const groups = new Map<string, typeof agents>();
  for (const agent of agents) {
    if (agent.visitor && !agent.current) continue;
    const group = groups.get(agent.locationId) ?? [];
    group.push(agent);
    groups.set(agent.locationId, group);
  }
  return `<section class="monitor-summary" aria-label="World summary">
    <div><span class="eyebrow">WORLD</span><h2>${escape(state.run?.title ?? "No active adventure")}</h2>
      <p><span class="status ${escape(status)}">${statusText}</span> /
        ${agents.some(agent => agent.visitor) ? `${agents.filter(agent => !agent.visitor).length} residents /
          ${agents.filter(agent => agent.visitor && agent.current).length} visitors present`
          : `${agents.length} ${agents.length === 1 ? "agent" : "agents"}`} / Read-only</p></div>
    <div class="monitor-clock" role="timer" aria-label="Current game time">
      <span class="eyebrow">CURRENT GAME TIME</span><strong>${clock?.time ?? "Untimed world"}</strong>
      ${clock ? `<small>${clock.description}</small>` : ""}</div>
  </section>
  ${state.run?.reason ? `<p class="notice">${escape(villageClockText(state.run.reason))}</p>` : ""}
  ${status === "budget_exceeded" ? '<p class="muted">Keep this world and continue: <a class="button" href="/?budget=extend">Extend budget in Player view</a></p>' : ""}
  ${canReopenAdventure(state.run, Boolean(state.world)) ? `<p class="muted">The day is unfinished. <a class="button" href="/">${status === "error" ? "Recover the interrupted day in Player view" : "Continue the unfinished day in Player view"}</a></p>` : ""}
  ${!active && agents.length ? '<p class="muted">Last recorded world state. The adventure is not running.</p>' : ""}
  ${!state.world && agents.length ? '<p class="muted">World snapshot unavailable. Locations below are last observed, not live positions.</p>' : ""}
  <div class="monitor-visuals${state.world?.village ? " with-town-map" : ""}">
    ${townMapMarkup(state, agents, map)}
    ${worldGanttMarkup(state, events, agents, scale)}
  </div>
  ${villageSystemsMarkup(state, [], state.observation?.village?.research?.enabled)}
  <div class="monitor-locations">${[...groups.values()].map(group =>
    `<section class="monitor-location" aria-label="${escape(group[0].location)}">
      <h2>${escape(group[0].location)} <small>${group.length}</small></h2>
      ${group.map(agent => `<article class="monitor-agent${agent.deciding ? " deciding" : ""}" data-monitor-agent="${escape(agent.id)}">
        <h3>${escape(agent.name)}</h3>
        ${agent.visitor ? '<span class="tag">Visitor</span>' : ""}
        ${!agent.current ? '<small class="muted">Last observed location / not live</small>' : ""}
        <p>${escape(agent.activity)}</p>
        ${agent.lastAction ? `<small class="muted">Last action: ${escape(agent.lastAction)}</small>` : ""}
      </article>`).join("")}
    </section>`).join("") || '<p class="muted">No agents to monitor yet. Start an adventure in Player or Admin view.</p>'}</div>`;
}

export function startWorldMonitor(app: HTMLDivElement): void {
  app.className = "world-view";
  document.title = "NPCSIM · World view";
  app.innerHTML = `<main>
    <header><div><span class="eyebrow">NPCSIM / LIVE WORLD</span><h1>World view<span class="dot">.</span></h1></div>
      <div class="header-right"><span class="live-dot"></span><span id="monitor-connection">Connecting</span>${viewNavigation("world")}</div>
    </header>
    <p id="monitor-error" class="notice" role="alert" hidden></p>
    <div id="monitor-content"><p class="muted">Loading the world...</p></div>
  </main>`;
  const content = app.querySelector<HTMLDivElement>("#monitor-content")!;
  const error = app.querySelector<HTMLParagraphElement>("#monitor-error")!;
  const connection = app.querySelector<HTMLSpanElement>("#monitor-connection")!;
  const dot = app.querySelector<HTMLSpanElement>(".live-dot")!;
  let previous = "";
  let state: State | null = null;
  const events = new Map<string, RunEvent>();
  const seen = new Set<string>();
  let historyReady = false;
  let zoom = 1;
  let renderedZoom = 1;
  let mapZoom = 1;
  let selectedActorId: string | undefined;
  let dialogue = new TownDialogue();
  let liveAfterSequence = 0;
  let bubbleTimer: ReturnType<typeof setTimeout> | undefined;
  let frame: number | null = null;
  const draw = () => {
    frame = null;
    if (!state) return;
    historyReady ||= seen.size >= state.event_count;
    const old = content.querySelector<HTMLDivElement>(".gantt-scroll");
    const viewportWidth = old?.clientWidth ?? Math.max(120,
      (state.world?.village && content.clientWidth > 1100 ? (content.clientWidth - 18) / 2.15 : content.clientWidth) - 130);
    const now = Date.now();
    const markup = worldMonitorMarkup(state, historyReady ? [...events.values()] : [], { zoom, viewportWidth },
      { zoom: mapZoom, selectedActorId, dialogue, now });
    clearTimeout(bubbleTimer);
    const expiries = dialogue.active(now).map(line => line.expiresAt);
    if (expiries.length) bubbleTimer = setTimeout(schedule, Math.min(...expiries) - now + 1);
    if (markup !== previous) {
      const mapScroll = content.querySelector<HTMLDivElement>(".town-map-scroll");
      const mapLeft = mapScroll?.scrollLeft ?? 0;
      const mapTop = mapScroll?.scrollTop ?? 0;
      const mapActorFocus = document.activeElement?.getAttribute("data-map-actor");
      const mapZoomFocus = document.activeElement?.getAttribute("data-map-zoom");
      const mapHadFocus = document.activeElement === mapScroll;
      const left = old?.scrollLeft ?? 0;
      const following = !old || old.scrollWidth - old.clientWidth - left < 24;
      const centre = old ? (left + old.clientWidth / 2) / old.scrollWidth : 0.5;
      const focused = document.activeElement?.getAttribute("data-gantt-focus");
      const zoomFocus = document.activeElement?.getAttribute("data-timeline-zoom");
      const hadFocus = old?.contains(document.activeElement);
      const zoomControls = renderedZoom === zoom ? content.querySelector(".gantt-zoom") : null;
      content.innerHTML = markup;
      const nextMapScroll = content.querySelector<HTMLDivElement>(".town-map-scroll");
      if (nextMapScroll) {
        nextMapScroll.scrollLeft = mapLeft;
        nextMapScroll.scrollTop = mapTop;
        if (mapHadFocus) nextMapScroll.focus({ preventScroll: true });
      }
      if (mapActorFocus) {
        [...content.querySelectorAll<HTMLButtonElement>("[data-map-actor]")]
          .find(button => button.dataset.mapActor === mapActorFocus)?.focus({ preventScroll: true });
      }
      if (mapZoomFocus) {
        const button = content.querySelector<HTMLButtonElement>(`[data-map-zoom="${mapZoomFocus}"]`);
        (button?.disabled ? content.querySelector<HTMLButtonElement>('[data-map-zoom="fit"]') : button)
          ?.focus({ preventScroll: true });
      }
      if (zoomControls) content.querySelector(".gantt-zoom")?.replaceWith(zoomControls);
      const scroll = content.querySelector<HTMLDivElement>(".gantt-scroll");
      if (scroll) {
        scroll.scrollLeft = following ? scroll.scrollWidth
          : renderedZoom !== zoom ? centre * scroll.scrollWidth - scroll.clientWidth / 2 : left;
        if (hadFocus) {
          const target = focused ? [...scroll.querySelectorAll<SVGElement>("[data-gantt-focus]")]
            .find(element => element.getAttribute("data-gantt-focus") === focused) : undefined;
          (target ?? scroll).focus({ preventScroll: true });
        }
      }
      if (zoomFocus) {
        const button = content.querySelector<HTMLButtonElement>(`[data-timeline-zoom="${zoomFocus}"]`);
        (button?.disabled ? content.querySelector<HTMLButtonElement>('[data-timeline-zoom="fit"]') : button)
          ?.focus({ preventScroll: true });
      }
      renderedZoom = zoom;
      previous = markup;
    }
    error.hidden = true;
  };
  const schedule = () => {
    if (frame === null) frame = requestAnimationFrame(() => {
      try { draw(); }
      catch (failure) { report(failure); }
    });
  };
  const render = (payload: unknown) => {
    const next = stateSchema.parse(payload);
    if (next.run?.id !== state?.run?.id) {
      events.clear();
      seen.clear();
      historyReady = false;
      zoom = renderedZoom = 1;
      mapZoom = 1;
      selectedActorId = undefined;
      dialogue = new TownDialogue();
      liveAfterSequence = next.event_count;
    }
    // Replayed events on reconnect belong in history, not in new live bubbles.
    if (awaitingSnapshot) liveAfterSequence = Math.max(liveAfterSequence, next.event_count);
    awaitingSnapshot = false;
    state = next;
    schedule();
  };
  const report = (failure: unknown) => {
    error.textContent = `Could not update the world: ${failure instanceof Error ? failure.message : String(failure)}`;
    error.hidden = false;
  };
  content.addEventListener("click", event => {
    if (!(event.target instanceof Element)) return;
    const actor = event.target.closest<HTMLButtonElement>("[data-map-actor]");
    if (actor) {
      selectedActorId = actor.dataset.mapActor === selectedActorId ? undefined : actor.dataset.mapActor;
      schedule();
      return;
    }
    const mapButton = event.target.closest<HTMLButtonElement>("[data-map-zoom]");
    if (mapButton && !mapButton.disabled) {
      mapZoom = mapButton.dataset.mapZoom === "fit" ? 1
        : mapButton.dataset.mapZoom === "in" ? Math.min(3, mapZoom + 0.5) : Math.max(1, mapZoom - 0.5);
      schedule();
      return;
    }
    const button = event.target.closest<HTMLButtonElement>("[data-timeline-zoom]");
    if (!button || button.disabled) return;
    zoom = button.dataset.timelineZoom === "fit" ? 1
      : button.dataset.timelineZoom === "in" ? Math.min(8, zoom * 2) : Math.max(1, zoom / 2);
    schedule();
  });
  window.addEventListener("resize", schedule);
  let awaitingSnapshot = true;
  const initialize = async () => {
    awaitingSnapshot = true;
    try {
      const response = await fetch("/api/state");
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      render(await response.json());
    } catch (failure) { report(failure); }
    const stream = new EventSource("/api/stream?compact=true");
    stream.onopen = () => {
      connection.textContent = "Live connection";
      dot.classList.add("connected");
    };
    stream.onerror = () => {
      connection.textContent = "Reconnecting / last snapshot";
      dot.classList.remove("connected");
      awaitingSnapshot = true;
    };
    stream.addEventListener("snapshot", (event: MessageEvent<string>) => {
      try { render(JSON.parse(event.data)); }
      catch (failure) { report(failure); }
    });
    stream.addEventListener("run-event", (message: MessageEvent<string>) => {
      try {
        const event = eventSchema.parse(JSON.parse(message.data));
        if (event.run_id !== state?.run?.id || seen.has(event.id)) return;
        dialogue.record(event, event.sequence > liveAfterSequence, Date.now());
        const projected = timelineEventProjection(event);
        seen.add(event.id);
        if (projected) events.set(event.id, projected);
        schedule();
      } catch (failure) { report(failure); }
    });
    window.addEventListener("pagehide", () => {
      stream.close();
      clearTimeout(bubbleTimer);
      if (frame !== null) cancelAnimationFrame(frame);
      frame = null;
    }, { once: true });
  };
  window.addEventListener("pageshow", event => {
    if (event.persisted) void initialize();
  });
  void initialize();
}
