"""Typed Chat actions for Goal lifecycle transitions."""

from __future__ import annotations

from typing import Any

from .control_plane.goals.activation import GoalActivationState, goal_activation_state
from .control_plane.goals.activation_service import set_goal_activation_state
from .control_plane.goals.deletion_service import delete_stopped_goal


GOAL_LIFECYCLE_SOURCE_BASIS_SCHEMA_VERSION = "loopx_goal_lifecycle_source_basis_v1"


class ChatGoalLifecycleActionMixin:
    """Keep Goal activation policy separate from general action orchestration."""

    def _goal_lifecycle_preview(
        self,
        parameters: dict[str, Any],
    ) -> dict[str, Any]:
        operation = str(parameters["operation"])
        if operation == "delete":
            return {"state_fingerprint": self._registry_fingerprint()}
        target_state = (
            GoalActivationState.STOPPED
            if operation == "stop"
            else GoalActivationState.ACTIVE
        )
        preview = set_goal_activation_state(
            registry_path=self.registry_path,
            goal_id=str(parameters["goal_id"]),
            state=target_state,
            reason=parameters.get("reason"),
            execute=False,
        )
        fingerprint = str(preview.get("observed_state_fingerprint") or "")
        source_identity = str(preview.get("source_identity") or "")
        if not preview.get("ok") or not fingerprint or not source_identity:
            raise ValueError(
                str(
                    preview.get("error")
                    or "Goal lifecycle source identity is unavailable"
                )
            )
        return {
            "state_fingerprint": fingerprint,
            "source_basis": {
                "schema_version": GOAL_LIFECYCLE_SOURCE_BASIS_SCHEMA_VERSION,
                "source_identity": source_identity,
            },
        }

    def _normalize_goal_lifecycle(
        self, parameters: dict[str, Any]
    ) -> dict[str, Any]:
        from .chat_actions import _opaque, _text

        values = self._allowed_parameters(
            parameters,
            allowed={"goal_id", "operation", "reason"},
        )
        goal_id = _opaque(values.get("goal_id"), field="goal_id")
        self._goal(goal_id)
        operation = str(values.get("operation") or "").strip().lower()
        if operation not in {"stop", "resume", "delete"}:
            raise ValueError("goal.lifecycle operation must be stop, resume, or delete")
        if operation == "delete" and goal_activation_state(self._goal(goal_id)) is not GoalActivationState.STOPPED:
            raise ValueError("stop the Goal before deleting it")
        result = {"goal_id": goal_id, "operation": operation}
        if values.get("reason"):
            result["reason"] = _text(values["reason"], field="reason", limit=600)
        return result

    def _apply_goal_delete(
        self,
        proposal_id: str,
        proposal: dict[str, Any],
        goal_id: str,
        current_fingerprint: str,
    ) -> dict[str, Any]:
        from .chat_actions import _digest

        expected_fingerprint = str(proposal.get("expected_state_fingerprint") or "")
        if current_fingerprint != expected_fingerprint:
            stale = self.store.apply(
                proposal_id,
                current_state_fingerprint=current_fingerprint,
                receipt={},
            )
            return {"proposal": stale, "turn": None}

        result = delete_stopped_goal(
            registry_path=self.registry_path,
            goal_id=goal_id,
            execute=True,
            expected_state_fingerprint=expected_fingerprint,
        )
        if result.get("stale"):
            stale = self.store.apply(
                proposal_id,
                current_state_fingerprint=str(
                    result.get("current_state_fingerprint") or current_fingerprint
                ),
                receipt={},
            )
            return {"proposal": stale, "turn": None}
        if not result.get("ok") or not (result.get("readback") or {}).get("verified"):
            raise ValueError(
                str(result.get("error") or "Goal deletion did not verify")
            )
        receipt = {
            "receipt_id": _digest(
                {
                    "proposal_id": proposal_id,
                    "goal_id": goal_id,
                    "operation": "delete",
                }
            )[:32],
            "outcome": "goal_deleted",
            "projection_verified": True,
            "resource_ids": {"goal_id": goal_id},
        }
        stored = self.store.apply(
            proposal_id,
            current_state_fingerprint=current_fingerprint,
            receipt=receipt,
        )
        return {"proposal": stored, "turn": None}

    def _apply_goal_lifecycle(
        self, proposal_id: str, proposal: dict[str, Any], parameters: dict[str, Any]
    ) -> dict[str, Any]:
        from .chat_actions import _digest

        goal_id = str(parameters["goal_id"])
        operation = str(parameters["operation"])
        if operation == "delete":
            current_fingerprint = self._registry_fingerprint()
            return self._apply_goal_delete(
                proposal_id, proposal, goal_id, current_fingerprint
            )

        target_state = (
            GoalActivationState.STOPPED
            if operation == "stop"
            else GoalActivationState.ACTIVE
        )
        expected_fingerprint = str(proposal.get("expected_state_fingerprint") or "")
        current = set_goal_activation_state(
            registry_path=self.registry_path,
            goal_id=goal_id,
            state=target_state,
            reason=parameters.get("reason"),
            execute=False,
        )
        current_fingerprint = str(current.get("observed_state_fingerprint") or "")
        current_state = GoalActivationState(str(current.get("before_state") or ""))
        source_basis = proposal.get("canonical_update_basis")
        expected_source_identity = (
            str(source_basis.get("source_identity") or "")
            if isinstance(source_basis, dict)
            and source_basis.get("schema_version")
            == GOAL_LIFECYCLE_SOURCE_BASIS_SCHEMA_VERSION
            else ""
        )
        source_route_matches = bool(
            expected_source_identity
            and expected_source_identity == current.get("source_identity")
        )
        idempotent_reapply = (
            source_route_matches
            and current_state is target_state
            and current_fingerprint != expected_fingerprint
        )
        if current_fingerprint != expected_fingerprint and not idempotent_reapply:
            stale = self.store.apply(
                proposal_id,
                current_state_fingerprint=current_fingerprint,
                receipt={},
            )
            return {"proposal": stale, "turn": None}
        result = set_goal_activation_state(
            registry_path=self.registry_path,
            goal_id=goal_id,
            state=target_state,
            reason=parameters.get("reason"),
            actor_kind="owner",
            expected_state_fingerprint=(
                current_fingerprint
                if idempotent_reapply
                else expected_fingerprint
            ),
            execute=True,
        )
        if result.get("error_kind") == "goal_action_stale":
            stale = self.store.apply(
                proposal_id,
                current_state_fingerprint=str(
                    result.get("observed_state_fingerprint")
                    or current_fingerprint
                ),
                receipt={},
            )
            return {"proposal": stale, "turn": None}
        if not result.get("ok") or not (result.get("readback") or {}).get(
            "verified"
        ):
            raise ValueError(
                str(result.get("error") or "Goal lifecycle projection did not verify")
            )
        receipt = {
            "receipt_id": _digest(
                {
                    "proposal_id": proposal_id,
                    "goal_id": goal_id,
                    "operation": operation,
                }
            )[:32],
            "outcome": (
                f"goal_{target_state.value}"
                if result.get("changed")
                else f"goal_already_{target_state.value}"
            ),
            "projection_verified": True,
            "resource_ids": {
                "goal_id": goal_id,
                "activation_state": target_state.value,
            },
        }
        stored = self.store.apply(
            proposal_id,
            current_state_fingerprint=(
                expected_fingerprint if idempotent_reapply else current_fingerprint
            ),
            receipt=receipt,
        )
        return {"proposal": stored, "turn": None}
