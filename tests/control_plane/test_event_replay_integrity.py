"""Event replay must preserve Todo identity and coherent scheduling/display."""
import pytest

from loopx.event_sourced_state import StateEventError, build_state_projection, make_state_event


def event(kind, number, *, todo_id="todo_alpha", **payload):
    return {**make_state_event(event_id=f"event-{number}", goal_id="example-goal",
        event_type=kind, refs={"todo_id": todo_id}, payload=payload,
        recorded_at="2026-09-24T00:00:00Z"), "append_sequence": number}


def test_second_creation_cannot_erase_a_completed_commitment():
    events = [event("todo_added", 1, title="Retain commitment", role="agent"),
              event("todo_completed", 2, evidence="Verified"),
              event("todo_added", 3, title="Replacement", role="agent")]
    with pytest.raises(StateEventError, match="already exists"):
        build_state_projection(events)


def test_priority_only_update_updates_rendered_text():
    result = build_state_projection([event("todo_added", 1, title="Keep title", priority="P2"),
                                     event("todo_updated", 2, priority="P0")])
    todo = result["agent_todos"]["items"][0]
    assert (todo["priority"], todo["title"], todo["text"]) == ("P0", "Keep title", "[P0] Keep title")


def test_role_update_moves_both_summary_and_source_section():
    result = build_state_projection([event("todo_added", 1, title="Owner decision", role="agent"),
                                     event("todo_updated", 2, role="user")])
    assert result["agent_todos"]["total_count"] == 0
    assert result["user_todos"]["items"][0]["source_section"] == "User Todo / Owner Review Reading Queue"


def test_zero_planner_order_is_a_real_order_not_a_missing_value():
    result = build_state_projection([event("todo_added", 1, todo_id="todo_first", title="First", planner_order=0),
                                     event("todo_added", 2, todo_id="todo_second", title="Second", planner_order=1)])
    assert [todo["todo_id"] for todo in result["agent_todos"]["items"]] == ["todo_first", "todo_second"]


def test_large_content_remains_complete_outside_the_typed_facts(monkeypatch):
    import json
    from loopx.control_plane import effect_runtime

    invoke = effect_runtime.effect_runtime_result
    request_sizes = []

    def measured(method, params, **kwargs):
        if method == "goal.state_event.plan_replay":
            request_sizes.append(len(json.dumps(params).encode()))
            assert "Retain this complete evidence" not in json.dumps(params)
        return invoke(method, params, **kwargs)

    monkeypatch.setattr(effect_runtime, "effect_runtime_result", measured)
    evidence = "Retain this complete evidence. " * 100_000
    source = [event("todo_added", 1, title="Large result"),
              event("todo_completed", 2, evidence=evidence)]
    result = build_state_projection(source)
    assert result["agent_todos"]["items"][0]["evidence"] == evidence.strip()
    assert len(request_sizes) == 1 and request_sizes[0] < 4096


def test_identical_event_replay_keeps_checksum_and_no_duplicate_todo():
    from loopx.event_sourced_state import StateEventConflictError, event_stream_checksum

    source = event("todo_added", 1, title="Original", role="agent")
    result = build_state_projection([source, source])
    assert result["source_event_count"] == result["agent_todos"]["total_count"] == 1
    assert result["source_checksum"] == event_stream_checksum([source])
    with pytest.raises(StateEventConflictError):
        build_state_projection([source, {**source, "payload": {"title": "Changed"}}])


def test_mixed_history_preserves_content_attribution_and_dependency_edges():
    source = [
        event("todo_added", 1, title="Source", priority="P1", claimed_by="author",
              task_class="advancement_task", capability_binding_ref="review:source",
              validation_command_argv=["python", "-c", "print('ok')"]),
        event("todo_deferred", 2, reason="Wait for review", resume_when="todo_done:todo_review"),
        event("todo_added", 3, todo_id="todo_review", title="Review", role="agent",
              task_class="advancement_task", claimed_by="reviewer"),
        event("todo_completed", 4, todo_id="todo_review", evidence="Review passed"),
        event("todo_updated", 5, title="Source revised", priority="P0"),
        event("todo_completed", 6, evidence="Delivered", successor_todo_ids=["todo_followup"]),
        event("todo_added", 7, todo_id="todo_followup", title="Follow-up", role="user",
              goal_bound=True, task_class="user_action"),
    ]
    result = build_state_projection(source)
    parent = next(row for row in result["agent_todos"]["items"] if row["todo_id"] == "todo_alpha")
    assert parent["text"] == "[P0] Source revised"
    assert parent["claimed_by"] == "author"
    assert parent["capability_binding_ref"] == "review:source"
    assert parent["successor_todo_ids"] == ["todo_followup"]
    assert parent["validation_command_argv"] == ["python", "-c", "print('ok')"]
    assert result["agent_todos"]["done_count"] == 2
    assert result["user_todos"]["open_count"] == 1


def test_long_history_folds_across_bounded_calls_without_losing_old_fields(monkeypatch):
    import json
    from loopx.control_plane import effect_runtime
    invoke = effect_runtime.effect_runtime_result
    sizes = []
    def measured(method, params, **kwargs):
        if method == "goal.state_event.plan_replay":
            sizes.append(len(json.dumps(params).encode()))
            assert len(params["events"]) <= 256
        return invoke(method, params, **kwargs)
    monkeypatch.setattr(effect_runtime, "effect_runtime_result", measured)
    source = [event("todo_added", 1, title="Original", claimed_by="author", capability_binding_ref="test:bound")]
    source.extend(event("todo_updated", i, title=f"Revision {i}") for i in range(2, 4100))
    source.append(event("todo_completed", 4100, evidence="Long history verified"))
    result = build_state_projection(source)
    todo = result["agent_todos"]["items"][0]
    assert todo["title"] == "Revision 4099"
    assert todo["status"] == "done" and todo["claimed_by"] == "author"
    assert todo["capability_binding_ref"] == "test:bound"
    assert len(sizes) == 17 and max(sizes) < 256_000
    # Duplicate identity protection must survive a batch boundary as well.
    with pytest.raises(StateEventError, match="already exists"):
        build_state_projection([*source, event("todo_added", 4101, title="Cannot reset history")])


def test_fractional_planner_order_is_rejected_before_typing():
    # The adapter used to truncate 1.5 to 1, so the fold sorted by one value
    # while the projection still reported the original fraction.
    with pytest.raises(StateEventError, match="planner_order must be an integer"):
        build_state_projection([
            event("todo_added", 1, title="Fractional", planner_order=1.5),
            event("todo_added", 2, title="Integer", planner_order=1, todo_id="todo_beta"),
        ])


@pytest.mark.parametrize("order", [True, "1", [1], {"value": 1}])
def test_non_integer_planner_order_forms_are_rejected(order):
    with pytest.raises(StateEventError, match="planner_order must be an integer"):
        build_state_projection([event("todo_added", 1, title="Bad order", planner_order=order)])


def test_integer_and_absent_planner_order_still_project():
    result = build_state_projection([
        event("todo_added", 1, title="First", planner_order=3),
        event("todo_added", 2, title="Second", todo_id="todo_beta"),
    ])
    items = result["agent_todos"]["items"]
    assert [item["todo_id"] for item in items] == ["todo_alpha", "todo_beta"]
    assert items[0]["planner_order"] == 3
