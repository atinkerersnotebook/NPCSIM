import { eventSchema, stateSchema } from "./contracts.ts";
import type { RunEvent, State } from "./contracts.ts";
import { timelineEventProjection, worldGanttMarkup } from "./world-gantt.ts";
import type { GanttActivity, GanttScale } from "./world-gantt.ts";
import { renderDungeonAppeal } from "./dungeon-appeal.ts";
import { slotLabel } from "./agent-profiles.ts";
import { actorPerspective } from "./world-view.ts";
import { canReopenAdventure, simulationClock, villageClockText, villageClockTime, dashboardHeader, dashboardWorldSummary, trackDashboardHeader } from "./view-mode.ts";
import { villageSystemsMarkup } from "./village-view.ts";
import { TownDialogue, townMapMarkup } from "./town-map.ts";
import type { TownMapOptions } from "./town-map.ts";
import { tradeNote } from "./trade-notes.ts";
import { actionSpeedometerMarkup } from "./action-speedometer.ts";
import { npcPortrait } from "./npc-sprite.ts";
import { sectionTabPanel, sectionTabs } from "./section-tabs.ts";
import { sceneAnchors, sceneHeight, sceneWidth } from "./millhaven-orthographic.ts";

const escape = (text: string) => text.replaceAll("&", "&amp;").replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#39;");

