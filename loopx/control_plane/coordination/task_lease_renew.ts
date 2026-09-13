import type {JsonObject} from "../effect_program.ts";
import type {AuthorityStore, AuthorityStoreCommit} from "./authority_store.ts";
import {AuthorityStoreProtocolError, canonicalAuthorityObject} from "./authority_store_codec.ts";
import {CoordinationCommandReceipt, commandReceiptResult} from "./command_receipt.ts";
import {indexCoordinationProjection, prepareCoordinationProjectionCommit, validateCoordinationTodoReadModel} from "./coordination_projection.ts";
import {decideTaskLeaseLifecycle} from "../work_items/task_lease_lifecycle_decision.ts";
import {requireStringLiteral} from "../runtime_decode.ts";
import {HANDOFF_MODES} from "./handoff_mode_policy.ts";
import {leaseEpoch, leaseIsActive, leaseVersion, normalizeAgent, normalizeGoalId,
  normalizeIdempotencyKey, normalizeOwner, normalizeTodoId,
  normalizeTtl, TaskLeaseAcquireError, utcIsoformat, type LeaseRecord} from "../work_items/task_lease_acquire.ts";
import {taskLeaseOperationIdentity, taskLeaseOperationRequestDigest} from "../work_items/task_lease_operation_identity.ts";

export const CANONICAL_TASK_LEASE_RENEW_RESULT_SCHEMA = "loopx_canonical_task_lease_renew_result_v0";
export const CANONICAL_TASK_LEASE_RENEW_RECEIPT_SCHEMA = "loopx_canonical_task_lease_renew_receipt_v0";
export interface CanonicalTaskLeaseRenewInput {
  goal_id: string;
  todo_id: string;
  owner: string;
  idempotency_key: string;
  expected_version: number | null;
  ttl_seconds: number;
  registered_agents: readonly string[];
  now: Date;
}
function failed(code: string, reason: string): JsonObject & {schema_version: typeof CANONICAL_TASK_LEASE_RENEW_RESULT_SCHEMA} {
  return {schema_version: CANONICAL_TASK_LEASE_RENEW_RESULT_SCHEMA, status: "failed", changed: false,
    reason_code: code, reason, failure_stage: "validation"};
}
function leaseRecord(value: JsonObject, input: CanonicalTaskLeaseRenewInput): LeaseRecord {
  if ((value.schema_version !== undefined && value.schema_version !== "task_lease_v0") ||
      (value.goal_id !== undefined && value.goal_id !== input.goal_id) || value.todo_id !== input.todo_id ||
      (value.status !== "active" && value.status !== "released")) {
    throw new AuthorityStoreProtocolError("canonical lease identity or schema is invalid");
  }
  if (typeof value.owner !== "string" || typeof value.idempotency_key !== "string" ||
      normalizeOwner(value.owner) !== value.owner || normalizeIdempotencyKey(value.idempotency_key) !== value.idempotency_key) {
    throw new AuthorityStoreProtocolError("canonical lease owner and execution key must be normalized strings");
  }
  leaseVersion(value); leaseEpoch(value);
  if (value.write_scopes !== undefined && (!Array.isArray(value.write_scopes) ||
      value.write_scopes.some(scope => typeof scope !== "string"))) {
    throw new AuthorityStoreProtocolError("canonical lease write_scopes must be strings");
  }
  return value;
}

