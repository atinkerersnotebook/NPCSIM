import assert from "node:assert/strict";
import test from "node:test";
import { townSupplyMarkup, townSupplySchema } from "../src/town-supply.ts";
import { viewMode, viewNavigation } from "../src/view-mode.ts";

test("supply navigation opens the read-only dashboard and handles an absent town", () => {
  assert.equal(viewMode("?view=supply"), "supply");
  assert.match(viewNavigation("supply"), /href="\/\?view=supply" aria-current="page"/);
  assert.match(townSupplyMarkup(townSupplySchema.parse(null)), /Start or load a village adventure/);
});

test("tomorrow's upkeep is visible and scoped without becoming active work", () => {
  const data = townSupplySchema.parse({ title: "Millhaven", revision: 1, minute: 2235, horizons: [2595, 3675, 5115, 12315, 45435],
    sites: { cottages: "Village cottages", farm: "Farm", forge: "Forge" }, fixtures: {}, residents: { hobb: "Hobb", ada: "Ada" },
    inventory: [], orders: [], requests: [], needs: [], cargo: [], production: [], risks: [], maintenance: [], projects: [], notes: [],
    upcoming_maintenance: [
      { id: "preview:cottage", title: "Service cottage railings", assignee: "hobb", location: "cottages",
        day: 3, release_at: 3900, due_at: 4020, required_minutes: 30, supplies: {}, status: "upcoming" },
      { id: "preview:fence", title: "Inspect the farm fence", assignee: "ada", location: "farm",
        day: 3, release_at: 3420, due_at: 3960, required_minutes: 20, supplies: {}, status: "upcoming" },
    ],
  });
  const markup = townSupplyMarkup(data);
  assert.match(markup, /Tomorrow.s routine upkeep/);
  assert.match(markup, /Service cottage railings[\s\S]*Inspect the farm fence/);
  assert.doesNotMatch(markup, /preview:cottage[^>]*active work/);
});

test("posted notes are grouped by their physical site and escaped without hiding stale copies", () => {
  const data = townSupplySchema.parse({ title: "Millhaven", revision: 1, minute: 480, horizons: [840, 1920, 3360, 10560, 43680],
    sites: { square: "Village Square", farm: "Farm Site" }, fixtures: { board: "Board", box: "Inbox" }, residents: {},
    inventory: [], orders: [], requests: [], needs: [], cargo: [], production: [], risks: [], maintenance: [], projects: [],
    notes: [{ id: "notice-1", fixture: "board", location: "square", author: "jory", title: "Need flour",
      text: "<img src=x onerror=alert(1)>", minute: 480, version: 1, status: "posted", latest_version: 2, latest_status: "accepted" },
    { id: "notice-2", fixture: "box", location: "farm", author: "ada", title: "Grain pickup",
      text: "Ready at the farm", minute: 480, version: 1, status: "posted", latest_version: 1, latest_status: "posted" }] });
  const markup = townSupplyMarkup(data);
  assert.match(markup, /<h3>Farm Site<\/h3>[\s\S]*Grain pickup[\s\S]*<h3>Village Square<\/h3>[\s\S]*Need flour/);
  assert.match(markup, /Latest: v2 · accepted/);
  assert.match(markup, /&lt;img src=x/);
  assert.doesNotMatch(markup, /<img src=x/);
});

