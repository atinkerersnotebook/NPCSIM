import { z } from "zod";
import { observationSchema, villageObservationSchema, villageResearchSchema } from "./contracts.ts";
import type { RunEvent, State } from "./contracts.ts";
import { clockTime } from "./bakery-view.ts";
import { latestVillageMinute, villageClockText, villageClockTime } from "./view-mode.ts";
import { actorDetailsMarkup } from "./actor-details.ts";
import { tradeNote } from "./trade-notes.ts";

const escape = (text: string) => text.replaceAll("&", "&amp;").replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#39;");
const personalSchema = villageObservationSchema.shape.personal.unwrap().pick({
  life: true, available_at: true
});
const researchProjectSchema = villageResearchSchema.pick({
  id: true, title: true, description: true, status: true, blocker: true,
  worked_minutes: true, required_minutes: true, attempts: true, successes: true,
  required_successes: true, inputs: true, equipment: true, duration: true, product: true
});
const timeSchema = z.object({
  village: z.object({ minute: z.number().nonnegative(), personal: personalSchema.optional(),
    research: z.object({ projects: z.array(researchProjectSchema) }).optional() }).nullish(),
  clock: z.object({ seconds: z.number().nonnegative() }).nullish(),
  bbq: z.object({ elapsed_seconds: z.number().nonnegative() }).nullish()
});
const observedSchema = timeSchema.extend({
  self: observationSchema.shape.self.pick({ actor_id: true, location_id: true })
});
const requestSchema = z.object({
  request_id: z.string(), action_id: z.string(), arguments: z.record(z.string(), z.unknown())
});
const resultSchema = z.object({
  request_id: z.string(), outcome: z.string(), code: z.string().optional(), explanation: z.string(),
  observation: observedSchema
});
const objectSchema = z.record(z.string(), z.unknown());
const timeOf = (value: z.infer<typeof timeSchema>) => value.village?.minute
  ?? (value.clock ? value.clock.seconds / 60 : value.bbq ? value.bbq.elapsed_seconds / 60 : undefined);

export function timelineEventProjection(event: RunEvent): RunEvent | null {
  const payload = event.kind === "observation" ? observedSchema.parse(event.payload)
    : event.kind === "action_request" ? requestSchema.parse(event.payload)
    : event.kind === "action_result" ? resultSchema.parse(event.payload)
    : event.kind === "control" ? objectSchema.parse(event.payload) : null;
  return payload === null ? null : { ...event, payload };
}

export interface GanttAgent {
  id: string;
  name: string;
  locationId: string;
  current: boolean;
  visitor?: boolean;
  activity?: string;
}
export interface GanttScale {
  zoom?: number;
  viewportWidth?: number;
}
interface Position {
  at: number;
  order: number;
  location: string;
  sleepUntil?: number;
}
export interface GanttSpan {
  start: number;
  end: number;
  location: string;
  label: string;
  detail: string;
  status?: string;
  kind: "action" | "travel" | "wait" | "sleep" | "route" | "free" | "rejected" | "event";
}
interface Milestone { at: number; label: string; detail: string }

const colours: Record<string, string> = {
  cottages: "#b49a86", "village-square": "#c8ac75", forge: "#e47750", farm: "#99c568",
  stable: "#b28a58", watermill: "#9573c4", bakery: "#ed88b1", tavern: "#e8bb48",
  workshop: "#4cb7a6", "well-site": "#6fa9d1", "stone-bridge": "#8fa8b4",
  woodlot: "#5ea66b", riverbank: "#65c7e9", "mill-shed": "#b2a292"
};
const palette = ["#9b80c5", "#e4ae65", "#75b5a2", "#db8b95", "#8db0d6", "#b4bd70"];
export function locationColour(id: string): string {
  return colours[id] ?? palette[[...id].reduce((sum, char) => sum + char.charCodeAt(0), 0) % palette.length];
}

