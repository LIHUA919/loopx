import assert from "node:assert/strict";
import test from "node:test";
import {mkdtemp, rm, readFile, writeFile, readdir} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {cadenceStorePath, manageAutomationCadence as manage, projectCadenceProgression as progression} from "../../loopx/control_plane/quota/automation_cadence.ts";

test("owner floor inherits without changing another agent; reductions and concurrent writes require authority", async () => {
  const root = await mkdtemp(join(tmpdir(), "cadence-"));
  const base = {runtime_root: root, goal_id: "fixture", operation: "configure", execute: true, owner_reference: "owner-request"};
  const read = (extra = {}) => manage({...base, operation: "read", ...extra});
  try {
    assert.equal((await read()).enabled, false);
    assert.deepEqual(await readdir(root), []);
    const preview = await manage({...base, expected_revision: 0, min_interval_minutes: 60, execute: false});
    assert.equal(preview.preview, true);
    assert.deepEqual(await readdir(root), []);
    await manage({...base, expected_revision: 0, min_interval_minutes: 60});
    await manage({...base, agent_id: "a", expected_revision: 1, min_interval_minutes: 1440});
    await manage({...base, agent_id: "a", automation_id: "daily", expected_revision: 2, min_interval_minutes: 2880});
    assert.equal((await read({agent_id: "a", automation_id: "daily"})).min_interval_minutes, 2880);
    assert.equal((await read({agent_id: "a", automation_id: "other"})).min_interval_minutes, 1440);
    assert.equal((await read({agent_id: "b"})).min_interval_minutes, 60);
    await assert.rejects(manage({...base, agent_id: "a", expected_revision: 3, min_interval_minutes: 1}), /owner-approved/);
    await assert.rejects(manage({...base, expected_revision: 0, min_interval_minutes: 100}), /revision conflict/);
    const concurrent = await Promise.allSettled([120, 180].map(min => manage({...base, expected_revision: 3, min_interval_minutes: min})));
    assert.equal(concurrent.filter(r => r.status === "fulfilled").length, 1);
    assert.equal(concurrent.filter(r => r.status === "rejected").length, 1);
    const cleared = await manage({...base, agent_id: "a", expected_revision: 4, min_interval_minutes: 0, approve_reduction: true});
    assert.equal(cleared.configuration_revision, 5);
    assert.ok([120, 180].includes(Number(cleared.min_interval_minutes)));
    assert.equal((await read({agent_id: "a", automation_id: "daily"})).min_interval_minutes, 2880);
    // A fresh read reconstructs the policy from the real file, not process cache.
    const saved = JSON.parse(await readFile(cadenceStorePath(root, "fixture"), "utf8"));
    assert.equal(saved.revision, 5);
    assert.equal(saved.rules.length, 3);
    assert.equal((await read()).enforcement, "scheduler_recommendation");
    await writeFile(cadenceStorePath(root, "fixture"), '{"broken":true}');
    await assert.rejects(read(), /identity\/schema mismatch/);
  } finally {await rm(root, {recursive: true, force: true});}
});

test("every backoff/reset interval respects the owner floor without a 60-minute ceiling", () => {
  for (const sequence of [[3, 6, 12], [15, 30, 60], [120, 240, 480], [1440, 2880]]) {
    const result = progression({progression: sequence, min_interval_minutes: 1440});
    assert.ok((result.progression as number[]).every(n => n >= 1440));
  }
  assert.deepEqual(progression({progression: [3, 6, 12], min_interval_minutes: 0}).progression, [3, 6, 12]);
  assert.throws(() => progression({progression: [1], min_interval_minutes: -1}));
  assert.throws(() => progression({progression: [1], min_interval_minutes: 1.5}));
});