const inventory = (location, product, usable, reserve, projected) => ({
  location, product, usable, reserve, projected, on_hand: usable, target: null,
});
const order = (id, source, destination, deadline, status, open = true) => ({
  id, source, destination, deadline, status, open, product: "flour", quantity: 3, remaining: 3,
  supplier: "s", buyer: "b", delivered_quantity: 0, in_transit: 0, reason: null,
});
const overview = townSupplySchema.parse({ title: "Millhaven", revision: 1, minute: 480, horizons: [840, 1920, 3360, 10560, 43680],
  sites: { farm: "Farm Site", mill: "Mill", square: "Village Square" }, fixtures: {}, residents: {},
  inventory: [inventory("farm", "grain", 6, 2, [5, 4, 3, 3, 3]), inventory("mill", "flour", 1, 3, [0, 7, 2, 2, 2]),
    inventory("farm", "eggs", 2, 0, [2, 2, 2, 2, 2])],
  orders: [order("incoming", "farm", "mill", 479, "accepted"), order("outgoing", "mill", "square", 840, "accepted"),
    order("later", "mill", "farm", 841, "accepted"), order("elsewhere", "farm", "square", 510, "accepted"),
    order("closed", "mill", "farm", 470, "fulfilled", false)],
  maintenance: [{ id: "one", title: "Tighten belt", assignee: "worker", location: "mill", due_at: 480,
    worked_minutes: 0, required_minutes: 10, supplies: {}, status: "pending", blocker: null }],
  projects: [{ id: "two", title: "Rebuild axle", location: "mill", due: null, progress: 0, status: "blocked", blocker: "Need timber" },
    { id: "three", title: "Dig ditch", location: "farm", due: 470, progress: 1, status: "completed", blocker: null }],
  cargo: [{ carrier: "cart-driver", product: "barley", quantity: 1, destination: "square", order_id: null }],
  requests: [], needs: [], production: [], risks: [], notes: [],
});

test("Town supply separates operational sections and keeps overview links to their tabs", () => {
  const markup = townSupplyMarkup(overview);
  const panels = [...markup.matchAll(/<section id="([^"]+)"[^>]+role="tabpanel"/g)].map(match => match[1]);
  assert.deepEqual(panels, ["supply-overview", "inventory", "orders", "production", "maintenance", "notices"]);
  const overviewPanel = markup.split('id="supply-overview"')[1].split('id="inventory"')[0];
  for (const id of ["inventory", "orders", "maintenance"]) assert.ok(overviewPanel.includes(`href="#${id}"`));
});

test("stock chart puts shortages first and forecast uses the selected inventory row", () => {
  const markup = townSupplyMarkup(overview, "", JSON.stringify(["farm", "grain"]));
  const reserves = markup.split('id="supply-reserves"')[1].split('id="supply-forecast-chart"')[0];
  assert.match(reserves, /flour · Mill[\s\S]*2 below reserve[\s\S]*grain · Farm Site/);
  assert.doesNotMatch(reserves, /eggs/);
  const forecast = markup.split('id="supply-forecast-chart"')[1].split('</section>')[0];
  assert.match(forecast, /grain · Farm Site[\s\S]*Now<\/strong><span>On hand: <strong>6<\/strong>[\s\S]*\+6h<\/strong><span>With plans: <strong>5<\/strong>[\s\S]*\+24h<\/strong><span>With plans: <strong>4<\/strong>[\s\S]*\+48h<\/strong><span>With plans: <strong>3<\/strong>/);
  assert.match(forecast, /Projection without unstarted plans unavailable/);
  assert.doesNotMatch(forecast, /Without plans: <strong>/);
});

test("location scopes attention, metrics, and details while matching both order endpoints", () => {
  const markup = townSupplyMarkup(overview, "mill", JSON.stringify(["farm", "grain"]));
  const attention = markup.split('id="supply-attention"')[1].split('</section>')[0];
  assert.match(attention, /Needs attention \(5\)[\s\S]*1 below reserve now · 0 at reserve \(no buffer\) · 1 projected below reserve[\s\S]*Overdue[\s\S]*incoming[\s\S]*Blocked[\s\S]*Rebuild axle[\s\S]*Need timber[\s\S]*Due now[\s\S]*Tighten belt[\s\S]*Due soon[\s\S]*outgoing/);
  assert.doesNotMatch(attention, /later|elsewhere|closed|Dig ditch/);
  assert.match(markup, /<div><dt>Open orders<\/dt><dd>3/);
  assert.match(markup, /<div><dt>Blocked orders<\/dt><dd>0/);
  assert.match(markup, /<div class="supply-metric-alert"><dt>Below reserve now<\/dt><dd>1/);
  assert.match(markup, /Inventory forecast<\/h3>[\s\S]*flour · Mill/);
  assert.match(markup, /<td>later<\/td>/);
  assert.doesNotMatch(markup, /elsewhere|Dig ditch|barley|grain · Farm Site/);
});

