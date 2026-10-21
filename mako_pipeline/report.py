"""Structured issues and reports (spec section 12)."""
from __future__ import annotations

import json
import traceback
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

from .constants import ERROR_CODES, FATAL_CODES, WARNING_CODES


@dataclass
class Issue:
    code: str
    severity: str
    message: str
    file: Optional[str] = None
    section: Optional[int] = None
    speaker: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class Report:
    stage: str = ""
    lines: List[str] = field(default_factory=list)
    issues: List[Issue] = field(default_factory=list)
    decisions: List[str] = field(default_factory=list)
    counts: Dict[str, Any] = field(default_factory=dict)
    outputs: List[str] = field(default_factory=list)
    debug: List[str] = field(default_factory=list)
    failed: bool = False

    # -- building -----------------------------------------------------------
    def log(self, text: str = "") -> None:
        self.lines.append(text)

    def decide(self, text: str) -> None:
        self.decisions.append(text)

    def add(self, code: str, message: str, file: Optional[str] = None,
            section: Optional[int] = None, speaker: Optional[str] = None) -> Issue:
        sev = "error" if code in ERROR_CODES else "warning"
        issue = Issue(code, sev, message, file, section, speaker)
        self.issues.append(issue)
        return issue

    def fail(self, code: str, message: str, **kw) -> None:
        """A stage-level error: the stage cannot proceed."""
        self.add(code, message, **kw)
        self.failed = True
        self.log(f"ERROR {code}: {message}")

    def exception(self, where: str, exc: BaseException) -> None:
        self.debug.append(f"{where}\n" + "".join(
            traceback.format_exception(type(exc), exc, exc.__traceback__)))

    def extend(self, other: "Report") -> None:
        self.lines += other.lines
        self.issues += other.issues
        self.decisions += other.decisions
        self.outputs += other.outputs
        self.debug += other.debug
        self.failed = self.failed or other.failed

    # -- results ------------------------------------------------------------
    @property
    def ok(self) -> bool:
        return not self.failed

    def exit_code(self) -> int:
        """0 ok; 1 completed with warnings; 2 failed / stage error."""
        if self.failed or any(i.code in FATAL_CODES for i in self.issues):
            return 2
        return 1 if self.issues else 0

    def warnings_text(self) -> str:
        if not self.issues:
            return "No warnings."
        return "\n".join(f"- [{i.code}] {i.message}" for i in self.issues)

    def to_text(self, with_decisions: bool = True) -> str:
        out = list(self.lines)
        out.append("")
        out.append("WARNINGS (read these):" if self.issues else "No warnings.")
        if self.issues:
            out += [f"  - [{i.code}] {i.message}" for i in self.issues]
        if with_decisions and self.decisions:
            out += ["", "DECISION LOG (what the tool decided, and which setting changes it):"]
            out += [f"  {d}" for d in self.decisions]
        if self.debug:
            out += ["", "DEBUG"] + self.debug
        return "\n".join(out) + "\n"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "stage": self.stage,
            "ok": self.ok,
            "counts": self.counts,
            "issues": [i.to_dict() for i in self.issues],
            "outputs": self.outputs,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False)


def issue_codes() -> List[str]:
    return sorted(ERROR_CODES | WARNING_CODES)
