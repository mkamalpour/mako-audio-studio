"""Keep verification independent of personal episode settings."""
import pytest
from pathlib import Path

from mako_pipeline import config, ffmpeg


def pytest_configure(config):
    # pytest creates basetemp itself, but expects its parent to exist on a fresh checkout.
    if config.option.basetemp:
        Path(config.option.basetemp).parent.mkdir(parents=True, exist_ok=True)


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "launcher_dir", lambda: tmp_path / "launcher")
    monkeypatch.delenv("MAKO_FFMPEG", raising=False)
    ffmpeg.reset_cache()
    yield
    ffmpeg.reset_cache()
