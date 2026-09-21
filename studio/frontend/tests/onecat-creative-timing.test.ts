// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import assert from "node:assert/strict";
import test from "node:test";
import { elapsed, totalSeconds } from "../src/onecat/creative/timing.ts";

test("completed task time is frozen, including after a later reload", () => {
  const run = { state: "completed", created_at: 100, finished_at: 225 };
  assert.equal(totalSeconds(run, 250), 125);
  assert.equal(totalSeconds(run, 90000), 125);
  assert.equal(elapsed(totalSeconds(run, 90000)), "2:05");
});

test("running tasks tick but missing historical finish time is unknown", () => {
  assert.equal(totalSeconds({state: "running", created_at: 100}, 122), 22);
  for (const state of ["completed", "failed", "cancelled"])
    assert.equal(totalSeconds({state, created_at: 100}, 90000), null);
  assert.equal(elapsed(null), "—");
  assert.equal(elapsed(NaN), "—");
  assert.equal(elapsed(-4), "—");
  assert.equal(elapsed(3661), "1:01:01");
});
