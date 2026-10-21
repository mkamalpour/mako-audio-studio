import csv
import hashlib
import os
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from make_audio import make  # noqa: E402
from mako_pipeline import cli, ffmpeg  # noqa: E402
from mako_pipeline.config import IntakeConfig, MergeConfig, SpeakerCfg  # noqa: E402
from mako_pipeline.ffmpeg import decode_audio  # noqa: E402
from mako_pipeline.intake import plan_intake, run_intake  # noqa: E402
from mako_pipeline.merge import Clip, place_section, run_merge, srt_time  # noqa: E402
from mako_pipeline.dsp import measure_lufs  # noqa: E402

SR = 48000


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def tree_hash(d):
    return {p.relative_to(d).as_posix(): sha(p) for p in sorted(Path(d).rglob("*")) if p.is_file()}


def setup_clean(tmp, spec, **kw):
    """spec: list of (section, line, speaker, amp). Writes mp3 clean files."""
    clean = tmp / "clean"
    clean.mkdir(parents=True, exist_ok=True)
    for i, (s, ln, sp, amp) in enumerate(spec):
        code = {10: "a"}.get(s, str(s))
        make(clean / f"S{code}_{ln}_{sp}.mp3", dur=0.8 + 0.1 * (i % 3), amp=amp, seed=i, freq=200 + 40 * (sp == "reza"))
    return tmp


# ---------------------------------------------------------------- intake
def test_a1_a2_intake(tmp_path):
    k, r = tmp_path / "k", tmp_path / "r"
    k.mkdir(), r.mkdir()
    import time
    make(k / "5.6.mp4", dur=0.7)
    time.sleep(0.05)
    make(k / "5.6 - Copy.mp4", dur=0.8, seed=3)
    for n in ("5.7.m4a", "5.10.mp3", "s6.1.wav", "S10-1.ogg", "hello.mp3"):
        make(k / n, dur=0.7)
    for i in range(1, 7):
        make(r / f"{i}.mp3", dur=0.7, seed=i)
    before = tree_hash(tmp_path)
    cfg = IntakeConfig(clean_format="mp3", project=str(tmp_path / "p"))
    cfg.speakers["koroush"] = SpeakerCfg(style="smart", folder=str(k))
    cfg.speakers["reza"] = SpeakerCfg(folder=str(r), style="sequence", sections_text="3,2,1")
    plan = plan_intake(cfg)
    assert any(i.src.name == "hello.mp3" and i.status == "unreadable" for i in plan.items)
    rep = run_intake(cfg)
    clean = tmp_path / "p" / "clean"
    names = sorted(p.name for p in clean.iterdir() if p.is_file())
    assert names == sorted(["K56.mp3", "K57.mp3", "K5a.mp3", "K61.mp3",
                            "Ka1.mp3", "R11.mp3", "R12.mp3", "R13.mp3",
                            "R21.mp3", "R22.mp3", "R31.mp3"])
    assert len(list((clean / "_duplicates").iterdir())) == 1
    assert sha(clean / "R11.mp3") == sha(r / "1.mp3")  # copied byte-for-byte (I4)
    assert tree_hash(tmp_path)["k/5.6.mp4"] == before["k/5.6.mp4"]
    assert {p: h for p, h in tree_hash(tmp_path).items() if p.startswith(("k/", "r/"))} == before  # I1
    run_intake(cfg)  # A2 re-run
    assert list((clean / "_replaced").iterdir())
    assert len([p for p in clean.iterdir() if p.is_file()]) == 11
    assert rep.exit_code() == 1


def test_a3_corrupt_and_a12_mp4(tmp_path):
    k = tmp_path / "k"
    k.mkdir()
    (k / "5.9.mp3").write_bytes(os.urandom(4000))
    make(k / "5.1.mp4", dur=0.8)
    cfg = IntakeConfig(clean_format="mp3", project=str(tmp_path / "p"))
    cfg.speakers["koroush"] = SpeakerCfg(style="smart", folder=str(k))
    rep = run_intake(cfg)
    assert any(i.code in ("E_DECODE", "E_CONVERT") for i in rep.issues)
    out = tmp_path / "p" / "clean" / "K51.mp3"
    assert out.exists() and abs(len(decode_audio(out, 16000)) / 16000 - 2.0) < 0.15 + 0.05


