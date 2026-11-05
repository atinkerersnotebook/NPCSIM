import type { z } from "zod";
import type { breadBatchSchema, clockSchema } from "./contracts";

type Batch = z.infer<typeof breadBatchSchema>;
type Clock = z.infer<typeof clockSchema>;

export function clockHelp(clock: Clock): string {
  const rate = `While the clock runs, 1 real second = ${clock.rate} bakery seconds. A two-hour rise takes ${(7200 / clock.rate).toFixed(1)} real clock-running seconds.`;
  return clock.mode === "decision_paused" ?
    `${rate} Decisions and controller processing freeze bakery time. Actions cost fixed simulated time; waiting runs the clock. Pause freezes everything.` :
    `${rate} Continuous challenge: the clock also runs during model calls. Faster speeds shorten real decision windows. Pause freezes everything.`;
}

export function clockTime(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds));
  return [Math.floor(whole / 3600), Math.floor(whole / 60) % 60, whole % 60]
    .map(value => String(value).padStart(2, "0")).join(":");
}

export function batchTiming(batch: Batch, now: number): string {
  if (batch.failure) return batch.failure;
  if (batch.stage === "served") return "Served on display";
  if (batch.ready_at !== null && now < batch.ready_at) {
    return `Ready in ${clockTime(batch.ready_at - now)}`;
  }
  if (batch.deadline !== null) {
    return now > batch.deadline ? "Handling deadline missed" :
      `READY - handle within ${clockTime(batch.deadline - now)}`;
  }
  return batch.ready_at !== null ? "READY for next task" : `${batch.ingredients.length}/4 ingredients measured`;
}
