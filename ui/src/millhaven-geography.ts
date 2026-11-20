export const townMapWidth = 1516;
export const townMapHeight = 1037;

// Pixel anchors on the original illustration, not simulated indoor coordinates.
export const townMapAnchors = {
  "village-square": { x: 745, y: 598 },
  forge: { x: 489, y: 500 },
  stable: { x: 425, y: 358 },
  watermill: { x: 1312, y: 450 },
  "mill-shed": { x: 1444, y: 471 },
  farm: { x: 873, y: 289 },
  "general-store": { x: 737, y: 464 },
  workshop: { x: 489, y: 655 },
  tavern: { x: 848, y: 848 },
  bakery: { x: 950, y: 641 },
  cottages: { x: 409, y: 826 },
  "herbal-workshop": { x: 536, y: 818 },
  woods: { x: 1370, y: 257 },
  bridge: { x: 1125, y: 568 },
  riverbank: { x: 1143, y: 308 },
  "tavern-yard": { x: 1084, y: 814 },
  "supply-depot": { x: 995, y: 524 },
  "communal-granary": { x: 948, y: 510 },
  "farm-feed-store": { x: 879, y: 402 },
  "north-fields": { x: 770, y: 150 },
  "north-pasture": { x: 634, y: 203 },
  "farm-cistern": { x: 799, y: 224 },
  "communal-kitchen": { x: 911, y: 776 },
  "iron-mine": { x: 158, y: 378 }
} as const;

export const townMapFixtureAnchors = {
  "ore-loading-dock": { x: 274, y: 523 },
  "forge-woodshed": { x: 340, y: 491 },
  "depot-woodshed": { x: 1020, y: 552 },
  "bakery-woodshed": { x: 1132, y: 645 },
  "tavern-woodshed": { x: 960, y: 822 },
  "cottage-woodshed": { x: 330, y: 790 },
  "village-pump": { x: 745, y: 620 },
  noticeboard: { x: 681, y: 612 },
  "maintenance-board": { x: 770, y: 606 }
} as const;
