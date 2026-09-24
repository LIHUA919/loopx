"""A global settings entry must survive ordinary project-to-global projection."""

import json
import subprocess
import sys

import pytest

from loopx.chat_action_store import ChatActionStore
from loopx.chat_actions import ChatActionService
from loopx.configuration_transaction import goal_capability_configuration_revision
from loopx.configure_goal import configure_goal
from loopx.control_plane.goals.configure_goal_service import (
    configure_goal_with_global_sync,
    read_goal_configuration_with_source_route,
)
from loopx.global_registry import sync_project_registry_to_global


@pytest.fixture
def mirrored_goal(tmp_path):
    source = tmp_path / "project" / ".loopx" / "registry.json"
    runtime = tmp_path / "runtime"
    source.parent.mkdir(parents=True)
    runtime.mkdir()
    source.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "common_runtime_root": str(runtime),
                "goals": [
                    {
                        "id": "example",
                        "repo": str(tmp_path / "project"),
                        "status": "active",
                        "coordination": {
                            "registered_agents": ["agent-a"],
                        },
                        "spawn_policy": {
                            "mode": "default",
                            "allowed": False,
                            "max_children": 3,
                        },
                    }
                ],
            }
        )
    )
    sync_project_registry_to_global(
        registry_path=source,
        runtime_root_override=str(runtime),
        goal_id="example",
        dry_run=False,
    )
    return source, runtime / "registry.global.json", runtime


def policy(path):
    return json.loads(path.read_text())["goals"][0]["spawn_policy"]


def registered_agents(path):
    return json.loads(path.read_text())["goals"][0]["coordination"][
        "registered_agents"
    ]


def preview_agent_binding(tmp_path, mirror):
    service = ChatActionService(
        store=ChatActionStore(tmp_path / "actions"),
        registry_path=mirror,
    )
    proposal = service.preview(
        {
            "action_kind": "agent.bind",
            "summary": "Bind agent-b to the Goal",
            "normalized_parameters": {
                "goal_id": "example",
                "agent_id": "agent-b",
            },
            "context": {},
            "idempotency_key": "bind-agent-b",
        }
    )
    return service, proposal


def test_global_cli_change_survives_source_resync(mirrored_goal):
    source, mirror, runtime = mirrored_goal
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--registry",
            str(mirror),
            "--runtime-root",
            str(runtime),
            "--format",
            "json",
            "configure-goal",
            "--goal-id",
            "example",
            "--multi-subagent-feature",
            "enabled",
            "--max-children",
            "4",
            "--subagent-model",
            "example-model",
            "--subagent-reasoning-effort",
            "max",
            "--execute",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout)["ok"] is True
    expected = {
        "mode": "multi_subagent",
        "allowed": True,
        "max_children": 4,
        "model_config": {"model": "example-model", "reasoning_effort": "max"},
    }
    assert policy(source) == expected
    assert policy(mirror) == expected
    sync_project_registry_to_global(
        registry_path=source,
        runtime_root_override=str(runtime),
        goal_id="example",
        dry_run=False,
    )
    assert policy(mirror) == expected


def test_stale_mirror_reads_source_and_rejects_stale_source_revision(mirrored_goal):
    source, mirror, runtime = mirrored_goal
    configure_goal(
        registry_path=source,
        goal_id="example",
        multi_subagent_feature="enabled",
        max_children=4,
        execute=True,
    )
    assert policy(mirror)["allowed"] is False
    current = read_goal_configuration_with_source_route(
        registry_path=mirror, goal_id="example", execute=False
    )
    assert current["after"]["orchestration"]["spawn_allowed"] is True
    revision = goal_capability_configuration_revision(
        "example", current["configuration_catalog"]["capability_catalog"]
    )
    configure_goal(
        registry_path=source, goal_id="example", max_children=2, execute=True
    )
    before = (source.read_bytes(), mirror.read_bytes())
    with pytest.raises(ValueError, match="configuration changed"):
        configure_goal_with_global_sync(
            registry_path=mirror,
            goal_id="example",
            runtime_root_override=str(runtime),
            execute=True,
            max_children=3,
            expected_goal_configuration_revision=revision,
        )
    assert (source.read_bytes(), mirror.read_bytes()) == before


def test_missing_source_never_falls_back_to_writing_mirror(mirrored_goal):
    source, mirror, runtime = mirrored_goal
    source.unlink()
    before = mirror.read_bytes()
    for execute in [False, True]:
        with pytest.raises(ValueError, match="source_registry is missing"):
            configure_goal_with_global_sync(
                registry_path=mirror,
                goal_id="example",
                runtime_root_override=str(runtime),
                execute=execute,
                multi_subagent_feature="enabled",
                max_children=4,
            )
    assert mirror.read_bytes() == before


