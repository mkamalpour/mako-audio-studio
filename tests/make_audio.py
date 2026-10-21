"""Synthetic speech-like clips (spec 14.1)."""
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mako_pipeline.ffmpeg import find_ffmpeg  # noqa: E402


def speech(dur=1.5, freq=220.0, amp=0.5, sr=48000, pad=0.6, seed=1):
    rng = np.random.default_rng(seed)
    n = int(dur * sr)
    t = np.arange(n) / sr
    env = 0.55 + 0.45 * np.sin(2 * np.pi * 3 * t)
    a = int(0.02 * sr)
    env[:a] *= np.linspace(0, 1, a)
    env[-a:] *= np.linspace(1, 0, a)
    body = amp * env * np.sin(2 * np.pi * freq * t)
    p = int(pad * sr)
    noise = lambda k: rng.normal(0, 0.002, k)  # noqa: E731
    return np.concatenate([noise(p), body, noise(p)]).astype(np.float32)


def write_wav(path, x, sr=48000):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())


def make(path, ext=None, **kw):
    """Write a synthetic clip; format is chosen by the extension (mp3, m4a, mp4, ogg, wav)."""
    path = Path(path)
    ext = (ext or path.suffix).lower().lstrip(".")
    x = speech(**kw)
    if ext == "wav":
        write_wav(path, x)
        return path
    tmp = path.with_name(path.stem + "__tmp.wav")
    write_wav(tmp, x)
    ff = find_ffmpeg()
    if ext == "mp4":
        dur = len(x) / 48000
        cmd = [ff, "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=black:s=64x64:d={dur}:r=10",
               "-i", str(tmp), "-c:v", "mpeg4", "-c:a", "aac", "-shortest", "-f", "mp4", str(path)]
    else:
        cmd = [ff, "-v", "error", "-y", "-i", str(tmp), str(path)]
    subprocess.run(cmd, check=True)
    tmp.unlink()
    return path
