/** Legacy event-log append admission. Python owns the locked bytes and codec;
 * this owner allocates sequences and rejects a whole conflicting/stale batch. */
import type {JsonObject} from "../effect_program.ts";
import {requireJsonObject, requireNonEmptyString} from "../runtime_decode.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";

type EventIdentity = {event_id: string; fingerprint: string};
type StoredIdentity = EventIdentity & {append_sequence: number};
type AppendChoice = {kind: "replay" | "append"; event_id: string; append_sequence: number};

function sequence(value: unknown, minimum: number): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < minimum) {
    throw new EffectRuntimeRequestError("event append sequence must be a safe integer");
  }
  return value;
}

function identity(value: unknown): EventIdentity {
  const row = requireJsonObject(value, "event identity");
  const fingerprint = requireNonEmptyString(row.fingerprint, "event fingerprint");
  if (!/^[a-f0-9]{64}$/.test(fingerprint)) throw new EffectRuntimeRequestError("invalid event fingerprint");
  return {event_id: requireNonEmptyString(row.event_id, "event_id"), fingerprint};
}

export function planStateEventAppend(value: unknown): JsonObject {
  const request = requireJsonObject(value, "state event append plan");
  if (request.schema_version !== "loopx_state_event_append_plan_v0" ||
      !Array.isArray(request.existing) || !Array.isArray(request.events)) {
    throw new EffectRuntimeRequestError("invalid state event append plan");
  }
  requireNonEmptyString(request.source_checksum, "source_checksum");
  if (request.expected_checksum !== null) {
    requireNonEmptyString(request.expected_checksum, "expected_checksum");
  }
  const result = (status: "planned" | "rejected", fields: JsonObject): JsonObject =>
    ({schema_version: "loopx_state_event_append_result_v0", status, ...fields});
  if (request.expected_checksum !== null && request.expected_checksum !== request.source_checksum) {
    return result("rejected", {reason_code: "event_source_changed"});
  }
  let last = sequence(request.last_sequence, 0);
  const known = new Map<string, StoredIdentity>();
  for (const raw of request.existing) {
    const row = requireJsonObject(raw, "stored event identity");
    const record = {...identity(row), append_sequence: sequence(row.append_sequence, 1)};
    if (record.append_sequence > last || known.has(record.event_id)) {
      throw new EffectRuntimeRequestError("inconsistent stored event identity");
    }
    known.set(record.event_id, record);
  }
  const choices: AppendChoice[] = [];
  for (const raw of request.events) {
    const item = identity(raw), prior = known.get(item.event_id);
    if (prior) {
      if (prior.fingerprint !== item.fingerprint) {
        return result("rejected", {reason_code: "event_id_conflict", event_id: item.event_id});
      }
      choices.push({kind: "replay", event_id: item.event_id, append_sequence: prior.append_sequence});
    } else {
      if (last === Number.MAX_SAFE_INTEGER) {
        return result("rejected", {reason_code: "event_sequence_exhausted"});
      }
      const record = {...item, append_sequence: ++last};
      known.set(item.event_id, record);
      choices.push({kind: "append", event_id: item.event_id, append_sequence: record.append_sequence});
    }
  }
  return result("planned", {choices});
}
