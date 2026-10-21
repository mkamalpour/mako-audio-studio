"""Command line: python mako.py check | preview | intake | merge | all  (spec 10.3)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import constants as C
from .config import (Settings, resolve_settings, save_settings, validate_intake)
from .ffmpeg import check_environment
from .intake import load_rename_overrides, plan_intake, plan_rows, run_intake
from .merge import qc_text, run_merge, script_template
from .report import Report

BOOL_KEYS = ["trim", "room_tone", "comfort_noise", "match_loudness", "match_tone", "export_mp3", "export_wav",
             "export_full", "export_stems"]
NUM_KEYS = {"gap_diff_ms": float, "gap_same_ms": float, "variation_pct": float, "section_gap_ms": float,
            "sample_rate": int, "target_lufs": float, "seed": int, "wav_bit_depth": int,
            "lead_pad_ms": float, "tail_pad_ms": float, "edge_fade_ms": float,
            "trim_threshold_db": float, "trim_floor_margin_db": float,
            "room_tone_min_ms": float, "room_tone_max_ms": float,
            "peak_ceiling_dbfs": float, "max_gain_db": float, "verify_tolerance_s": float,
            "overlap_amount": float, "quick_reply_amount": float, "thinking_pause_amount": float,
            "rhythm_memory": float, "tempo_drift": float, "min_gap_ms": float, "overlap_max_ms": float,
            "max_tone_db": float, "qc_long_silence_s": float, "qc_level_outlier_db": float,
            "qc_short_line_s": float, "qc_long_line_s": float}
STR_KEYS = ["starter", "starter_exceptions", "marker_formats", "srt_text_template", "mp3_bitrate", "dup_policy",
            "pacing", "pacing_profile", "loudness_mode", "intake_mode"]
ALIASES = {"gap_diff_ms": ["--gap-diff"], "gap_same_ms": ["--gap-same"], "variation_pct": ["--variation"],
           "section_gap_ms": ["--section-gap"]}
SHORT = {"koroush": "k", "reza": "r"}


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="mako", description="Mako Entertainment audio pipeline")
    ap.add_argument("command", choices=["check", "preview", "intake", "merge", "all", "qc", "script-template"])
    ap.add_argument("--project", help="project folder (holds clean/, merged/, script.txt ...)")
    ap.add_argument("--json", action="store_true", help="print the JSON report to stdout")
    ap.add_argument("--dry-run", action="store_true", help="merge: show the plan and write nothing")
    ap.add_argument("--save-settings", action="store_true", help="write the values used to <project>/mako_settings.json")
    ap.add_argument("--format", dest="clean_format", choices=["mp3", "wav"])
    for sp in C.SPEAKERS:
        s = SHORT[sp]
        ap.add_argument(f"--{s}-pattern", dest=f"{sp}_pattern", help="Template, e.g. K{section}_{line}")
        ap.add_argument(f"--{sp}", dest=f"{sp}_folder", help=f"{sp}'s raw folder")
        ap.add_argument(f"--{s}-style", dest=f"{sp}_style", choices=list(C.STYLES))
        ap.add_argument(f"--{s}-map", dest=f"{sp}_sections", help='section list, e.g. "5,8,8" or "1:1-5, 2:6-13"')
        ap.add_argument(f"--{s}-order", dest=f"{sp}_order_by", choices=list(C.ORDER_CHOICES))
        ap.add_argument(f"--{s}-digits", dest=f"{sp}_compact_digits", type=int, choices=[1, 2])
    for k in BOOL_KEYS:
        ap.add_argument("--" + k.replace("_", "-"), dest=k, action=argparse.BooleanOptionalAction, default=None)
    for k, t in NUM_KEYS.items():
        ap.add_argument("--" + k.replace("_", "-"), *ALIASES.get(k, []), dest=k, type=t, default=None)
    for k in STR_KEYS:
        ap.add_argument("--" + k.replace("_", "-"), dest=k, default=None)
    return ap


def _overrides(args: argparse.Namespace) -> Dict[str, Any]:
    keys = (BOOL_KEYS + list(NUM_KEYS) + STR_KEYS + ["clean_format"] +
            [f"{sp}_{k}" for sp in C.SPEAKERS for k in ("folder", "style", "sections", "order_by", "compact_digits", "pattern")])
    return {k: getattr(args, k) for k in keys if getattr(args, k, None) is not None}


def _emit(rep: Report, as_json: bool) -> None:
    if not as_json:
        print(rep.to_text(with_decisions=False))


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "check":
        info = check_environment()
        print("FFmpeg:  ", info["ffmpeg"] or info["ffmpeg_error"])
        print("Loudness:", info["loudness"])
        return 0 if info["ffmpeg"] else 2
    if not args.project:
        print("--project is required", file=sys.stderr)
        return 2
    try:
        st: Settings = resolve_settings(args.project, _overrides(args))
        validate_intake(st.intake)
        st.merge.validate()
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    if args.save_settings and not args.dry_run:
        save_settings(Path(args.project) / C.SETTINGS_FILE, st)
    rows, rename_issues = load_rename_overrides(Path(args.project) / C.RENAME_OVERRIDES_FILE)
    reports: Dict[str, Report] = {}
    if args.command == "preview":
        plan = plan_intake(st.intake, rows)
        if not args.json:
            for r in plan_rows(plan, st.intake.clean_format):
                print(" | ".join(r))
        rep = Report(stage="preview")
        rep.issues += plan.issues
        reports["preview"] = rep
    if args.command in ('intake', 'all') and args.dry_run:
        plan = plan_intake(st.intake, rows)
        rep = Report(stage='intake-preview')
        rep.issues += plan.issues
        reports['intake'] = rep
        if not args.json:
            for r in plan_rows(plan, st.intake.clean_format):
                print(' | '.join(r))
            print('Intake preview only; merge requires prepared clean files.')
        args.command = 'intake'
    elif args.command in ("intake", "all"):
        reports["intake"] = run_intake(st.intake, rename_rows=rows)
        _emit(reports["intake"], args.json)
    if args.command == "script-template":
        path, text = script_template(args.project, st.merge)
        print(f"Written: {path}\n{text}")
        return 0
    if args.command == "qc":  # read-only: decode everything, list what looks wrong, write nothing
        res = run_merge(args.project, st.merge, dry_run=True)
        reports["qc"] = res.report
        if not args.json:
            print(qc_text(res.qc_rows))
        _emit(res.report, args.json)
    if args.command in ("merge", "all") and not (reports.get("intake") and reports["intake"].failed):
        res = run_merge(args.project, st.merge, dry_run=args.dry_run)
        reports["merge"] = res.report
        if args.dry_run and not args.json:
            for r in res.plan_rows:
                print(" | ".join(r))
        _emit(reports["merge"], args.json)
    if args.command == "preview":
        _emit(reports["preview"], args.json)
    for rep in reports.values():
        rep.issues += rename_issues
    if args.json:
        data = {k: v.to_dict() for k, v in reports.items()}
        print(json.dumps(next(iter(data.values())) if len(data) == 1 else data, indent=2, ensure_ascii=False))
    return max((r.exit_code() for r in reports.values()), default=0)
