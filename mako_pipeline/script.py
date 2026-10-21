"""script.txt: explicit speaker order per section (spec section 8)."""
from __future__ import annotations

import re

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .names import parse_sec_code
from .report import Issue

MAX_REPEAT = 1000
MAX_LEN = 5000


@dataclass
class ScriptResult:
    sections: Dict[int, List[str]] = field(default_factory=dict)   # section -> ["K","R",...]
    exact: Dict[int, List[Tuple[str, int]]] = field(default_factory=dict)
    default: Optional[List[str]] = None
    issues: List[Issue] = field(default_factory=list)


class _Syntax(Exception):
    pass


def _parse_seq(text: str) -> List[str]:
    """item := K | R | '(' item+ ')' '*' INT ; whitespace optional."""
    toks: List[str] = []
    i, t = 0, text.upper()
    while i < len(t):
        c = t[i]
        if c.isspace():
            i += 1
        elif c in "KR()*":
            toks.append(c)
            i += 1
        elif c.isdigit():
            j = i
            while j < len(t) and t[j].isdigit():
                j += 1
            toks.append(t[i:j])
            i = j
        else:
            raise _Syntax(f"unexpected character {text[i]!r}")
    pos = [0]

    def items(stop_on_close: bool) -> List[str]:
        out: List[str] = []
        while pos[0] < len(toks):
            tk = toks[pos[0]]
            if tk in ("K", "R"):
                out.append(tk)
                pos[0] += 1
            elif tk == "(":
                pos[0] += 1
                inner = items(True)
                if pos[0] >= len(toks) or toks[pos[0]] != ")":
                    raise _Syntax("missing ')'")
                pos[0] += 1
                if pos[0] >= len(toks) or toks[pos[0]] != "*":
                    raise _Syntax("a group needs '*' and a number, like (K R)*3")
                pos[0] += 1
                if pos[0] >= len(toks) or not toks[pos[0]].isdigit():
                    raise _Syntax("a number must follow '*'")
                n = int(toks[pos[0]])
                pos[0] += 1
                if n < 1 or n > MAX_REPEAT:
                    raise _Syntax(f"repeat count must be 1..{MAX_REPEAT}")
                if not inner:
                    raise _Syntax("empty group")
                out.extend(inner * n)
            elif tk == ")":
                if stop_on_close:
                    return out
                raise _Syntax("unmatched ')'")
            else:
                raise _Syntax(f"unexpected {tk!r}")
            if len(out) > MAX_LEN:
                raise _Syntax("sequence is too long")
        return out

    seq = items(False)
    if not seq:
        raise _Syntax("no K/R items")
    return seq


def parse_script(text: str, source: str = "script.txt") -> ScriptResult:
    """Parse script text. Never raises: problems become E_SCRIPT_SYNTAX issues."""
    res = ScriptResult()
    for ln, raw in enumerate((text or "").splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        where = f"{source} line {ln}"
        if ":" not in line:
            res.issues.append(Issue("E_SCRIPT_SYNTAX", "error",
                                    f"{where}: expected 'section: K R K R' (got {line!r}). "
                                    "That section uses the default order.", file=source))
            continue
        head, body = line.split(":", 1)
        head = head.strip()
        if body.strip() == '-':
            sec = parse_sec_code(head)
            if sec is None or not 1 <= sec <= 999 or sec in res.sections or sec in res.exact:
                res.issues.append(Issue('E_SCRIPT_SYNTAX', 'error',
                                        f'{where}: use a unique section followed by - to exclude it.', file=source))
            else:
                res.exact[sec] = []
            continue
        # Explicit clip IDs are decimal line numbers, e.g. K3 R1 K1.
        explicit = bool(re.search(r'[KR]\d', body, re.I))
        if explicit:
            sec = parse_sec_code(head)
            tokens = body.upper().split()
            if sec is None or not 1 <= sec <= 999 or not tokens or any(
                    not re.fullmatch(r'[KR][1-9][0-9]{0,3}', t) for t in tokens):
                res.issues.append(Issue('E_SCRIPT_SYNTAX', 'error', f'{where}: use section: K3 R1 K1 (decimal line IDs).', file=source))
                continue
            seq_exact = [(SPEAKER_OF[t[0]], int(t[1:])) for t in tokens]
            if len(set(seq_exact)) != len(seq_exact) or sec in res.sections or sec in res.exact:
                res.issues.append(Issue('E_SCRIPT_SYNTAX', 'error', f'{where}: duplicate clip or section.', file=source))
                continue
            res.exact[sec] = seq_exact
            continue
        try:
            seq = _parse_seq(body)
        except _Syntax as e:
            res.issues.append(Issue("E_SCRIPT_SYNTAX", "error",
                                    f"{where}: {e}. That section uses the default order.",
                                    file=source))
            continue
        if head.lower() == "default":
            if res.default is not None:
                res.issues.append(Issue("E_SCRIPT_SYNTAX", "error",
                                        f"{where}: 'default' is written twice; the first one wins.",
                                        file=source))
            else:
                res.default = seq
            continue
        sec = parse_sec_code(head)
        if sec is None or sec < 1:
            res.issues.append(Issue("E_SCRIPT_SYNTAX", "error",
                                    f"{where}: can't read the section {head!r}. "
                                    "That line is ignored.", file=source))
            continue
        if sec in res.sections or sec in res.exact:
            res.issues.append(Issue("E_SCRIPT_SYNTAX", "error",
                                    f"{where}: section {head} is written twice; the first one wins.",
                                    file=source, section=sec))
            continue
        res.sections[sec] = seq
    return res


def load_script(path) -> Tuple[Optional[ScriptResult], str]:
    """Load <project>/script.txt if it exists. Returns (result or None, text)."""
    p = Path(path)
    if not p.is_file():
        return None, ""
    try:
        text = p.read_text(encoding="utf-8-sig")
    except Exception:
        text = p.read_text(encoding="utf-8", errors="replace")
    return parse_script(text, p.name), text


SPEAKER_OF = {"K": "koroush", "R": "reza"}
