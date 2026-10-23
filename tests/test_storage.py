import pytest
from mako_pipeline import util
from mako_pipeline.intake import save_rename_overrides


def test_failed_control_save_keeps_previous_and_cleans_staging(tmp_path, monkeypatch):
    target = tmp_path / "rename_overrides.csv"
    target.write_text("previous", encoding="utf-8")
    def fail(*args):
        raise PermissionError("locked destination")
    monkeypatch.setattr(util.os, "replace", fail)
    with pytest.raises(PermissionError):
        save_rename_overrides(target, [])
    assert target.read_text(encoding="utf-8") == "previous"
    assert list(tmp_path.iterdir()) == [target]


def test_distinct_staged_writes_do_not_clobber_each_other(tmp_path, monkeypatch):
    target = tmp_path / "controls.txt"
    replace = util.os.replace
    staged = []
    def nested(source, destination):
        staged.append(source)
        if len(staged) == 1:
            util.write_text_atomic(target, "second")
        replace(source, destination)
    monkeypatch.setattr(util.os, "replace", nested)
    util.write_text_atomic(target, "first")
    assert len(set(staged)) == 2
    assert target.read_text(encoding="utf-8") == "first"
    assert list(tmp_path.iterdir()) == [target]
