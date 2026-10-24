"""All constants of the Mako pipeline (see docs/SPEC.md)."""
from __future__ import annotations

TOOL_VERSION = "2.1.0"

# The only place to extend if more speakers are ever needed (spec Q8).
SPEAKERS = ("koroush", "reza")
SPEAKER_SHORT = {"k": "koroush", "r": "reza"}

SECTION_CODES = "123456789abcdefghijklmnopqrstuvwxyz"  # index = section-1

AUDIO_EXTS = (
    ".mp3", ".mp4", ".m4a", ".wav", ".aac", ".flac", ".ogg", ".oga", ".opus",
    ".webm", ".mkv", ".mov", ".wma", ".amr", ".3gp", ".aiff", ".aif",
)
CLEAN_EXTS = (".mp3", ".wav")

# Intake ordering modes for the "sequence" naming style.
ORDER_NUMBER = "number in the name"
ORDER_NAME = "name (natural A→Z)"
ORDER_DATE = "date modified"
ORDER_CHOICES = (ORDER_NUMBER, ORDER_NAME, ORDER_DATE)

STYLES = ("mako", "smart", "sequence", "compact", "custom")
STYLE_LABELS = {
    "Mako codes (K11 / R11)": "mako",
    "Auto-read names": "smart",
    "Numbered files + section list": "sequence",
    "Compact digits": "compact",
    "Custom filename pattern": "custom",
}
STYLE_TO_LABEL = {v: k for k, v in STYLE_LABELS.items()}

# DSP constants (defaults of the Advanced settings, spec 2A.2 / 9.5)
LEAD_PAD_MS = 40.0
TAIL_PAD_MS = 80.0
EDGE_FADE_MS = 5.0
TRIM_THRESHOLD_DB = 45.0
TRIM_FLOOR_MARGIN_DB = 6.0
ROOM_TONE_MIN_MS = 120.0
ROOM_TONE_MAX_MS = 500.0
BED_FADE_MS = 6.0
PEAK_CEILING_DBFS = -1.0
MAX_GAIN_DB = 15.0
BIG_GAIN_DB = 12.0            # a gain this large raises the noise too: warn
MATCH_CEILING_LUFS = -14.0    # loudness_mode "match": never louder than this
MIN_CLIP_S = 0.1
CLIPPED_PEAK = 0.999
NEAR_SILENT_PEAK = 0.003

# Natural pacing: pacing_profile name -> (pause scale, talk-over chance, quick-reply chance, spread)
PACING_PROFILES = {
    "tight":   dict(scale=0.67, overlap=0.28, quick=0.24, spread=0.40),
    "normal":  dict(scale=1.00, overlap=0.16, quick=0.16, spread=0.35),
    "relaxed": dict(scale=1.45, overlap=0.10, quick=0.09, spread=0.28),
}
PACING_CHOICES = ("auto",) + tuple(PACING_PROFILES)
PROFILE_TIGHT_BELOW_MS = 1500.0      # median speech length that picks "tight"
PROFILE_NORMAL_BELOW_MS = 4000.0     # ... "normal"; longer lines pick "relaxed"
RHYTHM_CARRY = 0.55                  # how much of the last transition's energy carries on
THINK_PAUSE_PROB = 0.06              # chance of a longer "thinking" beat
TEMPO_DRIFT_STEP = 0.02
TEMPO_DRIFT_RANGE = (0.82, 1.2)
TEMPO_PULL_BACK = 0.08
OVERLAP_MIN_MS = 30.0
OVERLAP_SPAN_MS = 90.0               # talk-over depth = 30 + 90 * eagerness
COMFORT_BELOW_SPEECH_DB = 30.0       # synthetic room noise stays this far under speech

LOUDNESS_MODES = ("fixed", "match")
PACING_MODES = ("natural", "fixed")
INTAKE_MODES = ("replace", "add")

# Issue codes (spec 12.1)
FATAL_CODES = {
    "E_FFMPEG_MISSING", "E_NO_FOLDER", "E_NO_CLEAN", "E_NO_CLEAN_FILES",
    "E_NO_READABLE_AUDIO", "E_BAD_SETTINGS", "E_NO_SPACE", "E_EXPORT",
}
ERROR_CODES = FATAL_CODES | {
    "E_SECTION_LIST_SYNTAX", "E_SCRIPT_SYNTAX",
    "E_DECODE", "E_CONVERT", "E_VERIFY_LENGTH", "E_COPY_SIZE",
}
WARNING_CODES = {
    "W_NAME_UNREADABLE", "W_UNMAPPED", "W_DUPLICATE_TAKE", "W_IGNORED_FILE",
    "W_MISSING_LINE", "W_EXTRA_LINE", "W_NO_AUDIO_SPEAKER", "W_COUNT_MISMATCH",
    "W_CLIPPED_SOURCE", "W_NEAR_SILENT", "W_SKIPPED_FILE", "W_GAIN_LIMITED",
    "W_LOUDNESS_UNMEASURED", "W_OVERRIDE_INVALID", "W_OVERRIDE_UNUSED",
    "W_RENAME_OVERRIDE_UNUSED", "W_LONG_SILENCE", "W_LEVEL_OUTLIER", "W_SHORT_LINE",
    "W_LONG_LINE", "W_TRIM_SUSPICIOUS", "W_OUTPUT_CLIPPED", "W_BIG_GAIN",
    "W_MIX_ATTENUATED",
}

FFMPEG_HINT = "Set MAKO_FFMPEG to an existing FFmpeg executable, or install imageio-ffmpeg when connectivity is available."

# File names inside the project folder
SETTINGS_FILE = "mako_settings.json"
SCRIPT_FILE = "script.txt"
OVERRIDES_FILE = "overrides.csv"
RENAME_OVERRIDES_FILE = "rename_overrides.csv"
