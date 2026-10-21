"""Natural pacing: how long the pause (or talk-over) before each line is.

Real conversation does not use one fixed gap. Replies after a fast exchange stay fast, a slow
exchange stays slow, now and then someone takes a thinking beat, and now and then a reply starts
a little before the other speaker has quite finished. This module draws every transition from one
continuous (log-normal) distribution, so any value from "immediate" to "long beat" can occur and
there is no dead zone between a few fixed clusters.

Everything is seeded per transition (seed, section, position): the same inputs and settings give
the same pacing, and changing one line never re-rolls the dice for the others. Every number that
shapes the result is a setting (see config.MergeConfig) or a constant in constants.py.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import List

import numpy as np

from . import constants as C


@dataclass
class PacingState:
    """Rhythm that carries from one transition to the next (and across sections)."""
    eagerness: float = 0.3
    tempo: float = 1.0
    last_think: bool = False


@dataclass
class Transition:
    gap_ms: float          # speech-end to speech-start; negative = the next line starts early (talk-over)
    kind: str              # "pause" | "quick" | "think" | "talk-over"
    eagerness: float
    tempo: float
    how: str               # decision-log text


def speech_ms(c, sr: int) -> float:
    """Length of the speech itself (kept pads excluded)."""
    return max(0.0, (len(c.x) - c.lead - c.tail) * 1000.0 / sr)


def pick_profile(clips: List, sr: int, setting: str = "auto") -> str:
    """Profile name for the whole run: forced by the setting, or chosen from the median line length."""
    if setting in C.PACING_PROFILES:
        return setting
    med = float(np.median([speech_ms(c, sr) for c in clips])) if clips else 2000.0
    if med < C.PROFILE_TIGHT_BELOW_MS:
        return "tight"
    if med < C.PROFILE_NORMAL_BELOW_MS:
        return "normal"
    return "relaxed"


def decide(prev, cur, cfg, profile: str, st: PacingState, rng: random.Random, sr: int) -> Transition:
    p = C.PACING_PROFILES[profile]
    same = prev.speaker == cur.speaker
    cfg_base = cfg.gap_same_ms if same else cfg.gap_diff_ms
    base = cfg_base * p["scale"]

    # Eagerness: were the two takes cut tight at the joint? (smoothed so the rhythm has inertia)
    raw = float(np.clip((prev.tail_e + cur.head_e) / 2.0 / 1.2, 0.0, 1.0))
    carry = float(np.clip(C.RHYTHM_CARRY * cfg.rhythm_memory, 0.0, 0.95))
    eag = carry * st.eagerness + (1.0 - carry) * raw
    st.eagerness = eag

    # Slow tempo drift over the whole episode (mean-reverting random walk)
    step = C.TEMPO_DRIFT_STEP * cfg.tempo_drift
    if step > 0:
        t = st.tempo + rng.uniform(-step, step)
        t += (1.0 - t) * C.TEMPO_PULL_BACK
        st.tempo = float(np.clip(t, *C.TEMPO_DRIFT_RANGE))
    tempo = st.tempo

    head = (f"natural pacing, {profile} profile: {'gap_same_ms' if same else 'gap_diff_ms'}={cfg_base:g} "
            f"x{p['scale']:g}, eagerness {eag:.2f}, tempo {tempo:.2f}, seed {cfg.seed}")

    # Talk-over first: it is a different event from a pause (the next line starts early)
    if not same and cfg.overlap_amount > 0:
        prob = min(0.85, (p["overlap"] + 0.35 * eag) * cfg.overlap_amount)
        if rng.random() < prob:
            depth = min(C.OVERLAP_MIN_MS + C.OVERLAP_SPAN_MS * eag, cfg.overlap_max_ms,
                        0.35 * min(speech_ms(prev, sr), speech_ms(cur, sr)))
            if depth >= 10.0:
                return Transition(-depth, "talk-over", eag, tempo, head + f", talk-over {depth:.0f} ms")

    pause_base = max(4.0, base * tempo * (1.0 - 0.72 * eag))
    sigma = 0.55 + 0.35 * p["spread"]
    mu = 0.0
    think = False
    if not st.last_think and eag < 0.5:
        think = rng.random() < min(0.9, C.THINK_PAUSE_PROB * cfg.thinking_pause_amount)
    st.last_think = think
    if think:
        mu += rng.uniform(0.5, 1.1)
    quick = rng.random() < min(0.6, p["quick"] * cfg.quick_reply_amount)
    if quick:
        mu -= rng.uniform(0.6, 1.3)
    gap = pause_base * rng.lognormvariate(mu, sigma)
    gap = min(gap, max(base * 6.0, cfg.min_gap_ms))
    gap = max(gap, cfg.min_gap_ms)
    kind = "think" if think else ("quick" if quick else "pause")
    return Transition(gap, kind, eag, tempo, head)
