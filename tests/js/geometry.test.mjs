import { test } from "node:test";
import assert from "node:assert/strict";
import { densityInArea, homography, insidePolygon, levelFor, polygonArea, toGround } from "../../src/roomtobreathe/web/js/geometry.js";

const close = (a, b, eps = 1e-6) => assert.ok(Math.abs(a - b) < eps, `${a} != ${b}`);

test("homography maps the clicked corners onto the ground rectangle", () => {
  // A trapezoid in the image (far edge narrower) seen as a 10 x 20 m rectangle on the ground.
  const img = [[100, 400], [500, 400], [380, 100], [220, 100]];
  const H = homography(img, [[0, 0], [10, 0], [10, 20], [0, 20]]);
  const g = img.map((p) => toGround(H, p));
  [[0, 0], [10, 0], [10, 20], [0, 20]].forEach(([X, Y], i) => { close(g[i][0], X); close(g[i][1], Y); });
  close(polygonArea(g), 200);
});

test("degenerate clicks are refused", () => {
  assert.throws(() => homography([[0, 0], [1, 1], [2, 2], [3, 3]], [[0, 0], [1, 0], [1, 1], [0, 1]]));
});

test("points above the horizon have no ground position", () => {
  const H = homography([[100, 400], [500, 400], [380, 100], [220, 100]], [[0, 0], [10, 0], [10, 20], [0, 20]]);
  assert.equal(toGround(H, [300, -2000]), null);
});

test("point in polygon", () => {
  const sq = [[0, 0], [10, 0], [10, 10], [0, 10]];
  assert.ok(insidePolygon([5, 5], sq));
  assert.ok(!insidePolygon([15, 5], sq));
});

test("density in a marked area: 400 people on 100 m2 is 4 p/m2", () => {
  // Top-down camera: a 64 x 64 cell map over a 512 x 512 image, ground 10 m x 10 m marked in the middle.
  const mw = 64, mh = 64, dm = new Float32Array(mw * mh);
  const corners = [[128, 128], [384, 128], [384, 384], [128, 384]]; // 256 px = 10 m
  for (let j = 16; j < 48; j++) for (let i = 16; i < 48; i++) dm[j * mw + i] = 400 / (32 * 32);
  const r = densityInArea(dm, mw, mh, 512, 512, corners, 10, 10);
  close(r.count, 400, 1e-3);
  close(r.density, 4, 1e-4);
  close(r.busiest_density, 4, 0.2);
  assert.equal(levelFor(r.density).name, "ORANGE");
});

test("a hotspot shows in the busiest density even when the average looks fine", () => {
  const mw = 64, mh = 64, dm = new Float32Array(mw * mh);
  const corners = [[128, 128], [384, 128], [384, 384], [128, 384]];
  for (let j = 16; j < 48; j++) for (let i = 16; i < 48; i++) dm[j * mw + i] = 100 / (32 * 32);
  for (let j = 16; j < 24; j++) for (let i = 16; i < 24; i++) dm[j * mw + i] += 0.6; // a crush in one corner
  const r = densityInArea(dm, mw, mh, 512, 512, corners, 10, 10);
  assert.ok(r.density < 5, `average ${r.density}`);
  assert.ok(r.busiest_density > 5.5, `busiest ${r.busiest_density}`);
});
