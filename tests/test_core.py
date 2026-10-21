import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from make_audio import speech  # noqa: E402
from mako_pipeline import dsp  # noqa: E402
from mako_pipeline.names import (CLEAN_RE, parse_clean, parse_sec_code, parse_sections_text,  # noqa: E402
                                 sec_code, smart_parse)
from mako_pipeline.script import parse_script  # noqa: E402
from mako_pipeline.util import natural_key  # noqa: E402

SR = 48000


def test_section_codes():
    assert [sec_code(i) for i in range(1, 10)] == [str(i) for i in range(1, 10)]
    assert (sec_code(10), sec_code(11), sec_code(35), sec_code(36)) == ("a", "b", "z", "36")
    for n in range(1, 61):
        assert parse_sec_code(sec_code(n)) == n


def test_clean_names():
    assert parse_clean("S1_3_koroush") == (1, 3, "koroush")
    assert parse_clean("Sa_5_reza") == (10, 5, "reza")
    assert not CLEAN_RE.match("S1_3_koroush__take1")


@pytest.mark.parametrize("stem,exp", [
    ("5.6", (5, 6)), ("s5.6", (5, 6)), ("S5-6", (5, 6)), ("05_06", (5, 6)), ("Section 5 - 6", (5, 6)),
    ("S1_3_koroush", (1, 3)), ("Sa_5", (10, 5)), ("S10-5", (10, 5)), ("5.6 - Copy", (5, 6)),
    ("hello", None), ("5", None), ("2024-0501", None)])
def test_smart(stem, exp):
    assert smart_parse(stem) == exp


def test_sections_text():
    assert parse_sections_text("5, 8, 8, 7").counts == [5, 8, 8, 7]
    r = parse_sections_text("1:1-5, 2:6-13, 3:9")
    assert r.ranges[3] == (9, 9) and r.map(7, 7) == (2, 2)
    assert parse_sections_text("").kind == "none"
    assert parse_sections_text("3,2,1").map(0, 6) == (3, 1)
    assert parse_sections_text("3,2,1").map(0, 7) is None
    with pytest.raises(ValueError):
        parse_sections_text("abc")
    with pytest.raises(ValueError):
        parse_sections_text("1:5-2")


def test_natural_sort():
    assert sorted(["5.10", "5.6"], key=natural_key) == ["5.6", "5.10"]


def test_trim_and_tone():
    x = speech(pad=0.6)
    clip, lead, tail = dsp.trim_clip(x, SR, True)
    assert lead >= 0.04 * SR - 1 and tail >= 0.08 * SR - 1 and len(clip) < len(x)
    c2, _, _ = dsp.trim_clip(x, SR, False)
    assert len(c2) == len(x)
    quiet = np.random.default_rng(0).normal(0, 0.002, SR).astype(np.float32)
    assert dsp.trim_clip(quiet, SR, True)[0].shape == quiet.shape
    assert dsp.find_room_tone(quiet, SR) is None
    tone = dsp.find_room_tone(x, SR)
    assert tone is not None
    db = dsp.frame_db(x, SR)
    assert 20 * np.log10(np.sqrt(np.mean(tone ** 2))) <= np.percentile(db, 10) + 4


def test_bed():
    tone = dsp.find_room_tone(speech(), SR)
    bed = dsp.make_bed(30000, tone, SR)
    assert len(bed) == 30000
    r = lambda a: 20 * np.log10(np.sqrt(np.mean(a.astype(float) ** 2)))  # noqa: E731
    assert abs(r(bed) - r(tone)) < 1.0
    assert np.max(np.abs(np.diff(bed))) <= 3 * (tone.max() - tone.min())
    assert len(dsp.make_bed(0, tone, SR)) == 0 and not dsp.make_bed(10, None, SR).any()


def test_script_grammar():
    r = parse_script("1: K R K R\n2: KRKR\n3: (K R)*3 K  # c\na: K R\ndefault: K R\n10: K\nbad line\n4: K X")
    assert r.sections[3] == list("KRKRKRK") and r.sections[2] == list("KRKR")
    assert r.sections[10] == list("KR") and r.default == ["K", "R"]
    codes = [i.code for i in r.issues]
    assert codes.count("E_SCRIPT_SYNTAX") == 3  # duplicate section 10, no colon, bad char
    assert 4 not in r.sections
