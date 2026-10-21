"""Visual media summaries and exact, non-destructive section audition."""

import numpy as np
import pytest

from make_audio import make
from mako_pipeline.config import SOUND_PRESETS, Settings, settings_from_dict
from mako_pipeline.ffmpeg import decode_audio
from mako_pipeline.merge import run_merge
from mako_pipeline.timeline import clip_catalog, _waveform


def test_catalog_keeps_excluded_clips_and_reports_corrupt_media(tmp_path):
    clean = tmp_path / 'clean'
    clean.mkdir()
    make(clean / 'K11.wav', dur=.5, pad=.1)
    make(clean / 'R11.wav', dur=.5, pad=.1)
    (clean / 'K12.wav').write_bytes(b'broken')
    (tmp_path / 'script.txt').write_text('1: K1', encoding='utf-8')
    catalog = clip_catalog(str(tmp_path), '1')
    assert [clip['token'] for clip in catalog] == ['K1', 'R1', 'K2']
    assert len(catalog[0]['peaks']) == 64
    assert catalog[0]['duration_s'] == pytest.approx(.7, abs=.01)
    assert max(catalog[0]['peaks']) > .1
    assert catalog[-1]['error'] and not catalog[-1]['peaks']


def test_waveform_cache_invalidates_when_source_changes(tmp_path, monkeypatch):
    import mako_pipeline.timeline as timeline
    clean = tmp_path / 'clean'
    clean.mkdir()
    clip = clean / 'K11.wav'
    clip.write_bytes(b'a')
    calls = []
    def decode(path, rate):
        calls.append(path)
        return np.zeros(8000, dtype=np.float32)
    _waveform.cache_clear()
    monkeypatch.setattr(timeline, 'decode_audio', decode)
    clip_catalog(str(tmp_path), 1)
    clip_catalog(str(tmp_path), 1)
    assert len(calls) == 1
    clip.write_bytes(b'changed')
    clip_catalog(str(tmp_path), 1)
    assert len(calls) == 2
    _waveform.cache_clear()


def test_section_preview_matches_full_export_without_saving(tmp_path):
    clean = tmp_path / 'clean'
    clean.mkdir()
    for token, amp in [('K11', .15), ('R11', .4), ('K21', .45), ('R21', .2)]:
        make(clean / (token + '.wav'), dur=.6, pad=.2, amp=amp)
    settings = Settings()
    cfg = settings.merge
    cfg.export_mp3 = False
    cfg.pacing = 'natural'
    cfg.match_loudness = True
    script = '1: K1 R1\n2: R1 K1'
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    preview = run_merge(tmp_path, cfg, script_text=script, override_text='', preview_section=2)
    assert preview.report.ok and preview.preview_audio is not None
    assert preview.out_dir is None
    assert {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()} == before
    exported = run_merge(tmp_path, cfg, script_text=script, override_text='')
    assert exported.report.ok
    actual = decode_audio(exported.out_dir / 'Section_02.wav', cfg.sample_rate)
    sr, audition = preview.preview_audio
    assert sr == cfg.sample_rate
    np.testing.assert_allclose(audition.astype(np.float32) / 32767, actual, atol=4e-5)


def test_missing_preview_section_fails_without_exports(tmp_path):
    clean = tmp_path / 'clean'
    clean.mkdir()
    make(clean / 'K11.wav', dur=.5)
    result = run_merge(tmp_path, Settings().merge, preview_section=2)
    assert result.report.failed and result.preview_audio is None
    assert not (tmp_path / 'merged').exists()


@pytest.mark.parametrize('preset', SOUND_PRESETS)
def test_sound_presets_are_valid_and_keep_folder_choices(preset):
    settings = Settings()
    settings.intake.project = 'my episode'
    settings.intake.speakers['koroush'].folder = 'my recordings'
    result = settings_from_dict(SOUND_PRESETS[preset], settings, strict=True)
    assert result.intake.project == 'my episode'
    assert result.intake.speakers['koroush'].folder == 'my recordings'


