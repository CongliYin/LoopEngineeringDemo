"""Deterministic report validator for the research loop."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REQUIRED_SECTIONS = [
    "技术全景与发展脉络",
    "关键技术路线详解",
    "工程实现与代码设计",
    "落地选型与使用建议",
    "评估方法与实验设计",
    "参考来源",
]

BODY_SECTIONS = [section for section in REQUIRED_SECTIONS if section != "参考来源"]
SOURCE_FIELDS = ["id", "title", "url", "publisher", "date", "summary", "relevance"]
VAGUE_PATTERNS = ["很多人认为", "显著提升", "业界普遍", "毫无疑问"]
ENGINEERING_TERMS = ["loop", "验证器", "状态", "权限", "人工升级"]
ENGINEERING_SECTION = "工程实现与代码设计"


@dataclass(frozen=True)
class ValidationReport:
    passed: bool
    issues: list[str]
    missing_sections: list[str]
    cited_sources: list[str]
    source_count: int
    source_year: int
    source_year_count: int
    source_year_ratio: float
    completed_revisions: int
    min_revision_rounds: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "issues": self.issues,
            "missing_sections": self.missing_sections,
            "cited_sources": self.cited_sources,
            "source_count": self.source_count,
            "source_year": self.source_year,
            "source_year_count": self.source_year_count,
            "source_year_ratio": self.source_year_ratio,
            "completed_revisions": self.completed_revisions,
            "min_revision_rounds": self.min_revision_rounds,
        }


def validate_report(
    report_path: Path,
    sources_path: Path,
    min_sources: int,
    source_year: int,
    min_source_year_ratio: float,
    min_revision_rounds: int,
    min_section_chars: int,
) -> ValidationReport:
    issues: list[str] = []
    sources, source_issues = _read_sources(sources_path)
    issues.extend(source_issues)
    source_year_count = _count_sources_from_year(sources, source_year)
    source_year_ratio = _ratio(source_year_count, len(sources))
    completed_revisions = _count_completed_revisions(report_path.parent)

    if min_revision_rounds > 0 and completed_revisions < min_revision_rounds:
        issues.append(
            f"only {completed_revisions} Codex revision rounds completed; "
            f"need at least {min_revision_rounds}"
        )

    if not report_path.exists():
        return ValidationReport(
            passed=False,
            issues=issues + ["research_report.md does not exist"],
            missing_sections=REQUIRED_SECTIONS,
            cited_sources=[],
            source_count=len(sources),
            source_year=source_year,
            source_year_count=source_year_count,
            source_year_ratio=source_year_ratio,
            completed_revisions=completed_revisions,
            min_revision_rounds=min_revision_rounds,
        )

    report = report_path.read_text(encoding="utf-8")
    source_ids = [str(source.get("id")) for source in sources if source.get("id")]
    source_id_set = set(source_ids)
    cited_sources = sorted(set(re.findall(r"\[(S\d+)\]", report)))
    body = _body_without_references(report)
    body_cited_sources = sorted(set(re.findall(r"\[(S\d+)\]", body)))

    issues.extend(_validate_sources(sources))

    missing_sections = [
        section for section in REQUIRED_SECTIONS if f"## {section}" not in report
    ]
    if missing_sections:
        issues.append(f"missing required sections: {', '.join(missing_sections)}")

    if len(sources) < min_sources:
        issues.append(f"only {len(sources)} sources collected; need at least {min_sources}")

    required_year_sources = math.ceil(len(sources) * min_source_year_ratio)
    if source_year_count < required_year_sources:
        issues.append(
            f"only {source_year_count}/{len(sources)} sources are from {source_year}; "
            f"need at least {required_year_sources} "
            f"({min_source_year_ratio:.0%})"
        )

    valid_citations = [source_id for source_id in cited_sources if source_id in source_id_set]
    if len(valid_citations) < min_sources:
        issues.append(
            f"only {len(valid_citations)} cited sources found; need at least {min_sources}"
        )

    valid_body_citations = [
        source_id for source_id in body_cited_sources if source_id in source_id_set
    ]
    if len(valid_body_citations) < min_sources:
        issues.append(
            f"only {len(valid_body_citations)} sources cited in report body; "
            f"need at least {min_sources}"
        )

    unknown_citations = [
        source_id for source_id in cited_sources if source_id not in source_id_set
    ]
    if unknown_citations:
        issues.append(f"unknown citations: {', '.join(unknown_citations)}")

    for source_id in source_ids:
        if source_id not in body_cited_sources:
            issues.append(f"source {source_id} is collected but not cited in report body")

    references = _section_text(report, "参考来源")
    if references:
        for source in sources:
            source_id = str(source.get("id", ""))
            url = str(source.get("url", ""))
            if source_id and f"[{source_id}]" not in references:
                issues.append(f"source {source_id} is missing from reference section")
            if url and url not in references:
                issues.append(f"source {source_id} url is missing from reference section")

    for section in BODY_SECTIONS:
        content = _section_text(report, section)
        if content and _text_length(content) < min_section_chars:
            issues.append(
                f"section {section} is too short: "
                f"{_text_length(content)} chars; need at least {min_section_chars}"
            )
        section_citations = set(re.findall(r"\[(S\d+)\]", content))
        if content and not section_citations.intersection(source_id_set):
            issues.append(f"section {section} has no valid source citation")

    engineering = _section_text(report, ENGINEERING_SECTION)
    for term in ENGINEERING_TERMS:
        if engineering and term not in engineering:
            issues.append(f"{ENGINEERING_SECTION} section missing required term: {term}")
    if engineering and "```" not in engineering:
        issues.append(f"{ENGINEERING_SECTION} section must include a concrete code snippet")
    if engineering and "```mermaid" not in engineering:
        issues.append(f"{ENGINEERING_SECTION} section must include a Mermaid code flow diagram")

    technical_route = _section_text(report, "关键技术路线详解")
    if technical_route and "```mermaid" not in technical_route:
        issues.append("关键技术路线详解 section must include a Mermaid route/timeline diagram")

    for pattern in VAGUE_PATTERNS:
        if pattern in report:
            issues.append(f"vague unsupported phrase found: {pattern}")

    return ValidationReport(
        passed=not issues,
        issues=issues,
        missing_sections=missing_sections,
        cited_sources=valid_citations,
        source_count=len(sources),
        source_year=source_year,
        source_year_count=source_year_count,
        source_year_ratio=source_year_ratio,
        completed_revisions=completed_revisions,
        min_revision_rounds=min_revision_rounds,
    )


def _read_sources(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    if not path.exists():
        return [], []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [], [f"sources.json is not valid JSON: {exc}"]
    if isinstance(data, list):
        return [source for source in data if isinstance(source, dict)], []
    if isinstance(data, dict) and isinstance(data.get("sources"), list):
        return [source for source in data["sources"] if isinstance(source, dict)], []
    return [], ["sources.json must be a JSON array or an object with a sources array"]


def _validate_sources(sources: list[dict[str, Any]]) -> list[str]:
    issues: list[str] = []
    ids = [str(source.get("id", "")).strip() for source in sources]
    expected_ids = [f"S{index}" for index in range(1, len(sources) + 1)]
    if ids != expected_ids:
        issues.append(
            f"source ids must be consecutive {', '.join(expected_ids)}; "
            f"got {', '.join(ids) if ids else 'none'}"
        )

    urls: set[str] = set()
    for index, source in enumerate(sources, start=1):
        source_id = str(source.get("id", f"S{index}")).strip()
        for field in SOURCE_FIELDS:
            if not str(source.get(field, "")).strip():
                issues.append(f"source {source_id} missing required field: {field}")

        url = str(source.get("url", "")).strip()
        if url and not re.match(r"^https?://", url):
            issues.append(f"source {source_id} url must start with http:// or https://")
        if url in urls:
            issues.append(f"duplicate source url: {url}")
        if url:
            urls.add(url)

        if _source_year(source) is None:
            issues.append(
                f"source {source_id} date must start with YYYY or YYYY-MM-DD"
            )
        if _text_length(str(source.get("summary", ""))) < 30:
            issues.append(f"source {source_id} summary is too short")
        if _text_length(str(source.get("relevance", ""))) < 20:
            issues.append(f"source {source_id} relevance is too short")

    return issues


def _count_sources_from_year(sources: list[dict[str, Any]], year: int) -> int:
    return sum(1 for source in sources if _source_year(source) == year)


def _source_year(source: dict[str, Any]) -> int | None:
    date = str(source.get("date", "")).strip()
    match = re.match(r"^(\d{4})(?:\b|-)", date)
    if not match:
        return None
    return int(match.group(1))


def _ratio(part: int, total: int) -> float:
    if total == 0:
        return 0.0
    return round(part / total, 4)


def _count_completed_revisions(workspace: Path) -> int:
    return sum(
        1
        for path in workspace.glob("round_*_codex.md")
        if path.is_file() and path.stat().st_size > 0
    )


def _section_text(report: str, section: str) -> str:
    pattern = rf"^## {re.escape(section)}\s*$([\s\S]*?)(?=^## |\Z)"
    match = re.search(pattern, report, flags=re.MULTILINE)
    if not match:
        return ""
    return match.group(1).strip()


def _body_without_references(report: str) -> str:
    match = re.search(r"^## 参考来源\s*$", report, flags=re.MULTILINE)
    if not match:
        return report
    return report[: match.start()]


def _text_length(text: str) -> int:
    return len(re.sub(r"\s+", "", text))