test("zero stock at a configured reserve remains actionable; forecast shows the first breach and its drivers", () => {
  const data = { ...overview, inventory: [inventory("mill", "out-of-stock", 0, 3, [0, 7, 7]),
    { ...inventory("mill", "at-reserve", 2, 2, [2, 1, 4]), forecast_changes: [
      { production: 0, inbound: 0, inputs: 0, outbound: 0, expired: 0 },
      { production: 0, inbound: 0, inputs: 1, outbound: 0, expired: 0 },
      { production: 0, inbound: 3, inputs: 0, outbound: 0, expired: 0 },
    ] }] };
  const empty = townSupplyMarkup(data, "mill", JSON.stringify(["mill", "out-of-stock"]));
  assert.match(empty, /aria-pressed="true">out of stock · Mill/);
  assert.match(empty, /Below reserve now<\/dt><dd>1/);
  const markup = townSupplyMarkup(data, "mill", JSON.stringify(["mill", "at-reserve"]));
  assert.match(markup, /1 below reserve now · 1 at reserve \(no buffer\) · 2 projected below reserve/);
  assert.match(markup, /First projected below reserve: <strong>\+24h/);
  assert.match(markup, /−1 planned production inputs[\s\S]*\+3 expected deliveries/);
  assert.match(townSupplyMarkup(data, "square"), /No products to forecast in this view/);
});

test("forecast separates actual stock and both scenarios, warning when either falls below reserve", () => {
  const movement = { production: 0, inbound: 0, inputs: 0, outbound: 0, expired: 0 };
  const data = townSupplySchema.parse({ ...overview, inventory: [{ ...inventory("mill", "herbs", 5, 3, [8, 2, 4, 4, 4]),
    on_hand: 7, baseline_projected: [1, 4, 0, 0, 0], forecast_changes: [
      { ...movement, production: 1, harvest: 4, planned_production: 2 }, movement, movement, movement, movement,
    ] }] });
  const markup = townSupplyMarkup(data);
  assert.match(markup, /Below reserve without plans at \+6h/);
  assert.match(markup, /Below reserve at \+48h<\/dt><dd>1/);
  const chart = markup.split('id="supply-forecast-chart"')[1].split('</section>')[0];
  assert.match(chart, /aria-label="Forecast legend"[\s\S]*On hand[\s\S]*With unstarted plans[\s\S]*Without unstarted plans[\s\S]*Reserve/);
  assert.match(chart, /Reserve: 3[\s\S]*Now<\/strong><span>On hand: <strong>7<\/strong><\/span><span>Usable: <strong>5/);
  assert.match(chart, /\+6h<\/strong><span>With plans: <strong>8<\/strong><\/span>\s*<span>Without plans: <strong>1/);
  assert.match(chart, /First projected below reserve with plans: <strong>\+24h/);
  assert.match(chart, /First projected below reserve without plans: <strong>\+6h/);
  assert.match(chart, /\+1 started production[\s\S]*class="supply-driver planned_production">\+2 planned production[\s\S]*class="supply-driver harvest">\+4 planned harvest \/ collection/);
  const inventoryTable = markup.split('aria-label="Inventory by location"')[1].split('</table>')[0];
  assert.match(inventoryTable, /\+6h · [^<]+ with plans<\/th><th scope="col">\+6h · [^<]+ without plans/);
  assert.match(inventoryTable, /<td>8<\/td><td>1<\/td><td>2<\/td><td>4<\/td><td>4<\/td><td>0<\/td>/);
});

