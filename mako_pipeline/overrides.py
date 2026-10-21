"""overrides.csv: per-line (and per-section) merge overrides (spec 9.12, C4)."""
from __future__ import annotations

import csv
import io
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from . import constants as C
from .names import parse_sec_code, sec_code
from .report import Issue
from .util import write_text_atomic

HEADER = ["section", "line", "speaker", "pause_before_ms", "gain_db", "trim", "room_tone"]
FIELDS = ("pause_before_ms", "gain_db", "trim", "room_tone")


@dataclass
class LineOverride:
    pause_before_ms: Optional[float] = None
    gain_db: Optional[float] = None
    trim: Optional[bool] = None
    room_tone: Optional[bool] = None


@dataclass
class Overrides:
    """line: (section, line, speaker) -> override.  section: (section, speaker or "") -> override."""
    line: Dict[Tuple[int, int, str], LineOverride]
    section: Dict[Tuple[int, str], LineOverride]
    used: Set[Tuple] = None  # type: ignore

    def __post_init__(self) -> None:
        if self.used is None:
            self.used = set()

    def lookup(self, sec: int, line: int, speaker: str, field: str):
        """Return (value, source) with precedence: per-line > per-section. (None, '') = inherit."""
        for key, table, src in (((sec, line, speaker), self.line, "line"),
                                ((sec, speaker), self.section, "section"),
                                ((sec, ""), self.section, "section")):
            ov = table.get(key)
            if ov is not None and getattr(ov, field) is not None:
                self.used.add(("L", key) if src == "line" else ("S", key))
                return getattr(ov, field), src
        return None, ""


def _bool(tok: str) -> Optional[bool]:
    t = tok.strip().lower()
    if t in ("on", "true", "yes", "1"):
        return True
    if t in ("off", "false", "no", "0"):
        return False
    raise ValueError(f"{tok!r} must be on or off")


def _num(tok: str, name: str, minimum: Optional[float] = None) -> float:
    try:
        v = float(tok)
    except ValueError:
        raise ValueError(f"{name} {tok!r} is not a number")
    if not math.isfinite(v):
        raise ValueError(f"{name} must be a finite number")
    if name == "gain_db" and not -60 <= v <= 60:
        raise ValueError("gain_db must be between -60 and 60")
    if name == "pause_before_ms" and v > 60000:
        raise ValueError("pause_before_ms must not exceed 60000")
    if minimum is not None and v < minimum:
        raise ValueError(f"{name} must be {minimum} or more")
    return v


def load_overrides(path) -> Tuple[Overrides, List[Issue]]:
    """Read overrides.csv. Invalid rows give W_OVERRIDE_INVALID and are ignored."""
    ov = Overrides({}, {})
    issues: List[Issue] = []
    p = Path(path)
    if not p.is_file():
        return ov, issues
    text = p.read_text(encoding="utf-8-sig", errors="replace")
    body = "\n".join(ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#"))
    for n, rec in enumerate(csv.reader(io.StringIO(body)), 1):
        if not rec:
            continue
        if rec[0].strip().lower() == "section":
            continue
        rec = [c.strip() for c in rec] + [""] * 7
        sec_t, line_t, spk, pause, gain, trim, tone = rec[:7]
        try:
            sec = parse_sec_code(sec_t) if sec_t else None
            if sec is None or sec < 1:
                raise ValueError(f"section {sec_t!r} is not a number or letter code")
            spk = spk.lower()
            if spk and spk not in C.SPEAKERS:
                raise ValueError(f"speaker {spk!r} must be one of {list(C.SPEAKERS)}")
            if line_t and (not line_t.isdigit() or not 1 <= int(line_t) <= 9999):
                raise ValueError(f"line {line_t!r} is not a number")
            if line_t and not spk:
                raise ValueError("a line row needs the speaker (lines are counted per speaker)")
            o = LineOverride(
                _num(pause, "pause_before_ms", -400) if pause else None,
                _num(gain, "gain_db") if gain else None,
                _bool(trim) if trim else None,
                _bool(tone) if tone else None)
        except ValueError as e:
            issues.append(Issue("W_OVERRIDE_INVALID", "warning",
                                f"{p.name} row {n}: {e}. The row is ignored."))
            continue
        if line_t:
            ov.line[(sec, int(line_t), spk)] = o
        else:
            ov.section[(sec, spk)] = o  # whole section (blank line): the per-section setting
    return ov, issues


def unused_issues(ov: Overrides, existing: Set[Tuple[int, int, str]],
                  sections: Set[int]) -> List[Issue]:
    """W_OVERRIDE_UNUSED for rows that match no file in this run."""
    out: List[Issue] = []
    for (sec, line, spk) in ov.line:
        if (sec, line, spk) not in existing:
            out.append(Issue("W_OVERRIDE_UNUSED", "warning",
                             f"overrides.csv: S{sec_code(sec)}_{line}_{spk} has an override row but "
                             "no such file takes part in this run.", section=sec, speaker=spk))
    for (sec, spk) in ov.section:
        if sec not in sections:
            out.append(Issue("W_OVERRIDE_UNUSED", "warning",
                             f"overrides.csv: section {sec} has an override row but no files in this run.",
                             section=sec))
    return out


def save_overrides(path, rows: List[List[str]]) -> None:
    """Write overrides.csv from table rows (empty rows are dropped)."""
    out = io.StringIO()
    out.write("# Per-line overrides. Blank cell = inherit. A row with no line applies to the whole section.\n"
              "# pause_before_ms is exact; a negative value (down to -400) makes the line start that much\n"
              "# before the previous line ends (talk-over). gain_db is extra gain for that line.\n")
    w = csv.writer(out, lineterminator="\n")
    w.writerow(HEADER)
    for r in rows:
        cells = ["" if c is None else str(c).strip() for c in r][:7]
        cells += [""] * (7 - len(cells))
        cells = ["" if c.lower() == "nan" else c for c in cells]
        if any(cells):
            w.writerow(cells)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(path, out.getvalue())


def read_rows(path) -> List[List[str]]:
    """Raw rows (without header/comments) for the UI table."""
    p = Path(path)
    if not p.is_file():
        return []
    text = p.read_text(encoding="utf-8-sig", errors="replace")
    body = "\n".join(ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#"))
    rows = [r for r in csv.reader(io.StringIO(body)) if r]
    if rows and rows[0] and rows[0][0].strip().lower() == "section":
        rows = rows[1:]
    return [([c.strip() for c in r] + [""] * 7)[:7] for r in rows]
