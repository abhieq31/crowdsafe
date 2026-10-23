// Map geometry for the venue picker. Zones drawn on the map become polygons in local metres around an
// origin, using the same equirectangular projection as Venue.local_to_latlon in venue.py, so the browser
// and the engine agree on every area. Over a venue (a few km at most) the error is far below 0.1%.
import { insidePolygon, polygonArea } from "./geometry.js";

const M_PER_DEG = 111_320;

export function toLocal([lat0, lon0], [lat, lon]) {
  return [(lon - lon0) * M_PER_DEG * Math.cos((lat0 * Math.PI) / 180), (lat - lat0) * M_PER_DEG];
}
export function toLatLon([lat0, lon0], [x, y]) {
  return [lat0 + y / M_PER_DEG, lon0 + x / (M_PER_DEG * Math.cos((lat0 * Math.PI) / 180))];
}
export const round2 = (p) => p.map((v) => Math.round(v * 100) / 100);

// Area in m² of a polygon given as [lat, lon] points.
export function areaM2(origin, latlngs) {
  return polygonArea(latlngs.map((p) => toLocal(origin, p)));
}

// Length in metres of a line given as [lat, lon] points (a gate drawn across its opening).
export function lengthM(origin, latlngs) {
  let s = 0;
  const pts = latlngs.map((p) => toLocal(origin, p));
  for (let i = 1; i < pts.length; i++) s += Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]);
  return s;
}

// Which zone lies on each side of a gate line? Returns [left, right] zone indexes, or "outside".
// Probes either side of the line's midpoint, perpendicular to it, at growing distances until the two
// sides differ, because a person tapping a gate on a map lands a few metres off the exact edge.
export function gateSides(origin, lineLatLngs, zonesLatLngs, probesM = [2, 5, 10, 20]) {
  const a = toLocal(origin, lineLatLngs[0]), b = toLocal(origin, lineLatLngs[lineLatLngs.length - 1]);
  const mx = (a[0] + b[0]) / 2, my = (a[1] + b[1]) / 2, len = Math.hypot(b[0] - a[0], b[1] - a[1]) || 1;
  const nx = -(b[1] - a[1]) / len, ny = (b[0] - a[0]) / len;
  const polys = zonesLatLngs.map((z) => z.map((p) => toLocal(origin, p)));
  const side = (sx, sy) => {
    const i = polys.findIndex((poly) => poly.length >= 3 && insidePolygon([sx, sy], poly));
    return i < 0 ? "outside" : i;
  };
  let last = null;
  for (const d of probesM) {
    last = [side(mx + nx * d, my + ny * d), side(mx - nx * d, my - ny * d)];
    if (last[0] !== last[1]) return last;
  }
  return last;
}

// ------------------------------------------------------------------ finding the place
// Coordinates or a map link pasted into the search box: "28.6420, 77.2299", a Google Maps link
// (…/@28.642,77.2299,17z, …!3d28.642!4d77.2299, …?q=28.642,77.2299), or an OpenStreetMap link (#map=17/28.642/77.2299).
export function parseLocation(text) {
  const t = String(text).trim();
  const ok = (lat, lon) => Math.abs(lat) <= 90 && Math.abs(lon) <= 180 ? [lat, lon] : null;
  let m = t.match(/!3d(-?\d+(?:\.\d+)?)!4d(-?\d+(?:\.\d+)?)/); // a place's own pin in a Google Maps link
  if (m) return ok(Number(m[1]), Number(m[2]));
  m = t.match(/@(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)/) || t.match(/[?&](?:q|ll|query|center)=(-?\d+(?:\.\d+)?)(?:,|%2C)\s*(-?\d+(?:\.\d+)?)/i);
  if (m) return ok(Number(m[1]), Number(m[2]));
  m = t.match(/map=\d+\/(-?\d+(?:\.\d+)?)\/(-?\d+(?:\.\d+)?)/);
  if (m) return ok(Number(m[1]), Number(m[2]));
  m = t.match(/^(-?\d{1,2}(?:\.\d+)?)\s*[, ]\s*(-?\d{1,3}(?:\.\d+)?)$/);
  if (m) return ok(Number(m[1]), Number(m[2]));
  return null;
}

// "Maidan", "ground" and "field" mean the same thing and are spelled differently on the map: the famous
// Ramlila Maidan in Delhi is "Ramlila Grounds" in OpenStreetMap. Search again without them.
const GENERIC = /\b(maidan|maidaan|grounds?|fields?|mela ground)\b/gi;
export function queryVariants(q) {
  const base = String(q).trim();
  const core = base.replace(GENERIC, " ").replace(/\s+,/g, ",").replace(/\s{2,}/g, " ").replace(/^[\s,]+|[\s,]+$/g, "");
  return core && core.toLowerCase() !== base.toLowerCase() ? [base, core] : [base];
}

// Merge results from several searches: one entry per place (within ~100 m), in the order found.
export function mergePlaces(lists, max = 8) {
  const out = [];
  for (const list of lists) {
    for (const p of list) {
      if (out.some((o) => Math.abs(o.lat - p.lat) < 0.001 && Math.abs(o.lon - p.lon) < 0.001)) continue;
      out.push(p);
      if (out.length >= max) return out;
    }
  }
  return out;
}