test("waterfall diagram shows downstream demand and blocked upstream inputs", () => {
  const data = townSupplySchema.parse({ ...overview,
    waterfall: { horizons: [840, 1920, 3360, 10560, 43680], requirements: [{
      id: "standing:bakery:bread:0", day: 1, worker: "jory", site: "bakery", product: "bread",
      planned_output: 8, finish: 510, status: "blocked", basis: "Flour unavailable", on_hand: 4,
      target: 8, inputs: { flour: 8 }, blocked_by: [{ id: "standing:bakery:bread:0", kind: "production input",
        minute: 510, location: "bakery", product: "flour", missing: 8 }] }] } });
  const markup = townSupplyMarkup(data);
  assert.match(markup, /48-hour supply waterfall/);
  assert.match(markup, /bread[\s\S]*blocked[\s\S]*8 flour/);
});

test("prepared meals distinguish breakfast, supper and unallocated portions", () => {
  const data = townSupplySchema.parse({ ...overview, inventory: [{
    ...inventory("tavern", "meals", 11, 0, [0, 0, 0, 0, 0]),
    meal_allocations: { prepared: 11, unallocated: 1, services: [
      { id: "0:breakfast", service: "breakfast", due: 510, required: 6, reserved: 6, shortfall: 0 },
      { id: "0:supper", service: "supper", due: 1140, required: 8, reserved: 4, shortfall: 4 },
    ] }
  }] });
  const markup = townSupplyMarkup(data, "", JSON.stringify(["tavern", "meals"]));
  assert.match(markup, /Prepared meal service allocation[\s\S]*11<\/strong> prepared · <strong>1<\/strong> unallocated/);
  assert.match(markup, /Breakfast[\s\S]*6\/6 reserved/);
  assert.match(markup, /Supper[\s\S]*4\/8 reserved[\s\S]*4 short/);
  const table = markup.split('aria-label="Inventory by location"')[1].split('</table>')[0];
  assert.match(table, /meals: breakfast[\s\S]*meals: supper[\s\S]*meals: unallocated/);
});

test("replenishment shows planned and blocked work under production and respects the site filter", () => {
  const data = townSupplySchema.parse({ ...overview, residents: { iona: "Iona", ada: "Ada", rowan: "Rowan" }, replenishment: [
    { id: "herbs", worker: "iona", location: "square", product: "herbs", quantity: 3, finish: 540, status: "planned", basis: "Local reserve" },
    { id: "eggs-and-milk", worker: "ada", location: "farm", product: "eggs_and_milk", quantity: 4, finish: 600, status: "planned", basis: "Local reserve" },
    { id: "wood", worker: "rowan", location: "mill", product: "wood", quantity: 0, finish: null, status: "blocked", basis: "No <dry> wood" },
  ] });
  const markup = townSupplyMarkup(data);
  const production = markup.split('id="production"')[1].split('</section>')[0];
  assert.match(production, /Production &amp; materials \(3\)[\s\S]*Internal replenishment tasks/);
  assert.match(production, /<td>Iona<\/td><td>Village Square<\/td><td>herbs<\/td><td>3<\/td><td>[^<]+<\/td><td>planned/);
  assert.match(production, /<td>Ada<\/td><td>Farm Site<\/td><td>eggs and milk<\/td>/);
  const mill = townSupplyMarkup(data, "mill").split('id="production"')[1].split('</section>')[0];
  assert.match(mill, /Production &amp; materials \(1\)[\s\S]*<td>Rowan<\/td><td>Mill<\/td><td>wood<\/td><td>—<\/td><td>Unscheduled<\/td><td>blocked<\/td><td>No &lt;dry&gt; wood/);
  assert.doesNotMatch(mill, /Iona|Ada/);
});

test("overview caps its stock preview without hiding the full inventory", () => {
  const data = { ...overview, inventory: Array.from({ length: 7 }, (_, i) => inventory("mill", `product_${i}`, i + 1, 8, [8, 8, 8])) };
  const markup = townSupplyMarkup(data, "mill");
  const preview = markup.split('aria-label="Stock and reserve comparison"')[1].split('</ul>')[0];
  assert.match(preview, /product 0 · Mill/);
  assert.doesNotMatch(preview, /product 6 · Mill/);
  assert.match(markup, /Full inventory \(7\)<\/a> · Showing 5 of 7/);
  assert.match(markup.split('id="inventory"')[1], /data-forecast-key[^>]+>product 6<\/button>/);
});
