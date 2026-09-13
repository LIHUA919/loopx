import {withFileMutationLock} from "../effect_runtime_io.ts";
import {requireShadowPrimaryWriteAllowed, shadowMaintenanceLockPath} from "./shadow_management.ts";

/** Canonical command writers share the maintenance guard; provider CAS owns state. */
export async function withCanonicalWriter<T>(root: string, goalId: string, dryRun: boolean, write: () => Promise<T>): Promise<T> {
  if (dryRun) return await write();
  return await withFileMutationLock(shadowMaintenanceLockPath(root, goalId), async () => {
    await requireShadowPrimaryWriteAllowed(root, goalId);
    return await write();
  });
}
