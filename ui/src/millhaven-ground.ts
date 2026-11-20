type Surface = "meadow" | "moss" | "grass" | "dirt" | "stone" | "furrows" | "water" | "planks";

export const groundFill = (surface: Surface) => `url(#scene-tile-${surface})`;

function grassDetails(seed: number) {
  const marks = [];
  for (let i = 0; i < 24; i++) {
    const x = ((i * 37 + i * i * 7 + seed * 23) % 97 + 1) / 100;
    const y = ((i * 73 + i * i * 3 + seed * 13) % 97 + 1) / 100;
    const tint = i % 4 === 0 ? "#b3b372" : i % 2 === 0 ? "#829452" : "#435e3a";
    marks.push(`<path d="M${x} ${y}l.026 -.06m-.026 .06l-.023 -.036" stroke="${tint}" stroke-width=".012" opacity=".55"/>`);
    if (i % 7 === 0) marks.push(`<circle cx="${x}" cy="${y - .018}" r=".009" fill="#d5bb78"/>`);
  }
  return marks.join("");
}

const grassPalette = { meadow: "#758650", moss: "#5c7548", grass: "#697e4b" };

export function groundPatterns(transform: string) {
  const tile = (kind: Surface, base: string, detail: string, width = 1, height = 1) =>
    `<pattern id="scene-tile-${kind}" patternUnits="userSpaceOnUse" width="${width}" height="${height}" patternTransform="${transform}">
      <rect width="${width}" height="${height}" fill="${base}"/>${detail}</pattern>`;
  const grass = (Object.keys(grassPalette) as (keyof typeof grassPalette)[]).map((kind, i) =>
    tile(kind, grassPalette[kind], `<path d="M0 1V0H1" fill="none" stroke="#344b33" stroke-opacity=".22" stroke-width=".022"/>${grassDetails(i + 1)}`));
  return grass.join("") + tile("dirt", "#ad9165", `
    <path d="M.025 .17h.06m.09 .12h.035m-.14 .095h.07m.09 -.31h.055" stroke="#806a49" stroke-width=".017" opacity=".6"/>
    <path d="M.12 .05h.035m.13 .24h.033m-.21 .155h.08" stroke="#cfb78a" stroke-width=".017"/>`, .32, .48)
    + tile("stone", "#716958", `
    <path d="M.01 .03H.2l.015 .06-.018 .059H.01z" fill="#a99c83"/>
    <path d="M.224 .03H.413v.119H.23l-.012-.047z" fill="#b7a88f"/>
    <path d="M0 .173H.105v.127H0zm.122 0h.183v.127H.122z" fill="#998d77"/>
    <path d="M.322 .173H.42v.127H.322z" fill="#b3a78c"/>
    <path d="M.02 .045H.19m.052 0h.147m-.25 .153h.145" stroke="#d7c6a9" stroke-opacity=".55" stroke-width=".012"/>`, .42, .32)
    + tile("furrows", "#74553b", `
    <path d="M.05 0v.5m.13-.5v.5" stroke="#9b7147" stroke-width=".075"/>
    <path d="M.065 0v.5" stroke="#bc955b" stroke-width=".012"/>
    <path d="M.18 .08l-.03-.068m.03 .068l.028-.049m-.028 .238l-.03-.075m.03 .075l.029-.05m-.029 .188l-.03-.061m.03 .061l.028-.048" stroke="#9fa357" stroke-width=".018"/>`, .27, .5)
    + tile("water", "#507d78", `
    <path d="M.035 .14h.19m-.055 .2h.23m-.34 .16h.16" stroke="#87a799" stroke-opacity=".6" stroke-width=".014"/>
    <path d="M.28 .09h.18m-.39 .53h.33" stroke="#396763" stroke-width=".035"/>`, .49, .69)
    + tile("planks", "#775635", `
    <rect x=".013" y=".014" width=".264" height=".113" fill="#b88e5b"/>
    <path d="M.023 .036h.241m-.158 .046h.123" stroke="#d3ae7a" stroke-width=".012"/>
    <circle cx=".035" cy=".1" r=".008" fill="#634429"/><circle cx=".248" cy=".1" r=".008" fill="#634429"/>`, .29, .145);
}
