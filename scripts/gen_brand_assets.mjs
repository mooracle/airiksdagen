// Brand asset generator for airiksdagen.se.
// Motif: the 349-seat Riksdag hemicycle, brand yellow (#f2c744) on ink (#17222c).
// Run:  node scripts/gen_brand_assets.mjs   (uses sharp from site/node_modules)
// Writes favicon.svg/.ico, logo.svg, logomark.svg, apple-touch-icon.png,
// icon-512.png, og-image.png into site/public/.
import { createRequire } from 'node:module';
import { writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const SITE = join(HERE, '..', 'site');
const OUT = join(SITE, 'public');
const sharp = createRequire(join(SITE, 'package.json'))('sharp');

const INK = '#17222c';
const YELLOW = '#f2c744';
const PAPER = '#f7f6f1';

// seat-dot hemicycle (flat side down, top semicircle)
function seats(cx, cy, radii, dotR, spacing = 2.5) {
  let s = '';
  for (const r of radii) {
    const n = Math.max(3, Math.round((Math.PI * r) / (dotR * spacing)) + 1);
    for (let k = 0; k < n; k++) {
      const t = (Math.PI * k) / (n - 1);
      s += `<circle cx="${(cx + r * Math.cos(t)).toFixed(2)}" cy="${(cy - r * Math.sin(t)).toFixed(2)}" r="${dotR}"/>`;
    }
  }
  return s;
}

// banded half-disc (legible at tiny sizes)
function halfDisc(cx, cy, R, fill, gap, bands = [0.64, 0.34]) {
  let s = `<path d="M ${cx - R} ${cy} A ${R} ${R} 0 0 1 ${cx + R} ${cy} Z" fill="${fill}"/>`;
  const sw = (R * 0.145).toFixed(2);
  for (const f of bands) {
    const r = R * f;
    s += `<path d="M ${cx - r} ${cy} A ${r} ${r} 0 0 1 ${cx + r} ${cy}" fill="none" stroke="${gap}" stroke-width="${sw}" stroke-linecap="round"/>`;
  }
  return s;
}

const faviconSvg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">
  <rect width="32" height="32" rx="7" fill="${INK}"/>
  ${halfDisc(16, 20.6, 11, YELLOW, INK)}
</svg>`;

const logomarkSvg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 56">
  <g fill="${YELLOW}">${seats(50, 49, [15, 24, 33, 42], 3.0)}</g>
</svg>`;

const logoSvg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 300 72" font-family="Georgia, 'Times New Roman', serif">
  <rect x="4" y="4" width="64" height="64" rx="14" fill="${INK}"/>
  ${halfDisc(36, 46, 22, YELLOW, INK)}
  <text x="84" y="47" font-size="38" font-weight="700" letter-spacing="-0.3" fill="${INK}">AI Riksdag</text>
</svg>`;

const appleSvg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 180 180">
  <rect width="180" height="180" fill="${INK}"/>
  <g fill="${YELLOW}" transform="translate(20 44) scale(1.4)">${seats(50, 49, [15, 24, 33, 42], 3.0)}</g>
</svg>`;

const ogSvg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 630" font-family="Georgia, 'Times New Roman', serif">
  <rect width="1200" height="630" fill="${PAPER}"/>
  <rect width="1200" height="12" fill="${YELLOW}"/>
  <g transform="translate(96 150)">
    <rect width="300" height="300" rx="66" fill="${INK}"/>
    <g fill="${YELLOW}" transform="translate(20 66) scale(2.6)">${seats(50, 49, [15, 24, 33, 42], 3.0)}</g>
  </g>
  <text x="452" y="292" font-size="104" font-weight="700" letter-spacing="-1" fill="${INK}">AI Riksdag</text>
  <rect x="456" y="322" width="150" height="6" fill="${YELLOW}"/>
  <text x="452" y="392" font-size="35" fill="#45525e" font-family="-apple-system,'Segoe UI',Helvetica,Arial,sans-serif">Hur borde partierna ha röstat —</text>
  <text x="452" y="440" font-size="35" fill="#45525e" font-family="-apple-system,'Segoe UI',Helvetica,Arial,sans-serif">enligt sina egna dokument?</text>
  <text x="452" y="516" font-size="30" fill="#1c5d99" font-weight="600" font-family="-apple-system,'Segoe UI',Helvetica,Arial,sans-serif">airiksdagen.se</text>
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
