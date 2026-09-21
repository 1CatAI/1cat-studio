// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import assert from "node:assert/strict";
import { test } from "node:test";
import { aspectRatio, sizeForRatio, sizesForRatio, type Size } from "../src/onecat/creative/output-sizes.ts";
const sizes: Size[] = [[1344,768],[768,1344],[768,768],[1024,768],[768,1024],[960,544],[544,960],[544,544],[736,544],[544,736]];
test("aligned sizes share one aspect ratio with independent resolution tiers", () => {
  assert.equal(aspectRatio(960,544), "16:9");
  assert.equal(aspectRatio(1344,768), "16:9");
  assert.deepEqual(sizesForRatio(sizes,"16:9"), [[960,544],[1344,768]]);
  assert.equal(aspectRatio(1536,1024), "3:2");
});
test("aspect changes preserve the resolution tier, including portrait and square", () => {
  assert.deepEqual(sizeForRatio(sizes,[960,544],"9:16"), [544,960]);
  assert.deepEqual(sizeForRatio(sizes,[544,960],"1:1"), [544,544]);
  assert.deepEqual(sizeForRatio(sizes,[1344,768],"4:3"), [1024,768]);
  assert.deepEqual(sizeForRatio([[1024,1024],[1344,768],[512,512],[672,384]],[512,512],"16:9"), [672,384]);
});
test("unsupported historical sizes remain explicit until the user selects a supported value", () => {
  assert.deepEqual(sizesForRatio(sizes, "21:9"), []);
  assert.equal(sizeForRatio(sizes,[960,544],"21:9"), undefined);
  assert.deepEqual(sizeForRatio(sizes,[1920,1080],"9:16"), [768,1344]);
});