function actionLabel(action: string, args: Record<string, unknown>, state: State, actor: string): string {
  const labels: Record<string, string> = {
    go: "Walk", look: "Look", read: "Read", take: "Pick up", drop: "Put down", say: "Talk",
    village_work: "Work", village_wait: "Wait", village_sleep: "Sleep",
    village_fit_wagon: "Fit wagon parts", village_start_route: "Start wagon route",
    village_stop_route: "Stop wagon route", village_project: "Project work",
    village_meeting: "Arrange meeting", village_load_goods: "Load goods", village_unload_goods: "Unload goods"
  };
  const label = labels[action] ?? action.replace(/^village_/, "").replaceAll("_", " ");
  if (action === "village_work") {
    const jobs: Record<string, string> = {
      blacksmith: "Forge work", "stable keeper": "Feed horse", miller: "Mill flour",
      innkeeper: "Cook meals", carpenter: "Carpentry", baker: "Bake bread",
      farmer: "Harvest", forester: "Forestry", guard: "Patrol", healer: "Herbal work", fisher: "Fish"
    };
    return jobs[state.world?.village?.roster[actor]?.occupation ?? ""] ?? label;
  }

  const target = typeof args.target === "string" ? state.world?.entities[args.target]?.name : undefined;
  return target && ["go", "look", "read", "take", "drop"].includes(action) ? `${label}: ${target}` : label;
}

function researchDetail(project: z.infer<typeof researchProjectSchema>): string {
  const inputs = Object.entries(project.inputs).map(([item, quantity]) => `${quantity} ${item}`).join(", ") || "none";
  return `Study: ${project.worked_minutes}/${project.required_minutes} min; trials: ${project.successes}/${project.attempts} successful (${project.required_successes} required). `
    + `Status: ${project.status}${project.blocker ? ` - ${project.blocker}` : ""}. ${project.description} `
    + `Proposed recipe: ${inputs} -> 1 ${project.title} / ${project.duration} min; equipment: ${project.equipment.join(", ") || "none"}.`;
}

function activityIcon(item: GanttSpan): string {
  if (item.kind === "travel") return "🚶";
  if (item.kind === "sleep") return "💤";
  if (item.kind === "wait") return "⏳";
  if (item.kind === "route" || /wagon|delivery|load goods|unload goods/i.test(item.label)) return "🚚";
  if (/cook|bake|meal|food/i.test(item.label)) return "🍞";
  if (/forge|carpentry|repair|work|project|fit/i.test(item.label)) return "🔨";
  if (/harvest|farm|forestry|timber/i.test(item.label)) return "🌿";
  if (/talk|meeting|trade|visit/i.test(item.label)) return "💬";
  if (/look|read|inspect/i.test(item.label)) return "🔎";
  return "●";
}

