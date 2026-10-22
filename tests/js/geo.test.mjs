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

import { mergePlaces, parseLocation, queryVariants } from "../../src/roomtobreathe/web/js/geo.js";

test("coordinates and map links pasted into search", () => {
  assert.deepEqual(parseLocation("28.6420, 77.2299"), [28.642, 77.2299]);
  assert.deepEqual(parseLocation("28.6420 77.2299"), [28.642, 77.2299]);
  assert.deepEqual(parseLocation("https://www.google.com/maps/@28.642,77.2299,17z"), [28.642, 77.2299]);
  assert.deepEqual(parseLocation("https://www.google.com/maps/place/Ramlila+Grounds/@28.6431,77.2256,17z/data=!3m1!4b1!4m6!3m5!1s0x0:0x0!8m2!3d28.642!4d77.2299"), [28.642, 77.2299]);
  assert.deepEqual(parseLocation("https://maps.google.com/?q=28.642,77.2299"), [28.642, 77.2299]);
  assert.deepEqual(parseLocation("https://www.openstreetmap.org/#map=17/28.64200/77.22990"), [28.642, 77.2299]);
  assert.equal(parseLocation("Ramlila Maidan"), null);
  assert.equal(parseLocation("120, 500"), null);
});

test("generic words are dropped for a second search", () => {
  assert.deepEqual(queryVariants("Ramlila Maidan"), ["Ramlila Maidan", "Ramlila"]);
  assert.deepEqual(queryVariants("Ramlila Maidan, New Delhi"), ["Ramlila Maidan, New Delhi", "Ramlila, New Delhi"]);
  assert.deepEqual(queryVariants("Eden Gardens, Kolkata"), ["Eden Gardens, Kolkata"]);
  assert.deepEqual(queryVariants("Maidan"), ["Maidan"]);
});

test("places found by several searches are listed once", () => {
  const a = [{ name: "A", lat: 28.642, lon: 77.2299 }], b = [{ name: "A2", lat: 28.6423, lon: 77.2301 }, { name: "B", lat: 28.69, lon: 77.29 }];
  assert.deepEqual(mergePlaces([a, b]).map((p) => p.name), ["A", "B"]);
});
