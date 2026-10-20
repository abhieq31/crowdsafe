// Ground-plane geometry for photos: the same homography maths as vision/calibration.py, in the browser.
// A homography H maps image pixels (x, y) to ground metres (X, Y). With it, a count of people inside a
// marked area becomes people per square metre, which is the quantity that decides whether a crowd is safe.

// Solve the 8x8 linear system for H from 4 point pairs (direct linear transform, h33 = 1).
export function homography(src, dst) {
  if (src.length !== 4 || dst.length !== 4) throw new Error("need exactly 4 point pairs");
  const A = [], b = [];
  for (let i = 0; i < 4; i++) {
    const [x, y] = src[i], [X, Y] = dst[i];
    A.push([x, y, 1, 0, 0, 0, -x * X, -y * X]); b.push(X);
    A.push([0, 0, 0, x, y, 1, -x * Y, -y * Y]); b.push(Y);
  }
  const h = solve(A, b);
  if (!h) throw new Error("the 4 points must form a proper quadrilateral (no three in a line)");
  return [[h[0], h[1], h[2]], [h[3], h[4], h[5]], [h[6], h[7], 1]];
}

// Gaussian elimination with partial pivoting. Returns null for a singular system.
function solve(A, b) {
  const n = b.length, M = A.map((row, i) => [...row, b[i]]);
  for (let c = 0; c < n; c++) {
    let p = c;
    for (let r = c + 1; r < n; r++) if (Math.abs(M[r][c]) > Math.abs(M[p][c])) p = r;
    if (Math.abs(M[p][c]) < 1e-12) return null;
    [M[c], M[p]] = [M[p], M[c]];
    for (let r = 0; r < n; r++) {
      if (r === c) continue;
      const f = M[r][c] / M[c][c];
      for (let k = c; k <= n; k++) M[r][k] -= f * M[c][k];
    }
  }
  return M.map((row, i) => row[n] / row[i]);
}

// Map one image point to the ground. Returns null for points on or beyond the horizon (w <= 0).
export function toGround(H, [x, y]) {
  const w = H[2][0] * x + H[2][1] * y + H[2][2];
  if (w <= 1e-12) return null;
  return [(H[0][0] * x + H[0][1] * y + H[0][2]) / w, (H[1][0] * x + H[1][1] * y + H[1][2]) / w];
}

export function polygonArea(pts) {
  let s = 0;
  for (let i = 0; i < pts.length; i++) {
    const [x1, y1] = pts[i], [x2, y2] = pts[(i + 1) % pts.length];
    s += x1 * y2 - x2 * y1;
  }
  return Math.abs(s) / 2;
}

export function insidePolygon([x, y], poly) {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = poly[i], [xj, yj] = poly[j];
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

// Density of people per m² inside a marked ground rectangle.
//   dm: Float32Array density map (people per cell), size mw x mh, covering an image of iw x ih pixels
//   corners: 4 image points clicked around a rectangle that is widthM x depthM metres on the ground
// Returns the count and average density inside, and the densest square metre: cells are grouped by their
// position on the ground into 1 m x 1 m squares, so perspective is accounted for. An average hides the
// spot where people are crushed; the densest square metre does not.
export function densityInArea(dm, mw, mh, iw, ih, corners, widthM, depthM) {
  const ground = [[0, 0], [widthM, 0], [widthM, depthM], [0, depthM]];
  const H = homography(corners, ground);
  const sx = iw / mw, sy = ih / mh;
  const bins = new Map(); // "bx,by" -> [people, m2]
  let count = 0;
  for (let j = 0; j < mh; j++) {
    for (let i = 0; i < mw; i++) {
      const c = [(i + 0.5) * sx, (j + 0.5) * sy];
      if (!insidePolygon(c, corners)) continue;
      const q = [[i * sx, j * sy], [(i + 1) * sx, j * sy], [(i + 1) * sx, (j + 1) * sy], [i * sx, (j + 1) * sy]]
        .map((p) => toGround(H, p));
      const g = toGround(H, c);
      if (!g || q.some((p) => p === null)) continue;
      const k = dm[j * mw + i];
      count += k;
      const key = `${Math.floor(g[0])},${Math.floor(g[1])}`;
      const bin = bins.get(key) || [0, 0];
      bin[0] += k; bin[1] += polygonArea(q);
      bins.set(key, bin);
    }
  }
  let busiest = null;
  for (const [n, a] of bins.values()) {
    if (a >= 0.5) busiest = Math.max(busiest ?? 0, n / a); // skip edge squares that are mostly outside
  }
  return { count, area_m2: widthM * depthM, density: count / (widthM * depthM), busiest_density: busiest, H };
}

// Same thresholds as physics.DensityThresholds.
export const LEVELS = [
  { at: 5.5, name: "RED", say: "Act now: people can be crushed at this density" },
  { at: 4.0, name: "ORANGE", say: "Unsafe if the crowd moves. Hold people back upstream" },
  { at: 2.0, name: "YELLOW", say: "Busy. Watch the trend" },
  { at: 0, name: "GREEN", say: "Room to move" },
];
export const levelFor = (d) => LEVELS.find((l) => d >= l.at);
