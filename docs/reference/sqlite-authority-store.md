# SQLite authority provider

SQLite is an **opt-in local conformance candidate**, behind the existing
TypeScript `AuthorityStore` interface. File remains the default. This slice
does not promote a goal, migrate existing authority, enable cross-host writes,
or qualify ten elapsed days of operation.

## Placement and persistence

The provider belongs to the existing shared-coordination authority boundary
(`loopx/control_plane/coordination`). It is bundled with LoopX, not a new
capability or extension. Legal transitions, actor/lease checks, operation
digests and replay decisions remain with the existing typed transaction
executors. Python only admits the corresponding `sqlite_v0` source receipt.

Each goal has a separate database under the runtime's `authority/sqlite-v0`
directory. Metadata binds the goal, schema version and random database
incarnation. Provider revisions combine that incarnation with a monotonic
integer sequence; they are not authority revisions or lease epochs.

The version-1 schema contains:

| Table | Contract |
| --- | --- |
| `metadata` | Version and database/goal identity |
| `head` | One bounded pointer to the current committed projection |
| `commits` | Unique operation ID, canonical commit digest, ordered cursor, original receipts, events and full projection |

`commits` also serves as the durable projection outbox used by
`scanCommitted`. There is no independent ACK or second receipt authority.
Existing consumers resume by cursor. A unique operation index makes receipt
lookup and cursor paging indexed. The common continuity check counts the compact
covering index, so total read/write cost is not independent of history length.
It does not deserialize the complete retained payload history. Historical receipts and full projections are retained without
pruning. Fixed live state therefore produces linear database growth, not
bounded total disk use. Growing application projections require separate
retention/compaction work.

Writes use `BEGIN IMMEDIATE`, a five-second busy timeout, WAL and
`synchronous=FULL`. The head, receipt, events and outbox row commit together.
Before-COMMIT failures roll back; a COMMIT error reports an ambiguous outcome
for receipt reconciliation. Readers use committed snapshots; no history rewrite
is needed for a new transaction. SQLite storage durability still depends on the
local filesystem and hardware honoring synchronization.

Schema changes are explicit: unknown `user_version`, foreign tables or a
different goal/incarnation fail closed. No automatic migration, identity
rotation, corruption repair, or network-filesystem sharing is supported.

## Read integrity

Authority reads share one SQLite snapshot for metadata, head and requested rows.
The same check runs inside the write transaction before any new commit row:
positive unique integer cursors must have `min=1` and `count=max=head`. Thus a
missing head, rolled-back head, or internal cursor gap is rejected as
`provider_protocol_violation` before returning authority or accepting a write.

The newest row's canonical commit digest is recomputed on every authority read
and write. Historical receipt reads additionally validate their selected row;
paged scans validate each returned row and the lookahead row used for
`has_more`. The digest includes the operation ID, projection, events, receipts
and expected predecessor revision, reconstructed from the unchanged v0 sequence
contract. No schema migration or alternate digest format is introduced.

This is integrity validation of the current and accessed evidence, not a full
cryptographic audit of every historical payload on each call. Unaccessed older
row digests are checked when those rows are read. Checksums detect inconsistent
data; they do not authenticate an administrator who can rewrite both the data
and its digest. Restoration of an older, internally consistent database remains
outside this slice's qualification boundary.

## Explicit selection

Use an isolated qualification runtime and an empty, unpromoted goal. Set
`RUNTIME_ROOT` to that runtime's absolute directory. The SQLite qualification
reference is **Node 22.22.3 with SQLite 3.51.3**. The provider checks the actual
embedded SQLite version and synchronous prepared-statement finalization before
creating or opening an authority database. Record both `sqlite_version()` and
`sqlite_source_id()`; the Node version alone is insufficient.

