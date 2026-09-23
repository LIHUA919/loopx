from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from loopx import paths
from loopx.local_state_migration import (
    RECEIPT_NAME,
    migrate_local_state,
    rollback_local_state_migration,
)
from loopx.project_prompt import build_new_project_prompt


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


def _fixture(tmp_path: Path, *, projects: int = 2) -> tuple[Path, Path, list[Path]]:
    source = tmp_path / "home" / ".codex" / "loopx"
    target = tmp_path / "home" / ".loopx"
    global_goals = []
    project_roots = []
    for index in range(projects):
        project = tmp_path / f"project-{index}"
        goal_id = f"goal-{index}"
        state = project / ".codex" / "goals" / goal_id / "ACTIVE_GOAL_STATE.md"
        state.parent.mkdir(parents=True)
        state.write_text(f"# {goal_id}\n", encoding="utf-8")
        local_registry = project / ".loopx" / "registry.json"
        goal = {
            "id": goal_id,
            "repo": str(project),
            "state_file": f".codex/goals/{goal_id}/ACTIVE_GOAL_STATE.md",
        }
        _write_json(local_registry, {"common_runtime_root": str(source), "goals": [goal]})
        global_goals.append({**goal, "source_registry": str(local_registry)})
        run = source / "goals" / goal_id / "runs" / "run.json"
        _write_json(run, {"goal_id": goal_id})
        project_roots.append(project)
    _write_json(source / "registry.global.json", {"common_runtime_root": str(source), "goals": global_goals})
    return source, target, project_roots


def test_default_route_keeps_one_existing_legacy_registry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source, target, projects = _fixture(tmp_path, projects=1)
    monkeypatch.setattr(paths, "LEGACY_RUNTIME_ROOT", source)
    monkeypatch.setattr(paths, "DEFAULT_RUNTIME_ROOT", target)
    assert paths.default_runtime_route()["status"] == "legacy"
    assert paths.resolve_runtime_root({}) == source
    assert paths.resolve_runtime_root({}, str(tmp_path / "custom")) == tmp_path / "custom"
    assert paths.registered_goal_state_file(projects[0], "goal-0", _read(projects[0] / ".loopx" / "registry.json")) == projects[0] / ".codex" / "goals" / "goal-0" / "ACTIVE_GOAL_STATE.md"
    assert paths.registered_goal_state_file(tmp_path / "fresh", "new") == tmp_path / "fresh" / ".loopx" / "goals" / "new" / "ACTIVE_GOAL_STATE.md"
    _write_json(target / "registry.global.json", {"goals": []})
    assert paths.default_runtime_route()["status"] == "conflict"
    with pytest.raises(ValueError, match="Both default LoopX registries"):
        paths.resolve_runtime_root({})
    (target / "registry.global.json").unlink()
    (target / "registry.global.json").mkdir()
    assert paths.default_runtime_route()["status"] == "invalid"
    with pytest.raises(ValueError, match="not a regular file"):
        paths.resolve_runtime_root({})


def test_existing_project_prompt_keeps_registered_goal_and_runtime_routes(tmp_path: Path) -> None:
    source, _target, projects = _fixture(tmp_path, projects=1)
    project = projects[0]
    payload = build_new_project_prompt(
        project=project,
        goal_doc=project / "GOAL.md",
        goal_id="goal-0",
        objective="Continue the registered Goal",
        domain="example",
        adapter_kind="read_only_project_map_v0",
        adapter_status="connected-read-only",
        next_probe=None,
        spawn_allowed=False,
        allowed_domains=None,
        write_scope=None,
    )
    assert f"--runtime-root {source}" in payload["quota_guard_command"]
    assert f"--runtime-root {source}" in payload["quota_spend_command"]
    assert f"--runtime-root {source}" in payload["connect_command"]
    assert ".codex/goals/goal-0/ACTIVE_GOAL_STATE.md" in payload["prompt"]


