import { z } from "zod";
import { observationSchema } from "./contracts.ts";
import type { RunEvent } from "./contracts";
import { nutritionLabel } from "./world-view.ts";

export interface StoryEntry {
  id: string;
  actorId: string;
  cycle: number;
  kind: "observation" | "intent" | "result" | "ending";
  text: string;
  explanation?: string;
  details?: string[];
}

export type StoryMembers = Readonly<Record<string, { readonly name: string }>>;

function memberNames(text: string, members: StoryMembers): string {
  const ids = Object.keys(members);
  if (!ids.length) return text;
  const names = new Map(ids.map(id => [id.toLowerCase(), members[id].name]));
  const pattern = ids.map(id => id.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|");
  return text.replace(new RegExp(`(?<![A-Za-z0-9_-])(?:${pattern})(?![A-Za-z0-9_-])`, "gi"),
    id => names.get(id.toLowerCase()) ?? id);
}

function actorName(actor: string, members: StoryMembers): string {
  return members[actor]?.name ?? actor.replace(/[-_]/g, " ").replace(/\b\w/g, letter => letter.toUpperCase());
}

export async function copyStory(
  paragraphs: readonly { text: string }[], clipboard: Pick<Clipboard, "writeText"> | undefined
): Promise<void> {
  if (!paragraphs.length) throw new Error("There is no final story to copy.");
  if (!clipboard) throw new Error("Clipboard access is unavailable. Open the dashboard on localhost or HTTPS.");
  await clipboard.writeText(paragraphs.map(paragraph => paragraph.text).join("\n\n"));
}

const objectSchema = z.record(z.string(), z.unknown());
const requestSchema = z.object({
  request_id: z.string(), action_id: z.string(), arguments: objectSchema
});
const resultSchema = z.object({
  request_id: z.string(), outcome: z.string(), explanation: z.string(),
  observation: observationSchema
});
const decisionSchema = z.object({ action_id: z.string(), justification: z.string() });

function firstPerson(text: string): string {
  return text.replace(/\bYou are\b/g, "I am").replace(/\bYou\b/g, "I")
    .replace(/\byou are\b/g, "I am").replace(/\byou\b/g, "I")
    .replace(/\bYour\b/g, "My").replace(/\byour\b/g, "my");
}

function namedVoice(text: string, name: string): string {
  const verbs = new Map<string, string>([["am", "is"], ["have", "has"], ["do", "does"], ["go", "goes"]]);
  for (const verb of ["try", "carry", "fly"]) verbs.set(verb, `${verb.slice(0, -1)}ies`);
  for (const verb of [
    "take", "look", "open", "drop", "unlock", "hit", "read", "slice",
    "dice", "spread", "assemble", "serve", "wait", "measure", "dig",
    "learn", "discover", "notice", "need", "observe", "pick", "put",
    "use", "give", "say", "swim", "move", "recover", "speak", "arrive",
    "remain", "mark", "place", "plug", "unplug", "turn", "add",
    "grind", "cook", "recruit", "sail", "want", "know", "see",
    "walk", "cross", "anchor", "repair", "prepare", "secure", "leave"
  ]) verbs.set(verb, `${verb}s`);
  return text.replace(/\bI['’](m|ve|ll|d)\b/g, (_, ending: string) =>
    `I ${{m: "am", ve: "have", ll: "will", d: "would"}[ending]}`)
    .replace(/\bI (don't|can't|won't)\b/g, (_, verb: string) =>
      `I ${{"don't": "do not", "can't": "cannot", "won't": "will not"}[verb]}`)
    .replace(/\bI ([a-z]+)\b/g, (_, verb: string) => `${name} ${verbs.get(verb) ?? verb}`)
    .replace(/\bI\b|\bme\b/g, () => name)
    .replace(/\b[Mm]y\b|\bmine\b/g, () => `${name}'s`)
    .replace(/\bmyself\b/g, () => name)
    .replace(new RegExp(`(^|[.!?]\\s+)${name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\b`, "gi"),
      (_, prefix: string) => prefix + name);
}

export function isTeamStory(events: readonly RunEvent[]): boolean {
  return events.some(event => event.kind === "observation" &&
    typeof event.payload === "object" && event.payload !== null && "role" in event.payload);
}

function list(names: string[]): string {
  return names.length < 2 ? (names[0] ?? "") :
    `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

function readable(text: string, names: Map<string, string>): string {
  return text.replace(/\b(You use|I use|\w+ uses) ([\w-]+): (?:Use [^.!?]+? to )?([A-Z])/g,
    (_, subject: string, capability: string, first: string) => `${subject} ${capability} to ${first.toLowerCase()}`)
    .replace(/`/g, "")
    .replace(/\b[A-Za-z][A-Za-z0-9]*(?:[-_][A-Za-z0-9]+)+\b/g, token =>
      names.get(token)?.toLowerCase() ??
      (token === "water_engineering" ? "underwater engineering" : token.replace(/_/g, " ")));
}

function intent(action: string, arguments_: Record<string, unknown>, names: Map<string, string>,
  challenges: Map<string, string>): string {
  const name = (value: unknown) => typeof value === "string" ? `the ${(names.get(value) ?? value).toLowerCase()}` : "";
  const target = name(arguments_.target);
  const item = name(arguments_.item);
  const description = challenges.get(action);
  if (description) return `I try to ${description[0].toLowerCase()}${description.slice(1).replace(/\.$/, "")}.`;
  const settings = Object.entries(arguments_).filter(([key, value]) =>
    key !== "target" && ["string", "number", "boolean"].includes(typeof value))
    .map(([key, value]) => `${key.replace(/_/g, " ")} set to ${String(value)}`);
  switch (action) {
    case "look": return target ? `I take a closer look at ${target}.` : "I look around.";
    case "open": return `I try opening ${target}.`;
    case "search": return `I try searching ${target} for concealed features.`;
    case "take": return `I try to pick up ${target}.`;
    case "drop": return `I try to put down ${target}.`;
    case "unlock": return `I try unlocking ${target} with ${item}.`;
    case "hit": return `I try hitting ${target} with ${item}.`;
    case "go": return `I try going through ${target}.`;
    case "read": return `I try reading ${target}.`;
    case "slice": return `I try slicing ${target} with ${item}.`;
    case "dice": return `I try dicing ${target} with ${item}.`;
    case "spread": return `I try spreading ${name(arguments_.ingredient)} on ${target} with ${item}.`;
    case "assemble": return `I try putting ${name(arguments_.first)} and ${name(arguments_.second)} together.`;
    case "serve": return `I try serving ${target} on ${name(arguments_.surface)}.`;
    case "teleport": return "I try speaking the journal's travel word.";
    case "cast_unlock": return `I try casting Unlock on ${target}.`;
    case "cast_shrink": return "I try casting Shrink on myself and my equipment.";
    case "cast_restore_size": return "I try restoring my original size.";
    case "recruit": return `I try recruiting ${target} with ${item}.`;
    case "sail": return "I try sailing to the other island.";
    case "wait_tide": return "I wait safely for the next tide.";
    case "play_rps": return `I try committing ${String(arguments_.move)} privately for this round.`;
    case "play_tic_tac_toe": return `I try placing my mark in cell ${String(arguments_.cell)}.`;
    case "erp_terminal": return `I enter "${String(arguments_.command)}" in the ERP terminal.`;
    case "adjust_bbq_heat": return `I adjust the BBQ heat dial to ${String(arguments_.heat_percent)}% and watch the temperature gauge.`;
    case "monitor_bbq": return "I watch the BBQ temperature gauge without changing the heat.";
    case "pace": return `I measure ${String(arguments_.count)} paces at ${target}.`;
    case "dig": return `I try digging at ${target} with ${item}.`;
    case "add": return `I try adding ${name(arguments_.ingredient)} to ${target} with ${item}.`;
    case "grind": return `I try grinding ${target} with ${name(arguments_.machine)}.`;
    case "cook": return `I try cooking ${target} in ${name(arguments_.pan)} on ${name(arguments_.stove)}.`;
    case "plug": return `I try plugging ${target} into ${name(arguments_.outlet)}.`;
    case "unplug": return `I try unplugging ${target}.`;
    case "turn_on": return `I try switching on ${target}.`;
    case "turn_off": return `I try switching off ${target}.`;
    case "place": return `I try placing ${target} on ${name(arguments_.surface)}.`;
    case "give": return `I try handing ${target} to ${String(arguments_.recipient ?? "a teammate")
      .replace(/[-_]/g, " ").replace(/\b\w/g, letter => letter.toUpperCase())}.`;
    case "say": return `I say, "${String(arguments_.message ?? "")}"`;
    case "wait": return "I wait for the work in progress.";
    default: return `I try to ${action.replace(/[-_]/g, " ")}${target ? ` at ${target}` : ""}${settings.length ? ` with ${list(settings)}` : ""}.`;
  }
}

function teamOrder<T extends { id: string; text: string }>(entries: T[], events: readonly RunEvent[]): T[] {
  const source = new Map(events.map(event => [event.id, event]));
  const openings = new Set<string>();
  return entries.sort((a, b) => (source.get(a.id)?.sequence ?? Infinity) - (source.get(b.id)?.sequence ?? Infinity))
    .filter(entry => {
      const event = source.get(entry.id);
      if (event?.kind !== "observation") return true;
      const payload = objectSchema.parse(event.payload);
      if (payload.opening !== true) return true;
      const opening = String(payload.narrative);
      if (openings.has(opening)) return false;
      openings.add(opening);
      return true;
    });
}

export function buildStory(events: readonly RunEvent[], actorId?: string, members: StoryMembers = {}): StoryEntry[] {
  const team = isTeamStory(events);
  if (team && !actorId) {
    const actors = new Set(events.map(event => event.actor_id));
    return teamOrder([...actors].flatMap(actor => buildStory(events, actor, members)), events);
  }
  const story: StoryEntry[] = [];
  const seen = new Set<string>();
  const names = new Map<string, string>();
  const challenges = new Map<string, string>();
  const descriptions = new Map<string, string>();
  const nutritionLabels = new Map<string, string>();
  const preparations = new Map<string, string>();
  const equipmentStates = new Map<string, string>();
  const ingredientLabels = new Map<string, string>();
  const decisions = new Map<number, z.infer<typeof decisionSchema>>();
  let locationId: string | undefined;
  let observed = false;

  function learn(value: z.infer<typeof observationSchema>): { discoveries: string[]; details: string[] } {
    const discoveries: string[] = [];
    const details: string[] = [];
    for (const challenge of value.skill_challenges ?? []) challenges.set(challenge.id, challenge.description);
    for (const entity of value.visible_entities) {
      const name = typeof entity.name === "string" ? entity.name : entity.id;
      if (!names.has(entity.id)) discoveries.push(`the ${name.toLowerCase()}`);
      names.set(entity.id, name);
      if (descriptions.get(entity.id) !== entity.description) details.push(firstPerson(entity.description));
      descriptions.set(entity.id, entity.description);
      if (entity.power !== undefined) {
        const powerState = JSON.stringify([entity.power, entity.plugged_into]);
        if (equipmentStates.get(entity.id) !== powerState) {
          const connection = entity.plugged_into == null ? "unplugged" :
            `connected to the ${(names.get(entity.plugged_into) ?? entity.plugged_into).toLowerCase()}`;
          details.push(`The ${name.toLowerCase()} is switched ${entity.power} and ${connection}.`);
        }
        equipmentStates.set(entity.id, powerState);
      }
      if (entity.ingredients?.length) {
        const label = entity.ingredients.join(", ");
        if (ingredientLabels.get(entity.id) !== label) details.push(`The ${name.toLowerCase()} contains ${label}.`);
        ingredientLabels.set(entity.id, label);
      }
      if (entity.food) {
        const label = nutritionLabel(entity.nutrition);
        if (nutritionLabels.get(entity.id) !== label) details.push(`The ${name.toLowerCase()}'s nutrition is ${label.toLowerCase()}.`);
        nutritionLabels.set(entity.id, label);
      }
      if (entity.preparation && preparations.get(entity.id) !== JSON.stringify(entity.preparation)) {
        for (const [key, value] of Object.entries(entity.preparation)) {
          if (key === "cut") details.push(`The ${name.toLowerCase()} is ${value}.`);
          else if (key === "cooking") details.push(`The ${name.toLowerCase()} is ${value}.`);
          else if (key === "topping") details.push(value === "plain" ? `The ${name.toLowerCase()} has no topping.` :
            value === "filled" ? `The ${name.toLowerCase()} has a spread.` :
            `The ${name.toLowerCase()} is coated with ${value.replaceAll("-", " ")}.`);
        }
        preparations.set(entity.id, JSON.stringify(entity.preparation));
      }
    }
    return { discoveries, details };
  }

  for (const event of [...events].sort((a, b) => a.sequence - b.sequence)) {
    if (seen.has(event.id) || (actorId && event.actor_id !== actorId)) continue;
    seen.add(event.id);
    const entry = (kind: StoryEntry["kind"], text: string, explanation?: string, prepend = false, details: string[] = []) => {
      text = readable(text, names);
      explanation = explanation ? readable(explanation, names) : undefined;
      details = details.map(detail => readable(detail, names));
      if (team) {
        const name = actorName(event.actor_id, members);
        text = namedVoice(text, name);
        explanation = explanation ? namedVoice(firstPerson(explanation), name) : undefined;
        details = details.map(detail => namedVoice(detail, name));
      }
      text = memberNames(text, members);
      explanation = explanation ? memberNames(explanation, members) : undefined;
      details = details.map(detail => memberNames(detail, members));
      const value = { id: event.id, actorId: event.actor_id, cycle: event.cycle, kind, text, ...(explanation ? { explanation } : {}),
        ...(details.length ? { details } : {}) };
      if (prepend) story.unshift(value);
      else story.push(value);
    };
    const payload = objectSchema.parse(event.payload);
    if (event.kind === "observation") {
      const value = observationSchema.parse(payload);
      const { discoveries, details } = learn(value);
      if (!observed) {
        const rule = value.self.body.carrying.rule;
        if (rule === "item_count" || rule === "inventory_units") {
          const limit = value.self.body.carrying.limit;
          const noun = rule === "inventory_units" ? `inventory ${limit === 1 ? "unit" : "units"}` : limit === 1 ? "item" : "items";
          details.unshift(`I can carry at most ${limit} ${noun} at a time.`);
        }
        const opening = value.opening === true;
        entry("observation", `${firstPerson(value.narrative)}${!opening && discoveries.length ? ` I can see ${list(discoveries)}.` : ""}`,
          undefined, opening, opening ? [] : details);
        observed = true;
      } else if (value.self.location_id !== locationId || discoveries.length || details.length) {
        const text = `${value.self.location_id !== locationId ? `${firstPerson(value.narrative)} ` : ""}${discoveries.length ? `I discover ${list(discoveries)}.` : ""}`.trim();
        entry("observation", text || "I notice a change in what I can see.", undefined, false, details);
      }
      locationId = value.self.location_id;
    } else if (event.kind === "started") {
      const goal = z.object({ goal: z.object({ objective: z.string(), revealed: z.boolean().optional() }) }).parse(payload).goal;
      entry("observation", goal.revealed === false ? "I do not yet know my assigned task. I need to explore and find instructions." :
        `${team ? "Our" : "My"} goal is to ${goal.objective[0].toLowerCase()}${goal.objective.slice(1)}`);
    } else if (event.kind === "goal_discovered") {
      const value = z.object({ goal: z.object({ objective: z.string().min(1) }), source_id: z.string() }).parse(payload);
      entry("observation", `After reading the ${(names.get(value.source_id) ?? value.source_id).toLowerCase()}, I learn my task: ${value.goal.objective}`);
    } else if (event.kind === "decision") {
      decisions.set(event.cycle, decisionSchema.parse(payload));
    } else if (event.kind === "action_request") {
      const request = requestSchema.parse(payload);
      const decision = decisions.get(event.cycle);
      entry("intent", intent(request.action_id, request.arguments, names, challenges),
        decision?.action_id === request.action_id ? decision.justification : undefined);
    } else if (event.kind === "action_result") {
      const result = resultSchema.parse(payload);
      const { discoveries, details } = learn(result.observation);
      const moved = result.observation.self.location_id !== locationId;
      entry("result", `${firstPerson(result.explanation)}${moved ? ` ${firstPerson(result.observation.narrative)}` : ""}${discoveries.length ? ` I discover ${list(discoveries)}.` : ""}`, undefined, false, details);
      locationId = result.observation.self.location_id;
    } else if (event.kind === "error") {
      entry("ending", `My attempt was interrupted. ${String(payload.message)}`);
    } else if (event.kind === "finished") {
      if (payload.status === "succeeded") entry("ending", team ?
        "The world confirms that we have achieved our goal." : "The world confirms that I have achieved my goal.");
      else if (payload.status === "blocked") entry("ending", `I cannot complete my goal. ${String(payload.reason)}`);
      else entry("ending", `My attempt has ended. ${String(payload.reason)}`);
    }
  }
  return story;
}

function confirmedRequests(events: readonly RunEvent[]): Map<string, string> {
  const requests = new Map<string, string>();
  const confirmed = new Map<string, string>();
  for (const event of [...events].sort((a, b) => a.sequence - b.sequence)) {
    if (event.kind !== "action_request" && event.kind !== "action_result") continue;
    const payload = objectSchema.parse(event.payload);
    if (typeof payload.request_id !== "string") continue;
    const key = `${event.actor_id}:${payload.request_id}`;
    if (event.kind === "action_request") requests.set(key, event.id);
    else if (payload.outcome === "APPLIED") {
      const request = requests.get(key);
      if (request) confirmed.set(request, event.id);
    }
  }
  return confirmed;
}

export function buildNarrative(events: readonly RunEvent[], actorId?: string,
  reviewedStory?: readonly { id: string; text: string }[], members: StoryMembers = {}): { id: string; text: string }[] {
  const confirmed = confirmedRequests(events);
  const confirmedResults = new Set(confirmed.values());
  if (reviewedStory?.length) {
    const team = isTeamStory(events);
    const actors = new Map(events.map(event => [event.id, event.actor_id]));
    const intents = new Map<string, string>();
    const namesByActor = new Map<string, Map<string, string>>();
    for (const actor of new Set(actors.values())) {
      for (const entry of buildStory(events, actor, members)) {
        if (entry.kind === "intent") intents.set(entry.id, entry.text);
      }
      const names = new Map<string, string>();
      for (const event of events) {
        if (event.actor_id !== actor) continue;
        const payload = objectSchema.parse(event.payload);
        const observed = event.kind === "observation" ? payload :
          event.kind === "action_result" ? payload.observation : undefined;
        if (!observed) continue;
        for (const entity of observationSchema.parse(observed).visible_entities) {
          if (typeof entity.name === "string") names.set(entity.id, entity.name);
        }
      }
      namesByActor.set(actor, names);
    }
    const rendered = reviewedStory.map(paragraph => {
      let text = paragraph.text.replace(/\bThe [^.!?\r\n]+ takes \d+ inventory units?\.\s*/g, "").trim();
      const actor = actors.get(paragraph.id);
      if (!actor) return { ...paragraph, text: memberNames(text, members) };
      const name = actorName(actor, members);
      text = text.startsWith(`${actor}: `) ? text.slice(actor.length + 2) : text;
      const attempt = intents.get(paragraph.id);
      if (attempt) text = text.replace(/\b(?:I try|\w+ tries) "[^"]+"(?: with \{[^{}]*\})?\./g, () => attempt);
      text = readable(text, namesByActor.get(actor) ?? new Map<string, string>());
      text = team ? namedVoice(text.replace(
        "The world confirms that I have achieved my goal.",
        "The world confirms that we have achieved our goal."), name) : text;
      if (confirmed.has(paragraph.id)) {
        if (attempt) text = text.replace(attempt, "").replace(/ {2,}/g, " ").trim();
        text = text.replace("I pick up ", "I picked up ")
          .replace(`${name} picks up `, `${name} picked up `);
      }
      return { id: paragraph.id, text: memberNames(text, members) };
    }).filter(paragraph => paragraph.text.length > 0);
    return team && !actorId ? teamOrder(rendered, events) : rendered;
  }
  const paragraphs: { id: string; text: string }[] = [];
  let pending: { cycle: number; actorId: string; explanation: string; paragraph: { id: string; text: string } } | undefined;
  for (const entry of buildStory(events, actorId, members)) {
    const details = entry.details?.length ? ` ${entry.details.join(" ")}` : "";
    if (entry.kind === "result" && pending && pending.actorId === entry.actorId &&
        (pending.cycle === entry.cycle || confirmed.get(pending.paragraph.id) === entry.id) &&
        paragraphs.at(-1) === pending.paragraph) {
      if (confirmed.get(pending.paragraph.id) === entry.id) {
        const name = actorName(entry.actorId, members);
        const text = entry.text.replace("I pick up ", "I picked up ")
          .replace(`${name} picks up `, `${name} picked up `);
        pending.paragraph.text = `${pending.explanation}${text}${details}`.trim();
      } else {
        pending.paragraph.text += ` ${entry.text}${details}`;
      }
      pending = undefined;
      continue;
    }
    const reason = entry.explanation?.trim();
    const explanation = reason ? `${firstPerson(reason)}${/[.!?]$/.test(reason) ? "" : "."} ` : "";
    const text = confirmedResults.has(entry.id) ? entry.text.replace("I pick up ", "I picked up ") : entry.text;
    const paragraph = { id: entry.id, text: `${explanation}${text}${details}`.trim() };
    paragraphs.push(paragraph);
    pending = entry.kind === "intent" ? { cycle: entry.cycle, actorId: entry.actorId, explanation, paragraph } : undefined;
  }
  return paragraphs;
}