export async function replayWorldHistory(runId: string, through: number, after: number,
  record: (event: RunEvent) => void, request = fetch, signal?: AbortSignal): Promise<void> {
  const load = async (path: string) => {
    const response = await request(path, { signal });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  };
  if (through > 0 && after === 0) {
    // With no positive cursor, the paged events endpoint returns the latest events.
    const first = eventSchema.parse(await load(`/api/events/1?run_id=${encodeURIComponent(runId)}`));
    if (first.run_id !== runId || first.sequence !== 1) throw new Error("World history changed while loading.");
    record(first);
    after = first.sequence;
  }
  while (after < through) {
    const limit = Math.min(500, through - after);
    const page = await load(`/api/events?compact=true&after=${after}&limit=${limit}`) as {
      run_id: string; events: unknown[];
    };
    if (page.run_id !== runId || !page.events.length) throw new Error("World history changed while loading.");
    for (const raw of page.events) {
      const event = eventSchema.parse(raw);
      if (event.run_id !== runId || event.sequence !== after + 1 || event.sequence > through) {
        throw new Error("World history contains an unexpected event sequence.");
      }
      record(event);
      after = event.sequence;
    }
  }
}

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
      } else if (!deciding && village.dashboard?.research?.some(item => item.owner === id
        && item.status === "in_progress" && resident.activity === `Research: ${item.title}`)) {
        const project = village.dashboard.research.find(item => item.owner === id
          && item.status === "in_progress" && resident.activity === `Research: ${item.title}`)!;
        activity = `Researching ${project.title}: ${project.worked_minutes}/${project.required_minutes} study min, `
          + `${project.successes}/${project.required_successes} successful trials`;
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

function worldNowMarkup(state: State, events: readonly RunEvent[], agents: ReturnType<typeof monitoredAgents>,
  active: boolean, historyReady: boolean): string {
  const village = state.world?.village;
  const turn = active ? agents.find(agent => agent.deciding) : undefined;
  const walks = new Set<string>();
  let movement: { actor: string; destination: string; minute?: number; sequence: number } | undefined;
  for (const event of events) {
    const payload = event.payload as { action_id?: unknown; request_id?: unknown } | null;
    if (event.kind === "action_request" && payload?.action_id === "go" && typeof payload.request_id === "string") {
      walks.add(payload.request_id);
    }
  }
  for (const event of events) {
    const payload = event.payload as { outcome?: unknown; request_id?: unknown; observation?: {
      self?: { location_id?: unknown }; village?: { minute?: unknown }
    } } | null;
    if (event.kind !== "action_result" || payload?.outcome !== "APPLIED"
      || typeof payload.request_id !== "string" || !walks.has(payload.request_id)
      || (movement && event.sequence <= movement.sequence)) continue;
    const observed = payload.observation;
    const destination = observed?.self?.location_id;
    if (typeof destination !== "string") continue;
    const minute = observed?.village?.minute;
    movement = { actor: event.actor_id, destination, sequence: event.sequence, minute: typeof minute === "number" ? minute : undefined };
  }
  const locationName = (id: string) => state.world?.entities[id]?.name ?? id.replaceAll(/[-_]/g, " ");
  const actorName = (id: string) => agents.find(agent => agent.id === id)?.name ?? slotLabel(id);
  const blocked = [
    ...Object.values(village?.tasks ?? {}).map(item => ({ title: item.kind.replaceAll("_", " "), ...item })),
    ...(village?.dashboard?.maintenance ?? []), ...(village?.dashboard?.research ?? [])
  ].filter(item => item.status === "blocked");
  const turnText = turn ? `${turn.name} · ${turn.current && state.world ? turn.location : `Last observed: ${turn.location}`}`
    : active ? "No turn in progress" : "Adventure is not running";
  const movementText = movement ? `${actorName(movement.actor)} → ${locationName(movement.destination)}`
    : historyReady ? "No movement recorded" : "Reading world history…";
  return `<section class="monitor-now" aria-labelledby="monitor-now-title"><h2 id="monitor-now-title">World now</h2><dl>
    <div><dt>Current turn</dt><dd>${escape(turnText)}</dd></div>
    <div><dt>Last recorded move${movement?.minute === undefined ? "" : ` · ${villageClockTime(movement.minute)}`}</dt><dd>${escape(movementText)}</dd></div>
    ${village ? `<div><dt>${active ? "Blocked work" : "Last recorded blocked work"} (${blocked.length})</dt><dd>${blocked.length ? `${escape(villageClockText(blocked[0].title))}${blocked[0].blocker ? ` · ${escape(villageClockText(blocked[0].blocker))}` : ""}${blocked.length > 1 ? ` · ${blocked.length - 1} more` : ""}` : "None recorded"}</dd></div>` : ""}
  </dl></section>`;
}

export function worldMonitorMarkup(
  state: State, events: readonly RunEvent[] = [], scale: GanttScale = {}, map: TownMapOptions = {},
  historyReady = true, sceneZoom = 1
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
  const timeline = worldGanttMarkup(state, events, agents, { ...scale, linkResidents: true, selectedActorId: map.selectedActorId });
  const systems = villageSystemsMarkup(state, state.observation?.village?.research?.enabled);
  const trades = state.world?.village?.dashboard?.trades ?? [];
  return `${dashboardWorldSummary(state.run?.title ?? "No active adventure",
    `<p><span class="status ${escape(status)}">${statusText}</span> /
      ${agents.some(agent => agent.visitor) ? `${agents.filter(agent => !agent.visitor).length} residents /
        ${agents.filter(agent => agent.visitor && agent.current).length} visitors present`
        : `${agents.length} ${agents.length === 1 ? "agent" : "agents"}`} / Read-only</p>`,
    `<div class="monitor-time-cards">
      ${actionSpeedometerMarkup(state.run)}
      <div class="monitor-clock" role="timer" aria-label="Current game time">
        <span class="eyebrow">CURRENT GAME TIME</span><strong>${clock?.time ?? "Untimed world"}</strong>
        ${clock ? `<small>${clock.description}</small>` : ""}
      </div>
    </div>`)}
  <section ${sectionTabPanel("monitor-overview", "Overview")}>
  ${worldNowMarkup(state, events, agents, active, historyReady)}
  ${state.run?.reason ? `<p class="notice">${escape(villageClockText(state.run.reason))}</p>` : ""}
  ${status === "budget_exceeded" ? '<p class="muted">Keep this world and continue: <a class="button" href="/?budget=extend">Extend budget in Admin view</a></p>' : ""}
  ${canReopenAdventure(state.run, Boolean(state.world)) ? `<p class="muted">The day is unfinished. <a class="button" href="/">${status === "error" ? "Recover the interrupted day in Player view" : "Continue the unfinished day in Player view"}</a></p>` : ""}
  ${!active && agents.length ? '<p class="muted">Last recorded world state. The adventure is not running.</p>' : ""}
  ${!state.world && agents.length ? '<p class="muted">World snapshot unavailable. Locations below are last observed, not live positions.</p>' : ""}
  </section>
  ${state.world?.village ? `<section ${sectionTabPanel("monitor-map", "Map")}>${townMapMarkup(state, agents, map)}</section>
  <section ${sectionTabPanel("monitor-scene", "Millhaven Scene")}>${townMapMarkup(state, agents, { ...map, zoom: sceneZoom, presentation: "scene" })}</section>` : ""}
  ${timeline ? `<section ${sectionTabPanel("monitor-timeline", "Timeline")}>${timeline}</section>` : ""}
  <section ${sectionTabPanel("monitor-residents", "Residents")}><h2 id="monitor-residents-title">Residents &amp; agents</h2>
  <div class="monitor-locations">${[...groups.values()].map(group =>
    `<section class="monitor-location" aria-label="${escape(group[0].location)}">
      <h2>${escape(group[0].location)} <small>${group.length}</small></h2>
      ${group.map(agent => `<article class="monitor-agent${agent.deciding ? " deciding" : ""}" data-monitor-agent="${escape(agent.id)}">
        <h3>${npcPortrait(agent.id)} <button type="button" class="monitor-resident-link" data-resident-select="${escape(agent.id)}" aria-label="Highlight ${escape(agent.name)}" aria-pressed="${agent.id === map.selectedActorId}">${escape(agent.name)}</button></h3>
        ${agent.visitor ? '<span class="tag">Visitor</span>' : ""}
        ${!agent.current ? '<small class="muted">Last observed location / not live</small>' : ""}
        <p>${escape(agent.activity)}</p>
        ${agent.lastAction ? `<small class="muted">Last action: ${escape(agent.lastAction)}</small>` : ""}
      </article>`).join("")}
    </section>`).join("") || '<p class="muted">No agents to monitor yet. Start an adventure in Player or Admin view.</p>'}</div></section>
  ${trades.length ? `<section ${sectionTabPanel("monitor-trades", "Trades")}><div class="monitor-trades">
    <h2>Recorded completed trades</h2>
    <p class="muted">Settled goods and payments only; trading presence and accepted orders are not completed deals.</p>
    <ul>${trades.slice().sort((a, b) => b.minute - a.minute).slice(0, 20)
      .map(trade => `<li>${escape(villageClockTime(trade.minute))} / ${escape(tradeNote(state, trade))}</li>`).join("")}</ul>
  </div></section>` : ""}
  ${systems ? `<section ${sectionTabPanel("monitor-systems", "Village systems")}><div class="monitor-systems">${systems}</div></section>` : ""}`;
}

export function startWorldMonitor(app: HTMLDivElement): void {
  app.className = "world-view";
  document.title = "NPCSIM · World view";
  app.innerHTML = `<main class="dashboard-page"><div class="command-shell">
    ${dashboardHeader("world", "LIVE WORLD", "World view", '<span class="live-dot"></span><span id="monitor-connection">Connecting</span>')}
    <nav class="dashboard-tabs" role="tablist" aria-label="World sections" hidden></nav></div>
    <p id="monitor-error" class="notice" role="alert" hidden></p>
    <div id="monitor-content"><p class="muted">Loading the world...</p></div>
  </main>`;
  trackDashboardHeader(app);
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
  let recent = false;
  let activity: GanttActivity = "all";
  let resetTimelineScroll = false;
  const mapViews = {
    "monitor-map": { zoom: 1, left: 0, top: 0 },
    "monitor-scene": { zoom: 1, left: 0, top: 0 }
  };
  const activeMapScroll = () => content.querySelector<HTMLDivElement>(
    ":is(#monitor-map, #monitor-scene):not([hidden]) .town-map-scroll");
  const mapView = (element: Element) => element.closest("#monitor-scene")
    ? mapViews["monitor-scene"] : mapViews["monitor-map"];
  let centerScene = true;
  let sceneZoomCenter: { x: number; y: number } | undefined;
  let sceneDrag: { pointer: number; x: number; y: number; left: number; top: number } | undefined;
  let timelineLeft = 0;
  let timelineCentre = 0.5;
  let followingTimeline = true;
  const rememberScroll = () => {
    const mapScroll = activeMapScroll();
    if (mapScroll) {
      const view = mapView(mapScroll);
      view.left = mapScroll.scrollLeft;
      view.top = mapScroll.scrollTop;
    }
    const scroll = content.querySelector<HTMLDivElement>("#monitor-timeline:not([hidden]) .gantt-scroll");
    if (scroll?.clientWidth) {
      timelineLeft = scroll.scrollLeft;
      followingTimeline = scroll.scrollWidth - scroll.clientWidth - timelineLeft < 24;
      timelineCentre = (timelineLeft + scroll.clientWidth / 2) / scroll.scrollWidth;
    }
  };
  const restoreScroll = () => {
    const mapScroll = activeMapScroll();
    if (mapScroll) {
      const view = mapView(mapScroll);
      const scene = view === mapViews["monitor-scene"];
      if (scene && (centerScene || sceneZoomCenter)) {
        const square = sceneAnchors["village-square"];
        const center = centerScene ? { x: square.x / sceneWidth, y: square.y / sceneHeight } : sceneZoomCenter!;
        const canvas = mapScroll.querySelector(".town-map-canvas")!.getBoundingClientRect();
        const viewport = mapScroll.getBoundingClientRect();
        mapScroll.scrollTo(center.x * canvas.width + canvas.left - viewport.left + mapScroll.scrollLeft - mapScroll.clientWidth / 2,
          center.y * canvas.height + canvas.top - viewport.top + mapScroll.scrollTop - mapScroll.clientHeight / 2);
        centerScene = false;
        sceneZoomCenter = undefined;
      } else {
        mapScroll.scrollTo(view.left, view.top);
      }
    }
    const scroll = content.querySelector<HTMLDivElement>("#monitor-timeline:not([hidden]) .gantt-scroll");
    if (scroll) scroll.scrollLeft = followingTimeline || resetTimelineScroll ? scroll.scrollWidth
      : renderedZoom !== zoom ? timelineCentre * scroll.scrollWidth - scroll.clientWidth / 2 : timelineLeft;
    return scroll;
  };
  const tabs = sectionTabs(app.querySelector<HTMLElement>(".dashboard-tabs")!, content, phase => {
    if (phase === "before") rememberScroll();
    else {
      restoreScroll();
      schedule();
    }
  });
  let selectedActorId: string | undefined;
  let dialogue = new TownDialogue();
  let liveAfterSequence = 0;
  let bubbleTimer: ReturnType<typeof setTimeout> | undefined;
  let frame: number | null = null;
  let pointerPosition: { x: number; y: number } | null = null;
  const draw = () => {
    frame = null;
    if (!state || sceneDrag) return;
    renderDungeonAppeal(state);
    historyReady ||= seen.size >= state.event_count;
    const old = content.querySelector<HTMLDivElement>("#monitor-timeline:not([hidden]) .gantt-scroll");
    const viewportWidth = old?.clientWidth || content.clientWidth - (window.matchMedia("(max-width: 700px)").matches ? 102 : 132);
    const now = Date.now();
    const markup = worldMonitorMarkup(state, historyReady ? [...events.values()] : [], { zoom, viewportWidth, recent, activity },
      { zoom: mapViews["monitor-map"].zoom, selectedActorId, dialogue, now }, historyReady, mapViews["monitor-scene"].zoom);
    clearTimeout(bubbleTimer);
    const expiries = dialogue.active(now).map(line => line.expiresAt);
    if (expiries.length) bubbleTimer = setTimeout(schedule, Math.min(...expiries) - now + 1);
    if (markup !== previous) {
      const mapScroll = activeMapScroll();
      rememberScroll();
      const openSystems = new Set([...content.querySelectorAll<HTMLDetailsElement>("#monitor-systems details[open]")]
        .map(details => details.querySelector("summary")?.textContent));
      const actorFocus = document.activeElement?.getAttribute("data-map-actor")
        ?? document.activeElement?.getAttribute("data-scene-actor");
      const residentFocus = document.activeElement?.getAttribute("data-resident-select");
      const residentArea = document.activeElement?.closest("#monitor-residents, #monitor-timeline")?.id;
      const filterFocus = ["data-timeline-recent", "data-timeline-activity", "data-timeline-reset"]
        .find(attribute => document.activeElement?.hasAttribute(attribute));
      const mapZoomFocus = document.activeElement?.getAttribute("data-map-zoom");
      const markerFocus = document.activeElement?.getAttribute("data-location-marker");
      const panelFocus = document.activeElement?.matches('[role="tabpanel"]') ? document.activeElement.id : undefined;
      const systemsFocus = document.activeElement?.matches("#monitor-systems summary") ? document.activeElement.textContent : null;
      const mapHadFocus = document.activeElement === mapScroll;
      const focused = document.activeElement?.getAttribute("data-gantt-focus");
      const nameFocus = document.activeElement?.getAttribute("data-gantt-name");
      const zoomFocus = document.activeElement?.getAttribute("data-timeline-zoom");
      const hadFocus = old?.contains(document.activeElement);
      const zoomControls = renderedZoom === zoom ? content.querySelector(".gantt-zoom") : null;
      content.innerHTML = markup;
      tabs.sync();
      const scroll = restoreScroll();
      if (panelFocus) document.getElementById(panelFocus)?.focus({ preventScroll: true });
      for (const details of content.querySelectorAll<HTMLDetailsElement>("#monitor-systems details")) {
        const summary = details.querySelector("summary");
        if (openSystems.has(summary?.textContent)) details.open = true;
        if (systemsFocus && summary?.textContent === systemsFocus) summary.focus({ preventScroll: true });
      }
      if (filterFocus) content.querySelector<HTMLElement>(`[${filterFocus}]`)?.focus({ preventScroll: true });
      if (residentFocus && residentArea) {
        [...content.querySelectorAll<HTMLButtonElement>(`#${residentArea} [data-resident-select]`)]
          .find(button => button.dataset.residentSelect === residentFocus)?.focus({ preventScroll: true });
      }
      const mapPanel = activeMapScroll()?.closest(".town-map");
      if (mapHadFocus) activeMapScroll()?.focus({ preventScroll: true });
      if (actorFocus && mapPanel) {
        [...mapPanel.querySelectorAll<HTMLButtonElement>("[data-map-actor], [data-scene-actor]")]
          .find(button => (button.dataset.mapActor ?? button.dataset.sceneActor) === actorFocus)?.focus({ preventScroll: true });
      }
      if (markerFocus && mapPanel) {
        [...mapPanel.querySelectorAll<HTMLButtonElement>("[data-location-marker]")]
          .find(button => button.dataset.locationMarker === markerFocus)?.focus({ preventScroll: true });
      }
      if (mapZoomFocus && mapPanel) {
        const button = mapPanel.querySelector<HTMLButtonElement>(`[data-map-zoom="${mapZoomFocus}"]`);
        (button?.disabled ? mapPanel.querySelector<HTMLButtonElement>('[data-map-zoom="fit"]') : button)
          ?.focus({ preventScroll: true });
      }
      if (nameFocus) [...content.querySelectorAll<HTMLElement>("[data-gantt-name]")]
        .find(label => label.dataset.ganttName === nameFocus)?.focus({ preventScroll: true });
      if (zoomControls) content.querySelector(".gantt-zoom")?.replaceWith(zoomControls);
      if (scroll && hadFocus) {
        const target = focused ? [...scroll.querySelectorAll<SVGElement>("[data-gantt-focus]")]
          .find(element => element.getAttribute("data-gantt-focus") === focused) : undefined;
        (target ?? scroll).focus({ preventScroll: true });
        if (target?.matches(".gantt-bar, .gantt-marker")) showGanttTooltip(target);
      }
      if (zoomFocus) {
        const button = content.querySelector<HTMLButtonElement>(`[data-timeline-zoom="${zoomFocus}"]`);
        (button?.disabled ? content.querySelector<HTMLButtonElement>('[data-timeline-zoom="fit"]') : button)
          ?.focus({ preventScroll: true });
      }
      if (pointerPosition) {
        const hovered = document.elementFromPoint(pointerPosition.x, pointerPosition.y)?.closest(".gantt-bar, .gantt-marker");
        if (hovered) showGanttTooltip(hovered);
      }
      resetTimelineScroll = false;
      renderedZoom = zoom;
      previous = markup;
      if (scroll && scroll.clientWidth !== viewportWidth) schedule();
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
      for (const view of Object.values(mapViews)) {
        view.zoom = 1;
        view.left = view.top = 0;
      }
      centerScene = true;
      sceneZoomCenter = undefined;
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
  const showGanttTooltip = (bar: Element) => {
    const bubble = content.querySelector<HTMLDivElement>("#gantt-tooltip");
    if (!bubble) return;
    const text = (name: string) => bar.getAttribute(`data-gantt-${name}`) ?? "";
    const element = (tag: string, className: string, value: string) => {
      const node = document.createElement(tag);
      node.className = className;
      node.textContent = value;
      return node;
    };
    const title = text("title");
    const status = text("status").replaceAll("_", " ");
    const header = element("div", "gantt-tooltip-heading", "");
    header.append(element("span", "gantt-tooltip-icon", text("icon")),
      element("div", "gantt-tooltip-title", ""));
    header.lastElementChild!.append(element("small", "", text("agent-name")), element("strong", "", title));
    bubble.replaceChildren(header);
    if (status) bubble.append(element("span", "gantt-tooltip-status", status));
    const facts = element("dl", "gantt-tooltip-facts", "");
    for (const [label, value] of [["When", text("range")], ["Duration", text("duration")],
      ["Location", text("place")]]) {
      if (!value) continue;
      facts.append(element("dt", "", label), element("dd", "", value));
    }
    bubble.append(facts);
    const detail = text("detail");
    if (detail && detail !== title && detail !== `${title} / ${text("status")}`) {
      const result = element("div", "gantt-tooltip-result", "");
      result.append(element("small", "", "Recorded detail"), element("p", "", detail));
      bubble.append(result);
    }
    for (const [name, label] of [["arguments", "Action details"], ["subgoal", "Current subgoal"],
      ["expected", "Expected outcome"], ["plan", "Plan"]] as const) {
      const value = text(name);
      if (!value) continue;
      const section = element("div", "gantt-tooltip-result", "");
      section.append(element("small", "", label), element("p", "", value));
      bubble.append(section);
    }
    if (text("reasoning")) {
      const reasoning = element("div", "gantt-tooltip-result", "");
      reasoning.append(element("small", "", "Why this action"), element("p", "", text("reasoning")));
      bubble.append(reasoning);
    }
    bubble.hidden = false;
    const bounds = bar.getBoundingClientRect();
    const { width, height } = bubble.getBoundingClientRect();
    bubble.style.left = `${Math.max(8, Math.min(bounds.left, window.innerWidth - width - 8))}px`;
    bubble.style.top = `${Math.max(8, Math.min(bounds.bottom + 8 + height > window.innerHeight
      ? bounds.top - height - 8 : bounds.bottom + 8, window.innerHeight - height - 8))}px`;
  };
  const hideGanttTooltip = () => {
    const bubble = content.querySelector<HTMLDivElement>("#gantt-tooltip");
    if (bubble) bubble.hidden = true;
  };
  content.addEventListener("pointerover", event => {
    if (event.target instanceof Element) {
      const bar = event.target.closest(".gantt-bar, .gantt-marker");
      if (bar) {
        pointerPosition = { x: event.clientX, y: event.clientY };
        showGanttTooltip(bar);
      }
    }
  });
  content.addEventListener("pointerdown", event => {
    if (!event.isPrimary || event.button !== 0 || event.pointerType === "touch" || !(event.target instanceof Element)
      || !event.target.closest(".scene-terrain")) return;
    const viewport = activeMapScroll();
    if (!viewport) return;
    sceneDrag = { pointer: event.pointerId, x: event.clientX, y: event.clientY,
      left: viewport.scrollLeft, top: viewport.scrollTop };
    viewport.setPointerCapture(event.pointerId);
    viewport.classList.add("scene-dragging");
    viewport.focus({ preventScroll: true });
    event.preventDefault();
  });
  content.addEventListener("lostpointercapture", event => {
    if (sceneDrag?.pointer !== event.pointerId) return;
    sceneDrag = undefined;
    if (event.target instanceof Element) event.target.classList.remove("scene-dragging");
    schedule();
  });
  content.addEventListener("pointermove", event => {
    if (sceneDrag?.pointer === event.pointerId && event.target instanceof HTMLElement) {
      event.target.scrollTo(sceneDrag.left + sceneDrag.x - event.clientX, sceneDrag.top + sceneDrag.y - event.clientY);
      return;
    }
    pointerPosition = event.target instanceof Element && event.target.closest(".gantt-bar, .gantt-marker")
      ? { x: event.clientX, y: event.clientY } : null;
  });
  content.addEventListener("pointerout", event => {
    if (!(event.target instanceof Element)) return;
    const bar = event.target.closest(".gantt-bar, .gantt-marker");
    if (bar && !(event.relatedTarget instanceof Element && bar.contains(event.relatedTarget))) {
      pointerPosition = null;
      hideGanttTooltip();
    }
  });
  content.addEventListener("focusin", event => {
    if (event.target instanceof Element && event.target.matches(".gantt-bar, .gantt-marker")) showGanttTooltip(event.target);
  });
  content.addEventListener("focusout", event => {
    if (event.target instanceof Element && event.target.matches(".gantt-bar, .gantt-marker")) hideGanttTooltip();
  });
  content.addEventListener("scroll", hideGanttTooltip, true);
  content.addEventListener("keydown", event => {
    const viewport = activeMapScroll();
    if (!viewport || event.target !== viewport || mapView(viewport) !== mapViews["monitor-scene"]
      || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
    const dx = event.key === "ArrowLeft" ? -80 : event.key === "ArrowRight" ? 80 : 0;
    const dy = event.key === "ArrowUp" ? -80 : event.key === "ArrowDown" ? 80 : 0;
    if (dx || dy) {
      event.preventDefault();
      viewport.scrollBy(dx, dy);
    }
  });
  content.addEventListener("change", event => {
    const select = event.target;
    if (!(select instanceof HTMLSelectElement) || !select.hasAttribute("data-timeline-activity")) return;
    if (select.value === "all" || select.value === "actions" || select.value === "movement"
      || select.value === "route" || select.value === "work" || select.value === "planning" || select.value === "dialogue"
      || select.value === "meals" || select.value === "diary" || select.value === "waiting"
      || select.value === "sleeping" || select.value === "free" || select.value === "rejected"
      || select.value === "events" || select.value === "milestones") {
      activity = select.value;
      schedule();
    }
  });
  content.addEventListener("click", event => {
    if (!(event.target instanceof Element)) return;
    if (event.target.closest("[data-timeline-export]") && state?.world?.village?.events) {
      const plans = state.world.village.events.filter(item => item.action === "daily_plan"
        || item.action === "customer_adjustment_required");
      const url = URL.createObjectURL(new Blob([JSON.stringify(plans, null, 2)], { type: "application/json" }));
      const link = document.createElement("a");
      link.href = url;
      link.download = `millhaven-planning-${state.run?.id ?? "episode"}.json`;
      link.click();
      URL.revokeObjectURL(url);
      return;
    }
    if (event.target.closest("[data-timeline-recent], [data-timeline-reset]")) {
      if (event.target.closest("[data-timeline-reset]")) {
        recent = false;
        activity = "all";
        zoom = 1;
      } else {
        recent = !recent;
      }
      resetTimelineScroll = true;
      schedule();
      return;
    }
    const resident = event.target.closest<HTMLButtonElement>("[data-resident-select]");
    if (resident) {
      selectedActorId = resident.dataset.residentSelect === selectedActorId ? undefined : resident.dataset.residentSelect;
      schedule();
      return;
    }
    const actor = event.target.closest<HTMLButtonElement>("[data-map-actor], [data-scene-actor]");
    if (actor) {
      const id = actor.dataset.mapActor ?? actor.dataset.sceneActor;
      selectedActorId = id === selectedActorId ? undefined : id;
      schedule();
      return;
    }
    if (event.target.closest("[data-scene-center]")) {
      centerScene = true;
      restoreScroll();
      return;
    }
    const mapButton = event.target.closest<HTMLButtonElement>("[data-map-zoom]");
    if (mapButton && !mapButton.disabled) {
      const view = mapView(mapButton);
      const viewport = activeMapScroll();
      const next = mapButton.dataset.mapZoom === "fit" ? 1
        : mapButton.dataset.mapZoom === "in" ? Math.min(3, view.zoom + 0.5) : Math.max(1, view.zoom - 0.5);
      if (viewport && next !== view.zoom && view === mapViews["monitor-scene"]) {
        const canvas = viewport.querySelector(".town-map-canvas")!.getBoundingClientRect();
        const bounds = viewport.getBoundingClientRect();
        sceneZoomCenter = { x: (bounds.left + viewport.clientWidth / 2 - canvas.left) / canvas.width,
          y: (bounds.top + viewport.clientHeight / 2 - canvas.top) / canvas.height };
      }
      view.zoom = next;
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
  const record = (event: RunEvent, live: boolean) => {
    if (event.run_id !== state?.run?.id || seen.has(event.id)) return;
    dialogue.record(event, live, Date.now());
    const projected = timelineEventProjection(event);
    seen.add(event.id);
    if (projected) events.set(event.id, projected);
  };
  const initialize = async () => {
    awaitingSnapshot = true;
    const controller = new AbortController();
    const { signal } = controller;
    let stream: EventSource | undefined;
    window.addEventListener("pagehide", () => {
      controller.abort();
      stream?.close();
      clearTimeout(bubbleTimer);
      if (frame !== null) cancelAnimationFrame(frame);
      frame = null;
    }, { once: true });
    let streamRun = "";
    let streamAfter = 0;
    try {
      const response = await fetch("/api/state", { signal });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      render(await response.json());
      streamRun = state?.run?.id ?? "";
      if (streamRun) {
        const historyThrough = state!.event_count;
        streamAfter = historyThrough;
        stream = new EventSource(`/api/stream?compact=true&run_id=${encodeURIComponent(streamRun)}&after=${streamAfter}`);
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
            record(event, event.sequence > liveAfterSequence);
            schedule();
          } catch (failure) { report(failure); }
        });
        await replayWorldHistory(streamRun, historyThrough, seen.size, event => {
          record(event, false);
          streamAfter = event.sequence;
        }, fetch, signal);
      }
      historyReady = true;
      schedule();
    } catch (failure) {
      if (signal.aborted) return;
      report(failure);
    }
    if (signal.aborted) return;
    if (!stream) {
      stream = new EventSource(`/api/stream?compact=true&run_id=${encodeURIComponent(streamRun)}&after=${streamAfter}`);
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
          record(event, event.sequence > liveAfterSequence);
          schedule();
        } catch (failure) { report(failure); }
      });
    }
  };
  window.addEventListener("pageshow", event => {
    if (event.persisted) void initialize();
  });
  void initialize();
}
