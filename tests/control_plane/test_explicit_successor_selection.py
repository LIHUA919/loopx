"""Real monitor-created work must survive selection preflight and retry."""

from __future__ import annotations

import json
import shlex

import pytest

from canonical_authority_fixture import (
    initialize_canonical_authority,
    isolate_sqlite_runtime,
)
from test_monitor_followthrough_contract import (
    AGENT_ID,
    GOAL_ID,
    _add_monitor,
    _write_fixture,
)
from loopx.control_plane.coordination.runtime_shadow import (
    build_todo_runtime_shadow_projection,
)
from loopx.control_plane.quota.heartbeat_receipt import find_heartbeat_receipt
from loopx.control_plane.testing.canary_harness import run_json_cli, run_json_cli_result
from loopx.todos import list_goal_todos


@pytest.mark.parametrize("provider", ["legacy", "file", "sqlite"])
@pytest.mark.parametrize("waiting_count", [0, 24])
def test_monitor_successor_selection_preserves_admission_and_retry(
    tmp_path,
    monkeypatch,
    provider,
    waiting_count,
):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, state = _write_fixture(tmp_path)
    with state.open("a") as stream:
        for index in range(waiting_count):
            stream.write(
                f"- [ ] [P1] Validate pending synthetic input {index}.\n"
                f"  <!-- loopx:todo todo_id=todo_pending_{index} status=open "
                f"task_class=advancement_task claimed_by={AGENT_ID} "
                "resume_when=todo_done:todo_dependency -->\n"
            )
        if waiting_count:
            stream.write(
                "- [ ] [P1] Wait for dependency.\n"
                "  <!-- loopx:todo todo_id=todo_dependency status=open "
                "task_class=advancement_task claimed_by=codex-main-control -->\n"
            )
    monitor = _add_monitor(
        registry,
        text="Observe a public revision",
        target_key="public-revision",
        next_due_at="2000-01-01T00:00:00Z",
    )
    if provider != "legacy":
        items = list_goal_todos(registry_path=registry, goal_id=GOAL_ID, role="agent")[
            "todos"
        ]
        projection = build_todo_runtime_shadow_projection(
            goal_id=GOAL_ID,
            todos=items,
            leases=[],
            handoff_mode="soft_claim",
        )
        initialize_canonical_authority(
            runtime, GOAL_ID, projection, state_path=state, provider=provider
        )
    result = run_json_cli(
        "quota",
        "monitor-poll",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--runtime-profile",
        "generic_cli",
        "--todo-id",
        monitor["todo_id"],
        "--result-hash",
        "revision-a",
        "--material-change",
        "--next-agent-todo",
        "Validate the observed revision",
        "--next-action-kind",
        "validate",
        "--next-claimed-by",
        AGENT_ID,
        "--next-continuation-policy",
        "same_agent_non_delivery",
        "--execute",
        registry_path=registry,
        runtime_root=runtime,
    )
    successor = result["todo_writeback"]["next_todos"][0]["todo_id"]
    args = (
        "quota",
        "should-run",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--codex-app",
    )
    default = run_json_cli(*args, registry_path=registry, runtime_root=runtime)
    assert default["selected_todo"]["todo_id"] == successor
    selection_args = (
        *args,
        "--todo-id",
        successor,
        "--turn-instance-id",
        "select-successor",
    )
    code, selected = run_json_cli_result(
        *selection_args, registry_path=registry, runtime_root=runtime
    )
    deferred = provider != "legacy" and waiting_count > 0
    assert default["normal_delivery_allowed"] is not deferred
    if not deferred:
        assert code == 0, selected
        assert selected["selected_todo"]["todo_id"] == successor
        assert (
            selected["interaction_contract"]["agent_channel"]["delivery_allowed"]
            is True
        )
        replay = run_json_cli(
            *selection_args, registry_path=registry, runtime_root=runtime
        )
        assert replay["heartbeat_receipt"]["status"] == "replayed"
        assert (
            replay["heartbeat_receipt"]["event_id"]
            == selected["heartbeat_receipt"]["event_id"]
        )
    else:
        assert waiting_count > 0
        assert default["effective_action"] == "autonomous_replan_required"
        assert code == 1, selected
        assert selected["error_code"] == "quota_action_selection_deferred"
        assert selected["action_selection"]["state"] == "deferred"
        assert selected["action_selection"]["reason"] == "autonomous_replan"
        assert str(tmp_path) not in json.dumps(selected["action_selection"])
        assert "heartbeat_receipt" not in selected
        assert (
            find_heartbeat_receipt(
                runtime,
                goal_id=GOAL_ID,
                agent_id=AGENT_ID,
                turn_instance_id="select-successor",
            )
            is None
        )
        assert not selected["interaction_contract"]["cli_channel"][
            "spend_after_validation"
        ]
        assert "settlement_plan" not in selected["interaction_contract"]["cli_channel"]
        code, retry = run_json_cli_result(
            *selection_args, registry_path=registry, runtime_root=runtime
        )
        assert code == 1
        assert retry["action_selection"] == selected["action_selection"]
        assert retry["recommended_action"] == selected["recommended_action"]
        envelope_code, envelope = run_json_cli_result(
            *selection_args,
            "--turn-envelope",
            registry_path=registry,
            runtime_root=runtime,
        )
        assert envelope_code == 1
        assert envelope["action_selection"] == selected["action_selection"]
        assert envelope["interaction_contract"]["cli_channel"]["next_cli_actions"] == [
            selected["recommended_action"]
        ]
        # Re-entry follows the real gate and binds its semantic replan obligation.
        command = shlex.split(retry["recommended_action"])
        assert "--todo-id" not in command
        assert command[command.index("--turn-instance-id") + 1] == "select-successor"
        reentry = run_json_cli(
            *command[1:], registry_path=registry, runtime_root=runtime
        )
        assert reentry["effective_action"] == "autonomous_replan_required"
        assert (
            reentry["heartbeat_receipt"]["semantic_replan_obligation_id"]
            == (reentry["autonomous_replan_obligation"]["obligation_id"])
        )
        assert reentry["normal_delivery_allowed"] is False


