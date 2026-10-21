"""Read-only media summaries for the visual section editor."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np

from .ffmpeg import FFmpegMissing, decode_audio
from .merge import scan_clean
from .names import parse_sec_code


@lru_cache(maxsize=256)
def _waveform(path: str, size: int, modified_ns: int):
    # Cache only a small envelope, never the decoded recording.
    try:
        audio = decode_audio(path, 8000)
        if not np.isfinite(audio).all():
            raise ValueError('Recording contains invalid audio samples.')
        width = max(1, (len(audio) + 63) // 64)
        padded = np.pad(np.abs(audio), (0, width * 64 - len(audio)))
        peaks = np.max(padded.reshape(64, width), axis=1)
        return len(audio) / 8000, tuple(float(v) for v in np.clip(peaks, 0, 1)), ''
    except (OSError, RuntimeError, ValueError, FFmpegMissing) as exc:
        return 0.0, (), str(exc)


def clip_catalog(project: str, section) -> list[dict]:
    """List every prepared clip in a section, including clips omitted from its order."""
    if not project or section in (None, ''):
        return []
    number = parse_sec_code(str(section))
    if number is None or not 1 <= number <= 999:
        raise ValueError('Choose a valid section first.')
    clean = (Path(project) / 'clean').resolve()
    if not clean.is_dir():
        return []
    scan, _ = scan_clean(clean)
    clips = []
    for speaker, lines in scan.get(number, {}).items():
        for line, path in lines.items():
            resolved = path.resolve()
            if not resolved.is_relative_to(clean):
                duration, peaks, error = 0.0, (), 'Recording is outside this episode folder.'
            else:
                try:
                    stat = resolved.stat()
                    duration, peaks, error = _waveform(str(resolved), stat.st_size, stat.st_mtime_ns)
                except OSError as exc:
                    duration, peaks, error = 0.0, (), str(exc)
            clips.append(dict(token=('K' if speaker == 'koroush' else 'R') + str(line),
                              speaker=speaker, filename=path.name,
                              duration_s=round(duration, 3), peaks=list(peaks), error=error))
    return sorted(clips, key=lambda clip: (int(clip['token'][1:]), clip['token'][0]))
