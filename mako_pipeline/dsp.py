"""Conservative DSP (spec 9.5): trim, room tone, bed, loudness. No EQ/compression/denoise."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np

from . import constants as C


@dataclass
class DspParams:
    lead_pad_ms: float = C.LEAD_PAD_MS
    tail_pad_ms: float = C.TAIL_PAD_MS
    edge_fade_ms: float = C.EDGE_FADE_MS
    trim_threshold_db: float = C.TRIM_THRESHOLD_DB
    trim_floor_margin_db: float = C.TRIM_FLOOR_MARGIN_DB
    room_tone_min_ms: float = C.ROOM_TONE_MIN_MS
    room_tone_max_ms: float = C.ROOM_TONE_MAX_MS


def frame_len(sr: int, ms: float = 10) -> int:
    return max(1, int(sr * ms / 1000))


def frame_db(x: np.ndarray, sr: int, ms: float = 10) -> np.ndarray:
    """Per-frame RMS level in dB. Trailing partial frame is dropped."""
    n = frame_len(sr, ms)
    k = len(x) // n
    if k < 1:
        return np.array([-120.0])
    f = x[: k * n].astype(np.float64).reshape(k, n)
    rms = np.sqrt(np.mean(f * f, axis=1))
    return 20.0 * np.log10(rms + 1e-9)


def speech_edges(x: np.ndarray, sr: int, p: Optional[DspParams] = None) -> Optional[Tuple[int, int]]:
    p = p or DspParams()
    n = frame_len(sr)
    db = frame_db(x, sr)
    peak_db = float(np.max(db))
    if peak_db < -60:
        return None
    floor_db = float(np.percentile(db, 5))
    thr = max(peak_db - p.trim_threshold_db, floor_db + p.trim_floor_margin_db)
    above = np.where(db > thr)[0]
    if len(above) == 0:
        return None
    return int(above[0] * n), int(min(len(x), (above[-1] + 1) * n))


def fade(x: np.ndarray, sr: int, ms: float = C.EDGE_FADE_MS) -> np.ndarray:
    """Linear fade in and out; length = min(ms, len/2); no fade if < 2 samples."""
    y = x.astype(np.float32).copy()
    fl = min(int(sr * ms / 1000), len(y) // 2)
    if fl < 2:
        return y
    ramp = np.linspace(0.0, 1.0, fl, dtype=np.float32)
    y[:fl] *= ramp
    y[-fl:] *= ramp[::-1]
    return y


def trim_clip(x: np.ndarray, sr: int, do_trim: bool,
              p: Optional[DspParams] = None) -> Tuple[np.ndarray, int, int]:
    """Return (clip, lead_kept, tail_kept). Natural pads are kept; fades only at cuts."""
    p = p or DspParams()
    edges = speech_edges(x, sr, p)
    if edges is None:
        return x.astype(np.float32).copy(), 0, 0
    first, last = edges
    if not do_trim:
        return fade(x, sr, p.edge_fade_ms), first, len(x) - last
    s = max(0, first - int(sr * p.lead_pad_ms / 1000))
    e = min(len(x), last + int(sr * p.tail_pad_ms / 1000))
    return fade(x[s:e], sr, p.edge_fade_ms), first - s, e - last


def find_room_tone(x: np.ndarray, sr: int, p: Optional[DspParams] = None) -> Optional[np.ndarray]:
    """Find the quietest stretch of the (untrimmed) clip to use as room tone."""
    p = p or DspParams()
    n = frame_len(sr)
    db = frame_db(x, sr)
    if len(db) < 14:
        return None
    peak = float(np.max(db))
    floor = float(np.percentile(db, 10))
    if peak - floor < 20:
        return None
    quiet = db <= min(floor + 4.0, peak - 25.0)
    q = np.concatenate([[0], quiet.astype(np.int8), [0]])
    d = np.diff(q)
    starts, ends = np.where(d == 1)[0], np.where(d == -1)[0]
    if len(starts) == 0:
        return None
    k = int(np.argmax(ends - starts))
    i, j = int(starts[k]), int(ends[k])
    a, b = i + 1, j - 1
    min_frames = int(round(p.room_tone_min_ms / 10))
    if b - a < min_frames:
        return None
    half = max(1, int(round(p.room_tone_max_ms / 10 / 2)))
    mid = (a + b) // 2
    a, b = max(a, mid - half), min(b, mid + half)
    return x[a * n: b * n].astype(np.float32).copy()


def comfort_noise(n: int, rms: float, sr: int, key: Tuple = ()) -> np.ndarray:
    """Soft, low-passed room hiss of exactly n samples at the given RMS (seeded: same key, same noise)."""
    if n <= 0 or rms <= 0:
        return np.zeros(max(n, 0), dtype=np.float32)
    rng = np.random.default_rng([abs(int(k)) % (2 ** 32) for k in key] or [0])
    white = rng.standard_normal(n)
    spec = np.fft.rfft(white)
    f = np.fft.rfftfreq(n, 1.0 / sr)
    spec *= 1.0 / np.sqrt(1.0 + (f / 1500.0))      # gentle pink-ish tilt, like a real room
    y = np.fft.irfft(spec, n=n)
    y = y / (float(np.sqrt(np.mean(y * y))) + 1e-12) * rms
    return y.astype(np.float32)


def make_bed(n: int, tone: Optional[np.ndarray], sr: int, floor: float = 0.0,
             key: Tuple = ()) -> np.ndarray:
    """Fill for a pause of exactly n samples.

    Real room tone (boomerang loop at its real level) when the clip has a usable quiet stretch;
    otherwise synthetic comfort noise at the clip's own noise floor (floor > 0); otherwise silence.
    """
    if n <= 0:
        return np.zeros(0, dtype=np.float32)
    if tone is not None and len(tone) >= 8:
        unit = np.concatenate([tone, tone[::-1]])
        reps = n // len(unit) + 1
        bed = np.tile(unit, reps)[:n].astype(np.float32)
        return fade(bed, sr, C.BED_FADE_MS)
    if floor > 0:
        return fade(comfort_noise(n, floor, sr, key), sr, C.BED_FADE_MS)
    return np.zeros(n, dtype=np.float32)


def measure_lufs(x: np.ndarray, sr: int) -> Tuple[Optional[float], str]:
    """Integrated loudness. Returns (value or None, engine label)."""
    if len(x) < 0.5 * sr:
        return None, "too short"
    try:
        import pyloudnorm as pyln  # type: ignore
        v = float(pyln.Meter(sr).integrated_loudness(x.astype(np.float64)))
        if np.isfinite(v):
            return v, "BS.1770 (pyloudnorm)"
    except Exception:
        pass
    db = frame_db(x, sr)
    active = db[db > np.max(db) - 40]
    if len(active) == 0:
        return None, "approx LUFS"
    return float(10 * np.log10(np.mean(10 ** (active / 10))) - 0.7), "approx LUFS"


def peak_db(x: np.ndarray) -> float:
    pk = float(np.max(np.abs(x))) if len(x) else 0.0
    return 20.0 * np.log10(pk) if pk > 0 else -120.0


def clip_levels(x: np.ndarray, sr: int) -> Tuple[float, float, float, float]:
    """(head_energy, tail_energy, speech_rms, noise_floor_rms), all linear.

    head/tail energy is the RMS of the first/last 15 ms relative to the speech level: a clip that was
    cut right at the first or last word has a high value ("eager" delivery), a padded one ~0.
    The noise floor is the quietest 5 % of 10 ms frames, capped well under the speech level.
    """
    db = frame_db(x, sr)
    peak = float(np.max(db))
    if peak < -60:
        return 0.0, 0.0, 0.0, 0.0
    active = db[db > peak - 30]
    speech = float(np.sqrt(np.mean(10 ** (active / 10))))
    n = max(1, int(sr * 0.015))
    head = float(np.sqrt(np.mean(x[:n].astype(np.float64) ** 2))) / max(speech, 1e-9)
    tail = float(np.sqrt(np.mean(x[-n:].astype(np.float64) ** 2))) / max(speech, 1e-9)
    floor = 10 ** (float(np.percentile(db, 5)) / 20.0)
    floor = min(floor, speech * 10 ** (-C.COMFORT_BELOW_SPEECH_DB / 20.0))
    return head, tail, speech, floor


def longest_silence_ms(x: np.ndarray, sr: int, edges: Optional[Tuple[int, int]],
                       p: Optional[DspParams] = None) -> float:
    """Longest stretch of near-silence strictly inside the speech (between its first and last frame)."""
    if edges is None:
        return 0.0
    p = p or DspParams()
    db = frame_db(x, sr)
    peak = float(np.max(db))
    floor = float(np.percentile(db, 5))
    thr = max(peak - p.trim_threshold_db, floor + p.trim_floor_margin_db)
    n = frame_len(sr)
    a, b = edges[0] // n, edges[1] // n
    seg = db[a:b] <= thr
    best = run = 0
    for v in seg:
        run = run + 1 if v else 0
        best = max(best, run)
    return best * 10.0


NFFT = 4096


def tone_spectrum(x: np.ndarray, sr: int) -> Tuple[np.ndarray, int]:
    """Summed power spectrum of the speech blocks of x (for tone matching). Returns (sum, blocks)."""
    db = frame_db(x, sr)
    if float(np.max(db)) < -60:
        return np.zeros(NFFT // 2 + 1), 0
    n = frame_len(sr)
    k = len(db)
    keep = db > float(np.max(db)) - 30
    if len(x) // n < k:
        k = len(x) // n
    frames = x[: k * n].reshape(k, n)[keep[:k]]
    sp = frames.reshape(-1).astype(np.float64)[: sr * 20]
    blocks = len(sp) // NFFT
    if blocks < 1:
        return np.zeros(NFFT // 2 + 1), 0
    b = sp[: blocks * NFFT].reshape(blocks, NFFT) * np.hanning(NFFT)
    return np.sum(np.abs(np.fft.rfft(b, axis=1)) ** 2, axis=0), blocks


def tilt_db(power_sum: np.ndarray, blocks: int, sr: int) -> Optional[float]:
    """High-band (2-6 kHz) over low-band (100-1000 Hz) power in dB. None if there is too little speech."""
    if blocks < 2:
        return None
    f = np.fft.rfftfreq(NFFT, 1.0 / sr)
    hi = (f > 2000) & (f < min(6000, sr / 2 - 100))
    lo = (f > 100) & (f < 1000)
    a, b = float(np.mean(power_sum[hi])), float(np.mean(power_sum[lo]))
    if a <= 0 or b <= 0:
        return None
    return 10.0 * float(np.log10(a / b))


def apply_tilt(x: np.ndarray, sr: int, diff_db: float) -> np.ndarray:
    """Gentle high-shelf (centre 2 kHz) of diff_db; used to bring two mics/rooms closer together."""
    if abs(diff_db) < 0.05 or len(x) < 1024:
        return x
    spec = np.fft.rfft(x.astype(np.float64))
    f = np.fft.rfftfreq(len(x), 1.0 / sr)
    shelf = 1.0 / (1.0 + np.exp(-(f - 2000.0) / 500.0))
    y = np.fft.irfft(spec * 10 ** (diff_db * shelf / 20.0), n=len(x))
    return y.astype(np.float32)
