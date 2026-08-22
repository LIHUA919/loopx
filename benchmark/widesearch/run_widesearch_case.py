"""Run one WideSearch case under baseline (native Codex Goal) or treatment
(LoopX-guided) arm, producing a fresh final_answer.md in an isolated workspace.

Verifier is intentionally NOT part of this repo: it runs as the official
WideSearch evaluator inside a pier task (sandboxed, gold hidden from the agent)
for the local real-sandbox path, matching the deepswe infrastructure. This file
reuses the shipped native Goal runtime (no second implementation):
  from loopx.capabilities.benchmark_toolkit.native_codex_goal import ...
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from tasks import answer_is_fresh, fresh_workspace, prepare_case, stamp_run_start

REPO_ROOT = Path(__file__).resolve().parents[2]

# Public-safe, provider-neutral verification discipline for web-research table
# benchmarks. Mirrors the review/refine treatment pattern used on code benchmarks:
# map the work to Todos, treat official sources as authoritative, cross-check
# high-ambiguity cells, run a review pass, and stop cleanly once verified.
WIDESEARCH_VERIFICATION_HINT = (
    "VERIFICATION DISCIPLINE (required for every answer table): "
    "1. Read the case task and enumerate its exact required columns; create one "
    "research and one independent-verification Todo per column group, and record the "
    "authoritative source URL as evidence when you complete each one. "
    "2. For list or ranking-style cells, treat the official/primary publisher of that "
    "fact as the single authoritative reference. "
    "3. For detail cells (dates, deadlines, prices, fees, quantities, codes, names, "
    "addresses, or any value that can be confused with a related-but-different one), "
    "require the value from the primary/official source and independently cross-check "
    "it against a second source; accept it only when two independent sources agree on "
    "the same semantics. "
    "4. Challenge every detail cell before accepting it: does the value answer exactly "
    "what the column asks - the same entity, same context, same unit, same period or "
    "round - or is it a related value (a processing fee instead of the quoted fee, an "
    "aggregate instead of the per-row figure, a different period, entity, or unit)? "
    "Reject values from memory, promotional or waiver pages, or third-party "
    "aggregators unless the primary source confirms them. "
    "5. After composing the full table, run a review pass that re-reads EVERY cell "
    "against its recorded evidence, explicitly flags any cell sourced from memory or a "
    "single source, and fixes those cells before writing the final answer. "
    "6. Do not add speculative rows or cells. "
    "7. Once the review pass is clean, write the final answer, mark the LoopX goal "
    "complete, and stop; do not perform unrelated control-plane cleanup."
)


def _objective(
    case_id: str,
    workspace: Path,
    instruction: str,
    treatment: bool,
    verification_hint: bool = False,
) -> str:
    common = (
        f"Write the final markdown table to {workspace}/final_answer.md and stop. "
        "Use web_search / web_fetch to gather facts from the web."
    )
    if treatment:
        hint = f"\n{WIDESEARCH_VERIFICATION_HINT}" if verification_hint else ""
        return (
            "Use the installed LoopX skill (/loopx) to start a goal for the benchmark "
            "task in this workspace, then complete the task through LoopX's guided "
            "control (follow its todos and state writebacks). "
            f"Task instruction is in instruction.md: {instruction} {common}{hint}"
        )
    return (
        "Complete the benchmark task described in instruction.md inside this "
        f"workspace. {common}"
    )


def _app_server_command(*, codex_bin: str, enable_web_search: bool) -> list[str]:
    """Build the app-server Goal command with hosted Responses API compatibility.

    The codex app-server emits a ``multi_agent_v1`` dynamic-tool namespace by
    default; some hosted Responses endpoints reject ``namespace`` tool types.
    Disabling the multi-agent feature keeps the Goal tool surface minimal and
    provider-neutral while preserving the goal tools (get/create/update_goal)
    that the benchmark needs. Web search is toggled independently because some
    hosted endpoints also reject the ``external_web_access`` web_search field.
    """

    command = [
        codex_bin,
        "app-server",
        "--listen",
        "stdio://",
        "--enable",
        "goals",
        "-c",
        "features.multi_agent=false",
    ]
    command += ["-c", "tools.web_search=true" if enable_web_search else "tools.web_search=false"]
    return command


def _native_goal_modules():
    sys.path.insert(0, str(REPO_ROOT))
    from loopx.capabilities.benchmark_toolkit.native_codex_goal import (  # noqa: PLC0415
        NativeGoalConfig,
        compact_native_goal_receipt,
        run_native_goal_process_until_terminal,
    )

    return NativeGoalConfig, compact_native_goal_receipt, run_native_goal_process_until_terminal


def run_case(
    *,
    case_id: str,
    arm: str,
    data_root: Path,
    timeout_sec: int,
    enable_web_search: bool = True,
    verification_hint: bool = False,
) -> dict:
    raw = data_root / "widesearch.jsonl"
    gold_dir = data_root / "gold"
    cases_root = data_root / "cases"
    run_id = f"{case_id}-{arm}-{time.strftime('%Y%m%d-%H%M%S')}"

    prepare_case(raw=raw, gold_dir=gold_dir, cases_root=cases_root, case_id=case_id)
    workspace = fresh_workspace(cases_root=cases_root, case_id=case_id, run_id=run_id)
    started_at = stamp_run_start(workspace)
    instruction = (workspace / "instruction.md").read_text(encoding="utf-8")

    NativeGoalConfig, compact_receipt, run_native = _native_goal_modules()
    model = os.environ.get("ARK_OPENAI_MODEL", "deepseek-v4-flash-ga-260731")
    config = NativeGoalConfig(
        cwd=str(workspace),
        objective=_objective(
            case_id,
            workspace,
            instruction,
            treatment=(arm == "treatment"),
            verification_hint=verification_hint,
        ),
        task_instruction=instruction,
        model=model,
        effort=os.environ.get("CODEX_GOAL_EFFORT", "xhigh"),
        approval_policy="never",
        sandbox="danger-full-access",
    )
    turn = run_native(
        config,
        codex_bin=os.environ.get("CODEX_BIN", "codex"),
        process_command=_app_server_command(
            codex_bin=os.environ.get("CODEX_BIN", "codex"),
            enable_web_search=enable_web_search,
        ),
        process_env={**os.environ},
        process_cwd=str(workspace),
        goal_timeout_sec=timeout_sec,
    )
    receipt = compact_receipt(turn)

    fresh, reason = answer_is_fresh(workspace, started_at)
    if not fresh:
        return {"status": "runner_invalid", "reason": reason, "receipt": receipt}
    return {
        "status": "completed",
        "final_answer": str(workspace / "final_answer.md"),
        "receipt": receipt,
        "arm": arm,
        "run_id": run_id,
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--arm", choices=["baseline", "treatment"], required=True)
    p.add_argument("--case", default="ws_en_001")
    p.add_argument("--data-root", type=Path, required=True)
    p.add_argument("--timeout-sec", type=int, default=7200)
    p.add_argument(
        "--disable-web-search",
        action="store_true",
        help=(
            "Disable the native web_search tool in the app-server Goal config "
            "(some hosted Responses endpoints reject its external_web_access field)."
        ),
    )
    p.add_argument(
        "--verification-hint",
        action="store_true",
        help=(
            "Append the public-safe verification/review discipline to the treatment "
            "objective so LoopX-guided runs independently verify and review each "
            "table cell (mirrors the deepswe review/refine treatment pattern)."
        ),
    )
    args = p.parse_args()
    outcome = run_case(
        case_id=args.case,
        arm=args.arm,
        data_root=args.data_root,
        timeout_sec=args.timeout_sec,
        enable_web_search=not args.disable_web_search,
        verification_hint=args.verification_hint,
    )
    print(json.dumps(outcome, ensure_ascii=False, sort_keys=True))
    return 0 if outcome["status"] == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
