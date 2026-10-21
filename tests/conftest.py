"""Keep verification independent of personal episode settings."""
import pytest

from mako_pipeline import config, ffmpeg


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "launcher_dir", lambda: tmp_path / "launcher")
    monkeypatch.delenv("MAKO_FFMPEG", raising=False)
    ffmpeg.reset_cache()
    yield
    ffmpeg.reset_cache()
