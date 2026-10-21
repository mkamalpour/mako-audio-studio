# Mako Audio Studio: control reference

The README describes the user workflow. This document records the control contracts implemented by the local Python pipeline.

## Coordinates and naming

Speakers are `koroush` (K) and `reza` (R). Default raw and prepared names are speaker + section code + dialogue code: `K11`, `Raa`, `Kbb`. Codes `1..9,a..z` represent 1..35, case-insensitively. Zero is invalid. Default raw parsing requires exactly three characters, without extension. The folder's speaker must match the prefix. For larger coordinates, use a separated custom raw pattern; prepared names fall back to `K36_1`. Legacy prepared names `S<section>_<decimal dialogue>_<speaker>` are supported, with section letters a..z.

Other raw presets: smart separated names; decimal compact (first 1 or 2 digits section); sequence with counts (`5,8,7`) or ranges (`1:1-5,2:6-13`); custom literal templates containing `{section}` and `{line}` exactly once. Adjacent slots each match one code character; separated slots match decimal section 1..999 and dialogue 1..9999. Custom templates are escaped literals, not arbitrary regular expressions. Persian/Arabic digits normalize to ASCII. Date-like names are refused by smart parsing.

## Intake

The preview reads immediate files in each folder, not subfolders. Supported media suffixes are in `constants.AUDIO_EXTS`. Duplicate coordinates use newest/oldest/manual policy; an explicit take overrides automatic choice. Unselected takes are preserved. Same-format input is decoded to check readability, copied, and checked by SHA-256 and size. Other formats are converted using FFmpeg and checked against source decoded duration. WAV conversion is 24-bit PCM; same-format WAV copies retain their original encoding.

New matching replacements are staged and verified before old matching files (including legacy names) are archived. Unrelated clean lines stay in place. Failed replacements retain their prior clean version. `intake_mode=add` skips prepared coordinates and repeated no-op runs succeed. Raw paths inside clean/ are rejected. A partial stage can complete with per-file issues; inspect its report before merging. Concurrent processing runs against the same episode are unsupported.

## Order: script.txt

```text
# Exact clip IDs: decimal dialogue numbers within the section
1: K2 R1 K1 R2
b: R11 K11
# Legacy speaker-turn grammar
3: K R R K R
4: (K R)*3 K
default: K R
```

Exact section arrangements use every listed clip once in the specified order. Omitted material is excluded with an issue, missing references warn, duplicate IDs are rejected. Legacy turn sequences consume the next line number for that speaker; unreferenced extras are appended with warnings. Invalid script lines are reported; export refuses to proceed until they are fixed. Section order is numerical; within-section precedence is explicit script, starter exceptions, default pattern, alternating starter. The UI editor manipulates a selected section's list through row moves and Apply; Save persists script.txt. A fresh template writes script.txt; if present it writes script.suggested.txt. Do not mix bare turns and exact IDs in one section.

## Other controls

`rename_overrides.csv`: `source_file,action,speaker,section,line`, action `include`, `exclude`, or `take`. Include needs positive section/dialogue. Speaker also disambiguates identical source basenames; ambiguous rows are reported. Preview edits persist here when preparing.

`overrides.csv`: `section,line,speaker,pause_before_ms,gain_db,trim,room_tone`. Blank dialogue applies to a section; line overrides beat section overrides. Negative pause means deliberate overlap. Non-finite values are refused. Gain is bounded to -60..60 dB, and pause to -400..60000 ms. Invalid rows are reported by overrides.py.

`mako_settings.json` is flat JSON. Built-in defaults < launcher settings < project settings < CLI/UI values. The GUI explicitly loads project settings; active form values are used for processing. Invalid file values may fall back to defaults; explicit UI/CLI values are validated strictly, including fractional integer settings. Configuration fields/defaults are defined in config.py, validation ranges there are authoritative; CLI `--help` lists flags. Use `--k-style custom --k-pattern "s{section}{line}"` (and corresponding R flags) for templates.

## Processing and limits

Merge decodes to mono float32 at 48 kHz by default (44.1 kHz optional). It trims edges conservatively with lead/tail padding and fades, optionally fills gaps with captured room tone or quiet synthesized noise, optionally applies gentle tone matching, then uses a fixed loudness gain per speaker with sample-peak and maximum-gain bounds. Natural pacing is seeded; fixed gaps are available. Per-line pause/gain/trim overrides take priority. Tone matching does not repair bad recordings. Peak protection is sample-based, not a true-peak limiter; overlapping signals can exceed full scale and are reported. WAV export defaults to 24-bit, MP3 to 320k. Identical inputs/settings/seed are tested for identical decoded WAV in the verified environment; cross-version binary identity is not promised.

GUI preview and QC validate current applied script/timing edits in memory, write no episode controls, and may decode all audio. CLI preview/QC use saved controls. Script/template and processing actions do write controls. GUI processing actions share a queue to prevent concurrent preparation/export/control writes. Writers from separate processes remain unsupported. Full-run rollback, recursive source uploads, drag-and-drop ordering, individual take auditioning, denoising, transcription, Persian UI, and incremental rendering are not implemented. Decoded clips remain in RAM. WAV files can consume significant disk space.

## Outputs and checks

A merge writes a new `merged/<timestamp>[_N]/` directory: section files, optional FULL, optional aligned speaker stems, SRT labels, Audacity labels, timeline.csv, QC, report and manifest. SRT is an editing aid. The manifest hashes inputs and produced files; timestamps are not reproducible. Failed export folders retain an incomplete marker. Temporary file renames reduce exposure of partial files but do not provide a transaction across all outputs.

Warnings include missing/excluded lines, duplicates, unreadable media, near silence, clipping, long silence, unusual line length, gain limits and suspicious trims. Reports distinguish stage failure from per-file errors. GUI binds to loopback with share disabled; local paths are intended for a trusted single-user workstation. Source recordings, output audio, settings and credentials must remain excluded from Git.

Validation: automated synthetic tests plus browser workflow checks are described in the final delivery. Actual voice quality and Filmora subtitle alignment require manual checks with the owner's material. No universal runtime compatibility is claimed.
