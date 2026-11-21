import { z } from "zod";
import { escape } from "./html.ts";
import { dashboardHeader, dashboardWorldSummary, simulationClock, trackDashboardHeader, villageClockTime } from "./view-mode.ts";
import { sectionTabPanel, sectionTabs } from "./section-tabs.ts";
import { renderDungeonAppeal } from "./dungeon-appeal.ts";

const quantity = z.number().int().nonnegative();
const text = z.string();
const amounts = z.record(text, quantity);
const names = z.record(text, text);
const dated = quantity.nullable();
const detail = text.nullable();
const checkpoints = z.tuple([quantity, quantity, quantity, quantity, quantity]);
const forecastChange = z.object({ production: quantity, inbound: quantity, inputs: quantity, outbound: quantity, expired: quantity,
  harvest: quantity.optional(), planned_production: quantity.optional() });

export const townSupplySchema = z.object({
  title: text, revision: quantity, minute: quantity, horizons: checkpoints,
  sites: names, residents: names, fixtures: names,
  inventory: z.array(z.object({ location: text, product: text, on_hand: quantity, usable: quantity,
    accounting_location: text.nullable().optional(),
    reserve: quantity, target: dated, projected: checkpoints, baseline_projected: checkpoints.optional(),
    forecast_changes:     z.tuple([forecastChange, forecastChange, forecastChange, forecastChange, forecastChange]).optional(),
    meal_allocations: z.object({ prepared: quantity, unallocated: quantity,
      services: z.array(z.object({ id: text, service: z.enum(["breakfast", "supper"]), due: quantity,
        required: quantity, reserved: quantity, shortfall: quantity })) }).optional() })),
  orders: z.array(z.object({ id: text, buyer: text, supplier: text, product: text, quantity,
    source: text, destination: text, delivered_quantity: quantity, remaining: quantity, in_transit: quantity,
    deadline: quantity, status: text, reason: detail, open: z.boolean() })),
  requests: z.array(z.object({ id: text, kind: text, owner: text, subject: text, quantity: dated,
    destination: detail, status: text, due: dated, order_id: detail, detail: text })),
  needs: z.array(z.object({ id: text, owner_id: text, good: text, quantity_needed: quantity,
    purpose: text, destination_id: text, supplier_id: text, status: text, notice_id: detail, blocker: detail })),
  cargo: z.array(z.object({ carrier: text, product: text, quantity, destination: text, order_id: detail })),
  production: z.array(z.object({ id: text, worker: text, location: text, outputs: amounts, inputs: amounts,
    ingredients: z.array(text), gathered: amounts, finish: quantity, status: text, basis: text })),
  replenishment: z.array(z.object({ id: text, worker: text, location: text, product: text,
    quantity, finish: dated, status: text, basis: text })).optional(),
  waterfall: z.object({ horizons: checkpoints, requirements: z.array(z.object({
    id: text, day: quantity, worker: text, site: text, product: text, planned_output: quantity,
    finish: dated, status: text, basis: text, on_hand: quantity, target: dated, inputs: amounts,
    blocked_by: z.array(z.object({ id: text, kind: text, minute: quantity, location: text,
      product: text, missing: quantity })) })) }).optional(),
  risks: z.array(z.object({ id: text, kind: text, minute: quantity, location: text, product: text, missing: quantity })),
  maintenance: z.array(z.object({ id: text, title: text, assignee: text, location: text, due_at: quantity,
    worked_minutes: quantity, required_minutes: quantity, supplies: amounts, status: text, blocker: detail })),
  upcoming_maintenance: z.array(z.object({ id: text, title: text, assignee: text, location: text,
    due_at: quantity, release_at: quantity, day: quantity, required_minutes: quantity, supplies: amounts,
    status: text })).default([]),
  projects: z.array(z.object({ id: text, title: text, location: text, status: text,
    due: dated, progress: dated, blocker: detail })),
  notes: z.array(z.object({ id: detail, fixture: text, location: detail, author: detail, title: text, text,
    minute: dated, version: dated, status: detail, latest_version: dated, latest_status: detail }))
}).nullable();

type Supply = z.infer<typeof townSupplySchema>;
const label = (value: string) => value.replace(/[-_]/g, " ");
const time = (minute: number | null) => minute === null ? "—" : villageClockTime(minute);
const goods = (values: Record<string, number>) => Object.entries(values)
  .map(([good, qty]) => `${qty} ${label(good)}`).join(", ") || "—";
