import { escape } from "./html.ts";
import type { townMapAnchors } from "./millhaven-geography.ts";

const image = new URL("./assets/millhaven-map.png", import.meta.url).href;
export const sceneWidth = 5692;
export const sceneHeight = 4420;
const landmarkReferenceWidth = 989;
const landmarkReferenceHeight = 768;

// Scene-only marker positions measured on the 989 × 768 orthographic artwork.
// They intentionally do not derive from townMapAnchors: the two illustrations
// share world locations but place their visible landmarks differently.
const landmarkPixels = {
  "village-square": [438, 462],
  forge: [268, 352],
  stable: [289, 289],
  watermill: [787, 432],
  "mill-shed": [880, 432],
  farm: [614, 293],
  "general-store": [438, 346],
  workshop: [225, 409],
  tavern: [459, 575],
  bakery: [622, 504],
  cottages: [235, 484],
  "herbal-workshop": [184, 576],
  woods: [851, 318],
  bridge: [685, 416],
  riverbank: [746, 310],
  "tavern-yard": [584, 583],
  "supply-depot": [605, 428],
  "communal-granary": [590, 410],
  "farm-feed-store": [650, 310],
  "north-fields": [560, 190],
  "north-pasture": [505, 175],
  "farm-cistern": [655, 315],
  "communal-kitchen": [520, 600],
  "iron-mine": [140, 334]
} satisfies Record<keyof typeof townMapAnchors, [number, number]>;

export const sceneAnchors = Object.fromEntries(Object.entries(landmarkPixels).map(([id, [x, y]]) => [id, {
  x: x / landmarkReferenceWidth * sceneWidth,
  y: y / landmarkReferenceHeight * sceneHeight
}]));

export const sceneTerrain = `<img class="scene-terrain" src="${escape(image)}" width="${sceneWidth}" height="${sceneHeight}" alt="Orthographic illustration of Millhaven" draggable="false">`;
