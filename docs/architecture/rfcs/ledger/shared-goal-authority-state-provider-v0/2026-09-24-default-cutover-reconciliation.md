# Default cutover: reconciled implementation frontier

- Baseline: `d64c4d377` on `main`, 2026-09-24; open PR states are a snapshot, not merge promises.
- Owners: overall roadmap #4574 R5/G2; shared authority L2–L9/D1–D3; TS migration T1–T4.
- Delivery: complete source transport through existing typed projection and shadow management owners.
- This checkpoint supersedes numerical remaining-PR estimates in earlier delivery entries.

## Correct the accounting

The previous “5–8”, “6–8” and “7–9” numbers counted broad work packages as
remaining PRs, then retained the estimate after parts landed and additional
prerequisites appeared. They are not an audited PR backlog and are withdrawn.
A code gap, an open PR, integration acceptance, an elapsed-time qualification
and a maintainer cutover decision are different units. Do not add or decrement
them as though they were interchangeable PRs.

| Evidence at this baseline | Current disposition |
| --- | --- |
| #4870 claim-preserving writes; #4888 reviewed cutover; #4920 drain planning | Implemented. Exercise their combined head; do not commission replacements. |
| #4922 complete canonical snapshot pagination; #4960 qualified SQLite runtime admission; #4961 display refresh recovery; #4964 shared source summaries | Implemented. Consumer and packaged-client acceptance still needs integration evidence; a whole new pagination/recovery implementation is not pending. |
| #4967 typed complete-source assembly; #4968 native outbox delivery/recovery | Implemented. Large source RPC failure below is a separate demonstrated gap, not absence of capture assembly. |
| #5003 atomic event-owned completion | Open. Solves batch publication/retry, **not** the event writer's shadow-capture binding. |
| #4994 explicit leased Agent handoff; #4995 generated Monitor proof; #4991 rejected poll reservation; #4992 deferred receipt-bound Turn | Open. Integrate their exact reviewed heads before deciding what caller work remains; do not recreate them under a new caller-refactor PR. |
| #4931 retained SQLite proof encoding, contributor #4224 | Open optimization plus incomplete D2 qualification. A speedup is not capacity/recovery/soak acceptance. |
| #4915 default `.loopx` filesystem placement | Separate configuration migration; does not select File/SQLite authority. |

