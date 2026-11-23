import { z } from "zod";

type RunRate = { id: string; actions: number; elapsed_seconds: number };
const sampleLimit = 20;
const minuteLimit = 30;
const turnLimit = 30;
const storageKey = "npcsim:action-speedometer:v2";
const nonnegative = z.number().nonnegative();
const sample = z.object({ actions: nonnegative, elapsed: nonnegative, gameMinute: nonnegative });
const bucket = z.object({ actions: nonnegative, elapsed: nonnegative });
const savedMeter = z.object({
  id: z.string(),
  history: z.array(sample).min(1).max(sampleLimit),
  minutes: z.array(z.tuple([z.number().int().nonnegative(), bucket])).max(minuteLimit),
  turns: z.array(z.tuple([z.number().int().nonnegative(), bucket])).max(turnLimit),
});
let current: z.infer<typeof savedMeter> | undefined;
let lastStored: string | null | undefined;

function restoreMeter() {
  try {
    const raw = sessionStorage.getItem(storageKey);
    if (raw === lastStored) return;
    lastStored = raw;
    const saved = raw ? savedMeter.safeParse(JSON.parse(raw)) : null;
    if (saved?.success) current = saved.data;
  } catch {
    // Storage may be blocked or invalid; keep sampling in memory.
  }
}

function saveMeter() {
  try {
    const raw = JSON.stringify(current);
    sessionStorage.setItem(storageKey, raw);
    lastStored = raw;
  } catch {
    // Continue in memory if the storage write fails.
  }
}

function addDelta<T extends z.infer<typeof bucket>>(
  values: [number, T][], key: number, delta: { actions: number; elapsed: number }, limit: number,
  factory: (actions: number, elapsed: number) => T,
): [number, T][] {
  const buckets = new Map(values);
  const previous = buckets.get(key);
  buckets.set(key, factory((previous?.actions ?? 0) + delta.actions, (previous?.elapsed ?? 0) + delta.elapsed));
  return [...buckets].filter(([index]) => index > key - limit);
}

function previousBucket(buckets: [number, z.infer<typeof bucket>][], currentIndex: number) {
  return new Map(buckets).get(currentIndex - 1);
}

function fixed(value: number): string {
  return Number.isInteger(value) ? value.toFixed(0) : value.toFixed(1);
}

export function actionSpeedometerMarkup(
  run: RunRate | null | undefined, gameMinute?: number | null,
): string {
  if (!run) return "";
  restoreMeter();
  const turnMinute = gameMinute === null || gameMinute === undefined ? undefined : Math.floor(gameMinute / 5);
  const latestGameMinute = gameMinute ?? 0;
  if (current?.id !== run.id) {
    const completedMinute = Math.max(0, Math.ceil(run.elapsed_seconds / 60) - 1);
    const initialElapsed = Math.max(0, Math.min(run.elapsed_seconds, 60));
    const minuteBuckets: [number, z.infer<typeof bucket>][] = [[completedMinute, {
      actions: run.actions, elapsed: initialElapsed,
    }]];
    const turnBuckets: [number, z.infer<typeof bucket>][] = turnMinute === undefined ? [] : [[
      Math.max(0, Math.ceil(latestGameMinute / 5) - 1), { actions: run.actions, elapsed: run.elapsed_seconds },
    ]];
    current = {
      id: run.id,
      history: [{ actions: run.actions, elapsed: run.elapsed_seconds, gameMinute: latestGameMinute }],
      minutes: minuteBuckets,
      turns: turnBuckets,
    };
    saveMeter();
  } else {
    const previous = current.history[current.history.length - 1];
    const elapsed = run.elapsed_seconds - previous.elapsed;
    const actions = run.actions - previous.actions;
    const gameElapsed = latestGameMinute - previous.gameMinute;
    if (elapsed >= 0 && actions >= 0 && gameElapsed >= 0 && (elapsed > 0 || actions > 0 || gameElapsed > 0)) {
      const minuteKey = Math.max(0, Math.ceil(run.elapsed_seconds / 60) - 1);
      current.minutes = addDelta(current.minutes, minuteKey, { actions, elapsed }, minuteLimit,
        (minuteActions, minuteElapsed) => ({ actions: minuteActions, elapsed: minuteElapsed }));
      if (turnMinute !== undefined) {
        const turnKey = Math.max(0, Math.ceil(latestGameMinute / 5) - 1);
        current.turns = addDelta(current.turns, turnKey, { actions, elapsed }, turnLimit,
          (turnActions, turnElapsed) => ({ actions: turnActions, elapsed: turnElapsed }));
      }
      current.history.push({ actions: run.actions, elapsed: run.elapsed_seconds, gameMinute: latestGameMinute });
      if (current.history.length > sampleLimit) current.history.shift();
      saveMeter();
    }
  }

  const latest = current.history[current.history.length - 1];
  const displayMinute = Math.floor(latest.elapsed / 60);
  const displayTurn = turnMinute === undefined ? undefined : Math.floor(latest.gameMinute / 5);
  const priorMinute = previousBucket(current.minutes, displayMinute);
  const actionsPerMinute = priorMinute?.actions ?? 0;
  const priorTurn = displayTurn === undefined ? undefined : previousBucket(current.turns, displayTurn);
  const actionsPerTurn = priorTurn?.actions;
  const timePerTurn = priorTurn?.elapsed;
  const minutesPerTurn = timePerTurn === undefined ? undefined : timePerTurn / 60;
  const minuteHistory = new Map(current.minutes);
  const minuteRates = Array.from({ length: 30 }, (_, index) =>
    minuteHistory.get(displayMinute - 29 + index)?.actions ?? 0);
  const minuteMax = Math.max(1, ...minuteRates);
  const bars = minuteRates.map((value, index) => {
    const height = Math.max(value > 0 ? 2 : 0, value / minuteMax * 30);
    return `<rect x="${index * 4 + 2}" y="${34 - height}" width="2.5" height="${height}" rx="1"/>
      <title>${29 - index}m ago: ${value.toFixed(1)} actions</title>`;
  }).join("");
  const tone = actionsPerMinute === 0 ? "stalled" : actionsPerMinute < 2 ? "slow" : "moving";
  return `<div class="action-speedometer ${tone}" role="group" aria-label="World throughput for completed periods">
    <div class="speedometer-primary" role="meter" aria-label="Actions processed in the prior real minute"
        aria-valuemin="0" aria-valuenow="${actionsPerMinute.toFixed(1)}">
      <span class="eyebrow">ACTIONS / MIN</span><strong>${fixed(actionsPerMinute)}</strong>
    </div>
    <div class="speedometer-turns">
      <div><span class="eyebrow">ACTIONS / TURN</span><strong>${actionsPerTurn === undefined ? "—" : fixed(actionsPerTurn)}</strong></div>
      <div><span class="eyebrow">MIN / TURN</span><strong>${minutesPerTurn === undefined ? "—" : `${minutesPerTurn.toFixed(1)}m`}</strong></div>
    </div>
    <svg class="speedometer-history" viewBox="0 0 128 38" role="img" aria-label="Actions per completed real minute over the past 30 minutes">
      <line x1="1" y1="35" x2="127" y2="35" class="speedometer-axis"/>${bars}
      <text x="2" y="9">30m</text><text x="113" y="9">now</text>
    </svg>
    <small>prior completed minute · turns are 5 game minutes</small>
  </div>`;
}
