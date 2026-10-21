"""Gradio interface (local only). Core code never imports this module."""
from __future__ import annotations

import functools
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")

import gradio as gr

from . import constants as C
from .config import (Settings, launcher_dir, resolve_settings, save_settings, settings_from_dict,
                     validate_intake)
from .ffmpeg import check_environment
from .intake import (load_rename_overrides, overrides_from_table, plan_intake, run_intake,
                     save_rename_overrides)
from .merge import qc_text, run_merge, script_template, scan_clean
from .overrides import HEADER as OV_HEADER, read_rows, overrides_text, parse_overrides
from .report import Report
from .script import parse_script
from .util import write_text_atomic

STYLE_LABELS = list(C.STYLE_LABELS)
TABLE_HEAD = ["speaker", "source_file", "section", "line", "skip", "new name", "note"]
PLAN_HEAD = ["section", "file", "start (s)", "gap before (ms)", "gain (dB)", "order"]
KEYS = (["project"] + [f"{sp}_{k}" for sp in C.SPEAKERS for k in ("folder", "style", "sections", "order_by", "compact_digits", "pattern")]
        + ["clean_format", "starter", "starter_exceptions", "gap_diff_ms", "gap_same_ms", "variation_pct",
           "section_gap_ms", "sample_rate", "trim", "room_tone", "match_loudness", "target_lufs", "export_mp3",
           "export_wav", "export_full", "export_stems", "seed", "wav_bit_depth", "marker_formats",
           "srt_text_template", "lead_pad_ms", "tail_pad_ms", "edge_fade_ms", "trim_threshold_db",
           "trim_floor_margin_db", "room_tone_min_ms", "room_tone_max_ms", "peak_ceiling_dbfs", "max_gain_db",
           "mp3_bitrate", "dup_policy", "verify_tolerance_s", "intake_mode", "pacing", "pacing_profile",
           "overlap_amount", "quick_reply_amount", "thinking_pause_amount", "rhythm_memory", "tempo_drift",
           "min_gap_ms", "overlap_max_ms", "comfort_noise", "loudness_mode", "match_tone", "max_tone_db",
           "qc_long_silence_s", "qc_level_outlier_db", "qc_short_line_s", "qc_long_line_s"])


def _s(v) -> str:
    if v is None or (isinstance(v, float) and v != v):
        return ""
    return str(v).strip()


def browse_folder() -> str:
    """Native folder dialog in a separate process (safe with the web server threads)."""
    code = ("import tkinter as t;from tkinter import filedialog as f;r=t.Tk();r.withdraw();"
            "r.attributes('-topmost',True);print(f.askdirectory() or '')")
    try:
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=300)
        return out.stdout.strip()
    except Exception:
        return ""


def open_folder(path: str) -> None:
    if not path or not Path(path).exists():
        return
    if os.name == "nt":
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def settings_from_values(vals) -> Settings:
    d: Dict[str, Any] = dict(zip(KEYS, vals))
    for sp in C.SPEAKERS:
        d[f"{sp}_style"] = C.STYLE_LABELS.get(_s(d.get(f"{sp}_style")), "mako")
    if not _s(d.get('project')):
        raise ValueError('Choose an episode project folder first.')
    st = settings_from_dict(d, Settings(), strict=True)
    validate_intake(st.intake)
    st.merge.validate()
    return st


def _save(st: Settings) -> None:
    Path(st.intake.project).mkdir(parents=True, exist_ok=True)
    save_settings(launcher_dir() / C.SETTINGS_FILE, st)  # remembers the project and last values
    if st.intake.project.strip() and Path(st.intake.project).is_dir():
        save_settings(Path(st.intake.project) / C.SETTINGS_FILE, st)


