import { escape } from "./html.ts";

const suppliedArt: Record<string, { head: string; portrait: string; body: string }> = {
  torvin: { head: new URL("./assets/torvin-head.png", import.meta.url).href,
    portrait: new URL("./assets/torvin-torso.png", import.meta.url).href, body: new URL("./assets/torvin-body.png", import.meta.url).href },
  bren: { head: new URL("./assets/bren-head.png", import.meta.url).href,
    portrait: new URL("./assets/bren-torso.png", import.meta.url).href, body: new URL("./assets/bren-body.png", import.meta.url).href },
  mara: { head: new URL("./assets/mara-head.png", import.meta.url).href,
    portrait: new URL("./assets/mara-torso.png", import.meta.url).href, body: new URL("./assets/mara-body.png", import.meta.url).href },
  elsie: { head: new URL("./assets/elsie-head.png", import.meta.url).href,
    portrait: new URL("./assets/elsie-torso.png", import.meta.url).href, body: new URL("./assets/elsie-body.png", import.meta.url).href },
  hobb: { head: new URL("./assets/hobb-head.png", import.meta.url).href,
    portrait: new URL("./assets/hobb-torso.png", import.meta.url).href, body: new URL("./assets/hobb-body.png", import.meta.url).href },
  jory: { head: new URL("./assets/jory-head.png", import.meta.url).href,
    portrait: new URL("./assets/jory-torso.png", import.meta.url).href, body: new URL("./assets/jory-body.png", import.meta.url).href },
  ada: { head: new URL("./assets/ada-head.png", import.meta.url).href,
    portrait: new URL("./assets/ada-torso.png", import.meta.url).href, body: new URL("./assets/ada-body.png", import.meta.url).href },
  nella: { head: new URL("./assets/nella-head.png", import.meta.url).href,
    portrait: new URL("./assets/nella-torso.png", import.meta.url).href, body: new URL("./assets/nella-body.png", import.meta.url).href },
  rowan: { head: new URL("./assets/rowan-head.png", import.meta.url).href,
    portrait: new URL("./assets/rowan-torso.png", import.meta.url).href, body: new URL("./assets/rowan-body.png", import.meta.url).href },
  garrick: { head: new URL("./assets/garrick-head.png", import.meta.url).href,
    portrait: new URL("./assets/garrick-torso.png", import.meta.url).href, body: new URL("./assets/garrick-body.png", import.meta.url).href },
  iona: { head: new URL("./assets/iona-head.png", import.meta.url).href,
    portrait: new URL("./assets/iona-torso.png", import.meta.url).href, body: new URL("./assets/iona-body.png", import.meta.url).href },
  finn: { head: new URL("./assets/finn-head.png", import.meta.url).href,
    portrait: new URL("./assets/finn-torso.png", import.meta.url).href, body: new URL("./assets/finn-body.png", import.meta.url).href },
  "iron-trader": { head: new URL("./assets/iron-trader-head.png", import.meta.url).href,
    portrait: new URL("./assets/iron-trader-torso.png", import.meta.url).href, body: new URL("./assets/iron-trader-body.png", import.meta.url).href },
  "general-trader": { head: new URL("./assets/general-trader-head.png", import.meta.url).href,
    portrait: new URL("./assets/general-trader-torso.png", import.meta.url).href, body: new URL("./assets/general-trader-body.png", import.meta.url).href },
  "pack-trader": { head: new URL("./assets/pack-trader-head.png", import.meta.url).href,
    portrait: new URL("./assets/pack-trader-torso.png", import.meta.url).href, body: new URL("./assets/pack-trader-body.png", import.meta.url).href },
  "visitor-man": { head: new URL("./assets/visitor-man-head.png", import.meta.url).href,
    portrait: new URL("./assets/visitor-man-torso.png", import.meta.url).href, body: new URL("./assets/visitor-man-body.png", import.meta.url).href },
  "visitor-woman": { head: new URL("./assets/visitor-woman-head.png", import.meta.url).href,
    portrait: new URL("./assets/visitor-woman-torso.png", import.meta.url).href, body: new URL("./assets/visitor-woman-body.png", import.meta.url).href }
};

function artFor(id: string) {
  if (id.startsWith("visitor:visiting-merchant:")) return suppliedArt["general-trader"];
  if (id.startsWith("visitor:passing-traveller:")) return suppliedArt["visitor-woman"];
  return suppliedArt[id];
}

type Palette = { hair: string; coat: string; trim: string; skin: string; accessory: string; style: number };

