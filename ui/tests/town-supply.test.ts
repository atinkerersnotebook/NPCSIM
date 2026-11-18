import assert from "node:assert/strict";
import test from "node:test";
import { townSupplyMarkup, townSupplySchema } from "../src/town-supply.ts";
import { viewMode, viewNavigation } from "../src/view-mode.ts";

test("supply navigation opens the read-only dashboard and handles an absent town", () => {
  assert.equal(viewMode("?view=supply"), "supply");
  assert.match(viewNavigation("supply"), /href="\/\?view=supply" aria-current="page"/);
  assert.match(townSupplyMarkup(townSupplySchema.parse(null)), /Start or load a village adventure/);
});

test("posted notes are grouped by their physical site and escaped without hiding stale copies", () => {
  const data = townSupplySchema.parse({ title: "Millhaven", revision: 1, minute: 480, horizons: [840, 1920, 3360],
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
const overview = townSupplySchema.parse({ title: "Millhaven", revision: 1, minute: 480, horizons: [840, 1920, 3360],
  sites: { farm: "Farm Site", mill: "Mill", square: "Village Square" }, fixtures: {}, residents: {},
  inventory: [inventory("farm", "grain", 6, 2, [5, 4, 3]), inventory("mill", "flour", 1, 3, [0, 7, 2]),
    inventory("farm", "eggs", 2, 0, [2, 2, 2])],
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

test("stock chart puts shortages first and forecast uses the selected inventory row", () => {
  const markup = townSupplyMarkup(overview, "", JSON.stringify(["farm", "grain"]));
  const reserves = markup.split('id="supply-reserves"')[1].split('id="supply-forecast-chart"')[0];
  assert.match(reserves, /flour · Mill[\s\S]*2 below reserve[\s\S]*grain · Farm Site/);
  assert.doesNotMatch(reserves, /eggs/);
  const forecast = markup.split('id="supply-forecast-chart"')[1].split('<dl')[0];
  assert.match(forecast, /grain · Farm Site[\s\S]*<strong>6<\/strong>[\s\S]*Now[\s\S]*<strong>5<\/strong>[\s\S]*\+6h[\s\S]*<strong>4<\/strong>[\s\S]*\+24h[\s\S]*<strong>3<\/strong>[\s\S]*\+48h/);
});

test("location scopes attention, metrics, and details while matching both order endpoints", () => {
  const markup = townSupplyMarkup(overview, "mill", JSON.stringify(["farm", "grain"]));
  const attention = markup.split('id="supply-attention"')[1].split('class="supply-chart-grid"')[0];
  assert.match(attention, /Needs attention \(4\)[\s\S]*Overdue[\s\S]*incoming[\s\S]*Blocked[\s\S]*Rebuild axle[\s\S]*Need timber[\s\S]*Due now[\s\S]*Tighten belt[\s\S]*Due soon[\s\S]*outgoing/);
  assert.doesNotMatch(attention, /later|elsewhere|closed|Dig ditch/);
  assert.match(markup, /Open orders<\/dt><dd>3/);
  assert.match(markup, /Inventory forecast<\/h3>[\s\S]*flour · Mill/);
  assert.match(markup, /<td>later<\/td>/);
  assert.doesNotMatch(markup, /elsewhere|Dig ditch|barley|grain · Farm Site/);
});

test("inventory hides zero on-hand rows even with inbound stock, without hiding shortages", () => {
  const data = { ...overview, inventory: [inventory("mill", "out-of-stock", 0, 3, [0, 7, 7]),
    { ...inventory("mill", "expired-stock", 2, 0, [0, 0, 0]), usable: 0 }] };
  const markup = townSupplyMarkup(data, "mill", JSON.stringify(["mill", "out-of-stock"]));
  assert.doesNotMatch(markup, /out of stock/);
  assert.match(markup, /Inventory forecast<\/h3>[\s\S]*expired stock · Mill/);
  assert.match(markup, /<td>expired stock<\/td><td>2<\/td><td>0<\/td>/);
  assert.match(markup, /Below reserve now<\/dt><dd>1/);
  assert.match(townSupplyMarkup(data, "square"), /No products on hand in this view/);
});
