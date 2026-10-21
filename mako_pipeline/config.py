"""Configuration dataclasses, defaults, JSON load/save and validation (spec 10, 2A.2)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field, fields, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import constants as C
from .dsp import DspParams
from .util import write_text_atomic


@dataclass
class SpeakerCfg:
    folder: str = ""
    style: str = "mako"                 # smart | sequence | compact
    order_by: str = C.ORDER_NUMBER
    sections_text: str = ""
    compact_digits: int = 1
    pattern: str = ""


@dataclass
class IntakeConfig:
    project: str = ""
    clean_format: str = "wav"            # mp3 | wav
    speakers: Dict[str, SpeakerCfg] = field(
        default_factory=lambda: {sp: SpeakerCfg() for sp in C.SPEAKERS})
    mp3_bitrate: str = "320k"
    dup_policy: str = "newest"           # newest | oldest | manual
    verify_tolerance_s: float = 0.15
    intake_mode: str = "replace"         # replace: rebuild this speaker's clean files | add: only new lines


@dataclass
class MergeConfig:
    starter: str = "koroush"
    starter_exceptions: str = ""
    # Pacing. "natural" draws every pause from a continuous, seeded distribution (with rhythm memory,
    # slow tempo drift, quick replies, thinking beats and short talk-overs); "fixed" uses the two
    # gaps below with +/- variation_pct. gap_diff_ms / gap_same_ms are the typical (median) gaps.
    pacing: str = "natural"
    pacing_profile: str = "auto"         # auto | tight | normal | relaxed
    gap_diff_ms: float = 300
    gap_same_ms: float = 200
    variation_pct: float = 20            # fixed pacing only
    overlap_amount: float = 1.0          # 0 = never talk over, 2 = twice as often
    quick_reply_amount: float = 1.0
    thinking_pause_amount: float = 1.0
    rhythm_memory: float = 1.0           # 0 = every pause independent, higher = the rhythm carries on
    tempo_drift: float = 1.0             # 0 = perfectly steady tempo
    min_gap_ms: float = 40               # shortest pause natural pacing may draw (talk-over excluded)
    overlap_max_ms: float = 120          # deepest talk-over
    section_gap_ms: float = 1000
    sample_rate: int = 48000
    trim: bool = True
    room_tone: bool = True
    comfort_noise: bool = True           # when a clip has no quiet stretch, synthesise room hiss, never dead silence
    match_loudness: bool = True
    loudness_mode: str = "fixed"         # fixed: every speaker to target_lufs | match: to the louder speaker
    target_lufs: float = -16.0
    match_tone: bool = False             # bring the two speakers' brightness closer (gentle shelf)
    max_tone_db: float = 3.0
    export_mp3: bool = True
    export_wav: bool = True
    export_full: bool = True
    export_stems: bool = False
    seed: int = 7
    wav_bit_depth: int = 24
    marker_formats: List[str] = field(default_factory=lambda: ["srt", "audacity", "csv"])
    srt_text_template: str = "{SPEAKER}  S{code}_{line}"
    # Advanced (2A.2)
    lead_pad_ms: float = C.LEAD_PAD_MS
    tail_pad_ms: float = C.TAIL_PAD_MS
    edge_fade_ms: float = C.EDGE_FADE_MS
    trim_threshold_db: float = C.TRIM_THRESHOLD_DB
    trim_floor_margin_db: float = C.TRIM_FLOOR_MARGIN_DB
    room_tone_min_ms: float = C.ROOM_TONE_MIN_MS
    room_tone_max_ms: float = C.ROOM_TONE_MAX_MS
    peak_ceiling_dbfs: float = C.PEAK_CEILING_DBFS
    max_gain_db: float = C.MAX_GAIN_DB
    mp3_bitrate: str = "320k"
    # Quality checks (warnings only; audio is never changed by them)
    qc_long_silence_s: float = 2.5
    qc_level_outlier_db: float = 8.0
    qc_short_line_s: float = 0.4
    qc_long_line_s: float = 60.0

    def dsp(self) -> DspParams:
        return DspParams(self.lead_pad_ms, self.tail_pad_ms, self.edge_fade_ms,
                         self.trim_threshold_db, self.trim_floor_margin_db,
                         self.room_tone_min_ms, self.room_tone_max_ms)

    def validate(self) -> None:
        """Raise ValueError with a human-readable message for any bad value."""
        problems = _problems(self)
        if problems:
            raise ValueError("Invalid setting(s): " + "; ".join(problems))


RANGES = {
    "gap_diff_ms": (0, 1500), "gap_same_ms": (0, 1500), "variation_pct": (0, 60),
    "section_gap_ms": (0, 5000), "target_lufs": (-30, -10),
    "lead_pad_ms": (0, 2000), "tail_pad_ms": (0, 2000), "edge_fade_ms": (0, 200),
    "trim_threshold_db": (10, 90), "trim_floor_margin_db": (0, 30),
    "room_tone_min_ms": (20, 2000), "room_tone_max_ms": (50, 5000),
    "peak_ceiling_dbfs": (-30, 0), "max_gain_db": (0, 60),
    "overlap_amount": (0, 3), "quick_reply_amount": (0, 3), "thinking_pause_amount": (0, 3),
    "rhythm_memory": (0, 1.7), "tempo_drift": (0, 3), "min_gap_ms": (0, 500), "overlap_max_ms": (0, 400),
    "max_tone_db": (0, 6), "qc_long_silence_s": (0.2, 60), "qc_level_outlier_db": (1, 30),
    "qc_short_line_s": (0, 5), "qc_long_line_s": (5, 600),
}
CHOICES = {
    "starter": C.SPEAKERS, "sample_rate": (44100, 48000), "wav_bit_depth": (16, 24),
    "pacing": C.PACING_MODES, "pacing_profile": C.PACING_CHOICES, "loudness_mode": C.LOUDNESS_MODES,
}
INTAKE_CHOICES = {"clean_format": ("mp3", "wav"), "dup_policy": ("newest", "oldest", "manual"),
                  "intake_mode": C.INTAKE_MODES}


def _problems(cfg: MergeConfig) -> List[str]:
    out: List[str] = []
    for k, (lo, hi) in RANGES.items():
        v = getattr(cfg, k)
        if not (lo <= v <= hi):
            out.append(f"{k}={v} (allowed {lo}..{hi})")
    for k, allowed in CHOICES.items():
        if getattr(cfg, k) not in allowed:
            out.append(f"{k}={getattr(cfg, k)!r} (allowed {list(allowed)})")
    if not cfg.export_mp3 and not cfg.export_wav:
        out.append('Enable at least one output format: WAV or MP3')
    if cfg.room_tone_min_ms > cfg.room_tone_max_ms:
        out.append('room_tone_min_ms must not exceed room_tone_max_ms')
    if cfg.qc_short_line_s > cfg.qc_long_line_s:
        out.append('short-line threshold must not exceed long-line threshold')
    bad_m = [m for m in cfg.marker_formats if m not in ("srt", "audacity", "csv")]
    if bad_m:
        out.append(f"marker_formats has unknown {bad_m}")
    return out


def validate_intake(cfg: IntakeConfig) -> None:
    problems = []
    import math
    from .names import compile_pattern
    if not math.isfinite(cfg.verify_tolerance_s) or not 0 <= cfg.verify_tolerance_s <= 10:
        problems.append('verify_tolerance_s must be finite and between 0 and 10')
    for sc in cfg.speakers.values():
        if sc.style == 'custom':
            compile_pattern(sc.pattern)
    for k, allowed in INTAKE_CHOICES.items():
        if getattr(cfg, k) not in allowed:
            problems.append(f"{k}={getattr(cfg, k)!r} (allowed {list(allowed)})")
    for sp, s in cfg.speakers.items():
        if s.style not in C.STYLES:
            problems.append(f"{sp} style={s.style!r}")
        if s.order_by not in C.ORDER_CHOICES:
            problems.append(f"{sp} order_by={s.order_by!r}")
        if s.compact_digits not in (1, 2):
            problems.append(f"{sp} compact_digits={s.compact_digits!r}")
    if problems:
        raise ValueError("Invalid setting(s): " + "; ".join(problems))


@dataclass
class Settings:
    intake: IntakeConfig = field(default_factory=IntakeConfig)
    merge: MergeConfig = field(default_factory=MergeConfig)


# Presets alter only the visible processing choices; recordings and explicit clip overrides stay intact.
SOUND_PRESETS = {
    'Clean dialogue': dict(pacing='fixed', gap_diff_ms=250, gap_same_ms=150,
                          section_gap_ms=800, variation_pct=0, trim=True,
                          room_tone=True, comfort_noise=False, match_loudness=True,
                          target_lufs=-16, match_tone=False, overlap_amount=0),
    'Natural conversation': dict(pacing='natural', pacing_profile='auto', gap_diff_ms=300,
                                 gap_same_ms=200, section_gap_ms=1000, variation_pct=20,
                                 trim=True, room_tone=True, comfort_noise=True,
                                 match_loudness=True, target_lufs=-16, match_tone=False,
                                 overlap_amount=0.3, quick_reply_amount=1,
                                 thinking_pause_amount=1, rhythm_memory=1, tempo_drift=1),
    'Just join clips': dict(pacing='fixed', gap_diff_ms=0, gap_same_ms=0,
                            section_gap_ms=0, variation_pct=0, trim=False,
                            room_tone=False, comfort_noise=False, match_loudness=False,
                            match_tone=False, overlap_amount=0),
}


# ----------------------------------------------------------------------------
# Flat dict <-> Settings  (mako_settings.json is a flat JSON object)
# ----------------------------------------------------------------------------
def _coerce(value: Any, default: Any) -> Any:
    """Coerce a JSON value to the type of `default`; raise on impossible."""
    if isinstance(default, bool):
        if isinstance(value, str):
            v = value.strip().lower()
            if v in ("true", "1", "yes", "on"):
                return True
            if v in ("false", "0", "no", "off"):
                return False
            raise ValueError(value)
        return bool(value)
    if isinstance(default, int):
        number = float(value)
        if not number.is_integer():
            raise ValueError('must be a finite whole number')
        return int(number)
    if isinstance(default, float):
        return float(value)
    if isinstance(default, list):
        if isinstance(value, str):
            return [p.strip() for p in value.split(",") if p.strip()]
        return [str(v) for v in value]
    return str(value)


def _speaker_keys(sp: str) -> Dict[str, str]:
    return {f"{sp}_folder": "folder", f"{sp}_style": "style", f"{sp}_order_by": "order_by",
            f"{sp}_sections": "sections_text", f"{sp}_compact_digits": "compact_digits", f"{sp}_pattern": "pattern"}


def settings_from_dict(d: Dict[str, Any], base: Optional[Settings] = None, *, strict: bool = False) -> Settings:
    """Apply flat settings. File values recover to defaults; explicit strict values raise."""
    s = base or Settings()
    m_defaults = MergeConfig()
    for f in fields(MergeConfig):
        if f.name in d:
            try:
                setattr(s.merge, f.name, _coerce(d[f.name], getattr(m_defaults, f.name)))
            except (TypeError, ValueError, OverflowError) as e:
                if strict:
                    raise ValueError(f'{f.name}: invalid value {d[f.name]!r}') from e
    for k in ("project", "clean_format", "dup_policy", "mp3_bitrate", "intake_mode"):
        if k in d:
            try:
                setattr(s.intake, k, str(d[k]))
            except Exception:
                pass
    if "verify_tolerance_s" in d:
        try:
            s.intake.verify_tolerance_s = float(d["verify_tolerance_s"])
        except (TypeError, ValueError, OverflowError) as e:
            if strict:
                raise ValueError('verify_tolerance_s must be a number') from e
    if "mp3_bitrate" in d:
        s.merge.mp3_bitrate = str(d["mp3_bitrate"])
    for sp in C.SPEAKERS:
        sc = s.intake.speakers.setdefault(sp, SpeakerCfg())
        for key, attr in _speaker_keys(sp).items():
            if key in d:
                try:
                    setattr(sc, attr, _coerce(d[key], getattr(SpeakerCfg(), attr)))
                except (TypeError, ValueError, OverflowError) as e:
                    if strict:
                        raise ValueError(f'{key}: invalid value {d[key]!r}') from e
    s.intake.mp3_bitrate = s.merge.mp3_bitrate
    if strict:
        validate_intake(s.intake)
        s.merge.validate()
    else:
        _sanitize(s)
    return s


def _sanitize(s: Settings) -> None:
    """Out-of-range values from a hand-edited file fall back to defaults (never crash)."""
    dm = MergeConfig()
    for k, (lo, hi) in RANGES.items():
        if not (lo <= getattr(s.merge, k) <= hi):
            setattr(s.merge, k, getattr(dm, k))
    for k, allowed in CHOICES.items():
        if getattr(s.merge, k) not in allowed:
            setattr(s.merge, k, getattr(dm, k))
    s.merge.marker_formats = [m for m in s.merge.marker_formats if m in ("srt", "audacity", "csv")]
    di = IntakeConfig()
    for k, allowed in INTAKE_CHOICES.items():
        if getattr(s.intake, k) not in allowed:
            setattr(s.intake, k, getattr(di, k))
    for sp, sc in s.intake.speakers.items():
        if sc.style not in C.STYLES:
            sc.style = "smart"
        if sc.order_by not in C.ORDER_CHOICES:
            sc.order_by = C.ORDER_NUMBER
        if sc.compact_digits not in (1, 2):
            sc.compact_digits = 1


def settings_to_dict(s: Settings) -> Dict[str, Any]:
    d: Dict[str, Any] = {"project": s.intake.project, "clean_format": s.intake.clean_format,
                         "dup_policy": s.intake.dup_policy, "intake_mode": s.intake.intake_mode,
                         "verify_tolerance_s": s.intake.verify_tolerance_s}
    d.update(asdict(s.merge))
    for sp in C.SPEAKERS:
        sc = s.intake.speakers.get(sp, SpeakerCfg())
        for key, attr in _speaker_keys(sp).items():
            d[key] = getattr(sc, attr)
    return d


def load_json(path) -> Dict[str, Any]:
    """Read a settings file; a missing or corrupt file gives {} (never crash)."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_settings(path, s: Settings) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(path, json.dumps(settings_to_dict(s), indent=2, ensure_ascii=False) + "\n")


def launcher_dir() -> Path:
    return Path(__file__).resolve().parent.parent


def resolve_settings(project: str = "", overrides: Optional[Dict[str, Any]] = None,
                     launcher: Optional[Path] = None) -> Settings:
    """Layering (lowest first): built-in defaults < launcher file < project file < overrides."""
    launcher = launcher or launcher_dir()
    s = settings_from_dict(load_json(launcher / C.SETTINGS_FILE))
    proj = project or s.intake.project
    if proj:
        s = settings_from_dict(load_json(Path(proj) / C.SETTINGS_FILE), s)
    if overrides:
        s = settings_from_dict(overrides, s, strict=True)
    if project:
        s.intake.project = project
    return s