There are six relevant open implementation PRs above (#5003, #4994, #4995,
#4991, #4992, #4931), plus the separately classified #4915 to avoid conflating
filesystem placement with authority. These are not six unstarted requirements,
nor a claim that every one is a mandatory storage-default dependency.

## Four concrete next delivery boundaries

These are **four proposed new batches including this delivery**, in addition to
integrating existing work. They are not a guaranteed total remaining PR count.
The command inventory and exact-profile acceptance can reveal further defects;
record a new demonstrated gap rather than silently keeping a range unchanged.

| Batch | Observable result and owning boundary | Exit and remaining dependency |
| --- | --- | --- |
| A. Complete source pipeline (this delivery) | A source larger than the RPC envelope can pass typed projection, bootstrap, writer capture, inspect, qualify and reviewed promotion without truncation. Python transports bytes; TS retains source admission and authority. | Large real CLI journey; File/SQLite complete reads; source-witness rejection; detached real-source rehearsal. Does not bind the event writer or qualify a provider default. |
| B. External-effect executor fence | Current execution proof protects the actual external-effect interval, including takeover, timeout, exit and uncertain completion, using the existing lease/effect owners. | Stale executors cannot execute or settle fenced work; exact receipt recovery. #4994/#4995 caller integration is reused; a point-in-time proof check alone is insufficient. |
| C. Event-writer binding and whole-Goal migration/rollback | Bind the actual event writer lock/publication lifecycle to the existing outbox lineage, then exercise mixed Markdown/event/lease writers, drain, reviewed cutover, canonical consumers and fenced export/rollback as one journey. Retire replaced Python decisions at their TS owner. | Integrate #5003 rather than reimplement atomic completion. Preserve `event_log_writer_not_bound` until the real binding passes. D1 consumers, command inventory and D3 cohort evidence must close; if this requires separate code, name the discovered boundary explicitly. |
| D. Default/onboarding and final bounded Python retirement | Qualified local profile is selected consistently by new Goal creation, settings, installation and packaged frontend/Lark/CLI; existing Goals follow explicit migration/disable guidance. Delete only business writers whose callers have switched. | B/C and applicable D1–D3 evidence, rollback and entrypoint readback. Keep permanent Python rendering, host IO and legal import/export. |

D2 capacity, crash/restore/upgrade/runtime coverage and **at least ten days of
natural elapsed soak** are evidence gates on an exact SQLite profile, not an
assumed one- or two-PR allocation. #4224 retains ownership. D3 integration and
owner-approved cohort cutover are also not automatically new PRs. No fixed
completion date or exact total PR count is defensible while these are open.
A File-only bounded cutover, a qualified SQLite default and migration of every
existing Goal have distinct acceptance scopes; none proves the other two.

PostgreSQL reuses the typed commands and AuthorityStore, while deployed
transport, authentication/tenant policy, restore identity, operations and
capacity qualification remain its separate medium-term path. Local default
does not wait for PostgreSQL deployment; a passing conformance suite does not
establish production service readiness.

## Complete-source transport and budget decision

At this baseline `test_canonical_snapshot_integration` fails before provider
admission: complete source projection exceeds the 2 MiB request limit. Paging
canonical reads already exists, but source capture and management still send
whole projections. Trimming source records would invalidate digests and parity;
increasing the generic RPC budget would enlarge every method's exposure.

The existing coordination contract generates both schema names and the byte cap
for Python and TS. Python file exchange stays in its existing source projection
adapter; there is no independently maintained same-name Python/TS module pair.
Only source-bearing handlers accept a private host-local transfer envelope.
Python writes a temporary request; TS verifies its method, byte length, SHA-256,
regular-file identity and private directory, then invokes the same handler.
TS writes an exclusively created result and returns a small bound receipt;
Python verifies the response bytes and cleans up temporary files on both success
and failure. Inline callers remain compatible. These artifacts are transient
transport, not another authority store or durable business receipt.

The RPC limit remains 2 MiB. **The new artifact limit is 16 MiB per request or
result**, a separate explicit bound, not unlimited streaming or an assertion
that all goals fit. Oversize input rejects before execution. Result delivery can
fail after a mutation; callers must recover using the existing operation identity,
never infer that an RPC error means no commit. No automatic mutation retry is
added. Memory still includes complete parsed objects; this does not solve
arbitrarily large provider history or capacity qualification.

The cap accommodates the multi-megabyte complete-source fixture and source
management's repeated representation while keeping allocation bounded. In 32
interleaved warm calls of the same small source on the same host, inline versus
artifact median was 9.15/11.62 ms and p95 11.60/15.93 ms. This measured local IO
cost is accepted for complete-source calls; it is not a global latency claim.
Future size changes require a measured workload and the existing budget review.

Validation uses real File/SQLite and a disposable PostgreSQL 16 server. The
large CLI regression qualifies and promotes only a synthetic isolated Goal.
A live source rehearsal that detected concurrent changes was discarded; the
accepted real-source rehearsal verifies a detached copy against its capture
witness and exercises all mutations there. Private sources, identifiers and
raw output are excluded from public artifacts. No active Goal is promoted.

No frontend settings or API shape changes are needed: the same CLI and Python
management adapters invoke the same domain handlers and return the same results.
The public change is that supported complete-source operations no longer fail
solely because their source crosses the RPC envelope. Defaults, authorization,
source freshness, event-writer holds and provider promotion criteria are unchanged.

A related runtime repair handles socket errors when a caller closes an oversized
response before draining it. One disconnected caller no longer crashes the
shared runtime; the regression asserts that subsequent paged reads retain the
same process identity. It neither cancels nor retries the business operation.
