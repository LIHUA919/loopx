# Canonical room work through the Agent CLI

This first composition stage of [the Agent IM / LoopX / OpenViking RFC](../architecture/rfcs/agent-im-openviking-collaboration-v0.md)
lets a local registered Agent publish compact work orientation and a canonical
claim receipt into its existing Lark Goal Channel. It uses one already promoted
local File or SQLite authority. It does not qualify independent multi-host
service authority, an IM button callback, or live OpenViking integration.

The existing Goal Channel connection remains the configuration owner. An exact,
enabled Agent connection and a verified project Bot are required; a default or
sibling connection cannot stand in for the named Agent. No new capability,
provider, scheduler, memory store, or frontend setting is introduced. Python
adapts CLI/provider IO; the TypeScript Todo transaction retains state, CAS,
identity and receipt authority. Identity follows the existing trusted local CLI
model; `--agent-id` is not remote authentication. The typed room read model reuses that claim
admission rule. No existing command automatically publishes work cards.

## Preview and publish

From a source checkout use `uv run --extra test loopx` in place of `loopx`.
Use the source registry and a previously authorized non-production connection.
Do not promote an active Goal merely to try these commands. Follow the existing
[reviewed promotion](reviewed-coordination-promotion.md) procedure on a disposable
fixture if qualification needs canonical authority.

```sh
loopx --registry .loopx/registry.json --format json goal-channel work project \
  --goal-id room-goal --agent-id agent-a
```

This preview reads canonical state and resolves the exact delivery binding. It
does not contact Lark, accept a claim, or send a message. The projection carries
`source_revision`, `generated_at`, the first eligible unclaimed Todo id and
actor-scoped counts. Task prose, notes, validation declarations, evidence,
artifacts and recalled context are omitted. `authority_state=available` describes
the provider read, not Goal completion or permission to execute.

Add `--execute` to publish that orientation through the existing verified Bot:

```sh
loopx --registry .loopx/registry.json --format json goal-channel work project \
  --goal-id room-goal --agent-id agent-a --execute
```

## Claim and receipt recovery

Take `--expected-revision` from the fresh projection and choose a stable
idempotency key. Preview first by omitting `--execute`:

```sh
loopx --registry .loopx/registry.json --format json goal-channel work claim \
  --goal-id room-goal --agent-id agent-a --todo-id todo_example \
  --expected-revision '<source_revision>' --idempotency-key room-claim-example
```

With `--execute`, the local Agent CLI asks the same canonical Todo owner used
by direct `todo claim`, then publishes the receipt and current orientation.
This explicit command authorizes those two effects. Room membership, delivery,
a card or recalled text supplies neither the Agent identity nor write scope.
Identity and binding are reread; binding/target revocation serialize with the
claim transaction; the registry witness is rechecked by the typed owner.

The provider compares the expected revision inside the claim transaction.
Competing commands at one revision cannot both commit. A stale revision returns
`conflict`; it never silently refreshes its basis. Canonical claims now also
reject a Todo bound to another Agent, matching the lifecycle authority boundary.
This affects direct promoted claims as well as the optional room facade.

On an uncertain response, retry the **same** Agent, Todo, revision and key.
Changing intent with the same key is rejected. `already_applied` returns
historical acceptance; it does not renew or acquire a lease. The receipt reports
`current_owner` and `current_claim_matches_actor`, and every result declares
`execution_authority_granted=false`. Execution still needs current quota,
applicable gates and, in hard-lease mode, a separate current lease acquisition.

Check current canonical ownership through the existing direct CLI:

```sh
loopx --registry .loopx/registry.json --format json todo list --goal-id room-goal
```

`canonical_claim_accepted` and `readback_verified` are separate facts. If a
claim committed but room send/readback failed, the result retains acceptance
and reports a recovery requirement. Unknown sends are conservatively reported
as possible external writes. Repeating the same semantic card uses exact Bot
history and provider idempotency rather than minting a new claim. Outages do
not queue unbounded writes or fall back to Markdown authority. After reconnect,
resolve the current binding and canonical revision again; a revoked connection
or identity cannot use an old receipt to resume this facade.

## Read-only context and remaining qualification

Keep any authorized OpenViking retrieval in the existing private
[Agent Turn Recall](../../loopx/capabilities/agent_turn_recall/README.md) path.
It consumes the exact selected quota packet, enforces its existing provider
scope, and produces private observations. This room stage never enables recall,
imports context, forwards pointers, writes memory, or treats a recalled approval
as a current gate. Authorized artifact-reference composition and live context
recovery remain later acceptance work on #5198.

The new checks use synthetic Lark transport and real disposable File/SQLite
stores, including source CLI projection and direct CLI ownership readback.
They are not evidence of a real room, two independently authenticated hosts,
a live daemon reconnect, or OpenViking provider qualification. Those checks need
an explicitly authorized non-production room, actor bindings and read-only
resource scope. Existing shared authority and provisioning tasks retain their
ownership; this stage does not promote or deploy them.

To disable publication, stop issuing the optional `work ... --execute` commands
or disconnect the exact connection through the existing Goal Channel connection
owner. This does not delete canonical claims or retract their receipts. Revert
this change to remove the optional CLI facade; existing direct Todo CLI and
Goal Channel configuration remain usable.

## 中文边界

本阶段组合已有的 Agent CLI、Goal Channel 与 File/SQLite Todo 权威。默认仅预览；
显式 `--execute` 才请求领取并发布房间回执。没有新增配置 owner 或前端开关，
不自动发送卡片，也不把群成员、卡片、投递或记忆当作执行权限。

竞争领取在权威事务内校验 revision；相同 key 的重试恢复历史接受结果，不能续租。
当前 owner、历史回执与消息读回分别呈现。领取成功但消息失败时保留接受事实；
重新连接后复核当前身份、连接、权威、quota 和 lease。绑定给其他 Agent 的任务会被拒绝，
这一修复也适用于直接的 promoted Todo claim。

公开卡片仅包含标识、计数与安全回执，不携带任务正文、证据、artifact 或记忆内容。
真实房间、多主机、daemon reconnect、授权 artifact 引用和 OpenViking 只读检索仍待联调。
合成 Lark transport 加真实隔离权威的测试不能宣称三方 RFC 已完成。
