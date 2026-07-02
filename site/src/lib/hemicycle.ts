/** Parliament seat layout: N seats on concentric semicircular arcs.
 *
 * Seats are returned sorted by angle (left to right across the chamber), so
 * filling them in party-block order produces contiguous party wedges. This is
 * the conventional party-grouped hemicycle — the real chamber seats MPs by
 * constituency, but party grouping is what makes the AI-vs-actual comparison
 * legible (noted in the methodology).
 */

export interface Seat {
  x: number;
  y: number;
  r: number;
}

export function hemicycleLayout(
  total: number,
  opts = { rows: 11, innerRadius: 140, outerRadius: 320, cx: 340, cy: 340 },
): Seat[] {
  const { rows, innerRadius, outerRadius, cx, cy } = opts;
  const radii: number[] = [];
  for (let i = 0; i < rows; i++) {
    radii.push(innerRadius + ((outerRadius - innerRadius) * i) / (rows - 1));
  }
  const radiusSum = radii.reduce((a, b) => a + b, 0);
  // seats per row proportional to arc length; fix rounding drift on last row
  const perRow = radii.map((r) => Math.round((total * r) / radiusSum));
  const drift = total - perRow.reduce((a, b) => a + b, 0);
  perRow[perRow.length - 1] += drift;

  const seats: { x: number; y: number; angle: number }[] = [];
  radii.forEach((radius, i) => {
    const n = perRow[i];
    for (let j = 0; j < n; j++) {
      const angle = Math.PI - (Math.PI * (j + 0.5)) / n; // pi (left) -> 0 (right)
      seats.push({
        x: cx + radius * Math.cos(angle),
        y: cy - radius * Math.sin(angle),
        angle,
      });
    }
  });
  seats.sort((a, b) => b.angle - a.angle); // left -> right
  const seatR = Math.max(4, ((outerRadius - innerRadius) / (rows - 1)) * 0.33);
  return seats.map((s) => ({ x: Math.round(s.x * 10) / 10, y: Math.round(s.y * 10) / 10, r: seatR }));
}

export const VOTE_COLORS: Record<string, string> = {
  Ja: '#2e7d32',
  Nej: '#c62828',
  Avstår: '#f9a825',
  Frånvarande: '#c9c4bb',
};
