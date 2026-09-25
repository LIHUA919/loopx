#!/usr/bin/env python3
"""Exercise event-owned completion on a detached real Markdown snapshot.

Only synthetic Todos are completed. Source Goal configuration, validators and
runtime are never activated. All writes target a temporary registry and event
log; output contains counts/timings only. This is event-adapter qualification,
not shadow-capture, provider promotion or SQLite soak evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

REPOSITORY = Path(__file__).resolve().parents[2]
if str(REPOSITORY) not in sys.path:
    sys.path.insert(0, str(REPOSITORY))

from loopx.event_sourced_state import (  # noqa: E402
    TODO_ADDED,
    AppendOnlyStateEventStore,
    backfill_todo_events_from_markdown,
    build_state_projection,
    make_state_event,
)
from loopx.history import load_registry  # noqa: E402
from loopx.state_refresh import resolve_goal_state  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--goal-id", required=True)
    args = parser.parse_args()
    registry_path = args.registry.expanduser().resolve()
    registry = load_registry(registry_path)
    _, _, source = resolve_goal_state(
        registry=registry,
        goal_id=args.goal_id,
        project_override=None,
        state_file_override=None,
    )
    source_bytes = source.read_bytes()
    source_digest = hashlib.sha256(source_bytes).digest()
    # Preserve real record variety and volume, without inheriting a live owner.
    events = backfill_todo_events_from_markdown(
        source_bytes.decode("utf-8"),
        goal_id="event-rehearsal",
        recorded_at="2026-09-24T00:00:00Z",
    )
    if not events:
        raise SystemExit("selected source has no Markdown Todos to rehearse")
    with tempfile.TemporaryDirectory(prefix="loopx-event-rehearsal-") as directory:
        root = Path(directory)
        state = root / "ACTIVE_GOAL_STATE.md"
        state.write_text(
            "---\ngoal_id: event-rehearsal\n---\n\n## Agent Todo\n", encoding="utf-8"
        )
        clone_registry = root / "registry.json"
        clone_registry.write_text(
            json.dumps(
                {
                    "common_runtime_root": str(root / "runtime"),
                    "goals": [
                        {
                            "id": "event-rehearsal",
                            "status": "active",
                            "repo": str(root),
                            "state_file": state.name,
                            "domain": "harness_self_improvement",
                            "adapter": {"kind": "harness_self_improvement"},
                            "coordination": {
                                "agent_model": "peer_v1",
                                "registered_agents": ["rehearsal-worker"],
                            },
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        store = AppendOnlyStateEventStore(root / "events.jsonl")
        store.append_many(events)
        initial_count = len(store.load())
        todo_id = "todo_rehearsal_atomic_completion"
        assert not any(row.get("refs", {}).get("todo_id") == todo_id for row in events)
        store.append(
            make_state_event(
                event_id="rehearsal-parent",
                goal_id="event-rehearsal",
                event_type=TODO_ADDED,
                refs={"todo_id": todo_id},
                payload={
                    "role": "agent",
                    "title": "Qualify one detached event transaction.",
                    "task_class": "advancement_task",
                    "claimed_by": "rehearsal-worker",
                },
                recorded_at="2026-09-24T01:00:00Z",
            )
        )
        before = store.path.read_bytes()
        command = [
            sys.executable,
            "-c",
            "from loopx.cli import main; raise SystemExit(main())",
            "--registry",
            str(clone_registry),
            "--format",
            "json",
            "todo",
            "complete",
            "--goal-id",
            "event-rehearsal",
            "--todo-id",
            todo_id,
            "--claimed-by",
            "rehearsal-worker",
            "--evidence",
            "Detached validation passed.",
            "--next-agent-todo",
            "Independently review the detached delivery.",
            "--next-task-class",
            "advancement_task",
            "--next-claimed-by",
            "rehearsal-worker",
            "--execute",
        ]
        started = time.perf_counter()
        first = subprocess.run(
            command, cwd=REPOSITORY, capture_output=True, text=True, timeout=120
        )
        # Do not include subprocess output: it can contain copied private titles.
        if first.returncode:
            raise RuntimeError(
                f"detached completion CLI failed (exit {first.returncode})"
            )
        result = json.loads(first.stdout)
        assert result["ok"] and result["source"] == "event_log"
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        landed = store.path.read_bytes()
        assert landed.startswith(before)
        projection = build_state_projection(store.load())
        parent = next(
            row
            for row in projection["agent_todos"]["items"]
            if row["todo_id"] == todo_id
        )
        assert parent["status"] == "done" and len(parent["successor_todo_ids"]) == 1
        replay = subprocess.run(
            command, cwd=REPOSITORY, capture_output=True, text=True, timeout=120
        )
        if replay.returncode:
            raise RuntimeError(f"detached replay CLI failed (exit {replay.returncode})")
        assert json.loads(replay.stdout)["idempotent_replay"] is True
        assert store.path.read_bytes() == landed
        assert hashlib.sha256(source.read_bytes()).digest() == source_digest, (
            "live source changed during rehearsal"
        )
        print(
            json.dumps(
                {
                    "ok": True,
                    "source_unchanged": True,
                    "source_bytes": len(source_bytes),
                    "backfilled_events": initial_count,
                    "detached_log_bytes": len(landed),
                    "completion_events_added": len(store.load()) - initial_count - 1,
                    "cli_completion_ms": elapsed_ms,
                    "replay_unchanged": True,
                }
            )
        )


if __name__ == "__main__":
    main()
