"""Small helpers shared by several modules."""
from __future__ import annotations

import datetime as _dt
import hashlib
import os
import re
import tempfile
from pathlib import Path
from typing import List, Union


def natural_key(s: str) -> List:
    """Sort key so that '5.10' sorts after '5.6'."""
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s.lower())]


def sha256_file(path: Union[str, Path], chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def unique_path(p: Path) -> Path:
    """Return p, or p with _2, _3 ... added to the stem if it already exists."""
    if not p.exists():
        return p
    i = 2
    while True:
        cand = p.with_name(f"{p.stem}_{i}{p.suffix}")
        if not cand.exists():
            return cand
        i += 1


def stamp_compact() -> str:
    return _dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def stamp_folder() -> str:
    return _dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def utc_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def part_path(p: Union[str, Path]) -> Path:
    """Temporary name used while a file is being written (never matches a real clean/merge name)."""
    p = Path(p)
    return p.with_name(p.name + ".part")


def write_text_atomic(path: Union[str, Path], text: str, newline: str = "\n") -> None:
    """Stage complete text before replacement, with a separate file for each save."""
    path = Path(path)
    descriptor, name = tempfile.mkstemp(prefix="." + path.name + "-", suffix=".part", dir=path.parent)
    os.close(descriptor)
    tmp = Path(name)
    try:
        with tmp.open("w", encoding="utf-8", newline=newline) as stream:
            stream.write(text)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