SQLite 3.51.3 and later 3.x releases contain the
[WAL-reset concurrency fix](https://www.sqlite.org/wal.html#the_wal_reset_bug).
The fixed 3.44.x (3.44.6+) and 3.50.x (3.50.7+) backport lines are also admitted
when their driver finalizes statements on close. Unknown version strings or
unverified vendor backports fail closed. Passing these prerequisites does not
qualify the complete D2 profile.

This intentionally rejects SQLite runtimes previously accepted by the
statement-only probe, including vulnerable drivers shipped with older Node 22
releases. The public Node minimum remains 22.18 for the default File path;
SQLite requires the additional fix. No provider selection changes and no
fallback to File occur when an explicitly selected SQLite runtime is rejected.
The optional driver is still loaded only after opt-in.

From the repository checkout, preview selection:

```sh
node --experimental-sqlite --experimental-strip-types \
  loopx/control_plane/coordination/local_authority_provider.ts \
  --runtime-root "$RUNTIME_ROOT" --goal-id example
```

Apply the same command with `--execute`. It creates the empty database and a
durable per-goal selector bound to its incarnation. Repeating it is idempotent.
The command rejects an existing canonical file head or a writer fence. It does
not bootstrap or promote authority. Existing qualification/promotion gates
still govern the first canonical state, with the chosen provider used as the
destination. Do not bypass those gates to enable a live goal.

The process starting the managed Effect runtime must use the qualified Node
runtime too. Stop a previously running managed runtime normally before changing
its Node executable. Adding an experimental flag to an older Node 22 release does not fix its
statement lifecycle.

After separately admitted canonical initialization, ordinary `loopx todo`
commands use the persisted selector. For example:

```sh
loopx --registry "$REGISTRY_PATH" --runtime-root "$RUNTIME_ROOT" \
  --format json todo list --goal-id example
```

An unavailable database, malformed selector, changed incarnation or lost
selector fails closed; none silently falls back to file authority. Deleting
the generated Markdown does not delete the canonical Todo state.

Provider-open errors retain selection identity separately from request errors.
A validated SQLite selector produces `source_authority=sqlite_v0` even when its
database is missing or unreadable. An invalid, unavailable or missing selector
reports `source_authority=null` because selection is unresolved; it never guesses
file authority. Typed `local_authority_selector_*` and
`local_authority_provider_*` reason codes identify that boundary, with a
`provider_reason_code` when the store returned a more specific diagnostic.
These failures set both `decision_read_from_provider=false` and
`legacy_fallback_used=false`. Successful responses and unrelated request/domain
errors retain their existing contracts.

## Promotion failure evidence

`legacy_writer_fenced` reports whether this promotion invocation verified the
exact persisted fence against the request. Provider opening precedes that
verification so opening errors retain their selected-provider diagnostics.
Such early failures report `false`, even if a fence exists but was not read and
matched. This is not proof that legacy writes are allowed; callers must consult
the durable writer guard. After successful fence verification, later failures
retain `true`. Request fields alone never establish fencing evidence.

The failure-path regression matrix covers:

| Boundary | Evidence checked |
| --- | --- |
| Selector/database open | List, exact read, mutation, create, claim, native/planning update, compatibility edit, terminal, monitor poll, archive, ACK and promotion retain typed source/reason, no fallback and unchanged authority bytes. |
| Fence readback | Missing, malformed and mismatched fences do not establish verified fencing; an open failure cannot infer it from an existing marker. |
| After verified fence | Missing/invalid shadow and rejected qualification preserve verified fencing without canonical writes. |
| Existing promotion readback | Exact receipt/first-commit lineage permits replay; missing receipts or mismatched lineage reject without modifying authority. |

The matrix is typed against every exported runtime entrypoint so adding a new
entrypoint requires an explicit failure fixture.

These tests use disposable file/SQLite stores and the production runtime
entrypoints. They preserve the current qualification gate: mirrored file shadow
health alone does not authorize a new canonical cutover. Shared store conformance
separately covers transactional CAS, commit ambiguity, receipt reconciliation and
projection replay. No active Goal is needed for this validation.

## Stop and recovery boundary

To stop using the candidate, stop the owning goal/host runtime and retain its
database and selector. For a managed goal, `loopx configure-goal --goal-id
example --quota-compute 0 --execute` pauses automatic turns; separately stop
any active host process before taking an offline backup. Pausing does not
cancel a transaction already running.

There is deliberately no in-place switch back to file authority after commits:
that requires an explicit migration with receipt/lineage validation. Do not
delete the selector to disable the provider. Preserve the database together
with any `-wal`/`-shm` files when recovering an interrupted runtime; use SQLite
backup facilities or a fully stopped database for a coherent backup. Restoring
an older snapshot as concurrent live authority is not supported. Disposable
qualification runtimes may be retired as a whole after their processes stop.

Selection grants local storage use only. It grants no actor/lease ownership,
external service access, cross-host synchronization or promotion authority.

## Reproduce validation

Use the qualified Node executable on PATH, including the Python CLI's managed
Effect runtime. The runner records the actual Node/SQLite/source identity.

```sh
npm ci --ignore-scripts
npm run typecheck:control-plane
node --no-warnings --experimental-sqlite --experimental-strip-types --test \
  tests/control_plane_ts/sqlite_authority_store.test.ts \
  tests/control_plane_ts/local_authority_provider.test.ts \
  tests/control_plane_ts/sqlite_runtime_admission.test.ts \
  tests/control_plane_ts/sqlite_capacity.test.ts
python -m pytest -q tests/control_plane/test_sqlite_authority_cli.py
node -e "require('node:fs').mkdirSync('.local', {recursive:true})"
node --no-warnings --experimental-sqlite --experimental-strip-types \
  examples/coordination/sqlite-capacity.ts --profile rehearsal --cli \
  --output .local/sqlite-rehearsal.json
node --no-warnings --experimental-sqlite --experimental-strip-types \
  examples/coordination/sqlite-capacity.ts --profile matched-64k --cli \
  --output .local/sqlite-matched-64k.json
```

The no-argument default intentionally replaces the former 4 KiB/100k run with
a small `rehearsal`; full capacity now requires an explicit profile. The default
`rehearsal` creates 100/1,000 commits and checks runner execution,
independent invariants and cleanup; it cannot satisfy formal performance
budgets. The explicit `matched-64k` profile creates separate 10k/100k databases,
serially, with exactly 64 KiB native synthetic projection JSON and at most 4 KiB
of new event/receipt JSON per commit. Each fill write is followed by three head
reads and two indexed historical receipt reads. Both formal groups sample the
last 1,000 commits and their corresponding reads, plus 200 scan-100 samples.
This one-Todo storage axis isolates history growth; it is not the complete
multi-agent/lease/capture workload.

`--cli` adds 20 formal samples (three in rehearsal) for complete CLI mutation,
status and quota, using fresh Python processes and a newly started managed
Effect runtime for each sample. Shutdown occurs outside the timed interval in
the isolated fixture. `--python` chooses the Python executable. These figures
include process startup but do not drop the OS file cache. Cold Node-only load
and warm actual-provider calls are separate. The provider's normal per-call
connection open/close remains inside warm timing. CLI mutations happen after
the fixed-history measurement; their extra commits are reported separately.

Reports carry p50/p95/p99 and counts, parent-process RSS, application request
JSON bytes and separate DB/WAL/SHM sizes at the target history. Resource-usage
peak RSS is process-lifetime across both groups; sampled axis RSS is separate,
and CLI child RSS is not measured. Application bytes, final files, SQLite
logical writes, cumulative WAL traffic and physical device writes are different
metrics. The unavailable write-traffic and pure busy-wait metrics remain
`missing`; a final WAL size of zero proves no cumulative-write bound.

Each axis reserves 5 GiB free space, caps its database at 16 GiB and checks a
2,400-second fill budget. All data are generated in a new temporary directory;
there is no flag to select an existing Goal/runtime for writes. Keep generated
reports in ignored local storage. Failure results survive in the report and
exit nonzero; omitted or incomplete evidence never becomes a pass. An exit
zero with `status=incomplete` means the requested measurements ran, not that
D2 qualified. Formal budget failures must remain visible without changing the
workload or thresholds to obtain a green report.

The real-process regressions exercise SIGKILL before and after business COMMIT,
lost-response receipt readback, exact head/event/receipt/scan equivalence and
SQLite `max_page_count` exhaustion. These are small disposable-database tests,
not power-loss, operating-system ENOSPC or large-history recovery qualification.
The source uses the shared retained-journal snapshot contract. No checkpoint,
retention deletion, restore-incarnation change or migration format is added.

### Qualification holds / 资格保留项

The report's `passed` rows apply only to their named axis and sample counts.
`failed` measurements remain failed; `missing` rows include cumulative storage
writes, pure lock wait, steady-state RSS proof, the full domain profile, 1 MiB
and 300k headroom, 24-hour consumer lag, large-history recovery, fenced
backup/restore, supported upgrades/rollback, OS/runtime coverage and a real
>=10-day soak. Those holds still block profile promotion. Accelerated volume
never substitutes for elapsed time, and running this command starts no soak.

SQLite 资格参考使用 Node 22.22.3／SQLite 3.51.3；打开前同时检查实际 WAL 修复版本
和 statement 关闭行为。公开 Node 最低版本 22.18 继续用于默认 File 路径。显式
SQLite 选择遇到不合格 runtime 会拒绝，不会改默认 provider 或静默回退。

默认无参数命令从旧的 4 KiB/100k 改为小型 `rehearsal`，只验证工具和不变量；
正式 64 KiB、10k/100k 对照必须显式选择
`matched-64k`。`--cli` 分开记录完整 CLI 冷启动与 warm store，返回分位数、样本数、
RSS 和文件大小；没有量到的累计 WAL／逻辑写入和纯锁等待保持 missing。
应用 JSON 字节不能替代底层写入量，WAL 最终归零不能证明没有写入放大。

进程中断与 SQLite 容量注入在一次性合成数据库上运行，不等于断电、真实文件系统
耗尽、长期 consumer backlog 或完整恢复验证。首批测量允许保留 failed/missing；
>=10 天自然时间 soak、迁移和晋升分别评审与授权。本入口不改变持久格式、Todo
语义、默认 provider 或任何活跃 Goal。
