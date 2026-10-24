"""Projects outside the app folder must play without broad filesystem access."""
import shutil
from pathlib import Path
from types import SimpleNamespace

import gradio as gr
import pytest

from mako_pipeline.ui import cache_playback


def test_external_episode_audio_is_cached_without_changing_original(tmp_path):
    project = tmp_path / 'episode'
    source = project / 'merged' / 'run' / 'FULL.wav'
    source.parent.mkdir(parents=True)
    source.write_bytes(b'original audio bytes')
    cache = tmp_path / 'cache'
    cache.mkdir()
    def copy(path):
        return shutil.copy2(path, cache / Path(path).name)
    component = SimpleNamespace(move_resource_to_block_cache=copy)
    result = Path(cache_playback(str(source), str(project), component))
    assert result.parent == cache
    assert result.read_bytes() == source.read_bytes() == b'original audio bytes'


@pytest.mark.parametrize('relative', ['private.wav', 'merged/notes.txt', '../other-episode/clean/voice.wav'])
def test_playback_rejects_unrelated_files(tmp_path, relative):
    project = tmp_path / 'episode'
    project.mkdir()
    source = project / relative
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b'not selected episode audio')
    component = SimpleNamespace(move_resource_to_block_cache=lambda _: pytest.fail('Unrelated file must not be cached'))
    with pytest.raises(gr.Error, match='from this episode'):
        cache_playback(str(source), str(project), component)


def test_cleared_selection_clears_player():
    assert cache_playback(None, '', None) is None