def test_browser_settings_read_and_apply_use_source(mirrored_goal):
    from tests.test_chat_server_cors import _request, _start_server

    source, mirror, runtime = mirrored_goal
    configure_goal(
        registry_path=source,
        goal_id="example",
        multi_subagent_feature="enabled",
        max_children=2,
        execute=True,
    )
    assert policy(mirror)["allowed"] is False
    server, thread = _start_server()
    server.registry_path = mirror
    server.runtime_root_override = str(runtime)
    server.goal_subagent_configuration_enabled = True
    port = server.server_address[1]
    origin = f"http://127.0.0.1:{port}"
    try:
        response = _request(
            port,
            method="GET",
            origin=origin,
            path="/api/chat/goal-configuration?goal_id=example",
        )
        inspection = json.loads(response.read())
        assert response.status == 200, inspection
        assert inspection["ok"] is True
        serialized = json.dumps(inspection["capability_catalog"])
        assert '"max_children": 2' in serialized
        body = {
            "goal_id": "example",
            "enabled": True,
            "max_children": 4,
            "model_config": {"model": "example-model", "reasoning_effort": "max"},
        }
        response = _request(
            port,
            method="POST",
            origin=origin,
            path="/api/chat/goal-subagents/dry-run",
            body=json.dumps(body).encode(),
        )
        preview = json.loads(response.read())
        assert response.status == 200, preview
        assert preview["before"]["orchestration"]["max_children"] == 2
        body["preview_id"] = preview["preview_id"]
        response = _request(
            port,
            method="POST",
            origin=origin,
            path="/api/chat/goal-subagents/apply",
            body=json.dumps(body).encode(),
        )
        applied = json.loads(response.read())
        assert response.status == 200, applied
        assert applied["ok"] is True
        assert policy(source) == policy(mirror)
        assert policy(source)["max_children"] == 4
        assert policy(source)["model_config"] == body["model_config"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_chat_agent_binding_survives_source_resync(tmp_path, mirrored_goal):
    source, mirror, runtime = mirrored_goal
    service, proposal = preview_agent_binding(tmp_path, mirror)

    applied = service.apply(proposal["proposal_id"])["proposal"]

    assert applied["status"] == "applied"
    assert applied["receipt"]["outcome"] == "agent_bound"
    assert registered_agents(source) == ["agent-a", "agent-b"]
    assert registered_agents(mirror) == ["agent-a", "agent-b"]
    sync_project_registry_to_global(
        registry_path=source,
        runtime_root_override=str(runtime),
        goal_id="example",
        dry_run=False,
    )
    assert registered_agents(mirror) == ["agent-a", "agent-b"]


def test_chat_agent_binding_rejects_stale_source_agents(tmp_path, mirrored_goal):
    source, mirror, _runtime = mirrored_goal
    service, proposal = preview_agent_binding(tmp_path, mirror)
    payload = json.loads(source.read_text())
    payload["goals"][0]["coordination"]["registered_agents"].append("agent-c")
    source.write_text(json.dumps(payload))
    before = source.read_bytes(), mirror.read_bytes()

    stale = service.apply(proposal["proposal_id"])["proposal"]

    assert stale["status"] == "stale"
    assert stale["receipt"] is None
    assert (source.read_bytes(), mirror.read_bytes()) == before


def test_chat_agent_binding_rejects_equal_peer_set_after_source_route_change(
    tmp_path, mirrored_goal
):
    source_a, mirror, _runtime = mirrored_goal
    service, proposal = preview_agent_binding(tmp_path, mirror)
    source_b = tmp_path / "project-b" / ".loopx" / "registry.json"
    source_b.parent.mkdir(parents=True)
    source_b.write_bytes(source_a.read_bytes())
    mirror_payload = json.loads(mirror.read_text())
    mirror_payload["goals"][0]["source_registry"] = str(source_b)
    mirror.write_text(json.dumps(mirror_payload))
    before = source_a.read_bytes(), source_b.read_bytes(), mirror.read_bytes()

    stale = service.apply(proposal["proposal_id"])["proposal"]

    assert stale["status"] == "stale"
    assert stale["receipt"] is None
    assert (source_a.read_bytes(), source_b.read_bytes(), mirror.read_bytes()) == before


def test_chat_agent_binding_does_not_recover_across_source_route_change(
    tmp_path, mirrored_goal
):
    source_a, mirror, _runtime = mirrored_goal
    service, proposal = preview_agent_binding(tmp_path, mirror)
    source_b = tmp_path / "project-b" / ".loopx" / "registry.json"
    source_b.parent.mkdir(parents=True)
    source_b_payload = json.loads(source_a.read_text())
    source_b_payload["goals"][0]["coordination"]["registered_agents"].append("agent-b")
    source_b.write_text(json.dumps(source_b_payload))
    mirror_payload = json.loads(mirror.read_text())
    mirror_payload["goals"][0]["source_registry"] = str(source_b)
    mirror.write_text(json.dumps(mirror_payload))
    before = source_a.read_bytes(), source_b.read_bytes(), mirror.read_bytes()

    stale = service.apply(proposal["proposal_id"])["proposal"]

    assert stale["status"] == "stale"
    assert stale["receipt"] is None
    assert (source_a.read_bytes(), source_b.read_bytes(), mirror.read_bytes()) == before


def test_chat_agent_binding_recovers_after_receipt_loss(
    tmp_path, mirrored_goal, monkeypatch
):
    source, mirror, _runtime = mirrored_goal
    service, proposal = preview_agent_binding(tmp_path, mirror)
    persist_receipt = service.store.apply

    def lose_receipt(*args, **kwargs):
        raise ConnectionError("simulated receipt loss")

    monkeypatch.setattr(service.store, "apply", lose_receipt)

    with pytest.raises(ConnectionError, match="receipt loss"):
        service.apply(proposal["proposal_id"])
    assert registered_agents(source) == ["agent-a", "agent-b"]
    assert registered_agents(mirror) == ["agent-a", "agent-b"]

    mirror_payload = json.loads(mirror.read_text())
    mirror_payload["goals"][0]["coordination"]["registered_agents"] = ["agent-a"]
    mirror.write_text(json.dumps(mirror_payload))

    monkeypatch.setattr(service.store, "apply", persist_receipt)
    recovered = service.apply(proposal["proposal_id"])["proposal"]

    assert recovered["status"] == "applied"
    assert recovered["receipt"]["outcome"] == "agent_already_bound"
    assert registered_agents(mirror) == ["agent-a", "agent-b"]
