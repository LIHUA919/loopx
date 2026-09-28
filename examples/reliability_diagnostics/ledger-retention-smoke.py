#!/usr/bin/env python3
"""Rehearse offline diagnostic export/delete/restore in disposable state only.

The invariant is byte-preserving recovery of negative evidence through the
real CLI. This is neither a live observer run nor automatic retention policy.
Use --installed with a non-editable installed package to check its entrypoint.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
AS_OF = "2026-09-01T12:00:00+00:00"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installed", action="store_true")
    args = parser.parse_args()
    if not args.installed:
        sys.path.insert(0, str(REPO_ROOT))

    import loopx
    from loopx.capabilities.reliability_diagnostics import (
        FIXTURE_GOAL_ID,
        run_dsh_fixture,
    )

    if args.installed:
        assert not Path(loopx.__file__).resolve().is_relative_to(REPO_ROOT), (
            "--installed requires a non-editable installation outside the checkout"
        )

    fixture = run_dsh_fixture()
    data = "".join(json.dumps(row) + "\n" for row in fixture["ledger_records"])
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    if not args.installed:
        env["PYTHONPATH"] = str(REPO_ROOT)

    with tempfile.TemporaryDirectory(prefix="loopx-retention-smoke-") as tmp:
        root = Path(tmp)
        runtime = root / "runtime"
        runtime.mkdir()
        # Synthetic siblings must survive operations on the one ledger.
        siblings = {
            runtime / name: b'{"synthetic":"unchanged"}\n'
            for name in ("goal.json", "todo.json", "quota.json", "gate.json", "session.json")
        }
        for path, content in siblings.items():
            path.write_bytes(content)

        def cli(command: str, *options: str, at: Path = runtime, stdin: str | None = None) -> dict:
            result = subprocess.run(
                [sys.executable, "-m", "loopx.cli", "--runtime-root", str(at),
                 "--format", "json", "reliability-diagnostics", command,
                 "--goal-id", FIXTURE_GOAL_ID, *options],
                cwd=root, env=env, input=stdin, capture_output=True, text=True,
                check=False, timeout=30,
            )
            assert result.returncode == 0, result.stderr or result.stdout
            payload = json.loads(result.stdout)
            assert payload["ok"] is True, payload
            return payload

        def readback(at: Path = runtime) -> dict:
            return cli("status", "--with-receipt", "--as-of", AS_OF, at=at)

        ingest = cli("ingest", "--input", "-", stdin=data)
        ledger = runtime / ingest["ledger_ref"]
        assert ingest["rejected_event_count"] == 0

        for expected_status in ("degraded", "invalid"):
            if expected_status == "invalid":
                # A refused control field leaves a durable marker. Recovery must
                # preserve it instead of selecting only accepted envelopes.
                refused = dict(fixture["ledger_records"][0], command={"kind": "stop"})
                refusal = cli("ingest", "--input", "-", stdin=json.dumps(refused) + "\n")
                assert refusal["ingest_gate_recorded"] is True
                assert refusal["rejected_by_reason"] == {"control_field_rejected": 1}
                assert b"reliability_ingest_violation_v0" in ledger.read_bytes()
                assert b'"command"' not in ledger.read_bytes()

            before = readback()
            receipt = before["receipt"]
            assert receipt["status"] == expected_status, receipt
            assert {"sequence_gap", "backpressure_drop", "raw_material_rejected",
                    "clock_uncertainty_exceeded"} <= set(receipt["reason_codes"])
            assert receipt["lost_event_count"] == 2
            assert receipt["backpressure_drop_count"] == 3
            assert receipt["clock"]["max_uncertainty_ms"] == 1500

            content = ledger.read_bytes()
            digest = hashlib.sha256(content).hexdigest()
            archive = root / f"archive-{expected_status}"
            copy_runtime = archive / "readback-runtime"
            copy_ledger = copy_runtime / ingest["ledger_ref"]
            copy_ledger.parent.mkdir(parents=True)
            copy_ledger.write_bytes(content)
            (archive / "readback.json").write_text(json.dumps(before), encoding="utf-8")
            (archive / "ledger.sha256").write_text(digest, encoding="ascii")
            assert hashlib.sha256(copy_ledger.read_bytes()).hexdigest() == digest
            assert copy_ledger.read_bytes() == ledger.read_bytes()
            assert readback(copy_runtime) == before

            # Tampering with a private export cannot be treated as verified.
            damaged = content + b"{}\n"
            assert hashlib.sha256(damaged).hexdigest() != digest
            assert hashlib.sha256(ledger.read_bytes()).hexdigest() == digest
            ledger.unlink()
            missing = readback()
            assert missing["receipt"]["status"] == "invalid"
            assert "no_observations" in missing["receipt"]["reason_codes"]
            assert missing["receipt"]["persisted_event_count"] == 0

            # Exclusive creation is the restore guard: never merge or overwrite
            # a file created by a restarted observer.
            with ledger.open("xb") as handle:
                handle.write(copy_ledger.read_bytes())
            try:
                ledger.open("xb").close()
            except FileExistsError:
                pass
            else:
                raise AssertionError("restore must refuse an existing ledger")
            assert hashlib.sha256(ledger.read_bytes()).hexdigest() == digest
            assert readback() == before
            assert all(path.read_bytes() == value for path, value in siblings.items())
            projection = before["projection"]
            assert projection["mode"] == "read_only"
            assert projection["authority"] == "none"
            assert projection["worker_influence"] == "none"
            assert receipt["outbound_endpoints"] == []
            assert receipt["observation_entered_worker_context"] is False
            assert receipt["observation_entered_scheduler_inputs"] is False

    print("reliability-diagnostics ledger-retention-smoke: ok "
          f"(synthetic; installed={args.installed}; degraded/invalid evidence preserved)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