export function buildWorldTimeline(state: State, events: readonly RunEvent[], agents: readonly GanttAgent[]) {
  const snapshot = timeSchema.parse(state.world ?? state.observation ?? {});
  const villageMinute = latestVillageMinute(state.world, state.observation);
  const clockNow = villageMinute ?? timeOf(snapshot);
  const turns = clockNow === undefined;
  if (turns && !state.run) return null;
  let now = clockNow ?? 0;
  const village = villageMinute !== undefined;
  const visitors = (state.world?.village?.dashboard?.visitors ?? []).filter(visitor =>
    visitor.arrival <= now && visitor.status !== "scheduled");
  const existing = new Set(agents.map(agent => agent.id));
  const cast = [...agents, ...visitors.filter(visitor => !existing.has(visitor.id)).map(visitor => ({
    id: visitor.id, name: visitor.name, locationId: visitor.location,
    current: visitor.departure === null, visitor: true
  }))];
  const rows = cast.map(agent => ({
    ...agent, occupation: agent.visitor ? "Visitor" : state.world?.village?.roster[agent.id]?.occupation ?? "",
    positions: [] as Position[], locations: [] as { start: number; end: number; location: string }[],
    spans: [] as GanttSpan[]
  }));
  const byId = new Map(rows.map(row => [row.id, row]));
  const requests = new Map<string, { event: RunEvent; request: z.infer<typeof requestSchema> }>();
  const walkingPlans = new Map<string, { destination: string; purpose: string }>();
  const milestones: Milestone[] = [];
  const routeChanges: { at: number; actor: string; active: boolean }[] = [];
  const recordObservation = (raw: z.infer<typeof observedSchema>, order: number) => {
    const at = turns ? now : timeOf(raw);
    const row = byId.get(raw.self.actor_id);
    if (!row || at === undefined || at > now) return;
    const life = raw.village?.personal?.life;
    row.positions.push({ at, order, location: raw.self.location_id,
      ...(life?.sleeping ? { sleepUntil: life.sleep_until ?? now } : {}) });
  };
  const source = events.filter(event => event.run_id === state.run?.id).slice()
    .sort((a, b) => a.sequence - b.sequence);
  const completedRequests = new Set<string>();
  for (const event of source) {
    if (event.kind === "action_request") {
      const request = requestSchema.parse(event.payload);
      requests.set(request.request_id, { event, request });
    } else if (event.kind === "observation") {
      recordObservation(observedSchema.parse(event.payload), event.sequence);
    } else if (event.kind === "action_result") {
      const result = resultSchema.parse(event.payload);
      const linked = requests.get(result.request_id);
      const row = byId.get(event.actor_id);
      if (turns && (!linked || !row || completedRequests.has(result.request_id))) continue;
      if (turns) {
        completedRequests.add(result.request_id);
        now += 1;
      }
      const at = turns ? now : timeOf(result.observation);
      recordObservation(result.observation, event.sequence);
      if (!linked || !row || at === undefined || at > now) continue;
      const { action_id: action, arguments: args } = linked.request;
      const projects = result.observation.village?.research?.projects ?? [];
      const research = action === "village_research_propose" && result.outcome === "APPLIED"
        ? projects.find(project => project.title.trim().toLowerCase() ===
          (typeof args.title === "string" ? args.title.trim().toLowerCase() : undefined))
        : action.startsWith("village_research_") || action === "village_research"
          ? projects.find(project => project.id === args.project) : undefined;
      if (action === "village_plan_walk" && result.outcome === "APPLIED"
        && typeof args.destination === "string" && typeof args.purpose === "string") {
        walkingPlans.set(event.actor_id, { destination: args.destination, purpose: args.purpose });
      }
      const walkingPlan = action === "go" && result.outcome === "APPLIED"
        ? walkingPlans.get(event.actor_id) : undefined;
      const planForTile = action === "village_plan_walk" && result.outcome === "APPLIED"
        ? walkingPlans.get(event.actor_id) : walkingPlan;
      const prior = row.positions.filter(point => point.order < event.sequence).at(-1);
      const start = turns ? at - 1
        : village ? linked.event.village_minute ?? prior?.at ?? at : prior?.at ?? at;
      if (start > now) continue;
      const accepted = result.outcome === "APPLIED" || result.code === "WAITING";
      const life = result.observation.village?.personal?.life;
      const reserved = result.observation.village?.personal?.available_at;
      let end = turns ? result.outcome === "REJECTED" ? start : at
        : accepted ? Math.max(at, reserved ?? at, life?.work?.finishes_at ?? at) : start;
      if (accepted && !turns && !village && action === "wait" && typeof args.seconds === "number") {
        end = Math.max(end, start + args.seconds / 60);
      }
      if (!turns && (event.free_action || (village && ["look", "read"].includes(action)))) end = start;
      const kind = result.outcome === "REJECTED" ? "rejected" : !accepted ? "event"
        : end === start ? "free" : action === "go" ? "travel"
        : /wait/.test(action) ? "wait" : life?.sleeping ? "sleep" : "action";
      if (kind !== "sleep") row.spans.push({
        start, end: Math.max(start, Math.min(now, end)), location: result.observation.self.location_id,
        label: research ? `Research: ${research.title}`
          : walkingPlan ? `Walk to ${state.world?.entities[walkingPlan.destination]?.name ?? walkingPlan.destination}`
          : actionLabel(action, args, state, event.actor_id),
        detail: research ? `${researchDetail(research)} ${villageClockText(result.explanation)}`
          : planForTile
          ? `Purpose: ${planForTile.purpose}${/[.!?]$/.test(planForTile.purpose) ? "" : "."} ${villageClockText(result.explanation)}`
          : villageClockText(result.explanation), status: result.outcome, kind
      });
      if (walkingPlan && result.observation.self.location_id === walkingPlan.destination) {
        walkingPlans.delete(event.actor_id);
      }
      if (accepted && ["village_start_route", "village_stop_route"].includes(action)) {
        routeChanges.push({ at: start, actor: event.actor_id, active: action === "village_start_route" });
        if (action === "village_start_route") milestones.push({ at: start, label: "Wagon route started", detail: result.explanation });
      }
    } else if (event.kind === "control" && event.village_minute !== undefined) {
      const value = objectSchema.parse(event.payload);
      if (value.command === "pause" && value.source === "llm_unavailable") {
        milestones.push({ at: event.village_minute, label: "LLM unavailable / paused", detail: String(value.reason) });
      }
    }
  }
  for (const event of state.world?.village?.events ?? []) {
    if (event.minute > now) continue;
    const row = byId.get(event.actor);
    if (row?.visitor && ["visitor_route_planned", "visitor_trade_enquiry"].includes(event.action)) {
      row.spans.push({ start: event.minute, end: event.minute, location: "", kind: "event",
        label: event.action === "visitor_route_planned" ? "Planned walk" : "Trade enquiry",
        detail: event.result });
    }
    if (row && ["wagon_drive", "timber_walk"].includes(event.action)
      && state.world?.entities[event.target]?.kind === "location") {
      row.positions.push({ at: event.minute, order: -1, location: event.target });
      row.spans.push({ start: event.minute, end: event.minute, location: event.target, kind: "event",
        label: event.action === "wagon_drive" ? "Wagon travel" : "Timber delivery", detail: event.result });
    }
    if (event.action === "wagon_paused") routeChanges.push({ at: event.minute, actor: event.actor, active: false });
  }
  for (const notice of state.world?.village?.notices ?? []) {
    const labels: Record<string, string> = {
      breakdown: "Mill breakdown", wagon_ready: "Wagon repaired",
      project_completed: `${notice.target.replaceAll("_", " ")} complete`,
      deadline_completed: `${notice.target.replaceAll("_", " ")} complete`,
      deadline_overdue: `${notice.target.replaceAll("_", " ")} overdue`
    };
    const label = notice.action === "tool_collected" && /shovel/.test(notice.target)
      ? "Shovel collected" : labels[notice.action];
    if (label && notice.minute <= now) milestones.push({ at: notice.minute, label, detail: notice.result });
  }
  for (const trade of state.world?.village?.dashboard?.trades ?? []) {
    if (trade.minute > now) continue;
    const detail = tradeNote(state, trade);
    for (const id of new Set([trade.resident, trade.visitor])) {
      const row = byId.get(id);
      if (row) row.spans.push({ start: trade.minute, end: trade.minute,
        location: trade.location, kind: "event", label: "Completed trade", detail, status: "settled" });
    }
  }
  for (const row of rows) {
    const visitor = visitors.find(person => person.id === row.id);
    const activityHistory = [
      ...(state.world?.village?.dashboard?.activities ?? []).filter(item => item.actor === row.id),
      ...(visitor?.activities ?? [])
    ];
    if (visitor) row.positions.push({ at: visitor.arrival, order: -1,
      location: visitor.activities[0]?.location ?? visitor.location });
    const orderedActivities = activityHistory.slice().sort((a, b) => a.start - b.start);
    for (const [index, activity] of orderedActivities.entries()) {
      if (activity.start > now) continue;
      const end = Math.min(now, activity.end, orderedActivities[index + 1]?.start ?? now,
        visitor?.departure ?? now);
      if (end < activity.start) continue;
      row.spans = row.spans.filter(span => !(span.kind === "action" && span.start === activity.start));
      row.spans.push({
        start: activity.start, end, location: activity.location, label: activity.label,
        detail: `${activity.label} / ${activity.status}`, status: activity.status, kind: "action"
      });
      row.positions.push({ at: activity.start, order: activity.start, location: activity.location });
    }
    if (row.current && row.locationId && !(visitor?.activities.some(activity =>
      activity.start <= now && now < activity.end && /travel/i.test(activity.label)))) row.positions.push({
      at: now, order: Number.MAX_SAFE_INTEGER, location: row.locationId
    });
    row.positions.sort((a, b) => a.at - b.at || a.order - b.order);
    const points = row.positions.filter((point, index, list) => list[index + 1]?.at !== point.at);
    points.forEach((point, index) => {
      const end = Math.min(points[index + 1]?.at ?? now, visitor?.departure ?? now);
      if (end > point.at) {
        const previous = row.locations.at(-1);
        if (previous?.location === point.location && previous.end === point.at) previous.end = end;
        else row.locations.push({ start: point.at, end, location: point.location });
        if (point.sleepUntil !== undefined && point.sleepUntil > point.at) {
          row.spans.push({ start: point.at, end: Math.min(end, point.sleepUntil),
            location: point.location, kind: "sleep", label: "Sleeping", detail: "Recorded rest at home." });
        }
      }
    });
    let routeStart: number | undefined;
    for (const change of routeChanges.filter(change => change.actor === row.id).sort((a, b) => a.at - b.at)) {
      if (change.active) routeStart ??= change.at;
      else if (routeStart !== undefined) {
        row.spans.push({ start: routeStart, end: change.at, location: "", kind: "route",
          label: "Wagon route", detail: "Automatic wagon route was active." });
        routeStart = undefined;
      }
    }
    if (routeStart !== undefined) row.spans.push({
      start: routeStart, end: now, location: "", kind: "route",
      label: "Wagon route", detail: "Automatic wagon route is active."
    });
    const recordedStarts = row.spans.filter(span => !["route", "sleep", "wait"].includes(span.kind))
      .map(span => span.start).sort((a, b) => a - b);
    const latestWaitAt = new Map<number, GanttSpan>();
    for (const wait of row.spans.filter(span => span.kind === "wait")) latestWaitAt.set(wait.start, wait);
    row.spans = row.spans.filter(span => span.kind !== "wait" || latestWaitAt.get(span.start) === span);
    for (const wait of latestWaitAt.values()) {
      const next = recordedStarts.find(start => start >= wait.start && start < wait.end);
      if (next !== undefined) wait.end = next;
    }
    row.spans = row.spans.filter(span => span.kind !== "wait" || span.end > span.start);
    const waits = row.spans.filter(span => span.kind === "wait").sort((a, b) => a.start - b.start);
    const mergedWaits: GanttSpan[] = [];
    for (const wait of waits) {
      const previous = mergedWaits.at(-1);
      if (!turns && previous && previous.location === wait.location && wait.start <= previous.end) {
        previous.end = Math.max(previous.end, wait.end);
        if (!previous.detail.includes(wait.detail)) previous.detail += `\n${wait.detail}`;
      } else mergedWaits.push(wait);
    }
    const merged: GanttSpan[] = [];
    for (const item of [...row.spans.filter(span => span.kind !== "wait"), ...mergedWaits]
      .sort((a, b) => a.start - b.start)) {
      const previous = merged.at(-1);
      if (!turns && previous && item.kind === "sleep" && previous.kind === "sleep"
        && previous.location === item.location && previous.end === item.start) {
        previous.end = item.end;
        if (previous.detail !== item.detail) previous.detail += `\n${item.detail}`;
      } else merged.push(item);
    }
    row.spans = merged;
  }
  const times = rows.flatMap(row => [...row.positions.map(point => point.at), ...row.spans.map(span => span.start)]);
  const start = Math.min(now, ...times);
  return { start, now, village, turns, rows, milestones: milestones.filter(mark => mark.at >= start && mark.at <= now) };
}

