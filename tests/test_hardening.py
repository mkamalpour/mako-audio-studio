"""Final audio safety, bounded analysis and interrupted export regressions."""
import json
from types import SimpleNamespace

import numpy as np
import pytest

from make_audio import make
from mako_pipeline.config import IntakeConfig, SpeakerCfg, MergeConfig
from mako_pipeline.dsp import measure_lufs, measure_lufs_many
from mako_pipeline.ffmpeg import decode_audio, encode_stream, raw_chunks
from mako_pipeline.intake import run_intake
from mako_pipeline.merge import run_merge


@pytest.mark.parametrize('sr', [44100, 48000])
@pytest.mark.parametrize('duration', [1.01, 2.37])
def test_streamed_loudness_matches_reference_meter(sr, duration):
    rng = np.random.default_rng(5)
    audio = rng.normal(0, .1, int(sr * duration)).astype(np.float32)
    audio[:sr // 4] *= .001
    parts = [audio[:123], audio[123:sr // 3], audio[sr // 3:]]
    expected, _ = measure_lufs(audio, sr)
    actual, engine = measure_lufs_many(parts, sr)
    assert 'streaming' in engine
    assert actual == pytest.approx(expected, abs=1e-8)


def test_loudness_analysis_has_bounded_working_chunks(monkeypatch):
    import mako_pipeline.dsp as dsp
    audio = np.random.default_rng(3).normal(0, .1, 480000).astype(np.float32)
    original = np.concatenate
    sizes = []
    def measured(arrays, *args, **kwargs):
        result = original(arrays, *args, **kwargs)
        sizes.append(result.size)
        return result
    monkeypatch.setattr(dsp.np, 'concatenate', measured)
    assert measure_lufs_many([audio], 48000)[0] is not None
    assert max(sizes) < 100000  # no whole-recording or whole-speaker concatenation


def test_silence_is_not_automatically_amplified():
    assert measure_lufs_many([np.zeros(48000, np.float32)], 48000)[0] is None


def test_overlapping_mix_and_stems_receive_same_safe_gain(tmp_path):
    clean = tmp_path / 'clean'
    clean.mkdir()
    for name in ('K11', 'R11'):
        make(clean / (name + '.wav'), dur=.8, pad=.1, amp=.95)
    cfg = MergeConfig(match_loudness=False, trim=False, room_tone=False,
                      comfort_noise=False, export_mp3=False, export_stems=True, edge_fade_ms=0)
    overrides = 'section,line,speaker,pause_before_ms,gain_db,trim,room_tone\n1,1,reza,-400,,,'
    preview = run_merge(tmp_path, cfg, script_text='1: K1 R1', override_text=overrides, preview_section=1)
    result = run_merge(tmp_path, cfg, script_text='1: K1 R1', override_text=overrides)
    assert result.report.ok and result.master_gain_db < 0
    out = result.out_dir
    mix = decode_audio(out / 'FULL.wav')
    stems = [decode_audio(out / ('FULL_' + speaker + '.wav')) for speaker in ('koroush', 'reza')]
    ceiling = 10 ** (cfg.peak_ceiling_dbfs / 20)
    assert max(np.max(np.abs(track)) for track in [mix] + stems) <= ceiling + 2e-6
    np.testing.assert_allclose(stems[0] + stems[1], mix, atol=5e-7)
    np.testing.assert_allclose(preview.preview_audio[1].astype(float) / 32767, mix, atol=4e-5)
    manifest = json.loads((out / 'manifest.json').read_text())
    assert manifest['master_gain_db'] == result.master_gain_db


def test_failed_audio_iterator_preserves_destination_and_removes_partial(tmp_path):
    output = tmp_path / 'audio.wav'
    output.write_bytes(b'previous completed export')
    def broken():
        yield np.zeros(4800, np.float32)
        raise RuntimeError('simulated source failure')
    with pytest.raises(RuntimeError, match='simulated source failure'):
        encode_stream(broken(), 48000, output, 'wav')
    assert output.read_bytes() == b'previous completed export'
    assert not (tmp_path / 'audio.wav.part').exists()


def test_encoder_refuses_non_finite_samples(tmp_path):
    with pytest.raises(ValueError, match='non-finite'):
        encode_stream([np.array([0, np.nan], np.float32)], 48000, tmp_path / 'audio.wav', 'wav')
    assert not list(tmp_path.glob('*.part'))


def test_decoder_refuses_non_finite_samples(monkeypatch):
    import mako_pipeline.ffmpeg as ffmpeg
    monkeypatch.setattr(ffmpeg, '_run', lambda args: SimpleNamespace(
        returncode=0, stdout=np.array([np.nan, 0], np.float32).tobytes(), stderr=b''))
    with pytest.raises(RuntimeError, match='non-finite'):
        decode_audio('unused.wav')


def test_truncated_raw_export_buffer_fails_instead_of_shortening_audio(tmp_path):
    path = tmp_path / 'audio.f32'
    np.zeros(10, np.float32).tofile(path)
    with pytest.raises(RuntimeError, match='ended early'):
        list(raw_chunks(path, 20))


def test_add_mode_recognizes_legacy_names_as_existing_coordinates(tmp_path):
    raw = tmp_path / 'raw'
    clean = tmp_path / 'episode' / 'clean'
    raw.mkdir()
    clean.mkdir(parents=True)
    make(raw / 'K11.wav', dur=.5)
    legacy = clean / 'S1_1_koroush.wav'
    legacy.write_bytes((raw / 'K11.wav').read_bytes())
    cfg = IntakeConfig(project=str(clean.parent), intake_mode='add')
    cfg.speakers['koroush'] = SpeakerCfg(folder=str(raw))
    result = run_intake(cfg)
    assert result.ok and result.counts['koroush']['skipped'] == 1
    assert list(clean.iterdir()) == [legacy]


@pytest.mark.parametrize('change', ['order', 'gap', 'section', 'project'])
def test_late_preview_is_not_playable_after_form_changes(tmp_path, change):
    from mako_pipeline.config import Settings, settings_to_dict
    from mako_pipeline.constants import STYLE_TO_LABEL
    from mako_pipeline.ui import KEYS, preview_signature, publish_section_preview
    st = Settings()
    st.intake.project = str(tmp_path)
    data = settings_to_dict(st)
    for speaker in ('koroush', 'reza'):
        data[speaker + '_style'] = STYLE_TO_LABEL[data[speaker + '_style']]
    values = [data[key] for key in KEYS] + ['1: K1 R1', [], '1']
    audio = (48000, np.zeros(100, np.int16))
    pending = dict(signature=preview_signature(values), audio=audio, status='Ready')
    assert publish_section_preview(pending, *values)[0] is audio
    if change == 'order': values[len(KEYS)] = '1: R1 K1'
    elif change == 'gap': values[KEYS.index('gap_diff_ms')] = 700
    elif change == 'section': values[-1] = '2'
    else: values[KEYS.index('project')] = str(tmp_path / 'another-episode')
    result, message = publish_section_preview(pending, *values)
    assert result is None and 'changed during rendering' in message


def test_invalid_mp3_bitrate_is_rejected_before_processing():
    with pytest.raises(ValueError, match='mp3_bitrate'):
        MergeConfig(mp3_bitrate='typo').validate()


def test_mp3_encoder_matches_installed_ffmpeg(monkeypatch):
    from types import SimpleNamespace
    from mako_pipeline import ffmpeg
    for listing, expected in [(b" A....D mp3_mf MP3 via MediaFoundation\n", "mp3_mf"), (b" A....D mp3_mf Native\n A....D libmp3lame LAME\n", "libmp3lame")]:
        ffmpeg.reset_cache()
        monkeypatch.setattr(ffmpeg, "_run", lambda args, data=None: SimpleNamespace(stdout=listing, stderr=b"", returncode=0))
        assert ffmpeg._mp3_encoder() == expected
    ffmpeg.reset_cache()
    monkeypatch.setattr(ffmpeg, "_run", lambda args, data=None: SimpleNamespace(stdout=b" A....D pcm_s24le PCM\n", stderr=b"", returncode=0))
    import pytest
    with pytest.raises(RuntimeError, match="Select WAV"):
        ffmpeg._mp3_encoder()
