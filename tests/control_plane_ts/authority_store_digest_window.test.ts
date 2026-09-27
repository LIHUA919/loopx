import assert from "node:assert/strict";
import {createHash} from "node:crypto";
import test from "node:test";

import {canonicalAuthoritySha256, createCanonicalAuthorityDigestWindow} from
  "../../loopx/control_plane/coordination/authority_store_codec.ts";

test("window digest keeps the exact canonical v0 hash across JSON shapes and cache eviction", () => {
  const digest = createCanonicalAuthorityDigestWindow();
  const sparse: unknown[] = [];
  sparse.length = 3;
  sparse[1] = "middle";
  const fixtures: unknown[] = [null, "quote\"slash\\\n", -0, 1.25e30, true, sparse,
    JSON.parse('{"": {"marker": "empty"}, "__proto__": {"marker": "proto"}}'),
    JSON.parse('{"10":1,"2":2,"Ω":"🙂","nested":{"b":1,"a":2}}')];
  for (const value of fixtures) assert.equal(digest(value), canonicalAuthoritySha256(value));
  for (let index = 0; index < 8; index++) {
    const value = {payload: String(index).repeat(1024 * 1024), ordinal: index};
    assert.equal(digest(value), canonicalAuthoritySha256(value));
  }
  assert.throws(() => digest({invalid: Number.NaN}));
});

test("one window reuses stable large JSON strings across many distinct proofs", () => {
  const padding = "p".repeat(1024 * 1024);
  const inputs = Array.from({length: 100}, (_, index) => ({padding, ordinal: index}));
  const digest = createCanonicalAuthorityDigestWindow();
  const baseline = inputs.map(value => canonicalAuthoritySha256(value));
  assert.deepEqual(inputs.map(value => digest(value)), baseline);
  // Relative speed belongs in the explicit experiment, not a timing-sensitive
  // correctness test running beside unrelated CI workers.
});

test("window digest preserves strict input validation and exact UTF-8 bytes", () => {
  const digest = createCanonicalAuthorityDigestWindow();
  const cycle: unknown[] = []; cycle.push(cycle);
  for (const value of [undefined, NaN, Infinity, 1n, new Date(), {bad: undefined}, cycle]) {
    assert.throws(() => digest(value));
  }
  // Literal canonical bytes are independent of the implementation under test.
  const plain = {b: [true, null, -0], a: "\u4e2d"};
  assert.equal(digest(plain), createHash("sha256").update('{"a":"中","b":[true,null,0]}').digest("hex"));
  const strings = ["\ud800".repeat(1200), '"\\\n'.repeat(1200), "🙂".repeat(600), "x".repeat(3 * 1024 ** 2)];
  for (const value of strings) {
    assert.equal(digest(value), createHash("sha256").update(JSON.stringify(value)).digest("hex"));
  }
  const changing = {padding: "p".repeat(4096), metadata: {attempt: 1}};
  const before = digest(changing); changing.metadata.attempt = 2;
  assert.notEqual(digest(changing), before);
  assert.equal(digest(changing), canonicalAuthoritySha256(changing));
});