def test_visual_remove_restore_and_undo_preserve_explicit_exclusion(tmp_path):
    import json
    from mako_pipeline.ui import timeline_edit, _board_context
    from mako_pipeline.script import parse_script
    clean = tmp_path / 'clean'
    clean.mkdir()
    make(clean / 'K11.wav', dur=.4)
    def action(text, kind, history, token='K1'):
        context, revision = _board_context(str(tmp_path), text)
        payload = json.dumps(dict(action=kind, token=token, section='1', context=context, revision=revision))
        return timeline_edit(payload, str(tmp_path), text, '1', token, history)
    text, _, history, _ = action('1: K1', 'remove', {})
    assert parse_script(text).exact[1] == []
    assert clip_catalog(str(tmp_path), '1')[0]['token'] == 'K1'
    text, _, history, _ = action(text, 'undo', history)
    assert parse_script(text).exact[1] == [('koroush', 1)]
    text, _, history, _ = action(text, 'redo', history)
    assert parse_script(text).exact[1] == []
    text, _, _, _ = action(text, 'add', history)
    assert parse_script(text).exact[1] == [('koroush', 1)]


def test_visual_editor_rejects_stale_and_fabricated_drag_payloads(tmp_path):
    import json
    from mako_pipeline.ui import timeline_edit, _board_context
    text = '1: K1 R1'
    context, revision = _board_context(str(tmp_path), text)
    payload = dict(action='reorder', tokens=['R1', 'K1'], section='1', context=context, revision=revision)
    with pytest.raises(Exception, match='order changed'):
        timeline_edit(json.dumps(payload), str(tmp_path), '1: R1 K1', '1', 'K1', {})
    payload['tokens'] = ['K1', 'K1']
    with pytest.raises(Exception, match='Order changed'):
        timeline_edit(json.dumps(payload), str(tmp_path), text, '1', 'K1', {})
    with pytest.raises(Exception, match='order changed'):
        timeline_edit(json.dumps(payload), str(tmp_path / 'other'), text, '1', 'K1', {})


def test_visual_board_escapes_media_labels():
    from mako_pipeline.timeline_ui import render_board
    card = dict(token='K1', speaker='koroush', filename='<img src=x onerror=alert(1)>',
                duration_s=1, peaks=[.5] * 64, error='<script>alert(1)</script>')
    markup = render_board([card], ['K1'], '1')
    assert '<img src=x' not in markup and '<script>' not in markup
    assert '&lt;img' in markup and '&lt;script' in markup


def test_section_exclusion_does_not_reappear_in_export(tmp_path):
    clean = tmp_path / 'clean'
    clean.mkdir()
    make(clean / 'K11.wav', dur=.4)
    make(clean / 'K21.wav', dur=.4)
    result = run_merge(tmp_path, Settings().merge, dry_run=True, script_text='1: -\n2: K1')
    assert result.report.ok
    assert [row['section'] for row in result.rows] == [2]


def test_invalid_preview_selector_cannot_trigger_export(tmp_path, monkeypatch):
    from mako_pipeline.config import settings_to_dict
    from mako_pipeline.constants import STYLE_TO_LABEL
    import mako_pipeline.ui as ui
    st = Settings()
    st.intake.project = str(tmp_path)
    data = settings_to_dict(st)
    for speaker in ('koroush', 'reza'):
        data[speaker + '_style'] = STYLE_TO_LABEL[data[speaker + '_style']]
    def forbidden(*args, **kwargs):
        raise AssertionError('Invalid preview must not reach merge/export')
    monkeypatch.setattr(ui, 'run_merge', forbidden)
    with pytest.raises(ValueError, match='valid section'):
        ui.do_section_preview(*([data[key] for key in ui.KEYS] + ['', [], 'invalid']))


def test_rebuilding_template_preserves_excluded_sections(tmp_path):
    from mako_pipeline.merge import script_template
    from mako_pipeline.script import parse_script
    clean = tmp_path / 'clean'
    clean.mkdir()
    make(clean / 'K11.wav', dur=.4)
    make(clean / 'K21.wav', dur=.4)
    (tmp_path / 'script.txt').write_text('1: -\n2: K1', encoding='utf-8')
    _, text = script_template(tmp_path, Settings().merge)
    parsed = parse_script(text)
    assert not parsed.issues and parsed.exact[1] == []
