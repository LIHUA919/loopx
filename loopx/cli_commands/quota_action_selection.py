"""CLI transport for typed explicit selection admission and receipt recovery."""

from __future__ import annotations

import argparse
import shlex
from collections.abc import Mapping
from pathlib import Path

from ..control_plane.quota.error_codes import (
    HeartbeatReceiptIdentityConflictError,
    QuotaActionSelectionNotAdmitted,
)
from ..control_plane.scheduler.execution_context import render_scheduler_execution_args
from ..control_plane.todos.contract import normalize_todo_id
from .quota_context import QuotaCommandContext


def require_requested_quota_action_selection(
    payload: dict[str, object],
    *,
    requested_todo_id: str | None,
    receipt_bound_todo_id: str | None,
    receipt_bound_replan_obligation_id: str | None,
) -> None:
    if not requested_todo_id or (
        receipt_bound_todo_id or receipt_bound_replan_obligation_id
    ):
        return
    qualification = payload.get("action_selection")
    selected_todo = payload.get("selected_todo")
    selected_todo_id = (
        normalize_todo_id(selected_todo.get("todo_id"))
        if isinstance(selected_todo, Mapping)
        else None
    )
    selection_binding = (
        selected_todo.get("selection_binding")
        if isinstance(selected_todo, Mapping)
        else None
    )
    execution_obligation_value = payload.get("execution_obligation")
    execution_obligation: Mapping[str, object] = (
        execution_obligation_value
        if isinstance(execution_obligation_value, Mapping)
        else {}
    )
    interaction_value = payload.get("interaction_contract")
    interaction: Mapping[str, object] = (
        interaction_value if isinstance(interaction_value, Mapping) else {}
    )
    agent_channel_value = interaction.get("agent_channel")
    agent_channel: Mapping[str, object] = (
        agent_channel_value if isinstance(agent_channel_value, Mapping) else {}
    )
    pending_selection_qualified = (
        selection_binding == "pending_action_selection"
        and payload.get("normal_delivery_allowed") is True
    )
    exact_current_obligation_qualified = (
        selection_binding != "pending_action_selection"
        and execution_obligation.get("must_attempt_work") is True
        and agent_channel.get("must_attempt") is True
    )
    if (
        selected_todo_id != requested_todo_id
        or payload.get("ok") is not True
        or payload.get("should_run") is not True
        or not (pending_selection_qualified or exact_current_obligation_qualified)
    ):
        if isinstance(qualification, Mapping) and qualification.get("state") in {
            "rejected",
            "deferred",
        }:
            raise QuotaActionSelectionNotAdmitted(str(qualification["reason"]))
        raise HeartbeatReceiptIdentityConflictError(
            "explicit action selection must name one currently projected "
            "agent-scoped, capability-ready Todo"
        )
    if exact_current_obligation_qualified:
        # Exact due-monitor selection uses the existing obligation route,
        # rather than the advancement-only candidate qualifier.
        payload.pop("action_selection", None)


def commit_requested_action_selection(
    payload: Mapping[str, object],
    *,
    requested_todo_id: str | None,
) -> None:
    """Project the exact requested selection only after receipt reconciliation."""

    selected_todo = payload.get("selected_todo")
    if (
        requested_todo_id
        and isinstance(selected_todo, dict)
        and normalize_todo_id(selected_todo.get("todo_id")) == requested_todo_id
    ):
        selected_todo["selection_binding"] = "heartbeat_receipt"


def reject_action_selection(
    payload: dict[str, object],
    *,
    args: argparse.Namespace,
    registry_path: Path,
    context: QuotaCommandContext,
) -> dict[str, object]:
    """Keep the failed preflight typed and re-enter before any settlement."""
    qualification = payload["action_selection"]
    payload.update(
        ok=False,
        should_run=False,
        normal_delivery_allowed=False,
        error_code=f"quota_action_selection_{qualification['state']}",
        reason=str(qualification["reason"]),
    )
    command = shlex.join(
        [
            "loopx",
            "--registry",
            str(registry_path),
            "--runtime-root",
            str(context.runtime_root),
            "--format",
            "json",
            "quota",
            "should-run",
            "--goal-id",
            args.goal_id,
            "--agent-id",
            args.agent_id,
            *(
                ["--turn-instance-id", context.heartbeat_turn_id]
                if context.heartbeat_turn_id
                else []
            ),
            *[
                token
                for capability in (args.available_capabilities or [])
                for token in ("--available-capability", capability)
            ],
        ]
    ) + render_scheduler_execution_args(
        scheduler_execution_context=context.scheduler_context
    )
    payload["recommended_action"] = command
    payload.pop("selected_todo", None)
    payload.pop("action_portfolio", None)
    payload["interaction_contract"]["cli_channel"] = {
        "next_cli_actions": [command],
        "spend_allowed_now": False,
        "spend_after_validation": False,
        "spend_policy": "re-enter the current guard before delivery or settlement",
    }
    payload["interaction_contract"]["agent_channel"].update(
        delivery_allowed=False,
        selection_required=False,
        primary_action="re-enter the current guard before selecting work",
    )
    return payload