def test_canonical_add_after_pending_guard_reports_final_boundary(tmp_path):
    from test_quota_settlement_cli import (
        AGENT_ID as agent_id,
        GOAL_ID as goal_id,
        _write_fixture as write_fixture,
        _configure_selectable_alternative,
    )

    project, runtime, registry = write_fixture(tmp_path)
    _configure_selectable_alternative(project)
    state = project / f".codex/goals/{goal_id}/ACTIVE_GOAL_STATE.md"
    items = list_goal_todos(registry_path=registry, goal_id=goal_id, role="agent")[
        "todos"
    ]
    projection = build_todo_runtime_shadow_projection(
        goal_id=goal_id,
        todos=items,
        leases=[],
        handoff_mode="soft_claim",
    )
    initialize_canonical_authority(runtime, goal_id, projection, state_path=state)
    args = (
        "quota",
        "should-run",
        "--goal-id",
        goal_id,
        "--agent-id",
        agent_id,
        "--codex-app",
        "--turn-instance-id",
        "add-after-guard",
    )
    initial = run_json_cli(*args, registry_path=registry, runtime_root=runtime)
    assert (
        initial["interaction_contract"]["agent_channel"]["selection_required"] is True
    )
    assert "settlement_identity" not in initial["heartbeat_receipt"]
    receipt = find_heartbeat_receipt(
        runtime, goal_id=goal_id, agent_id=agent_id, turn_instance_id="add-after-guard"
    )
    added = run_json_cli(
        "todo",
        "add",
        "--goal-id",
        goal_id,
        "--role",
        "agent",
        "--task-class",
        "advancement_task",
        "--action-kind",
        "implement",
        "--claimed-by",
        agent_id,
        "--text",
        "Validate the new canonical task",
        "--task-repository",
        "git:github.com/example/read-only-settlement-fixture",
        "--required-write-scope",
        "loopx/**",
        registry_path=registry,
        runtime_root=runtime,
    )
    selection = (*args, "--todo-id", added["todo_id"])
    code, rejected = run_json_cli_result(
        *selection, registry_path=registry, runtime_root=runtime
    )
    assert code == 1
    assert rejected["error_code"] == "quota_action_selection_deferred"
    assert rejected["action_selection"]["reason"] == "control_repair"
    assert rejected["action_selection"]["requested_todo_id"] == added["todo_id"]
    assert rejected["normal_delivery_allowed"] is False
    assert "heartbeat_receipt" not in rejected
    assert (
        find_heartbeat_receipt(
            runtime,
            goal_id=goal_id,
            agent_id=agent_id,
            turn_instance_id="add-after-guard",
        )
        == receipt
    )
    code, repeated = run_json_cli_result(
        *selection, registry_path=registry, runtime_root=runtime
    )
    assert code == 1
    assert repeated["action_selection"] == rejected["action_selection"]
    command = shlex.split(repeated["recommended_action"])
    assert "--todo-id" not in command
    guard = run_json_cli(*command[1:], registry_path=registry, runtime_root=runtime)
    assert guard["normal_delivery_allowed"] is False
    assert guard["workspace_repair_allowed"] or guard["self_repair_allowed"]