const portraits: Record<string, Palette> = {
  torvin: { hair: "#49352f", coat: "#575963", trim: "#d8a366", skin: "#bd8464", accessory: "#bac4c3", style: 0 },
  bren: { hair: "#b98750", coat: "#69735a", trim: "#d8b27b", skin: "#e5af85", accessory: "#78523e", style: 1 },
  mara: { hair: "#503a30", coat: "#4e7582", trim: "#d1c5a5", skin: "#b97d61", accessory: "#e4d8b7", style: 2 },
  elsie: { hair: "#9b623e", coat: "#874d61", trim: "#e6c688", skin: "#e7aa81", accessory: "#e8d9b0", style: 3 },
  hobb: { hair: "#77716b", coat: "#715a46", trim: "#c6a37a", skin: "#c39071", accessory: "#b5b5ad", style: 4 },
  jory: { hair: "#533a35", coat: "#bd8052", trim: "#efe1bf", skin: "#d69a72", accessory: "#f1e7cd", style: 5 },
  ada: { hair: "#694736", coat: "#779054", trim: "#dec485", skin: "#bd805d", accessory: "#d6b471", style: 6 },
  nella: { hair: "#322d3b", coat: "#615583", trim: "#d4ad6e", skin: "#d9a17c", accessory: "#e4bb70", style: 7 },
  rowan: { hair: "#5c4535", coat: "#476b50", trim: "#a5ac6d", skin: "#d0a079", accessory: "#8c6846", style: 8 },
  garrick: { hair: "#37383d", coat: "#707f89", trim: "#c5ced0", skin: "#ad765c", accessory: "#c3cdd0", style: 9 },
  iona: { hair: "#b3a5a0", coat: "#597b70", trim: "#b4d2a9", skin: "#c99578", accessory: "#a7c481", style: 10 },
  finn: { hair: "#9a6540", coat: "#52768a", trim: "#c6ab83", skin: "#e2af85", accessory: "#acc5c2", style: 11 }
};

const fallback = Object.values(portraits);

function paletteFor(id: string): Palette {
  const hash = [...id].reduce((value, char) => (value * 31 + char.charCodeAt(0)) >>> 0, 0);
  return portraits[id] ?? fallback[hash % fallback.length];
}

const pixel = (x: number, y: number, w: number, h: number, fill: string) =>
  `<rect x="${x}" y="${y}" width="${w}" height="${h}" fill="${fill}"/>`;

function tint(color: string, toward: string, fraction: number): string {
  return `#${[1, 3, 5].map(index => Math.round(
    parseInt(color.slice(index, index + 2), 16) * (1 - fraction)
    + parseInt(toward.slice(index, index + 2), 16) * fraction
  ).toString(16).padStart(2, "0")).join("")}`;
}

export function npcSprite(id: string, fullBody = false): string {
  const art = artFor(id);
  const image = fullBody ? art?.body : art?.head;
  if (image) {
    return `<img class="npc-sprite npc-sprite-image" src="${image}" alt="" aria-hidden="true" data-sprite="${escape(id)}">`;
  }
  const { hair, coat, trim, skin, accessory, style } = paletteFor(id);
  const outline = "#292832";
  const shapes = [
    pixel(4, 27, 4, 3, outline), pixel(12, 27, 4, 3, outline),
    pixel(5, 23, 3, 5, "#373543"), pixel(12, 23, 3, 5, "#373543"),
    pixel(2, 15, 16, 10, outline), pixel(3, 16, 14, 8, coat),
    pixel(3, 16, 2, 6, trim), pixel(15, 16, 2, 6, trim),
    pixel(8, 16, 4, 8, trim), pixel(9, 17, 2, 5, coat),
    pixel(1, 18, 2, 5, skin), pixel(17, 18, 2, 5, skin),
    pixel(7, 13, 6, 4, skin), pixel(5, 5, 10, 10, outline),
    pixel(6, 6, 8, 8, skin), pixel(4, 7, 2, 6, hair), pixel(14, 7, 2, 6, hair),
    pixel(7, 10, 2, 1, outline), pixel(12, 10, 2, 1, outline),
    pixel(9, 13, 2, 1, "#8c554c"),
    pixel(5, 4, 10, 3, hair), pixel(6, 3, 8, 2, hair)
  ];
  switch (style) {
    case 0: // smith's apron and beard
      shapes.push(pixel(7, 13, 6, 2, hair), pixel(7, 19, 6, 6, accessory), pixel(9, 21, 2, 2, outline)); break;
    case 1: // stable keeper's cap and scarf
      shapes.push(pixel(4, 4, 12, 2, accessory), pixel(7, 2, 7, 2, accessory), pixel(6, 15, 9, 2, trim)); break;
    case 2: // miller's kerchief
      shapes.push(pixel(5, 3, 10, 3, accessory), pixel(13, 5, 3, 5, accessory), pixel(7, 17, 6, 4, accessory)); break;
    case 3: // innkeeper's pinned hair and apron
      shapes.push(pixel(3, 6, 3, 5, hair), pixel(14, 6, 3, 5, hair), pixel(13, 4, 2, 2, accessory), pixel(7, 18, 6, 6, accessory)); break;
    case 4: // carpenter's headband and tool belt
      shapes.push(pixel(5, 5, 10, 2, accessory), pixel(4, 21, 12, 2, trim), pixel(13, 20, 2, 4, accessory)); break;
    case 5: // baker's toque and white apron
      shapes.push(pixel(5, 1, 10, 5, accessory), pixel(4, 3, 3, 3, accessory), pixel(13, 3, 3, 3, accessory), pixel(7, 17, 6, 7, accessory)); break;
    case 6: // farmer's straw hat
      shapes.push(pixel(2, 5, 16, 2, accessory), pixel(5, 2, 10, 3, accessory), pixel(7, 18, 6, 2, trim)); break;
    case 7: // merchant's circlet and brooch
      shapes.push(pixel(5, 5, 10, 1, accessory), pixel(10, 4, 2, 2, accessory), pixel(13, 17, 2, 2, accessory)); break;
    case 8: // forester's hood and satchel
      shapes.push(pixel(3, 4, 3, 11, coat), pixel(14, 4, 3, 11, coat), pixel(5, 3, 10, 2, coat), pixel(13, 19, 4, 5, accessory)); break;
    case 9: // guard's helm and breastplate
      shapes.push(pixel(4, 3, 12, 4, accessory), pixel(3, 6, 3, 7, accessory), pixel(14, 6, 3, 7, accessory), pixel(7, 18, 6, 5, accessory)); break;
    case 10: // healer's braid and herb pouch
      shapes.push(pixel(14, 6, 2, 13, hair), pixel(15, 17, 2, 5, hair), pixel(12, 20, 4, 4, accessory)); break;
    case 11: // fisher's wool cap and shoulder strap
      shapes.push(pixel(4, 4, 12, 3, accessory), pixel(6, 2, 8, 2, accessory), pixel(12, 16, 2, 8, trim)); break;
  }
  return `<svg class="npc-sprite" viewBox="0 0 20 30" xmlns="http://www.w3.org/2000/svg" aria-hidden="true" focusable="false" data-sprite="${escape(id)}">${shapes.join("")}</svg>`;
}