export function worldGanttMarkup(state: State, events: readonly RunEvent[], agents: readonly GanttAgent[],
  scale: GanttScale = {}): string {
  const chart = buildWorldTimeline(state, events, agents);
  if (!chart || !chart.rows.length) return "";
  const format = (minute: number) => chart.turns ? `Turn ${minute}`
    : chart.village ? villageClockTime(minute) : clockTime(minute * 60);
  const span = Math.max(chart.turns ? 1 : 10, chart.now - chart.start);
  const zoom = Math.max(1, Math.min(8, scale.zoom ?? 1));
  const width = Math.round(Math.max(120, scale.viewportWidth ?? 900) * zoom);
  const spacing = Math.max(chart.turns ? 1 : chart.village ? 5 : 0.5, span / Math.max(1, width / 65));
  const tick = [0.5, 1, 2, 5, 10, 15, 30, 60, 120, 360, 720, 1440, 10080].find(value => value >= spacing)
    ?? Math.ceil(spacing / 1440) * 1440;
  const start = Math.floor(chart.start / tick) * tick;
  const end = Math.max(start + tick * 2, Math.ceil(chart.now / tick) * tick + tick);
  const top = 58, rowHeight = 100, height = top + rowHeight * chart.rows.length + 30;
  const x = (minute: number) => (minute - start) / (end - start) * (width - 20) + 10;
  const placeName = (id: string) => state.world?.entities[id]?.name ?? id.replaceAll("-", " ");
  const tooltip = (text: string) => `<title>${escape(text)}</title>`;
  const shorten = (text: string, characters: number) => text.length > characters
    ? `${text.slice(0, Math.max(0, characters - 3)).trimEnd()}...` : text;
  const grid: string[] = [];
  for (let minute = start; minute <= end; minute += tick) {
    grid.push(`<line x1="${x(minute)}" x2="${x(minute)}" y1="${top - 12}" y2="${height - 20}" class="gantt-gridline"/>
      <text x="${x(minute)}" y="${height - 5}" text-anchor="middle" class="gantt-tick">${escape(chart.village ? villageClockTime(minute % 1440) : format(minute))}</text>`);
  }
  const legend = new Map<string, string>();
  const rows = chart.rows.map((row, index) => {
    const y = top + index * rowHeight;
    const locations = row.locations.map((band, bandIndex) => {
      legend.set(band.location, placeName(band.location));
      const w = x(band.end) - x(band.start);
      const inset = Math.min(1.5, w / 8);
      const clip = `gantt-location-${index}-${bandIndex}`;
      return `<g class="gantt-location">${tooltip(`${row.name} / ${placeName(band.location)} / ${format(band.start)} - ${format(band.end)}`)}
        <clipPath id="${clip}"><rect x="${x(band.start) + 6}" y="${y + 4}" width="${Math.max(0, w - 12)}" height="82" rx="8"/></clipPath>
        <rect x="${x(band.start) + inset}" y="${y + 4}" width="${w - inset * 2}" height="82" rx="8" fill="${locationColour(band.location)}" opacity=".8"/>
        ${w > 80 ? `<text x="${x(band.start) + 8}" y="${y + 16}" class="gantt-place" clip-path="url(#${clip})">${escape(placeName(band.location))}</text>` : ""}</g>`;
    }).join("");
    const durationSpans = row.spans.filter(item => item.end > item.start)
      .sort((a, b) => Number(a.kind !== "route" && a.kind !== "sleep") - Number(b.kind !== "route" && b.kind !== "sleep"));
    const bars = durationSpans.map((item, barIndex) => {
      const w = x(item.end) - x(item.start);
      const inset = Math.min(1.5, w / 8);
      const tileWidth = w - inset * 2;
      const id = `gantt-clip-${index}-${barIndex}`;
      const location = item.location ? placeName(item.location) : "";
      const duration = chart.turns ? `${item.end - item.start} action attempt`
        : chart.village ? `${Math.round((item.end - item.start) * 10) / 10} min`
        : `${Math.round((item.end - item.start) * 600) / 10} sec`;
      const range = chart.turns ? format(item.end) : `${format(item.start)} - ${format(item.end)}`;
      const timing = `${duration} / ${range}`;
      const detail = `${row.name} / ${range} / ${duration}${location ? ` / ${location}` : ""} / ${item.label}. ${item.detail}`;
      const label = item.kind === "wait" ? "Waiting" : item.label;
      const summary = (item.detail || location).replace(/\s+/g, " ").trim();
      const icon = activityIcon(item);
      const status = item.status ?? (item.kind === "sleep" || item.kind === "route" || item.kind === "wait" ? "Recorded" : "");
      const iconWidth = 24;
      const characters = tileWidth >= 48 ? Math.floor((tileWidth - 16 - iconWidth) / 7) : 0;
      const detailCharacters = Math.floor((tileWidth - 16) / 6);
      return `<g tabindex="0" role="img" data-gantt-focus="${escape(`${row.id}:${item.kind}:${item.start}:${item.label}`)}" aria-label="${escape(detail)}" aria-describedby="gantt-tooltip" data-gantt-title="${escape(item.label)}" data-gantt-icon="${icon}" data-gantt-status="${escape(status)}" data-gantt-duration="${escape(duration)}" data-gantt-range="${escape(range)}" data-gantt-place="${escape(location)}" data-gantt-detail="${escape(item.detail)}" data-gantt-agent-name="${escape(row.name)}" class="gantt-bar ${item.kind}">
        <clipPath id="${id}"><rect x="${x(item.start) + inset + 6}" y="${y + 19}" width="${Math.max(0, tileWidth - 12)}" height="64"/></clipPath>
        <rect x="${x(item.start) + inset}" y="${y + 19}" width="${tileWidth}" height="64" rx="7"/>
        ${tileWidth >= 28 ? `<text x="${x(item.start) + inset + 7}" y="${y + 37}" class="gantt-cell-icon" clip-path="url(#${id})">${icon}</text>` : ""}
        ${characters >= 6 ? `<text x="${x(item.start) + w / 2 + (tileWidth >= 48 ? iconWidth / 2 : 0)}" y="${y + 37}" text-anchor="middle" class="gantt-cell-title" clip-path="url(#${id})">${escape(shorten(label, characters))}</text>` : ""}
        ${detailCharacters >= 8 ? `<text x="${x(item.start) + w / 2}" y="${y + 54}" text-anchor="middle" class="gantt-cell-time" clip-path="url(#${id})">${escape(shorten(detailCharacters >= 30 ? timing : duration, detailCharacters))}</text>` : ""}
        ${detailCharacters >= 14 && summary ? `<text x="${x(item.start) + w / 2}" y="${y + 71}" text-anchor="middle" class="gantt-cell-detail" clip-path="url(#${id})">${escape(shorten(summary, detailCharacters))}</text>` : ""}</g>`;
    }).join("");
    const markers = new Map<number, GanttSpan[]>();
    for (const item of row.spans.filter(item => item.end === item.start)) {
      const list = markers.get(item.start) ?? [];
      list.push(item);
      markers.set(item.start, list);
    }
    const dots = [...markers].map(([at, items]) => {
      const detail = `${row.name} / ${format(chart.turns ? at + 1 : at)} / ${items.map(item => `${item.label}: ${item.detail}`).join("; ")}`;
      return `<g tabindex="0" role="img" aria-label="${escape(detail)}" class="gantt-marker ${items.some(item => item.kind === "rejected") ? "rejected" : ""}">
        ${tooltip(detail)}<path d="M ${x(at)} ${y + 88} l 4 4 l -4 4 l -4 -4 Z"/>
        ${items.length > 1 ? `<text x="${x(at) + 6}" y="${y + 95}">${items.length}</text>` : ""}</g>`;
    }).join("");
    return `<g data-gantt-agent="${escape(row.id)}"><line x1="0" x2="${width}" y1="${y + rowHeight}" y2="${y + rowHeight}" class="gantt-gridline"/>${locations}${bars}${dots}</g>`;
  }).join("");
  const milestones = new Map<number, Milestone[]>();
  for (const mark of chart.milestones) {
    const list = milestones.get(mark.at) ?? [];
    list.push(mark);
    milestones.set(mark.at, list);
  }
  const lines = [...milestones].map(([at, marks], index) => `<g class="gantt-milestone" tabindex="0" role="img"
    aria-label="${escape(marks.map(mark => `${format(at)} ${mark.detail}`).join("; "))}">
    ${tooltip(marks.map(mark => `${format(at)} / ${mark.label}: ${mark.detail}`).join("\n"))}
    <line x1="${x(at)}" x2="${x(at)}" y1="8" y2="${height - 20}"/>
    <text x="${x(at)}" y="${13 + index % 3 * 13}" text-anchor="middle">${escape(marks.length > 1 ? `${marks.length} events` : marks[0].label)}</text></g>`).join("");
  return `<section class="world-gantt" aria-label="Recorded agent activity timeline">
    <div class="gantt-heading"><div><span class="eyebrow">RECORDED ACTIVITY</span><h2>World timeline</h2></div>
      <div class="gantt-tools"><p>${escape(format(chart.start))} - ${escape(format(chart.now))} / ${escape(state.run?.status ?? "")}</p>
        <div class="gantt-zoom" role="group" aria-label="Timeline timescale">
          <button type="button" class="button subtle" data-timeline-zoom="out" aria-label="Zoom out timescale"${zoom <= 1 ? " disabled" : ""}>-</button>
          <output aria-label="Timeline zoom">${Math.round(zoom * 100)}%</output>
          <button type="button" class="button subtle" data-timeline-zoom="in" aria-label="Zoom in timescale"${zoom >= 8 ? " disabled" : ""}>+</button>
          <button type="button" class="button subtle" data-timeline-zoom="fit">Fit</button>
        </div></div></div>
    <p class="gantt-help">Location colours show recorded positions. ${chart.turns
      ? "Turns count recorded action attempts across all agents, including inspections and rejected actions, not elapsed time. Turn 0 is the initial observation. Boxes show recorded actions; red diamonds mark rejected attempts."
      : "Boxes show action time or reserved availability, not model thinking time. Diamonds mark instant or free actions; red diamonds are rejected actions."}
      Hover or focus for details. Scroll sideways to explore.</p>
    <div id="gantt-tooltip" class="gantt-tooltip" role="tooltip" hidden></div>
    <div class="gantt-layout">
      <div class="gantt-names" style="padding-top:${top}px">${chart.rows.map(row =>
        `<div class="gantt-name-row" style="height:${rowHeight}px"><span class="gantt-name-anchor" data-gantt-name="${escape(row.id)}" tabindex="0" aria-label="${escape(`${row.name}, actor details`)}" aria-describedby="gantt-actor-${escape(row.id)}"><strong>${escape(row.name)}</strong><small>${escape(row.occupation)}</small><span id="gantt-actor-${escape(row.id)}" class="actor-details-popover" role="tooltip"><strong>${escape(row.name)}${row.visitor ? " / Visitor" : ""}</strong>${actorDetailsMarkup(state, { ...row, activity: agents.find(agent => agent.id === row.id)?.activity })}</span></span></div>`).join("")}</div>
      <div class="gantt-scroll" tabindex="0" aria-label="Scrollable activity timeline">
        <svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" role="group" aria-label="Activity from ${escape(format(chart.start))} to ${escape(format(chart.now))}">
          <defs>
            <linearGradient id="gantt-tile" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stop-color="#f2faf8"/><stop offset="1" stop-color="#cededb"/>
            </linearGradient>
            <pattern id="gantt-route" width="7" height="7" patternUnits="userSpaceOnUse"><path d="M0 7L7 0" stroke="#87bee2" stroke-width="1"/></pattern>
          </defs>
          ${grid.join("")}${rows}${lines}
          <rect x="${x(chart.now)}" y="${top - 10}" width="${width - x(chart.now)}" height="${height - top - 10}" class="gantt-future"/>
          <line x1="${x(chart.now)}" x2="${x(chart.now)}" y1="4" y2="${height - 20}" class="gantt-now"/>
          <text x="${Math.max(100, x(chart.now) - 5)}" y="48" text-anchor="end" class="gantt-now-label">${escape(state.run?.status === "running" ? "Now" : state.run?.status === "paused" ? "Paused" : state.run?.status === "ready" ? "Ready" : "Stopped")} ${escape(format(chart.now))}</text>
        </svg>
      </div>
    </div>
    <div class="gantt-legend">${[...legend].map(([id, name]) =>
      `<span><i style="background:${locationColour(id)}"></i>${escape(name)}</span>`).join("")}
      ${chart.turns ? "" : '<span><i class="route"></i>Wagon route</span><span><i class="sleep"></i>Sleeping</span>'}<span><i class="travel"></i>Walking</span>
    </div>
    ${events.length ? "" : '<p class="gantt-help">Waiting for recorded history. Unknown earlier positions are left blank.</p>'}
  </section>`;
}