def _safe(fn, n: int, warn_i: int, log_i: int):
    """Wrap a handler: an unexpected error is shown in the warnings and report boxes, never a blank page."""
    @functools.wraps(fn)          # keeps the signature, so Gradio still injects its Progress tracker
    def wrapper(*a, **k):
        try:
            return fn(*a, **k)
        except Exception as e:  # noqa: BLE001 - the UI must always answer
            import traceback
            out = [gr.update()] * n
            if isinstance(e, (ValueError, gr.Error)):
                message = str(e)
                out[warn_i] = message
                out[log_i] = message
            else:
                out[warn_i] = f'Unable to finish: {e}'
                out[log_i] = traceback.format_exc()
            return tuple(out) if n > 1 else out[0]
    return wrapper


def _warn_block(rep: Report) -> str:
    if not rep.issues:
        return "No warnings."
    return f"{len(rep.issues)} warning(s)/error(s):\n" + rep.warnings_text()


def _table_dicts(table) -> List[dict]:
    rows = table.values.tolist() if hasattr(table, "values") else (table or [])
    return [dict(zip(["speaker", "source_file", "section", "line", "skip"], r[:5])) for r in rows if r and _s(r[1])]


def do_preview(*vals):
    st = settings_from_values(vals[:len(KEYS)])
    rows, _ = load_rename_overrides(Path(st.intake.project) / C.RENAME_OVERRIDES_FILE) if st.intake.project else ([], [])
    plan = plan_intake(st.intake, rows)
    labels = {"ok": "OK", "dup": "older take", "unreadable": "CAN'T READ", "unmapped": "NOT MAPPED", "excluded": "EXCLUDED"}
    table = [[i.speaker, str(i.src.resolve()), i.section or "", i.line or "", i.status == "excluded",
              i.new_name("." + st.intake.clean_format) if i.status == "ok" else "",
              labels[i.status] + (f" - {i.note}" if i.note else "")] for i in plan.items]
    rep = Report(stage="preview")
    rep.issues += plan.issues
    return table, _warn_block(rep), f"{len(plan.items)} file(s) read. Nothing was changed."


def _intake(st: Settings, table, progress=None) -> Report:
    proj = Path(st.intake.project)
    rn = proj / C.RENAME_OVERRIDES_FILE
    existing, _ = load_rename_overrides(rn)
    edits = _table_dicts(table)
    if edits and proj.is_dir():
        baseline = plan_intake(st.intake, [])
        new_rows = overrides_from_table(edits, baseline, existing)
        save_rename_overrides(rn, new_rows)  # repeatable decision
        existing = new_rows
    return run_intake(st.intake, rename_rows=existing, progress=progress)


def _files(rep: Report, out) -> List[str]:
    return [str(Path(out) / o) for o in rep.outputs if o.endswith((".mp3", ".wav"))] if out else []


def form_controls(script: str, ov_rows):
    """Validate current edits once for saving, previews and exports."""
    script = script or ''
    parsed = parse_script(script)
    if parsed.issues:
        raise ValueError('Invalid dialogue order: ' + '; '.join(i.message for i in parsed.issues))
    rows = ov_rows.values.tolist() if hasattr(ov_rows, 'values') else (ov_rows or [])
    text = overrides_text(rows)
    _, issues = parse_overrides(text)
    if issues:
        raise ValueError('Invalid timing or gain: ' + '; '.join(i.message for i in issues))
    return script, text


def _save_texts(st: Settings, script: str, ov_rows) -> None:
    script, text = form_controls(script, ov_rows)
    proj = Path(st.intake.project)
    if not proj.is_dir():
        raise ValueError('Choose an existing episode folder before saving controls.')
    write_text_atomic(proj / C.SCRIPT_FILE, script)
    write_text_atomic(proj / C.OVERRIDES_FILE, text)


def listen_piece(project, section, token):
    """Resolve only known, prepared clips; never treat user tokens as file paths."""
    if not project or not section or not token:
        raise gr.Error('Choose a section and a prepared clip first.')
    import re
    from .names import parse_sec_code
    match = re.fullmatch(r'([KR])([1-9][0-9]{0,3})', token, re.I)
    clean = Path(project) / 'clean'
    if not match or not clean.is_dir():
        raise gr.Error('Prepare recordings first, then choose an exact clip ID such as K1.')
    scan, _ = scan_clean(clean)
    speaker = 'koroush' if match[1].upper() == 'K' else 'reza'
    path = scan.get(parse_sec_code(str(section)), {}).get(speaker, {}).get(int(match[2]))
    if path is None:
        raise gr.Error('That clip is missing from the prepared recordings.')
    if not path.resolve().is_relative_to(clean.resolve()):
        raise gr.Error("Prepared clips must be inside this episode's clean folder.")
    return str(path.resolve())


