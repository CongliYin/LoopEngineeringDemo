"""Research loop where Codex CLI is the main research agent."""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from string import Template
from typing import Any

from research.validator import validate_report
from research.validator import REQUIRED_SECTIONS


TEMPLATE_DIR = Path(__file__).with_name("templates")
AGENT_PASSING_SCORE = 96
SCORECARD_MIN_SCORE = 90
SCORECARD_STRICT_MIN_SCORE = 92
SCORECARD_STRICT_DIMENSIONS = {
    "mechanism_depth",
    "source_grounding",
    "engineering_usability",
    "evaluation_design",
}
SCORECARD_DIMENSIONS = [
    "topic_coverage",
    "technical_routes",
    "mechanism_depth",
    "source_grounding",
    "engineering_usability",
    "evaluation_design",
    "adoption_guidance",
    "readability",
]


@dataclass(frozen=True)
class CodexResearchLoopConfig:
    topic: str
    workspace: Path
    max_rounds: int
    min_sources: int
    source_year: int
    min_source_year_ratio: float
    min_revision_rounds: int
    min_section_chars: int
    codex_bin: str
    codex_timeout_seconds: int
    model: str | None
    validator_model: str | None
    validator_reasoning_effort: str | None
    sandbox: str
    approval_policy: str
    keep_workspace: bool
    dry_run: bool
    enable_search: bool
    enable_agent_validator: bool


