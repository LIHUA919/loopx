# Canonical lease renewal

`task-lease renew` supports an already promoted local File or SQLite Goal.
Previously these calls reached the legacy writer fence and were rejected.
Unpromoted Goals keep the existing legacy lease behavior. Selecting a provider
alone does not promote a Goal or grant execution authority.

Read the current canonical lease before choosing its CAS version:

```bash
loopx --registry registry.json task-lease inspect \
  --goal-id example-goal --todo-id todo_work --format json
loopx --registry registry.json task-lease renew \
  --goal-id example-goal --todo-id todo_work --owner agent-a \
  --idempotency-key execution-a --expected-version 3 \
  --ttl-seconds 600 --format json
```

Use the version and execution key belonging to the current lease. A renewal
increments the lease version, preserves owner/key/epoch/write scopes, and sets
the expiry from the runtime's current clock plus the requested TTL. The default
TTL and its limits remain those of the existing lease command. Expired leases,
ineligible owners, terminal Todos, stale versions and invalid state are rejected.

The new request schema is canonical-only. Missing/invalid fences or unavailable
providers never downgrade to a legacy lease file. Older runtimes reject the new
schema. Canonical mode, Todo and lease facts come from the provider; stale,
missing or malformed display frontmatter does not decide renewal eligibility.

## Commit, retry and readback

The lease projection, event and original receipt commit under one provider CAS.
The operation identity includes the Goal, Todo, owner, execution key and expected
lease version; changing TTL for the same identity is rejected. Keep a frozen
request when the response is lost or ambiguous. Recover the original receipt
before starting another renewal.

`status=replayed` and `idempotent=true` return the original lease result, even
after a later renewal or expiry. They do **not** extend expiry again or prove
current authority. Use `task-lease inspect` for current state; do not use an old
replay's version as a newly granted execution proof.

Canonical responses carry `source_authority`, `provider_revision` and `cursor`;
they do not invent a `lease_path`. Renewal writes neither a legacy lease record
nor a second shadow authority. A lease-only change does not require rewriting
Todo Markdown. The existing canonical journal retains the complete historical
projection and receipt.

## Scope and rollback

This adds renewal, not canonical transfer/release or the entire actor lifecycle.
It does not migrate legacy lifecycle receipts, change SQLite tables, switch
provider defaults, deploy a PostgreSQL service or authorize an active-Goal
migration. PostgreSQL local routing remains outside this command's supported
provider set.

Rolling back code retains the canonical state and writer fence; an older client
or runtime may reject renewal again. Do not remove the fence as a rollback or
create a replacement legacy lease. Keep renewal availability and lease expiry in
the operational rollback plan.

Correctness validation uses real File/SQLite and real CLI/process boundaries.
The SQLite validation profile uses Node 22.22.3 / SQLite 3.51.3. This does not
qualify the full [SQLite D2 capacity/continuity profile](sqlite-authority-store.md),
start its elapsed soak or grant promotion.

## 中文操作说明

已 promoted 的本地 File/SQLite Goal 现在可使用现有 `task-lease renew`；此前该入口
会被 legacy writer fence 拒绝。未 promoted 的 Goal 继续使用旧 lease 路径；provider
selector 本身不构成 promotion。先用上面的 `task-lease inspect` 读取当前 lease，
再使用该 lease 的 execution key 和 version 调用 renew。

合法续租只递增 version，并按 TS runtime 时钟加 TTL 更新 expiry；owner、key、epoch
和 write scopes 保持。到期、owner 不合法、Todo 已终止、version 过时或状态损坏时拒绝。
canonical mode/Todo/lease 从 provider 读取，不受 stale/missing/损坏显示 frontmatter 控制。
新请求 schema 绑定 canonical 路径，fence/provider 失效时不回退；旧 runtime 会拒绝该 schema。

lease、event 和原 receipt 在一次 provider CAS 中提交。相同 expected version 的同一
identity 改 TTL 会拒绝；响应丢失时冻结原请求并恢复 receipt。`replayed`／`idempotent=true`
只返回历史结果，不再次延长 expiry，也不授予当前执行权。之后的操作需要重新 inspect
当前状态。返回值提供 provider/revision/cursor，不伪造 lease_path，不写第二份 legacy/shadow
状态，也不为 lease-only 修改制造 Todo Markdown 投影待办。

本切片仅增加 renew，不交付 canonical transfer/release、完整 actor lifecycle 或 legacy
receipt 迁移。它不改 SQLite 表结构、默认 provider 或活动 Goal，不部署 PostgreSQL。
代码回退时保留 canonical state 和 fence，并把旧版本可能再次拒绝续租纳入运维计划；
不能删除 fence 或建立 legacy lease 作为回滚。真实 File/SQLite、CLI 和进程验证不等于
完整 D2、自然时间 soak 或 promotion 已合格。
