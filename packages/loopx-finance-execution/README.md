# LoopX Finance Execution

Status: optional M1 simulator.

This distribution is the first financial consumer of LoopX's human-confirmed
operation envelope. It validates one immutable `finance_order_intent_v0` and
returns a deterministic simulated fill. It contains no venue client, signer,
credential reader, transfer path, reservation authority, or retrying network
submission.

The Core typed-action store owns confirmation and the single execution claim.
This package accepts only a consumed claim whose operation and payload digests
match. Its `finance.operation.simulate` permission does not authorize a real
order. Every result is marked `simulation=true` and
`external_write_performed=false`.

Install and enable it through the normal LoopX extension lifecycle before using
the M1 operation-card flow:

```bash
python3 -m pip install ./packages/loopx-finance-execution
loopx extension install \
  --manifest packages/loopx-finance-execution/extension.toml \
  --execute --format json
loopx extension enable loopx-finance-execution --execute --format json
```

Real venue adapters are intentionally out of scope. They require the later
finance reservation, ambiguity/reconciliation and venue conformance milestones.