def do_intake(*vals, progress=gr.Progress()):
    st = settings_from_values(vals[:len(KEYS)])
    _save(st)
    rep = _intake(st, vals[len(KEYS) + 2], lambda f, d: progress(f, desc=d))
    return _warn_block(rep), rep.to_text(), gr.update()


def do_merge(*vals, progress=gr.Progress()):
    st = settings_from_values(vals[:len(KEYS)])
    _save(st)
    _save_texts(st, vals[len(KEYS)], vals[len(KEYS) + 1])
    res = run_merge(st.intake.project, st.merge, lambda f, d: progress(f, desc=d))
    files = _files(res.report, res.out_dir)
    return (_warn_block(res.report), res.report.to_text(), gr.update(choices=files, value=files[0] if files else None))


def do_both(*vals, progress=gr.Progress()):
    st = settings_from_values(vals[:len(KEYS)])
    _save(st)
    _save_texts(st, vals[len(KEYS)], vals[len(KEYS) + 1])
    r1 = _intake(st, vals[len(KEYS) + 2], lambda f, d: progress(f * 0.3, desc=d))
    if r1.failed:
        return _warn_block(r1), r1.to_text(), gr.update()
    res = run_merge(st.intake.project, st.merge, lambda f, d: progress(0.3 + f * 0.7, desc=d))
    r1.extend(res.report)
    files = _files(res.report, res.out_dir)
    return _warn_block(r1), r1.to_text(), gr.update(choices=files, value=files[0] if files else None)


def do_plan(*vals, progress=gr.Progress()):
    st = settings_from_values(vals[:len(KEYS)])
    script, overrides = form_controls(vals[len(KEYS)], vals[len(KEYS) + 1])
    res = run_merge(st.intake.project, st.merge, lambda f, d: progress(f, desc=d),
                    dry_run=True, script_text=script, override_text=overrides)
    return res.plan_rows, _warn_block(res.report), res.report.to_text()


def do_check(*vals, progress=gr.Progress()):
    """Read-only quality check: decodes every clean file, writes nothing."""
    st = settings_from_values(vals[:len(KEYS)])
    script, overrides = form_controls(vals[len(KEYS)], vals[len(KEYS) + 1])
    res = run_merge(st.intake.project, st.merge, lambda f, d: progress(f, desc=d),
                    dry_run=True, script_text=script, override_text=overrides)
    return _warn_block(res.report), qc_text(res.qc_rows) + "\n" + res.report.to_text(with_decisions=False)


def do_template(*vals):
    st = settings_from_values(vals[:len(KEYS)])
    _save(st)
    path, text = script_template(st.intake.project, st.merge)
    return text, f"Written: {path.name} in the project folder. Edit it, then run Merge."


def do_save_texts(*vals):
    st = settings_from_values(vals[:len(KEYS)])
    _save(st)
    _save_texts(st, vals[len(KEYS)], vals[len(KEYS) + 1])
    return "Saved script.txt and overrides.csv in the project folder."


def load_project_texts(project: str):
    p = Path(project or "")
    script = (p / C.SCRIPT_FILE).read_text(encoding="utf-8-sig") if (p / C.SCRIPT_FILE).is_file() else ""
    return script, read_rows(p / C.OVERRIDES_FILE)


def naming_visibility(label):
    style = C.STYLE_LABELS.get(label, "mako")
    return tuple(gr.update(visible=show) for show in
                 (style == "custom", style == "sequence", style == "sequence", style == "compact"))


