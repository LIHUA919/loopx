import type {JsonObject} from "../effect_program.ts";
import {canonicalAuthoritySha256} from "../coordination/authority_store_codec.ts";
import {FileAuthorityStore} from "../coordination/file_authority_store.ts";
import {SqliteAuthorityStore} from "../coordination/sqlite_authority_store.ts";
import {openLocalAuthorityStore, localAuthorityOpenFailure} from "../coordination/local_authority_provider.ts";
import {loadLegacyCoordinationWriterFence} from "../coordination/legacy_writer_fence.ts";
import {withCanonicalWriter} from "../coordination/local_authority_write.ts";
import {ShadowManagementError} from "../coordination/shadow_management.ts";
import {EffectRuntimeLockTimeoutError} from "../effect_runtime_errors.ts";
import {executeCanonicalTaskLeaseRenew} from "../coordination/task_lease_renew.ts";
import {revalidateAuthoritySources, TaskLeaseAcquireError, type AuthorityFacts} from "./task_lease_acquire.ts";

interface LocalRenewRequest {
  runtime_root: string; goal_id: string; todo_id: string;
  owner: string | null; idempotency_key: string | null;
  expected_version: number | null; ttl_seconds: number | null; authority: AuthorityFacts | null;
}

/** A canonical-only request requires a durable fence and can never fall back. */
export async function renewCanonicalTaskLease(request: LocalRenewRequest,
  dependencies: {now: () => Date; beforeWrite?: (lease: JsonObject) => void | Promise<void>}): Promise<JsonObject> {
  const root = request.runtime_root, goalId = request.goal_id;
  const initialFence = await loadLegacyCoordinationWriterFence(root, goalId);
  const evidence: JsonObject = {source_authority: null, decision_read_from_provider: false, legacy_fallback_used: false};
  const rejected = (code: string, reason: string): JsonObject => ({status: "failed", changed: false,
    reason_code: code, reason, failure_stage: "validation", ...evidence});
  if (initialFence.status === "missing") return rejected("canonical_renew_fence_missing", "canonical renewal fence is missing; legacy fallback is forbidden");
  if (initialFence.status === "failed") return rejected(initialFence.reason_code, initialFence.reason);
  try {
    return await withCanonicalWriter(root, goalId, false, async () => {
      const verifyFence = async () => {
        const current = await loadLegacyCoordinationWriterFence(root, goalId);
        if (current.status !== "loaded" || canonicalAuthoritySha256(current.fence) !== canonicalAuthoritySha256(initialFence.fence)) {
          throw new TaskLeaseAcquireError("canonical renewal writer fence changed; inspect authority before retrying", "canonical_renew_fence_changed", {goal_id: goalId});
        }
      };
      await verifyFence();
      const store = await openLocalAuthorityStore(root, goalId);
      // Current local opening supports exactly these built-in providers. Reject
      // any future kind until its typed opening contract is adopted here.
      if (store instanceof SqliteAuthorityStore) evidence.source_authority = "sqlite_v0";
      else if (store instanceof FileAuthorityStore) evidence.source_authority = "file_v0";
      else return rejected("canonical_renew_provider_unsupported", "canonical renewal provider is unsupported");
      if (!request.owner || !request.idempotency_key || !request.authority) return rejected("authority_required", "canonical renewal needs registered actor context");
      evidence.decision_read_from_provider = true;
      const result = await executeCanonicalTaskLeaseRenew(store, {goal_id: goalId, todo_id: request.todo_id,
        owner: request.owner, idempotency_key: request.idempotency_key, expected_version: request.expected_version,
        ttl_seconds: request.ttl_seconds!, registered_agents: request.authority.registered_agents, now: dependencies.now()}, async lease => {
        await dependencies.beforeWrite?.(lease);
        await verifyFence();
        await revalidateAuthoritySources(request.authority!.source_receipts);
      });
      return {...result, ...evidence};
    });
  } catch (error) {
    if (error instanceof ShadowManagementError || error instanceof TaskLeaseAcquireError) return {...rejected(error.code, error.message), ...error.payload};
    if (error instanceof EffectRuntimeLockTimeoutError) return rejected("lock_acquire_timeout", "canonical renewal maintenance lock timed out");
    return {...rejected("canonical_renew_route_failed", error instanceof Error ? error.message : "canonical renewal route failed"), ...localAuthorityOpenFailure(error)};
  }
}
