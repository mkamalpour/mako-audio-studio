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

Verified here: Windows, Python 3.11.0, NumPy 2.4.6, Gradio 5.50.0, pyloudnorm 0.1.1, imageio-ffmpeg 0.6.0 (bundled FFmpeg 7.1). Other runtimes have not been verified. FFmpeg is included by the Python dependency; `MAKO_FFMPEG` can point to another installation.

## Your workflow

1. Choose a separate **episode project folder**. Select Koroush's and Reza's raw recording folders in **Recordings & names**. Use **Load project settings** when reopening an existing episode.
2. Keep **Mako codes** for your normal recordings. `K11` = Koroush / section 1 / dialogue 1. `R12` = Reza / section 1 / dialogue 2. Case does not matter. Codes run `1..9, a=10, b=11, ... z=35`: `Kbb` = section 11 / dialogue 11. Preview and correct assignments, then **Prepare recordings**.
3. In **Dialogue order**, fill the order from prepared recordings. Choose a section, choose a clip in **Clip to move**, move it up/down, or edit the rows. **Apply this section**, then **Save dialogue order and overrides**. `K1` here means dialogue 1 within the selected section; editor dialogue IDs are decimal, so `K11` here means dialogue 11. Every section has its own order.
4. In **Sound & export**, choose silence trimming, pauses, loudness matching, WAV/MP3, and optional speaker tracks. In **Build & listen**, preview the merge plan, inspect quality issues, export, and listen.

Default ordering alternates Koroush then Reza within each section. Explicit section arrangements can start with either person, contain consecutive lines from one person, and omit clips deliberately. Omitted or missing clips are reported. Moving rows uses buttons; drag-and-drop is not implemented.

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

The engine trims only clip edges, retains speech padding, matches loudness with a constant gain per speaker, and offers fixed or seeded natural pacing. Tone matching and speaker stems are optional. Talk-over can be disabled by setting its amount to zero; overlapping voices may clip and are checked. There is no automatic dialogue understanding, transcription, denoising, compressor, limiter, or true-peak mastering. Listen before publishing; automatic trim and pacing cannot guarantee natural speech.

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

Other commands: `intake`, `qc`, `script-template`, `check`; use `--help` for options. `all --dry-run` previews intake only, because merging requires prepared files. Preview, quality check, and dry-run do not save controls. GUI merge/quality previews use the saved script and timing overrides, so save them first. Exit codes: 0 clean, 1 issues, 2 stage failure.

Verification in this environment: 65 automated checks passed; the real browser workflow covered preparation, section reordering/save, and WAV/MP3 export with playback controls. Tests cover naming, source preservation, conversion, failed replacements, exact ordering, pauses, reproducibility, markers, and exports. Real voice quality and Filmora import still need listening/manual validation. Long episodes hold decoded clips in memory; no incremental render or packaged executable is included.
