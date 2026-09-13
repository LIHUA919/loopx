import { createRequire } from "node:module";
import {AuthorityStoreProtocolError} from "./authority_store_codec.ts";

const require = createRequire(import.meta.url);

export interface SqliteRuntimeInfo {
  node_version: string;
  sqlite_version: string;
  sqlite_source_id: string;
  synchronous_statement_finalization: boolean;
}

/** Official SQLite 3 release lines containing the WAL-reset fix. */
export function hasSqliteWalResetFix(version: string): boolean {
  const match = /^(0|[1-9]\d{0,2})\.(0|[1-9]\d{0,2})\.(0|[1-9]\d{0,2})$/.exec(version);
  if (!match) return false;
  const [major, minor, patch] = match.slice(1).map(Number);
  if (major !== 3) return false;
  return minor > 51 || (minor === 51 && patch >= 3) ||
    (minor === 50 && patch >= 7) || (minor === 44 && patch >= 6);
}

let cached: {driver: typeof import("node:sqlite"); info: SqliteRuntimeInfo} | undefined;

/** Probe only an in-memory database before any authority path is created. */
export function sqliteAuthorityRuntime(): {driver: typeof import("node:sqlite"); info: SqliteRuntimeInfo} {
  if (cached) return cached;
  let driver: typeof import("node:sqlite");
  try { driver = require("node:sqlite") as typeof import("node:sqlite"); }
  catch { throw new AuthorityStoreProtocolError("SQLite authority requires node:sqlite; use the qualified Node 22.22.3 runtime. File authority uses the public Node minimum"); }
  const db = new driver.DatabaseSync(":memory:");
  let version = "", sourceId = "";
  let statement: ReturnType<typeof db.prepare>;
  try {
    const row = db.prepare("SELECT sqlite_version() AS version, sqlite_source_id() AS source_id").get();
    version = String(row?.version ?? ""); sourceId = String(row?.source_id ?? "");
    statement = db.prepare("SELECT 1");
  } finally { db.close(); }
  let finalized = false;
  try { statement.get(); }
  catch (error) { finalized = (error as NodeJS.ErrnoException).code === "ERR_INVALID_STATE"; }
  if (!finalized || !hasSqliteWalResetFix(version)) {
    throw new AuthorityStoreProtocolError(`SQLite authority runtime is not qualified (SQLite ${version}, synchronous statement finalization=${finalized}); require the WAL-reset fix in SQLite 3.51.3+, 3.50.7+ or 3.44.6+ and finalized statements on close. Use the qualified Node 22.22.3 runtime; File authority uses the public Node minimum`);
  }
  const info = {node_version: process.version, sqlite_version: version, sqlite_source_id: sourceId,
    synchronous_statement_finalization: finalized};
  cached = {driver, info};
  return cached;
}
