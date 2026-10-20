// Checks that the offline browser planner matches the Python implementation.
// Run: node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { computeCapacity, assessRisk } from "../../src/crowdsafe/web/js/planning.js";

const vectors = JSON.parse(readFileSync(new URL("../vectors/planning.json", import.meta.url)));

for (const c of vectors.capacity) {
  test(`capacity: ${c.name}`, () => {
    const r = computeCapacity(c.area, c.exits, c.density, c.evac_s);
    delete r.findings;
    assert.deepEqual(r, c.expected);
  });
}

for (const c of vectors.risk) {
  test(`risk: ${c.name}`, () => {
    const r = assessRisk(c.profile);
    assert.equal(r.score, c.expected.score);
    assert.equal(r.tier, c.expected.tier);
    assert.equal(r.approve, c.expected.approve);
    assert.equal(r.occupancy_ratio, c.expected.occupancy_ratio);
    assert.deepEqual(r.factors.map((f) => f.code), c.expected.codes);
  });
}
