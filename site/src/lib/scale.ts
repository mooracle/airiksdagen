/**
 * Diverging colour scale for agreement-style ratios (0 = full divergence,
 * 1 = full agreement), shared by the co-voting matrix and the home timeline so
 * the two heatmaps speak the same language.
 *
 * Replaces the `hsl(ratio * 120 ...)` hue sweeps both used previously. Sweeping
 * hue from red to green passes straight through yellow, which is the *lightest*
 * hue at any fixed HSL lightness — so a mid-range cell came out brighter than
 * both extremes and the ramp was non-monotonic in luminance (0.19 → 0.56 at the
 * midpoint → 0.45 at the top end). Nothing could be read by lightness, and in
 * greyscale or with red-green colour blindness the scale collapsed entirely.
 *
 * The stops below are a proper diverging ramp: hue carries the *direction* away
 * from the midpoint and luminance carries the *distance*, symmetrically.
 *
 * Every caller must pass an ABSOLUTE ratio on the same 0–1 axis. Feeding this a
 * value that has been re-centred on some other baseline — a deviation from a
 * per-row average, say — makes identical numbers come out different colours in
 * different rows, which is what the scale exists to prevent.
 */

/** Stops from 0 → 1, evenly spaced. Red = diverges, light green = the 50% mark,
 *  green = agrees. The midpoint used to be a warm neutral (#efece0); it is a
 *  light green now so that "half the time" reads as the start of the green half
 *  rather than as an absence of colour, and so every heatmap on the site puts
 *  the same tone at 50%.
 *
 *  Measured luminance stays a clean V — 0.33 / 0.49 / 0.66 / 0.81 / 0.65 / 0.47
 *  / 0.36 — so "pale = middling" still reads without colour, and both extremes
 *  stay light enough to carry --ink text at ≥ 6:1 (the cells print their own
 *  percentage, so the number has to stay legible on every stop). */
const DIVERGING = [
  '#d98b7a',
  '#e8ae9c',
  '#f0cdbc',
  '#dcefcd',
  '#b8dfa8',
  '#93c586',
  '#74b177',
] as const;

/** Fill for a cell with no value (self-comparison, or no data). */
export const EMPTY_FILL = '#f2f0ea';

function hexToRgb(hex: string): [number, number, number] {
  const h = hex.slice(1);
  return [
    parseInt(h.slice(0, 2), 16),
    parseInt(h.slice(2, 4), 16),
    parseInt(h.slice(4, 6), 16),
  ];
}

/**
 * Blend between the two neighbouring stops. Interpolation runs in linear-light
 * RGB, not sRGB: mixing gamma-encoded channels darkens the midpoint of every
 * blend, which would reintroduce exactly the luminance dips the stops were
 * chosen to avoid.
 */
export function agreementColor(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return EMPTY_FILL;
  const t = Math.min(1, Math.max(0, v)) * (DIVERGING.length - 1);
  const i = Math.min(DIVERGING.length - 2, Math.floor(t));
  const f = t - i;
  const a = hexToRgb(DIVERGING[i]);
  const b = hexToRgb(DIVERGING[i + 1]);
  const toLin = (c: number) => {
    const s = c / 255;
    return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  const toSrgb = (l: number) => {
    const s = l <= 0.0031308 ? l * 12.92 : 1.055 * l ** (1 / 2.4) - 0.055;
    return Math.round(Math.min(1, Math.max(0, s)) * 255);
  };
  const ch = (k: number) => toSrgb(toLin(a[k]) + (toLin(b[k]) - toLin(a[k])) * f);
  return `#${[ch(0), ch(1), ch(2)].map((c) => c.toString(16).padStart(2, '0')).join('')}`;
}