class CodexResearchLoopRunner:
    """External loop around Codex CLI for research reports.

    The controller owns trigger, workspace, validation, logs, protected files,
    round budget, and stop conditions. Codex owns web search, tool use, source
    gathering, synthesis, and report writing.
    """

    def __init__(self, config: CodexResearchLoopConfig) -> None:
        self.config = config
        self.workspace = config.workspace

    def run(self) -> bool:
        self._prepare_workspace()
        self._print_header()
        validation: dict[str, Any] = initial_goal_context()

        for round_number in range(1, self.config.max_rounds + 1):
            prompt = self._build_codex_prompt(round_number, validation)
            prompt_path = self.workspace / f"round_{round_number}_codex_prompt.md"
            write_text(prompt_path, prompt)
            command = self._codex_command(round_number)

            if self.config.dry_run:
                print(f"\n=== Round {round_number}: dry run ===")
                print(f"Command: {shlex.join(command)}")
                print(f"Prompt saved to: {prompt_path}")
                print(prompt)
                print("\nSTOP: dry run printed the first Codex invocation.")
                print(f"Workspace: {self.workspace}")
                return True

            protected_before = self._read_protected_files()
            print(f"\n=== Round {round_number}: trigger Codex writer ===")
            print(f"Command: {shlex.join(command)}")
            print(f"Prompt saved to: {prompt_path}")
            completed = self._run_codex_process(command, prompt)
            write_text(
                self.workspace / f"round_{round_number}_codex.stdout.log",
                completed.stdout,
            )
            write_text(
                self.workspace / f"round_{round_number}_codex.stderr.log",
                completed.stderr,
            )
            if completed.stdout.strip():
                print("\nCodex stdout:")
                print(trim_output(completed.stdout))
            if completed.stderr.strip():
                print("\nCodex stderr:")
                print(trim_output(completed.stderr))

            changed_protected = self._changed_protected_files(protected_before)
            if changed_protected:
                self._restore_protected_files(protected_before)
                print("\nSTOP: Codex changed protected loop-control files.")
                for path in changed_protected:
                    print(f"  protected: {path}")
                print("The controller restored those files and rejected the turn.")
                print(f"Workspace: {self.workspace}")
                return False

            if completed.returncode != 0:
                if self._has_candidate_outputs():
                    print(
                        "\nWARN: codex exec exited with code "
                        f"{completed.returncode}, but candidate outputs exist."
                    )
                    print(
                        "The controller will continue to the next external "
                        "validation instead of discarding the turn."
                    )
                else:
                    print(f"\nSTOP: codex exec failed with exit code {completed.returncode}.")
                    print("No candidate report/sources were produced for validation.")
                    print(f"Workspace: {self.workspace}")
                    return False

            print(f"\n=== Round {round_number}: external validation ===")
            validation, hard_error = self._run_external_validation(f"round_{round_number}")
            print(json.dumps(validation, ensure_ascii=False, indent=2))

            if hard_error:
                print("\nSTOP: validator agent failed, so the controller cannot trust validation.")
                print(f"Workspace: {self.workspace}")
                return False

            if validation["passed"]:
                print("\nSTOP: report passed hybrid validation.")
                print(f"Workspace: {self.workspace}")
                return True

        print("\nSTOP: round budget exhausted before report passed validation.")
        print(f"Workspace: {self.workspace}")
        return False

    def _prepare_workspace(self) -> None:
        if self.workspace.exists() and not self.config.keep_workspace:
            shutil.rmtree(self.workspace)
        self.workspace.mkdir(parents=True, exist_ok=True)
        write_text(self.workspace / "brief.md", self._render_template("brief.md"))
        write_text(
            self.workspace / "rubric.md",
            self._render_template("rubric.md"),
        )
        if not (self.workspace / "sources.json").exists():
            write_json(self.workspace / "sources.json", [])

    def _print_header(self) -> None:
        print("Codex Research Loop Engineering")
        print(f"Topic: {self.config.topic}")
        print(f"Workspace: {self.workspace}")
        print("\nLoop contract:")
        print("  automation : this CLI invocation")
        print("  actor      : Codex CLI")
        print("  search     : Codex CLI --search" if self.config.enable_search else "  search     : disabled")
        print("  verifier   : deterministic report validator")
        print(
            "  agent eval : Codex validator agent"
            if self.config.enable_agent_validator
            else "  agent eval : disabled"
        )
        print("  protected  : brief.md, rubric.md, validation files")
        print("  memory     : sources.json, research_report.md, prompts, Codex logs")
        print(
            "  recency    : "
            f"{format_ratio(self.config.min_source_year_ratio)} sources from "
            f"{self.config.source_year}"
        )
        quality_parts = [f"{self.config.min_section_chars}+ chars per body section"]
        if self.config.min_revision_rounds > 0:
            quality_parts.insert(
                0,
                f"{self.config.min_revision_rounds}+ Codex revisions",
            )
        print(f"  quality    : {', '.join(quality_parts)}")
        print(f"  budget     : {self.config.max_rounds} rounds")

    def _codex_command(self, round_number: int) -> list[str]:
        command = [
            self.config.codex_bin,
            "--cd",
            str(self.workspace.resolve()),
            "--sandbox",
            self.config.sandbox,
            "--ask-for-approval",
            self.config.approval_policy,
        ]
        if self.config.enable_search:
            command.append("--search")
        if self.config.model:
            command.extend(["--model", self.config.model])
        command.extend(
            [
                "exec",
                "--skip-git-repo-check",
                "--ephemeral",
                "--output-last-message",
                str((self.workspace / f"round_{round_number}_codex.md").resolve()),
                "-",
            ]
        )
        return command

    def _build_codex_prompt(self, round_number: int, validation: dict[str, Any]) -> str:
        return self._render_template(
            "codex_prompt.md",
            {
                "round_number": str(round_number),
                "validation_json": json.dumps(validation, ensure_ascii=False, indent=2),
            },
        )

    def _run_external_validation(self, label: str) -> tuple[dict[str, Any], bool]:
        rule_validation = validate_report(
            self.workspace / "research_report.md",
            self.workspace / "sources.json",
            self.config.min_sources,
            self.config.source_year,
            self.config.min_source_year_ratio,
            self.config.min_revision_rounds,
            self.config.min_section_chars,
        ).to_dict()
        write_json(self.workspace / f"{label}_rule_validation.json", rule_validation)

        agent_validation = None
        hard_error = False
        if self.config.enable_agent_validator:
            agent_validation = self._run_agent_validator(label, rule_validation)
            hard_error = bool(agent_validation.get("controller_error"))

        combined = combine_validation(rule_validation, agent_validation)
        write_json(self.workspace / f"{label}_validation.json", combined)
        return combined, hard_error

    def _run_agent_validator(
        self,
        label: str,
        rule_validation: dict[str, Any],
    ) -> dict[str, Any]:
        prompt = self._render_template(
            "validator_prompt.md",
            {
                "round_label": label,
                "rule_validation_json": json.dumps(
                    rule_validation,
                    ensure_ascii=False,
                    indent=2,
                ),
            },
        )
        prompt_path = self.workspace / f"{label}_validator_prompt.md"
        write_text(prompt_path, prompt)
        command = self._validator_command()

        print(f"\n=== {label}: Codex validator agent ===")
        print(f"Command: {shlex.join(command)}")
        print(f"Prompt saved to: {prompt_path}")
        if self.config.dry_run:
            print(prompt)
            return {
                "passed": False,
                "controller_error": False,
                "score": 0,
                "issues": [
                    {
                        "severity": "major",
                        "section": "controller",
                        "message": "dry run skipped Codex validator agent",
                    }
                ],
                "required_fixes": ["Run without --dry-run to execute validator agent."],
                "scorecard": empty_scorecard(),
                "modification_suggestions": [],
                "non_blocking_findings": [],
                "residual_risks": [],
                "source_audit_notes": [],
                "next_improvements": [],
            }

        completed = self._run_codex_process(command, prompt)
        write_text(self.workspace / f"{label}_validator.md", completed.stdout)
        write_text(self.workspace / f"{label}_validator.stdout.log", completed.stdout)
        write_text(self.workspace / f"{label}_validator.stderr.log", completed.stderr)
        if completed.stdout.strip():
            print("\nValidator stdout:")
            print(trim_output(completed.stdout))
        if completed.stderr.strip():
            print("\nValidator stderr:")
            print(trim_output(completed.stderr))

        if completed.returncode != 0:
            return {
                "passed": False,
                "controller_error": True,
                "score": 0,
                "issues": [
                    {
                        "severity": "critical",
                        "section": "controller",
                        "message": (
                            "Codex validator agent exited with code "
                            f"{completed.returncode}"
                        ),
                    }
                ],
                "required_fixes": ["Check Codex CLI authentication, network, and logs."],
                "scorecard": empty_scorecard(),
                "modification_suggestions": [],
                "non_blocking_findings": [],
                "residual_risks": [],
                "source_audit_notes": [],
                "next_improvements": [],
            }

        parsed = parse_validator_json(completed.stdout)
        write_json(self.workspace / f"{label}_agent_validation.json", parsed)
        return parsed

    def _validator_command(self) -> list[str]:
        command = [
            self.config.codex_bin,
            "--cd",
            str(self.workspace.resolve()),
            "--sandbox",
            "read-only",
            "--ask-for-approval",
            self.config.approval_policy,
        ]
        if self.config.enable_search:
            command.append("--search")
        validator_model = self.config.validator_model or self.config.model
        if validator_model:
            command.extend(["--model", validator_model])
        if self.config.validator_reasoning_effort:
            command.extend(
                [
                    "-c",
                    (
                        "model_reasoning_effort="
                        f'"{self.config.validator_reasoning_effort}"'
                    ),
                ]
            )
        command.extend(
            [
                "exec",
                "--skip-git-repo-check",
                "--ephemeral",
                "-",
            ]
        )
        return command

    def _render_template(
        self,
        template_name: str,
        extra_values: dict[str, str] | None = None,
    ) -> str:
        values = {
            "topic": self.config.topic,
            "max_rounds": str(self.config.max_rounds),
            "min_sources": str(self.config.min_sources),
            "source_year": str(self.config.source_year),
            "min_source_year_ratio": format_ratio(self.config.min_source_year_ratio),
            "min_revision_rounds": str(self.config.min_revision_rounds),
            "min_section_chars": str(self.config.min_section_chars),
            "required_sections": format_required_sections(),
        }
        if extra_values:
            values.update(extra_values)
        template = Template((TEMPLATE_DIR / template_name).read_text(encoding="utf-8"))
        return template.safe_substitute(values)

    def _run_codex_process(
        self,
        command: list[str],
        prompt: str,
    ) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                command,
                input=prompt,
                check=False,
                capture_output=True,
                text=True,
                timeout=self.config.codex_timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            return subprocess.CompletedProcess(
                args=command,
                returncode=124,
                stdout=decode_timeout_output(exc.stdout),
                stderr=(
                    decode_timeout_output(exc.stderr)
                    + "\nCodex process timed out after "
                    + f"{self.config.codex_timeout_seconds} seconds."
                ).strip(),
            )

    def _read_protected_files(self) -> dict[str, str]:
        protected = {}
        for path in self.workspace.glob("*"):
            if not path.is_file():
                continue
            if is_protected(path):
                protected[path.name] = path.read_text(encoding="utf-8")
        return protected

    def _changed_protected_files(self, before: dict[str, str]) -> list[str]:
        changed = []
        for name, content in before.items():
            path = self.workspace / name
            if not path.exists() or path.read_text(encoding="utf-8") != content:
                changed.append(name)
        return changed

    def _restore_protected_files(self, before: dict[str, str]) -> None:
        for name, content in before.items():
            (self.workspace / name).write_text(content, encoding="utf-8")

    def _has_candidate_outputs(self) -> bool:
        report_path = self.workspace / "research_report.md"
        sources_path = self.workspace / "sources.json"
        return (
            report_path.exists()
            and report_path.stat().st_size > 0
            and sources_path.exists()
            and sources_path.stat().st_size > 2
        )


