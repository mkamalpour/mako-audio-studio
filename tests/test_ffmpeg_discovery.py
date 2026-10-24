"""Encoder discovery must work without Conda activation or network access."""
import pytest

from mako_pipeline import ffmpeg


def test_conda_encoder_found_without_activation(tmp_path, monkeypatch):
    encoder = tmp_path / "Library" / "bin" / "ffmpeg.exe"
    encoder.parent.mkdir(parents=True)
    encoder.write_bytes(b"discovery fixture; never executed")
    monkeypatch.setattr(ffmpeg.sys, "prefix", str(tmp_path))
    monkeypatch.setattr(ffmpeg, "_imageio_path", lambda: None)
    monkeypatch.setattr(ffmpeg.shutil, "which", lambda _: None)
    assert ffmpeg.find_ffmpeg() == str(encoder)


def test_directory_override_does_not_count_as_encoder(tmp_path, monkeypatch):
    monkeypatch.setattr(ffmpeg, "_candidates", lambda: [str(tmp_path)])
    with pytest.raises(ffmpeg.FFmpegMissing, match="MAKO_FFMPEG"):
        ffmpeg.find_ffmpeg()


def test_deleted_cached_encoder_is_rediscovered(tmp_path, monkeypatch):
    old = tmp_path / "old.exe"
    old.write_bytes(b"old")
    replacement = tmp_path / "new.exe"
    replacement.write_bytes(b"new")
    monkeypatch.setattr(ffmpeg, "_candidates", lambda: [str(old), str(replacement)])
    assert ffmpeg.find_ffmpeg() == str(old)
    old.unlink()
    assert ffmpeg.find_ffmpeg() == str(replacement)
