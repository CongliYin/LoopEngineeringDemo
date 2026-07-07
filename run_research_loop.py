#!/usr/bin/env python3
"""Run a research-report loop."""

from __future__ import annotations

import argparse
from pathlib import Path

from research.codex_runner import (
    CodexResearchLoopConfig,
    CodexResearchLoopRunner,
)


DEFAULT_TOPIC = "Agent 自主进化的技术路线、工程实现方式与评估方法"
DEFAULT_VALIDATOR_MODEL = "gpt-5.5"
DEFAULT_VALIDATOR_REASONING_EFFORT = "xhigh"


def normalize_reasoning_effort(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower().replace("_", "-")
    aliases = {
        "extra-high": "xhigh",
        "extra high": "xhigh",
    }
    return aliases.get(normalized, normalized)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a loop-engineered research report workflow."
    )
    parser.add_argument(
        "--topic",
        default=DEFAULT_TOPIC,
        help="Research topic.",
    )
    parser.add_argument(
        "--workspace",
        default=".research_loop_workspaces/agent_evolution",
        help="Workspace directory for this research loop.",
    )
    parser.add_argument(
        "--max-rounds",
        type=int,
        default=5,
        help="Maximum research/write/review rounds.",
    )
    parser.add_argument(
        "--min-sources",
        type=int,
        default=15,
        help="Minimum cited sources required by the deterministic validator.",
    )
    parser.add_argument(
        "--source-year",
        type=int,
        default=2026,
        help="Year that most sources should come from.",
    )
    parser.add_argument(
        "--min-source-year-ratio",
        type=float,
        default=0.8,
        help="Minimum share of sources that must come from --source-year.",
    )
    parser.add_argument(
        "--min-revision-rounds",
        type=int,
        default=0,
        help=(
            "Minimum completed Codex writer rounds required before passing; "
            "0 disables this gate."
        ),
    )
    parser.add_argument(
        "--min-section-chars",
        type=int,
        default=1800,
        help="Minimum non-space characters required for each body section.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Optional model override passed to the Codex writer agent.",
    )
    parser.add_argument(
        "--validator-model",
        default=DEFAULT_VALIDATOR_MODEL,
        help=(
            "Model passed to the Codex validator agent."
        ),
    )
    parser.add_argument(
        "--validator-reasoning-effort",
        default=DEFAULT_VALIDATOR_REASONING_EFFORT,
        help=(
            "Reasoning effort passed only to the Codex validator agent via "
            "model_reasoning_effort."
        ),
    )
    parser.add_argument(
        "--codex-bin",
        default="codex",
        help="Codex executable to call.",
    )
    parser.add_argument(
        "--codex-timeout-seconds",
        type=int,
        default=1200,
        help="Timeout for each Codex CLI writer/validator call.",
    )
    parser.add_argument(
        "--sandbox",
        default="workspace-write",
        choices=["read-only", "workspace-write", "danger-full-access"],
        help="Sandbox mode passed to Codex CLI.",
    )
    parser.add_argument(
        "--approval-policy",
        default="never",
        choices=["untrusted", "on-request", "never"],
        help="Approval policy passed to Codex CLI.",
    )
    parser.add_argument(
        "--keep-workspace",
        action="store_true",
        help="Keep existing workspace contents before running.",
    )
    parser.add_argument(
        "--no-search",
        action="store_true",
        help="Do not pass --search to Codex CLI.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Create the workspace and prompt without calling Codex.",
    )
    parser.add_argument(
        "--no-agent-validator",
        action="store_true",
        help="Disable the Codex validator agent and use deterministic checks only.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = CodexResearchLoopConfig(
        topic=args.topic,
        workspace=Path(args.workspace),
        max_rounds=args.max_rounds,
        min_sources=args.min_sources,
        source_year=args.source_year,
        min_source_year_ratio=args.min_source_year_ratio,
        min_revision_rounds=args.min_revision_rounds,
        min_section_chars=args.min_section_chars,
        model=args.model,
        validator_model=args.validator_model,
        validator_reasoning_effort=normalize_reasoning_effort(
            args.validator_reasoning_effort
        ),
        codex_bin=args.codex_bin,
        codex_timeout_seconds=args.codex_timeout_seconds,
        sandbox=args.sandbox,
        approval_policy=args.approval_policy,
        keep_workspace=args.keep_workspace,
        dry_run=args.dry_run,
        enable_search=not args.no_search,
        enable_agent_validator=not args.no_agent_validator,
    )
    runner = CodexResearchLoopRunner(config)
    return 0 if runner.run() else 1


if __name__ == "__main__":
    raise SystemExit(main())
