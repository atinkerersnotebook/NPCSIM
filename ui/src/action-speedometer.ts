type RunRate = { id: string; actions: number; elapsed_seconds: number; status?: string };
type RateSample = { actions: number; elapsed: number; rate: number };
const histories = new Map<string, RateSample[]>();
const buckets = new Map<string, Map<number, number>>();

export function actionSpeedometerMarkup(run: RunRate | null | undefined): string {
  if (!run) return "";
  const history = histories.get(run.id) ?? [];
  const previous = history.at(-1);
  if (!previous) {
    const rate = run.elapsed_seconds > 0 ? run.actions * 60 / run.elapsed_seconds : 0;
    history.push({ actions: run.actions, elapsed: run.elapsed_seconds, rate });
    buckets.set(run.id, new Map([[Math.floor(run.elapsed_seconds / 60), rate]]));
  } else if (run.actions !== previous.actions || run.elapsed_seconds !== previous.elapsed) {
    const elapsed = Math.max(0, run.elapsed_seconds - previous.elapsed);
    const actions = Math.max(0, run.actions - previous.actions);
    if (elapsed > 0 && actions > 0) history.push({ actions: run.actions, elapsed: run.elapsed_seconds, rate: actions * 60 / elapsed });
    else history.push({ actions: run.actions, elapsed: run.elapsed_seconds, rate: 0 });
    const minute = Math.floor(run.elapsed_seconds / 60);
    const minuteHistory = buckets.get(run.id) ?? new Map<number, number>();
    minuteHistory.set(minute, (minuteHistory.get(minute) ?? 0) + actions);
    for (const key of [...minuteHistory.keys()]) if (key < minute - 29) minuteHistory.delete(key);
    buckets.set(run.id, minuteHistory);
  }
  while (history.length > 20) history.shift();
  histories.set(run.id, history);
  const rate = history.at(-1)?.rate ?? 0;
  const max = Math.max(6, Math.ceil(Math.max(...history.map(sample => sample.rate), rate) / 3) * 3);
  const currentMinute = Math.floor(run.elapsed_seconds / 60);
  const minuteHistory = buckets.get(run.id) ?? new Map<number, number>();
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
