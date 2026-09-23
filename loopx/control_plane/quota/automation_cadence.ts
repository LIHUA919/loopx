/** Owner cadence policy and schedule projection; independent of hint resets. */
import {readFile} from "node:fs/promises";
import {createHash} from "node:crypto";
import type {JsonObject} from "../effect_program.ts";
import {atomicWriteJson, withFileMutationLock} from "../effect_runtime_io.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {requireJsonObject, requireNonEmptyString, requireInteger, requireStringLiteral} from "../runtime_decode.ts";
import {schedulerStatePath} from "../scheduler/state_store.ts";

const SCHEMA = "automation_cadence_store_v1";
const RESULT = "automation_cadence_result_v1";
type Scope = {agent_id: string | null; automation_id: string | null};
type Rule = Scope & {min_interval_minutes: number; revision: number; owner_reference: string};
type Store = {schema_version: typeof SCHEMA; goal_id: string; revision: number; rules: Rule[]};
const fail = (message: string): never => {throw new EffectRuntimeRequestError(message, "automation_cadence_invalid");};
function text(value: unknown, name: string): string {
  const s = requireNonEmptyString(value, name).trim();
  if (!s || s.length > 256 || /[\u0000-\u001f]/.test(s)) fail(`${name} is invalid`);
  return s;
}
function integer(value: unknown, name: string): number {
  const n = requireInteger(value, name);
  if (!Number.isSafeInteger(n) || n < 0) fail(`${name} must be a non-negative safe integer`);
  return n;
}
function minutes(value: unknown): number {
  const n = integer(value, "min_interval_minutes");
  if (n > 525600) fail("min_interval_minutes exceeds one year");
  return n;
}
function scope(p: JsonObject): Scope {
  const agent_id = p.agent_id == null ? null : text(p.agent_id, "agent_id");
  const automation_id = p.automation_id == null ? null : text(p.automation_id, "automation_id");
  if (automation_id && !agent_id) fail("automation scope requires agent_id");
  return {agent_id, automation_id};
}
const key = (s: Scope): string => createHash("sha256").update(JSON.stringify([s.agent_id, s.automation_id])).digest("hex");
function decode(value: unknown, goal: string): Store {
  const p = requireJsonObject(value, "cadence store");
  if (p.schema_version !== SCHEMA || p.goal_id !== goal) fail("cadence store identity/schema mismatch");
  const revision = integer(p.revision, "revision");
  if (!Array.isArray(p.rules)) fail("cadence rules must be an array");
  const seen = new Set<string>();
  const rules = (p.rules as unknown[]).map(raw => {
    const r = requireJsonObject(raw, "rule"), s = scope(r), k = key(s);
    if (seen.has(k)) fail("duplicate cadence scope");
    seen.add(k);
    const v = integer(r.revision, "rule revision");
    if (v > revision) fail("rule revision exceeds configuration revision");
    return {...s, min_interval_minutes: minutes(r.min_interval_minutes), revision: v, owner_reference: text(r.owner_reference, "owner_reference")};
  });
  return {schema_version: SCHEMA, goal_id: goal, revision, rules};
}
export function cadenceStorePath(runtimeRoot: string, goalId: string): string {
  return schedulerStatePath(runtimeRoot, {goalId, agentId: "owner-policy", surface: "quota", stateKey: "automation-cadence-v1"});
}
async function load(path: string, goal: string): Promise<Store> {
  try {return decode(JSON.parse(await readFile(path, "utf8")), goal);}
  catch (e) {
    if ((e as NodeJS.ErrnoException).code !== "ENOENT") throw e;
    return {schema_version: SCHEMA, goal_id: goal, revision: 0, rules: []};
  }
}
function applicable(r: Rule, s: Scope): boolean {
  return r.agent_id === null || (r.agent_id === s.agent_id && (r.automation_id === null || r.automation_id === s.automation_id));
}
function projection(store: Store, s: Scope): JsonObject {
  const rules = store.rules.filter(r => applicable(r, s));
  const floor = Math.max(0, ...rules.map(r => r.min_interval_minutes));
  return {
    schema_version: RESULT, ok: true, enabled: floor > 0, goal_id: store.goal_id, ...s,
    configuration_revision: store.revision, min_interval_minutes: floor,
    sources: rules,
    reason: floor === 0 ? "unconfigured" : "owner_minimum_interval",
    enforcement: "scheduler_recommendation", pre_model_admission: "not_qualified",
  };
}
/** Pure typed calculation shared by scheduler adapters; no policy mutation. */
export function projectCadenceProgression(p: JsonObject): JsonObject {
  const floor = minutes(p.min_interval_minutes);
  if (!Array.isArray(p.progression) || p.progression.length === 0) fail("progression is required");
  const progression = [...new Set((p.progression as unknown[]).map(v => Math.max(1, floor, integer(v, "interval"))))];
  return {progression, effective_interval_minutes: progression[0], min_interval_minutes: floor,
    reason: floor > 0 ? "owner_floor_applied_after_backoff" : "unconfigured"};
}
/** Apply one policy snapshot to both scheduler projections in one transport call. */
export function projectCadenceSchedule(p: JsonObject): JsonObject {
  const floor = minutes(p.min_interval_minutes);
  return {
    local: projectCadenceProgression({progression: p.local, min_interval_minutes: floor}).progression,
    app: projectCadenceProgression({progression: p.app, min_interval_minutes: floor}).progression,
    local_max: Math.max(integer(p.local_max, "local_max"), floor),
    app_max: Math.max(integer(p.app_max, "app_max"), floor),
    floor,
    guarantee: {
      pre_model_atomic_admission: "not_qualified",
      model_wakeup_tokens_prevented: false,
      schedule_readback_required: true,
      unsupported_schedule_action: "pause_affected_automation",
      boundary: "schedule recommendation only; App hook coverage is not qualified",
      on_apply_failure: "pause_affected_automation_do_not_shorten_interval",
    },
  };
}

/** Local owner CLI boundary. Same-UID filesystem access is not an authentication sandbox. */
export async function manageAutomationCadence(p: JsonObject): Promise<JsonObject> {
  const operation = requireStringLiteral(p.operation, ["read", "configure"] as const, "operation");
  const goal = text(p.goal_id, "goal_id"), s = scope(p);
  const path = cadenceStorePath(requireNonEmptyString(p.runtime_root, "runtime_root"), goal);
  const act = async (): Promise<JsonObject> => {
    const store = await load(path, goal);
    if (operation === "read") return projection(store, s);
    if (operation === "configure") {
      if (integer(p.expected_revision, "expected_revision") !== store.revision) fail("configuration revision conflict; read current policy before changing it");
      const value = minutes(p.min_interval_minutes), reference = text(p.owner_reference, "owner_reference");
      const old = store.rules.find(r => key(r) === key(s));
      if (value < (old?.min_interval_minutes ?? 0) && p.approve_reduction !== true) fail("lowering or disabling the floor requires explicit owner-approved reduction");
      const rule = {...s, min_interval_minutes: value, revision: store.revision + 1, owner_reference: reference};
      store.rules = [...store.rules.filter(r => key(r) !== key(s)), rule];
      store.revision++;
      if (p.execute === true) await atomicWriteJson(path, store);
      return {...projection(store, s), written: p.execute === true, preview: p.execute !== true};
    }
    return fail("unsupported cadence operation");
  };
  // Read-only policy inspection never creates directories or lock files.
  return operation === "read" || p.execute !== true ? act() : withFileMutationLock(path, act);
}
