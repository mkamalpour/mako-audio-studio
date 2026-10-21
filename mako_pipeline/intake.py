"""Stage 1 - Intake: read raw files, understand names, convert to MP3/WAV, rename into clean/."""
from __future__ import annotations

import csv
import io
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from . import constants as C
from .config import IntakeConfig, SpeakerCfg, validate_intake
from .ffmpeg import FFmpegMissing, convert_file, duration_s, find_ffmpeg
from .names import (build_clean_name, compact_parse, last_integer, parse_clean,
                    parse_sec_code, parse_sections_text, sec_code, smart_parse, pattern_parse, mako_parse)
from .report import Issue, Report
from .util import natural_key, part_path, sha256_file, stamp_compact, unique_path, write_text_atomic

Progress = Optional[Callable[[float, str], None]]


# ----------------------------------------------------------------------------
# Data model
# ----------------------------------------------------------------------------
@dataclass
class PlanItem:
    """One raw file's intake decision."""
    src: Path
    speaker: str
    section: Optional[int]
    line: Optional[int]
    status: str            # "ok" | "dup" | "unreadable" | "unmapped" | "excluded"
    note: str = ""
    folder_speaker: str = ""
    mtime: float = 0.0
    take_n: int = 0
    forced_take: bool = False
    origin: str = "auto"   # "auto" | "override"

    def new_name(self, ext: str = ".mp3") -> str:
        if self.status == "ok":
            return build_clean_name(self.section, self.line, self.speaker, ext)
        if self.status == "dup":
            return (f"S{sec_code(self.section)}_{self.line}_{self.speaker}"
                    f"__take{self.take_n}{self.src.suffix.lower()}")
        return ""


@dataclass
class IntakePlan:
    items: List[PlanItem] = field(default_factory=list)
    issues: List[Issue] = field(default_factory=list)
    found: Dict[str, int] = field(default_factory=dict)
    decisions: List[str] = field(default_factory=list)

    def ok_items(self, speaker: Optional[str] = None) -> List[PlanItem]:
        return [i for i in self.items if i.status == "ok" and (speaker is None or i.speaker == speaker)]


@dataclass
class RenameRow:
    source_file: str
    action: str                      # include | exclude | take
    speaker: str = ""
    section: Optional[int] = None
    line: Optional[int] = None


