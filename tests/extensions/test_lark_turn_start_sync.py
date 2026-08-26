from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest

from loopx.control_plane.work_items.work_lane import (
    operator_inbox_material_review_due_work_lane_contract,
)
from loopx.extensions.lark import turn_start_sync as turn_start_sync_module
from loopx.extensions.lark.event_collector import load_lark_event_collector_config
from loopx.extensions.lark.routed_inbox import project_routed_lark_event_inbox_urgency
from loopx.extensions.lark.turn_start_sync import sync_lark_turn_start_inbox

FIRST_NOW = datetime(2026, 8, 26, 10, 0, tzinfo=UTC)
SECOND_NOW = datetime(2026, 8, 26, 10, 5, tzinfo=UTC)


def _project(tmp_path: Path, *, material_review: bool = True) -> tuple[Path, Path]:
    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(
        ["git", "init", "--quiet"],
        cwd=project,
        check=True,
        capture_output=True,
    )
    (project / ".gitignore").write_text(".loopx/\n", encoding="utf-8")
    config_dir = project / ".loopx" / "config"
    config_dir.mkdir(parents=True)
    inbox = config_dir / "inbox.json"
    inbox.write_text(
        json.dumps(
            {
                "schema_version": "lark_event_inbox_config_v0",
                "enabled": True,
                "inbox_dir": ".loopx/inbox/requirements",
                "capture_scope": "configured_chat_all",
                "material_review": {"enabled": material_review, "drain_limit": 20},
                "reply": {"enabled": False},
            }
        ),
        encoding="utf-8",
    )
    collector = config_dir / "collector.json"
    collector.write_text(
        json.dumps(
            {
                "schema_version": "lark_event_collector_config_v1",
                "enabled": True,
                "service_name": "loopx-turn-start-fixture",
                "supervisor": "systemd",
                "consume_timeout": "30m",
                "profile": "fixture-bot",
                "turn_start_sync": {
                    "enabled": True,
                    "initial_lookback_seconds": 900,
                    "overlap_seconds": 5,
                    "page_size": 50,
                },
                "routes": [
                    {
                        "route_key": "requirements",
                        "chat_id": "oc_fixture",
                        "event_inbox_config": ".loopx/config/inbox.json",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return project, collector


class PageRunner:
    def __init__(self, pages: Sequence[dict[str, object]]) -> None:
        self.pages = list(pages)
        self.calls: list[list[str]] = []

    def __call__(
        self, argv: Sequence[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(argv))
        page = self.pages.pop(0)
        return subprocess.CompletedProcess(
            args=list(argv),
            returncode=0,
            stdout=json.dumps({"ok": True, "identity": "bot", "data": page}),
            stderr="",
        )


def _page(*messages: dict[str, object]) -> dict[str, object]:
    return {
        "messages": list(messages),
        "has_more": False,
        "page_token": "",
    }


def test_turn_start_sync_captures_then_requires_same_turn_agent_read(
    tmp_path: Path,
) -> None:
    project, config = _project(tmp_path)
    runner = PageRunner(
        [
            _page(
                {
                    "message_id": "om_new_context",
                    "create_time": "2026-08-26T09:59:00Z",
                    "content": "Please adjust the current plan.",
                    "deleted": False,
                }
            )
        ]
    )

    result = sync_lark_turn_start_inbox(
        project=project,
        config_path=config,
        runner=runner,
        now=FIRST_NOW,
    )

    assert result["status"] == "observed"
    assert result["observation_count"] == 1
    assert result["agent_read_required"] is True
    assert result["private_content_returned"] is False
    assert result["provider_payload_returned"] is False
    captured = json.loads(
        (project / ".loopx/inbox/requirements/om_new_context.json").read_text()
    )
    assert captured["content"] == "Please adjust the current plan."
    urgency = project_routed_lark_event_inbox_urgency(
        project=project,
        config_path=config,
    )
    lane = operator_inbox_material_review_due_work_lane_contract(
        {
            "capabilities": {
                "lark_event_inbox": {
                    "urgency": urgency,
                    "drain_command": "loopx lark-inbox drain --goal-id fixture",
                }
            }
        },
        current_contract={"lane": "advancement_task"},
    )
    assert lane is not None
    assert lane["priority_preemption"] is True
    assert lane["semantic_triage_required"] is True
    assert "replan_goal" in lane["allowed_dispositions"]
    assert "before ordinary work" in str(lane["action"])


def test_completed_sync_opens_a_new_overlapping_tail_window(tmp_path: Path) -> None:
    project, config = _project(tmp_path)
    first = PageRunner([_page()])
    first_result = sync_lark_turn_start_inbox(
        project=project,
        config_path=config,
        runner=first,
        now=FIRST_NOW,
    )
    assert first_result["status"] == "empty"

    second = PageRunner([_page()])
    second_result = sync_lark_turn_start_inbox(
        project=project,
        config_path=config,
        runner=second,
        now=SECOND_NOW,
    )

    assert second_result["status"] == "empty"
    argv = second.calls[0]
    assert argv[argv.index("--start") + 1] == "2026-08-26T09:59:55Z"
    assert argv[argv.index("--end") + 1] == "2026-08-26T10:05:00Z"


def test_overlapping_duplicate_does_not_require_mutable_content_readback(
    tmp_path: Path,
) -> None:
    project, config = _project(tmp_path)
    first = PageRunner(
        [
            _page(
                {
                    "message_id": "om_overlap",
                    "create_time": "2026-08-26T09:59:00Z",
                    "content": "Original provider rendering.",
                    "deleted": False,
                }
            )
        ]
    )
    assert sync_lark_turn_start_inbox(
        project=project,
        config_path=config,
        runner=first,
        now=FIRST_NOW,
    )["status"] == "observed"
    second = PageRunner(
        [
            _page(
                {
                    "message_id": "om_overlap",
                    "create_time": "2026-08-26T09:59:00Z",
                    "content": "Edited provider rendering.",
                    "deleted": False,
                }
            )
        ]
    )

    result = sync_lark_turn_start_inbox(
        project=project,
        config_path=config,
        runner=second,
        now=SECOND_NOW,
    )

    assert result["status"] == "empty"
    assert result["observation_count"] == 0
    assert result["agent_read_required"] is False
    captured = json.loads(
        (project / ".loopx/inbox/requirements/om_overlap.json").read_text()
    )
    assert captured["content"] == "Original provider rendering."


def test_provider_schema_error_is_distinct_from_a_real_empty_inbox(
    tmp_path: Path,
) -> None:
    project, config = _project(tmp_path)
    runner = PageRunner([{"items": [], "has_more": False, "page_token": ""}])

    result = sync_lark_turn_start_inbox(
        project=project,
        config_path=config,
        runner=runner,
        now=FIRST_NOW,
    )

    assert result["status"] == "unavailable"
    assert result["error_code"] == "provider_contract_error"
    assert result["agent_read_required"] is False
    assert not (project / ".loopx/inbox/.turn-start/requirements.json").exists()


def test_inbox_write_still_requires_agent_read_when_cursor_commit_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, config = _project(tmp_path)
    runner = PageRunner(
        [
            _page(
                {
                    "message_id": "om_cursor_failure",
                    "create_time": "2026-08-26T09:59:00Z",
                    "content": "Steer the current turn.",
                    "deleted": False,
                }
            )
        ]
    )

    def fail_cursor_write(*_args: object, **_kwargs: object) -> None:
        raise OSError("fixture cursor write failure")

    monkeypatch.setattr(turn_start_sync_module, "_write_cursor", fail_cursor_write)
    result = sync_lark_turn_start_inbox(
        project=project,
        config_path=config,
        runner=runner,
        now=FIRST_NOW,
    )

    assert result["status"] == "partial"
    assert result["observation_count"] == 1
    assert result["agent_read_required"] is True
    assert result["local_private_state_mutated"] is True
    assert result["error_code"] == "route_sync_partial"
    assert (
        project / ".loopx/inbox/requirements/om_cursor_failure.json"
    ).is_file()


def test_turn_start_sync_requires_an_agent_material_triage_lane(
    tmp_path: Path,
) -> None:
    project, config = _project(tmp_path, material_review=False)

    with pytest.raises(ValueError, match="Agent triage lane"):
        load_lark_event_collector_config(project=project, config_path=config)
