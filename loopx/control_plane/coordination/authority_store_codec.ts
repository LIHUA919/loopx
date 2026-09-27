import { createHash } from "node:crypto";

import type { JsonObject } from "../effect_program.ts";
import type { AuthorityStoreCommit } from "./authority_store.ts";

/** Provider-neutral validation failure at the authority-store boundary. */
export class AuthorityStoreProtocolError extends Error {}

export function isAuthorityJsonObject(value: unknown): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function authorityUnicodeCompare(left: string, right: string): number {
  // Walk code points without allocating two arrays for every sort comparison.
  // JS's default sort compares UTF-16 units, which would change persisted
  // revisions for supplementary characters relative to BMP characters.
  let leftIndex = 0, rightIndex = 0;
  while (leftIndex < left.length && rightIndex < right.length) {
    const leftPoint = left.codePointAt(leftIndex)!;
    const rightPoint = right.codePointAt(rightIndex)!;
    if (leftPoint !== rightPoint) return leftPoint - rightPoint;
    leftIndex += leftPoint > 0xffff ? 2 : 1;
    rightIndex += rightPoint > 0xffff ? 2 : 1;
  }
  return leftIndex < left.length ? 1 : rightIndex < right.length ? -1 : 0;
}

export function hasExactAuthorityKeys(
  value: JsonObject,
  keys: readonly string[],
): boolean {
  const actual = Object.keys(value).sort(authorityUnicodeCompare);
  const expected = [...keys].sort(authorityUnicodeCompare);
  return actual.length === expected.length &&
    actual.every((key, index) => key === expected[index]);
}

export function requireAuthorityStoreId(value: unknown, name: string): string {
  if (typeof value !== "string" || value.trim() !== value || value.length === 0) {
    throw new AuthorityStoreProtocolError(`${name} must be a non-empty trimmed string`);
  }
  return value;
}

export function canonicalAuthorityJson(
  value: unknown,
  stack = new Set<object>(),
): unknown {
  if (value === null || typeof value === "string" || typeof value === "boolean") {
    return value;
  }
  if (typeof value === "number") {
    if (!Number.isFinite(value)) {
      throw new AuthorityStoreProtocolError("JSON numbers must be finite");
    }
    return value;
  }
  if (Array.isArray(value)) {
    if (stack.has(value)) throw new AuthorityStoreProtocolError("JSON value must be acyclic");
    stack.add(value);
    try {
      return value.map((item) => canonicalAuthorityJson(item, stack));
    } finally {
      stack.delete(value);
    }
  }
  if (!isAuthorityJsonObject(value)) {
    throw new AuthorityStoreProtocolError("value must be strict JSON");
  }
  const prototype = Object.getPrototypeOf(value);
  if (prototype !== Object.prototype && prototype !== null) {
    throw new AuthorityStoreProtocolError("JSON objects must be plain objects");
  }
  if (stack.has(value)) throw new AuthorityStoreProtocolError("JSON value must be acyclic");
  stack.add(value);
  try {
    return Object.fromEntries(
      Object.keys(value).sort(authorityUnicodeCompare).map((key) => [
        key,
        canonicalAuthorityJson(value[key], stack),
      ]),
    );
  } finally {
    stack.delete(value);
  }
}

export function canonicalAuthorityObject(value: unknown, name: string): JsonObject {
  if (!isAuthorityJsonObject(value)) {
    throw new AuthorityStoreProtocolError(`${name} must be an object`);
  }
  return canonicalAuthorityJson(value) as JsonObject;
}

export function canonicalAuthorityObjectList(
  value: unknown,
  name: string,
): JsonObject[] {
  if (!Array.isArray(value)) {
    throw new AuthorityStoreProtocolError(`${name} must be an array`);
  }
  return value.map((item, index) =>
    canonicalAuthorityObject(item, `${name}[${index}]`)
  );
}

export function canonicalAuthorityBytes(value: unknown): Buffer {
  return Buffer.from(JSON.stringify(canonicalAuthorityJson(value)), "utf8");
}

export function canonicalAuthoritySha256(value: unknown): string {
  return createHash("sha256").update(canonicalAuthorityBytes(value)).digest("hex");
}

export type CanonicalAuthorityDigest = (value: unknown) => string;

