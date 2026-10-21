# Mako Audio Studio

A local recording-to-episode workspace for Reza and Koroush's **Mako Entertainment** channel. Select each speaker's recording folder, review names, arrange clips section by section, and export an episode for Filmora or another editor.

## Start on Windows

Use **Python 3.11**. Run `install_windows.bat` once, then `run_mako.bat`. The interface opens in your browser at the local address printed by Gradio (usually http://127.0.0.1:7860).

Or install manually:

```powershell
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python mako.py check
.venv/Scripts/python mako.py
```

Verified here: Windows, Python 3.11.0, NumPy 2.4.6, SciPy 1.17.1, Gradio 5.50.0, pyloudnorm 0.1.1, imageio-ffmpeg 0.6.0 (bundled FFmpeg 7.1). Other runtimes have not been verified. FFmpeg is included by the Python dependency; `MAKO_FFMPEG` can point to another installation.

## Your workflow

1. Choose a separate **episode project folder**. Select Koroush's and Reza's raw recording folders in **Recordings & names**. Use **Load project settings** when reopening an existing episode.
2. Keep **Mako codes** for your normal recordings. `K11` = Koroush / section 1 / dialogue 1. `R12` = Reza / section 1 / dialogue 2. Case does not matter. Codes run `1..9, a=10, b=11, ... z=35`: `Kbb` = section 11 / dialogue 11. Preview and correct assignments, then **Prepare recordings**.
3. In **Dialogue order**, fill the order from prepared recordings. Select a section and drag its waveform cards into place. Use Earlier/Later buttons or Alt+Left/Right for keyboard/touch control. Remove clips to the available pool and add them back; Undo/Redo tracks board edits during this session. Card edits update the current script immediately. The advanced table still uses **Apply this section**. `K1` here means dialogue 1 in the selected section; dialogue IDs in the editor are decimal.
4. Choose a sound starting point in **Sound & export**: **Clean dialogue**, **Natural conversation**, or **Just join clips**, then adjust controls as needed. Back in the editor, **Render section preview** builds processed playback using the current episode settings without saving or exporting. Save your order to keep it between sessions; export from **Build & listen**.

Default ordering alternates Koroush then Reza within each section. Explicit arrangements can start with either person, contain consecutive lines from one speaker, or exclude clips and entire sections. Excluded recordings remain available for reinsertion. Desktop dragging and keyboard/touch buttons are supported; this is a clip-order board, not a sample-accurate waveform trimming editor.

### Other recording names

The other presets support separated names (`5.6`, `Section 5 - 6`), compact decimal numbers, or numbered recordings with section counts. Custom templates use `{section}` and `{line}` without the extension:

| Recording | Custom pattern | Meaning |
|---|---|---|
| `s11.wav` | `s{section}{line}` | section 1, dialogue 1 |
| `ab.wav` | `{section}{line}` | section 10, dialogue 11 |
| `K11_11.wav` | `K{section}_{line}` | section 11, dialogue 11 |

Adjacent placeholders each consume one code character. Separated placeholders consume decimal numbers. Files that do not match are reported rather than guessed. Standard working filenames are `K11.wav`, `Rbb.wav`, etc.; beyond code 35 the unambiguous fallback is `K36_1.wav`. Existing `S1_1_koroush.mp3` working files remain readable.

## Audio choices and safety

**WAV is the default intermediate and editing master.** MP3 is an optional compact export; choosing MP3 as the intermediate adds a lossy encoding stage. WAV conversion uses 24-bit PCM; same-format inputs are copied byte for byte. Conversion cannot restore quality lost in the original recording.

The engine trims only clip edges, retains speech padding, matches loudness with a constant gain per speaker, and offers fixed or seeded natural pacing. Tone matching and speaker stems are optional. Talk-over can be disabled by setting its amount to zero; a final constant attenuation protects mix and speaker-track sample peaks when overlaps need extra headroom. This preserves dynamics rather than compressing voices. MP3 encoding can introduce peak overshoots. There is no automatic dialogue understanding, transcription, denoising, compressor, limiter, or true-peak mastering. Listen before publishing; automatic trim and pacing cannot guarantee natural speech.

Raw audio is never renamed or edited in place. A replacement is verified before the matching old clean file is archived under `clean/_replaced/`; unrelated prepared lines stay available. Add mode keeps prepared lines. Retakes are preserved in `clean/_duplicates/`. Every merge gets a new output folder. Incomplete exports retain `INCOMPLETE.txt`. Keep your own backups: this is not a transactional database, and disk/process failures can interrupt a run.

## Project files and exports

```text
mako.py                 UI / CLI launcher
mako_pipeline/          Naming, intake, ordering, DSP, export, UI
tests/                 Synthetic audio and workflow regression tests
docs/SPEC.md           Control formats and implementation limits
```

An episode folder contains `clean/`, `merged/<timestamp>/`, and optional local controls: `mako_settings.json`, `script.txt`, `overrides.csv`, `rename_overrides.csv`. Settings are also remembered locally beside the launcher. Recordings, outputs, settings, environments, and credentials are excluded from Git.

Exports include section WAV/MP3 files, `FULL.wav`/`FULL.mp3`, SRT clip labels (not transcriptions), timeline CSV, quality reports, and a SHA-256 manifest. Optional speaker WAV tracks share the same start time. Import `FULL.wav` into Filmora; test SRT support in your Filmora version.

## Command line and checks

```powershell
.venv/Scripts/python mako.py all --project D:/Mako/Episode01 --koroush D:/Recordings/K --reza D:/Recordings/R
.venv/Scripts/python mako.py preview --project D:/Mako/Episode01 --koroush D:/Recordings/K --json
.venv/Scripts/python mako.py merge --project D:/Mako/Episode01 --dry-run
.venv/Scripts/python -m pip install -r requirements-dev.txt
.venv/Scripts/python -m pytest -q
```

Other commands: `intake`, `qc`, `script-template`, `check`; use `--help` for options. `all --dry-run` previews intake only, because merging requires prepared files. Preview, quality check, and dry-run do not save controls. GUI merge/quality previews use the current applied script and timing edits without saving them. Explicit invalid UI/CLI settings produce a clear error; invalid saved values may recover to defaults; other invalid configuration is reported. Exit codes: 0 clean, 1 issues, 2 stage failure.

Verification in this environment: **106 automated checks passed**. Browser checks covered real drag/save/export, undo/redo, remove/reinsert, keyboard ordering, and processed section playback. A regression test verifies section-preview audio matches its full export within integer sample precision. Tests also cover naming, source preservation, failed replacements, exact ordering, seeded pacing, and stale-editor action rejection. A 96-clip synthetic episode (12 minutes 25 seconds) passed conversion, corrupt-input reporting, WAV/MP3 export, stem alignment, 96 SRT cues, and manifest hash checks; source hashes stayed unchanged. Browser checks also rejected playback when the order changed during rendering. Real voice quality and Filmora import still require listening/manual validation. Loudness analysis streams bounded-size chunks, but long episodes still hold decoded clips in memory; section preview analyzes the episode to keep global levels/pacing consistent, so it is not instantaneous. No incremental render or packaged executable is included.