def order_sections(text, current=None):
    sections = []
    for line in (text or "").splitlines():
        key, sep, _ = line.partition(":")
        key = key.strip()
        from .names import parse_sec_code
        if sep and parse_sec_code(key) is not None and key not in sections:
            sections.append(key)
    return gr.update(choices=sections, value=current if current in sections else (sections[0] if sections else None))


def section_rows(text, section):
    for line in (text or "").splitlines():
        key, sep, tokens = line.partition(":")
        if sep and key.strip() == str(section):
            return [[token] for token in tokens.split("#", 1)[0].split()]
    return []


def piece_choices(rows, selected=None):
    rows = rows.values.tolist() if hasattr(rows, "values") else (rows or [])
    choices = list(dict.fromkeys(_s(row[0]) for row in rows if row and _s(row[0])))
    return gr.update(choices=choices, value=selected if selected in choices else (choices[0] if choices else None))


def move_piece(rows, selected, direction):
    rows = rows.values.tolist() if hasattr(rows, "values") else list(rows or [])
    tokens = [_s(row[0]) if row else "" for row in rows]
    if selected not in tokens:
        return rows, selected
    index = tokens.index(selected)
    target = index + direction
    if 0 <= target < len(rows):
        rows[index], rows[target] = rows[target], rows[index]
    return rows, selected


def apply_section(text, section, rows):
    if not section:
        raise gr.Error("Fill the order from prepared recordings, then choose a section.")
    rows = rows.values.tolist() if hasattr(rows, "values") else (rows or [])
    tokens = [_s(row[0]) for row in rows if row and _s(row[0])]
    import re
    if not tokens or any(not re.fullmatch(r"[kKrR](?:[1-9][0-9]*)?", token) for token in tokens):
        raise gr.Error("Use clip IDs such as K1, R1, K2, R10; one clip per row.")
    bare = [len(token) == 1 for token in tokens]
    if any(bare) and not all(bare):
        raise gr.Error("Use either exact clip IDs or speaker turns for the entire section.")
    if not all(bare):
        normalized = [token.upper() for token in tokens]
        if len(set(normalized)) != len(normalized):
            raise gr.Error("Each clip can appear only once in a section.")
        if any(len(token[1:]) > 4 for token in tokens):
            raise gr.Error("Dialogue IDs must be between 1 and 9999.")
    replacement = f"{section}: " + " ".join(tokens)
    lines = (text or "").splitlines()
    for index, line in enumerate(lines):
        if line.partition(":")[0].strip() == str(section):
            lines[index] = replacement
            break
    else:
        lines.append(replacement)
    return "\n".join(lines)


