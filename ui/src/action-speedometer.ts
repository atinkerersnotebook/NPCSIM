import { z } from "zod";

type RunRate = { id: string; actions: number; elapsed_seconds: number };
const sampleLimit = 20;
const minuteLimit = 30;
const storageKey = "npcsim:action-speedometer:v1";
const nonnegative = z.number().nonnegative();
const savedMeter = z.object({
  id: z.string(),
  history: z.array(z.object({ actions: nonnegative, elapsed: nonnegative, rate: nonnegative })).min(1).max(sampleLimit),
  buckets: z.array(z.tuple([z.number().int().nonnegative(), nonnegative])).max(minuteLimit),
});
let current: z.infer<typeof savedMeter> | undefined;
let lastStored: string | null | undefined;

function restoreMeter() {
  try {
    const raw = sessionStorage.getItem(storageKey);
    if (raw === lastStored) return;
    lastStored = raw;
    const saved = raw && savedMeter.safeParse(JSON.parse(raw));
    if (saved && saved.success) current = saved.data;
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

export function actionSpeedometerMarkup(run: RunRate | null | undefined): string {
  if (!run) return "";
  restoreMeter();
  const minute = Math.floor(run.elapsed_seconds / 60);
  if (current?.id !== run.id) {
    const rate = run.elapsed_seconds > 0 ? run.actions * 60 / run.elapsed_seconds : 0;
    current = { id: run.id, history: [{ actions: run.actions, elapsed: run.elapsed_seconds, rate }], buckets: [[minute, rate]] };
    saveMeter();
  } else {
    const { history, buckets } = current;
    const previous = history[history.length - 1];
    const elapsed = run.elapsed_seconds - previous.elapsed;
    const actions = run.actions - previous.actions;
    // Ignore duplicate samples and older snapshots from a restored page.
    if (elapsed >= 0 && actions >= 0 && (elapsed > 0 || actions > 0)) {
      history.push({ actions: run.actions, elapsed: run.elapsed_seconds, rate: elapsed > 0 ? actions * 60 / elapsed : 0 });
      if (history.length > sampleLimit) history.shift();
      const minuteHistory = new Map(buckets);
      minuteHistory.set(minute, (minuteHistory.get(minute) ?? 0) + actions);
      current.buckets = [...minuteHistory].filter(([key]) => key > minute - minuteLimit);
      saveMeter();
    }
  }
  const { history, buckets } = current;
  const latest = history[history.length - 1];
  const rate = latest.rate;
  const max = Math.max(6, Math.ceil(Math.max(...history.map(sample => sample.rate)) / 3) * 3);
  const currentMinute = Math.floor(latest.elapsed / 60);
  const minuteHistory = new Map(buckets);
  const minuteRates = Array.from({ length: 30 }, (_, index) =>
    minuteHistory.get(currentMinute - 29 + index) ?? 0);
  const minuteMax = Math.max(1, ...minuteRates);
  const bars = minuteRates.map((value, index) => {
    const height = Math.max(value > 0 ? 2 : 0, value / minuteMax * 30);
    return `<rect x="${index * 4 + 2}" y="${34 - height}" width="2.5" height="${height}" rx="1"/>
      <title>${29 - index}m ago: ${value.toFixed(1)} actions</title>`;
  }).join("");
  const tone = rate === 0 ? "stalled" : rate < 2 ? "slow" : "moving";
  return `<div class="action-speedometer ${tone}" role="meter" aria-label="Actions processed per real minute"
      aria-valuemin="0" aria-valuemax="${max}" aria-valuenow="${rate.toFixed(1)}">
    <div><span class="eyebrow">ACTIONS / MIN</span><strong>${rate.toFixed(1)}</strong></div>
    <svg class="speedometer-history" viewBox="0 0 128 38" role="img" aria-label="Actions per minute over the past 30 real minutes">
      <line x1="1" y1="35" x2="127" y2="35" class="speedometer-axis"/>${bars}
      <text x="2" y="9">30m</text><text x="113" y="9">now</text>
    </svg>
    <small>${history.length > 1 ? "rolling throughput · past 30m" : "episode average · collecting history"}</small>
  </div>`;
}
