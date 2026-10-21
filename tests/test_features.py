import hashlib
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_audio import make  # noqa: E402
from mako_pipeline.config import IntakeConfig, MergeConfig, SpeakerCfg  # noqa: E402
from mako_pipeline.dsp import make_bed  # noqa: E402
from mako_pipeline.intake import run_intake  # noqa: E402
from mako_pipeline.merge import run_merge, script_template  # noqa: E402
from mako_pipeline.names import smart_parse  # noqa: E402


def _clean(tmp, n=6):
    d = tmp / "clean"
    d.mkdir(parents=True, exist_ok=True)
    i = 0
    for s in (1, 2):
        for ln in range(1, n + 1):
            for sp in ("koroush", "reza"):
                make(d / f"S{s}_{ln}_{sp}.mp3", dur=0.8 + 0.1 * (i % 4), amp=0.4, seed=i, pad=0.3)
                i += 1
    return tmp


def _sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def test_natural_pacing_is_seeded_and_varied(tmp_path):
    _clean(tmp_path)
    a = run_merge(tmp_path, MergeConfig(export_mp3=False))
    b = run_merge(tmp_path, MergeConfig(export_mp3=False))
    assert _sha(a.out_dir / "FULL.wav") == _sha(b.out_dir / "FULL.wav")
    c = run_merge(tmp_path, MergeConfig(export_mp3=False, seed=3))
    assert _sha(c.out_dir / "FULL.wav") != _sha(a.out_dir / "FULL.wav")
    gaps = {round(float(r[3])) for r in run_merge(tmp_path, MergeConfig(), dry_run=True).plan_rows}
    assert len(gaps) > 8  # a continuous spread, not a few fixed values


def test_talk_over_and_off_switch(tmp_path):
    _clean(tmp_path, 10)
    on = run_merge(tmp_path, MergeConfig(overlap_amount=3.0), dry_run=True)
    assert any(float(r[3]) < 0 for r in on.plan_rows)
    off = run_merge(tmp_path, MergeConfig(overlap_amount=0.0, min_gap_ms=60), dry_run=True)
    assert all(float(r[3]) >= 59 for r in off.plan_rows[1:] if r[2] != "0.00")


def test_override_negative_pause_is_exact(tmp_path):
    _clean(tmp_path, 2)
    (tmp_path / "overrides.csv").write_text("section,line,speaker,pause_before_ms\n1,1,reza,-80\n")
    rows = run_merge(tmp_path, MergeConfig(), dry_run=True).plan_rows
    assert abs(float(rows[1][3]) + 80) < 1


def test_comfort_noise_never_dead_silent():
    sr = 48000
    bed = make_bed(sr // 2, None, sr, floor=0.001, key=(7, 1, 2))
    assert 0.0005 < float(np.sqrt(np.mean(bed ** 2))) < 0.002
    assert np.array_equal(bed, make_bed(sr // 2, None, sr, floor=0.001, key=(7, 1, 2)))
    assert not make_bed(sr // 2, None, sr).any()  # no floor known: honest silence


def test_reconciliation_and_no_part_files(tmp_path):
    _clean(tmp_path, 2)
    (tmp_path / "clean" / "S1_9_koroush.mp3").write_bytes(b"not audio" * 50)
    res = run_merge(tmp_path, MergeConfig(export_wav=False))
    first = res.report.lines[0]
    assert first.startswith("Read ") and "skipped 1" in first
    n = res.report.counts
    assert n["lines_read"] == n["lines_placed"] + n["lines_skipped"]
    assert not list(res.out_dir.glob("*.part")) and not (res.out_dir / "INCOMPLETE.txt").exists()


def test_bad_settings_code(tmp_path):
    _clean(tmp_path, 1)
    res = run_merge(tmp_path, MergeConfig(gap_diff_ms=99999))
    assert any(i.code == "E_BAD_SETTINGS" for i in res.report.issues)


def test_tone_match_and_loudness_modes(tmp_path):
    _clean(tmp_path, 3)
    r = run_merge(tmp_path, MergeConfig(match_tone=True, loudness_mode="match", export_wav=False), dry_run=True)
    assert r.report.ok


def test_persian_digits_and_dates():
    assert smart_parse("۵.۶") == (5, 6)
    assert smart_parse("12-05-2024") is None


def test_intake_add_mode(tmp_path):
    k = tmp_path / "k"
    k.mkdir()
    make(k / "1.1.wav", dur=0.7)
    cfg = IntakeConfig(clean_format="mp3", project=str(tmp_path / "p"))
    cfg.speakers["koroush"] = SpeakerCfg(style="smart", folder=str(k))
    run_intake(cfg)
    make(k / "1.2.wav", dur=0.7)
    cfg.intake_mode = "add"
    before = _sha(tmp_path / "p" / "clean" / "K11.mp3")
    rep = run_intake(cfg)
    assert _sha(tmp_path / "p" / "clean" / "K11.mp3") == before
    assert (tmp_path / "p" / "clean" / "K12.mp3").exists() and rep.ok


def test_script_template_never_overwrites(tmp_path):
    _clean(tmp_path, 2)
    p, _ = script_template(tmp_path, MergeConfig())
    assert p.name == "script.txt"
    p2, _ = script_template(tmp_path, MergeConfig())
    assert p2.name == "script.suggested.txt"
