import { z } from "zod";
import { dungeonMasterRulingSchema, observationSchema, villageObservationSchema, villageResearchSchema } from "./contracts.ts";
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
    research: z.object({ projects: z.array(researchProjectSchema) }).optional(),
    fallback: z.object({ decision: z.object({ selected_task_id: z.string().nullable(),
      reason: z.string() }).nullable().optional() }).optional() }).nullish(),
  clock: z.object({ seconds: z.number().nonnegative() }).nullish(),
  bbq: z.object({ elapsed_seconds: z.number().nonnegative() }).nullish()
});
const observedSchema = timeSchema.extend({
  self: observationSchema.shape.self.pick({ actor_id: true, location_id: true })
});
const requestSchema = z.object({
  request_id: z.string(), action_id: z.string(), arguments: z.record(z.string(), z.unknown())
});
const decisionSchema = z.object({
  action_id: z.string(), justification: z.string(), arguments: z.record(z.string(), z.unknown()).optional(),
  expected_outcome: z.string().optional(), subgoal: z.string().optional(), plan: z.array(z.string()).optional(),
  model: z.string().optional(), compression: z.object({
    requested_mode: z.string().optional(), effective_mode: z.string().optional(),
    options: z.object({
      deduplicate: z.boolean().optional(), relevant_memory: z.boolean().optional(),
      aggregate_events: z.boolean().optional(), perception_delta: z.boolean().optional()
    }).optional()
  }).passthrough().optional()
});
const diagnosticCompressionSchema = decisionSchema.shape.compression.unwrap();
const supervisorReviewSchema = z.object({
  verdict: z.enum(["continue", "defeat"]),
  findings: z.array(z.object({ reason: z.string(), evidence_ids: z.array(z.string()).optional() })).min(1),
  model: z.string().nullable().optional(), compression: diagnosticCompressionSchema.optional()
});
const actionContextSchema = z.object({
  request_id: z.string(), description: z.string(), model: z.string().nullable().optional(),
  compression: diagnosticCompressionSchema.optional()
});
const adjudicationContextSchema = z.object({
  request_id: z.string(), ruling_event_id: z.string(), description: z.string(),
  model: z.string().nullable().optional(), compression: diagnosticCompressionSchema.optional()
});
const worldStateContextSchema = actionContextSchema.omit({ request_id: true }).extend({
  source_observation_event_id: z.string().optional()
});
const resultSchema = z.object({
  request_id: z.string(), outcome: z.string(), code: z.string().optional(), explanation: z.string(),
  observation: observedSchema
});
const objectSchema = z.record(z.string(), z.unknown());
const dungeonAppealSchema = z.object({
  id: z.string(), actor: z.string(), request: z.string(), category: z.string(),
  status: z.string(), response: z.string().nullable(), npc_acknowledgement: z.string().optional(),
  created_at: z.number().nonnegative(),
  expires_at: z.number().nonnegative(), world_changes: z.array(z.object({
    kind: z.string(), location: z.string(), product: z.string(), quantity: z.number().nonnegative(),
    inventory_location_note: z.string().nullable().optional()
  })).optional()
});
const timeOf = (value: z.infer<typeof timeSchema>) => value.village?.minute
  ?? (value.clock ? value.clock.seconds / 60 : value.bbq ? value.bbq.elapsed_seconds / 60 : undefined);

type DailyPlanDetails = { trigger?: string; assumptions?: string[]; calculations?: string[];
  actions?: string[]; needs_customer_adjustment?: boolean; planning_days?: number;
  component_lines?: { day: number; output: string; quantity: number; for: string[]; order_ids: string[];
    components: { product: string; required: number; on_hand: number; confirmed_incoming: number;
      shortfall: number; source: string; supplier: string | null; confirmation: string | null }[] }[] };

