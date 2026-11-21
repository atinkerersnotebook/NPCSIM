import type { State } from "./contracts.ts";
import { escape } from "./html.ts";
import { villageClockTime } from "./view-mode.ts";

const dismissedAcknowledgements = new Set<string>();

export function renderDungeonAppeal(state: State | null, onError: (error: unknown) => void = console.error): void {
  const shell = document.querySelector<HTMLElement>(".command-shell");
  const main = document.querySelector<HTMLElement>("main.dashboard-page");
  const village = state?.world?.village;
  const pending = village?.dungeon_appeals?.filter(appeal => appeal.status === "pending"
    && appeal.expires_at > (village?.minute ?? 0)) ?? [];
  const recent = village?.dungeon_appeals?.filter(appeal => !dismissedAcknowledgements.has(appeal.id)
    && appeal.npc_acknowledgement
    && (appeal.acknowledgement_expires_at ?? appeal.created_at + 5) > (village?.minute ?? 0)).slice(-3) ?? [];
  let strip = document.querySelector<HTMLElement>("#dungeon-appeal-strip");
  if (!shell && !main || (!pending.length && !recent.length) || !state?.run) {
    strip?.remove();
    return;
  }
  if (!strip) {
    strip = document.createElement("section");
    strip.id = "dungeon-appeal-strip";
    strip.className = "dungeon-appeal-strip";
    strip.setAttribute("aria-label", "Dungeon Master requests");
    (shell ?? main!).after(strip);
  }
  const signature = [...pending, ...recent].map(item =>
    JSON.stringify([item.id, item.status, item.response, item.npc_acknowledgement])).join("|");
  if (strip.dataset.signature === signature) return;
  strip.dataset.signature = signature;
  const runId = state.run.id;
  strip.innerHTML = `<div class="dungeon-appeal-strip-title"><strong>Dungeon Master requests</strong><span>${pending.length} pending${recent.length ? ` · ${recent.length} recent` : ""}</span></div>
    ${pending.map(appeal => {
      const actor = state.run?.agent_profiles?.[appeal.actor]?.name ?? appeal.actor;
      return `<article class="dungeon-appeal-row" data-appeal-id="${escape(appeal.id)}">
        <div><span class="tag amber">${escape(appeal.category.toUpperCase())}</span><strong>${escape(actor)}</strong>
          <p>${escape(appeal.request)}</p><small>Expires at ${villageClockTime(appeal.expires_at)} · no response means continue normally</small></div>
        <label>Reply <input id="dm-reply-${escape(appeal.id)}" type="text" maxlength="1000" placeholder="Optional; required when replying"></label>
        <div class="button-row"><button type="button" class="button" data-dm-appeal="answer">Reply</button>
        <button type="button" class="button subtle" data-dm-appeal="decline">Decline</button></div>
      </article>`;
    }).join("")}
    ${recent.map(appeal => {
      const actor = state.run?.agent_profiles?.[appeal.actor]?.name ?? appeal.actor;
      const change = appeal.world_changes?.map(item => `Added ${item.quantity} ${item.product} to ${item.location}`).join("; ") ?? "";
      return `<article class="dungeon-appeal-row dungeon-appeal-receipt" data-appeal-receipt="${escape(appeal.id)}">
        <div><span class="tag green">ACKNOWLEDGED</span><strong>${escape(actor)}</strong>
          <p class="dungeon-appeal-acknowledgement">${escape(appeal.npc_acknowledgement ?? "Acknowledged.")}</p>
          ${change ? `<small>${escape(change)} · Reply: ${escape(appeal.response ?? "No intervention granted")}</small>` : ""}</div>
        <button type="button" class="button subtle" data-dm-dismiss="${escape(appeal.id)}">Dismiss</button></article>`;
    }).join("")}`;
  strip.querySelectorAll<HTMLButtonElement>("[data-dm-dismiss]").forEach(button => button.addEventListener("click", () => {
    dismissedAcknowledgements.add(button.dataset.dmDismiss!);
    button.closest<HTMLElement>("[data-appeal-receipt]")?.remove();
    if (!strip?.querySelector("[data-appeal-id], [data-appeal-receipt]")) strip?.remove();
  }));
  strip.querySelectorAll<HTMLButtonElement>("[data-dm-appeal]").forEach(button => button.addEventListener("click", async () => {
    const row = button.closest<HTMLElement>("[data-appeal-id]")!;
    const appealId = row.dataset.appealId!;
    const response = button.dataset.dmAppeal as "answer" | "decline";
    const text = row.querySelector<HTMLInputElement>("input")?.value ?? "";
    button.disabled = true;
    try {
      const result = await fetch("/api/interactions/appeal-response", { method: "POST",
        headers: { "content-type": "application/json" }, body: JSON.stringify({
          run_id: runId, appeal_id: appealId, response, text }) });
      if (!result.ok) throw new Error(await result.text());
      renderDungeonAppeal(await result.json(), onError);
    } catch (failure) {
      button.disabled = false;
      let message = row.querySelector<HTMLElement>(".dungeon-appeal-error");
      if (!message) {
        message = document.createElement("p");
        message.className = "dungeon-appeal-error";
        message.setAttribute("role", "alert");
        row.append(message);
      }
      message.textContent = failure instanceof Error ? failure.message : String(failure);
      onError(failure);
    }
  }));
}