def test_new_default_route_rejects_orphaned_legacy_state(tmp_path: Path) -> None:
    project = tmp_path / "project"
    legacy = paths.legacy_goal_state_file(project, "goal-one")
    legacy.parent.mkdir(parents=True)
    legacy.write_text("old authority\n", encoding="utf-8")
    with pytest.raises(ValueError, match="legacy Goal state exists"):
        paths.require_single_goal_state_route(
            project, "goal-one", paths.default_goal_state_file(project, "goal-one")
        )
    result = subprocess.run(
        [
            sys.executable, "-m", "loopx.cli",
            "--runtime-root", str(tmp_path / "runtime"), "--format", "json",
            "bootstrap", "--project", str(project), "--goal-id", "goal-one",
            "--objective", "Continue the old Goal", "--dry-run",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert "legacy Goal state exists" in result.stdout
    assert not paths.default_goal_state_file(project, "goal-one").exists()


def _read(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _extension_cli_result(
    home: Path, *arguments: str, runtime_root: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, "-m", "loopx.cli", "--format", "json"]
    if runtime_root is not None:
        command.extend(("--runtime-root", str(runtime_root)))
    command.extend(("extension", *arguments))
    env = {
        key: value for key, value in os.environ.items()
        if key not in {"LOOPX_RUNTIME_ROOT", "LOOPX_REGISTRY"}
    }
    env["HOME"] = str(home)
    return subprocess.run(
        command, cwd=home, env=env, text=True, capture_output=True, check=False,
    )


def _extension_cli(
    home: Path, *arguments: str, runtime_root: Path | None = None,
) -> dict[str, object]:
    result = _extension_cli_result(home, *arguments, runtime_root=runtime_root)
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    return payload


def _list_extensions(home: Path, *, runtime_root: Path | None = None) -> list[dict[str, object]]:
    return _extension_cli(home, "list", runtime_root=runtime_root)["extensions"]


def test_extension_cli_follows_legacy_execute_and_rollback_routes(tmp_path: Path) -> None:
    source, target, _projects = _fixture(tmp_path, projects=1)
    home = tmp_path / "home"
    extension_state = {
        "schema_version": "loopx_extension_state_v0",
        "extensions": {"example": {
            "id": "example", "enabled": True, "active_revision": "rev-1", "revisions": [],
        }},
    }
    _write_json(source / "extensions" / "state.json", extension_state)
    assert _list_extensions(home) == _list_extensions(home, runtime_root=source)
    assert _list_extensions(home)[0]["id"] == "example"

    preview = migrate_local_state(source_runtime_root=source, target_runtime_root=target)
    receipt = migrate_local_state(
        source_runtime_root=source, target_runtime_root=target,
        expected_plan_id=preview["plan_id"], execute=True,
    )
    assert not source.exists()
    assert _read(target / "extensions" / "state.json") == extension_state
    assert _list_extensions(home) == _list_extensions(home, runtime_root=target)
    assert _list_extensions(home)[0]["id"] == "example"
    assert not (source / "extensions" / "state.json").exists()

    rollback_local_state_migration(Path(receipt["backup_dir"]) / RECEIPT_NAME, execute=True)
    assert _list_extensions(home) == _list_extensions(home, runtime_root=source)
    assert _list_extensions(home)[0]["id"] == "example"
    assert not target.exists()


def test_extension_cli_uses_fresh_loopx_default(tmp_path: Path) -> None:
    home = tmp_path / "fresh-home"
    _write_json(home / ".loopx" / "extensions" / "state.json", {
        "schema_version": "loopx_extension_state_v0",
        "extensions": {"fresh": {
            "id": "fresh", "enabled": True, "active_revision": "rev-1", "revisions": [],
        }},
    })
    assert _list_extensions(home)[0]["id"] == "fresh"
    disabled = _extension_cli(home, "disable", "fresh", "--execute")
    assert disabled["changed"] is True
    assert _list_extensions(home)[0]["enabled"] is False
    assert _read(home / ".loopx" / "extensions" / "state.json")["extensions"]["fresh"]["enabled"] is False
    assert not (home / ".codex" / "loopx").exists()


def test_extension_cli_conflicting_defaults_require_an_explicit_route(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _write_json(home / ".codex" / "loopx" / "registry.global.json", {"goals": []})
    _write_json(home / ".loopx" / "registry.global.json", {"goals": []})
    _write_json(home / ".loopx" / "extensions" / "state.json", {
        "schema_version": "loopx_extension_state_v0",
        "extensions": {"selected": {"id": "selected", "enabled": False}},
    })

    result = _extension_cli_result(home, "list")
    assert result.returncode != 0
    assert "Both default LoopX registries exist" in result.stderr
    assert _list_extensions(home, runtime_root=home / ".loopx")[0]["id"] == "selected"


def test_preview_execute_and_verified_rollback_cover_all_registered_projects(tmp_path: Path) -> None:
    source, target, projects = _fixture(tmp_path)
    before = (source / "registry.global.json").read_bytes()
    preview = migrate_local_state(source_runtime_root=source, target_runtime_root=target)
    assert preview["dry_run"] is True
    assert preview["project_count"] == 2
    assert preview["goal_directory_count"] == 2
    assert source.exists() and not target.exists()
    assert (source / "registry.global.json").read_bytes() == before

    receipt = migrate_local_state(
        source_runtime_root=source,
        target_runtime_root=target,
        expected_plan_id=preview["plan_id"],
        execute=True,
    )
    receipt_path = Path(receipt["backup_dir"]) / RECEIPT_NAME
    assert receipt_path.exists()
    assert not source.exists()
    assert _read(target / "registry.global.json")["common_runtime_root"] == str(target)
    for index, project in enumerate(projects):
        goal_id = f"goal-{index}"
        assert not (project / ".codex" / "goals" / goal_id).exists()
        assert (project / ".loopx" / "goals" / goal_id / "ACTIVE_GOAL_STATE.md").exists()
        local = _read(project / ".loopx" / "registry.json")
        assert local["common_runtime_root"] == str(target)
        assert local["goals"][0]["state_file"] == f".loopx/goals/{goal_id}/ACTIVE_GOAL_STATE.md"
        assert paths.resolve_runtime_root(local) == target

    assert rollback_local_state_migration(receipt_path)["status"] == "rollback_ready"
    assert rollback_local_state_migration(receipt_path, execute=True)["status"] == "rolled_back"
    assert source.exists() and not target.exists()
    assert (source / "registry.global.json").read_bytes() == before
    for index, project in enumerate(projects):
        assert (project / ".codex" / "goals" / f"goal-{index}" / "ACTIVE_GOAL_STATE.md").exists()


def test_stale_plan_and_target_conflict_leave_source_unchanged(tmp_path: Path) -> None:
    source, target, projects = _fixture(tmp_path, projects=1)
    preview = migrate_local_state(source_runtime_root=source, target_runtime_root=target)
    state = projects[0] / ".codex" / "goals" / "goal-0" / "ACTIVE_GOAL_STATE.md"
    state.write_text("changed\n", encoding="utf-8")
    with pytest.raises(ValueError, match="preview changed"):
        migrate_local_state(source_runtime_root=source, target_runtime_root=target, expected_plan_id=preview["plan_id"], execute=True)
    assert source.exists() and not target.exists()
    target.mkdir()
    with pytest.raises(FileExistsError, match="target runtime root already exists"):
        migrate_local_state(source_runtime_root=source, target_runtime_root=target)
    assert source.exists() and state.read_text() == "changed\n"


def test_failed_write_restores_original_authority(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from loopx import local_state_migration as migration

    source, target, projects = _fixture(tmp_path, projects=1)
    preview = migrate_local_state(source_runtime_root=source, target_runtime_root=target)
    original_write = migration._write_registry

    def fail_global_target(path: Path, payload: dict[str, object]) -> None:
        if path == target / "registry.global.json":
            raise OSError("synthetic write failure")
        original_write(path, payload)

    monkeypatch.setattr(migration, "_write_registry", fail_global_target)
    with pytest.raises(RuntimeError, match="original routes were restored"):
        migrate_local_state(source_runtime_root=source, target_runtime_root=target, expected_plan_id=preview["plan_id"], execute=True)
    assert source.exists() and not target.exists()
    assert (projects[0] / ".codex" / "goals" / "goal-0" / "ACTIVE_GOAL_STATE.md").exists()
    assert _read(source / "registry.global.json")["common_runtime_root"] == str(source)
    assert _read(projects[0] / ".loopx" / "registry.json")["common_runtime_root"] == str(source)


def test_symlink_and_changed_target_block_unsafe_migration_or_rollback(tmp_path: Path) -> None:
    source, target, _projects = _fixture(tmp_path, projects=1)
    link = source / "linked-state"
    link.symlink_to(source / "goals", target_is_directory=True)
    with pytest.raises(ValueError, match="contains a symlink"):
        migrate_local_state(source_runtime_root=source, target_runtime_root=target)
    link.unlink()
    preview = migrate_local_state(source_runtime_root=source, target_runtime_root=target)
    receipt = migrate_local_state(
        source_runtime_root=source,
        target_runtime_root=target,
        expected_plan_id=preview["plan_id"],
        execute=True,
    )
    (target / "goals" / "goal-0" / "runs" / "run.json").write_text("new run\n", encoding="utf-8")
    with pytest.raises(ValueError, match="automatic rollback is unsafe"):
        rollback_local_state_migration(Path(receipt["backup_dir"]) / RECEIPT_NAME, execute=True)
    assert target.exists() and not source.exists()


def test_cli_preview_execute_and_rollback_readback(tmp_path: Path) -> None:
    source, target, projects = _fixture(tmp_path, projects=1)

    def invoke(*args: str) -> dict[str, object]:
        result = subprocess.run(
            [sys.executable, "-m", "loopx.cli", "--format", "json", "migrate-local-state", *args],
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr or result.stdout
        return json.loads(result.stdout)

    paths_args = ("--source-runtime-root", str(source), "--target-runtime-root", str(target))
    preview = invoke(*paths_args)
    assert preview["dry_run"] is True
    receipt = invoke(*paths_args, "--execute", "--expected-plan-id", str(preview["plan_id"]))
    assert receipt["status"] == "migrated"
    assert _read(projects[0] / ".loopx" / "registry.json")["common_runtime_root"] == str(target)
    receipt_path = str(Path(str(receipt["backup_dir"])) / RECEIPT_NAME)
    assert invoke("--rollback-receipt", receipt_path)["status"] == "rollback_ready"
    assert invoke("--rollback-receipt", receipt_path, "--execute")["status"] == "rolled_back"
    assert source.exists() and not target.exists()