function dailyPlanDetail(event: { result: string; details?: unknown } | undefined): string {
  if (!event) return "";
  const details = event.details as DailyPlanDetails | undefined;
  const lines = details?.component_lines ?? [];
  const jobs = lines.map(line => {
    const stocked = line.components.filter(item => item.shortfall === 0)
      .map(item => `${item.required} ${item.product}${item.supplier ? ` from ${item.supplier}` : " in stock"}`);
    const missing = line.components.filter(item => item.shortfall > 0)
      .map(item => `${item.shortfall} ${item.product}${item.supplier ? ` from ${item.supplier}` : " (supplier unconfirmed)"}`);
    return `Day ${line.day}: ${line.quantity} ${line.output}${line.for.length ? ` for ${line.for.join(", ")}` : ""}.`
      + (stocked.length ? `\n  Ready: ${stocked.join(", ")}.` : "")
      + (missing.length ? `\n  Needs order: ${missing.join(", ")}.` : "");
  });
  return [`Plan: ${details?.planning_days ?? 2} days ahead.`,
    jobs.length ? jobs.join("\n") : event.result,
    details?.needs_customer_adjustment ? "Customer quantities need adjustment before these jobs can be promised." : ""]
    .filter(Boolean).join("\n");
}
export function timelineEventProjection(event: RunEvent): RunEvent | null {
  const payload = event.kind === "observation" ? observationSchema.parse(event.payload)
    : event.kind === "decision" ? decisionSchema.parse(event.payload)
    : event.kind === "action_request" ? requestSchema.parse(event.payload)
    : event.kind === "action_result" ? resultSchema.parse(event.payload)
    : event.kind === "dungeon_master_ruling" ? dungeonMasterRulingSchema.parse(event.payload)
    : event.kind === "dungeon_master_review" ? supervisorReviewSchema.parse(event.payload)
    : event.kind === "action_context" ? actionContextSchema.parse(event.payload)
    : event.kind === "adjudication_context" ? adjudicationContextSchema.parse(event.payload)
    : event.kind === "world_state_context" ? worldStateContextSchema.parse(event.payload)
    : event.kind === "control" ? objectSchema.parse(event.payload)
    : event.kind === "dungeon_master_appeal_resolved" ? dungeonAppealSchema.parse(event.payload) : null;
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
export type GanttActivity = "all" | "actions" | "movement" | "route" | "work" | "planning" | "dialogue"
  | "meals" | "diary" | "waiting" | "sleeping" | "free" | "rejected" | "events" | "milestones";
export interface GanttScale {
  zoom?: number;
  viewportWidth?: number;
  recent?: boolean;
  activity?: GanttActivity;
  linkResidents?: boolean;
  selectedActorId?: string;
  showConnections?: boolean;
  showUntracked?: boolean;
  showSupervisor?: boolean;
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
  reasoning?: string;
  arguments?: string;
  expectedOutcome?: string;
  subgoal?: string;
  plan?: string[];
  model?: string;
  promptFormat?: string;
  debugFlags?: string[];
  contextualDescription?: string;
  originalDetail?: string;
  contextualModel?: string;
  contextualPromptFormat?: string;
  contextualDebugFlags?: string[];
  observation?: string;
  fromLocation?: string;
  toLocation?: string;
  dmVerdict?: string;
  dmComment?: string;
  dmNarrative?: string;
  dmModel?: string;
  dmWordingModel?: string;
  dmFindings?: { reason: string; evidence_ids: string[] }[];
  dmPromptFormat?: string;
  dmDebugFlags?: string[];
  status?: string;
  activity?: Exclude<GanttActivity, "all">;
  kind: "action" | "travel" | "wait" | "sleep" | "route" | "free" | "rejected" | "event";
}
export interface GanttLink {
  at: number;
  from: string;
  to: string;
  label: string;
  detail: string;
  kind: "supply" | "dialogue" | "trade";
  fromStart?: number;
  fromEnd?: number;
  toStart?: number;
  toEnd?: number;
  reference?: string;
}
interface Milestone { at: number; label: string; detail: string }

const colours: Record<string, string> = {
  "warehouse-receiving": "#66b7d2", "warehouse-bulk-a": "#d39a52",
  "warehouse-bulk-b": "#d7bd59", "warehouse-pick-a": "#74b879",
  "warehouse-pick-b": "#c77f9c", "warehouse-overhead": "#8174c7",
  "warehouse-dispatch": "#e07a61", "warehouse-cross-aisle": "#5fb7aa",
  "warehouse-rack-base": "#9a8bb5",
  rookery: "#cf83a8", quarry: "#d49a52", "river-landing": "#55b6c9",
  "submerged-chamber": "#587fc4", "signal-yard": "#b7c85f", refuge: "#8fcf9b",
  cottages: "#b49a86", "village-square": "#c8ac75", forge: "#e47750", farm: "#99c568",
  stable: "#b28a58", watermill: "#9573c4", bakery: "#ed88b1", tavern: "#e8bb48",
  workshop: "#4cb7a6", "well-site": "#6fa9d1", "stone-bridge": "#8fa8b4",
  woodlot: "#5ea66b", riverbank: "#65c7e9", "mill-shed": "#b2a292"
};
export function locationColour(id: string): string {
  if (colours[id]) return colours[id];
  let hash = 2166136261;
  for (const char of id) hash = Math.imul(hash ^ char.charCodeAt(0), 16777619) >>> 0;
  const hue = hash % 360;
  const saturation = 48 + (hash >>> 8) % 18;
  const lightness = 58 + (hash >>> 16) % 12;
  return `hsl(${hue} ${saturation}% ${lightness}%)`;
}

function linkColour(id: string): string {
  const colour = locationColour(id).replace("#", "");
  return `#${[0, 2, 4].map(offset => Math.round(Number.parseInt(colour.slice(offset, offset + 2), 16) * .66)
    .toString(16).padStart(2, "0")).join("")}`;
}

function actionLabel(action: string, args: Record<string, unknown>, state: State, actor: string): string {
  const labels: Record<string, string> = {
    go: "Walk", look: "Look", read: "Read", take: "Pick up", drop: "Put down", say: "Talk",
    village_eat: "Eat",
    village_work: "Work", village_wait: "Wait", village_sleep: "Sleep",
    village_fit_wagon: "Fit wagon parts", village_start_route: "Start wagon route",
    village_stop_route: "Stop wagon route", village_project: "Project work",
    village_meeting: "Arrange meeting", village_load_goods: "Load goods", village_unload_goods: "Unload goods",
    village_write_diary: "Diary"
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

export function recordedActionRequest(title: string, details: string): string {
  const message = details.match(/^Message:\s*([\s\S]+)$/m)?.[1]?.trim();
  const target = details.match(/^Target:\s*(.+)$/m)?.[1]?.trim();
  const recipient = details.match(/^Recipient:\s*(.+)$/m)?.[1]?.trim();
  const intention = /^Talk$/i.test(title) && message
    ? `say “${message}”`
    : /^Give:/i.test(title) || /^Give$/i.test(title)
      ? `give ${target ?? "the item"}${recipient ? ` to ${recipient}` : ""}`
      : `${title.replace(/:/g, "").trim().replace(/^./, character => character.toLowerCase())}${target ? ` ${target}` : ""}`;
  return `I want to ${intention}.`;
}

function actionArguments(action: string, args: Record<string, unknown>, state: State,
  actors: readonly GanttAgent[]): string {
  if (!Object.keys(args).length) return "No parameters";
  if (action === "village_propose_order") {
    const quantity = typeof args.quantity === "number" ? args.quantity : "?";
    const product = typeof args.product === "string" ? args.product : "product";
    const supplier = recipientName(args, state, actors) ?? "supplier";
    const deadline = typeof args.deadline === "number" ? villageClockTime(args.deadline) : "unspecified deadline";
    const fresh = typeof args.required_fresh_until === "number" ? villageClockTime(args.required_fresh_until) : null;
    return `Request ${quantity} ${product} from ${supplier} by ${deadline}${fresh ? `; keep fresh through ${fresh}` : ""}.`;
  }

  const friendly = (value: unknown): string => {
    if (typeof value === "string") return actors.find(actor => actor.id === value)?.name
      ?? state.world?.entities[value]?.name ?? value.replaceAll("_", " ").replaceAll("-", " ");
    if (Array.isArray(value)) return value.map(friendly).join(", ");
    if (value && typeof value === "object") return JSON.stringify(value);
    return String(value);
  };
  return Object.entries(args).map(([key, value]) => {
    const label = key.replaceAll("_", " ").replace(/^./, char => char.toUpperCase());
    const displayed = typeof value === "number" && /deadline|until|minute|at/.test(key)
      ? `${villageClockTime(value)} (minute ${value})` : friendly(value);
    return `${label}: ${displayed}`;
  }).join("\n");
}

function recipientName(args: Record<string, unknown>, state: State, actors: readonly GanttAgent[]): string | null {
  const recipient = typeof args.recipient === "string" ? args.recipient
    : typeof args.supplier === "string" ? args.supplier
    : typeof args.target === "string" ? args.target : null;
  if (!recipient) return null;
  const name = actors.find(actor => actor.id === recipient)?.name
    ?? state.world?.entities[recipient]?.name
    ?? state.world?.village?.dashboard?.visitors?.find(visitor => visitor.id === recipient)?.name
    ?? recipient;
  return name === recipient ? name.replaceAll("-", " ").replaceAll("_", " ")
    .replace(/\b\w/g, char => char.toUpperCase()) : name;
}

function interactionRecipients(action: string, args: Record<string, unknown>, state: State,
  actorId: string): string[] {
  const nestedNotice = args.notice && typeof args.notice === "object"
    ? args.notice as Record<string, unknown> : undefined;
  const direct = [args.recipient, args.supplier, args.preferred_supplier_id, args.listener,
    args.listener_id, ...(Array.isArray(args.recipients) ? args.recipients : []),
    ...(Array.isArray(args.participant_ids) ? args.participant_ids : []),
    ...(nestedNotice && Array.isArray(nestedNotice.recipients) ? nestedNotice.recipients : [])];
  const noticeId = typeof args.notice_id === "string" ? args.notice_id
    : typeof args.request_id === "string" && /notice|request|respond|update/.test(action) ? args.request_id : null;
  const notice = state.world?.village?.dashboard?.communication?.notices.find(item => item.id === noticeId);
  if (notice) {
    if (notice.author === actorId || notice.recipient_ids?.includes(actorId)) {
      direct.push(...(notice.author === actorId
        ? notice.recipient_ids ?? notice.recipients
        : [notice.author]));
    }
  }
  const interaction = action === "say" || action === "village_propose_order"
    || /^(village_)(ask|tell|notice_|inbox_|request_supply$|respond_to_request$|update_request$|share_known_|propose_meeting$|schedule_crew$)/.test(action);
  if (!interaction) return [];
  return [...new Set(direct.filter((id): id is string => typeof id === "string" && id !== actorId))];
}

function interactionLabel(action: string, args: Record<string, unknown>): { label: string; kind: GanttLink["kind"] } {
  if (action === "say") return { label: "Chat", kind: "dialogue" };
  if (/order|supply|request/.test(action)) {
    const product = typeof args.product === "string" ? `: ${args.product}`
      : typeof args.good === "string" ? `: ${args.good}` : "";
    return { label: `Supply request${product}`, kind: "supply" };
  }
  if (/reply|respond|update/.test(action)) return { label: "Request response", kind: "supply" };
  return { label: action.replace(/^village_/, "").replaceAll("_", " "), kind: "dialogue" };
}

function speakerName(row: { id: string; name: string }): string {
  if (row.name !== row.id) return row.name;
  return row.id.replaceAll("-", " ").replaceAll("_", " ")
    .replace(/\b\w/g, char => char.toUpperCase());
}

function decisionDiagnostics(state: State, decision?: {
  model?: string | null; compression?: z.infer<typeof diagnosticCompressionSchema>
}): {
  model?: string; promptFormat?: string; debugFlags: string[]
} {
  if (!decision) return { debugFlags: [] };
  const proxy = state.run?.config?.proxy ?? state.config.proxy;
  const format = decision.compression?.effective_mode ?? proxy.decision_message_format ?? proxy.message_format;
  const options = decision.compression?.options ?? {
    deduplicate: proxy.decision_message_deduplicate ?? proxy.message_deduplicate,
    relevant_memory: proxy.decision_message_relevant_memory ?? proxy.message_relevant_memory,
    aggregate_events: proxy.decision_message_aggregate_events ?? proxy.message_aggregate_events,
    perception_delta: proxy.decision_message_perception_delta ?? proxy.message_perception_delta
  };
  return {
    model: decision.model ?? proxy.decision_model ?? proxy.model ?? undefined,
    promptFormat: format,
    debugFlags: Object.entries(options).filter((entry): entry is [string, true] => entry[1] === true)
      .map(([name]) => name)
  };
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
  if (item.activity === "diary") return "📖";
  if (/look|read|inspect/i.test(item.label)) return "🔎";
  return "●";
}

export function buildWorldTimeline(state: State, events: readonly RunEvent[], agents: readonly GanttAgent[]) {
  const snapshot = timeSchema.parse(state.world ?? state.observation ?? {});
  const villageMinute = latestVillageMinute(state.world, state.observation);
  const hasVillageContext = Boolean(snapshot.village);
  const hasClockContext = Boolean(snapshot.clock || snapshot.bbq);
  const eventMinute = events.reduce<number | undefined>((latest, event) => {
    if (event.run_id !== state.run?.id) return latest;
    let candidate = hasVillageContext ? event.village_minute : undefined;
    let clockCandidate: number | undefined;
    if (event.kind === "observation") {
      const parsed = observedSchema.safeParse(event.payload);
      if (parsed.success) {
        if (!hasVillageContext) candidate = undefined;
        clockCandidate = timeOf(parsed.data);
      }
    }
    if (event.kind === "action_result") {
      const parsed = resultSchema.safeParse(event.payload);
      if (parsed.success) {
        if (!hasVillageContext) candidate = undefined;
        clockCandidate = timeOf(parsed.data.observation);
      }
    }
    const value = hasVillageContext ? candidate : hasClockContext ? clockCandidate : undefined;
    return value === undefined ? latest : Math.max(latest ?? value, value);
  }, undefined);
  const clockNow = Math.max(villageMinute ?? -Infinity, eventMinute ?? -Infinity, timeOf(snapshot) ?? -Infinity);
  const effectiveClockNow = Number.isFinite(clockNow) ? clockNow : undefined;
  const turns = effectiveClockNow === undefined;
  if (turns && !state.run) return null;
  let now = effectiveClockNow ?? 0;
  const village = hasVillageContext;
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
  rows.unshift({
    id: "__dungeon_master__", name: "Supervisor", locationId: "", current: true,
    occupation: "World state", visitor: false,
    positions: [] as Position[], locations: [] as { start: number; end: number; location: string }[],
    spans: [] as GanttSpan[]
  } as typeof rows[number]);
  rows.push({
    id: "__dungeon_master_review__", name: "Supervisor", locationId: "", current: true,
    occupation: "Actions reviewed", visitor: false,
    positions: [] as Position[], locations: [] as { start: number; end: number; location: string }[],
    spans: [] as GanttSpan[]
  } as typeof rows[number]);
  const byId = new Map(rows.map(row => [row.id, row]));
  const worldState = byId.get("__dungeon_master__");
  const roundReview = byId.get("__dungeon_master_review__");
  const requests = new Map<string, { event: RunEvent; request: z.infer<typeof requestSchema>;
    decision?: z.infer<typeof decisionSchema>; observation?: string }>();
  const decisions = new Map<string, z.infer<typeof decisionSchema>>();
  const observations = new Map<string, string>();
  const heardMessages = new Map<string, Set<string>>();
  const walkingPlans = new Map<string, { destination: string; purpose: string }>();
  const milestones: Milestone[] = [];
  const routeChanges: { at: number; actor: string; active: boolean }[] = [];
  const links: GanttLink[] = [];
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
  const roundNarratives = new Map<number, string>();
  for (const event of source.filter(item => item.kind === "observation")) {
    const cycle = event.round ?? event.cycle;
    if (roundNarratives.has(cycle)) continue;
    const observation = observationSchema.parse(event.payload);
    roundNarratives.set(cycle, observation.narrative.trim());
  }
  const rulings = new Map<string, z.infer<typeof dungeonMasterRulingSchema>>();
  for (const event of source.filter(event => event.kind === "dungeon_master_ruling")) {
    const ruling = dungeonMasterRulingSchema.parse(event.payload);
    rulings.set(event.actor_id + ":" + event.cycle, ruling);
  }
  const actionContexts = new Map<string, z.infer<typeof actionContextSchema>>();
  for (const event of source.filter(event => event.kind === "action_context")) {
    const context = actionContextSchema.parse(event.payload);
    actionContexts.set(context.request_id, context);
  }
  const adjudicationContexts = new Map<string, z.infer<typeof adjudicationContextSchema>>();
  for (const event of source.filter(event => event.kind === "adjudication_context")) {
    const context = adjudicationContextSchema.parse(event.payload);
    adjudicationContexts.set(context.ruling_event_id, context);
  }
  const completedRequests = new Set<string>();
  const worldStateContexts = new Map(source.filter(event => event.kind === "world_state_context").map(event => {
    const context = worldStateContextSchema.parse(event.payload);
    return [context.source_observation_event_id, context] as const;
  }));
  for (const event of source) {
    if (event.kind === "dungeon_master_review" && roundReview) {
      const review = supervisorReviewSchema.parse(event.payload);
      const diagnostics = decisionDiagnostics(state, review);
      const round = event.round ?? event.cycle;
      const at = event.village_minute ?? round - 1;
      const detail = review.findings.map(finding => finding.reason).join(" ");
      const start = turns ? Math.max(0, round - 1) : Math.floor(at / 5) * 5;
      roundReview.spans = roundReview.spans.filter(span => span.start !== start);
      roundReview.spans.push({
        start, end: turns ? round : start + 5, location: "", kind: "event",
        label: `Round ${round} / DM ${review.verdict}`,
        detail, status: review.verdict,
        dmVerdict: review.verdict,
        dmFindings: review.findings.map(finding => ({
          reason: finding.reason, evidence_ids: finding.evidence_ids ?? []
        })),
        ...(diagnostics.model ? { model: diagnostics.model } : {}),
        ...(diagnostics.promptFormat ? { promptFormat: diagnostics.promptFormat } : {}),
        ...(diagnostics.debugFlags.length ? { debugFlags: diagnostics.debugFlags } : {})
      });
    }
    if (event.kind === "decision") {
      decisions.set(event.actor_id, decisionSchema.parse(event.payload));
    } else if (event.kind === "action_request") {
      const request = requestSchema.parse(event.payload);
      const decision = decisions.get(event.actor_id);
      requests.set(request.request_id, { event, request,
        ...(decision?.action_id === request.action_id ? { decision } : {}),
        ...(observations.has(event.actor_id) ? { observation: observations.get(event.actor_id) } : {}) });
      decisions.delete(event.actor_id);
    } else if (event.kind === "observation") {
      const observation = observationSchema.parse(event.payload);
      const entities = observation.visible_entities.map(entity => {
        const name = state.world?.entities[entity.id]?.name ?? entity.id;
        const raw = entity as { id: string; open?: boolean; required_work?: string[]; status?: string };
        const stateParts = [raw.open === true ? "open" : raw.open === false ? "closed" : null,
          raw.required_work?.length ? `needs ${raw.required_work.join(", ")}` : null]
          .filter(Boolean);
        return `${name}${stateParts.length ? ` (${stateParts.join("; ")})` : ""}`;
      });
      const actors = (observation.visible_actors ?? []).map(actor => actor.actor_id);
      const heard = heardMessages.get(event.actor_id) ?? new Set<string>();
      const messages = (observation.messages ?? [])
        .filter(message => message.actor_id !== event.actor_id)
        .map(message => `${message.actor_id}: “${message.text}”`)
        .filter(message => !heard.has(message));
      messages.forEach(message => heard.add(message));
      heardMessages.set(event.actor_id, heard);
      const carrying = observation.self.inventory.map(item => item.name);
      const sharedNarrative = roundNarratives.get(event.round ?? event.cycle) ?? "";
      const privatePerspective = [observation.location.description, observation.narrative]
        .filter((text, index, values) => text && values.indexOf(text) === index
          && !sharedNarrative.includes(text.trim()));
      observations.set(event.actor_id, [
        privatePerspective.join(" "),
        carrying.length ? `Carrying: ${carrying.join(", ")}.` : "Carrying: nothing.",
        entities.length ? `Nearby: ${entities.join(", ")}.` : "Nothing else is visible nearby.",
        actors.length ? `Other actors visible: ${actors.join(", ")}.` : "No other actors are visible.",
        messages.length ? `Messages heard: ${messages.join(" ")}` : ""
      ].filter(Boolean).join("\n\n"));
      recordObservation(observation, event.sequence);
      if (worldState && event.sequence === Math.min(...source.filter(item =>
        item.kind === "observation" && item.cycle === event.cycle).map(item => item.sequence))) {
        const round = event.round ?? event.cycle;
        const at = timeOf(observation);
        const start = turns ? Math.max(0, round - 1) : Math.floor((at ?? 0) / 5) * 5;
        const end = turns ? round : start + 5;
        const occupied = rows.filter(row => row.id !== "__dungeon_master__").map(row => {
          const position = row.positions.filter(item => item.order < event.sequence).at(-1);
          return position ? `${row.name} at ${state.world?.entities[position.location]?.name ?? position.location}` : null;
        }).filter((value): value is string => value !== null);
        const detail = `${observation.narrative}\n\nCast state: ${occupied.join(";") || "initializing"}.`;
        const summary = worldStateContexts.get(event.id);
        const diagnostics = summary ? decisionDiagnostics(state, summary) : undefined;
        const existing = worldState.spans.find(span => span.start === start);
        if (existing) {
          existing.originalDetail = detail;
          existing.detail = summary?.description ?? detail;
        }
        else worldState.spans.push({
          start, end, location: "", kind: "event",
          label: `World state / Round ${round}`,
          detail: summary?.description ?? detail, originalDetail: detail, status: "RECORDED",
          ...(diagnostics?.model ? { model: diagnostics.model } : {}),
          ...(diagnostics?.promptFormat ? { promptFormat: diagnostics.promptFormat } : {}),
          ...(diagnostics?.debugFlags.length ? { debugFlags: diagnostics.debugFlags } : {})
        });
      }
    } else if (event.kind === "action_result") {
      const result = resultSchema.parse(event.payload);
      const linked = requests.get(result.request_id);
      const row = byId.get(event.actor_id);
      const duplicate = completedRequests.has(result.request_id);
      completedRequests.add(result.request_id);
      if (turns && (!linked || !row || duplicate)) continue;
      if (turns) {
        now = event.round ?? now + 1;
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
      const start = turns ? Math.max(0, at - 1)
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
      const decision = result.observation.village?.fallback?.decision;
      const waitReason = kind === "wait" && decision?.selected_task_id === "wait:event"
        ? decision.reason.trim().replace(/[.!?]+$/, "") : "";
      const waitLabel = waitReason ? /^waiting\b/i.test(waitReason) ? waitReason : `Waiting: ${waitReason}` : "";
      const diaryNote = action === "village_write_diary"
        ? [typeof args.text === "string" ? args.text : "",
          typeof args.problems === "string" ? `Problems: ${args.problems}` : "",
          typeof args.suggestions === "string" ? `Suggestions: ${args.suggestions}` : ""]
          .filter(Boolean).join("\n") : "";
      const readNote = /(?:^read$|read_|_read$)/i.test(action)
        ? `Reading: ${villageClockText(result.explanation)} Reason: ${linked.decision?.justification.trim()
          || "review local information before the next task"}. Learned: ${villageClockText(result.explanation)}` : "";
      const villagePlan = action === "village_daily_plan" && result.outcome === "APPLIED"
        ? state.world?.village?.events?.filter(item => item.action === "daily_plan" && item.actor === event.actor_id)
          .filter(item => item.minute <= at).at(-1)
        : undefined;
      const recordedDetail = action === "look" && /\b(?:when|until|after|once)\b/i.test(result.explanation)
        ? `Inspection only; this is a future condition, not a completed outcome: ${result.explanation}`
        : villageClockText(result.explanation);
      const diagnostics = decisionDiagnostics(state, linked.decision);
      const actionContext = actionContexts.get(linked.request.request_id);
      const contextDiagnostics = actionContext ? decisionDiagnostics(state, actionContext) : undefined;
      const ruling = rulings.get(event.actor_id + ":" + event.cycle);
      const adjudicationContext = ruling ? adjudicationContexts.get(
        source.find(item => item.kind === "dungeon_master_ruling" && item.actor_id === event.actor_id
          && item.cycle === event.cycle)?.id ?? "") : undefined;
      const adjudicationDiagnostics = adjudicationContext
        ? decisionDiagnostics(state, adjudicationContext) : undefined;
      if (kind !== "sleep") row.spans.push({
        start, end: Math.max(start, Math.min(now, end)), location: result.observation.self.location_id,
        label: waitLabel ? waitLabel
          : action === "say" && recipientName(args, state, cast) ? `${speakerName(row)} → ${recipientName(args, state, cast)}`
          : research ? `Research: ${research.title}`
          : walkingPlan ? `Walk to ${state.world?.entities[walkingPlan.destination]?.name ?? walkingPlan.destination}`
          : actionLabel(action, args, state, event.actor_id),
        detail: waitReason ? `Reason: ${waitReason}`
          : diaryNote ? `${diaryNote}\n${villageClockText(result.explanation)}`
          : readNote ? readNote
          : research ? `${researchDetail(research)} ${villageClockText(result.explanation)}`
          : villagePlan ? dailyPlanDetail(villagePlan)
          : planForTile
          ? `Purpose: ${planForTile.purpose}${/[.!?]$/.test(planForTile.purpose) ? "" : "."} ${villageClockText(result.explanation)}`
          : recordedDetail, status: result.outcome, kind,
        activity: action === "village_daily_plan" ? "planning"
          : action === "village_write_diary" ? "diary"
          : action === "village_eat" ? "meals"
          : action === "go" || action === "village_plan_walk" ? "movement"
          : ["say", "village_ask", "village_tell", "village_share_route", "village_meeting"].includes(action) ? "dialogue"
          : life?.work || /^village_(work|project|research|repair|maintain|craft|produce|load_goods|unload_goods)(_|$)/.test(action) ? "work" : undefined,
        ...(rulings.has(event.actor_id + ":" + event.cycle) ? {
          dmVerdict: rulings.get(event.actor_id + ":" + event.cycle)!.verdict,
          dmComment: rulings.get(event.actor_id + ":" + event.cycle)!.reason,
          dmNarrative: adjudicationContext?.description,
          dmModel: rulings.get(event.actor_id + ":" + event.cycle)!.model ?? "engine",
          dmWordingModel: adjudicationDiagnostics?.model,
          dmFindings: rulings.get(event.actor_id + ":" + event.cycle)!.findings,
          dmPromptFormat: adjudicationDiagnostics?.promptFormat
            ?? (rulings.get(event.actor_id + ":" + event.cycle)!.compression?.effective_mode as string | undefined)
            ?? "deterministic",
          dmDebugFlags: adjudicationDiagnostics?.debugFlags.length ? adjudicationDiagnostics.debugFlags
            : Object.entries(((rulings.get(event.actor_id + ":" + event.cycle)!.compression?.options ?? {}) as object))
            .filter((entry): entry is [string, true] => entry[1] === true).map(([name]) => name)
        } : {}),
        arguments: actionArguments(action, args, state, cast),
        ...(linked.observation ? { observation: linked.observation } : {}),
        ...(action === "go" && result.observation.self.location_id !== prior?.location ? {
          fromLocation: prior?.location ?? "",
          toLocation: result.observation.self.location_id
        } : {}),
        ...(linked.decision?.justification.trim() ? { reasoning: linked.decision.justification.trim() } : {}),
        ...(diagnostics.model ? { model: diagnostics.model } : {}),
        ...(diagnostics.promptFormat ? { promptFormat: diagnostics.promptFormat } : {}),
        ...(diagnostics.debugFlags.length ? { debugFlags: diagnostics.debugFlags } : {}),
        ...(actionContext ? { contextualDescription: actionContext.description } : {}),
        ...(contextDiagnostics?.model ? { contextualModel: contextDiagnostics.model } : {}),
        ...(contextDiagnostics?.promptFormat ? { contextualPromptFormat: contextDiagnostics.promptFormat } : {}),
        ...(contextDiagnostics?.debugFlags.length ? { contextualDebugFlags: contextDiagnostics.debugFlags } : {}),
        ...(linked.decision?.expected_outcome?.trim() ? { expectedOutcome: linked.decision.expected_outcome.trim() } : {}),
        ...(linked.decision?.subgoal?.trim() ? { subgoal: linked.decision.subgoal.trim() } : {}),
        ...(linked.decision?.plan?.length ? { plan: linked.decision.plan } : {})
      });
      const link = interactionLabel(action, args);
      const reference = typeof args.notice_id === "string" ? args.notice_id
        : /order|supply|request/.test(action) ? linked.request.request_id : undefined;
      const recipients = interactionRecipients(action, args, state, event.actor_id);
      if (!recipients.length && reference) {
        const earlier = links.find(link => link.reference === reference && link.to === event.actor_id);
        if (earlier) recipients.push(earlier.from);
      }
      for (const recipient of recipients) {
        links.push({ at: start, from: event.actor_id, to: recipient, label: link.label,
          detail: `${row.name} to ${recipientName({ recipient }, state, cast) ?? recipient}: ${result.explanation}`,
          kind: link.kind, fromStart: start, fromEnd: Math.max(start, Math.min(now, end)), reference });
      }
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
    } else if (event.kind === "dungeon_master_appeal_resolved") {
      const appeal = dungeonAppealSchema.parse(event.payload);
      const row = byId.get(appeal.actor);
      const at = event.village_minute ?? appeal.created_at;
      if (!row || at > now) continue;
      const changes = appeal.world_changes?.map(change =>
        `World change: added ${change.quantity} ${change.product} at ${change.location}.`).join(" ") ?? "";
      row.spans.push({
        start: at, end: at, location: row.locationId, kind: "event",
        label: `Dungeon Master ${appeal.status}`,
        detail: `${row.name} asked: ${appeal.request}\nReply: ${appeal.response ?? "No intervention was granted."}`
          + `${appeal.npc_acknowledgement ? `\n${row.name}: ${appeal.npc_acknowledgement}` : ""}`
          + `${changes ? `\n${changes}` : ""}`,
        status: appeal.status, activity: "dialogue"
      });
    }
  }
  for (const [requestId, linked] of requests) {
    if (!state.in_flight || completedRequests.has(requestId)) continue;
    const row = byId.get(linked.event.actor_id);
    if (!row) continue;
    const at = turns ? now : linked.event.village_minute
      ?? row.positions.filter(point => point.order < linked.event.sequence).at(-1)?.at ?? now;
    if (at > now) continue;
    const action = linked.request.action_id;
    const diagnostics = decisionDiagnostics(state, linked.decision);
    row.spans.push({
      start: at, end: at,
      location: row.positions.filter(point => point.order < linked.event.sequence).at(-1)?.location ?? row.locationId,
      label: actionLabel(action, linked.request.arguments, state, linked.event.actor_id),
      detail: "Action in progress. Waiting for the world result.", status: "IN_PROGRESS",
      kind: "free",
      ...(diagnostics.model ? { model: diagnostics.model } : {}),
      ...(diagnostics.promptFormat ? { promptFormat: diagnostics.promptFormat } : {}),
      ...(diagnostics.debugFlags.length ? { debugFlags: diagnostics.debugFlags } : {}),
      activity: ["say", "village_ask", "village_tell", "village_share_route", "village_meeting"].includes(action)
        ? "dialogue" : action === "go" || action === "village_plan_walk" ? "movement" : undefined,
      arguments: actionArguments(action, linked.request.arguments, state, cast),
      ...(linked.decision?.justification.trim() ? { reasoning: linked.decision.justification.trim() } : {}),
      ...(linked.decision?.subgoal?.trim() ? { subgoal: linked.decision.subgoal.trim() } : {}),
      ...(linked.decision?.plan?.length ? { plan: linked.decision.plan } : {})
    });
  }
  const initialWorldStateContext = [...worldStateContexts.values()].at(0);
  if (worldState && initialWorldStateContext) {
    const diagnostics = decisionDiagnostics(state, initialWorldStateContext);
    const initial = worldState.spans.at(0);
    if (initial) {
      initial.originalDetail ??= initial.detail;
      initial.detail = initialWorldStateContext.description;
      if (diagnostics.model) initial.model = diagnostics.model;
      if (diagnostics.promptFormat) initial.promptFormat = diagnostics.promptFormat;
      if (diagnostics.debugFlags.length) initial.debugFlags = diagnostics.debugFlags;
    }
  }
  if (worldState && !worldState.spans.length && effectiveClockNow !== undefined) {
    const width = village ? 5 : 1;
    const start = Math.max(0, now - width);
    const end = Math.max(now, start + Number.EPSILON);
    const narrative = initialWorldStateContext?.description ?? (state.observation?.narrative
      || (state.world ? "Initial world state recorded." : "Initial state recorded."));
    const diagnostics = initialWorldStateContext ? decisionDiagnostics(state, initialWorldStateContext) : undefined;
    worldState.spans.push({
      start, end, location: "", kind: "event",
      label: "World state / Initial observation",
      detail: narrative, originalDetail: state.observation?.narrative, status: "RECORDED",
      ...(diagnostics?.model ? { model: diagnostics.model } : {}),
      ...(diagnostics?.promptFormat ? { promptFormat: diagnostics.promptFormat } : {}),
      ...(diagnostics?.debugFlags.length ? { debugFlags: diagnostics.debugFlags } : {})
    });
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
    if (row && event.action === "daily_plan") {
      row.spans.push({ start: event.minute, end: event.minute, location: event.target, kind: "event",
        label: "Daily planning",
        detail: dailyPlanDetail(event), activity: "planning" });
    }
    if (row && event.action === "customer_adjustment_required") {
      row.spans.push({ start: event.minute, end: event.minute, location: event.target, kind: "event",
          label: "Customer adjustment required", detail: event.result, activity: "dialogue" });
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
    links.push({ at: trade.minute, from: trade.resident, to: trade.visitor,
      label: "Completed trade", detail, kind: "trade", fromStart: trade.minute,
      fromEnd: trade.minute, toStart: trade.minute, toEnd: trade.minute });
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
      const linked = row.spans.find(span => span.kind === "action" && span.start === activity.start);
      row.spans = row.spans.filter(span => !(span.kind === "action" && span.start === activity.start));
      row.spans.push({
        start: activity.start, end, location: activity.location, label: activity.label,
        detail: `${activity.label} / ${activity.status}`, status: activity.status, kind: "action",
        activity: linked?.activity
      });
      row.positions.push({ at: activity.start, order: activity.start, location: activity.location });
    }
    if (row.current && row.locationId && !(visitor?.activities.some(activity =>
      activity.start <= now && now < activity.end && /travel/i.test(activity.label)))) row.positions.push({
      at: now, order: Number.MAX_SAFE_INTEGER, location: row.locationId
    });
    const lastRecorded = row.positions.filter(point => point.order !== Number.MAX_SAFE_INTEGER).at(-1);
    const currentAtNow = row.positions.find(point => point.at === now && point.order === Number.MAX_SAFE_INTEGER);
    if (turns && lastRecorded && currentAtNow && lastRecorded.location !== currentAtNow.location) {
      row.positions.push({ at: now, order: Number.MAX_SAFE_INTEGER - 1, location: currentAtNow.location });
    }
    row.positions.sort((a, b) => a.at - b.at || a.order - b.order);
    const atTurn = new Map<number, typeof row.positions[number]>();
    for (const point of row.positions) {
      const existing = atTurn.get(point.at);
      if (!existing || point.order >= existing.order) atTurn.set(point.at, point);
    }
    const points = [...atTurn.values()];
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
        if (previous.label !== wait.label) {
          previous.label = "Waiting: multiple reasons";
          if (!previous.detail.split("\n").includes(wait.detail)) previous.detail += `\n${wait.detail}`;
        }
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
  if (turns && state.run && ["succeeded", "blocked", "failed", "stalled", "cancelled"].includes(state.run.status)) {
    const terminalTurn = now + 1;
    for (const row of rows) {
      if (row.current && row.locationId) {
        row.positions = row.positions.filter(point => point.order !== Number.MAX_SAFE_INTEGER);
        row.positions.push({ at: terminalTurn, order: Number.MAX_SAFE_INTEGER, location: row.locationId });
        row.positions.sort((a, b) => a.at - b.at || a.order - b.order);
        const atTurn = new Map<number, typeof row.positions[number]>();
        for (const point of row.positions) {
          const existing = atTurn.get(point.at);
          if (!existing || point.order >= existing.order) atTurn.set(point.at, point);
        }
        const points = [...atTurn.values()];
        row.locations = [];
        points.forEach((point, index) => {
          const end = points[index + 1]?.at ?? terminalTurn;
          if (end <= point.at) return;
          const previous = row.locations.at(-1);
          if (previous?.location === point.location && previous.end === point.at) previous.end = end;
          else row.locations.push({ start: point.at, end, location: point.location });
        });
      }
    }
    now = terminalTurn;
  }
  const times = rows.flatMap(row => [...row.positions.map(point => point.at), ...row.spans.map(span => span.start)]);
  const start = Math.min(now, ...times);
  const paired = new Set<GanttLink>();
  const resolvedLinks: GanttLink[] = links.filter(link => link.kind === "trade");
  const interactions = links.filter(link => link.kind !== "trade").sort((a, b) => a.at - b.at);
  for (const call of interactions) {
    if (paired.has(call)) continue;
    const response = interactions.find(item => item !== call && !paired.has(item)
      && item.from === call.to && item.to === call.from && item.at >= call.at
      && (call.reference && item.reference ? call.reference === item.reference
        : item.at - call.at <= 120));
    if (!response) continue;
    paired.add(call);
    paired.add(response);
    resolvedLinks.push({ ...call, toStart: response.fromStart, toEnd: response.fromEnd,
      detail: `${call.detail} Response: ${response.detail}` });
  }
  return { start, now, village, turns, rows, links: resolvedLinks,
    milestones: milestones.filter(mark => mark.at >= start && mark.at <= now) };
}

export function worldGanttMarkup(state: State, events: readonly RunEvent[], agents: readonly GanttAgent[],
  scale: GanttScale = {}): string {
  const model = buildWorldTimeline(state, events, agents);
  if (!model || !model.rows.length) return "";
  const activity = scale.activity ?? "all";
  const cutoff = scale.recent ? Math.max(model.start, model.now - (model.turns ? 20 : 60)) : model.start;
  const chart = { ...model, start: cutoff };
  const trackedByScenario: Record<string, Set<string>> = {
    "millhaven-warehouse-snapshot": new Set(["jory", "ada", "nella"]),
    "millhaven-warehouse-teamwork": new Set(["jory", "ada", "nella"])
  };
  const tracked = trackedByScenario[state.run?.scenario_id ?? ""];
  const hiddenRows = scale.showUntracked ? new Set<string>() : new Set(chart.rows
    .filter(row => !row.id.startsWith("__dungeon_master")
      && (tracked ? !tracked.has(row.id) : !row.current))
    .map(row => row.id));
  if (scale.showSupervisor === false) {
    hiddenRows.add("__dungeon_master__");
    hiddenRows.add("__dungeon_master_review__");
  }
  const matches = (item: GanttSpan) => activity === "all"
    || (activity === "actions" && item.kind === "action" && item.activity === undefined)
    || (activity === "movement" && item.kind === "travel")
    || (activity === "route" && item.kind === "route")
    || (activity === "waiting" && item.kind === "wait")
    || (activity === "sleeping" && item.kind === "sleep")
    || (activity === "free" && item.kind === "free")
    || (activity === "rejected" && item.kind === "rejected")
    || (activity === "events" && item.kind === "event")
    || activity === item.activity;
  const visible = (item: { start: number; end: number }) => item.start === item.end ? item.start >= cutoff : item.end > cutoff;
  const format = (minute: number) => chart.turns ? `Turn ${minute}`
    : chart.village ? villageClockTime(minute) : clockTime(minute * 60);
  const span = Math.max(chart.turns ? 1 : 10, chart.now - chart.start);
  const zoom = Math.max(1, Math.min(8, scale.zoom ?? 1));
  const roundCount = Math.max(1, Math.ceil(chart.now - chart.start));
  const requestedRoundWidth = chart.turns && !scale.recent ? roundCount * 256 : 0;
  const width = Math.round(Math.max(requestedRoundWidth, Math.max(120, scale.viewportWidth ?? 900)) * zoom);
  const spacing = Math.max(chart.turns ? 1 : chart.village ? 5 : 0.5, span / Math.max(1, width / 65));
  const tick = [0.5, 1, 2, 5, 10, 15, 30, 60, 120, 360, 720, 1440, 10080].find(value => value >= spacing)
    ?? Math.ceil(spacing / 1440) * 1440;
  const start = Math.floor(chart.start / tick) * tick;
  const end = Math.max(start + tick * 2, Math.ceil(chart.now / tick) * tick + tick);
  const top = 58, rowHeight = 210, height = top + rowHeight * chart.rows.length + 30;
  const x = (minute: number) => (minute - start) / (end - start) * (width - 20) + 10;
  const rowY = (id: string, instant = false) => {
    const index = chart.rows.findIndex(row => row.id === id);
    return index < 0 ? null : top + index * rowHeight + (instant ? 92 : 51);
  };
  const placeName = (id: string) => state.world?.entities[id]?.name ?? id.replaceAll("-", " ");
  const tooltip = (text: string) => `<title>${escape(text)}</title>`;
  const shorten = (text: string, characters: number) => text.length > characters
    ? `${text.slice(0, Math.max(0, characters - 3)).trimEnd()}...` : text;
  const grid: string[] = [];
  const roundHeaders: string[] = [];
  for (let minute = start; minute <= end; minute += tick) {
    const anchor = minute === start ? "start" : minute === end ? "end" : "middle";
    grid.push(`<line x1="${x(minute)}" x2="${x(minute)}" y1="${top - 12}" y2="${height - 20}" class="gantt-gridline"/>
      <text x="${x(minute)}" y="${height - 5}" text-anchor="${anchor}" class="gantt-tick">${escape(chart.village ? villageClockTime(minute % 1440) : format(minute))}</text>`);
    if (chart.turns && Number.isInteger(minute) && minute > 0 && minute < end) {
      roundHeaders.push(`<text x="${x(minute - .5)}" y="22" text-anchor="middle" class="gantt-round-header">Round ${minute}</text>`);
    }
  }
  const legend = new Map<string, string>();
  const wrap = (text: string, characters: number, lines: number) => {
    const words = text.replace(/\s+/g, " ").trim().split(" ");
    const result: string[] = [];
    for (const word of words) {
      const current = result.at(-1);
      if (!current || current.length + word.length + 1 > characters) result.push(word);
      else result[result.length - 1] = `${current} ${word}`;
      if (result.length > lines) break;
    }
    if (result.length > lines) result.splice(lines, result.length, "...");
    return result;
  };
  const rows = chart.rows.filter(row => !hiddenRows.has(row.id)).map((row, index) => {
    const y = top + index * rowHeight;
    const sourceLocations = row.locations.filter(visible);
    const movementIntervals = row.spans.filter(span => span.kind === "travel"
      && span.fromLocation && span.toLocation && visible(span));
    const regionSegments: { start: number; end: number; location: string }[] = [];
    for (const band of sourceLocations) {
      const movements = movementIntervals.filter(move => move.start >= band.start && move.end <= band.end);
      let cursor = band.start;
      for (const move of movements) {
        if (cursor < move.start) regionSegments.push({ start: cursor, end: move.start, location: move.fromLocation! });
        regionSegments.push({ start: move.start, end: move.start + (move.end - move.start) / 2, location: move.fromLocation! });
        regionSegments.push({ start: move.start + (move.end - move.start) / 2, end: move.end, location: move.toLocation! });
        cursor = move.end;
      }
      if (cursor < band.end) regionSegments.push({ start: cursor, end: band.end, location: band.location });
      if (!movements.length) regionSegments.push({ start: band.start, end: band.end, location: band.location });
    }
    const mergedRegions = regionSegments.sort((a, b) => a.start - b.start || a.end - b.end)
      .reduce<typeof regionSegments>((merged, region) => {
      const previous = merged.at(-1);
      if (previous?.location === region.location && region.start <= previous.end) {
        previous.end = Math.max(previous.end, region.end);
      }
      else merged.push({ ...region });
      return merged;
    }, []);
    const locations = mergedRegions.map((band, bandIndex) => {
      band = { ...band, start: Math.max(cutoff, band.start) };
      legend.set(band.location, placeName(band.location));
      const w = x(band.end) - x(band.start);
      const clip = `gantt-location-${index}-${bandIndex}`;
      const backgroundHeight = 158;
      return `<g class="gantt-location">${tooltip(`${row.name} / ${placeName(band.location)} / ${format(band.start)} - ${format(band.end)}`)}
        <clipPath id="${clip}"><rect x="${x(band.start) + 6}" y="${y + 4}" width="${Math.max(0, w - 12)}" height="${backgroundHeight}" rx="8"/></clipPath>
        <rect x="${x(band.start)}" y="${y + 4}" width="${w}" height="${backgroundHeight}" rx="0" fill="${locationColour(band.location)}" opacity=".8"/>
        ${w > 80 ? `<text x="${x(band.start) + w / 2}" y="${y + 16}" text-anchor="middle" class="gantt-place" clip-path="url(#${clip})">${escape(placeName(band.location))}</text>` : ""}</g>`;
    }).join("");
    const spans = row.spans.filter(visible);
    const durationSpans = spans.filter(item => item.end > item.start)
      .sort((a, b) => Number(a.kind !== "route" && a.kind !== "sleep") - Number(b.kind !== "route" && b.kind !== "sleep"));
    const bars = durationSpans.map((item, barIndex) => {
      const left = x(Math.max(cutoff, item.start));
      const w = x(item.end) - left;
      const inset = Math.min(10, w / 5);
      const tileWidth = w - inset * 2;
      const primaryHeight = row.id.startsWith("__dungeon_master") ? 128 : 64;
      const id = `gantt-clip-${index}-${barIndex}`;
      const location = item.location ? placeName(item.location) : "";
      const duration = chart.turns ? `${item.end - item.start} action attempt`
        : chart.village ? `${Math.round((item.end - item.start) * 10) / 10} min`
        : `${Math.round((item.end - item.start) * 600) / 10} sec`;
      const range = chart.turns ? format(item.end) : `${format(item.start)} - ${format(item.end)}`;
      const timing = `${duration} / ${range}`;
      const decisionDetail = [item.arguments ? `Action details: ${item.arguments}` : "",
        item.observation ? `Observation: ${item.observation}` : "",
        item.subgoal ? `Subgoal: ${item.subgoal}` : "", item.expectedOutcome ? `Expected: ${item.expectedOutcome}` : "",
        item.plan?.length ? `Plan: ${item.plan.join("; ")}` : "", item.reasoning ? `Reason: ${item.reasoning}` : ""]
        .filter(Boolean).join(" ");
      const detail = `${row.name} / ${range} / ${duration}${location ? ` / ${location}` : ""} / ${item.label}. ${item.detail}${decisionDetail ? ` ${decisionDetail}` : ""}`;
      const label = item.kind === "wait" && item.label === "Wait" ? "Waiting" : item.label;
      const summary = (row.id.startsWith("__dungeon_master")
        ? item.detail
        : item.contextualDescription ?? recordedActionRequest(label, item.arguments ?? "")
      ).replace(/\s+/g, " ").trim();
      const icon = activityIcon(item);
      const status = item.status ?? (item.kind === "sleep" || item.kind === "route" || item.kind === "wait" ? "Recorded" : "");
      const iconWidth = 24;
      const characters = tileWidth >= 48 ? Math.floor((tileWidth - 16 - iconWidth) / 7) : 0;
      const detailCharacters = Math.floor((tileWidth - 16) / 6);
      const dmNarrative = row.id.startsWith("__dungeon_master")
        ? wrap(summary, Math.max(18, detailCharacters), 7) : [];
      const dmCommentLines = item.dmNarrative || item.dmComment
        ? wrap(item.dmNarrative || item.dmComment!, Math.max(16, detailCharacters), 3) : [];
      const npcRequestLines = !row.id.startsWith("__dungeon_master")
        ? wrap(summary, Math.max(18, detailCharacters), 2) : [];
      const movementSplit = "";
      return `<g tabindex="0" role="img" data-gantt-focus="${escape(`${row.id}:${item.kind}:${item.start}:${item.label}`)}" aria-label="${escape(detail)}"             aria-describedby="gantt-tooltip"             data-gantt-kind="${row.id === "__dungeon_master_review__" ? "dm-review" : row.id === "__dungeon_master__" ? "dm-state" : "npc"}" data-gantt-title="${escape(item.label)}" data-gantt-icon="${icon}" data-gantt-status="${escape(status)}" data-gantt-duration="${escape(duration)}" data-gantt-range="${escape(range)}" data-gantt-place="${escape(location)}" data-gantt-model="${escape(item.model ?? "engine")}" data-gantt-prompt="${escape(item.promptFormat ?? "deterministic")}" data-gantt-flags="${escape(item.debugFlags?.join(",") ?? "none")}" data-gantt-context-description="${escape(item.contextualDescription ?? "")}" data-gantt-context-model="${escape(item.contextualModel ?? "")}"       data-gantt-context-prompt="${escape(item.contextualPromptFormat ?? "")}" data-gantt-context-flags="${escape(item.contextualDebugFlags?.join(",") ?? "")}" data-gantt-findings="${escape(JSON.stringify(item.dmFindings ?? []))}" data-gantt-detail="${escape(item.detail)}" data-gantt-original-detail="${escape(item.originalDetail ?? "")}" data-gantt-observation="${escape(item.observation ?? "")}" data-gantt-dm-verdict="${escape(item.dmVerdict ?? "")}" data-gantt-dm-comment="${escape(item.dmComment ?? "")}" data-gantt-arguments="${escape(item.arguments ?? "")}" data-gantt-expected="${escape(item.expectedOutcome ?? "")}" data-gantt-subgoal="${escape(item.subgoal ?? "")}" data-gantt-plan="${escape(item.plan?.join("\n") ?? "")}" data-gantt-reasoning="${escape(item.reasoning ?? "")}" data-gantt-agent-name="${escape(row.name)}" class="gantt-bar ${item.kind}${item.activity === "planning" ? " activity-planning" : ""}${matches(item) ? "" : " is-filtered-out"}">
        <clipPath id="${id}"><rect x="${left + inset + 6}" y="${y + 19}" width="${Math.max(0, tileWidth - 12)}" height="${primaryHeight}"/></clipPath>
        <rect x="${left + inset}" y="${y + 19}" width="${tileWidth}" height="${primaryHeight}" rx="7"/>
        ${movementSplit}
        ${tileWidth >= 28 ? `<text x="${left + inset + 7}" y="${y + 37}" class="gantt-cell-icon" clip-path="url(#${id})">${icon}</text>` : ""}
        ${characters >= 6 && !row.id.startsWith("__dungeon_master") ? `<text x="${left + w / 2 + (tileWidth >= 48 ? iconWidth / 2 : 0)}" y="${y + 37}" text-anchor="middle" class="gantt-cell-title npc-action-request" clip-path="url(#${id})">${escape(shorten(label, characters))}</text>` : ""}
        ${false ? `<text x="${left + w / 2}" y="${y + 54}" text-anchor="middle" class="gantt-cell-time" clip-path="url(#${id})">${escape(shorten(detailCharacters >= 30 ? timing : duration, detailCharacters))}</text>` : ""}
        ${dmNarrative.length ? dmNarrative.map((line, lineIndex) =>
          `<text x="${left + w / 2}" y="${y + 48 + lineIndex * 14}" text-anchor="middle" class="gantt-cell-detail" clip-path="url(#${id})">${escape(line)}</text>`).join("")
          : npcRequestLines.length ? npcRequestLines.map((line, lineIndex) =>
            `<text x="${left + w / 2}" y="${y + 61 + lineIndex * 14}" text-anchor="middle" class="gantt-cell-detail" clip-path="url(#${id})">${escape(line)}</text>`).join("") : ""}
        ${item.dmComment ? `</g><g tabindex="0" role="img" aria-describedby="gantt-tooltip" data-gantt-focus="${escape(`${row.id}:dm:${item.start}:${item.label}`)}" data-gantt-kind="dm" data-gantt-title="Action Adjudication" data-gantt-icon="⚖" data-gantt-status="${escape(item.dmVerdict ?? "adjudication")}" data-gantt-range="${escape(range)}" data-gantt-place="${escape(location)}" data-gantt-model="${escape(item.dmModel ?? "engine")}" data-gantt-wording-model="${escape(item.dmWordingModel ?? "")}" data-gantt-prompt="${escape(item.dmPromptFormat ?? "deterministic")}" data-gantt-flags="${escape(item.dmDebugFlags?.join(",") || "none")}" data-gantt-detail="${escape(item.dmNarrative || item.dmComment)}" data-gantt-original-detail="${escape(item.dmComment)}" data-gantt-findings="${escape(JSON.stringify(item.dmFindings ?? []))}" data-gantt-agent-name="Supervisor" class="gantt-dm-tile${matches(item) ? "" : " is-filtered-out"}"><rect x="${left + inset}" y="${y + 88}" width="${tileWidth}" height="64" rx="7" class="gantt-dm-box"/>${dmCommentLines.map((line, lineIndex) => `<text x="${left + w / 2}" y="${y + 104 + lineIndex * 15}" text-anchor="middle" class="gantt-dm-text gantt-dm-comment">${escape(line)}</text>`).join("")}` : ""}</g>`;
    }).join("");
    const markers = new Map<number, GanttSpan[]>();
    for (const item of spans.filter(item => item.end === item.start)) {
      const list = markers.get(item.start) ?? [];
      list.push(item);
      markers.set(item.start, list);
    }
    const dots = [...markers].map(([at, items]) => {
      const detail = `${row.name} / ${format(chart.turns ? at + 1 : at)} / ${items.map(item => [
        `${item.label}: ${item.detail}`, item.arguments ? `Action details: ${item.arguments}` : "",
        item.subgoal ? `Subgoal: ${item.subgoal}` : "", item.expectedOutcome ? `Expected: ${item.expectedOutcome}` : "",
        item.plan?.length ? `Plan: ${item.plan.join("; ")}` : ""
      ].filter(Boolean).join(" ")).join("; ")}`;
      const reasoning = items.map(item => item.reasoning).filter(Boolean).join("; ");
      const argumentsText = items.map(item => item.arguments).filter(Boolean).join("\n");
      const expected = items.map(item => item.expectedOutcome).filter(Boolean).join("; ");
      const subgoal = items.map(item => item.subgoal).filter(Boolean).join("; ");
      const plan = items.flatMap(item => item.plan ?? []).join("\n");
      const observation = items.map(item => item.observation).filter(Boolean).join("\n");
      return `<g tabindex="0" role="img" data-gantt-focus="${escape(`${row.id}:marker:${at}`)}" aria-label="${escape(detail + (reasoning ? ` Reason: ${reasoning}` : ""))}" aria-describedby="gantt-tooltip" data-gantt-title="${escape(items.length === 1 ? items[0].label : `${items.length} events`)}" data-gantt-icon="●" data-gantt-status="${escape(items.some(item => item.kind === "rejected") ? "REJECTED" : "Recorded")}" data-gantt-range="${escape(format(chart.turns ? at + 1 : at))}" data-gantt-place="${escape(items[0].location ? placeName(items[0].location) : "")}" data-gantt-model="${escape(items.find(item => item.model)?.model ?? "engine")}" data-gantt-prompt="${escape(items.find(item => item.promptFormat)?.promptFormat ?? "deterministic")}" data-gantt-flags="${escape(items.flatMap(item => item.debugFlags ?? []).filter((value, index, values) => values.indexOf(value) === index).join(",") || "none")}" data-gantt-detail="${escape(items.map(item => `${item.label}: ${item.detail}`).join("\n"))}" data-gantt-observation="${escape(observation)}" data-gantt-arguments="${escape(argumentsText)}" data-gantt-expected="${escape(expected)}" data-gantt-subgoal="${escape(subgoal)}" data-gantt-plan="${escape(plan)}" data-gantt-reasoning="${escape(reasoning)}" data-gantt-agent-name="${escape(row.name)}" class="gantt-marker ${items.some(item => item.kind === "rejected") ? "rejected" : ""}${items.some(item => item.activity === "planning") ? " activity-planning" : ""}${items.some(matches) ? "" : " is-filtered-out"}">
        <path d="M ${x(at)} ${y + 88} l 4 4 l -4 4 l -4 -4 Z"/>
        ${items.length > 1 ? `<text x="${x(at) + 6}" y="${y + 95}">${items.length}</text>` : ""}</g>`;
    }).join("");
    return `<g data-gantt-agent="${escape(row.id)}">${row.id === scale.selectedActorId
      ? `<rect class="gantt-selected-lane" x="1" y="${y + 1}" width="${width - 2}" height="${rowHeight - 2}" rx="6"/>` : ""}<line x1="0" x2="${width}" y1="${y + rowHeight}" y2="${y + rowHeight}" class="gantt-gridline"/>${locations}${bars}${dots}</g>`;
  }).join("");
  const usedLanes = new Set<number>();
  const showConnections = scale.showConnections ?? true;
  const links = (showConnections ? chart.links : []).filter(link => link.at >= cutoff && link.at <= chart.now).map((link, index) => {
    const fromRow = chart.rows.find(row => row.id === link.from);
    const toRow = chart.rows.find(row => row.id === link.to);
    const sourceSpan = fromRow?.spans.find(span => span.start === link.fromStart
      && (link.fromEnd === undefined || span.end === link.fromEnd));
    const destinationSpan = toRow?.spans.find(span => span.start === link.toStart
      && (link.toEnd === undefined || span.end === link.toEnd));
    if (!sourceSpan || !destinationSpan) return "";
    const fromY = rowY(link.from, sourceSpan.start === sourceSpan.end);
    const toY = rowY(link.to, destinationSpan.start === destinationSpan.end);
    if (fromY === null || toY === null || fromY === toY) return "";
    const sourceTimed = sourceSpan.end > sourceSpan.start;
    const destinationTimed = destinationSpan.end > destinationSpan.start;
    const sourceWidth = x(sourceSpan.end) - x(sourceSpan.start);
    const destinationWidth = x(destinationSpan.end) - x(destinationSpan.start);
    const sourceX = sourceTimed ? x(sourceSpan.end) - Math.min(10, sourceWidth / 5) : x(sourceSpan.start) + 4;
    const destinationX = destinationTimed
      ? x(destinationSpan.start) + Math.min(10, destinationWidth / 5) : x(destinationSpan.start) - 4;
    const exitX = sourceX + 16;
    const entryX = destinationX - 16;
    const proposedLane = entryX >= exitX ? (exitX + entryX) / 2 : entryX;
    let laneX = Math.round(proposedLane);
    let offset = 0;
    while (usedLanes.has(laneX)) laneX = Math.round(proposedLane + ++offset * 12);
    usedLanes.add(laneX);
    laneX = Math.min(width - 8, Math.max(8, laneX));
    const path = `M ${sourceX} ${fromY} H ${laneX} V ${toY} H ${destinationX}`;
    const hidden = activity !== "all" && !(activity === "dialogue" && link.kind === "dialogue")
      && !(activity === "events" && link.kind === "trade");
    const colour = linkColour(link.from);
    const id = `${link.from}:${link.to}:${link.fromStart ?? link.at}:${link.toStart ?? link.at}:${index}`;
    return `<g class="gantt-link ${link.kind}${hidden ? " is-filtered-out" : ""}" tabindex="0" role="button"
      data-gantt-link="${escape(id)}" aria-pressed="false"
      style="--gantt-link-color:${colour}"
      aria-label="${escape(`${format(link.at)} / ${link.detail}`)}">
      ${tooltip(`${format(link.at)} / ${link.label}: ${link.detail}`)}
      <path d="${path}"/>
      <circle class="gantt-link-start" cx="${sourceX}" cy="${fromY}" r="4"/>
      <circle class="gantt-link-end" cx="${destinationX}" cy="${toY}" r="4"/>
      ${index === 0 || index === chart.links.length - 1 ? `<text x="${laneX + 6}" y="${(fromY + toY) / 2 - 6}">${escape(link.label)}</text>` : ""}
    </g>`;
  }).join("");
  const milestones = new Map<number, Milestone[]>();
  for (const mark of chart.milestones) {
    if (mark.at < cutoff) continue;
    const list = milestones.get(mark.at) ?? [];
    list.push(mark);
    milestones.set(mark.at, list);
  }
  const lines = [...milestones].map(([at, marks], index) => `<g class="gantt-milestone${activity === "all" || activity === "milestones" ? "" : " is-filtered-out"}" tabindex="0" role="img"
    aria-label="${escape(marks.map(mark => `${format(at)} ${mark.detail}`).join("; "))}">
    ${tooltip(marks.map(mark => `${format(at)} / ${mark.label}: ${mark.detail}`).join("\n"))}
    <line x1="${x(at)}" x2="${x(at)}" y1="8" y2="${height - 20}"/>
    <text x="${x(at)}" y="${13 + index % 3 * 13}" text-anchor="middle">${escape(marks.length > 1 ? `${marks.length} events` : marks[0].label)}</text></g>`).join("");
  return `<section class="world-gantt" aria-label="Recorded agent activity timeline">
    <div class="gantt-heading"><div><span class="eyebrow">RECORDED ACTIVITY</span><h2>World timeline</h2></div>
      <div class="gantt-tools"><p>${escape(format(chart.start))} - ${escape(format(chart.now))} / ${escape(state.run?.status ?? "")}</p>
        <div class="gantt-zoom" role="group" aria-label="Timeline timescale">
          <button type="button" class="button subtle" data-timeline-export>Export planning JSON</button>
          <button type="button" class="button subtle" data-timeline-zoom="out" aria-label="Zoom out timescale"${zoom <= 1 ? " disabled" : ""}>-</button>
          <output aria-label="Timeline zoom">${Math.round(zoom * 100)}%</output>
          <button type="button" class="button subtle" data-timeline-zoom="in" aria-label="Zoom in timescale"${zoom >= 8 ? " disabled" : ""}>+</button>
          <button type="button" class="button subtle" data-timeline-zoom="fit">Fit</button>
        </div></div></div>
    ${scale.linkResidents ? `<div class="gantt-filters" aria-label="Timeline filters" role="group">
      <button type="button" class="button subtle" data-timeline-recent aria-pressed="${Boolean(scale.recent)}">${model.turns ? "Full timeline" : "Last simulated hour"}</button>
      <button type="button" class="button subtle" data-timeline-untracked aria-pressed="${Boolean(scale.showUntracked)}">${scale.showUntracked ? "Hide untracked" : "Show untracked"}</button>
      <button type="button" class="button subtle" data-timeline-supervisor aria-pressed="${scale.showSupervisor !== false}">${scale.showSupervisor === false ? "Show Supervisor" : "Hide Supervisor"}</button>
      <button type="button" class="button subtle" data-timeline-connections aria-pressed="${showConnections}">${showConnections ? "Connections on" : "Connections off"}</button>
      <label>Timeline cells <select data-timeline-activity>${([
        ["all", "All cells"], ["actions", "Other actions"], ["movement", "Walking"], ["route", "Routes"],
        ["work", "Work"], ["planning", "Daily planning"], ["dialogue", "Dialogue"], ["meals", "Meals"], ["diary", "Diary entries"],
        ["waiting", "Waiting"], ["sleeping", "Sleeping"], ["free", "Free actions"],
        ["rejected", "Rejected actions"], ["events", "Events"], ["milestones", "Milestones"]
      ] as const).map(([value, label]) =>
        `<option value="${value}"${value === activity ? " selected" : ""}>${label}</option>`).join("")}</select></label>
      <button type="button" class="button subtle" data-timeline-reset>Full timeline</button>
    </div>` : ""}
    <p class="gantt-help">Location colours show recorded positions. ${chart.turns
      ? "Rounds group ordered agent actions. Round-robin episodes contain one action per round; shared-round episodes may contain several sequential actions. Turn 0 is the initial observation. Boxes show recorded actions; red diamonds mark rejected attempts."
      : "Boxes show action time or reserved availability, not model thinking time. Diamonds mark instant or free actions; red diamonds are rejected actions."}
      Arrows connect an NPC's request, chat, or response to the other NPC. Hover or focus for details. Scroll sideways to explore.</p>
    ${activity !== "all" && events.length && !chart.rows.some(row => row.spans.some(item => visible(item) && matches(item)))
      && !(activity === "milestones" && chart.milestones.some(mark => mark.at >= cutoff))
      ? '<p class="gantt-help">No matching activities in this time range.</p>' : ""}
    <div id="gantt-tooltip" class="gantt-tooltip" role="tooltip" hidden></div>
    <div class="gantt-layout">
      <div class="gantt-names" style="padding-top:${top}px">${chart.rows.filter(row => !hiddenRows.has(row.id)).map(row =>
        `<div class="gantt-name-row${row.id === scale.selectedActorId ? " is-selected" : ""}" style="height:${rowHeight}px"><span class="gantt-name-anchor" data-gantt-name="${escape(row.id)}" tabindex="0" aria-label="${escape(`${row.name}, actor details`)}" aria-describedby="gantt-actor-${escape(row.id)}"><strong>${escape(row.name)}</strong><small>${escape(row.occupation)}</small><span id="gantt-actor-${escape(row.id)}" class="actor-details-popover" role="tooltip"><strong>${escape(row.name)}${row.visitor ? " / Visitor" : ""}</strong>${actorDetailsMarkup(state, { ...row, activity: agents.find(agent => agent.id === row.id)?.activity })}</span></span>${scale.linkResidents ? `<button type="button" class="gantt-resident-link" data-resident-select="${escape(row.id)}" aria-label="Highlight ${escape(row.name)}" aria-pressed="${row.id === scale.selectedActorId}">Highlight</button>` : ""}</div>`).join("")}</div>
      <div class="gantt-scroll" tabindex="0" aria-label="Scrollable activity timeline">
        <svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" role="group" aria-label="Activity from ${escape(format(chart.start))} to ${escape(format(chart.now))}">
          ${roundHeaders.join("")}
          <defs>
            <linearGradient id="gantt-tile" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stop-color="#f2faf8"/><stop offset="1" stop-color="#cededb"/>
            </linearGradient>
            <pattern id="gantt-route" width="7" height="7" patternUnits="userSpaceOnUse"><path d="M0 7L7 0" stroke="#87bee2" stroke-width="1"/></pattern>
          </defs>
          ${grid.join("")}${rows}${links}${lines}
          <rect x="${x(chart.now)}" y="${top - 10}" width="${width - x(chart.now)}" height="${height - top - 10}" class="gantt-future"/>
          <line x1="${x(chart.now)}" x2="${x(chart.now)}" y1="4" y2="${height - 20}" class="gantt-now"/>
          <text x="${Math.max(100, x(chart.now) - 5)}" y="48" text-anchor="end" class="gantt-now-label">${escape(state.run?.status === "running" ? "Now" : state.run?.status === "paused" ? "Paused" : state.run?.status === "ready" ? "Ready" : "Stopped")} ${escape(format(chart.now))}</text>
        </svg>
      </div>
    </div>
    <div class="gantt-legend">${[...legend].map(([id, name]) =>
      `<span><i style="background:${locationColour(id)}"></i>${escape(name)}</span>`).join("")}
      ${chart.turns ? "" : '<span><i class="route"></i>Wagon route</span><span><i class="sleep"></i>Sleeping</span>'}<span><i class="travel"></i>Walking</span>
      ${showConnections && chart.links.some(link => link.kind === "supply") ? '<span><i class="link supply"></i>Supply link</span>' : ""}
      ${showConnections && chart.links.some(link => link.kind === "dialogue") ? '<span><i class="link dialogue"></i>Chat / notice</span>' : ""}
      ${showConnections && chart.links.some(link => link.kind === "trade") ? '<span><i class="link trade"></i>Completed trade</span>' : ""}
    </div>
    ${events.length ? "" : '<p class="gantt-help">Waiting for recorded history. Unknown earlier positions are left blank.</p>'}
  </section>`;
}