def test_a8_no_ffmpeg(tmp_path, monkeypatch):
    monkeypatch.setattr(ffmpeg, "_candidates", lambda: [])
    ffmpeg.reset_cache()
    rep = run_intake(IntakeConfig(clean_format="mp3", project=str(tmp_path)))
    ffmpeg.reset_cache()
    assert rep.failed and rep.issues[0].code == "E_FFMPEG_MISSING" and "imageio-ffmpeg" in rep.issues[0].message


def test_a9_unicode(tmp_path):
    k = tmp_path / "پوشه ی من"
    k.mkdir()
    make(k / "5.1 سلام.wav", dur=0.7)
    cfg = IntakeConfig(clean_format="mp3", project=str(tmp_path / "پروژه 1"))
    cfg.speakers["koroush"] = SpeakerCfg(style="smart", folder=str(k))
    assert run_intake(cfg).ok
    assert run_merge(cfg.project, MergeConfig(export_wav=False)).report.ok


# ---------------------------------------------------------------- merge
def test_a4_a6_a7_merge(tmp_path):
    spec = []
    for s in (1, 2):
        for n in (1, 2, 3):
            spec += [(s, n, "koroush", 0.5), (s, n, "reza", 0.08)]
    setup_clean(tmp_path, spec)
    cfg = MergeConfig(starter_exceptions="2:reza", export_stems=True, pacing="fixed")
    res = run_merge(tmp_path, cfg)
    out = res.out_dir
    rows = list(csv.DictReader(open(out / "timeline.csv")))
    sec2 = [r["speaker"] for r in rows if r["section"] == "2"]
    assert sec2[:2] == ["reza", "koroush"]
    full = decode_audio(out / "FULL.wav", SR)
    for sp in ("koroush", "reza"):
        st = decode_audio(out / f"FULL_{sp}.wav", SR)
        assert len(st) == len(full)
    kst = decode_audio(out / "FULL_koroush.wav", SR)
    r0 = rows[1]  # reza line in section 1
    a, b = int(float(r0["full_start_s"]) * SR) + 100, int(float(r0["full_end_s"]) * SR) - 100
    assert not kst[a:b].any()  # zero where the other speaks
    kk = np.concatenate([kst[int(float(r["full_start_s"]) * SR):int(float(r["full_end_s"]) * SR)]
                         for r in rows if r["speaker"] == "koroush"])
    rr = decode_audio(out / "FULL_reza.wav", SR)
    rz = np.concatenate([rr[int(float(r["full_start_s"]) * SR):int(float(r["full_end_s"]) * SR)]
                         for r in rows if r["speaker"] == "reza"])
    lk, lr = measure_lufs(kk, SR)[0], measure_lufs(rz, SR)[0]
    assert abs(lk + 16) < 0.6 and abs(lr + 16) < 0.6
    assert abs(float(rows[-1]["full_end_s"]) - len(full) / SR) < 0.01
    srt = (out / "FULL.srt").read_text().split("\n\n")
    assert srt[0].splitlines()[1].startswith("00:00:00,000 --> ")
    assert srt_time(float(rows[1]["full_start_s"])) in srt[1]
    assert (out / "manifest.json").exists() and (out / "FULL_labels.txt").exists()
    res2 = run_merge(tmp_path, cfg)  # A6 determinism + I3
    assert res2.out_dir != out
    assert sha(res2.out_dir / "FULL.wav") == sha(out / "FULL.wav")
    res3 = run_merge(tmp_path, MergeConfig(seed=99))
    assert sha(res3.out_dir / "FULL.wav") != sha(out / "FULL.wav")


def test_a5_missing_and_corrupt(tmp_path):
    setup_clean(tmp_path, [(1, 1, "koroush", .4), (1, 1, "reza", .4), (2, 1, "koroush", .4),
                           (2, 4, "koroush", .4), (2, 1, "reza", .4), (3, 1, "koroush", .4)])
    (tmp_path / "clean" / "S1_2_koroush.mp3").write_bytes(os.urandom(3000))
    (tmp_path / "clean" / "junk.mp3").write_bytes(b"x")
    res = run_merge(tmp_path, MergeConfig(export_wav=False))
    codes = {i.code for i in res.report.issues}
    assert {"W_MISSING_LINE", "W_NO_AUDIO_SPEAKER", "W_SKIPPED_FILE", "W_IGNORED_FILE"} <= codes
    assert res.out_dir and (res.out_dir / "FULL.mp3").exists()


