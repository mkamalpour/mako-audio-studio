"""FFmpeg access: decode, encode, convert. All calls pass argument lists (never shell strings)."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Iterable, Iterator, List, Optional, Tuple

import numpy as np

from .constants import FFMPEG_HINT
from .util import part_path

CHUNK_SAMPLES = 1 << 20


class FFmpegMissing(Exception):
    """Raised when no FFmpeg binary can be found."""


_CACHE = {"path": None, "mp3_encoder": None}  # the only hidden global state: the cached FFmpeg path


def reset_cache() -> None:
    _CACHE["path"] = None
    _CACHE["mp3_encoder"] = None


def _imageio_path() -> Optional[str]:
    try:
        import imageio_ffmpeg  # type: ignore
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def _candidates() -> List[str]:
    """Explicit override, bundled encoder, then system/local fallbacks."""
    out: List[str] = []
    env = os.environ.get("MAKO_FFMPEG")
    if env:
        out.append(env)
    io = _imageio_path()
    if io:
        out.append(io)
    w = shutil.which("ffmpeg")
    if w:
        out.append(w)
    # Calling an environment's python.exe directly does not activate Conda's
    # Library/bin PATH entry. Reuse its installed encoder without a download.
    prefix = Path(sys.prefix)
    for relative in ("Library/bin/ffmpeg.exe", "bin/ffmpeg", "Scripts/ffmpeg.exe"):
        candidate = prefix / relative
        if candidate.is_file():
            out.append(str(candidate))
    here = Path(__file__).resolve().parent.parent
    for name in ("ffmpeg.exe", "ffmpeg"):
        p = here / name
        if p.is_file():
            out.append(str(p))
    return out


def find_ffmpeg() -> str:
    """Return the FFmpeg path or raise FFmpegMissing."""
    if _CACHE["path"] and os.path.isfile(_CACHE["path"]):
        return _CACHE["path"]
    reset_cache()
    for c in _candidates():
        if c and os.path.isfile(c):
            _CACHE["path"] = c
            return c
    raise FFmpegMissing("FFmpeg was not found. " + FFMPEG_HINT)


def _flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def _run(args: List[str], data: Optional[bytes] = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [find_ffmpeg()] + args, input=data,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=_flags(),
    )


def _mp3_encoder() -> str:
    """Use an encoder actually present in this FFmpeg build, including Windows MF."""
    if _CACHE.get("mp3_encoder"):
        return _CACHE["mp3_encoder"]
    result = _run(["-hide_banner", "-encoders"])
    listing = (result.stdout or b"").decode("utf-8", "replace")
    for name in ("libmp3lame", "mp3_mf"):
        if any(len(line.split()) > 1 and line.split()[1] == name for line in listing.splitlines()):
            _CACHE["mp3_encoder"] = name
            return name
    raise RuntimeError("This FFmpeg has no supported MP3 encoder. Select WAV output or choose an existing FFmpeg with libmp3lame or mp3_mf.")


def _last_err(cp: subprocess.CompletedProcess) -> str:
    txt = (cp.stderr or b"").decode("utf-8", "replace").strip().splitlines()
    return (txt[-1] if txt else "no details")[:120]


def decode_audio(path, sr: int = 48000) -> np.ndarray:
    """Decode any audio/video file to float32 mono at `sr` (decoded once per file)."""
    cp = _run(["-v", "error", "-nostdin", "-i", str(Path(path).resolve()), "-vn",
               "-ac", "1", "-ar", str(sr), "-f", "f32le", "pipe:1"])
    buf = cp.stdout or b""
    if cp.returncode != 0 or len(buf) < 4:
        raise RuntimeError(
            "can't be decoded - the file is corrupt, incomplete, or not audio "
            f"[{_last_err(cp)}]"
        )
    audio = np.frombuffer(buf[: len(buf) // 4 * 4], dtype="<f4").astype(np.float32)
    if not np.isfinite(audio).all():
        raise RuntimeError('Recording contains non-finite audio samples; re-export it from the recorder.')
    return audio


def duration_s(path) -> float:
    """Duration measured by decoding at 16 kHz mono (used for verification)."""
    return len(decode_audio(path, 16000)) / 16000.0


def convert_file(src, dst, fmt: str = "mp3", bitrate: str = "320k") -> Tuple[bool, str]:
    """Convert the first audio stream of src to dst. Keeps sample rate and channel count.

    Written as dst.part and renamed on success, so a crash never leaves a half file under the real name.
    """
    dst = Path(dst)
    tmp = part_path(dst)
    base = ["-v", "error", "-nostdin", "-y", "-i", str(Path(src).resolve()),
            "-map", "0:a:0", "-vn", "-sn", "-dn", "-map_metadata", "-1"]
    if fmt == "wav":
        codec = ["-c:a", "pcm_s24le", "-f", "wav"]
    else:
        codec = ["-c:a", _mp3_encoder(), "-b:a", bitrate, "-f", "mp3"]
    attempts = [[], ["-ar", "48000"]] if fmt != "wav" else [[]]
    err = ""
    for extra in attempts:  # libmp3lame only supports some sample rates: retry at 48 kHz
        cp = _run(base + codec + extra + ["-fflags", "+bitexact", str(tmp)])
        if cp.returncode == 0 and tmp.exists() and tmp.stat().st_size > 0:
            os.replace(tmp, dst)
            return True, ""
        err = _last_err(cp)
    tmp.unlink(missing_ok=True)
    return False, err


def _to_s16(x: np.ndarray) -> bytes:
    return (np.clip(x, -1.0, 1.0) * 32767.0).round().astype("<i2").tobytes()


def _to_f32(x: np.ndarray) -> bytes:
    return np.clip(x, -1.0, 1.0).astype("<f4").tobytes()


def encode_stream(chunks: Iterable[np.ndarray], sr: int, out, fmt: str = "mp3",
                  bitrate: str = "320k", bit_depth: int = 24) -> None:
    """Encode float32 mono chunks to mp3/wav with one FFmpeg process (one encode)."""
    out = Path(out)
    tmp = part_path(out)
    if fmt == "mp3":  # one conversion to 16-bit for the MP3 pipe
        inp, conv = "s16le", _to_s16
        codec = ["-c:a", _mp3_encoder(), "-b:a", bitrate, "-f", "mp3"]
    else:
        inp, conv = "f32le", _to_f32
        codec = ["-c:a", "pcm_s24le" if bit_depth == 24 else "pcm_s16le", "-f", "wav"]
    args = [find_ffmpeg(), "-v", "error", "-y", "-f", inp, "-ar", str(sr), "-ac", "1",
            "-i", "pipe:0"] + codec + ["-fflags", "+bitexact", str(tmp)]
    p = None
    try:
        with tempfile.TemporaryFile() as errf:
            p = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                 stderr=errf, creationflags=_flags())
            broken = False
            try:
                for ch in chunks:
                    if len(ch):
                        if not np.isfinite(ch).all():
                            raise ValueError('Cannot encode non-finite audio samples.')
                        p.stdin.write(conv(ch))
            except BrokenPipeError:
                broken = True
            finally:
                try:
                    p.stdin.close()
                except BrokenPipeError:
                    broken = True
            rc = p.wait(timeout=120)
            if rc != 0 or broken:
                errf.seek(0)
                msg = errf.read().decode("utf-8", "replace").strip().splitlines()
                raise RuntimeError(f"encoding {out.name} failed: {(msg[-1] if msg else 'encoder pipe closed')[:120]}")
        os.replace(tmp, out)
    except BaseException:
        if p is not None and p.poll() is None:
            p.kill()
            p.wait()
        tmp.unlink(missing_ok=True)
        raise


def encode_audio(x: np.ndarray, sr: int, out, fmt: str = "mp3",
                 bitrate: str = "320k", bit_depth: int = 24) -> None:
    encode_stream(_chunks(x), sr, out, fmt, bitrate, bit_depth)


def _chunks(x: np.ndarray) -> Iterator[np.ndarray]:
    for i in range(0, len(x), CHUNK_SAMPLES):
        yield x[i:i + CHUNK_SAMPLES]


def raw_chunks(raw_path, n_samples: int) -> Iterator[np.ndarray]:
    """Stream a raw float32 file in chunks (used for the on-disk FULL buffer)."""
    with open(raw_path, "rb") as f:
        left = n_samples
        while left > 0:
            a = np.fromfile(f, dtype="<f4", count=min(CHUNK_SAMPLES, left))
            if len(a) == 0:
                raise RuntimeError('Temporary audio buffer ended early; export was not completed.')
            left -= len(a)
            yield a


def encode_raw(raw_path, n_samples: int, sr: int, out, fmt: str = "mp3",
               bitrate: str = "320k", bit_depth: int = 24) -> None:
    encode_stream(raw_chunks(raw_path, n_samples), sr, out, fmt, bitrate, bit_depth)


def check_environment() -> dict:
    """Environment summary for the UI header and `check` command."""
    info = {"ffmpeg": None, "ffmpeg_error": None, "loudness": "approx LUFS (install pyloudnorm for exact)"}
    try:
        info["ffmpeg"] = find_ffmpeg()
    except FFmpegMissing as e:
        info["ffmpeg_error"] = str(e)
    try:
        import pyloudnorm  # noqa: F401
        info["loudness"] = "exact BS.1770 (pyloudnorm)"
    except Exception:
        pass
    return info
