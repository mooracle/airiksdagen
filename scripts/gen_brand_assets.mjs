// Brand asset generator for airiksdagen.se.
//
// Motif: the real 349-seat Riksdag chamber, one dot per seat, each dot in its
// party's own colour and blocks ordered left-to-right exactly as the site's own
// Hemicycle component orders them. The mark *is* the dataset.
//
// Run:  node scripts/gen_brand_assets.mjs   (uses sharp from site/node_modules)
// Writes favicon.svg/.ico, logo.svg, logomark.svg, apple-touch-icon.png,
// icon-512.png, og-image.png into site/public/.
import { createRequire } from 'node:module';
import { writeFileSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const SITE = join(HERE, '..', 'site');
const OUT = join(SITE, 'public');
const sharp = createRequire(join(SITE, 'package.json'))('sharp');

// INK matches --ink in site/src/layouts/Base.astro exactly. It used to be
// #17222c while the stylesheet said #14202b, so anything the mark painted in
// "background" colour was a near-miss dark sitting on top of the header.
// GOLD is the single brand gold, matching --gold (chrome rules only — the mark
// itself no longer uses it).
const INK = '#14202b';
const GOLD = '#e3ad39';
const PAPER = '#f7f6f1';

const META = JSON.parse(readFileSync(join(SITE, 'src', 'data', 'meta.json'), 'utf-8'));
const ORDER = META.hemicycle_order;

/**
 * Logo palette — the parties' hues, luminance-normalised.
 *
 * The literal brand colours cannot survive on both a dark and a light ground:
 * KD navy (#000077) measures 1.00:1 against the ink header — identical
 * luminance, invisible — while SD yellow (#DDDD00) measures 1.35:1 against
 * paper. No single drawing using them works on both.
 *
 * A colour that clears 3:1 against ink *and* white has to sit in a narrow
 * luminance band, [0.141, 0.300]. Each party's hue is kept and its lightness
 * remapped into that band, preserving the relative order within each hue family
 * (so KD stays the darkest blue, M the lightest). Saturation is capped where
 * full saturation at these lightnesses goes electric, and two hues are nudged:
 * KD off pure navy, and SD from yellow toward amber, since yellow darkened
 * enough to hold on white turns olive.
 *
 * So these are *not* the site's party colours and are deliberately not read from
 * meta.json — the charts and badges on the pages still use the real ones, where
 * they only ever sit on a light panel and `partyInk`/`.pbadge` handle them.
 * Measured worst case here: 3.06:1 on ink, 3.00:1 on white.
 */
const COLOR = {
  V: '#cf253e',  // crimson
  S: '#e3526f',  // red, lighter than V so the two adjacent reds separate
  MP: '#64a227', // lime, darkened
  C: '#0b8d37',  // green
  L: '#0c82ec',  // blue
  KD: '#5962bb', // navy, lightened to a slate indigo
  M: '#159fb9',  // sky, pushed toward cyan and away from L
  SD: '#b7900f', // yellow, darkened and warmed to amber
};

// Seats held in the 2022–2026 Riksdag; sums to 349 and matches the per-seat
// `seats` array in the case data. Not in meta.json, hence stated here.
const SEATS = { V: 24, S: 107, MP: 18, C: 24, L: 16, KD: 19, M: 68, SD: 73 };
const TOTAL = Object.values(SEATS).reduce((a, b) => a + b, 0);

/**
 * Seat positions on concentric semicircular arcs, sorted left-to-right so that
 * filling them in ORDER produces contiguous party wedges.
 *
 * This mirrors hemicycleLayout() in site/src/lib/hemicycle.ts — same rows, same
 * proportional row packing, same left-to-right sort — so the logo and the seat
 * charts on the case pages are the same drawing at different scales.
 */
function layout(total, rows, innerR, outerR, cx, cy) {
  const radii = [];
  for (let i = 0; i < rows; i++) radii.push(innerR + ((outerR - innerR) * i) / (rows - 1));
  const sum = radii.reduce((a, b) => a + b, 0);
  const perRow = radii.map((r) => Math.round((total * r) / sum));
  perRow[perRow.length - 1] += total - perRow.reduce((a, b) => a + b, 0);
  const seats = [];
  radii.forEach((radius, i) => {
    for (let j = 0; j < perRow[i]; j++) {
      const a = Math.PI - (Math.PI * (j + 0.5)) / perRow[i];
      seats.push({ x: cx + radius * Math.cos(a), y: cy - radius * Math.sin(a), angle: a });
    }
  });
  seats.sort((a, b) => b.angle - a.angle);
  return { seats, r: Math.max(0.5, ((outerR - innerR) / (rows - 1)) * 0.33) };
}

/**
 * The mark: 349 seat dots, one per seat, in party order left to right.
 *
 * Fully transparent — no tile, no plate, no outline ring. Because every colour
 * in COLOR sits in the both-grounds luminance band, the dots carry themselves on
 * ink, on paper, on white and on an unknown background, which a tile could only
 * fake by bringing its own. An outline would have to commit to being either dark
 * or light and would then be wrong on one of them.
 */
function chamber(cx, cy, R, rows = 11) {
  const { seats, r } = layout(TOTAL, rows, R * 0.32, R, cx, cy);
  // Seats are consumed left-to-right, so each party takes one contiguous block.
  // Dots never overlap, so they can be grouped per party and share one fill
  // instead of repeating the hex 349 times.
  let i = 0;
  return ORDER.map((p) => {
    // `r` stays per-circle: it is a geometry attribute, not an inheritable
    // presentation one, so hoisting it onto the <g> would draw nothing at all.
    const dots = seats
      .slice(i, (i += SEATS[p]))
      .map((s) => `<circle cx="${s.x.toFixed(1)}" cy="${s.y.toFixed(1)}" r="${r.toFixed(2)}"/>`)
      .join('');
    return `<g fill="${COLOR[p]}">${dots}</g>`;
  }).join('');
}

// Browser tab, transparent — works on a light or a dark tab strip. Drawn at 5
// rows rather than 11: same drawing, fewer and therefore fatter dots. At 11 rows
// the downscale to 16px averages every dot against its background and the whole
// thing turns into a wash. Individual seats are still lost at 16px; the colour
// signature (red left, blue centre, amber right) is what survives.
const faviconSvg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">
  ${chamber(16, 24, 14, 5)}
</svg>`;

// The universal mark, transparent. Used in the site header via <img>: 349 inline
// circles would add ~14KB to every one of the 2,500+ case pages, so the nav
// references this file instead.
const logomarkSvg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 52">
  ${chamber(50, 50, 48)}
</svg>`;

// Wordmark lockup. The chamber is background-agnostic but the wordmark beside it
// cannot be, so this one commits to a light ground (it is also the schema.org
// publisher logo, which is shown on light). Use logomark.svg on dark.
const logoSvg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 300 72" font-family="Georgia, 'Times New Roman', serif">
  ${chamber(38, 56, 34)}
  <text x="84" y="47" font-size="38" font-weight="700" letter-spacing="-0.3" fill="${INK}">AI Riksdag</text>
</svg>`;

// iOS home screen / PWA. These must be opaque — a transparent home-screen icon
// composites to black on iOS — so this is the one place that commits to a ground.
const appleSvg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 180 180">
  <rect width="180" height="180" fill="${INK}"/>
  ${chamber(90, 124, 76)}
</svg>`;

const ogSvg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 630" font-family="Georgia, 'Times New Roman', serif">
  <rect width="1200" height="630" fill="${PAPER}"/>
  <rect width="1200" height="12" fill="${GOLD}"/>
  ${chamber(268, 380, 176)}
  <text x="516" y="292" font-size="104" font-weight="700" letter-spacing="-1" fill="${INK}">AI Riksdag</text>
  <rect x="520" y="322" width="150" height="6" fill="${GOLD}"/>
  <text x="516" y="392" font-size="35" fill="#45525e" font-family="-apple-system,'Segoe UI',Helvetica,Arial,sans-serif">Hur borde partierna ha röstat —</text>
  <text x="516" y="440" font-size="35" fill="#45525e" font-family="-apple-system,'Segoe UI',Helvetica,Arial,sans-serif">enligt sina egna dokument?</text>
  <text x="516" y="516" font-size="30" fill="#1c5d99" font-weight="600" font-family="-apple-system,'Segoe UI',Helvetica,Arial,sans-serif">airiksdagen.se</text>
</svg>`;

writeFileSync(join(OUT, 'favicon.svg'), faviconSvg + '\n');
writeFileSync(join(OUT, 'logomark.svg'), logomarkSvg + '\n');
writeFileSync(join(OUT, 'logo.svg'), logoSvg + '\n');

const png = (svg, size) => sharp(Buffer.from(svg)).resize(size, size).png().toBuffer();

function pngToIco(items) {
  const dir = Buffer.alloc(items.length * 16);
  let offset = 6 + items.length * 16;
  items.forEach((it, i) => {
    const b = i * 16;
    dir.writeUInt8(it.size >= 256 ? 0 : it.size, b);
    dir.writeUInt8(it.size >= 256 ? 0 : it.size, b + 1);
    dir.writeUInt16LE(1, b + 4);
    dir.writeUInt16LE(32, b + 6);
    dir.writeUInt32LE(it.buf.length, b + 8);
    dir.writeUInt32LE(offset, b + 12);
    offset += it.buf.length;
  });
  const header = Buffer.alloc(6);
  header.writeUInt16LE(1, 2);
  header.writeUInt16LE(items.length, 4);
  return Buffer.concat([header, dir, ...items.map((i) => i.buf)]);
}

const ico = pngToIco(await Promise.all([16, 32, 48].map(async (s) => ({ size: s, buf: await png(faviconSvg, s) }))));
writeFileSync(join(OUT, 'favicon.ico'), ico);
writeFileSync(join(OUT, 'apple-touch-icon.png'), await png(appleSvg, 180));
writeFileSync(join(OUT, 'icon-512.png'), await png(appleSvg, 512));
writeFileSync(join(OUT, 'og-image.png'), await sharp(Buffer.from(ogSvg)).resize(1200, 630).png().toBuffer());
console.log('brand assets written to site/public/');
