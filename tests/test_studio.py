import json
from pathlib import Path

import pytest

from mako_pipeline import cli
from mako_pipeline.config import IntakeConfig, SpeakerCfg, MergeConfig
from mako_pipeline.intake import run_intake
from mako_pipeline.merge import run_merge, build_order
from mako_pipeline.names import mako_parse, pattern_parse, build_clean_name, parse_clean
from mako_pipeline.report import Report
from mako_pipeline.script import parse_script
from make_audio import make


@pytest.mark.parametrize('stem,speaker,expected', [
    ('K11', 'koroush', (1, 1)), ('kBB', 'koroush', (11, 11)),
    ('Raa', 'reza', (10, 10)), ('kzz', 'koroush', (35, 35)),
    ('K01', 'koroush', None), ('K11', 'reza', None), ('K111', 'koroush', None)])
def test_mako_codes(stem, speaker, expected):
    assert mako_parse(stem, speaker) == expected


def test_all_code_roundtrips():
    for speaker in ('koroush', 'reza'):
        for section in range(1, 36):
            for line in range(1, 36):
                name = build_clean_name(section, line, speaker, '.wav')
                assert mako_parse(Path(name).stem, speaker) == (section, line)
                assert parse_clean(Path(name).stem) == (section, line, speaker)
    assert parse_clean('K36_101') == (36, 101, 'koroush')


@pytest.mark.parametrize('stem,pattern,expected', [
    ('S11', 's{section}{line}', (1, 1)), ('ab', '{section}{line}', (10, 11)),
    ('K11_11', 'K{section}_{line}', (11, 11)), ('11', '{section}{line}', (1, 1)),
    ('111', '{section}{line}', None), ('12-05-2024', '{section}_{line}', None)])
def test_custom_templates(stem, pattern, expected):
    assert pattern_parse(stem, pattern) == expected


def test_exact_arrangement_excludes_and_reorders():
    files = {1: {'koroush': {1: Path('K11.wav'), 2: Path('K12.wav')},
                 'reza': {1: Path('R11.wav'), 2: Path('R12.wav')}}}
    report = Report()
    order = build_order(files, MergeConfig(), parse_script('1: R2 K1 R1'), report)
    assert [(i.speaker, i.line) for i in order[1]] == [('reza', 2), ('koroush', 1), ('reza', 1)]
    assert any(i.code == 'W_EXTRA_LINE' for i in report.issues)
    assert parse_script('1: K1 K1').issues


def test_failed_replacement_keeps_previous_audio(tmp_path):
    raw = tmp_path / 'raw'
    raw.mkdir()
    source = raw / 'K11.wav'
    make(source, dur=.7)
    cfg = IntakeConfig(project=str(tmp_path / 'episode'))
    cfg.speakers['koroush'] = SpeakerCfg(folder=str(raw))
    assert run_intake(cfg).ok
    target = tmp_path / 'episode' / 'clean' / 'K11.wav'
    original = target.read_bytes()
    source.write_bytes(b'broken audio')
    assert run_intake(cfg).failed
    assert target.read_bytes() == original
    assert not list(target.parent.glob('.intake-*'))


def test_default_pipeline_and_add_repeat(tmp_path):
    raw = tmp_path / 'raw'
    raw.mkdir()
    make(raw / 'Kbb.wav', dur=.7)
    cfg = IntakeConfig(project=str(tmp_path / 'episode'))
    cfg.speakers['koroush'] = SpeakerCfg(folder=str(raw))
    assert run_intake(cfg).ok
    assert (tmp_path / 'episode' / 'clean' / 'Kbb.wav').exists()
    cfg.intake_mode = 'add'
    assert run_intake(cfg).ok
    result = run_merge(cfg.project, MergeConfig(export_mp3=False))
    assert result.report.ok
    assert result.rows[0]['section'] == 11 and result.rows[0]['line'] == 11
    assert (result.out_dir / 'FULL.wav').exists()


def test_raw_inside_clean_refused(tmp_path):
    raw = tmp_path / 'clean'
    raw.mkdir()
    make(raw / 'K11.wav', dur=.7)
    cfg = IntakeConfig(project=str(tmp_path))
    cfg.speakers['koroush'] = SpeakerCfg(folder=str(raw))
    before = (raw / 'K11.wav').read_bytes()
    assert run_intake(cfg).failed
    assert (raw / 'K11.wav').read_bytes() == before


def test_cli_json_and_dry_run_are_read_only(tmp_path, capsys):
    raw = tmp_path / 'raw'
    raw.mkdir()
    make(raw / 'K11.wav', dur=.7)
    episode = tmp_path / 'episode'
    args = ['--project', str(episode), '--koroush', str(raw), '--json']
    assert cli.main(['preview'] + args) == 0
    assert json.loads(capsys.readouterr().out)['stage'] == 'preview'
    assert cli.main(['all', '--dry-run', '--save-settings'] + args) == 0
    assert json.loads(capsys.readouterr().out)['stage'] == 'intake-preview'
    assert not episode.exists()


def test_install_failure_restores_archived_line(tmp_path, monkeypatch):
    import mako_pipeline.intake as intake
    raw = tmp_path / 'raw'
    raw.mkdir()
    make(raw / 'K11.wav', dur=.7)
    cfg = IntakeConfig(project=str(tmp_path / 'episode'))
    cfg.speakers['koroush'] = SpeakerCfg(folder=str(raw))
    assert run_intake(cfg).ok
    target = tmp_path / 'episode' / 'clean' / 'K11.wav'
    before = target.read_bytes()
    replace = intake.os.replace
    def fail_install(src, dst):
        if Path(dst) == target:
            raise OSError('simulated installation failure')
        return replace(src, dst)
    monkeypatch.setattr(intake.os, 'replace', fail_install)
    assert run_intake(cfg).failed
    assert target.read_bytes() == before


def test_invalid_script_refuses_export(tmp_path):
    clean = tmp_path / 'clean'
    clean.mkdir()
    make(clean / 'K11.wav', dur=.7)
    (tmp_path / 'script.txt').write_text('1: K1 K1', encoding='utf-8')
    result = run_merge(tmp_path, MergeConfig())
    assert result.report.failed
    assert not (tmp_path / 'merged').exists()


@pytest.mark.parametrize('value', ['NaN', 'inf', '1000'])
def test_invalid_gain_override_is_reported(tmp_path, value):
    from mako_pipeline.overrides import load_overrides
    path = tmp_path / 'overrides.csv'
    path.write_text('section,line,speaker,pause_before_ms,gain_db,trim,room_tone\n1,1,koroush,,' + value + ',,', encoding='utf-8')
    overrides, issues = load_overrides(path)
    assert issues and not overrides.line


def test_full_source_path_disambiguates_shared_names(tmp_path):
    from mako_pipeline.intake import RenameRow, plan_intake
    cfg = IntakeConfig(project=str(tmp_path / 'episode'))
    for speaker in ('koroush', 'reza'):
        folder = tmp_path / speaker
        folder.mkdir()
        (folder / '11.wav').write_bytes(b'preview does not decode')
        cfg.speakers[speaker] = SpeakerCfg(folder=str(folder), style='custom', pattern='{section}{line}')
    source = str((tmp_path / 'koroush' / '11.wav').resolve())
    plan = plan_intake(cfg, [RenameRow(source, 'include', 'reza', 2, 3)])
    assert {(i.folder_speaker, i.speaker, i.section, i.line) for i in plan.ok_items()} == {
        ('koroush', 'reza', 2, 3), ('reza', 'reza', 1, 1)}