const shown = (value: string | number | null) => escape(value ?? "—");
const table = (caption: string, headings: string[], rows: (string | number | null)[][], empty: string,
  productButton?: (rowIndex: number) => string) => rows.length
  ? `<div class="supply-table" tabindex="0" role="region" aria-label="${escape(caption)}"><table>
      <caption class="supply-caption">${escape(caption)}</caption><thead><tr>${headings.map(heading => `<th scope="col">${escape(heading)}</th>`).join("")}</tr></thead>
      <tbody>${rows.map((row, i) => `<tr>${row.map((cell, j) => `<td>${j === 1 && productButton ? productButton(i) : shown(cell)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`
  : `<p class="muted">${escape(empty)}</p>`;

type Snapshot = NonNullable<Supply>;
type InventoryRow = Snapshot["inventory"][number];
const name = (id: string | null, list: Record<string, string>) => id === null ? "—" : list[id] ?? label(id);
const stockKey = (row: InventoryRow) => JSON.stringify([row.location, row.product]);
const stockLabel = (row: InventoryRow, data: Snapshot) => `${label(row.product)} · ${name(row.location, data.sites)}`;
const shortage = (row: InventoryRow) => Math.max(0, row.reserve - row.usable);
const percent = (value: number, max: number) => value / max * 100;
const forecastLabels = ["Now", "+6h", "+24h", "+48h", "+7d", "+30d"];
const previewLimit = 5;
const belowProjectedReserve = (row: InventoryRow, index: number) => row.projected[index] < row.reserve
  || (row.baseline_projected?.[index] ?? row.reserve) < row.reserve;
const firstBreach = (row: InventoryRow) => row.projected.findIndex((_, i) => belowProjectedReserve(row, i));
function waterfallDiagram(data: Snapshot): string {
  const rows = (data.waterfall?.requirements ?? []).filter(row => row.finish !== null && row.finish <= data.minute + 2880);
  const byProduct = new Map<string, typeof rows>();
  for (const row of rows) byProduct.set(row.product, [...(byProduct.get(row.product) ?? []), row]);
  const chains = [...byProduct].map(([product, items]) => {
    const item = items[0];
    const inputs = Object.entries(item.inputs);
    const blocked = item.blocked_by.length > 0 || item.status === "blocked";
    return `<li class="supply-waterfall-chain${blocked ? " blocked" : ""}">
      <div class="supply-waterfall-source"><small>Daily demand</small><strong>${escape(label(product))}</strong><span>${item.planned_output} required</span></div>
      <span class="supply-waterfall-arrow" aria-hidden="true">→</span>
      <div class="supply-waterfall-stage"><small>${escape(name(item.site, data.sites))}</small><strong>${escape(name(item.worker, data.residents))}</strong><span>${escape(label(item.status))}</span></div>
      ${inputs.length ? `<span class="supply-waterfall-arrow" aria-hidden="true">→</span><div class="supply-waterfall-inputs"><small>Upstream inputs</small>${inputs.map(([input, quantity]) => `<span class="${item.blocked_by.some(block => block.product === input) ? "blocked" : ""}">${quantity} ${escape(label(input))}</span>`).join("")}</div>` : ""}
    </li>`;
  }).join("");
  return `<section class="supply-card supply-waterfall" aria-labelledby="supply-waterfall-title">
    <h3 id="supply-waterfall-title">48-hour supply waterfall</h3>
    <p class="muted">Read right to left: final demand pulls required work and then its physical inputs. Red links are currently blocked.</p>
    <ul>${chains || '<li class="muted">No timed supply requirements in this horizon.</li>'}</ul>
  </section>`;
}
const stockRisk = (row: InventoryRow) => shortage(row) > 0 ? 0 : firstBreach(row) >= 0 ? 1
  : row.reserve > 0 && row.usable === row.reserve ? 2 : 3;
const forecastButton = (row: InventoryRow, caption: string, selected: boolean) =>
  `<button type="button" class="supply-stock-link" data-forecast-key="${escape(stockKey(row))}" aria-controls="supply-forecast-chart" aria-pressed="${selected}">${escape(caption)}</button>`;
function inventoryRows(data: Snapshot, choices: InventoryRow[]) {
  const rows: { row: InventoryRow; values: (string | number | null)[]; caption: string }[] = [];
  for (const row of choices) {
    if (!row.meal_allocations) {
      rows.push({ row, caption: label(row.product), values: [name(row.location, data.sites), label(row.product),
        row.on_hand, row.usable, row.reserve, row.target,
        ...row.projected.flatMap((value, i) => choices.some(item => item.baseline_projected)
          ? [value, row.baseline_projected?.[i] ?? null] : [value])] });
      continue;
    }
    for (const service of row.meal_allocations.services) {
      const caption = `meals: ${service.service}`;
      rows.push({ row, caption, values: [name(row.location, data.sites), caption,
        service.reserved, service.reserved, 0, null,
        ...row.projected.flatMap(() => choices.some(item => item.baseline_projected) ? [null, null] : [null])] });
    }
    if (row.meal_allocations.unallocated) {
      const caption = "meals: unallocated";
      rows.push({ row, caption, values: [name(row.location, data.sites), caption,
        row.meal_allocations.unallocated, row.meal_allocations.unallocated, 0, null,
        ...row.projected.flatMap(() => choices.some(item => item.baseline_projected) ? [null, null] : [null])] });
    }
  }
  return rows;
}

function atLocation(data: Snapshot, location: string): Snapshot {
  if (!location) return data;
  return { ...data,
    inventory: data.inventory.filter(row => row.location === location),
    orders: data.orders.filter(row => row.source === location || row.destination === location),
    requests: data.requests.filter(row => row.destination === location),
    needs: data.needs.filter(row => row.destination_id === location),
    cargo: data.cargo.filter(row => row.destination === location),
    production: data.production.filter(row => row.location === location),
    replenishment: data.replenishment?.filter(row => row.location === location),
    risks: data.risks.filter(row => row.location === location),
    maintenance: data.maintenance.filter(row => row.location === location),
    upcoming_maintenance: data.upcoming_maintenance.filter(row => row.location === location),
    projects: data.projects.filter(row => row.location === location),
    notes: data.notes.filter(row => row.location === location),
  };
}

function forecastChoices(data: Snapshot): InventoryRow[] {
  return data.inventory.filter(row => row.on_hand > 0 || row.reserve > 0 || row.projected.some(value => value > 0)
    || row.baseline_projected?.some(value => value > 0))
    .sort((a, b) => stockRisk(a) - stockRisk(b) || shortage(b) - shortage(a)
      || (firstBreach(a) - firstBreach(b)) || stockLabel(a, data).localeCompare(stockLabel(b, data)));
}

const forecastDrivers = [["production", "started production", "+"], ["planned_production", "planned production", "+"],
  ["harvest", "planned harvest / collection", "+"], ["inbound", "expected deliveries", "+"],
  ["inputs", "planned production inputs", "−"], ["outbound", "outbound orders", "−"], ["expired", "expiry", "−"]] as const;

function stockForecast(data: Snapshot, row: InventoryRow): string {
  const baseline = row.baseline_projected;
  const max = Math.max(1, row.on_hand, row.reserve, ...row.projected, ...(baseline ?? []));
  const fill = (value: number, series: string) => `<i class="${series}" style="--size:${percent(value, max)}%"></i>`;
  const marker = row.reserve ? `<b style="--mark:${percent(row.reserve, max)}%"></b>` : "";
  const warning = (values: InventoryRow["projected"], scenario: string) => {
    const i = values.findIndex(value => value < row.reserve);
    return i < 0 ? "" : `<p class="supply-shortage">First projected below reserve${scenario}: <strong>${forecastLabels[i + 1]} · ${time(data.horizons[i])}</strong> (${values[i]} usable vs. ${row.reserve} reserve).</p>`;
  };
  return `<p><strong>${escape(stockLabel(row, data))}</strong> · ${row.reserve ? `Reserve: ${row.reserve}` : "No reserve configured"}</p>
    ${row.meal_allocations ? `<div class="supply-meal-allocations" aria-label="Prepared meal service allocation">
      <p><strong>${row.meal_allocations.prepared}</strong> prepared · <strong>${row.meal_allocations.unallocated}</strong> unallocated</p>
      <ul>${row.meal_allocations.services.map(service => `<li><strong>${service.service === "breakfast" ? "Breakfast" : "Supper"}</strong>
        · ${time(service.due)} · ${service.reserved}/${service.required} reserved${service.shortfall
          ? ` · <span class="supply-shortage">${service.shortfall} short</span>` : ""}</li>`).join("")}</ul>
    </div>` : ""}
    <p class="muted">Future bars show projected usable stock, not guaranteed stock levels.${marker ? " The white line marks the reserve." : ""}</p>
    <ul class="supply-legend" aria-label="Forecast legend"><li class="on-hand">On hand</li><li class="with-plans">With unstarted plans</li>
      ${baseline ? '<li class="without-plans">Without unstarted plans</li>' : ""}${row.reserve ? '<li class="reserve">Reserve</li>' : ""}</ul>
    ${baseline ? "" : '<p class="muted">Projection without unstarted plans unavailable in this snapshot.</p>'}
    <ul class="supply-columns" aria-label="Inventory by checkpoint">
      <li><div class="supply-column" aria-hidden="true">${fill(row.on_hand, "on-hand")}${marker}</div>
        <strong>Now</strong><span>On hand: <strong>${row.on_hand}</strong></span><span>Usable: <strong>${row.usable}</strong></span>
        ${shortage(row) ? '<small class="supply-shortage">Usable below reserve</small>' : ""}</li>
      ${row.projected.map((value, i) => `<li><div class="supply-column${baseline ? " paired" : ""}" aria-hidden="true">
          ${fill(value, "with-plans")}${baseline ? fill(baseline[i], "without-plans") : ""}${marker}</div>
        <strong>${forecastLabels[i + 1]}</strong><span>With plans: <strong>${value}</strong></span>
        ${baseline ? `<span>Without plans: <strong>${baseline[i]}</strong></span>` : ""}
        ${belowProjectedReserve(row, i) ? '<small class="supply-shortage">Below reserve</small>' : ""}</li>`).join("")}</ul>
    ${warning(row.projected, baseline ? " with plans" : "")}${baseline ? warning(baseline, " without plans") : ""}
    ${row.forecast_changes ? `<details><summary>What changes the forecast?</summary><ul class="supply-forecast-drivers">${row.forecast_changes.map((change, i) => {
      const explanation = forecastDrivers.flatMap(([key, title, sign]) => {
        const value = change[key] ?? 0;
        return value > 0 ? [`<span class="supply-driver ${key}">${sign}${value} ${title}</span>`] : [];
      });
      return `<li><strong>${forecastLabels[i]} → ${forecastLabels[i + 1]}</strong>: ${explanation.join(" · ") || "No modeled stock movement"}</li>`;
    }).join("")}</ul></details>` : '<p class="muted">Stock movement details are unavailable in this snapshot.</p>'}`;
}

function stockCharts(data: Snapshot, choices: InventoryRow[], selected: InventoryRow | undefined): string {
  const reserves = choices.filter(row => row.reserve > 0);
  const preview = reserves.slice(0, previewLimit);
  const reserveMax = preview.reduce((max, row) => Math.max(max, row.usable, row.reserve), 1);
  return `<div class="supply-chart-grid">
    <section class="supply-card" aria-labelledby="supply-reserves"><h3 id="supply-reserves">Usable stock vs. reserve</h3>
      <p class="muted">The marker is the reserve. Highest-risk stock appears first.</p>
      ${preview.length ? `<ul class="supply-bars" aria-label="Stock and reserve comparison">${preview.map(row => `<li>
        <div>${forecastButton(row, stockLabel(row, data), row === selected)} <span>${row.usable} usable · ${row.reserve} reserve</span></div>
        <div class="supply-bar" aria-hidden="true"><i class="${shortage(row) ? "short" : ""}" style="--size:${percent(row.usable, reserveMax)}%"></i>
          <b style="--mark:${percent(row.reserve, reserveMax)}%"></b></div>
        ${shortage(row) ? `<small class="supply-shortage">${shortage(row)} below reserve</small>` : ""}</li>`).join("")}</ul>`
        : '<p class="muted">No stock with a configured reserve in this view.</p>'}
      <p class="supply-card-footer"><a href="#inventory">Full inventory (${choices.length})</a>${reserves.length > previewLimit ? ` · Showing ${previewLimit} of ${reserves.length} with reserves` : ""}</p>
    </section>
    <section class="supply-card" id="supply-forecast-chart" aria-labelledby="supply-forecast" tabindex="-1"><h3 id="supply-forecast">Inventory forecast</h3>
      <label class="supply-forecast-picker">Forecast product &amp; site <select id="supply-product"${choices.length ? "" : " disabled"}>
        ${choices.length ? choices.map(row => `<option value="${escape(stockKey(row))}"${row === selected ? " selected" : ""}>${escape(stockLabel(row, data))}</option>`).join("") : "<option>No inventory here</option>"}
      </select></label>
      ${selected ? stockForecast(data, selected) : '<p class="muted">No products to forecast in this view.</p>'}
    </section></div>`;
}

const attentionWindow = 6 * 60;

function attentionMarkup(data: Snapshot, choices: InventoryRow[], selected: InventoryRow | undefined): string {
  const site = (id: string) => name(id, data.sites);
  const priority = { Overdue: 0, Blocked: 1, "Due now": 2, "Due soon": 3 };
  const classify = (due: number | null, blocked: boolean): keyof typeof priority | null =>
    due !== null && due < data.minute ? "Overdue" : blocked ? "Blocked"
      : due === data.minute ? "Due now" : due !== null && due <= data.minute + attentionWindow ? "Due soon" : null;
  const items = [
    ...data.orders.filter(order => order.open && (order.remaining > 0 || order.status === "blocked")).map(order => ({
      title: `${order.remaining} ${label(order.product)} remaining (${order.id})`, where: `${site(order.source)} → ${site(order.destination)}`,
      due: order.deadline, blocked: order.status === "blocked", reason: order.reason, section: "orders", kind: "Order",
    })),
    ...data.maintenance.filter(job => job.status !== "completed").map(job => ({ title: job.title, where: site(job.location),
      due: job.due_at, blocked: job.status === "blocked", reason: job.blocker, section: "maintenance", kind: "Maintenance" })),
    ...data.projects.filter(project => project.status !== "completed").map(project => ({ title: project.title, where: site(project.location),
      due: project.due, blocked: project.status === "blocked", reason: project.blocker, section: "maintenance", kind: "Project" })),
  ].flatMap(item => {
    const status = classify(item.due, item.blocked);
    return status ? [{ ...item, status }] : [];
  }).sort((a, b) => priority[a.status] - priority[b.status] || (a.due ?? Infinity) - (b.due ?? Infinity));
  const stock = choices.filter(row => stockRisk(row) < 3);
  const below = stock.filter(row => shortage(row) > 0).length;
  const at = stock.filter(row => row.reserve > 0 && row.usable === row.reserve).length;
  const projected = stock.filter(row => firstBreach(row) >= 0).length;
  return `<section class="supply-card" aria-labelledby="supply-attention"><h3 id="supply-attention">Needs attention (${items.length + stock.length})</h3>
    <div class="supply-attention-grid">
    <div><h4>Stock risk (${stock.length})</h4>
    <p class="muted">${below} below reserve now · ${at} at reserve (no buffer) · ${projected} projected below reserve. A product can count in more than one category.</p>
    ${stock.length ? `<ul class="supply-attention" aria-label="Stock needing attention">${stock.slice(0, previewLimit).map(row => {
      const breach = firstBreach(row);
      const scenario = breach < 0 || !row.baseline_projected ? "" : row.projected[breach] >= row.reserve ? " without plans"
        : row.baseline_projected[breach] >= row.reserve ? " with plans" : " with and without plans";
      return `<li><span class="supply-badge ${stockRisk(row) < 2 ? "urgent" : ""}">${shortage(row) ? "Below reserve" : row.usable === row.reserve ? "At reserve" : "Projected below reserve"}</span>
        <div>${forecastButton(row, stockLabel(row, data), row === selected)}<p>${row.usable} usable · ${row.reserve} reserve${breach >= 0 ? ` · Below reserve${scenario} at ${forecastLabels[breach + 1]}` : ""}</p></div></li>`;
    }).join("")}</ul>` : '<p class="muted">No stock at risk in this view.</p>'}
    ${stock.length > previewLimit ? `<p class="supply-card-footer">Showing ${previewLimit} of ${stock.length} · <a href="#inventory">Full inventory</a></p>` : ""}
    </div><div><h4>Orders &amp; maintenance (${items.length})</h4>
    <p class="muted">Overdue, blocked, or due in the next 6 simulated hours.</p>
    ${items.length ? `<ul class="supply-attention" aria-label="Orders and maintenance needing attention">${items.slice(0, previewLimit).map(item => `<li>
      <span class="supply-badge ${item.status === "Due soon" ? "" : "urgent"}">${item.status}${item.blocked && item.status !== "Blocked" ? " · Blocked" : ""}</span>
      <div><a href="#${item.section}">${item.kind}: ${escape(item.title)}</a><p>${escape(item.where)} · ${item.due === null ? "No deadline" : `Due ${escape(time(item.due))}`}${item.reason ? ` · ${escape(item.reason)}` : ""}</p></div>
    </li>`).join("")}</ul>` : '<p class="muted">No orders or maintenance need attention in this view.</p>'}
    ${items.length > previewLimit ? `<p class="supply-card-footer">Showing ${previewLimit} of ${items.length} · <a href="#orders">Full orders &amp; requests</a> · <a href="#maintenance">Full maintenance</a></p>` : ""}</div></div></section>`;
}

function supplyWorldSummary(data: NonNullable<Supply>): string {
  const clock = simulationClock({ village: { minute: data.minute } });
  return dashboardWorldSummary(data.title,
    `<p>Read-only · Saved town state · Revision ${data.revision}</p>`,
    clock ? `<div class="monitor-clock" role="timer" aria-label="Current game time"><span class="eyebrow">CURRENT GAME TIME</span><strong>${clock.time}</strong><small>${clock.description}</small></div>` : "");
}

export function townSupplyMarkup(snapshot: Supply, location = "", forecastKey = ""): string {
  if (!snapshot) return '<p class="muted">No town snapshot available. Start or load a village adventure in <a href="/">Player view</a>.</p>';
  const data = atLocation(snapshot, location);
  const choices = forecastChoices(data);
  const inventoryDisplayRows = inventoryRows(data, choices);
  const selected = choices.find(row => stockKey(row) === forecastKey) ?? choices[0];
  const site = (id: string | null) => name(id, data.sites);
  const resident = (id: string | null) => name(id, data.residents);
  const open = data.orders.filter(order => order.open);
  const pastDue = open.filter(order => order.remaining > 0 && order.deadline < data.minute);
  const belowReserve = data.inventory.filter(row => row.usable < row.reserve);
  const replenishment = data.replenishment ?? [];
  const waterfall = data.waterfall?.requirements ?? [];
  const hasBaseline = choices.some(row => row.baseline_projected);
  const maintenanceIssues = data.maintenance.filter(job => job.status !== "completed" &&
    (job.status === "blocked" || job.due_at <= data.minute)).length
    + data.projects.filter(project => project.status !== "completed" &&
      (project.status === "blocked" || (project.due !== null && project.due <= data.minute))).length;
  const riskMetrics = [["Blocked orders", open.filter(order => order.status === "blocked").length],
    ["Missed delivery deadlines", pastDue.length + data.orders.filter(order => order.reason === "MISSED_DEADLINE").length],
    ["Below reserve now", belowReserve.length], ["Below reserve at +48h", data.inventory.filter(row => belowProjectedReserve(row, 2)).length],
    ["48h forecast exceptions", data.risks.length], ["Blocked / due maintenance", maintenanceIssues]] as const;
  const notesBySite = new Map<string | null, typeof data.notes>();
  for (const note of [...data.notes].sort((a, b) => site(a.location).localeCompare(site(b.location))
    || (b.minute ?? -1) - (a.minute ?? -1))) {
    const group = notesBySite.get(note.location) ?? [];
    group.push(note);
    notesBySite.set(note.location, group);
  }
  const orderRows = (orders: typeof data.orders) => orders.map(order => [order.id,
    `${order.quantity} ${label(order.product)}`, `${site(order.source)} → ${site(order.destination)}`,
    `${resident(order.supplier)} → ${resident(order.buyer)}`, label(order.status), order.delivered_quantity,
    order.in_transit, order.remaining, time(order.deadline), order.reason]);
  const orderHead = ["Order", "Requested", "Route", "Supplier → buyer", "Status", "Delivered", "In transit", "Remaining", "Due", "Reason"];
  const panel = (id: string, label: string, count?: number) =>
    sectionTabPanel(id, count === undefined ? label : `${label} (${count})`, "supply-section");
  return `<section ${panel("supply-overview", "Overview")}><h2>Supply overview</h2>
    ${attentionMarkup(data, choices, selected)}
    <dl class="supply-metrics"><div><dt>Open orders</dt><dd>${open.length}</dd></div>
      ${riskMetrics.map(([title, value]) => `<div${value > 0 ? ' class="supply-metric-alert"' : ""}><dt>${title}</dt><dd>${value}</dd></div>`).join("")}</dl>
    ${stockCharts(data, choices, selected)}
  </section>
  <section ${panel("inventory", "Inventory & forecast", choices.length)}><h2>Inventory & forecast</h2>
    <p class="muted">Forecasts age stock and include started production and committed deliveries, using order deadlines as estimated delivery times. The with-plans forecast also includes modeled unstarted production, replenishment, and standing daily capacity for configured targets. Standing rows remain conditional on real workers, inputs, equipment, care and execution. Baseline excludes these plans. Consumption such as meals and maintenance remains excluded unless explicitly modeled. Reserves are protected from allocations; timing and execution are not guaranteed.</p>
    ${table("Inventory by location", ["Location", "Product", "On hand", "Usable now", "Reserve", "Production target",
      ...data.horizons.flatMap((due, i) => {
        const checkpoint = `${forecastLabels[i + 1]} · ${time(due)}`;
        return hasBaseline ? [`${checkpoint} with plans`, `${checkpoint} without plans`] : [checkpoint];
      })], inventoryDisplayRows.map(entry => entry.values), "No inventory in this view.",
      i => forecastButton(inventoryDisplayRows[i].row, inventoryDisplayRows[i].caption,
        inventoryDisplayRows[i].row === selected))}
    <h3>Forecast exceptions</h3>${table("Projected production and delivery shortages", ["Reference", "Type", "When", "Location", "Product", "Missing"],
      data.risks.map(risk => [risk.id, risk.kind, time(risk.minute), site(risk.location), label(risk.product), risk.missing]),
      "No modeled shortages in the next 48 simulated hours.")}
    <h3>Cargo in transit</h3>${table("Cargo in transit", ["Carrier", "Product", "Quantity", "Destination", "Order"],
      data.cargo.map(item => [resident(item.carrier), label(item.product), item.quantity, site(item.destination), item.order_id]), "No shipment cargo recorded.")}
  </section>
  <section ${panel("orders", "Orders & requests", data.orders.length + data.requests.length + data.needs.length)}><h2>Orders & requests</h2>
    ${table("Open orders", orderHead, orderRows(open), "No open supplier orders.")}
    <details><summary>Closed orders (${data.orders.length - open.length})</summary>
      ${table("Closed orders", orderHead, orderRows(data.orders.filter(order => !order.open)), "No closed supplier orders.")}</details>
    <h3>Recorded requests</h3>${table("Recorded supply and work requests", ["Request", "Type", "Subject", "Requester", "Quantity", "Destination", "Status", "Due", "Order", "Detail"],
      data.requests.map(item => [item.id, label(item.kind), item.subject, resident(item.owner), item.quantity,
        site(item.destination), label(item.status), time(item.due), item.order_id, item.detail]), "No supply or work requests recorded.")}
    <h3>Resident supply needs</h3>${table("Resident supply needs", ["Resident", "Good", "Needed", "Supplier", "Destination", "Status", "Notice", "Purpose / blocker"],
      data.needs.map(need => [resident(need.owner_id), label(need.good), need.quantity_needed, resident(need.supplier_id),
        site(need.destination_id), label(need.status), need.notice_id, [need.purpose, need.blocker].filter(Boolean).join(" · ")]), "No resident supply needs tracked.")}
  </section>
  <section ${panel("production", "Production & materials", data.production.length + replenishment.length)}><h2>Production & bills of materials</h2>
    <p class="muted">Materials are the quantities recorded in each plan. An active batch lists known ingredients, not inferred quantities.</p>
    ${table("Planned production and active batches", ["Reference", "Worker", "Site", "Output", "Recorded materials", "To gather (included)", "Finish", "Status", "Planning basis"],
      data.production.map(item => [item.id, resident(item.worker), site(item.location), goods(item.outputs),
        item.ingredients.length ? item.ingredients.map(label).join(", ") : goods(item.inputs), goods(item.gathered),
        time(item.finish), label(item.status), item.basis]), "No recorded production plans or active batches.")}
    <h3>Internal replenishment</h3>
    <p class="muted">Planned harvest and collection replenish local stock. Blocked tasks are listed but do not add stock to the forecast.</p>
    ${table("Internal replenishment tasks", ["Reference", "Worker", "Site", "Product", "Quantity", "Estimated finish", "Status", "Planning basis / blocker"],
      replenishment.map(item => [item.id, resident(item.worker), site(item.location), label(item.product),
        item.status === "blocked" && item.quantity === 0 ? null : item.quantity,
        item.finish === null ? "Unscheduled" : time(item.finish), label(item.status), item.basis]), "No internal replenishment tasks recorded.")}
    <h3>Supply-chain requirements waterfall</h3>
    <p class="muted">Daily meal demand is expanded backwards into producer requirements, including bread ingredients, flour-to-grain, tea-to-herbs, livestock feed, fishing quota, fuel, and log demand. Rows are conditional plans; blockers identify what must be replenished first.</p>
    ${waterfallDiagram(data)}
    ${table("Supply-chain requirements waterfall", ["Day", "Worker", "Site", "Product", "Planned output", "On hand", "Target", "Inputs", "Finish", "Status / blockers"],
      waterfall.map(item => [`Day ${item.day}`, resident(item.worker), site(item.site), label(item.product),
        item.planned_output, item.on_hand, item.target, goods(item.inputs), time(item.finish),
        `${label(item.status)}${item.blocked_by.length ? ` · ${item.blocked_by.map(block => `${label(block.product)} short ${block.missing}`).join(", ")}` : ""}`]),
      "No standing supply-chain requirements are modeled.")}
  </section>
  <section ${panel("maintenance", "Maintenance", data.maintenance.length + data.projects.length)}><h2>Maintenance projects & jobs</h2>
    ${table("Structural projects", ["Project", "Site", "Status", "Contributions", "Due", "Blocker"],
      data.projects.map(project => [project.title, site(project.location), label(project.status), project.progress,
        time(project.due), project.blocker]), "No structural projects tracked.")}
    <h3>Tomorrow's routine upkeep</h3>
    <p class="muted">Preview only; these jobs become active on release and are not duplicate current work.</p>
    ${table("Tomorrow's routine upkeep", ["Job", "Assignee", "Site", "Release", "Due", "Work minutes", "Materials"],
      data.upcoming_maintenance.map(job => [job.title, resident(job.assignee), site(job.location),
        time(job.release_at), time(job.due_at), job.required_minutes, goods(job.supplies)]),
      "No routine jobs are scheduled to start tomorrow.")}
    ${table("Maintenance jobs, including completed history", ["Job", "Assignee", "Site", "Status", "Work minutes", "Due", "Materials", "Blocker"],
      [...data.maintenance].sort((a, b) => Number(a.status === "completed") - Number(b.status === "completed")
        || b.due_at - a.due_at).map(job => [job.title, resident(job.assignee), site(job.location), label(job.status),
        `${job.worked_minutes} / ${job.required_minutes}`, time(job.due_at), goods(job.supplies), job.blocker]), "No routine maintenance jobs recorded.")}
  </section>
  <section ${panel("notices", "Posted notes", data.notes.length)}><h2>Posted notes by location</h2>
    <p class="muted">Physical boards and inboxes, fixed signs, and archived public bell notices. An older copy can remain posted after a notice changes.</p>
    ${[...notesBySite].map(([location, notes]) => `<h3>${escape(location === null ? "Unknown location" : site(location))}</h3>
      <div class="supply-notes">${notes.map(note => `<article><h4>${escape(note.title)}</h4><small>${escape([
        name(note.fixture, data.fixtures), note.author && resident(note.author), note.minute !== null && time(note.minute),
        note.version !== null && `${note.id} · v${note.version} · ${label(note.status ?? "")}`,
        note.latest_version !== note.version && `Latest: v${note.latest_version} · ${label(note.latest_status ?? "")}`
      ].filter(Boolean).join(" · "))}</small><p>${escape(note.text)}</p></article>`).join("")}</div>`).join("") || '<p class="muted">No posted notes recorded.</p>'}
  </section>`;
}

export function startTownSupply(app: HTMLDivElement): void {
  app.className = "world-view supply-view";
  document.title = "NPCSIM · Town supply chain";
  app.innerHTML = `<main class="dashboard-page"><div class="command-shell">
    ${dashboardHeader("supply", "TOWN OPERATIONS", "Town supply chain")}
    <nav class="dashboard-tabs" role="tablist" aria-label="Town supply sections" hidden></nav></div>
    <p class="notice" role="alert" hidden></p><div id="supply-summary"></div>
    <div class="supply-tools" hidden><button id="export-waterfall" class="button subtle" type="button">Export waterfall JSON</button>
      <label>Location <select id="supply-location" aria-controls="supply-content"></select></label>
    </div><div id="supply-content"><p class="muted">Loading town snapshot…</p></div></main>`;
  trackDashboardHeader(app);
  const content = app.querySelector<HTMLDivElement>("#supply-content")!;
  const summary = app.querySelector<HTMLDivElement>("#supply-summary")!;
  const error = app.querySelector<HTMLParagraphElement>("[role=alert]")!;
  const tools = app.querySelector<HTMLDivElement>(".supply-tools")!;
  const locationFilter = app.querySelector<HTMLSelectElement>("#supply-location")!;
  const tableScroll = new Map<string | null, [number, number]>();
  const tables = () => content.querySelectorAll<HTMLElement>(".dashboard-panel:not([hidden]) .supply-table");
  const rememberScroll = () => {
    for (const table of tables()) tableScroll.set(table.getAttribute("aria-label"), [table.scrollLeft, table.scrollTop]);
  };
  const restoreScroll = () => {
    for (const table of tables()) {
      const position = tableScroll.get(table.getAttribute("aria-label"));
      if (position) table.scrollTo(...position);
    }
  };
  const tabs = sectionTabs(app.querySelector<HTMLElement>(".dashboard-tabs")!, content,
    phase => phase === "before" ? rememberScroll() : restoreScroll());
  let forecastKey = "";
  let snapshot: Supply = null;
  const exportWaterfall = app.querySelector<HTMLButtonElement>("#export-waterfall");
  exportWaterfall?.addEventListener("click", () => {
    if (!snapshot?.waterfall) return;
    const payload = JSON.stringify({
      title: snapshot.title, minute: snapshot.minute, revision: snapshot.revision,
      ...snapshot.waterfall,
    }, null, 2);
    const url = URL.createObjectURL(new Blob([payload], { type: "application/json" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = `millhaven-supply-waterfall-day-${Math.floor(snapshot.minute / 1440) + 1}.json`;
    link.click();
    URL.revokeObjectURL(url);
  });
  let previous = "";
  let controller: AbortController | undefined;
  let timer: ReturnType<typeof setTimeout>;

  const options = new WeakMap<HTMLSelectElement, string>();
  const setOptions = (select: HTMLSelectElement, choices: [string, string][]) => {
    const signature = JSON.stringify(choices);
    if (signature === options.get(select)) return;
    const previousValue = select.value;
    select.replaceChildren(...choices.map(([value, title]) => new Option(title, value)));
    if (choices.some(([value]) => value === previousValue)) select.value = previousValue;
    options.set(select, signature);
  };
  const render = (preserveScroll = false) => {
    const heading = snapshot ? supplyWorldSummary(snapshot) : "";
    if (summary.innerHTML !== heading) summary.innerHTML = heading;
    tools.hidden = !snapshot;
    if (snapshot) {
      void fetch("/api/state").then(response => response.ok ? response.json() : null)
        .then(payload => { if (payload) renderDungeonAppeal(payload); });
      const sites = Object.entries(snapshot.sites).sort((a, b) => a[1].localeCompare(b[1]));
      setOptions(locationFilter, [["", "All locations"], ...sites]);
    }
    const details = ["#supply-forecast-chart details", "#orders details"];
    const openDetails = details.filter(selector => content.querySelector<HTMLDetailsElement>(selector)?.open);
    const focusId = content.contains(document.activeElement) ? document.activeElement?.id : undefined;
    if (preserveScroll) rememberScroll();
    else tableScroll.clear();
    content.innerHTML = townSupplyMarkup(snapshot, locationFilter.value, forecastKey);
    tabs.sync();
    const picker = content.querySelector<HTMLSelectElement>("#supply-product");
    forecastKey = picker?.value ?? "";
    for (const selector of openDetails) content.querySelector(selector)?.setAttribute("open", "");
    if (focusId) document.getElementById(focusId)?.focus({ preventScroll: true });
    if (preserveScroll) restoreScroll();
  };
  locationFilter.addEventListener("change", () => render());
  content.addEventListener("change", event => {
    const picker = event.target;
    if (!(picker instanceof HTMLSelectElement) || picker.id !== "supply-product") return;
    forecastKey = picker.value;
    render(true);
  });
  content.addEventListener("click", event => {
    const button = event.target instanceof Element ? event.target.closest<HTMLButtonElement>("[data-forecast-key]") : null;
    if (!button?.dataset.forecastKey) return;
    forecastKey = button.dataset.forecastKey;
    render(true);
    tabs.select("supply-overview");
    const chart = content.querySelector<HTMLElement>("#supply-forecast-chart");
    chart?.focus({ preventScroll: true });
    chart?.scrollIntoView({ block: "nearest" });
  });
  const refresh = async () => {
    const request = new AbortController();
    controller = request;
    try {
      const response = await fetch("/api/town-supply", { signal: request.signal });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      if (request.signal.aborted) return;
      const signature = JSON.stringify(data);
      if (signature !== previous) {
        snapshot = townSupplySchema.parse(data);
        render(true);
        previous = signature;
      }
      error.hidden = true;
    } catch (failure) {
      if (request.signal.aborted) return;
      error.textContent = `Could not refresh the town snapshot: ${failure instanceof Error ? failure.message : String(failure)}`;
      error.hidden = false;
    } finally {
      if (!request.signal.aborted) timer = setTimeout(() => void refresh(), 5000);
    }
  };
  window.addEventListener("pagehide", () => { controller?.abort(); clearTimeout(timer); });
  window.addEventListener("pageshow", event => { if (event.persisted) void refresh(); });
  void refresh();
}
