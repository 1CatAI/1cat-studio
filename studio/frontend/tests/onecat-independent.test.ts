// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import { strict as assert } from "node:assert";
import { test } from "node:test";
import { safeMarkdownUrl } from "../src/onecat/markdown-url.ts";
import { readFastApiError } from "../src/onecat/http-error.ts";

test("Markdown navigation rejects executable and local-resource schemes", () => {
  for (const value of ["javascript:alert(1)", "JaVa\nScRiPt:alert(1)", "\u0000javascript:alert(1)",
    "data:text/html,<script>alert(1)</script>", "file:///etc/passwd", "vbscript:msgbox(1)", "blob:https://example.org/secret"]) {
    assert.equal(safeMarkdownUrl(value), undefined, value);
  }
  for (const value of ["https://example.org/a?q=中文", "http://localhost/a", "mailto:hello@example.org",
    "/models", "#heading", "../chat", "//example.org/test"]) {
    assert.equal(safeMarkdownUrl(value), value);
  }
});

test("API failures preserve validation locations and proxy messages", async () => {
  const json = (value: unknown) => new Response(JSON.stringify(value), { status: 422 });
  assert.equal(await readFastApiError(json({ detail: "Model unavailable" })), "Model unavailable");
  assert.equal(await readFastApiError(json({ detail: [{ loc: ["body", "profiles", 0, "name"], msg: "Required" }] })), "profiles.0.name: Required");
  assert.equal(await readFastApiError(json({ error: { message: "Backend offline" } })), "Backend offline");
  assert.equal(await readFastApiError(new Response("Gateway timeout", { status: 504 })), "Gateway timeout");
  assert.match(await readFastApiError(new Response("", { status: 503 })), /^HTTP 503/);
});
