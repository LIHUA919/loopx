"""The public completion entrypoint must never publish half a continuation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from loopx.control_plane.todos import active_state_editing as io
from loopx.control_plane.todos import event_writeback
from loopx.event_sourced_state import (
    TODO_ADDED,
    TODO_COMPLETED,
    TODO_UPDATED,
    AppendOnlyStateEventStore,
    StateEventCommitUnknownError,
    build_state_projection,
    make_state_event,
)
from loopx.todos import complete_goal_todo


@pytest.fixture
def event_goal(tmp_path: Path):
    state = tmp_path / "ACTIVE_GOAL_STATE.md"
    state.write_text(
        "---\ngoal_id: event-transaction\n---\n\n## Agent Todo\n", encoding="utf-8"
    )
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "common_runtime_root": str(tmp_path / "runtime"),
                "goals": [
                    {
                        "id": "event-transaction",
                        "status": "active",
                        "repo": str(tmp_path),
                        "state_file": state.name,
                        "domain": "harness_self_improvement",
                        "adapter": {"kind": "harness_self_improvement"},
                        "coordination": {
                            "agent_model": "peer_v1",
                            "registered_agents": ["worker"],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    store = AppendOnlyStateEventStore(tmp_path / "events.jsonl")
    store.append(
        make_state_event(
            event_id="parent",
            goal_id="event-transaction",
            event_type=TODO_ADDED,
            refs={"todo_id": "todo_parent"},
            payload={
                "role": "agent",
                "title": "Validate the implementation before independent review.",
                "task_class": "advancement_task",
                "claimed_by": "worker",
            },
            recorded_at="2026-09-24T00:00:00Z",
        )
    )
    request = dict(
        registry_path=registry,
        goal_id="event-transaction",
        todo_id="todo_parent",
        claimed_by="worker",
        evidence="Implementation validation passed.",
        completion_turn_key="completion-transaction",
        next_agent_todo="Review the implementation independently.",
        next_task_class="advancement_task",
        next_claimed_by="worker",
    )
    return store, request, state


def test_late_completion_encoding_failure_cannot_orphan_successor(
    event_goal, monkeypatch
):
    store, request, state = event_goal
    before, markdown = store.path.read_bytes(), state.read_bytes()
    encode = event_writeback.make_state_event

    def fail_completion(**kwargs):
        if kwargs["event_type"] == TODO_COMPLETED:
            raise ValueError("injected completion encoding failure")
        return encode(**kwargs)

    monkeypatch.setattr(event_writeback, "make_state_event", fail_completion)
    with pytest.raises(ValueError, match="encoding failure"):
        complete_goal_todo(**request)
    assert store.path.read_bytes() == before
    assert state.read_bytes() == markdown


def test_concurrent_source_change_rejects_entire_completion(event_goal, monkeypatch):
    store, request, _ = event_goal
    derive = event_writeback.derive_successor_proposals

    def concurrent_update(**kwargs):
        proposals = derive(**kwargs)
        store.append(
            make_state_event(
                event_id="concurrent-update",
                goal_id="event-transaction",
                event_type=TODO_UPDATED,
                refs={"todo_id": "todo_parent"},
                payload={"title": "A revised validation requirement."},
                recorded_at="2026-09-24T00:01:00Z",
            )
        )
        return proposals

    monkeypatch.setattr(
        event_writeback, "derive_successor_proposals", concurrent_update
    )
    result = complete_goal_todo(**request)
    assert result["ok"] is False
    assert result["completed"] is False
    assert [row["event_id"] for row in store.load()] == ["parent", "concurrent-update"]


def test_lost_sync_ack_retries_public_completion_without_duplicate_work(
    event_goal, monkeypatch
):
    store, request, _ = event_goal
    sync = io.fsync_state_directory
    failures = []

    def fail_event_log_once(path):
        if path == store.path and not failures:
            failures.append(path)
            raise OSError("injected event-log directory sync failure")
        return sync(path)

    monkeypatch.setattr(io, "fsync_state_directory", fail_event_log_once)
    with pytest.raises(StateEventCommitUnknownError):
        complete_goal_todo(**request)
    landed = store.path.read_bytes()
    projection = build_state_projection(store.load())
    todos = projection["agent_todos"]["items"]
    parent = next(row for row in todos if row["todo_id"] == "todo_parent")
    assert parent["status"] == "done"
    assert len(parent["successor_todo_ids"]) == 1
    assert parent["successor_todo_ids"][0] in {row["todo_id"] for row in todos}
    replay = complete_goal_todo(**request)
    assert replay["idempotent_replay"] is True
    assert replay["changed"] is False
    assert store.path.read_bytes() == landed


def test_complete_successors_are_visible_together_and_dry_run_is_read_only(event_goal):
    store, request, state = event_goal
    request.update(
        next_user_todo="Approve the proposed delivery.",
        next_user_task_class="user_gate",
    )
    before, markdown = store.path.read_bytes(), state.read_bytes()
    preview = complete_goal_todo(**request, dry_run=True)
    assert preview["ok"] is True
    assert store.path.read_bytes() == before
    result = complete_goal_todo(**request)
    assert result["ok"] is True
    assert len(result["next_todos"]) == 2
    for successor in result["next_todos"]:
        assert successor["required_capabilities"] == []
        assert successor["excluded_agents"] == []
    events = store.load()
    assert events[-1]["event_type"] == TODO_COMPLETED
    projection = build_state_projection(events)
    parent = next(
        row
        for row in projection["agent_todos"]["items"]
        if row["todo_id"] == "todo_parent"
    )
    all_ids = {
        row["todo_id"]
        for role in ("agent_todos", "user_todos")
        for row in projection[role]["items"]
    }
    assert len(parent["successor_todo_ids"]) == 2
    assert set(parent["successor_todo_ids"]) <= all_ids
    assert state.read_bytes() == markdown
