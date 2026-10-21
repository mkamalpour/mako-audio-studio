"""Naming standard (spec section 6) and section-list grammar (7.2)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .constants import SECTION_CODES, SPEAKERS

CLEAN_RE = re.compile(r"^S([0-9a-z]{1,3})_(\d{1,4})_([a-z]+)$", re.I)  # applied to the stem
_SMART_DIGITS = re.compile(r"^\s*(?:s(?:ec(?:tion)?)?)?[\s._\-]*(\d{1,3})[\s._\-]+(\d{1,4})(?!\d)", re.I)
_SMART_LETTER = re.compile(r"^\s*s[\s._\-]*([a-z])[\s._\-]+(\d{1,4})(?!\d)", re.I)


_DIGIT_MAP = {ord(c): str(i) for i, c in enumerate("۰۱۲۳۴۵۶۷۸۹")}          # Persian
_DIGIT_MAP.update({ord(c): str(i) for i, c in enumerate("٠١٢٣٤٥٦٧٨٩")})   # Arabic-Indic
_DATE_LIKE = re.compile(r"(?<!\d)(?:(?:19|20)\d{2}[-._ ]\d{1,2}[-._ ]\d{1,2}|\d{1,2}[-._ ]\d{1,2}[-._ ](?:19|20)\d{2})(?!\d)")


def normalize_digits(s: str) -> str:
    """Persian / Arabic-Indic digits -> ASCII, so files named with them are read like any other."""
    return s.translate(_DIGIT_MAP)


def sec_code(n: int) -> str:
    """Section number -> code used in clean names (10..35 -> a..z)."""
    return SECTION_CODES[n - 1] if 10 <= n <= 35 else str(n)


def parse_sec_code(tok: str) -> Optional[int]:
    """Inverse of sec_code. Returns None when the token is not a section code."""
    tok = tok.strip()
    if tok.isdigit():
        return int(tok)
    if len(tok) == 1 and tok.isalpha() and tok.lower() in SECTION_CODES:
        return SECTION_CODES.index(tok.lower()) + 1
    return None


def build_clean_name(section: int, line: int, speaker: str, ext: str = ".mp3") -> str:
    if not ext.startswith("."):
        ext = "." + ext
    prefix = 'K' if speaker == 'koroush' else 'R'
    if 1 <= section <= 35 and 1 <= line <= 35:
        return f'{prefix}{sec_code(section)}{sec_code(line)}{ext}'
    return f'{prefix}{section}_{line}{ext}'


def parse_clean(stem: str) -> Optional[Tuple[int, int, str]]:
    """Parse a clean-file stem -> (section, line, speaker_lowercase) or None."""
    m = re.fullmatch(r'([kr])([1-9a-z])([1-9a-z])', stem, re.I)
    if m:
        return parse_sec_code(m[2]), parse_sec_code(m[3]), 'koroush' if m[1].lower() == 'k' else 'reza'
    m = re.fullmatch(r'([kr])([1-9][0-9]{0,2})_([1-9][0-9]{0,3})', stem, re.I)
    if m:
        return int(m[2]), int(m[3]), 'koroush' if m[1].lower() == 'k' else 'reza'
    m = CLEAN_RE.match(stem)
    if not m:
        return None
    sec = parse_sec_code(m.group(1))
    line = int(m.group(2))
    if sec is None or sec < 1 or line < 1:
        return None
    return sec, line, m.group(3).lower()


def smart_parse(stem: str) -> Optional[Tuple[int, int]]:
    """Auto-read parser for raw file stems. Date-like names (12-05-2024) are refused, not guessed."""
    stem = normalize_digits(stem)
    if _DATE_LIKE.search(stem):
        return None
    m = re.fullmatch(r"[kr](\d)(\d{1,4})(?:[ _-]+(?:take|final|copy).*|\s*\(\d+\))?", stem, re.I)
    if m:
        sec, line = map(int, m.groups())
        return (sec, line) if sec > 0 and line > 0 else None
    stem = re.sub(r"^[kr](?=\d+[_ .-])", "", stem, flags=re.I)
    m = _SMART_DIGITS.match(stem)
    if m:
        sec, line = int(m.group(1)), int(m.group(2))
    else:
        m = _SMART_LETTER.match(stem)
        if not m:
            return None
        sec = parse_sec_code(m.group(1))
        line = int(m.group(2))
        if sec is None:
            return None
    if sec < 1 or line < 1:
        return None
    return sec, line


def compact_parse(stem: str, k: int) -> Optional[Tuple[int, int]]:
    """'compact' style: stem is all digits, first k digits = section, rest = line."""
    stem = normalize_digits(stem)
    if not stem.isdigit() or len(stem) <= k:
        return None
    sec, line = int(stem[:k]), int(stem[k:])
    if sec < 1 or line < 1:
        return None
    return sec, line


def last_integer(stem: str) -> Optional[int]:
    nums = re.findall(r"\d+", normalize_digits(stem))
    return int(nums[-1]) if nums else None


# ----------------------------------------------------------------------------
# Section list for the "sequence" style
# ----------------------------------------------------------------------------
@dataclass
class SectionMap:
    kind: str = "none"                         # "none" | "counts" | "ranges"
    counts: List[int] = field(default_factory=list)
    ranges: Dict[int, Tuple[int, int]] = field(default_factory=dict)

    def map(self, key: int, pos: int) -> Optional[Tuple[int, int]]:
        """Return (section, line) for a file, or None when it is not covered (never section 1)."""
        if self.kind == "none":
            return 1, key
        if self.kind == "counts":
            start = 0
            for i, c in enumerate(self.counts):
                if pos <= start + c:
                    return i + 1, pos - start
                start += c
            return None
        for sec, (a, b) in self.ranges.items():
            if a <= key <= b:
                return sec, key - a + 1
        return None


def parse_sections_text(text: str) -> SectionMap:
    """Parse the section list grammar (counts or ranges). Raises ValueError on bad input."""
    bad = ValueError(
        "Can't read the section list. Use counts like \"5, 8, 8, 7\" "
        "or ranges like \"1:1-5, 2:6-13\"."
    )
    parts = [p.strip() for p in re.split(r"[,;\n]+", text or "") if p.strip()]
    if not parts:
        return SectionMap("none")
    if all(re.fullmatch(r"\d+", p) for p in parts):
        counts = [int(p) for p in parts]
        if any(c < 1 for c in counts):
            raise bad
        return SectionMap("counts", counts=counts)
    ranges: Dict[int, Tuple[int, int]] = {}
    for p in parts:
        m = re.fullmatch(r"([0-9a-zA-Z]{1,3})\s*:\s*(\d+)(?:\s*-\s*(\d+))?", p)
        if not m:
            raise bad
        sec = parse_sec_code(m.group(1))
        a = int(m.group(2))
        b = int(m.group(3)) if m.group(3) else a
        if sec is None or sec < 1 or b < a or sec in ranges:
            raise bad
        ranges[sec] = (a, b)
    return SectionMap("ranges", ranges=ranges)


def speaker_from_token(tok: str) -> Optional[str]:
    t = tok.strip().lower()
    if t in SPEAKERS:
        return t
    if t in ("k", "r"):
        return "koroush" if t == "k" else "reza"
    return None


def compile_pattern(pattern: str):
    """Literal templates only: no user-supplied regular expressions."""
    if pattern.count('{section}') != 1 or pattern.count('{line}') != 1:
        raise ValueError('Custom pattern needs {section} and {line} exactly once.')
    parts = re.split(r'(\{section\}|\{line\})', pattern)
    literals = ''.join(p for p in parts if p not in ('{section}', '{line}'))
    if '{' in literals or '}' in literals or '/' in literals or chr(92) in literals:
        raise ValueError('Use a filename stem with only {section} and {line} placeholders.')
    compact = '{section}{line}' in pattern or '{line}{section}' in pattern
    body = ''.join(r'(?P<section>[0-9]{1,3})' if p == '{section}' else
                   r'(?P<line>[0-9]{1,4})' if p == '{line}' else re.escape(p) for p in parts)
    if compact:
        body = body.replace('[0-9]{1,3}', '[1-9a-z]').replace('[0-9]{1,4}', '[1-9a-z]')
    return re.compile(body, re.I)


def pattern_parse(stem: str, pattern: str) -> Optional[Tuple[int, int]]:
    m = compile_pattern(pattern).fullmatch(normalize_digits(stem))
    if not m:
        return None
    sec, line = parse_sec_code(m['section']), parse_sec_code(m['line'])
    return (sec, line) if sec > 0 and line > 0 else None


def mako_parse(stem: str, speaker: str) -> Optional[Tuple[int, int]]:
    """K11 through Kzz: one base-36 character for each coordinate (1..35)."""
    m = re.fullmatch(r'([kr])([1-9a-z])([1-9a-z])', normalize_digits(stem), re.I)
    if not m or m[1].lower() != speaker[0].lower():
        return None
    return parse_sec_code(m[2]), parse_sec_code(m[3])
