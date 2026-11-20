import { escape } from "./html.ts";
import { groundFill, groundPatterns } from "./millhaven-ground.ts";
import { townMapAnchors, townMapHeight, townMapWidth } from "./millhaven-geography.ts";

const tileSize = 56;
const halfTileWidth = 76;
const halfTileHeight = halfTileWidth / 2;
const columns = Math.ceil(townMapWidth / tileSize);
const rows = Math.ceil(townMapHeight / tileSize);
const horizon = 160;
export const sceneWidth = (columns + rows) * halfTileWidth + 120;
export const sceneHeight = horizon + (columns + rows) * halfTileHeight + 70;
const originX = (sceneWidth - (columns - rows) * halfTileWidth) / 2;

function project(x: number, y: number): [number, number] {
  return [originX + (x - y) * halfTileWidth, horizon + (x + y) * halfTileHeight];
}

type Site = keyof typeof townMapAnchors;
function site(id: Site): [number, number] {
  const { x, y } = townMapAnchors[id];
  return [x / tileSize, y / tileSize];
}

export const sceneAnchors = Object.fromEntries(Object.keys(townMapAnchors).map(key => {
  const [x, y] = project(...site(key as Site));
  return [key, { x, y }];
}));

const polygon = (points: number[][], fill: string, stroke = "none") =>
  `<polygon points="${points.map(point => point.join(",")).join(" ")}" fill="${fill}" stroke="${stroke}" stroke-linejoin="round"/>`;
const ground = (x: number, y: number, w: number, d: number, color: string) =>
  polygon([project(x, y), project(x + w, y), project(x + w, y + d), project(x, y + d)], color);

type GroundPoint = [number, number];

function roadFootprint(points: readonly GroundPoint[], width: number, fill: string) {
  const half = width / 2;
  return points.slice(1).map(([x, y], i) => {
    const [fromX, fromY] = points[i];
    return ground(Math.min(x, fromX) - half, Math.min(y, fromY) - half,
      Math.abs(x - fromX) + width, Math.abs(y - fromY) + width, fill);
  }).join("");
}

function roadRoute(waypoints: readonly GroundPoint[]): GroundPoint[] {
  return waypoints.flatMap(([x, y], i): GroundPoint[] => {
    if (i === 0) return [[x, y]];
    const [fromX, fromY] = waypoints[i - 1];
    if (fromX === x || fromY === y) return [[x, y]];
    if (Math.abs(x - fromX) >= Math.abs(y - fromY)) {
      const middle = (fromX + x) / 2;
      return [[middle, fromY], [middle, y], [x, y]];
    }
    const middle = (fromY + y) / 2;
    return [[fromX, middle], [x, middle], [x, y]];
  });
}

function trail(points: GroundPoint[], color: string, width: number) {
  const projected = points.map(([x, y]) => project(x, y).join(",")).join(" ");
  return `<polyline points="${projected}" fill="none" stroke="${color}" stroke-width="${width}" stroke-linejoin="round" stroke-linecap="round"/>`;
}

const roads: Site[][] = [
  ["iron-mine", "stable", "forge", "village-square", "bakery", "bridge", "watermill", "mill-shed"],
  ["forge", "workshop", "cottages", "herbal-workshop", "tavern", "tavern-yard"],
  ["tavern", "bakery", "village-square", "general-store", "farm-feed-store", "farm"],
  ["farm", "north-fields", "north-pasture"], ["farm", "farm-cistern"],
  ["general-store", "communal-granary", "supply-depot", "bridge"], ["bridge", "riverbank"],
  ["watermill", "woods"], ["tavern", "communal-kitchen"]
];

const artwork = {
  inn: new URL("./assets/scene-inn.png", import.meta.url).href,
  forge: new URL("./assets/scene-forge.png", import.meta.url).href,
  cottage: new URL("./assets/scene-cottage.png", import.meta.url).href,
  barn: new URL("./assets/scene-barn.png", import.meta.url).href,
  mill: new URL("./assets/scene-mill.png", import.meta.url).href,
  trees: new URL("./assets/scene-trees.png", import.meta.url).href
};

function prop(kind: keyof typeof artwork, x: number, y: number, width: number, height: number, shade = 0) {
  const [a, b] = project(x, y);
  return `<ellipse cx="${a + width * .09}" cy="${b - 3}" rx="${width * .43}" ry="${width * .13}" fill="#182019" opacity=".48" filter="url(#scene-shadow)"/>
    <image href="${escape(artwork[kind])}" x="${a - width / 2}" y="${b - height}" width="${width}" height="${height}" style="filter:brightness(${1 - shade})"/>`;
}

// Move the artwork onto lots beside the road without moving the NPC/location anchor.
const lotOffsets: Partial<Record<Site, [number, number]>> = {
  stable: [.50, -.48], "mill-shed": [-.05, -.68],
  workshop: [-.66, -.28], "herbal-workshop": [-.25, -.15],
  "communal-granary": [-.10, -.80], "supply-depot": [.55, .10],
  "farm-feed-store": [.25, -1.60], "farm-cistern": [.80, .20],
  "communal-kitchen": [.45, -.10]
};

