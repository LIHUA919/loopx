import assert from "node:assert/strict";
import test from "node:test";
import {planStateEventAppend} from "../../loopx/control_plane/goals/state_event_append.ts";

const old = {event_id: "old", fingerprint: "a".repeat(64), append_sequence: 7};
const fresh = {event_id: "new", fingerprint: "b".repeat(64)};
const request = {schema_version: "loopx_state_event_append_plan_v0", source_checksum: "basis",
  expected_checksum: "basis", last_sequence: 7, existing: [old], events: [fresh, old, fresh]};

test("allocate only once for a duplicate, preserving historical replay order", () => {
  assert.deepEqual(planStateEventAppend(request).choices, [
    {kind: "append", event_id: "new", append_sequence: 8},
    {kind: "replay", event_id: "old", append_sequence: 7},
    {kind: "replay", event_id: "new", append_sequence: 8},
  ]);
  assert.equal(request.last_sequence, 7);
});

test("late conflicts never return a partial append plan", () => {
  for (const event_id of ["old", "new"]) {
    const result = planStateEventAppend({...request,
      events: [fresh, {event_id, fingerprint: "c".repeat(64)}]});
    assert.equal(result.status, "rejected");
    assert.equal(result.reason_code, "event_id_conflict");
    assert.equal(result.choices, undefined);
  }
});

test("source mismatch wins even for an empty durability confirmation", () => {
  const result = planStateEventAppend({...request, source_checksum: "changed", events: []});
  assert.equal(result.reason_code, "event_source_changed");
  assert.equal(result.choices, undefined);
});

test("sequence exhaustion cannot round two new events onto one identity", () => {
  assert.equal(planStateEventAppend({...request, last_sequence: Number.MAX_SAFE_INTEGER}).reason_code,
    "event_sequence_exhausted");
  assert.throws(() => planStateEventAppend({...request, last_sequence: Number.MAX_SAFE_INTEGER + 1}));
  assert.equal(planStateEventAppend({...request, last_sequence: Number.MAX_SAFE_INTEGER, events: [old]}).status,
    "planned");
});

test("malformed or contradictory compact source witnesses reject", () => {
  for (const changed of [{source_checksum: null}, {expected_checksum: undefined}, {last_sequence: true}, {existing: [old, old]},
    {existing: [{...old, append_sequence: 8}]}, {events: [{...fresh, fingerprint: "not-a-hash"}]}]) {
    assert.throws(() => planStateEventAppend({...request, ...changed}));
  }
});

test("valid event ids cannot collide with JavaScript object prototype names", () => {
  const events = ["__proto__", "constructor", "toString"].map(event_id => ({...fresh, event_id}));
  assert.deepEqual(planStateEventAppend({...request, existing: [], last_sequence: 0,
    events: [...events, events[0]]}).choices, [
    {kind: "append", event_id: "__proto__", append_sequence: 1},
    {kind: "append", event_id: "constructor", append_sequence: 2},
    {kind: "append", event_id: "toString", append_sequence: 3},
    {kind: "replay", event_id: "__proto__", append_sequence: 1},
  ]);
});
