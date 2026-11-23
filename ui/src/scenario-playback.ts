import { z } from "zod";
import { villageClockTime } from "./view-mode.ts";

const checkpointsSchema = z.object({ checkpoints: z.array(z.object({
  sequence: z.number().int().nonnegative(), revision: z.number().int().nonnegative(),
  minute: z.number().int().nonnegative().nullable(), turn: z.number().int().nonnegative().optional(),
  turn_start: z.boolean().optional()
})) });

export interface ScenarioPlaybackOptions {
  snapshotPath: (sequence: number) => string;
  onSnapshot: (payload: unknown, live: boolean) => void;
  onError: (message: string) => void;
}

export function mountScenarioPlayback(host: HTMLElement, options: ScenarioPlaybackOptions) {
  host.innerHTML = `<div class="supply-playback" aria-label="Scenario playback" hidden>
    <span class="eyebrow supply-playback-title">PLAYBACK</span>
    <div class="supply-playback-buttons">
      <button data-playback="start" class="button subtle" type="button" aria-label="First checkpoint">⏮</button>
      <button data-playback="turn-back" class="button subtle" type="button" aria-label="Previous turn">− Turn</button>
      <button data-playback="back" class="button subtle" type="button" aria-label="Previous checkpoint">◀</button>
      <button data-playback="toggle" class="button primary" type="button" aria-label="Play playback">▶ Play</button>
      <button data-playback="next" class="button subtle" type="button" aria-label="Next checkpoint">▶</button>
      <button data-playback="turn-next" class="button subtle" type="button" aria-label="Next turn">Turn +</button>
      <button data-playback="live" class="button subtle" type="button" aria-label="Return to live state">Live</button>
    </div>
    <label class="supply-playback-seek"><span>Checkpoint</span><input data-playback="seek" type="range" min="0" max="0" value="0" step="1" aria-label="Playback checkpoint" />
      <output data-playback="position">Live</output></label>
    <label class="supply-playback-speed">Speed <select data-playback="speed" aria-label="Playback speed">
      <option value="0.5">0.5×</option><option value="1" selected>1×</option><option value="2">2×</option><option value="4">4×</option>
    </select></label>
  </div>`;
  const root = host.querySelector<HTMLElement>(".supply-playback")!;
  const button = (name: string) => root.querySelector<HTMLButtonElement>(`[data-playback="${name}"]`)!;
  const seek = root.querySelector<HTMLInputElement>('[data-playback="seek"]')!;
  const position = root.querySelector<HTMLOutputElement>('[data-playback="position"]')!;
  const speed = root.querySelector<HTMLSelectElement>('[data-playback="speed"]')!;
  const start = button("start");
  const turnBack = button("turn-back");
  const back = button("back");
  const toggle = button("toggle");
  const next = button("next");
  const turnNext = button("turn-next");
  const live = button("live");
  let checkpoints: z.infer<typeof checkpointsSchema>["checkpoints"] = [];
  let index: number | null = null;
  let running = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let loadId = 0;
  const update = () => {
    root.hidden = checkpoints.length === 0;
    seek.max = String(Math.max(0, checkpoints.length - 1));
    seek.value = String(index ?? Math.max(0, checkpoints.length - 1));
    seek.disabled = checkpoints.length === 0;
    const cursor = index ?? checkpoints.length - 1;
    const currentTurn = checkpoints[cursor]?.turn;
    const hasPreviousTurn = checkpoints.some((item, position) => position < cursor && item.turn !== currentTurn);
    const hasNextTurn = checkpoints.some((item, position) => position > cursor && item.turn !== currentTurn);
    start.disabled = back.disabled = index === 0 || checkpoints.length === 0;
    turnBack.disabled = !hasPreviousTurn;
    next.disabled = toggle.disabled = checkpoints.length === 0 || index === checkpoints.length - 1;
    turnNext.disabled = !hasNextTurn;
    live.disabled = index === null;
    if (index === null) position.textContent = "Live";
    else {
      const checkpoint = checkpoints[index];
      position.textContent = `${index + 1} / ${checkpoints.length} · ${checkpoint.minute === null
        ? `revision ${checkpoint.revision}` : villageClockTime(checkpoint.minute)}${checkpoint.turn === undefined ? "" : ` · turn ${checkpoint.turn}`}`;
    }
  };
  const stop = () => {
    running = false;
    clearTimeout(timer);
    timer = undefined;
    toggle.textContent = "▶ Play";
    toggle.setAttribute("aria-label", "Play playback");
  };
  const load = async (requested: number) => {
    if (!checkpoints.length) return;
    const selected = Math.max(0, Math.min(requested, checkpoints.length - 1));
    const request = ++loadId;
    try {
      const response = await fetch(options.snapshotPath(checkpoints[selected].sequence));
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload: unknown = await response.json();
      if (request !== loadId) return;
      index = selected;
      options.onSnapshot(payload, false);
      update();
    } catch (error) {
      stop();
      options.onError(`Could not load playback checkpoint: ${error instanceof Error ? error.message : String(error)}`);
    }
  };
  const schedule = async () => {
    stop();
    running = true;
    toggle.textContent = "Ⅱ Pause";
    toggle.setAttribute("aria-label", "Pause playback");
    if (index === null) await load(0);
    if (!running) return;
    const advance = async () => {
      if (index === null || index >= checkpoints.length - 1) { stop(); update(); return; }
      await load(index + 1);
      if (running) timer = setTimeout(() => void advance(), 1000 / Number(speed.value));
    };
    timer = setTimeout(() => void advance(), 1000 / Number(speed.value));
  };
  start.addEventListener("click", () => { stop(); void load(0); });
  turnBack.addEventListener("click", () => {
    stop();
    const cursor = index ?? checkpoints.length - 1;
    const currentTurn = checkpoints[cursor]?.turn;
    for (let position = cursor - 1; position >= 0; position--) {
      if (checkpoints[position].turn !== currentTurn) {
        const targetTurn = checkpoints[position].turn;
        void load(checkpoints.findIndex(item => item.turn === targetTurn));
        break;
      }
    }
  });
  back.addEventListener("click", () => { stop(); void load((index ?? checkpoints.length) - 1); });
  next.addEventListener("click", () => { stop(); void load((index ?? -1) + 1); });
  turnNext.addEventListener("click", () => {
    stop();
    const cursor = index ?? -1;
    const currentTurn = checkpoints[cursor]?.turn ?? checkpoints[0]?.turn;
    const target = checkpoints.findIndex((item, position) => position > cursor && item.turn !== currentTurn);
    if (target >= 0) void load(target);
  });
  toggle.addEventListener("click", () => running ? stop() : void schedule());
  live.addEventListener("click", () => {
    ++loadId;
    stop();
    index = null;
    options.onSnapshot(null, true);
    update();
  });
  seek.addEventListener("input", () => { stop(); void load(Number(seek.value)); });
  speed.addEventListener("change", () => { if (running) void schedule(); });
  return {
    element: root,
    get active() { return index !== null; },
    async refresh(signal?: AbortSignal) {
      const response = await fetch("/api/town-supply/playback", { signal });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      checkpoints = checkpointsSchema.parse(await response.json()).checkpoints;
      update();
    },
    destroy: stop
  };
}