def create_interface() -> "gr.Blocks":
    st = resolve_settings()
    env = check_environment()
    with gr.Blocks(title="Mako Audio Studio", theme=gr.themes.Soft(primary_hue="teal", font=["Segoe UI", "Arial", "sans-serif"], font_mono=["Consolas", "monospace"]), css="""
        .gradio-container {max-width: 1220px !important; font-family: 'Segoe UI', sans-serif;}
        #studio-header {border-left: 4px solid #0f766e; padding: 8px 20px; margin: 12px 0 22px;}
        #studio-header h1 {letter-spacing: -.04em; font-size: 36px; margin-bottom: 6px;}
        .tab-nav button {font-weight: 650 !important; padding: 14px 18px !important;}
        .speaker-panel {border-top: 3px solid #0f766e; padding-top: 12px;}
        .speaker-panel.reza {border-top-color: #b7791f;}
        .studio-table {min-width: 0 !important; max-width: 100%; overflow-x: auto !important;}
        .studio-table .table-wrap {overflow-x: auto !important; max-width: 100%;}
        .studio-table table {min-width: 960px !important; table-layout: auto !important;}
        .studio-table th, .studio-table td {min-width: 110px !important;}
        @media (max-width: 600px) {
          #studio-header {padding: 3px 12px; margin: 4px 0 10px;}
          #studio-header h1 {font-size: 27px;}
          #project-controls {gap: 6px;}
          .gradio-container {padding-top: 8px !important;}
        }
        """) as demo:
        gr.Markdown("MAKO ENTERTAINMENT / AUDIO WORKSPACE\n# Mako Audio Studio\nRecord separately. Build one conversation.", elem_id="studio-header")
        with gr.Accordion("Local audio engine", open=False):
            gr.Markdown(f"FFmpeg: `{env['ffmpeg'] or env['ffmpeg_error']}`  \nLoudness: {env['loudness']}")
        with gr.Row(elem_id="project-controls"):
            project = gr.Textbox(label="Project folder", value=st.intake.project, scale=6)
            b_proj = gr.Button("Browse", scale=1)
            b_load = gr.Button("Load project settings", scale=1)
        comps: Dict[str, Any] = {"project": project}
        with gr.Tab("1 - Recordings & names"):
            gr.Markdown("Choose each recording folder. Default names: **K11 / R11** = speaker, section 1, dialogue 1. Case does not matter. **a = 10, b = 11**, then c, d, and so on. **Kbb = section 11, dialogue 11.** Originals stay untouched.")
            with gr.Row():
                for sp in C.SPEAKERS:
                    sc = st.intake.speakers[sp]
                    with gr.Column(elem_classes=["speaker-panel", sp]):
                        gr.Markdown(f"### {sp.capitalize()}  /  {sp[0].upper()}")
                        comps[f"{sp}_folder"] = gr.Textbox(label="Raw folder", value=sc.folder)
                        bb = gr.Button(f"Browse {sp}'s folder")
                        bb.click(browse_folder, None, comps[f"{sp}_folder"])
                        comps[f"{sp}_style"] = gr.Dropdown(STYLE_LABELS, value=C.STYLE_TO_LABEL[sc.style], label="Naming style")
                        comps[f"{sp}_pattern"] = gr.Textbox(label="Custom pattern (without extension)", value=sc.pattern, placeholder="s{section}{line} or {section}{line}", visible=sc.style == "custom")
                        comps[f"{sp}_sections"] = gr.Textbox(label="Section list (numbered files)", value=sc.sections_text,
                                                             placeholder="5, 8, 8, 7   or   1:1-5, 2:6-13", visible=sc.style == "sequence")
                        comps[f"{sp}_order_by"] = gr.Dropdown(list(C.ORDER_CHOICES), value=sc.order_by, label="Order by", visible=sc.style == "sequence")
                        comps[f"{sp}_compact_digits"] = gr.Radio([1, 2], value=sc.compact_digits, label="Digits of the section (compact)", visible=sc.style == "compact")
            comps["clean_format"] = gr.Radio(["mp3", "wav"], value=st.intake.clean_format, label="Working format (WAV recommended; MP3 saves space)")
            b_prev = gr.Button("Preview recording names", variant="primary")
            table = gr.Dataframe(headers=TABLE_HEAD, value=[], type="array", interactive=True,
                                 datatype=["str", "str", "number", "number", "bool", "str", "str"],
                                 label="Name preview - edit speaker, section, dialogue or skip before preparing",
                                 elem_classes=["studio-table"], column_widths=[120, 320, 110, 110, 90, 150, 180])
            name_status = gr.Textbox(label="Naming status", lines=2, interactive=False)
            name_log = gr.Textbox(label="Preview details", lines=2, interactive=False)
            b_in = gr.Button("Prepare recordings")
            prepare_status = gr.Textbox(label="Preparation status", lines=2, interactive=False)

        b_proj.click(browse_folder, None, project)
        m = st.merge
        script0, ov0 = load_project_texts(st.intake.project)
        with gr.Tab("2 - Dialogue order"):
            gr.Markdown("### Piece together the conversation\nPrepare your recordings first, then fill the order. Choose a section and move its clips into place. Clip IDs here omit the section: **K1** is Koroush's first dialogue in the selected section. Include each clip you want to hear; unlisted clips are left out.")
            b_tpl = gr.Button("Fill order from prepared recordings", variant="primary")
            section = gr.Dropdown(label="Section", choices=[], interactive=True)
            pieces = gr.Dataframe(headers=["Clip in playback order"], value=[], type="array", interactive=True,
                                  datatype=["str"], label="Section pieces - choose a clip below, then move up or down; dialogue IDs use decimal numbers")
            selected_piece = gr.Dropdown(label="Clip to move or listen", choices=[], interactive=True)
            b_listen = gr.Button('Listen to selected clip')
            clip_audio = gr.Audio(label='Prepared recording (before merge processing)', type='filepath')
            b_listen.click(listen_piece, [project, section, selected_piece], clip_audio)
            selected_piece.change(lambda: None, None, clip_audio)
            section.change(lambda: None, None, clip_audio)
            project.change(lambda: None, None, clip_audio)
            with gr.Row():
                b_up = gr.Button("Move up")
                b_down = gr.Button("Move down")
                b_apply = gr.Button("Apply this section", variant="primary")
            def select_piece(evt: gr.SelectData):
                return _s(evt.value)
            pieces.select(select_piece, None, selected_piece)
            pieces.change(piece_choices, [pieces, selected_piece], selected_piece)
            b_up.click(lambda r, i: move_piece(r, i, -1), [pieces, selected_piece], [pieces, selected_piece])
            b_down.click(lambda r, i: move_piece(r, i, 1), [pieces, selected_piece], [pieces, selected_piece])
            with gr.Accordion("Full episode order / custom script", open=False):
                script = gr.Textbox(label="Exact clip order, one section per line", lines=8, value=script0,
                                    placeholder="1: K1 R1 K2 R2\n2: R1 K1 R2 K2")
                gr.Markdown("Use exact dialogue IDs to choose every piece. Legacy speaker turns such as `1: K R K R` are also accepted. Apply section edits before previewing. Preview uses your current unsaved script and timing controls.")
            with gr.Accordion("Per-clip timing and gain", open=False):
                ov = gr.Dataframe(headers=OV_HEADER, value=ov0 or [["", "", "", "", "", "", ""]], type="array",
                                  interactive=True, label="Per-line overrides (overrides.csv)", elem_classes=["studio-table"], column_widths=[140] * len(OV_HEADER))
            b_save = gr.Button("Save dialogue order and overrides")
            order_status = gr.Textbox(label="Order status", lines=2, interactive=False)
            script.change(order_sections, [script, section], section)
            section.change(section_rows, [script, section], pieces)
            b_apply.click(apply_section, [script, section, pieces], script)
        with gr.Tab("3 - Sound & export"):
            gr.Markdown("### Make the edit feel natural\nTrim empty edges, set comfortable pauses and balance both voices. Start here; open the detailed controls only when needed.")
            comps["pacing"] = gr.Radio(list(C.PACING_MODES), value=m.pacing, label="Pacing (natural = varied, fixed = the two gaps below)")
            comps["gap_diff_ms"] = gr.Slider(0, 1500, m.gap_diff_ms, step=10, label="Typical pause when the speaker changes (ms)")
            comps["gap_same_ms"] = gr.Slider(0, 1500, m.gap_same_ms, step=10, label="Typical pause, same speaker (ms)")
            comps["section_gap_ms"] = gr.Slider(0, 5000, m.section_gap_ms, step=100, label="Gap between sections (ms)")
            comps["trim"] = gr.Checkbox(m.trim, label="Trim silence")
            comps["room_tone"] = gr.Checkbox(m.room_tone, label="Fill pauses with room tone")
            comps["match_loudness"] = gr.Checkbox(m.match_loudness, label="Match loudness")
            comps["target_lufs"] = gr.Slider(-30, -10, m.target_lufs, step=0.5, label="Target loudness (LUFS)")
            comps["export_mp3"] = gr.Checkbox(m.export_mp3, label="Export MP3")
            comps["export_wav"] = gr.Checkbox(m.export_wav, label="Export WAV")
            comps["export_full"] = gr.Checkbox(m.export_full, label="Export FULL episode")
            comps["export_stems"] = gr.Checkbox(m.export_stems, label="Export one track per speaker")
            with gr.Accordion("Export and matching details", open=False):
                comps["starter"] = gr.Radio(list(C.SPEAKERS), value=m.starter, label="Starts each section")
                comps["starter_exceptions"] = gr.Textbox(label="Starter exceptions", value=m.starter_exceptions, placeholder="3:reza, 7:koroush")
                comps["variation_pct"] = gr.Slider(0, 60, m.variation_pct, step=1, label="Pause variation (%), fixed pacing only")
                comps["sample_rate"] = gr.Radio(["44100", "48000"], value=str(m.sample_rate), label="Sample rate")
                comps["comfort_noise"] = gr.Checkbox(m.comfort_noise, label="Soft room hiss when a clip has no quiet stretch (never dead silence)")
                comps["loudness_mode"] = gr.Radio(list(C.LOUDNESS_MODES), value=m.loudness_mode, label="Loudness target (fixed = target below, match = the louder speaker)")
                comps["match_tone"] = gr.Checkbox(m.match_tone, label="Bring the two voices' brightness closer (gentle)")
                comps["max_tone_db"] = gr.Slider(0, 6, m.max_tone_db, step=0.5, label="Most brightness change (dB)")
                comps["seed"] = gr.Number(m.seed, label="Seed", precision=0)
                comps["wav_bit_depth"] = gr.Radio(["16", "24"], value=str(m.wav_bit_depth), label="WAV bit depth")
                comps["marker_formats"] = gr.CheckboxGroup(["srt", "audacity", "csv"], value=m.marker_formats, label="Markers")
                comps["srt_text_template"] = gr.Textbox(label="SRT text", value=m.srt_text_template)
            with gr.Accordion("Natural pacing (used when pacing = natural)", open=False):
                comps["pacing_profile"] = gr.Radio(list(C.PACING_CHOICES), value=m.pacing_profile, label="Profile (auto picks from line length)")
                comps["overlap_amount"] = gr.Slider(0, 3, m.overlap_amount, step=0.1, label="Talk-over amount (0 = never)")
                comps["overlap_max_ms"] = gr.Slider(0, 400, m.overlap_max_ms, step=10, label="Deepest talk-over (ms)")
                comps["quick_reply_amount"] = gr.Slider(0, 3, m.quick_reply_amount, step=0.1, label="Quick replies")
                comps["thinking_pause_amount"] = gr.Slider(0, 3, m.thinking_pause_amount, step=0.1, label="Thinking pauses")
                comps["rhythm_memory"] = gr.Slider(0, 1.7, m.rhythm_memory, step=0.1, label="Rhythm memory (how long a fast or slow stretch lasts)")
                comps["tempo_drift"] = gr.Slider(0, 3, m.tempo_drift, step=0.1, label="Tempo drift over the episode (0 = steady)")
                comps["min_gap_ms"] = gr.Slider(0, 500, m.min_gap_ms, step=5, label="Shortest pause (ms)")
            with gr.Accordion("Advanced", open=False):
                for k, lab in [("lead_pad_ms", "Lead pad (ms)"), ("tail_pad_ms", "Tail pad (ms)"), ("edge_fade_ms", "Edge fade (ms)"),
                               ("trim_threshold_db", "Trim threshold below peak (dB)"), ("trim_floor_margin_db", "Trim margin above noise (dB)"),
                               ("room_tone_min_ms", "Room tone min (ms)"), ("room_tone_max_ms", "Room tone max (ms)"),
                               ("peak_ceiling_dbfs", "Peak ceiling (dBFS)"), ("max_gain_db", "Max gain (dB)")]:
                    comps[k] = gr.Number(getattr(m, k), label=lab)
                comps["mp3_bitrate"] = gr.Textbox(label="MP3 bitrate", value=m.mp3_bitrate)
                comps["dup_policy"] = gr.Radio(["newest", "oldest", "manual"], value=st.intake.dup_policy, label="Retakes: keep")
                comps["verify_tolerance_s"] = gr.Number(st.intake.verify_tolerance_s, label="Verify tolerance (s)")
                comps["intake_mode"] = gr.Radio(list(C.INTAKE_MODES), value=st.intake.intake_mode, label="Intake: replace matching lines safely, or add only new lines")
                comps["qc_long_silence_s"] = gr.Number(m.qc_long_silence_s, label="Warn: silence inside a line longer than (s)")
                comps["qc_level_outlier_db"] = gr.Number(m.qc_level_outlier_db, label="Warn: line louder/quieter than the speaker's typical by (dB)")
                comps["qc_short_line_s"] = gr.Number(m.qc_short_line_s, label="Warn: line shorter than (s)")
                comps["qc_long_line_s"] = gr.Number(m.qc_long_line_s, label="Warn: line longer than (s)")
        with gr.Tab("4 - Build & listen"):
            with gr.Row():
                b_mg = gr.Button("Export episode")
                b_all = gr.Button("Prepare & export", variant="primary")
                b_plan = gr.Button("Merge plan preview")
                b_chk = gr.Button("Check material")
            warn = gr.Textbox(label="Status & issues", lines=4)
            log = gr.Textbox(label="Run report", lines=10)
            plan = gr.Dataframe(headers=PLAN_HEAD, value=[], type="array", interactive=False, label="Merge plan", elem_classes=["studio-table"], column_widths=[160] * len(PLAN_HEAD))
            files = gr.Dropdown([], label="Merged files")
            audio = gr.Audio(label="Listen", type="filepath")
            files.change(lambda p: p, files, audio)
            b_open = gr.Button("Open project folder")
            b_open.click(open_folder, project, None)
        ins = [comps[k] for k in KEYS] + [script, ov, table]
        processing = dict(concurrency_id="episode-processing", concurrency_limit=1)
        b_prev.click(_safe(do_preview, 3, 1, 2), ins, [table, name_status, name_log], **processing)
        b_in.click(_safe(do_intake, 3, 0, 1), ins, [prepare_status, name_log, files], **processing)
        b_mg.click(_safe(do_merge, 3, 0, 1), ins, [warn, log, files], **processing)
        b_all.click(_safe(do_both, 3, 0, 1), ins, [warn, log, files], **processing)
        b_plan.click(_safe(do_plan, 3, 1, 2), ins, [plan, warn, log], **processing)
        b_chk.click(_safe(do_check, 2, 0, 1), ins, [warn, log], **processing)
        b_tpl.click(_safe(do_template, 2, 1, 1), ins, [script, order_status], **processing)
        b_save.click(_safe(do_save_texts, 1, 0, 0), ins, order_status, **processing)
        def load_episode(path):
            loaded = resolve_settings(path)
            from .config import settings_to_dict
            d = settings_to_dict(loaded)
            d['project'] = path
            for sp in C.SPEAKERS:
                d[f'{sp}_style'] = C.STYLE_TO_LABEL[d[f'{sp}_style']]
            for k in ('sample_rate', 'wav_bit_depth'):
                d[k] = str(d[k])
            text, overrides = load_project_texts(path)
            return [d[k] for k in KEYS if k != "project"] + [text, overrides, [], gr.update(choices=[], value=None), None]
        b_load.click(load_episode, project, [comps[k] for k in KEYS if k != "project"] + [script, ov, table, files, audio])
        def change_project(path):
            text, overrides = load_project_texts(path)
            return text, overrides, [], gr.update(choices=[], value=None), None
        project.change(change_project, project, [script, ov, table, files, audio])
        demo.load(order_sections, [script, section], section)
        for sp in C.SPEAKERS:
            comps[f'{sp}_style'].change(naming_visibility, comps[f'{sp}_style'],
                                      [comps[f'{sp}_{key}'] for key in ('pattern', 'sections', 'order_by', 'compact_digits')])
            for key in ('folder', 'style', 'sections', 'order_by', 'compact_digits', 'pattern'):
                comps[f'{sp}_{key}'].change(lambda: [], None, table)
    return demo


def launch() -> None:
    create_interface().launch(inbrowser=True, share=False, server_name="127.0.0.1")