def is_protected(path: Path) -> bool:
    name = path.name
    return (
        name in {"brief.md", "rubric.md"}
        or name.endswith("_validation.json")
        or name == "final_validation.json"
    )


def write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def trim_output(output: str, max_lines: int = 120) -> str:
    lines = output.strip().splitlines()
    if len(lines) <= max_lines:
        return output.strip()
    head = lines[: max_lines // 2]
    tail = lines[-max_lines // 2 :]
    omitted = len(lines) - len(head) - len(tail)
    return "\n".join(head + [f"... {omitted} lines omitted ..."] + tail)


def decode_timeout_output(output: str | bytes | None) -> str:
    if output is None:
        return ""
    if isinstance(output, bytes):
        return output.decode("utf-8", errors="replace")
    return output


def format_ratio(ratio: float) -> str:
    return f"{ratio:.0%}"


def format_required_sections() -> str:
    return "、".join(f"`## {section}`" for section in REQUIRED_SECTIONS)


def initial_goal_context() -> dict[str, Any]:
    return {
        "passed": False,
        "validation_mode": "initial_goal",
        "issues": [],
        "goal": (
            "Create the first complete research_report.md and sources.json "
            "from brief.md and rubric.md. No previous validation exists yet."
        ),
        "rule_validation": None,
        "agent_validation": None,
        "agent_review": {
            "non_blocking_findings": [],
            "residual_risks": [],
            "source_audit_notes": [],
            "next_improvements": [],
        },
    }


def combine_validation(
    rule_validation: dict[str, Any],
    agent_validation: dict[str, Any] | None,
) -> dict[str, Any]:
    issues: list[str] = []
    if not rule_validation["passed"]:
        issues.extend(str(issue) for issue in rule_validation.get("issues", []))
    if agent_validation and not agent_validation.get("passed", False):
        issues.extend(format_agent_issues(agent_validation))

    return {
        "passed": rule_validation["passed"]
        and (agent_validation is None or bool(agent_validation.get("passed"))),
        "validation_mode": "hybrid" if agent_validation is not None else "rules_only",
        "issues": issues,
        "rule_validation": rule_validation,
        "agent_validation": agent_validation,
        "agent_review": extract_agent_review(agent_validation),
    }


def format_agent_issues(agent_validation: dict[str, Any]) -> list[str]:
    issues = []
    for issue in agent_validation.get("issues", []):
        if isinstance(issue, dict):
            severity = issue.get("severity", "unknown")
            section = issue.get("section", "overall")
            message = issue.get("message", "")
            issues.append(f"agent {severity} issue in {section}: {message}")
        else:
            issues.append(f"agent issue: {issue}")
    for fix in agent_validation.get("required_fixes", []):
        issues.append(f"agent required fix: {fix}")
    for suggestion in agent_validation.get("modification_suggestions", []):
        issues.append(f"agent modification suggestion: {suggestion}")
    return issues


def extract_agent_review(agent_validation: dict[str, Any] | None) -> dict[str, Any] | None:
    if agent_validation is None:
        return None
    return {
        "non_blocking_findings": agent_validation.get("non_blocking_findings", []),
        "residual_risks": agent_validation.get("residual_risks", []),
        "source_audit_notes": agent_validation.get("source_audit_notes", []),
        "next_improvements": agent_validation.get("next_improvements", []),
    }


def parse_validator_json(output: str) -> dict[str, Any]:
    data = extract_json_object(output)
    if data is None:
        return {
            "passed": False,
            "controller_error": False,
            "score": 0,
            "summary": "validator agent did not return valid JSON",
            "issues": [
                {
                    "severity": "critical",
                    "section": "validator",
                    "message": "Output did not contain a valid JSON object.",
                }
            ],
            "required_fixes": ["Return a single JSON object that matches the schema."],
            "scorecard": empty_scorecard(),
            "modification_suggestions": [],
            "non_blocking_findings": [],
            "residual_risks": [],
            "source_audit_notes": [],
            "next_improvements": [],
        }

    score = normalize_score(data.get("score"))
    issues = normalize_issue_list(data.get("issues"))
    required_fixes = normalize_string_list(data.get("required_fixes"))
    scorecard = normalize_scorecard(data.get("scorecard"))
    modification_suggestions = normalize_string_list(
        data.get("modification_suggestions")
    )
    non_blocking_findings = normalize_review_findings(
        data.get("non_blocking_findings")
    )
    residual_risks = normalize_string_list(data.get("residual_risks"))
    source_audit_notes = normalize_string_list(data.get("source_audit_notes"))
    next_improvements = normalize_string_list(data.get("next_improvements"))

    passed = bool(data.get("passed"))
    if score < AGENT_PASSING_SCORE:
        passed = False
        issues.append(
            {
                "severity": "major",
                "section": "overall",
                "message": (
                    f"Validator score {score} is below the passing threshold "
                    f"{AGENT_PASSING_SCORE}."
                ),
            }
        )
    critical_count = count_issues_by_severity(issues, "critical")
    if critical_count:
        passed = False
        issues.append(
            {
                "severity": "major",
                "section": "overall",
                "message": (
                    f"Validator returned {critical_count} critical issue(s); "
                    "critical issues block passing."
                ),
            }
        )
    major_count = count_issues_by_severity(issues, "major")
    if major_count > 1:
        passed = False
        issues.append(
            {
                "severity": "major",
                "section": "overall",
                "message": (
                    f"Validator returned {major_count} major issue(s); "
                    "at most one major issue is allowed."
                ),
            }
        )
    if modification_suggestions:
        passed = False
        issues.append(
            {
                "severity": "major",
                "section": "overall",
                "message": (
                    "Validator agent still has modification suggestions; "
                    "the controller requires zero suggestions before passing."
                ),
            }
        )
    if required_fixes:
        passed = False
    scorecard_issues = validate_scorecard(scorecard)
    if scorecard_issues:
        passed = False
        issues.extend(scorecard_issues)
    review_contract_issues = validate_no_review_backlog(
        non_blocking_findings,
        residual_risks,
        source_audit_notes,
        next_improvements,
    )
    if review_contract_issues:
        passed = False
        issues.extend(review_contract_issues)

    return {
        "passed": passed,
        "controller_error": False,
        "score": score,
        "summary": str(data.get("summary", "")).strip(),
        "issues": issues,
        "required_fixes": required_fixes,
        "scorecard": scorecard,
        "modification_suggestions": modification_suggestions,
        "non_blocking_findings": non_blocking_findings,
        "residual_risks": residual_risks,
        "source_audit_notes": source_audit_notes,
        "next_improvements": next_improvements,
    }


def extract_json_object(output: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    for index, char in enumerate(output):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(output[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def normalize_score(value: Any) -> int:
    try:
        return max(0, min(100, int(value)))
    except (TypeError, ValueError):
        return 0


def empty_scorecard() -> dict[str, dict[str, Any]]:
    return {
        dimension: {"score": 0, "blocking_notes": []}
        for dimension in SCORECARD_DIMENSIONS
    }


def normalize_scorecard(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        return empty_scorecard()
    scorecard: dict[str, dict[str, Any]] = {}
    for dimension in SCORECARD_DIMENSIONS:
        raw_item = value.get(dimension)
        if isinstance(raw_item, dict):
            scorecard[dimension] = {
                "score": normalize_score(raw_item.get("score")),
                "blocking_notes": normalize_string_list(
                    raw_item.get("blocking_notes")
                ),
            }
        else:
            scorecard[dimension] = {"score": 0, "blocking_notes": []}
    return scorecard


def normalize_issue_list(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    issues = []
    for item in value:
        if isinstance(item, dict):
            issues.append(
                {
                    "severity": str(item.get("severity", "major")),
                    "section": str(item.get("section", "overall")),
                    "message": str(item.get("message", "")),
                }
            )
        else:
            issues.append(
                {
                    "severity": "major",
                    "section": "overall",
                    "message": str(item),
                }
            )
    return issues


def normalize_review_findings(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    findings = []
    for item in value:
        if isinstance(item, dict):
            findings.append(
                {
                    "section": str(item.get("section", "overall")),
                    "message": str(item.get("message", "")),
                    "why_non_blocking": str(item.get("why_non_blocking", "")),
                }
            )
        else:
            findings.append(
                {
                    "section": "overall",
                    "message": str(item),
                    "why_non_blocking": "",
                }
            )
    return findings


def normalize_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def count_issues_by_severity(issues: list[dict[str, str]], severity: str) -> int:
    return sum(1 for issue in issues if issue.get("severity", "").lower() == severity)


def validate_scorecard(scorecard: dict[str, dict[str, Any]]) -> list[dict[str, str]]:
    issues = []
    for dimension in SCORECARD_DIMENSIONS:
        item = scorecard.get(dimension, {})
        score = normalize_score(item.get("score"))
        min_score = (
            SCORECARD_STRICT_MIN_SCORE
            if dimension in SCORECARD_STRICT_DIMENSIONS
            else SCORECARD_MIN_SCORE
        )
        if score < min_score:
            issues.append(
                {
                    "severity": "major",
                    "section": "validator",
                    "message": (
                        f"Scorecard dimension {dimension} scored {score}; "
                        f"minimum is {min_score}."
                    ),
                }
            )
        blocking_notes = normalize_string_list(item.get("blocking_notes"))
        if blocking_notes:
            issues.append(
                {
                    "severity": "major",
                    "section": "validator",
                    "message": (
                        f"Scorecard dimension {dimension} still has "
                        "blocking notes; all scorecard blockers must be "
                        "resolved before passing."
                    ),
                }
            )
    return issues


def validate_no_review_backlog(
    non_blocking_findings: list[dict[str, str]],
    residual_risks: list[str],
    source_audit_notes: list[str],
    next_improvements: list[str],
) -> list[dict[str, str]]:
    issues = []
    if non_blocking_findings:
        issues.append(
            {
                "severity": "major",
                "section": "validator",
                "message": (
                    "Validator returned unresolved non_blocking_findings; "
                    "review backlog must be resolved before passing."
                ),
            }
        )
    if residual_risks:
        issues.append(
            {
                "severity": "major",
                "section": "validator",
                "message": (
                    "Validator returned unresolved residual_risks; "
                    "review backlog must be resolved before passing."
                ),
            }
        )
    if source_audit_notes:
        issues.append(
            {
                "severity": "major",
                "section": "validator",
                "message": (
                    "Validator returned unresolved source_audit_notes; "
                    "review backlog must be resolved before passing."
                ),
            }
        )
    if next_improvements:
        issues.append(
            {
                "severity": "major",
                "section": "validator",
                "message": (
                    "Validator returned unresolved next_improvements; "
                    "review backlog must be resolved before passing."
                ),
            }
        )
    return issues
