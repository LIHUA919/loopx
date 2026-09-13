/** Disposable child for renewal race and lost-response integration tests. */
import {readFileSync} from "node:fs";
import {openLocalAuthorityStore} from "../../loopx/control_plane/coordination/local_authority_provider.ts";
import {executeCanonicalTaskLeaseRenew} from "../../loopx/control_plane/coordination/task_lease_renew.ts";
import type {AuthorityStore} from "../../loopx/control_plane/coordination/authority_store.ts";
const [path, mode, ttl] = process.argv.slice(2);
const request = JSON.parse(readFileSync(path!, "utf8"));
const store = await openLocalAuthorityStore(request.runtime_root, request.goal_id);
let release: () => void = () => {};
const barrier = new Promise<void>(resolve => {release = resolve;});
process.on("message", () => release());
const measured: AuthorityStore = {
  storeIdentity: () => store.storeIdentity(), readReceipt: id => store.readReceipt(id),
  scanCommitted: (...args) => store.scanCommitted(...args),
  loadAuthority: async () => {
    const head = await store.loadAuthority();
    if (mode === "race") {process.send!({ready: true}); await barrier;}
    return head;
  },
  commitAuthority: async input => {
    if (mode === "before") process.kill(process.pid, "SIGKILL");
    const result = await store.commitAuthority(input);
    if (mode === "after" && result.status === "applied") process.kill(process.pid, "SIGKILL");
    return result;
  },
};
const result = await executeCanonicalTaskLeaseRenew(measured, {...request,
  registered_agents: ["agent-a", "agent-b"], now: new Date(request.now), ttl_seconds: Number(ttl)});
process.stdout.write(JSON.stringify(result));
if (process.connected) process.disconnect();
