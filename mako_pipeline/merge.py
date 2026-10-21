"""Stage 2 - Merge: order lines, conservative processing, export (spec section 9)."""
from __future__ import annotations

import csv
import io
import json
import random
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

from . import constants as C
from .config import MergeConfig, asdict
from . import pacing
from .dsp import (apply_tilt, clip_levels, find_room_tone, longest_silence_ms, make_bed,
                  measure_lufs, peak_db, speech_edges, tilt_db, tone_spectrum, trim_clip)
from .ffmpeg import (FFmpegMissing, decode_audio, encode_audio, encode_raw, find_ffmpeg)
from .names import build_clean_name, parse_clean, parse_sec_code, sec_code, speaker_from_token
from .overrides import Overrides, load_overrides, unused_issues, parse_overrides
from .report import Issue, Report
from .script import SPEAKER_OF, ScriptResult, load_script, parse_script
from .util import sha256_file, stamp_folder, utc_iso, write_text_atomic, unique_path

Progress = Optional[Callable[[float, str], None]]
Scan = Dict[int, Dict[str, Dict[int, Path]]]


@dataclass
class OrderItem:
    section: int
    speaker: str
    line: int
    path: Path
    deviates: bool = False


@dataclass
class Clip:
    speaker: str
    line: int
    path: Path
    x: np.ndarray
    lead: int
    tail: int
    tone: Optional[np.ndarray]
    section: int = 0
    pause_ms: Optional[float] = None
    pause_src: str = ""
    extra_gain: float = 0.0
    gain_src: str = ""
    gain_db: float = 0.0
    trim_on: bool = True
    trim_src: str = ""
    tone_on: bool = True
    tone_src: str = ""
    head_e: float = 0.0        # cut-tight measures (see dsp.clip_levels)
    tail_e: float = 0.0
    speech_rms: float = 0.0
    floor: float = 0.0         # noise floor, used for comfort noise when there is no real room tone
    speech_s: float = 0.0
    peak: float = 0.0
    longest_silence_ms: float = 0.0
    flags: List[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return f"S{sec_code(self.section)}_{self.line}_{self.speaker}"


@dataclass
class Placement:
    clip: Clip
    start: int
    insert: int
    target_ms: float
    gap_ms: float
    bed_tone: Optional[np.ndarray]
    floor: float = 0.0
    kind: str = "pause"
    key: Tuple = ()


@dataclass
class SectionPlan:
    section: int
    places: List[Placement]
    length: int


@dataclass
class MergeResult:
    report: Report
    out_dir: Optional[Path] = None
    rows: List[dict] = field(default_factory=list)
    plan_rows: List[List[str]] = field(default_factory=list)
    qc_rows: List[dict] = field(default_factory=list)
    preview_audio: Optional[Tuple[int, np.ndarray]] = None


# ----------------------------------------------------------------------------
# Scan, ordering, validation
# ----------------------------------------------------------------------------
def scan_clean(clean_dir) -> Tuple[Scan, List[Path]]:
    """Top-level *.mp3/*.wav only. If both exist for one line the .wav wins."""
    scan: Scan = {}
    ignored: List[Path] = []
    for p in sorted(Path(clean_dir).iterdir()):
        if not p.is_file() or p.suffix.lower() not in C.CLEAN_EXTS:
            continue
        r = parse_clean(p.stem)
        if not r or r[2] not in C.SPEAKERS:
            ignored.append(p)
            continue
        sec, line, sp = r
        slot = scan.setdefault(sec, {}).setdefault(sp, {})
        if line not in slot or p.suffix.lower() == ".wav":
            slot[line] = p
    return scan, ignored


def parse_starter_exceptions(text: str) -> Dict[int, str]:
    out: Dict[int, str] = {}
    for part in [p for p in re.split(r"[,;\n]+", text or "") if p.strip()]:
        m = re.fullmatch(r"\s*([0-9a-zA-Z]{1,3})\s*:\s*([a-zA-Z]+)\s*", part)
        sec = parse_sec_code(m.group(1)) if m else None
        sp = speaker_from_token(m.group(2)) if m else None
        if sec is None or sp is None:
            raise ValueError("Can't read the starter exceptions. Use e.g. \"3:reza, 7:koroush\".")
        out[sec] = sp
    return out


def strict_order(files: Dict[str, Dict[int, Path]], starter: str) -> List[Tuple[str, int]]:
    other = [s for s in C.SPEAKERS if s != starter][0]
    lines = sorted(set().union(*[set(files.get(s, {})) for s in C.SPEAKERS]))
    return [(sp, n) for n in lines for sp in (starter, other) if n in files.get(sp, {})]


def _extras(sec, files, used, report_issues):
    rest = sorted(((n, C.SPEAKERS.index(sp), sp) for sp in files for n in files[sp]
                   if (sp, n) not in used))
    out = []
    for n, _, sp in rest:
        report_issues.append(Issue("W_EXTRA_LINE", "warning",
                                   f"Section {sec}: {files[sp][n].name} has no slot in the script. "
                                   "It is placed at the end of the section.", file=files[sp][n].name,
                                   section=sec, speaker=sp))
        out.append((sp, n))
    return out


def order_from_script(sec: int, seq: List[str], files, issues: List[Issue],
                      cyclic: bool = False) -> List[Tuple[str, int]]:
    cnt = {s: 0 for s in C.SPEAKERS}
    maxn = {s: max(files.get(s, {}) or [0]) for s in C.SPEAKERS}
    out: List[Tuple[str, int]] = []
    used = set()
    i = stall = 0
    while True:
        if cyclic:
            if all(cnt[s] >= maxn[s] for s in C.SPEAKERS) or stall > len(seq):
                break
        elif i >= len(seq):
            break
        sp = SPEAKER_OF[seq[i % len(seq)]]
        i += 1
        if cyclic and cnt[sp] >= maxn[sp]:
            stall += 1
            continue
        stall = 0
        cnt[sp] += 1
        n = cnt[sp]
        if n in files.get(sp, {}):
            out.append((sp, n))
            used.add((sp, n))
        elif not cyclic:
            issues.append(Issue("W_MISSING_LINE", "warning",
                                f"Section {sec} expects {build_clean_name(sec, n, sp)} (from script.txt) "
                                "but it is not in clean/. The slot is skipped.",
                                section=sec, speaker=sp))
    return out + _extras(sec, files, used, issues)


def build_order(scan: Scan, cfg: MergeConfig, script: Optional[ScriptResult],
                report: Report) -> Dict[int, List[OrderItem]]:
    """Final order of every section: script line > starter_exceptions > default pattern > starter."""
    exc = parse_starter_exceptions(cfg.starter_exceptions)
    result: Dict[int, List[OrderItem]] = {}
    if script:
        report.issues += script.issues
        for sec in set(script.sections) | set(script.exact):
            if sec not in scan:
                report.add("W_MISSING_LINE", f"script.txt lists section {sec} but clean/ has no files for it.",
                           section=sec)
    for sec in sorted(scan):
        files = scan[sec]
        starter = exc.get(sec, cfg.starter)
        strict = strict_order(files, starter)
        if script and sec in script.exact:
            pairs = []
            for sp, line in script.exact[sec]:
                if line in files.get(sp, {}):
                    pairs.append((sp, line))
                else:
                    report.add('W_MISSING_LINE', f'Section {sec}: requested {sp} line {line} is missing.', section=sec, speaker=sp)
            for sp in files:
                for line in files[sp]:
                    if (sp, line) not in pairs:
                        report.add('W_EXTRA_LINE', f'{files[sp][line].name}: excluded by the explicit section arrangement.', section=sec, speaker=sp)
            why = 'explicit clip arrangement in script.txt'
        elif script and sec in script.sections:
            pairs = order_from_script(sec, script.sections[sec], files, report.issues)
            why = f"script.txt line \"{sec_code(sec)}:\"  (override: script.txt)"
        elif sec in exc:
            pairs, why = strict, f"starter_exceptions {sec}:{starter}  (override: starter_exceptions)"
        elif script and script.default:
            pairs = order_from_script(sec, script.default, files, report.issues, cyclic=True)
            why = "script.txt default pattern  (override: add a line for this section)"
        else:
            pairs, why = strict, f"starter '{starter}', strict alternation  (override: starter / starter_exceptions / script.txt)"
        items = [OrderItem(sec, sp, n, files[sp][n], i >= len(strict) or strict[i] != (sp, n))
                 for i, (sp, n) in enumerate(pairs)]
        result[sec] = items
        letters = " ".join(("K" if i.speaker == "koroush" else "R") + str(i.line) for i in items)
        report.decide(f"Section {sec} order: {letters}  <- {why}")
    return result


def validate_scan(scan: Scan, ignored: List[Path], report: Report) -> None:
    for p in ignored:
        report.add("W_IGNORED_FILE", f"{p.name} in clean/ is not a standard clean name. It is ignored.",
                   file=p.name)
    for sec in sorted(scan):
        counts = {}
        for sp in C.SPEAKERS:
            lines = scan[sec].get(sp, {})
            counts[sp] = len(lines)
            if lines:
                missing = [n for n in range(1, max(lines) + 1) if n not in lines]
                if missing:
                    report.add("W_MISSING_LINE", f"Section {sec}: {sp} is missing line(s) "
                               f"{', '.join(map(str, missing))} (has 1..{max(lines)} with holes).",
                               section=sec, speaker=sp)
            elif any(scan[sec].get(o) for o in C.SPEAKERS if o != sp):
                report.add("W_NO_AUDIO_SPEAKER", f"Section {sec}: {sp} has no lines but the other speaker does.",
                           section=sec, speaker=sp)
        if abs(counts["koroush"] - counts["reza"]) > 1:
            report.add("W_COUNT_MISMATCH", f"Section {sec}: koroush has {counts['koroush']} lines, "
                       f"reza has {counts['reza']}.", section=sec)


def preview_order(project, cfg: MergeConfig) -> Tuple[Dict[int, List[OrderItem]], Report]:
    """Order preview without decoding any audio (no writes)."""
    rep = Report(stage="merge-preview")
    clean = Path(project) / "clean"
    if not clean.is_dir():
        rep.fail("E_NO_CLEAN", f"There is no clean folder yet: {clean}. Run Intake first.")
        return {}, rep
    scan, ignored = scan_clean(clean)
    script, _ = load_script(Path(project) / C.SCRIPT_FILE)
    validate_scan(scan, ignored, rep)
    return build_order(scan, cfg, script, rep), rep


# ----------------------------------------------------------------------------
# Loading clips, gain, placement
# ----------------------------------------------------------------------------
def load_clip(it: OrderItem, cfg: MergeConfig, ov: Overrides, rep: Report) -> Optional[Clip]:
    sr = cfg.sample_rate
    try:
        x = decode_audio(it.path, sr)
    except FFmpegMissing:
        raise
    except RuntimeError as e:
        rep.add("W_SKIPPED_FILE", f"{it.path.name}: {e}. The file is skipped.", file=it.path.name,
                section=it.section, speaker=it.speaker)
        return None
    if len(x) < C.MIN_CLIP_S * sr:
        rep.add("W_SKIPPED_FILE", f"{it.path.name}: shorter than 0.1 s. The file is skipped.",
                file=it.path.name, section=it.section, speaker=it.speaker)
        return None
    pk = float(np.max(np.abs(x)))
    if pk >= C.CLIPPED_PEAK:
        rep.add("W_CLIPPED_SOURCE", f"{it.path.name}: the recording is clipped (peak {pk:.3f}).",
                file=it.path.name, section=it.section, speaker=it.speaker)
    if pk < C.NEAR_SILENT_PEAK:
        rep.add("W_NEAR_SILENT", f"{it.path.name}: the recording is nearly silent (peak {pk:.4f}).",
                file=it.path.name, section=it.section, speaker=it.speaker)
    c = Clip(it.speaker, it.line, it.path, x, 0, 0, None, it.section)
    v, s = ov.lookup(it.section, it.line, it.speaker, "trim")
    c.trim_on, c.trim_src = (cfg.trim, "trim=" + str(cfg.trim).lower()) if v is None else (v, f"overrides.csv ({s})")
    v, s = ov.lookup(it.section, it.line, it.speaker, "room_tone")
    c.tone_on, c.tone_src = (cfg.room_tone, "room_tone=" + str(cfg.room_tone).lower()) if v is None else (v, f"overrides.csv ({s})")
    v, s = ov.lookup(it.section, it.line, it.speaker, "pause_before_ms")
    if v is not None:
        c.pause_ms, c.pause_src = float(v), f"overrides.csv ({s})"
    v, s = ov.lookup(it.section, it.line, it.speaker, "gain_db")
    if v is not None:
        c.extra_gain, c.gain_src = float(v), f"overrides.csv ({s})"
    p = cfg.dsp()
    c.tone = find_room_tone(x, sr, p) if c.tone_on else None  # on the untrimmed clip
    c.head_e, c.tail_e, c.speech_rms, floor = clip_levels(x, sr)
    c.floor = floor if (c.tone_on and cfg.comfort_noise) else 0.0
    c.peak = pk
    edges = speech_edges(x, sr, p)
    c.speech_s = (edges[1] - edges[0]) / sr if edges else 0.0
    c.longest_silence_ms = longest_silence_ms(x, sr, edges, p)
    c.x, c.lead, c.tail = trim_clip(x, sr, c.trim_on, p)
    if c.trim_on and edges is not None and (c.speech_s < 0.1 or len(c.x) < 0.1 * len(x)):
        rep.add("W_TRIM_SUSPICIOUS", f"{it.path.name}: trimming would have removed almost the whole "
                "recording, so it was kept untrimmed. Check the file.", file=it.path.name,
                section=it.section, speaker=it.speaker)
        c.x, c.lead, c.tail = trim_clip(x, sr, False, p)
        c.trim_on, c.trim_src = False, "kept untrimmed: trimming would have removed almost everything"
        c.flags.append("trim_suspicious")
    _line_checks(c, it, cfg, rep)
    return c


def _line_checks(c: Clip, it: OrderItem, cfg: MergeConfig, rep: Report) -> None:
    """Per-recording quality checks. Warnings only: they never change the audio."""
    kw = dict(file=it.path.name, section=it.section, speaker=it.speaker)
    if c.peak >= C.CLIPPED_PEAK:
        c.flags.append("clipped")
    if c.peak < C.NEAR_SILENT_PEAK:
        c.flags.append("near_silent")
        return
    if c.longest_silence_ms > cfg.qc_long_silence_s * 1000:
        c.flags.append("long_silence")
        rep.add("W_LONG_SILENCE", f"{it.path.name}: {c.longest_silence_ms / 1000:.1f} s of silence in the "
                "middle of the line. Check for a stop, a mistake or a long breath.", **kw)
    if 0 < c.speech_s < cfg.qc_short_line_s:
        c.flags.append("short_line")
        rep.add("W_SHORT_LINE", f"{it.path.name}: only {c.speech_s:.2f} s of speech. Check the line number "
                "and that nothing was cut off.", **kw)
    if c.speech_s > cfg.qc_long_line_s:
        c.flags.append("long_line")
        rep.add("W_LONG_LINE", f"{it.path.name}: {c.speech_s:.0f} s of speech. Check that this is one line "
                "and not several recorded together.", **kw)


def level_outliers(clips: List[Clip], cfg: MergeConfig, rep: Report) -> None:
    """A line far louder/quieter than the speaker's typical line (gain is one constant per speaker)."""
    for sp in C.SPEAKERS:
        mine = [c for c in clips if c.speaker == sp and c.speech_rms > 0]
        if len(mine) < 4:
            continue
        lev = {id(c): 20 * np.log10(c.speech_rms) for c in mine}
        med = float(np.median(list(lev.values())))
        for c in mine:
            d = lev[id(c)] - med
            if abs(d) > cfg.qc_level_outlier_db:
                c.flags.append("level_outlier")
                rep.add("W_LEVEL_OUTLIER", f"{c.path.name}: {abs(d):.0f} dB {'louder' if d > 0 else 'quieter'} than "
                        f"{sp}'s typical line. If that is a mistake, set gain_db for this line in overrides.csv.",
                        file=c.path.name, section=c.section, speaker=sp)


def match_tone(clips: List[Clip], cfg: MergeConfig, rep: Report) -> Dict[str, float]:
    """Optional: nudge each speaker's brightness toward the middle with a gentle shelf (one value per speaker)."""
    sr = cfg.sample_rate
    tilts: Dict[str, float] = {}
    for sp in C.SPEAKERS:
        mine = [c for c in clips if c.speaker == sp]
        tot, blocks = None, 0
        for c in mine:
            ps, b = tone_spectrum(c.x, sr)
            if b:
                tot = ps if tot is None else tot + ps
                blocks += b
        t = tilt_db(tot, blocks, sr) if tot is not None else None
        if t is not None:
            tilts[sp] = t
    if len(tilts) < 2:
        rep.log("Tone match: needs speech from both speakers; nothing changed.")
        return {}
    target = float(np.median(list(tilts.values())))
    applied: Dict[str, float] = {}
    for sp, t in tilts.items():
        d = float(np.clip(target - t, -cfg.max_tone_db, cfg.max_tone_db))
        applied[sp] = d
        rep.log(f"Tone {sp}: brightness {t:+.1f} dB, target {target:+.1f} dB -> shelf {d:+.1f} dB")
        rep.decide(f"{sp}: tone shelf {d:+.2f} dB  <- match_tone=true, max_tone_db={cfg.max_tone_db:g}  "
                   "(override: match_tone, max_tone_db)")
        if abs(d) < 0.3:
            continue
        for c in clips:
            if c.speaker == sp:
                c.x = apply_tilt(c.x, sr, d)
                if c.tone is not None:
                    c.tone = apply_tilt(c.tone, sr, d)
    return applied


def compute_gains(clips: List[Clip], cfg: MergeConfig, rep: Report) -> Dict[str, float]:
    """One constant gain per speaker (I7), plus optional per-line extra gain."""
    sr = cfg.sample_rate
    mine_by: Dict[str, List[Clip]] = {sp: [c for c in clips if c.speaker == sp] for sp in C.SPEAKERS}
    measured: Dict[str, Optional[float]] = {}
    engines: Dict[str, str] = {}
    if cfg.match_loudness:
        for sp, mine in mine_by.items():
            if mine:
                measured[sp], engines[sp] = measure_lufs(np.concatenate([c.x for c in mine]), sr)
    known = [v for v in measured.values() if v is not None]
    if cfg.loudness_mode == "match" and known:
        target = min(max(known), C.MATCH_CEILING_LUFS)
        target_why = f"loudness_mode=match (louder speaker, never above {C.MATCH_CEILING_LUFS:g} LUFS)"
    else:
        target = cfg.target_lufs
        target_why = f"target_lufs={cfg.target_lufs:g}"
    gains: Dict[str, float] = {}
    for sp in C.SPEAKERS:
        mine = mine_by[sp]
        if not mine:
            continue
        g, m, engine = 0.0, measured.get(sp), engines.get(sp, "")
        if cfg.match_loudness:
            if m is None:
                rep.add("W_LOUDNESS_UNMEASURED", f"{sp}: loudness could not be measured ({engine}); "
                        "no gain applied.", speaker=sp)
            else:
                g = target - m
                limit = cfg.peak_ceiling_dbfs - max(peak_db(c.x) for c in mine)
                if g > limit:
                    rep.add("W_GAIN_LIMITED", f"{sp}: gain limited to {limit:+.1f} dB so peaks stay under "
                            f"{cfg.peak_ceiling_dbfs} dBFS.", speaker=sp)
                    g = limit
                if abs(g) > cfg.max_gain_db:
                    rep.add("W_GAIN_LIMITED", f"{sp}: {g:+.1f} dB would be needed; limited to "
                            f"{cfg.max_gain_db:g} dB (max_gain_db).", speaker=sp)
                g = float(np.clip(g, -cfg.max_gain_db, cfg.max_gain_db))
                if abs(g) >= C.BIG_GAIN_DB:
                    rep.add("W_BIG_GAIN", f"{sp}: a gain of {g:+.1f} dB is needed to reach the target. "
                            "That also raises the background noise: check the recording level.", speaker=sp)
        gains[sp] = g
        txt = "off" if not cfg.match_loudness else (f"measured {m:.1f} LUFS [{engine}]"
                                                   if m is not None else "not measured")
        rep.log(f"Loudness {sp}: {txt} -> gain {g:+.2f} dB (target {target:g} LUFS)")
        for c in mine:
            tot = g + c.extra_gain
            if c.extra_gain > 0:
                lim = cfg.peak_ceiling_dbfs - peak_db(c.x)
                if tot > lim:
                    tot = max(g, lim)
                    rep.add("W_GAIN_LIMITED", f"{c.name}: extra gain limited so the peak stays under "
                            f"{cfg.peak_ceiling_dbfs} dBFS.", file=c.path.name, section=c.section, speaker=sp)
            c.gain_db = tot
            f = 10 ** (tot / 20)
            c.x = (c.x * f).astype(np.float32)
            c.floor *= f
            c.speech_rms *= f
            if c.tone is not None:
                c.tone = (c.tone * f).astype(np.float32)
            src = (f"match_loudness to {target:g} LUFS ({target_why})" if cfg.match_loudness
                   else "match_loudness off")
            extra = f" + {c.extra_gain:+.2f} dB from {c.gain_src}" if c.gain_src else ""
            rep.decide(f"{c.name}: gain {tot:+.2f} dB  <- {src}{extra}  "
                       "(override: overrides.csv gain_db, target_lufs, loudness_mode)")
    return gains


def place_section(sec: int, clips: List[Clip], cfg: MergeConfig, rep: Optional[Report] = None,
                  state: Optional[pacing.PacingState] = None, profile: Optional[str] = None) -> SectionPlan:
    """Sequential placement. Pauses are exact, seeded and may be negative (talk-over).

    pacing=natural: every pause is drawn by pacing.decide (state carries across sections).
    pacing=fixed:   gap_diff_ms / gap_same_ms with +/- variation_pct.
    A per-line pause_before_ms in overrides.csv always wins and is exact.
    """
    sr = cfg.sample_rate
    natural = cfg.pacing == "natural"
    if natural:
        state = state or pacing.PacingState()
        profile = profile or pacing.pick_profile(clips, sr, cfg.pacing_profile)
    places: List[Placement] = []
    end = 0
    for i, c in enumerate(clips):
        if i == 0:
            places.append(Placement(c, 0, 0, 0.0, 0.0, None))
            end = len(c.x)
            continue
        prev = clips[i - 1]
        kind = "pause"
        if natural:
            tr = pacing.decide(prev, c, cfg, profile, state, random.Random(f"{cfg.seed}-n-{sec}-{i}"), sr)
            target, how, kind = tr.gap_ms, tr.how, tr.kind
        else:
            base = cfg.gap_same_ms if prev.speaker == c.speaker else cfg.gap_diff_ms
            r = random.Random(f"{cfg.seed}-{sec}-{i}").uniform(-1, 1)
            target = max(0.0, base * (1 + cfg.variation_pct / 100 * r))
            how = (f"fixed pacing: gap_{'same' if prev.speaker == c.speaker else 'diff'}_ms={base:g}, "
                   f"variation {cfg.variation_pct:g}%, seed {cfg.seed}")
        if c.pause_ms is not None:
            target, how = c.pause_ms, c.pause_src
            kind = "talk-over" if target < 0 else "override"
        insert = int(round(sr * target / 1000)) - prev.tail - c.lead
        start = max(places[-1].start, end + insert)       # never earlier than the previous line starts
        insert = start - end
        gap = (insert + prev.tail + c.lead) * 1000.0 / sr
        tone = (prev.tone if prev.tone is not None else c.tone) if c.tone_on else None
        floor = (prev.floor or c.floor) if c.tone_on else 0.0
        places.append(Placement(c, start, insert, target, gap, tone, floor, kind, (cfg.seed, sec, i)))
        end = max(end, start + len(c.x))
        if rep is not None:
            what = "talk-over" if gap < 0 else "pause"
            rep.decide(f"{c.name}: {what} {abs(gap):.0f} ms  <- {how}  "
                       "(override: overrides.csv pause_before_ms, pacing, gap_diff_ms, gap_same_ms)")
    return SectionPlan(sec, places, end)


def render_section(sp: SectionPlan, sr: int, stems: bool):
    mix = np.zeros(sp.length, dtype=np.float32)
    st = {s: np.zeros(sp.length, dtype=np.float32) for s in C.SPEAKERS} if stems else {}
    prev_end = 0
    for p in sp.places:
        n = len(p.clip.x)
        if p.insert > 0:
            mix[prev_end:prev_end + p.insert] += make_bed(p.insert, p.bed_tone, sr, p.floor, p.key)
        mix[p.start:p.start + n] += p.clip.x
        if stems:
            st[p.clip.speaker][p.start:p.start + n] += p.clip.x
        prev_end = max(prev_end, p.start + n)
    return mix, st


# ----------------------------------------------------------------------------
# Marker / timeline writers
# ----------------------------------------------------------------------------
def srt_time(t: float) -> str:
    ms = int(round(round(t, 3) * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def srt_text(template: str, section: int, line: int, speaker: str) -> str:
    vals = dict(speaker=speaker, SPEAKER=speaker.upper(), section=section, code=sec_code(section), line=line)
    try:
        return template.format(**vals)
    except (KeyError, IndexError, ValueError):
        return "{SPEAKER}  S{code}_{line}".format(**vals)


def write_srt(path: Path, cues: List[Tuple[float, float, str]]) -> None:
    out = []
    for i, (a, b, text) in enumerate(cues, 1):
        out.append(f"{i}\n{srt_time(a)} --> {srt_time(b)}\n{text}\n")
    write_text_atomic(path, "\n".join(out))


def write_labels(path: Path, rows: List[Tuple[float, float, str]]) -> None:
    write_text_atomic(path, "".join(f"{a:.6f}\t{b:.6f}\t{t}\n" for a, b, t in rows))


TIMELINE_HEADER = ["section", "line", "speaker", "file", "section_start_s", "section_end_s",
                   "full_start_s", "full_end_s", "gap_before_ms", "gain_db"]


def write_timeline(path: Path, rows: List[dict]) -> None:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(TIMELINE_HEADER)
    for r in rows:
        w.writerow([r["section"], r["line"], r["speaker"], r["file"],
                    f"{r['section_start_s']:.3f}", f"{r['section_end_s']:.3f}",
                    f"{r['full_start_s']:.3f}", f"{r['full_end_s']:.3f}",
                    f"{r['gap_before_ms']:.1f}", f"{r['gain_db']:.2f}"])
    write_text_atomic(path, buf.getvalue())


def new_output_dir(project) -> Path:
    base = Path(project) / "merged"
    base.mkdir(parents=True, exist_ok=True)
    name, k = stamp_folder(), 2
    d = base / name
    while True:
        try:
            d.mkdir(exist_ok=False)
            return d
        except FileExistsError:
            d = base / f"{name}_{k}"
            k += 1


# ----------------------------------------------------------------------------
# Main entry
# ----------------------------------------------------------------------------
def _fmt_time(s: float) -> str:
    return f"{int(s // 60):02d}:{s % 60:05.2f}"


def _formats(cfg: MergeConfig) -> List[str]:
    f = (["mp3"] if cfg.export_mp3 else []) + (["wav"] if cfg.export_wav else [])
    return f or ["mp3"]


def _encode(arr_or_raw, n, sr, out: Path, fmt: str, cfg: MergeConfig, raw: bool) -> None:
    if raw:
        encode_raw(arr_or_raw, n, sr, out, fmt, cfg.mp3_bitrate, cfg.wav_bit_depth)
    else:
        encode_audio(arr_or_raw, sr, out, fmt, cfg.mp3_bitrate, cfg.wav_bit_depth)


def run_merge(project, cfg: MergeConfig, progress: Progress = None, dry_run: bool = False,
              script_text: Optional[str] = None, override_text: Optional[str] = None,
              preview_section: Optional[int] = None) -> MergeResult:
    """Merge clean/ into merged/<timestamp>/. With dry_run nothing is written (merge plan preview)."""
    rep = Report(stage="merge")
    res = MergeResult(rep)
    if preview_section is not None:
        dry_run = True  # section audition can never create an export or change controls
    try:
        find_ffmpeg()
    except FFmpegMissing as e:
        rep.fail("E_FFMPEG_MISSING", str(e))
        return res
    try:
        cfg.validate()
        parse_starter_exceptions(cfg.starter_exceptions)
    except ValueError as e:
        rep.fail("E_BAD_SETTINGS", str(e))
        return res
    project = Path(project)
    clean = project / "clean"
    if not clean.is_dir():
        rep.fail("E_NO_CLEAN", f"There is no clean folder yet: {clean}. Run Intake first, or put "
                 "correctly named files (S1_3_koroush.mp3) into a folder called clean.")
        return res
    scan, ignored = scan_clean(clean)
    if not scan:
        rep.fail("E_NO_CLEAN_FILES", f"No usable files in {clean}. Expected names like S1_3_koroush.mp3.")
        return res
    if script_text is None:
        script, _ = load_script(project / C.SCRIPT_FILE)
    else:
        script = parse_script(script_text)
    if script and script.issues:
        rep.issues += script.issues
        rep.fail("E_BAD_SETTINGS", "Fix the reported script errors before merging; no audio was exported.")
        return res
    ov, ov_issues = (load_overrides(project / C.OVERRIDES_FILE) if override_text is None
                     else parse_overrides(override_text))
    rep.issues += ov_issues
    validate_scan(scan, ignored, rep)
    order = build_order(scan, cfg, script, rep)
    if preview_section is not None and preview_section not in order:
        rep.fail('E_BAD_SETTINGS', f'Section {preview_section} has no prepared clips.')
        return res
    total = max(1, sum(len(v) for v in order.values()))
    clips_by_sec: Dict[int, List[Clip]] = {}
    done = 0
    for sec in sorted(order):
        for it in order[sec]:
            if progress:
                progress(0.4 * done / total, f"reading {it.path.name}")
            done += 1
            try:
                c = load_clip(it, cfg, ov, rep)
            except FFmpegMissing as e:
                rep.fail("E_FFMPEG_MISSING", str(e))
                return res
            except Exception as e:  # one bad file never stops the run (I8)
                rep.add("W_SKIPPED_FILE", f"{it.path.name}: unexpected problem ({e}). Skipped.",
                        file=it.path.name, section=sec, speaker=it.speaker)
                rep.exception(f"load {it.path.name}", e)
                continue
            if c is not None:
                clips_by_sec.setdefault(sec, []).append(c)
    all_clips = [c for s in sorted(clips_by_sec) for c in clips_by_sec[s]]
    if not all_clips:
        rep.fail("E_NO_READABLE_AUDIO", "None of the clean files could be read.")
        return res
    existing = {(c.section, c.line, c.speaker) for c in all_clips}
    rep.issues += unused_issues(ov, existing, set(clips_by_sec))
    level_outliers(all_clips, cfg, rep)
    if cfg.match_tone:
        match_tone(all_clips, cfg, rep)
    gains = compute_gains(all_clips, cfg, rep)
    for c in all_clips:
        rep.decide(f"{c.name}: trim {'on' if c.trim_on else 'off'}, room tone {'on' if c.tone_on else 'off'}"
                   f"  <- {c.trim_src}; {c.tone_src}  (override: overrides.csv trim / room_tone)")
    sections = sorted(clips_by_sec)
    sr = cfg.sample_rate
    profile = pacing.pick_profile(all_clips, sr, cfg.pacing_profile) if cfg.pacing == "natural" else ""
    if profile:
        med = float(np.median([pacing.speech_ms(c, sr) for c in all_clips]))
        rep.decide(f"Pacing: natural, {profile} profile  <- pacing_profile={cfg.pacing_profile} "
                   f"(median line {med / 1000:.1f} s)  (override: pacing_profile, or pacing=fixed)")
    else:
        rep.decide("Pacing: fixed  <- pacing=fixed  (override: pacing=natural)")
    state = pacing.PacingState()
    plans = {s: place_section(s, clips_by_sec[s], cfg, rep, state, profile) for s in sections}
    gap_n = int(sr * cfg.section_gap_ms / 1000)
    offsets, off = {}, 0
    for s in sections:
        offsets[s] = off
        off += plans[s].length + gap_n
    full_len = off - gap_n

    res.rows = _timeline_rows(plans, offsets, sr)
    res.plan_rows = [[str(r["section"]), r["file"], f"{r['section_start_s']:.2f}",
                      f"{r['gap_before_ms']:.0f}", f"{r['gain_db']:+.2f}", m]
                     for r, m in _plan_marks(res.rows, order)]
    for s in sections:
        rep.log(f"Section {s}: {len(plans[s].places)} lines, {_fmt_time(plans[s].length / sr)}")
    rep.log(f"FULL: {_fmt_time(full_len / sr)}")
    kinds: Dict[str, int] = {}
    for pl in plans.values():
        for q in pl.places[1:]:
            kinds[q.kind] = kinds.get(q.kind, 0) + 1
    rep.log("Transitions: " + (", ".join(f"{n} {k}" for k, n in sorted(kinds.items())) or "none"))
    read_n = sum(len(v) for v in order.values())
    skipped_n = read_n - len(all_clips)
    rep.lines.insert(0, f"Read {read_n}, placed {len(all_clips)}, skipped {skipped_n}")
    rep.counts = {"sections": len(sections), "lines_read": read_n, "lines_placed": len(all_clips),
                  "lines_skipped": skipped_n, "full_seconds": round(full_len / sr, 3),
                  "gains_db": {k: round(v, 2) for k, v in gains.items()}, "transitions": kinds}
    res.qc_rows = _qc_rows(all_clips)
    if preview_section is not None:
        if preview_section not in plans:
            rep.fail('E_NO_READABLE_AUDIO', f'Section {preview_section} has no readable clips in its order.')
            return res
        if progress:
            progress(0.9, f'Rendering section {preview_section} preview')
        audio, _ = render_section(plans[preview_section], sr, False)
        if len(audio) and float(np.max(np.abs(audio))) > 1.0:
            rep.add('W_OUTPUT_CLIPPED', 'Overlapping clips exceed full scale in the section preview. Reduce gain or overlap.')
        res.preview_audio = (sr, (np.clip(audio, -1, 1) * 32767).round().astype(np.int16))
        rep.log(f'Section {preview_section} preview ready; no files were exported or saved.')
        if progress:
            progress(1.0, 'Section preview ready')
    if dry_run:
        rep.log("Plan only: nothing was written.")
        return res
    need = _estimate_bytes(cfg, full_len, len(sections))
    try:
        base = Path(project)
        base.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(base).free
    except OSError:
        free = None
    if free is not None and free < need:
        rep.fail("E_NO_SPACE", f"Not enough free disk space: about {need / 1e6:.0f} MB are needed and "
                 f"{free / 1e6:.0f} MB are free. Free some space or turn off WAV / stems and try again.")
        return res
    out = new_output_dir(project)
    res.out_dir = out
    marker = out / "INCOMPLETE.txt"
    write_text_atomic(marker, "This merge has not finished. Do not use these files.\n"
                              "If the run was interrupted, start it again: nothing else was changed.\n")
    try:
        _export_all(out, cfg, plans, offsets, full_len, res, progress)
    except Exception as e:
        rep.fail("E_EXPORT", f"Export failed: {e}")
        rep.exception("export", e)
    _write_qc(out, res.qc_rows)
    rep.outputs += ["qc.json", "qc.txt"]
    _write_reports(out, cfg, project, all_clips, gains, rep)
    if not rep.failed:
        marker.unlink(missing_ok=True)
    rep.log(f"Output folder: {out}")
    return res


def _estimate_bytes(cfg: MergeConfig, full_len: int, n_sections: int) -> float:
    """Generous disk estimate: temporary float buffers + every requested output."""
    tracks = 1 + (len(C.SPEAKERS) if cfg.export_stems else 0)
    per_sample = 4.0 * tracks                                   # raw FULL buffers
    wav = 3.0 if cfg.wav_bit_depth == 24 else 2.0
    if cfg.export_wav:
        per_sample += wav * tracks * (2 if cfg.export_full else 1)
    if cfg.export_mp3:
        per_sample += 1.0 * (2 if cfg.export_full else 1)
    return full_len * per_sample * 1.15 + n_sections * 1e5


def _qc_rows(clips: List[Clip]) -> List[dict]:
    rows = []
    for c in clips:
        lvl = 20 * np.log10(c.speech_rms) if c.speech_rms > 0 else -120.0
        rows.append(dict(section=c.section, line=c.line, speaker=c.speaker, file=c.path.name,
                         speech_s=round(c.speech_s, 2), speech_level_db=round(float(lvl), 1),
                         peak_db=round(peak_db(np.array([c.peak], dtype=np.float32)), 1),
                         longest_silence_s=round(c.longest_silence_ms / 1000, 2), flags=list(c.flags)))
    return rows


def qc_text(rows: List[dict]) -> str:
    out = ["QUALITY CHECK  (section / file / speech s / level dB / peak dB / longest silence s / flags)"]
    for r in sorted(rows, key=lambda r: (-len(r["flags"]), r["section"], r["line"], r["speaker"])):
        out.append(f"  S{r['section']:<3} {r['file']:<26} {r['speech_s']:>6.2f} {r['speech_level_db']:>7.1f} "
                   f"{r['peak_db']:>7.1f} {r['longest_silence_s']:>6.2f}  {', '.join(r['flags']) or '-'}")
    return "\n".join(out) + "\n"


def _write_qc(out: Path, rows: List[dict]) -> None:
    write_text_atomic(out / "qc.json", json.dumps(rows, indent=2, ensure_ascii=False))
    write_text_atomic(out / "qc.txt", qc_text(rows))


def order_marks(items: List[OrderItem], plan: SectionPlan) -> List[bool]:
    dev = {(i.speaker, i.line): i.deviates for i in items}
    return [dev.get((p.clip.speaker, p.clip.line), False) for p in plan.places]


def _plan_marks(rows, order):
    dev = {(i.section, i.speaker, i.line): i.deviates for v in order.values() for i in v}
    return [(r, "≠ not alternating" if dev.get((r["section"], r["speaker"], r["line"])) else "") for r in rows]


def _timeline_rows(plans, offsets, sr) -> List[dict]:
    rows = []
    for s, pl in plans.items():
        for p in pl.places:
            a, b = p.start, p.start + len(p.clip.x)
            rows.append(dict(section=s, line=p.clip.line, speaker=p.clip.speaker, file=p.clip.path.name,
                             section_start_s=a / sr, section_end_s=b / sr,
                             full_start_s=(offsets[s] + a) / sr, full_end_s=(offsets[s] + b) / sr,
                             gap_before_ms=p.gap_ms, gain_db=p.clip.gain_db))
    return rows


def _export_all(out: Path, cfg: MergeConfig, plans, offsets, full_len, res: MergeResult,
                progress: Progress) -> None:
    sr, rep = cfg.sample_rate, res.report
    fmts = _formats(cfg)
    secs = sorted(plans)
    gap_n = int(sr * cfg.section_gap_ms / 1000)
    with tempfile.TemporaryDirectory(prefix="mako_") as tmp:
        raw = {k: open(Path(tmp) / f"{k}.f32", "wb") for k in (("mix",) + (C.SPEAKERS if cfg.export_stems else ()))}
        try:
            for n, s in enumerate(secs):
                if progress:
                    progress(0.4 + 0.5 * n / len(secs), f"rendering section {s}")
                pl = plans[s]
                mix, st = render_section(pl, sr, cfg.export_stems)
                top = float(np.max(np.abs(mix))) if len(mix) else 0.0
                if top > 1.0:
                    rep.add("W_OUTPUT_CLIPPED", f"Section {s}: the mix reaches {20 * np.log10(top):+.1f} dBFS "
                            "(over full scale), most likely where lines overlap. Lower gain_db for the "
                            "lines there, or turn talk-over down (overlap_amount).", section=s)
                base = out / f"Section_{s:02d}"
                for f in fmts:
                    _encode(mix, 0, sr, Path(f"{base}.{f}"), f, cfg, False)
                    rep.outputs.append(f"{base.name}.{f}")
                for sp, arr in st.items():
                    _encode(arr, 0, sr, out / f"{base.name}_{sp}.wav", "wav", cfg, False)
                    rep.outputs.append(f"{base.name}_{sp}.wav")
                rows = [r for r in res.rows if r["section"] == s]
                _write_markers(out, base.name, cfg, rows, "section")
                if cfg.export_full:
                    mix.astype("<f4").tofile(raw["mix"])
                    for sp, arr in st.items():
                        arr.astype("<f4").tofile(raw[sp])
                    if n < len(secs) - 1:
                        lastc = pl.places[-1].clip
                        last = lastc.tone if (cfg.room_tone and lastc.tone_on) else None
                        make_bed(gap_n, last, sr, lastc.floor if lastc.tone_on else 0.0,
                                 (cfg.seed, s, 99999)).astype("<f4").tofile(raw["mix"])
                        for sp in st:
                            np.zeros(gap_n, dtype="<f4").tofile(raw[sp])
                del mix, st
        finally:
            for f in raw.values():
                f.close()
        if cfg.export_full:
            if progress:
                progress(0.92, "encoding FULL")
            for f in fmts:
                _encode(Path(tmp) / "mix.f32", full_len, sr, out / f"FULL.{f}", f, cfg, True)
                rep.outputs.append(f"FULL.{f}")
            for sp in (C.SPEAKERS if cfg.export_stems else ()):
                _encode(Path(tmp) / f"{sp}.f32", full_len, sr, out / f"FULL_{sp}.wav", "wav", cfg, True)
                rep.outputs.append(f"FULL_{sp}.wav")
            _write_markers(out, "FULL", cfg, res.rows, "full")
    write_timeline(out / "timeline.csv", res.rows)
    rep.outputs.append("timeline.csv")


def _write_markers(out: Path, base: str, cfg: MergeConfig, rows: List[dict], kind: str) -> None:
    a, b = (f"{kind}_start_s", f"{kind}_end_s")
    if kind == "section":
        a, b = "section_start_s", "section_end_s"
    if "srt" in cfg.marker_formats:
        write_srt(out / f"{base}.srt", [(r[a], r[b], srt_text(cfg.srt_text_template, r["section"], r["line"],
                                                              r["speaker"])) for r in rows])
    if "audacity" in cfg.marker_formats:
        write_labels(out / f"{base}_labels.txt", [(r[a], r[b], f"S{sec_code(r['section'])}_{r['line']} {r['speaker']}")
                                                  for r in rows])


def _write_reports(out: Path, cfg: MergeConfig, project: Path, clips: List[Clip], gains, rep: Report) -> None:
    write_text_atomic(out / "report.txt", rep.to_text())
    write_text_atomic(out / "report.json", rep.to_json())
    man = {"tool_version": C.TOOL_VERSION, "created_utc": utc_iso(), "config": asdict(cfg),
           "sample_rate": cfg.sample_rate,
           "inputs": [{"file": c.path.name, "sha256": sha256_file(c.path), "speaker": c.speaker,
                       "section": c.section, "line": c.line} for c in clips],
           "outputs": [{"file": f.name, "sha256": sha256_file(f)} for f in sorted(out.iterdir())
                       if f.is_file() and f.name not in ("manifest.json", "INCOMPLETE.txt")
                       and not f.name.endswith(".part")],
           "gains_db": {k: round(v, 4) for k, v in gains.items()},
           "warnings": [i.to_dict() for i in rep.issues]}
    write_text_atomic(out / "manifest.json", json.dumps(man, indent=2, ensure_ascii=False))


def script_template(project, cfg: MergeConfig) -> Tuple[Path, str]:
    """Write script.txt (or script.suggested.txt if one exists) from the order the tool would use now.

    The owner then edits only the sections that are not strictly alternating. Never overwrites.
    """
    project = Path(project)
    order, _ = preview_order(project, cfg)
    lines = ["# One line per section: exact clip IDs K1 R1 K2 (decimal dialogue numbers).",
             "# Reorder IDs freely. Omitted IDs are excluded from explicit section arrangements.",
             "# Edit only the sections that are not strictly alternating, then run Merge."]
    for sec in sorted(order):
        seq = " ".join(("K" if i.speaker == "koroush" else "R") + str(i.line) for i in order[sec])
        lines.append(f"{sec_code(sec)}: {seq}")
    target = project / C.SCRIPT_FILE
    if target.exists():
        target = unique_path(project / "script.suggested.txt")
    write_text_atomic(target, "\n".join(lines) + "\n")
    return target, "\n".join(lines)
