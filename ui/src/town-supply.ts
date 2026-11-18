import { z } from "zod";
import { escape } from "./html.ts";
import { viewNavigation, villageClockTime } from "./view-mode.ts";

const quantity = z.number().int().nonnegative();
const text = z.string();
const amounts = z.record(text, quantity);
const names = z.record(text, text);
const dated = quantity.nullable();
const detail = text.nullable();

export const townSupplySchema = z.object({
  title: text, revision: quantity, minute: quantity, horizons: z.tuple([quantity, quantity, quantity]),
  sites: names, residents: names, fixtures: names,
  inventory: z.array(z.object({ location: text, product: text, on_hand: quantity, usable: quantity,
    reserve: quantity, target: dated, projected: z.tuple([quantity, quantity, quantity]) })),
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
  risks: z.array(z.object({ id: text, kind: text, minute: quantity, location: text, product: text, missing: quantity })),
  maintenance: z.array(z.object({ id: text, title: text, assignee: text, location: text, due_at: quantity,
    worked_minutes: quantity, required_minutes: quantity, supplies: amounts, status: text, blocker: detail })),
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
const table = (caption: string, headings: string[], rows: (string | number | null)[][], empty: string) => rows.length
  ? `<div class="supply-table" tabindex="0" role="region" aria-label="${escape(caption)}"><table>
      <caption class="supply-caption">${escape(caption)}</caption><thead><tr>${headings.map(heading => `<th scope="col">${escape(heading)}</th>`).join("")}</tr></thead>
      <tbody>${rows.map(row => `<tr>${row.map(cell => `<td>${shown(cell)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`
  : `<p class="muted">${escape(empty)}</p>`;
const sections = [["inventory", "Inventory & forecast"], ["orders", "Orders & requests"],
  ["production", "Production & materials"], ["maintenance", "Maintenance"], ["notices", "Posted notes"]] as const;

type Snapshot = NonNullable<Supply>;
type InventoryRow = Snapshot["inventory"][number];
const name = (id: string | null, list: Record<string, string>) => id === null ? "—" : list[id] ?? label(id);
const stockKey = (row: InventoryRow) => JSON.stringify([row.location, row.product]);
const stockLabel = (row: InventoryRow, data: Snapshot) => `${label(row.product)} · ${name(row.location, data.sites)}`;
const shortage = (row: InventoryRow) => Math.max(0, row.reserve - row.usable);
const percent = (value: number, max: number) => value / max * 100;
const forecastLabels = ["Now", "+6h", "+24h", "+48h"];

function atLocation(data: Snapshot, location: string): Snapshot {
  if (!location) return data;
  return { ...data,
    inventory: data.inventory.filter(row => row.location === location),
    orders: data.orders.filter(row => row.source === location || row.destination === location),
    requests: data.requests.filter(row => row.destination === location),
    needs: data.needs.filter(row => row.destination_id === location),
    cargo: data.cargo.filter(row => row.destination === location),
    production: data.production.filter(row => row.location === location),
    risks: data.risks.filter(row => row.location === location),
    maintenance: data.maintenance.filter(row => row.location === location),
    projects: data.projects.filter(row => row.location === location),
    notes: data.notes.filter(row => row.location === location),
  };
}

const stockedInventory = (data: Snapshot) => data.inventory.filter(row => row.on_hand > 0);

function forecastChoices(data: Snapshot): InventoryRow[] {
  return stockedInventory(data).sort((a, b) => Number(shortage(b) > 0) - Number(shortage(a) > 0)
    || Number(b.reserve > 0) - Number(a.reserve > 0) || stockLabel(a, data).localeCompare(stockLabel(b, data)));
}

function stockCharts(data: Snapshot, forecastKey: string): string {
  const choices = forecastChoices(data);
  const reserves = choices.filter(row => row.reserve > 0);
  const reserveMax = reserves.reduce((max, row) => Math.max(max, row.usable, row.reserve), 1);
  const selected = choices.find(row => stockKey(row) === forecastKey) ?? choices[0];
  const values = selected ? [selected.usable, ...selected.projected] : [];
  const forecastMax = Math.max(1, selected?.reserve ?? 0, ...values);
  return `<div class="supply-chart-grid">
    <section class="supply-card" aria-labelledby="supply-reserves"><h3 id="supply-reserves">Usable stock vs. reserve</h3>
      <p class="muted">The marker is the reserve. Shortages appear first.</p>
      ${reserves.length ? `<ul class="supply-bars" tabindex="0" aria-label="Stock and reserve comparison">${reserves.map(row => `<li>
        <div><strong>${escape(stockLabel(row, data))}</strong> <span>${row.usable} usable · ${row.reserve} reserve</span></div>
        <div class="supply-bar" aria-hidden="true"><i class="${shortage(row) ? "short" : ""}" style="--size:${percent(row.usable, reserveMax)}%"></i>
          <b style="--mark:${percent(row.reserve, reserveMax)}%"></b></div>
        ${shortage(row) ? `<small class="supply-shortage">${shortage(row)} below reserve</small>` : ""}</li>`).join("")}</ul>`
        : '<p class="muted">No stock with a configured reserve in this view.</p>'}
    </section>
    <section class="supply-card" id="supply-forecast-chart" aria-labelledby="supply-forecast"><h3 id="supply-forecast">Inventory forecast</h3>
      ${selected ? `<p><strong>${escape(stockLabel(selected, data))}</strong> · ${selected.reserve ? `Reserve: ${selected.reserve}` : "No reserve configured"}</p>
        <p class="muted">Future bars are projections at simulated checkpoints, not guaranteed stock levels.</p>
        <ul class="supply-columns">${values.map((value, i) => `<li><strong>${value}</strong>
          <div class="supply-column" aria-hidden="true"><i class="${value < selected.reserve ? "short" : ""}" style="--size:${percent(value, forecastMax)}%"></i>
            ${selected.reserve ? `<b style="--mark:${percent(selected.reserve, forecastMax)}%"></b>` : ""}</div>
          <span>${forecastLabels[i]}</span>${value < selected.reserve ? '<small class="supply-shortage">Below reserve</small>' : ""}</li>`).join("")}</ul>`
        : '<p class="muted">No products on hand to forecast in this view.</p>'}
    </section></div>`;
}

const attentionWindow = 6 * 60;

function attentionMarkup(data: Snapshot): string {
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
  return `<section class="supply-card" aria-labelledby="supply-attention"><h3 id="supply-attention">Needs attention (${items.length})</h3>
    <p class="muted">Overdue, blocked, or due in the next 6 simulated hours.</p>
    ${items.length ? `<ul class="supply-attention" tabindex="0" aria-label="Orders and maintenance needing attention">${items.map(item => `<li>
      <span class="supply-badge ${item.status === "Due soon" ? "" : "urgent"}">${item.status}${item.blocked && item.status !== "Blocked" ? " · Blocked" : ""}</span>
      <div><a href="#${item.section}">${item.kind}: ${escape(item.title)}</a><p>${escape(item.where)} · ${item.due === null ? "No deadline" : `Due ${escape(time(item.due))}`}${item.reason ? ` · ${escape(item.reason)}` : ""}</p></div>
    </li>`).join("")}</ul>` : '<p class="muted">No orders or maintenance need attention in this view.</p>'}</section>`;
}

export function townSupplyMarkup(snapshot: Supply, location = "", forecastKey = ""): string {
  if (!snapshot) return '<p class="muted">No town snapshot available. Start or load a village adventure in <a href="/">Player view</a>.</p>';
  const data = atLocation(snapshot, location);
  const site = (id: string | null) => name(id, data.sites);
  const resident = (id: string | null) => name(id, data.residents);
  const open = data.orders.filter(order => order.open);
  const pastDue = open.filter(order => order.remaining > 0 && order.deadline < data.minute);
  const belowReserve = data.inventory.filter(row => row.usable < row.reserve);
  const maintenanceIssues = data.maintenance.filter(job => job.status !== "completed" &&
    (job.status === "blocked" || job.due_at <= data.minute)).length
    + data.projects.filter(project => project.status !== "completed" &&
      (project.status === "blocked" || (project.due !== null && project.due <= data.minute))).length;
  const metrics = [["Open orders", open.length], ["Blocked orders", open.filter(order => order.status === "blocked").length],
    ["Missed delivery deadlines", pastDue.length + data.orders.filter(order => order.reason === "MISSED_DEADLINE").length],
    ["Below reserve now", belowReserve.length], ["Below reserve at +48h", data.inventory.filter(row => row.projected[2] < row.reserve).length],
    ["48h forecast exceptions", data.risks.length], ["Blocked / due maintenance", maintenanceIssues]];
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
  return `<section class="monitor-summary" aria-label="Supply chain health">
    <div><span class="eyebrow">READ-ONLY OPERATOR VIEW</span><h2>${escape(data.title)}</h2>
      <p class="muted">Saved town state · Revision ${data.revision}</p></div>
    <div class="monitor-clock"><span class="eyebrow">SIMULATION TIME</span><strong>${escape(time(data.minute))}</strong></div>
  </section>
  <nav class="supply-links" aria-label="Dashboard sections"><a href="#supply-overview">Overview</a>${sections.map(([id, title]) => `<a href="#${id}">${title}</a>`).join("")}</nav>
  <section id="supply-overview" class="supply-section"><h2>Supply overview</h2>
    ${attentionMarkup(data)}
    ${stockCharts(data, forecastKey)}
  </section>
  <dl class="supply-metrics">${metrics.map(([title, value]) => `<div><dt>${title}</dt><dd>${value}</dd></div>`).join("")}</dl>
  <section id="inventory" class="supply-section"><h2>Inventory & forecast</h2>
    <p class="muted">Only products with stock on hand are shown. The forecast ages stock and projects recorded production and committed deliveries, using order deadlines as estimated delivery times. It excludes unrecorded work and future consumption, including meals and maintenance. Reserves are protected; delivery timing and plan execution are not guaranteed.</p>
    ${table("Inventory by location", ["Location", "Product", "On hand", "Usable now", "Reserve", "Production target",
      ...data.horizons.map((due, i) => `+${[6, 24, 48][i]}h · ${time(due)}`)], stockedInventory(data).map(row => [site(row.location),
      label(row.product), row.on_hand, row.usable, row.reserve, row.target, ...row.projected]), "No products on hand in this view.")}
    <h3>Forecast exceptions</h3>${table("Projected production and delivery shortages", ["Reference", "Type", "When", "Location", "Product", "Missing"],
      data.risks.map(risk => [risk.id, risk.kind, time(risk.minute), site(risk.location), label(risk.product), risk.missing]),
      "No modeled shortages in the next 48 simulated hours.")}
    <h3>Cargo in transit</h3>${table("Cargo in transit", ["Carrier", "Product", "Quantity", "Destination", "Order"],
      data.cargo.map(item => [resident(item.carrier), label(item.product), item.quantity, site(item.destination), item.order_id]), "No shipment cargo recorded.")}
  </section>
  <section id="orders" class="supply-section"><h2>Orders & requests</h2>
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
  <section id="production" class="supply-section"><h2>Production & bills of materials</h2>
    <p class="muted">Materials are the quantities recorded in each plan. An active batch lists known ingredients, not inferred quantities.</p>
    ${table("Planned production and active batches", ["Reference", "Worker", "Site", "Output", "Recorded materials", "To gather (included)", "Finish", "Status", "Planning basis"],
      data.production.map(item => [item.id, resident(item.worker), site(item.location), goods(item.outputs),
        item.ingredients.length ? item.ingredients.map(label).join(", ") : goods(item.inputs), goods(item.gathered),
        time(item.finish), label(item.status), item.basis]), "No recorded production plans or active batches.")}
  </section>
  <section id="maintenance" class="supply-section"><h2>Maintenance projects & jobs</h2>
    ${table("Structural projects", ["Project", "Site", "Status", "Contributions", "Due", "Blocker"],
      data.projects.map(project => [project.title, site(project.location), label(project.status), project.progress,
        time(project.due), project.blocker]), "No structural projects tracked.")}
    ${table("Maintenance jobs, including completed history", ["Job", "Assignee", "Site", "Status", "Work minutes", "Due", "Materials", "Blocker"],
      [...data.maintenance].sort((a, b) => Number(a.status === "completed") - Number(b.status === "completed")
        || b.due_at - a.due_at).map(job => [job.title, resident(job.assignee), site(job.location), label(job.status),
        `${job.worked_minutes} / ${job.required_minutes}`, time(job.due_at), goods(job.supplies), job.blocker]), "No routine maintenance jobs recorded.")}
  </section>
  <section id="notices" class="supply-section"><h2>Posted notes by location</h2>
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
  app.innerHTML = `<main><header><div><span class="eyebrow">NPCSIM / TOWN OPERATIONS</span>
    <h1>Town supply chain<span class="dot">.</span></h1></div><div class="header-right">${viewNavigation("supply")}</div></header>
    <div class="supply-tools" hidden>
      <label>Location <select id="supply-location" aria-controls="supply-content"></select></label>
      <label>Forecast product &amp; site <select id="supply-product" aria-controls="supply-forecast-chart"></select></label>
    </div>
    <p class="notice" role="alert" hidden></p><div id="supply-content"><p class="muted">Loading town snapshot…</p></div></main>`;
  const content = app.querySelector<HTMLDivElement>("#supply-content")!;
  const error = app.querySelector<HTMLParagraphElement>("[role=alert]")!;
  const tools = app.querySelector<HTMLDivElement>(".supply-tools")!;
  const locationFilter = app.querySelector<HTMLSelectElement>("#supply-location")!;
  const forecastFilter = app.querySelector<HTMLSelectElement>("#supply-product")!;
  let snapshot: Supply = null;
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
    tools.hidden = !snapshot;
    if (snapshot) {
      const sites = Object.entries(snapshot.sites).sort((a, b) => a[1].localeCompare(b[1]));
      setOptions(locationFilter, [["", "All locations"], ...sites]);
      const scoped = atLocation(snapshot, locationFilter.value);
      const products = forecastChoices(scoped).map((row): [string, string] => [stockKey(row), stockLabel(row, scoped)]);
      setOptions(forecastFilter, products.length ? products : [["", "No inventory here"]]);
      forecastFilter.disabled = products.length === 0;
    }
    const historyOpen = content.querySelector("details")?.open;
    const scrollable = ".supply-bars, .supply-attention, .supply-table";
    const regions = new Map(preserveScroll ? [...content.querySelectorAll<HTMLElement>(scrollable)]
      .map(region => [region.getAttribute("aria-label"), { left: region.scrollLeft, top: region.scrollTop }] as const) : []);
    content.innerHTML = townSupplyMarkup(snapshot, locationFilter.value, forecastFilter.value);
    const history = content.querySelector("details");
    if (history && historyOpen) history.open = true;
    if (preserveScroll) {
      for (const region of content.querySelectorAll<HTMLElement>(scrollable)) {
        const previousRegion = regions.get(region.getAttribute("aria-label"));
        if (previousRegion) region.scrollTo(previousRegion.left, previousRegion.top);
      }
    }
  };
  locationFilter.addEventListener("change", () => render());
  forecastFilter.addEventListener("change", () => render(true));
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
        if (!previous && location.hash) document.getElementById(location.hash.slice(1))?.scrollIntoView();
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