export function npcPortrait(id: string): string {
  const portrait = artFor(id)?.portrait;
  if (portrait) {
    return `<img class="npc-portrait npc-portrait-image" src="${portrait}" alt="" aria-hidden="true" data-portrait="${escape(id)}">`;
  }
  const { hair, coat, trim, skin, accessory, style } = paletteFor(id);
  const outline = "#292832";
  const shade = tint(skin, "#49373d", 0.35);
  const light = tint(skin, "#ffe7b5", 0.28);
  const hairLight = tint(hair, "#eac890", 0.28);
  const coatShade = tint(coat, outline, 0.35);
  const shapes = [
    pixel(0, 0, 40, 48, "#263b3b"),
    pixel(3, 3, 34, 42, "#354848"), pixel(5, 5, 30, 38, "#3f5050"),
    pixel(3, 3, 12, 2, "#718378"), pixel(3, 3, 2, 14, "#718378"),
    pixel(25, 43, 12, 2, "#172b2e"), pixel(35, 31, 2, 14, "#172b2e"),
    pixel(6, 8, 2, 2, "#c1ad79"), pixel(33, 12, 2, 2, "#c1ad79"),
    pixel(3, 37, 34, 11, outline), pixel(6, 34, 28, 12, coat),
    pixel(7, 37, 5, 9, coatShade), pixel(28, 37, 5, 9, coatShade),
    pixel(2, 39, 5, 9, coat), pixel(33, 39, 5, 9, coat),
    pixel(7, 37, 4, 9, trim), pixel(29, 37, 4, 9, trim),
    pixel(15, 34, 10, 12, trim), pixel(18, 38, 4, 8, coat), pixel(10, 40, 3, 2, coatShade),
    pixel(14, 30, 12, 8, outline), pixel(15, 30, 10, 7, skin),
    pixel(10, 10, 20, 23, outline), pixel(11, 12, 18, 19, skin),
    pixel(12, 18, 3, 9, light), pixel(15, 15, 10, 2, light), pixel(26, 18, 2, 5, light),
    pixel(12, 26, 3, 4, shade), pixel(25, 27, 3, 4, shade),
    pixel(9, 18, 2, 7, hair), pixel(29, 18, 2, 7, hair),
    pixel(13, 8, 14, 5, hair), pixel(11, 11, 18, 5, hair), pixel(13, 9, 9, 2, hairLight),
    pixel(12, 12, 5, 2, hairLight),
    pixel(14, 17, 5, 2, hair), pixel(23, 17, 5, 2, hair),
    pixel(15, 20, 4, 3, "#fff1d2"), pixel(23, 20, 4, 3, "#fff1d2"),
    pixel(16, 20, 2, 3, outline), pixel(24, 20, 2, 3, outline),
    pixel(20, 23, 2, 4, shade), pixel(21, 24, 2, 2, light),
    pixel(18, 28, 6, 1, "#714744"), pixel(19, 29, 4, 1, "#e2a38c"),
    pixel(12, 24, 2, 2, shade), pixel(27, 24, 2, 2, shade)
  ];
  switch (style) {
    case 0: // smith's beard, apron and metal clasp
      shapes.push(pixel(14, 29, 14, 5, hair), pixel(18, 31, 6, 4, hair),
        pixel(15, 39, 10, 9, accessory), pixel(18, 39, 4, 3, "#6e777c"), pixel(19, 40, 2, 2, trim)); break;
    case 1: // stable keeper's cap and scarf
      shapes.push(pixel(9, 8, 23, 4, accessory), pixel(12, 5, 16, 4, accessory),
        pixel(11, 32, 18, 4, trim), pixel(24, 35, 4, 7, trim)); break;
    case 2: // miller's kerchief and flour-dusted apron
      shapes.push(pixel(10, 8, 20, 5, accessory), pixel(26, 12, 5, 11, accessory),
        pixel(17, 6, 9, 3, accessory), pixel(14, 37, 12, 11, accessory), pixel(10, 40, 3, 2, accessory)); break;
    case 3: // innkeeper's pinned hair and apron
      shapes.push(pixel(7, 14, 4, 16, hair), pixel(29, 14, 4, 16, hair),
        pixel(27, 8, 4, 4, accessory), pixel(16, 38, 8, 10, accessory), pixel(12, 35, 16, 2, trim)); break;
    case 4: // carpenter's headband and tool belt
      shapes.push(pixel(10, 13, 20, 3, accessory), pixel(7, 42, 26, 3, trim),
        pixel(28, 38, 4, 8, accessory), pixel(29, 39, 2, 4, outline)); break;
    case 5: // baker's toque and double-breasted apron
      shapes.push(pixel(11, 2, 18, 9, accessory), pixel(8, 5, 5, 7, accessory),
        pixel(27, 5, 5, 7, accessory), pixel(11, 10, 18, 3, trim),
        pixel(14, 37, 12, 11, accessory), pixel(17, 40, 2, 2, coat), pixel(23, 40, 2, 2, coat)); break;
    case 6: // farmer's wide straw hat and overalls
      shapes.push(pixel(4, 12, 32, 4, accessory), pixel(11, 6, 18, 7, accessory),
        pixel(12, 10, 16, 2, trim), pixel(13, 36, 4, 11, trim), pixel(24, 36, 4, 11, trim)); break;
    case 7: // merchant's circlet, collar and brooch
      shapes.push(pixel(11, 14, 18, 2, accessory), pixel(19, 12, 4, 4, trim),
        pixel(10, 35, 8, 4, accessory), pixel(24, 35, 8, 4, accessory), pixel(26, 40, 4, 4, trim)); break;
    case 8: // forester's hood and shoulder satchel
      shapes.push(pixel(6, 10, 6, 23, coat), pixel(28, 10, 6, 23, coat),
        pixel(11, 7, 18, 5, coat), pixel(12, 35, 4, 12, accessory), pixel(27, 40, 7, 7, accessory)); break;
    case 9: // guard's helm and segmented armor
      shapes.push(pixel(9, 5, 22, 8, accessory), pixel(7, 12, 5, 15, accessory),
        pixel(28, 12, 5, 15, accessory), pixel(18, 5, 4, 12, trim),
        pixel(13, 38, 14, 9, accessory), pixel(19, 38, 2, 9, outline)); break;
    case 10: // healer's braid and herb pouch
      shapes.push(pixel(29, 13, 4, 23, hair), pixel(31, 33, 4, 10, hair),
        pixel(10, 36, 9, 4, accessory), pixel(25, 40, 8, 7, accessory),
        pixel(27, 37, 2, 5, "#73a75e")); break;
    case 11: // fisher's knitted cap and diagonal strap
      shapes.push(pixel(9, 8, 22, 6, accessory), pixel(12, 5, 16, 4, accessory),
        pixel(15, 4, 3, 2, trim), pixel(27, 34, 4, 5, trim),
        pixel(23, 39, 4, 5, trim), pixel(19, 44, 4, 4, trim)); break;
  }
  shapes.push(
    pixel(8, 35, 4, 2, tint(trim, "#fff4d4", 0.25)),
    pixel(28, 35, 4, 2, tint(trim, "#fff4d4", 0.25)),
    pixel(6, 45, 28, 1, coatShade)
  );
  return `<svg class="npc-portrait" viewBox="0 0 40 48" xmlns="http://www.w3.org/2000/svg" aria-hidden="true" focusable="false" data-portrait="${escape(id)}">${shapes.join("")}</svg>`;
}