/**
 * Recompute the exact canonical v0 digest while reusing encoded long strings
 * within one verification window. Retained projections often share a large
 * unchanged value across many deltas; serializing it for every state and
 * commit proof dominates historical reads. The cache is bounded and never
 * survives the caller's window, so a later read still verifies fresh bytes.
 */
export function createCanonicalAuthorityDigestWindow(): CanonicalAuthorityDigest {
  const maxEntryBytes = 2 * 1024 * 1024;
  const maxCachedBytes = 4 * 1024 * 1024;
  const encoded = new Map<string, Buffer>();
  let cachedBytes = 0;
  const stringBytes = (value: string): string | Buffer => {
    if (value.length < 1024) return JSON.stringify(value);
    const previous = encoded.get(value);
    if (previous) return previous;
    const bytes = Buffer.from(JSON.stringify(value), "utf8");
    if (bytes.byteLength > maxEntryBytes) return bytes;
    while (cachedBytes + bytes.byteLength > maxCachedBytes) {
      const oldest = encoded.keys().next().value;
      if (oldest === undefined) break;
      cachedBytes -= encoded.get(oldest)!.byteLength;
      encoded.delete(oldest);
    }
    encoded.set(value, bytes);
    cachedBytes += bytes.byteLength;
    return bytes;
  };
  const hasLongString = (part: unknown): boolean => {
    if (typeof part === "string") return part.length >= 1024;
    if (part === null || typeof part !== "object") return false;
    if (Array.isArray(part)) return part.some(hasLongString);
    return Object.keys(part).some(key => key.length >= 1024 || hasLongString((part as JsonObject)[key]));
  };
  return (value: unknown): string => {
    const canonical = canonicalAuthorityJson(value);
    const hash = createHash("sha256");
    // Ordinary record-rich states have nothing reusable in this cache. Keep
    // their native JSON encoder instead of paying per-token JS traversal.
    if (!hasLongString(canonical)) return hash.update(JSON.stringify(canonical)).digest("hex");
    // Batch small tokens: calling the native hash binding per punctuation/key
    // regresses ordinary many-field Todo projections without large strings.
    let pending = "";
    const flush = (): void => {
      if (pending) { hash.update(pending); pending = ""; }
    };
    const append = (chunk: string | Buffer): void => {
      if (typeof chunk !== "string") { flush(); hash.update(chunk); return; }
      pending += chunk;
      if (pending.length >= 8192) flush();
    };
    const visit = (part: unknown): void => {
      if (part === null) { append("null"); return; }
      if (typeof part === "string") { append(stringBytes(part)); return; }
      if (typeof part === "number" || typeof part === "boolean") {
        append(JSON.stringify(part)); return;
      }
      if (Array.isArray(part)) {
        append("[");
        for (let index = 0; index < part.length; index++) {
          if (index) append(",");
          if (Object.hasOwn(part, index)) visit(part[index]);
          else append("null"); // JSON.stringify represents sparse slots as null.
        }
        append("]");
        return;
      }
      append("{");
      let first = true;
      for (const key of Object.keys(part as JsonObject)) {
        if (!first) append(",");
        first = false;
        append(stringBytes(key));
        append(":");
        visit((part as JsonObject)[key]);
      }
      append("}");
    };
    visit(canonical);
    flush();
    return hash.digest("hex");
  };
}

export function parseAuthorityCursor(value: string | null): bigint {
  if (value === null) return 0n;
  if (typeof value !== "string" || !/^[1-9]\d*$/.test(value)) {
    throw new AuthorityStoreProtocolError("provider cursor is invalid");
  }
  return BigInt(value);
}

export function normalizeAuthorityStoreCommit(
  commit: AuthorityStoreCommit,
): AuthorityStoreCommit {
  const expectedRevision = commit.expected_provider_revision;
  if (
    expectedRevision !== null &&
    (typeof expectedRevision !== "string" || expectedRevision.length === 0)
  ) {
    throw new AuthorityStoreProtocolError("expected provider revision is invalid");
  }
  return {
    expected_provider_revision: expectedRevision,
    operation_id: requireAuthorityStoreId(commit.operation_id, "operation id"),
    events: canonicalAuthorityObjectList(commit.events, "events"),
    next_projection: canonicalAuthorityObject(commit.next_projection, "projection"),
    receipts: canonicalAuthorityObjectList(commit.receipts, "receipts"),
  };
}