/** Renew uses canonical facts and the existing lifecycle decision, never a lease file. */
export async function executeCanonicalTaskLeaseRenew(store: AuthorityStore, raw: CanonicalTaskLeaseRenewInput,
  beforeCommit?: (lease: JsonObject) => Promise<void>): Promise<JsonObject> {
  let input: CanonicalTaskLeaseRenewInput;
  try {
    input = {...raw, goal_id: normalizeGoalId(raw.goal_id), todo_id: normalizeTodoId(raw.todo_id),
      owner: normalizeOwner(raw.owner), idempotency_key: normalizeIdempotencyKey(raw.idempotency_key),
      ttl_seconds: normalizeTtl(raw.ttl_seconds), registered_agents: raw.registered_agents.map(normalizeOwner)};
    if (input.expected_version === null) return failed("version_required", "task lease renew requires the current lease version");
    if (!Number.isSafeInteger(input.expected_version) || input.expected_version < 0) return failed("invalid_expected_version", "lease version must be a non-negative safe integer");
    if (!(input.now instanceof Date) || !Number.isFinite(input.now.valueOf())) return failed("invalid_clock", "renew requires a valid runtime clock");
  } catch (error) { return failed(error instanceof TaskLeaseAcquireError ? error.code : "invalid_canonical_renew", error instanceof Error ? error.message : "invalid canonical renewal"); }
  const identityInput = {...input, operation: "renew", new_owner: null, new_idempotency_key: null};
  const operationId = `lease-renew:${taskLeaseOperationIdentity(identityInput)!}`;
  const receipt = new CoordinationCommandReceipt({result_schema: CANONICAL_TASK_LEASE_RENEW_RESULT_SCHEMA,
    identity: {schema_version: CANONICAL_TASK_LEASE_RENEW_RECEIPT_SCHEMA, operation_id: operationId,
      goal_id: input.goal_id, request_sha256: taskLeaseOperationRequestDigest(identityInput)!}, failure: failed,
    decode(original) {
      const payload = commandReceiptResult(original);
      let lease: LeaseRecord;
      try {
        lease = leaseRecord(canonicalAuthorityObject(payload.fields.lease, "renew receipt lease"), input);
        leaseIsActive(lease, new Date(0)); // Validate the timestamp without requiring current authority.
      } catch (error) {
        throw new AuthorityStoreProtocolError(error instanceof Error ? error.message : "invalid renewal receipt lease");
      }
      if (payload.fields.renewed !== true || payload.changed !== true || lease.owner !== input.owner ||
          lease.status !== "active" || lease.idempotency_key !== input.idempotency_key || leaseVersion(lease) !== input.expected_version! + 1) {
        throw new AuthorityStoreProtocolError("canonical renewal receipt does not match its intent");
      }
      return {...payload, fields: {...payload.fields, operation_id: operationId}};
    }});
  let commit: AuthorityStoreCommit;
  try {
    const replay = await receipt.read(store);
    if (replay !== null) return replay;
    const head = await store.loadAuthority();
    if (head.status !== "loaded") return {schema_version: CANONICAL_TASK_LEASE_RENEW_RESULT_SCHEMA, ...head, failure_stage: "validation", changed: false};
    const index = indexCoordinationProjection(head.head, input.goal_id);
    validateCoordinationTodoReadModel(head.head, input.goal_id);
    const todo = index.todos.get(input.todo_id), rawLease = index.leases.get(input.todo_id);
    const lease = rawLease ? leaseRecord(rawLease, input) : null;
    const excluded = todo?.excluded_agents ?? [];
    if (!Array.isArray(excluded) || excluded.some(value => typeof value !== "string")) return failed("invalid_coordination_projection", "Todo exclusions must be strings");
    const mode = requireStringLiteral(head.head.handoff_mode ?? "legacy", HANDOFF_MODES, "canonical handoff_mode");
    const decision = decideTaskLeaseLifecycle({handoff_mode: mode, registered_agents: input.registered_agents,
      todo: todo ? {todo_id: input.todo_id, status: String(todo.status), claimed_by: normalizeAgent(todo.claimed_by), excluded_agents: excluded as string[]} : null,
      lease: lease ? {present: true, active: leaseIsActive(lease, input.now), status: String(lease.status),
        owner: normalizeOwner(lease.owner), idempotency_key: normalizeIdempotencyKey(lease.idempotency_key),
        version: leaseVersion(lease), lease_epoch: leaseEpoch(lease), write_scopes: (lease.write_scopes ?? []) as string[], acquire_ttl_seconds: null} : null,
      command: {operation: "renew", owner: input.owner, idempotency_key: input.idempotency_key,
        expected_version: input.expected_version, ttl_seconds: input.ttl_seconds, new_owner: null, new_idempotency_key: null}});
    if (decision.outcome !== "apply" || !lease || !decision.next_lease) return {...failed(decision.code, `canonical task lease renew rejected: ${decision.code}`), handoff_mode: mode};
    if (!Number.isSafeInteger(decision.next_lease.version)) return failed("lease_generation_exhausted", "lease version cannot advance safely");
    const next = {...lease, version: decision.next_lease.version, updated_at: utcIsoformat(input.now),
      expires_at: utcIsoformat(new Date(input.now.valueOf() + input.ttl_seconds * 1000))};
    commit = prepareCoordinationProjectionCommit({goal_id: input.goal_id, operation_id: operationId,
      expected_provider_revision: head.provider_revision, projection: head.head, mutations: [{kind: "lease_upsert", lease: next}]});
    commit.receipts = [{schema_version: CANONICAL_TASK_LEASE_RENEW_RECEIPT_SCHEMA, operation_id: operationId,
      goal_id: input.goal_id, request_sha256: taskLeaseOperationRequestDigest(identityInput)!,
      result: {changed: true, renewed: true, lease: next, handoff_mode: mode}}];
    await beforeCommit?.(next);
  } catch (error) {
    return failed(error instanceof TaskLeaseAcquireError ? error.code : "invalid_canonical_renew_state",
      error instanceof Error ? error.message : "canonical renewal state could not be read");
  }
  try {
    const result = await receipt.commit(store, commit);
    return result.status === "applied" || result.status === "replayed" || result.status === "recovered"
      ? result : {...result, failure_stage: "durable_writeback"};
  } catch {
    return {schema_version: CANONICAL_TASK_LEASE_RENEW_RESULT_SCHEMA, status: "ambiguous", changed: false,
      reason_code: "canonical_renew_recovery_required", reason: "renewal response is uncertain; recover the same operation",
      recovery: {operation_id: operationId, retry_with_same_operation_id: true}, failure_stage: "durable_writeback"};
  }
}
