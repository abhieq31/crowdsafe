import { test } from "node:test";
import assert from "node:assert/strict";
import { areaM2, gateSides, lengthM, toLatLon, toLocal } from "../../src/roomtobreathe/web/js/geo.js";

const O = [22.5726, 88.3639]; // Kolkata
const close = (a, b, eps) => assert.ok(Math.abs(a - b) <= eps, `${a} != ${b}`);
const rect = (x0, y0, x1, y1) => [[x0, y0], [x1, y0], [x1, y1], [x0, y1]].map((p) => toLatLon(O, p));

test("local metres round-trip through latitude/longitude", () => {
  const [x, y] = toLocal(O, toLatLon(O, [123.4, -56.7]));
  close(x, 123.4, 1e-6); close(y, -56.7, 1e-6);
});

test("a 60 x 40 m ground drawn on the map is 2400 m2", () => {
  close(areaM2(O, rect(0, 0, 60, 40)), 2400, 0.01);
});

test("matches the engine's projection (venue.py local_to_latlon)", () => {
  // venue.py: lat = lat0 + y / 111320; lon = lon0 + x / (111320 cos lat0)
  const [lat, lon] = toLatLon(O, [100, 50]);
  close(lat, O[0] + 50 / 111320, 1e-12);
  close(lon, O[1] + 100 / (111320 * Math.cos((O[0] * Math.PI) / 180)), 1e-12);
});

test("a gate line drawn across a 4 m opening measures 4 m", () => {
  close(lengthM(O, [toLatLon(O, [10, 0]), toLatLon(O, [14, 0])]), 4, 1e-6);
});

test("the zones on each side of a gate are found", () => {
  const zones = [rect(0, 0, 50, 30), rect(50, 0, 80, 30)]; // hall | exit lane, sharing the x=50 edge
  const between = gateSides(O, [toLatLon(O, [50, 12]), toLatLon(O, [50, 16])], zones);
  assert.deepEqual([...between].sort(), [0, 1]);
  const exit = gateSides(O, [toLatLon(O, [80, 12]), toLatLon(O, [80, 16])], zones);
  assert.deepEqual([...exit].sort((a, b) => String(a).localeCompare(String(b))), [1, "outside"]);
});

test("a gate tapped a few metres inside the edge still finds both sides", () => {
  const zones = [rect(0, 0, 50, 30)];
  const sides = gateSides(O, [toLatLon(O, [20, 4]), toLatLon(O, [26, 4])], zones); // 4 m inside the y=0 edge
  assert.deepEqual([...sides].sort((a, b) => String(a).localeCompare(String(b))), [0, "outside"]);
  const middle = gateSides(O, [toLatLon(O, [20, 15]), toLatLon(O, [26, 15])], zones); // nowhere near an edge
  assert.equal(middle[0], middle[1]);
});