def test_a13_script_order(tmp_path):
    spec = [(3, n, sp, .4) for n in (1, 2, 3) for sp in ("koroush", "reza") if not (sp == "koroush" and n == 3)]
    setup_clean(tmp_path, spec)
    (tmp_path / "script.txt").write_text("3: K R R K R\n")
    res = run_merge(tmp_path, MergeConfig(export_wav=False), dry_run=True)
    order = [(r[1]) for r in res.plan_rows]
    assert order == ["S3_1_koroush.mp3", "S3_1_reza.mp3", "S3_2_reza.mp3", "S3_2_koroush.mp3", "S3_3_reza.mp3"]
    assert res.plan_rows[2][5].startswith("≠") or res.plan_rows[3][5].startswith("≠")
    (tmp_path / "script.txt").write_text("3: K R K\n")  # missing + extra
    res = run_merge(tmp_path, MergeConfig(export_wav=False), dry_run=True)
    assert {"W_MISSING_LINE", "W_EXTRA_LINE"} & {i.code for i in res.report.issues}


def test_pause_exact():
    sr = 48000
    mk = lambda sp, lead, tail: Clip(sp, 1, Path("x.mp3"), np.zeros(sr // 2, np.float32), lead, tail, None, 1)  # noqa: E731
    cfg = MergeConfig(variation_pct=0, pacing="fixed")
    a, b = mk("koroush", 1920, 3840), mk("reza", 1920, 3840)
    pl = place_section(1, [a, b], cfg)
    assert abs(pl.places[1].gap_ms - 300) < 1
    b.pause_ms = 120.0
    assert abs(place_section(1, [a, b], cfg).places[1].gap_ms - 120) < 1
    c1 = place_section(1, [a, b], MergeConfig(seed=5, pacing="fixed")).places[1].target_ms
    assert c1 == place_section(1, [a, b], MergeConfig(seed=5, pacing="fixed")).places[1].target_ms


def test_a15_overrides_precedence(tmp_path):
    setup_clean(tmp_path, [(1, 1, "koroush", .4), (1, 1, "reza", .4), (1, 2, "koroush", .4)])
    cfg = MergeConfig(export_wav=False, variation_pct=0, pacing="fixed")
    base = run_merge(tmp_path, cfg, dry_run=True).plan_rows
    (tmp_path / "overrides.csv").write_text("section,line,speaker,pause_before_ms\n1,,,50\n1,1,reza,700\n")
    gaps = [float(r[3]) for r in run_merge(tmp_path, cfg, dry_run=True).plan_rows]
    assert gaps[1] >= 600 or abs(gaps[1] - 700) < 5  # line row beats section row
    assert gaps[2] < float(base[2][3]) and abs(gaps[2] - 50) < 80  # section row beats setting
    (tmp_path / "overrides.csv").unlink()
    assert run_merge(tmp_path, cfg, dry_run=True).plan_rows == base  # I13
    (tmp_path / "overrides.csv").write_text("section,line,speaker,gain_db\n1,9,reza,3\n9,,,x\n")
    codes = {i.code for i in run_merge(tmp_path, cfg, dry_run=True).report.issues}
    assert {"W_OVERRIDE_UNUSED", "W_OVERRIDE_INVALID"} <= codes
    d = [x for x in run_merge(tmp_path, cfg, dry_run=True).report.decisions]
    assert any("pause" in x and "gap_diff_ms" in x for x in d)


def test_cli(tmp_path, capsys):
    k = tmp_path / "k"
    k.mkdir()
    make(k / "1.1.wav", dur=0.7)
    make(k / "1.2.wav", dur=0.7)
    code = cli.main(["all", "--project", str(tmp_path / "p"), "--koroush", str(k), "--k-style", "smart", "--no-export-wav", "--json"])
    assert code in (0, 1)
    assert list((tmp_path / "p" / "merged").glob("*/FULL.mp3"))
    assert cli.main(["check"]) == 0
    assert cli.main(["merge", "--project", str(tmp_path / "nope")]) == 2


def test_ui_builds():
    pytest.importorskip("gradio")
    from mako_pipeline.ui import create_interface
    assert create_interface() is not None