# ----------------------------------------------------------------------------
# rename_overrides.csv
# ----------------------------------------------------------------------------
def load_rename_overrides(path) -> Tuple[List[RenameRow], List[Issue]]:
    """Read rename_overrides.csv ('#' comments, blank cells inherit). Bad rows are reported."""
    p = Path(path)
    if not p.is_file():
        return [], []
    text = p.read_text(encoding="utf-8-sig", errors="replace")
    body = "\n".join(ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#"))
    rows: List[RenameRow] = []
    issues: List[Issue] = []
    for n, rec in enumerate(csv.reader(io.StringIO(body)), 1):
        if not rec or [c.strip().lower() for c in rec[:2]] == ["source_file", "action"]:
            continue
        rec = [c.strip() for c in rec] + [""] * 5
        src, action, spk, sec, line = rec[:5]
        action = action.lower()
        sp = spk.lower()
        bad = None
        if not src:
            bad = "source_file is empty"
        elif action not in ("include", "exclude", "take"):
            bad = f"action {action!r} must be include, exclude or take"
        elif sp and sp not in C.SPEAKERS:
            bad = f"speaker {spk!r} must be one of {list(C.SPEAKERS)}"
        secn = parse_sec_code(sec) if sec else None
        if not bad and sec and (secn is None or not 1 <= secn <= 999):
            bad = f"section {sec!r} is not a number or letter code"
        if not bad and line and (not line.isdigit() or not 1 <= int(line) <= 9999):
            bad = f"line {line!r} is not a number"
        if bad:
            issues.append(Issue("W_RENAME_OVERRIDE_UNUSED", "warning",
                                f"{p.name} row {n}: {bad}. The row is ignored.", file=src or None))
            continue
        rows.append(RenameRow(src, action, sp, secn, int(line) if line else None))
    return rows, issues


def save_rename_overrides(path, rows: List[RenameRow]) -> None:
    """Write rename_overrides.csv (plain text, editable in Notepad)."""
    out = io.StringIO()
    out.write("# Corrections for intake. action: include | exclude | take. Blank cells inherit.\n")
    w = csv.writer(out, lineterminator="\n")
    w.writerow(["source_file", "action", "speaker", "section", "line"])
    for r in rows:
        w.writerow([r.source_file, r.action, r.speaker,
                    "" if r.section is None else r.section,
                    "" if r.line is None else r.line])
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(out.getvalue(), encoding="utf-8")


# ----------------------------------------------------------------------------
# Planning (pure: no writes)
# ----------------------------------------------------------------------------
def _list_audio(folder: Path) -> List[Path]:
    out = [p for p in folder.iterdir()
           if p.is_file() and not p.name.startswith(".") and p.suffix.lower() in C.AUDIO_EXTS]
    return sorted(out, key=lambda p: natural_key(p.name))


def _auto_parse(sp: str, scfg: SpeakerCfg, files: List[Path]
                ) -> Dict[str, Tuple[str, Optional[int], Optional[int], str]]:
    """Map file name -> (status, section, line, note) for the non-overridden files."""
    res: Dict[str, Tuple[str, Optional[int], Optional[int], str]] = {}
    if scfg.style == 'mako':
        for p in files:
            r = mako_parse(p.stem, sp)
            res[p.name] = ('ok', r[0], r[1], '') if r else (
                'unreadable', None, None, 'expected speaker + section code + dialogue code, e.g. K11 or Raa')
    elif scfg.style == 'custom':
        for p in files:
            r = pattern_parse(p.stem, scfg.pattern)
            res[p.name] = ('ok', r[0], r[1], '') if r else (
                'unreadable', None, None, 'name does not match the custom pattern')
    elif scfg.style == "smart":
        for p in files:
            r = smart_parse(p.stem)
            if p.stem[:1].lower() in ('k', 'r') and p.stem[1:2].isdigit() and p.stem[0].lower() != sp[0]:
                r = None
            res[p.name] = ("ok", r[0], r[1], "") if r else (
                "unreadable", None, None, "can't read section/line from the name")
    elif scfg.style == "compact":
        for p in files:
            r = compact_parse(p.stem, scfg.compact_digits)
            res[p.name] = ("ok", r[0], r[1], "") if r else (
                "unreadable", None, None,
                f"compact style needs only digits and more than {scfg.compact_digits} of them")
    else:  # sequence
        smap = parse_sections_text(scfg.sections_text)  # ValueError handled by the caller
        number_mode = scfg.order_by == C.ORDER_NUMBER
        usable = list(files)
        if number_mode:
            keyed = []
            for p in files:
                k = last_integer(p.stem)
                if k is None:
                    res[p.name] = ("unreadable", None, None, "no number found in the name")
                else:
                    keyed.append((k, p))
            keyed.sort(key=lambda kp: (kp[0], natural_key(kp[1].name)))
            ordered = keyed
        else:
            if scfg.order_by == C.ORDER_DATE:
                srt = sorted(usable, key=lambda p: (p.stat().st_mtime, natural_key(p.name)))
            else:
                srt = sorted(usable, key=lambda p: natural_key(p.name))
            ordered = [(i + 1, p) for i, p in enumerate(srt)]
        for pos, (key, p) in enumerate(ordered, 1):
            if not number_mode:
                key = pos
            m = smap.map(key, pos)
            if m is None:
                res[p.name] = ("unmapped", None, None,
                               f"number/position {key} is not covered by the section list")
            else:
                res[p.name] = ("ok", m[0], m[1], "")
    return res


def plan_intake(cfg: IntakeConfig, rename_rows: Optional[List[RenameRow]] = None) -> IntakePlan:
    """Decide the clean name of every raw file. Pure: reads folders, writes nothing."""
    plan = IntakePlan()
    files: Dict[str, List[Path]] = {}
    for sp in C.SPEAKERS:
        scfg = cfg.speakers.get(sp) or SpeakerCfg()
        if not scfg.folder.strip():
            continue
        folder = Path(scfg.folder)
        try:
            files[sp] = _list_audio(folder)
        except (OSError, NotADirectoryError):
            plan.issues.append(Issue("E_NO_FOLDER", "error",
                                     f"{sp.capitalize()}'s folder does not exist or can't be read: {folder}",
                                     speaker=sp))
            continue
        plan.found[sp] = len(files[sp])

    # -- rename overrides -> per target file
    by_target: Dict[Tuple[str, str], RenameRow] = {}
    for row in rename_rows or []:
        cands = [(fsp, p) for fsp in files for p in files[fsp]
                 if str(p.resolve()).casefold() == row.source_file.casefold()]
        if not cands:
            cands = [(fsp, p) for fsp in files for p in files[fsp] if p.name == row.source_file]
        if not cands:
            cands = [(fsp, p) for fsp in files for p in files[fsp]
                     if p.name.casefold() == row.source_file.casefold()]
        if not cands:
            plan.issues.append(Issue("W_RENAME_OVERRIDE_UNUSED", "warning",
                                     f"rename_overrides.csv: no file named {row.source_file!r} "
                                     "in the raw folders. The row is ignored.", file=row.source_file))
            continue
        pref = [c for c in cands if c[0] == row.speaker]
        if len(pref or cands) != 1:
            plan.issues.append(Issue('W_RENAME_OVERRIDE_UNUSED', 'warning',
                f'{row.source_file}: ambiguous source in both folders; specify its folder speaker.', file=row.source_file))
            continue
        fsp, p = (pref or cands)[0]
        by_target[(fsp, p.name)] = row

    items: List[PlanItem] = []
    for sp in C.SPEAKERS:
        if sp not in files:
            continue
        scfg = cfg.speakers[sp]
        def _auto(p: Path, sp: str = sp) -> bool:
            r = by_target.get((sp, p.name))
            return r is None or (r.action != "exclude" and not (r.section and r.line))
        auto_files = [p for p in files[sp] if _auto(p)]
        try:
            parsed = _auto_parse(sp, scfg, auto_files)
        except ValueError as e:
            plan.issues.append(Issue("E_SECTION_LIST_SYNTAX", "error",
                                     f"{sp.capitalize()}: {e}", speaker=sp))
            continue
        for p in files[sp]:
            row = by_target.get((sp, p.name))
            mt = p.stat().st_mtime
            if row is not None and row.action == "exclude":
                items.append(PlanItem(p, sp, None, None, "excluded",
                                      "excluded in rename_overrides.csv", sp, mt, origin="override"))
                continue
            if row is not None and row.action in ("include", "take") and row.section and row.line:
                tsp = row.speaker or sp
                items.append(PlanItem(p, tsp, row.section, row.line, "ok",
                                      "set in rename_overrides.csv", sp, mt,
                                      forced_take=row.action == "take", origin="override"))
                continue
            if row is not None and row.action == "include":
                plan.issues.append(Issue("W_RENAME_OVERRIDE_UNUSED", "warning",
                                         f"rename_overrides.csv: 'include' for {p.name!r} needs "
                                         "section and line. The row is ignored.", file=p.name))
            status, sec, line, note = parsed[p.name]
            it = PlanItem(p, sp, sec, line, status, note, sp, mt,
                          forced_take=row is not None and row.action == "take")
            if status == "ok" and (sec > 999 or line > 9999):
                it.status, it.section, it.line = "unreadable", None, None
                it.note = "section/line number is too large"
            if it.status == "unreadable":
                plan.issues.append(Issue("W_NAME_UNREADABLE", "warning",
                                         f"{p.name}: {it.note}. Rename it or add a row to "
                                         "rename_overrides.csv.", file=p.name, speaker=sp))
            elif it.status == "unmapped":
                plan.issues.append(Issue("W_UNMAPPED", "warning",
                                         f"{p.name}: {it.note}. The file is skipped.",
                                         file=p.name, speaker=sp))
            items.append(it)

    _resolve_duplicates(items, cfg.dup_policy, plan)
    _add_decisions(items, cfg, plan)
    plan.items = items
    return plan


def _resolve_duplicates(items: List[PlanItem], policy: str, plan: IntakePlan) -> None:
    groups: Dict[Tuple[str, int, int], List[PlanItem]] = {}
    for it in items:
        if it.status == "ok":
            groups.setdefault((it.speaker, it.section, it.line), []).append(it)
    for (sp, sec, line), grp in groups.items():
        if len(grp) < 2:
            continue
        grp.sort(key=lambda i: (i.mtime, natural_key(i.src.name)))
        forced = [i for i in grp if i.forced_take]
        if forced:
            winner, rule = forced[-1], "take row in rename_overrides.csv"
        elif policy == "oldest":
            winner, rule = grp[0], "dup_policy=oldest"
        else:
            winner, rule = grp[-1], "dup_policy=newest"
            if policy == "manual":
                rule = "dup_policy=manual but no 'take' row, so the newest was used"
        winner.note = f"{'oldest' if policy == 'oldest' and not forced else 'newest' if not forced else 'chosen'} of {len(grp)} takes"
        n = 0
        for it in grp:
            if it is winner:
                continue
            n += 1
            it.status, it.take_n = "dup", n
            it.note = f"kept {winner.src.name}"
        plan.issues.append(Issue(
            "W_DUPLICATE_TAKE", "warning",
            f"{len(grp)} takes of {build_clean_name(sec, line, sp, '')}: kept {winner.src.name} "
            f"({rule}); the others are in clean/_duplicates/.", speaker=sp, section=sec))


def _add_decisions(items: List[PlanItem], cfg: IntakeConfig, plan: IntakePlan) -> None:
    for it in items:
        if it.status not in ("ok", "dup"):
            continue
        sty = cfg.speakers[it.folder_speaker].style if it.folder_speaker in cfg.speakers else "smart"
        why = ("rename_overrides.csv" if it.origin == "override"
               else f"naming style '{sty}'")
        key = ("(change: rename_overrides.csv)" if it.origin == "override" else
               f"(override: rename_overrides.csv, or {it.folder_speaker}_style in mako_settings.json)")
        target = it.new_name(".mp3") if it.status == "ok" else f"_duplicates/{it.new_name()}"
        plan.decisions.append(f"{it.src.name} -> {target}  <- {why}  {key}")


def plan_rows(plan: IntakePlan, clean_format: str = "mp3") -> List[List[str]]:
    """Rows for the preview table: speaker | original file | new name | note."""
    labels = {"ok": "OK", "dup": "older take", "unreadable": "CAN'T READ",
              "unmapped": "NOT MAPPED", "excluded": "EXCLUDED"}
    rows = []
    for it in plan.items:
        new = it.new_name("." + clean_format) if it.status == "ok" else (
            "_duplicates/" + it.new_name() if it.status == "dup" else "")
        note = labels[it.status] + (f" - {it.note}" if it.note and it.status != "ok" else
                                    (f" ({it.note})" if it.note else ""))
        rows.append([it.speaker, it.src.name, new, note])
    return rows


def overrides_from_table(table: List[dict], baseline: IntakePlan,
                         existing: List[RenameRow]) -> List[RenameRow]:
    """Turn edited preview-table rows into rename_overrides rows (only the differences)."""
    base: Dict[str, List[PlanItem]] = {}
    for it in baseline.items:
        base.setdefault(it.src.name, []).append(it)
        base.setdefault(str(it.src.resolve()), []).append(it)
    out: List[RenameRow] = [r for r in existing if r.action == "take"]
    for row in table:
        name = str(row.get("source_file", "")).strip()
        cands = base.get(name)
        if not name or not cands:
            continue
        spk = str(row.get("speaker", "")).strip().lower()
        b = next((c for c in cands if c.speaker == spk), cands[0])
        if row.get("skip"):
            out.append(RenameRow(name, "exclude", b.folder_speaker))
            continue
        sec, line = row.get("section"), row.get("line")
        try:
            sec_i = int(float(sec)) if sec not in (None, "") else None
            line_i = int(float(line)) if line not in (None, "") else None
        except (TypeError, ValueError):
            continue
        if sec_i is None and line_i is None:
            continue
        if spk not in C.SPEAKERS or sec_i is None or line_i is None:
            raise ValueError(f'{name}: enter a valid speaker, section and line, or tick skip.')
        if not 1 <= sec_i <= 999 or not 1 <= line_i <= 9999:
            raise ValueError(f'{name}: section must be 1..999 and line 1..9999.')
        same = (b.status in ("ok", "dup") and (b.speaker, b.section, b.line) == (spk, sec_i, line_i))
        if not same:
            out.append(RenameRow(name, "include", spk, sec_i, line_i))
    return out


# ----------------------------------------------------------------------------
# Running
# ----------------------------------------------------------------------------
def _archive_speaker(clean: Path, sp: str, stamp: str, rep: Report) -> int:
    """Move (never delete) this speaker's existing clean files to _replaced/<stamp>/."""
    moved = 0
    dest = clean / "_replaced" / stamp
    names: List[str] = []
    for p in sorted(clean.iterdir()):
        if not p.is_file():
            continue
        r = parse_clean(p.stem)
        if r and r[2] == sp:
            dest.mkdir(parents=True, exist_ok=True)
            target = unique_path(dest / p.name)
            shutil.move(str(p), str(target))
            names.append(f"{p.name} -> {target.name}")
            moved += 1
    if moved:
        log = dest / "MOVED.txt"       # nothing is ever lost: this lists what was parked and why
        old = log.read_text(encoding="utf-8") if log.exists() else ""
        write_text_atomic(log, old + f"{sp}: these clean files were replaced by a new intake run\n"
                          + "".join(f"  {n}\n" for n in names))
        rep.log(f"   {moved} previous {sp} clean file(s) moved to clean/_replaced/{stamp}/")
    return moved


def _copy_verified(src: Path, dst: Path) -> Tuple[bool, str]:
    """Byte-for-byte copy, verified by size and SHA-256, under a temporary name until it is proven good."""
    tmp = part_path(dst)
    shutil.copy2(src, tmp)
    if tmp.stat().st_size != src.stat().st_size:
        tmp.unlink(missing_ok=True)
        return False, "size differs after copy"
    if sha256_file(tmp) != sha256_file(src):
        tmp.unlink(missing_ok=True)
        return False, "SHA-256 differs after copy"
    os.replace(tmp, dst)
    return True, ""


def _do_ok_item(it: PlanItem, clean: Path, cfg: IntakeConfig, rep: Report,
                cnt: Dict[str, int], stamp: str) -> None:
    fmt = cfg.clean_format
    ext = "." + fmt
    dst = clean / it.new_name(ext)
    import tempfile
    archived = []
    before = dict(cnt)
    output_count = len(rep.outputs)
    try:
        with tempfile.TemporaryDirectory(prefix='.intake-', dir=clean) as stage:
            staged = Path(stage)
            _write_ok_item(it, staged, cfg, rep, cnt)
            if cnt['copied'] + cnt['converted'] == before['copied'] + before['converted']:
                return
            candidate = staged / it.new_name(ext)
            for old in list(clean.iterdir()):
                if (old.is_file() and old.suffix.lower() in C.CLEAN_EXTS and
                        parse_clean(old.stem) == (it.section, it.line, it.speaker)):
                    d = clean / '_replaced' / stamp
                    d.mkdir(parents=True, exist_ok=True)
                    parked = unique_path(d / old.name)
                    shutil.move(str(old), str(parked))
                    archived.append((old, parked))
            os.replace(candidate, dst)
            rep.outputs[-1] = str(dst)
    except OSError as e:
        for old, parked in reversed(archived):
            if not old.exists() and parked.exists():
                try:
                    shutil.move(str(parked), str(old))
                except OSError:
                    rep.log(f'Previous recording remains recoverable at {parked}')
        cnt.update(before)
        cnt['failed'] += 1
        del rep.outputs[output_count:]
        rep.add('E_CONVERT', f'{it.src.name}: unable to install the prepared recording ({e}).', file=it.src.name)
        rep.exception(f'intake {it.src.name}', e)


def _write_ok_item(it, clean, cfg, rep, cnt):
    fmt = cfg.clean_format
    ext = '.' + fmt
    dst = clean / it.new_name(ext)
    name = it.src.name
    try:
        if it.src.suffix.lower() == ext:
            try:
                duration_s(it.src)
            except RuntimeError as e:
                rep.add("E_DECODE", f"{name}: {e}. The file is skipped.", file=name, speaker=it.speaker)
                rep.log(f"   FAILED  {name}  (can't be decoded)")
                cnt["failed"] += 1
                return
            good, why = _copy_verified(it.src, dst)
            if not good:
                rep.add("E_COPY_SIZE", f"{name}: {why}. The copy was removed.", file=name, speaker=it.speaker)
                dst.unlink(missing_ok=True)
                cnt["failed"] += 1
                return
            cnt["copied"] += 1
            rep.log(f"   OK  {name} -> {dst.name}  (copied unchanged)")
        else:
            good, err = convert_file(it.src, dst, fmt, cfg.mp3_bitrate)
            if not good:
                dst.unlink(missing_ok=True)
                rep.add("E_CONVERT", f"{name}: conversion failed [{err}]. The file is skipped.",
                        file=name, speaker=it.speaker)
                rep.log(f"   FAILED  {name}  (conversion failed)")
                cnt["failed"] += 1
                return
            try:
                d_src, d_dst = duration_s(it.src), duration_s(dst)
            except RuntimeError as e:
                dst.unlink(missing_ok=True)
                rep.add("E_DECODE", f"{name}: converted file can't be read back ({e}).",
                        file=name, speaker=it.speaker)
                cnt["failed"] += 1
                return
            if abs(d_src - d_dst) > cfg.verify_tolerance_s:
                dst.unlink(missing_ok=True)  # only the tool's own failed output
                rep.add("E_VERIFY_LENGTH",
                        f"{name}: length changed by {abs(d_src - d_dst):.2f} s in conversion "
                        f"(allowed {cfg.verify_tolerance_s} s). The output was removed.",
                        file=name, speaker=it.speaker)
                cnt["failed"] += 1
                return
            cnt["converted"] += 1
            rep.log(f"   OK  {name} -> {dst.name}  (converted to {fmt.upper()})")
        rep.outputs.append(str(dst))
    except Exception as e:  # one bad file never stops the run (I8)
        rep.add("E_CONVERT", f"{name}: unexpected problem ({e}). The file is skipped.",
                file=name, speaker=it.speaker)
        rep.exception(f"intake {name}", e)
        cnt["failed"] += 1


def _do_dup_item(it: PlanItem, clean: Path, rep: Report, cnt: Dict[str, int]) -> None:
    ddir = clean / "_duplicates"
    ddir.mkdir(parents=True, exist_ok=True)
    stem = f"S{sec_code(it.section)}_{it.line}_{it.speaker}__take"
    try:
        size, digest = it.src.stat().st_size, None
        for ex in ddir.glob(stem + "*" + it.src.suffix.lower()):  # already stored by an earlier run
            if ex.stat().st_size == size:
                digest = digest or sha256_file(it.src)
                if sha256_file(ex) == digest:
                    cnt["duplicates"] += 1
                    rep.log(f"   older take  {it.src.name} -> clean/_duplicates/{ex.name}  (already there)")
                    return
        n = it.take_n
        while (ddir / f"{stem}{n}{it.src.suffix.lower()}").exists():
            n += 1
        dst = ddir / f"{stem}{n}{it.src.suffix.lower()}"
        tmp = part_path(dst)
        shutil.copy2(it.src, tmp)
        os.replace(tmp, dst)
        cnt["duplicates"] += 1
        rep.log(f"   older take  {it.src.name} -> clean/_duplicates/{dst.name}")
        rep.outputs.append(str(dst))
    except Exception as e:
        rep.add("E_COPY_SIZE", f"{it.src.name}: could not store the older take ({e}).",
                file=it.src.name, speaker=it.speaker)
        rep.exception(f"intake dup {it.src.name}", e)
        cnt["failed"] += 1


def run_intake(cfg: IntakeConfig, plan: Optional[IntakePlan] = None,
               rename_rows: Optional[List[RenameRow]] = None,
               progress: Progress = None) -> Report:
    """Copy/convert/rename the planned files into <project>/clean/."""
    rep = Report(stage="intake")
    try:
        find_ffmpeg()
    except FFmpegMissing as e:
        rep.fail("E_FFMPEG_MISSING", str(e))
        return rep
    if not cfg.project.strip():
        rep.fail("E_NO_FOLDER", "Choose a project folder first.")
        return rep
    try:
        validate_intake(cfg)
    except ValueError as e:
        rep.fail("E_NO_FOLDER", str(e))
        return rep
    plan = plan or plan_intake(cfg, rename_rows)
    rep.issues += plan.issues
    rep.decisions += plan.decisions
    if not plan.found:
        rep.fail("E_NO_FOLDER", "No raw folder was given (or none exists). Choose Koroush's and/or Reza's folder.")
        return rep
    clean = Path(cfg.project) / "clean"
    for sc in cfg.speakers.values():
        if sc.folder and (Path(sc.folder).resolve() == clean.resolve() or
                          clean.resolve() in Path(sc.folder).resolve().parents):
            rep.fail('E_NO_FOLDER', "Raw folders must be outside the project's clean/ folder.")
            return rep
    clean.mkdir(parents=True, exist_ok=True)
    stamp = stamp_compact()
    total = max(1, len([i for i in plan.items if i.status in ("ok", "dup")]))
    done = 0
    counts: Dict[str, Dict[str, int]] = {}
    for sp in C.SPEAKERS:
        mine = [i for i in plan.items if i.folder_speaker == sp]
        if sp not in plan.found:
            continue
        cnt = {"found": plan.found[sp], "processed": 0, "copied": 0, "converted": 0,
               "duplicates": 0, "skipped": 0, "failed": 0}
        counts[sp] = cnt
        todo = [i for i in mine if i.status == "ok"]
        rep.log(f"[{sp.upper()}] {plan.found[sp]} files found -> {len(todo)} to process")
        for it in mine:
            if it.status in ("unreadable", "unmapped", "excluded"):
                cnt["skipped"] += 1
                rep.log(f"   SKIPPED  {it.src.name}  ({it.note})")
    add_mode = cfg.intake_mode == "add"
    prepared_coordinates = {parse_clean(p.stem) for p in clean.iterdir()
                            if p.is_file() and p.suffix.lower() in C.CLEAN_EXTS}
    if add_mode:
        rep.log("Add mode: existing clean files are kept; only lines that are not there yet are written.")
    for it in plan.items:
        cnt = counts.get(it.folder_speaker)
        if cnt is None or it.status not in ("ok", "dup"):
            continue
        if progress:
            progress(done / total, f"{it.src.name}")
        done += 1
        if it.status == "dup":
            _do_dup_item(it, clean, rep, cnt)
        elif add_mode and (it.section, it.line, it.speaker) in prepared_coordinates:
            cnt["skipped"] += 1
            rep.log(f"   SKIPPED  {it.src.name}  ({it.new_name('.' + cfg.clean_format)} is already in clean/; "
                    "intake_mode=add keeps it)")
        else:
            cnt["processed"] += 1
            _do_ok_item(it, clean, cfg, rep, cnt, stamp)
    rep.counts = counts
    found = sum(c["found"] for c in counts.values())
    rep.lines.insert(0, f"Found {found}, processed {sum(c['copied'] + c['converted'] for c in counts.values())}, "
                        f"older takes {sum(c['duplicates'] for c in counts.values())}, "
                        f"skipped {sum(c['skipped'] for c in counts.values())}, "
                        f"failed {sum(c['failed'] for c in counts.values())}")
    if not plan.ok_items():
        rep.fail("E_NO_READABLE_AUDIO",
                 "No file could be read. Check the naming style and the section list, then press Preview.")
    elif not any(c["copied"] + c["converted"] for c in counts.values()) and not (add_mode and any(c["skipped"] for c in counts.values())):
        rep.fail("E_NO_READABLE_AUDIO", "Every file failed to process. See the warnings above.")
    for sp, c in counts.items():
        rep.log(f"[{sp.upper()}] copied {c['copied']}, converted {c['converted']}, "
                f"older takes {c['duplicates']}, skipped {c['skipped']}, failed {c['failed']}")
    if progress:
        progress(1.0, "done")
    return rep