const buildings: [Site, keyof typeof artwork, number, number, number?][] = [
  ["forge", "forge", 151, 155], ["stable", "barn", 147, 122, .06], ["watermill", "mill", 170, 192],
  ["mill-shed", "barn", 93, 79, .13], ["general-store", "cottage", 139, 138],
  ["workshop", "forge", 114, 118, .10], ["tavern", "inn", 188, 188], ["bakery", "cottage", 130, 130, .08],
  ["cottages", "cottage", 137, 137, .11], ["herbal-workshop", "cottage", 117, 117],
  ["communal-granary", "barn", 128, 104], ["supply-depot", "barn", 104, 88],
  ["communal-kitchen", "cottage", 104, 104], ["farm-feed-store", "barn", 89, 73],
  ["farm-cistern", "barn", 78, 60, .08], ["iron-mine", "forge", 126, 128, .22]
];

function buildTerrain(): string {
  const tiles = [];
  const scenery: { depth: number; markup: string }[] = [];
  const [fx, fy] = site("farm");
  const [sx, sy] = site("village-square");
  const farmX = Math.floor(fx);
  const farmY = Math.floor(fy);
  const [wx, wy] = site("woods");
  for (let x = 0; x < columns; x++) for (let y = 0; y < rows; y++) {
    const mix = (x * 53 + y * 97 + x * y * 11) % 29;
    const field = x >= farmX - 2 && x < farmX && y >= farmY - 1 && y <= farmY;
    const wooded = Math.hypot(x - wx, y - wy) < 3;
    const surface = field ? "furrows" : wooded || mix < 5 ? "moss" : mix < 22 ? "grass" : "meadow";
    tiles.push(ground(x, y, 1.012, 1.012, groundFill(surface)));
    if ((x < 2 || y < 2 || x >= columns - 1 || y >= rows - 1 || Math.hypot(x - wx, y - wy) < 2)
      && mix % 3 === 0) {
      const size = 72 + mix * 3;
      scenery.push({ depth: x + y, markup: prop("trees", x + .45, y + .5, size, size * 1.1, mix % 5 * .035) });
    }
  }
  const plaza = ground(sx - .64, sy - .64, 1.28, 1.28, "#665d4b")
    + ground(sx - .57, sy - .57, 1.14, 1.14, groundFill("stone"));
  const [bx, by] = site("bridge");
  const [rx, ry] = site("riverbank");
  const [mx, my] = site("watermill");
  const river: [number, number][] = [[rx + 1.1, -.3], [rx + .65, ry], [mx - .7, my - .45],
    [bx, by], [bx + .48, by + 3], [bx + 1.7, rows + .4]];
  const roadPaths = roads.map(road => roadRoute(road.map(site)));
  const water = trail(river, "#485d46", 62) + trail(river, "#777d58", 53) + trail(river, "#345b59", 47)
    + trail(river, groundFill("water"), 39);
  const crossing: GroundPoint[] = [[bx - .49, by], [bx + .49, by]];
  const bridge = roadFootprint(crossing, .31, "#604733") + roadFootprint(crossing, .25, groundFill("planks"));
  const approaches: GroundPoint[][] = [];
  for (const [id, kind, width, height, shade] of buildings) {
    const [x, y] = site(id);
    const [dx, dy] = lotOffsets[id] ?? [-.32, -.31];
    const lotX = x + dx;
    const lotY = y + dy;
    approaches.push(roadRoute([[x, y], [lotX, lotY]]));
    scenery.push({ depth: lotX + lotY, markup: prop(kind, lotX, lotY, width, height, shade) });
  }
  const streets = `<g class="scene-roads">
    ${roadPaths.map(road => roadFootprint(road, .36, "#7b6648")).join("")}
    ${approaches.map(path => roadFootprint(path, .21, "#7b6648")).join("")}
    ${roadPaths.map(road => roadFootprint(road, .28, groundFill("dirt"))).join("")}
    ${approaches.map(path => roadFootprint(path, .15, groundFill("dirt"))).join("")}</g>`;
  return `<svg class="scene-terrain" viewBox="0 0 ${sceneWidth} ${sceneHeight}" width="${sceneWidth}" height="${sceneHeight}" role="img" aria-label="Isometric Millhaven with timber buildings, woodland, fields and a river">
    <defs>
      <filter id="scene-shadow" x="-30%" y="-60%" width="160%" height="220%"><feGaussianBlur stdDeviation="3"/></filter>
      <radialGradient id="scene-base"><stop stop-color="#526742"/><stop offset="1" stop-color="#344635"/></radialGradient>
      ${groundPatterns(`matrix(${halfTileWidth} ${halfTileHeight} ${-halfTileWidth} ${halfTileHeight} ${originX} ${horizon})`)}
    </defs>
    <rect width="100%" height="100%" fill="url(#scene-base)"/>
    ${tiles.join("")}${streets}${plaza}${water}<g class="scene-bridge">${bridge}</g>
    ${scenery.sort((a, b) => a.depth - b.depth).map(item => item.markup).join("")}</svg>`;
}

export const sceneTerrain = buildTerrain();
