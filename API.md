# Anki Miner two-call API

Another program can run Anki Miner without its window, in two calls. `prepare` reads an episode's subtitles, applies the user's filters and stops before anything is cut or sent to Anki. It lists every subtitle line and every word the run could mine. The calling program picks words, and a line for each, with its own data. `commit` mines only those words and reports a note ID for each.

Only `commit` writes to Anki, and only the words it is given. Anki Miner's known-words list and statistics are never touched: the calling program keeps its own record of what the user knows and mined.

Requirements: Anki running with the AnkiConnect add-on, an offline dictionary, and ffmpeg (bundled with the installers). Sources are video episodes (a video file and a subtitle file); YouTube, audiobooks, books and manga are not supported yet.

## Calling it

| Install | Program |
|---|---|
| pip | `anki-miner` |
| Linux .deb | `/usr/bin/anki-miner` |
| Linux AppImage | `./AnkiMiner-<version>-Linux-x86_64.AppImage` |
| macOS | `/Applications/AnkiMiner.app/Contents/MacOS/AnkiMiner` |
| Windows | `%LOCALAPPDATA%\Programs\AnkiMiner\AnkiMiner.exe` |

```
AnkiMiner --api prepare RUN_FILE
AnkiMiner --api commit COMMIT_FILE
AnkiMiner --api check --language CODE [--profile ID]
AnkiMiner --api version
AnkiMiner --api profiles
AnkiMiner --api settings-export --language CODE --out FILE [--profile ID]
```

Each call writes one JSON line to stdout, its verdict. Exit code 0 means the verdict was written; any other exit code is a crash. stderr carries free-form diagnostics and the output of child processes such as ffmpeg: read it or send it to `DEVNULL`, because an unread stderr pipe can fill up and stall the call. The log goes to `anki_miner.api.log` in Anki Miner's data folder (`~/.anki_miner`, or `ANKI_MINER_HOME` when set).

On Windows, `AnkiMiner.exe` is a GUI-subsystem program: a caller that captures stdout gets the verdict, but `cmd.exe` shows nothing.

A verdict:

```json
{"schema": 1, "command": "prepare", "ok": true, "error": null, "message": null,
 "runs": [{"run_id": "job-1234-ep05", "ok": true, "error": null, "message": null, "file": "candidates.json"}]}
```

- A call refused as a whole has `ok: false`, an `error` code, a `message` and `runs: []`.
- With several runs, `ok` is false if any run failed; each run carries its own `error` and `message`, and the top-level `error` stays null.
- `check`, `version` and `profiles` put their answer in `result`.

`prepare` and `commit` run one at a time: they hold Anki Miner's instance lock. They give `BUSY` while the Anki Miner window is open (including a window opened past its "already running" warning) or while another command-line or API run is working; the `message` says which. `check`, `version`, `profiles` and `settings-export` run at any time. On Windows every call holds the app's mutex, so the installer waits for it.

## The run folder

`run_dir` must exist. Each run uses `<run_dir>/<run_id>/`; a `run_id` is 1 to 64 letters, digits, `-` and `_`. Files are UTF-8 without a BOM and are replaced atomically.

| File | Written by | Content |
|---|---|---|
| `candidates.json` | prepare | lines and candidate words |
| `prepared.json` | prepare | what commit checks for `RUN_STALE`; do not edit |
| `progress.json` | prepare, commit | the current stage while a run works |
| `result-<n>.json` | commit | one per commit of that run; `n` counts up from 1 |
| `cancel` | the caller | stops the run (see Cancelling) |
| `media/` | prepare, commit | temporary clips and pictures, removed before the run ends |

`prepare` on an existing `run_id` replaces it: the files above are removed and anything else in the folder is left alone. The folder belongs to the caller, who deletes it when done.

## prepare

The run file:

| Key | Value |
|---|---|
| `schema` | `1` |
| `run_dir` | an existing folder |
| `profile` | a profile `id` as `profiles` lists it; `null` or left out is the active profile |
| `language` | the mining language code (`ja`, `zh`, `ko`, …) |
| `config` | optional settings for this run only (below) |
| `episodes` | one or more episodes; several in one call share one dictionary load |

The active profile reads the settings the window uses; before Anki Miner was ever set up, that is the defaults, as in the app. Any other profile reads that profile's file; a profile that does not exist or cannot be read gives `PROFILE_UNREADABLE`, never the defaults. The settings then switch to `language` the way the window's language switch does, and `config` is applied on top. Nothing is saved.

Allowed `config` keys: `anki_deck_name`, `anki_note_type`, `anki_fields`, `card_type`, `card_type_marker_fields`, `allow_duplicate_cards`, `merge_incomplete_cues`, `max_parallel_workers`, `min_frequency_rank`, `max_frequency_rank`, `use_blacklist`, `use_whitelist`, `deduplicate_sentences`, `use_i_plus_one_filter`, `max_sentence_duration_seconds`, `max_sentence_chars`, `exclude_hiragana_only_words`, `exclude_katakana_only_words`. `anki_fields` and `card_type_marker_fields` merge key by key into the profile's. An unknown key, a value of the wrong type, or a value Anki Miner's settings refuse gives `BAD_RUN_FILE`; the ranges themselves are not checked.

API runs never subtract words the user already knows (Anki's cards, the known-words list, the ignore list): the caller filters known words itself.

An episode:

| Key | Value |
|---|---|
| `run_id` | required |
| `video_file`, `subtitle_file` | required paths; a relative path is taken from the folder `prepare` runs in |
| `subtitle_offset` | seconds, or `null` for the profile's offset |
| `audio_track_override` | 0-based audio track, or `null` to find the mining language's track |
| `source_label_override` | the card's Source text instead of `<series> — <episode>` |
| `secondary_subtitle_file`, `secondary_subtitle_offset` | a second-language track for the sentence translation field |
| `series_name_override`, `episode_name_override` | instead of the video's folder and file names |
| `tags` | added to the profile's tags on this episode's cards |

Before any episode runs, `prepare` checks the language pack, dictionary indexes that need re-importing, ffmpeg and ffprobe, the Anki deck, note type and field mapping, and the offline dictionary. A failure refuses the call with `SETUP_ERROR`, or `ANKI_UNREACHABLE` when Anki does not answer. Per episode, a video that does not open gives `VIDEO_UNREADABLE` and a subtitle that cannot be read gives `SUBTITLE_UNREADABLE`; the other episodes still run.

`prepare` writes nothing to Anki and nothing outside the run folder except its log.

`candidates.json`:

```json
{"schema": 1, "run_id": "job-1234-ep05",
 "lines": [[0, 12.48, 14.9, "約束したでしょう", [0, 0]],
           [1, 15.02, 17.6, "今日こそは言うよ", [0, 1]],
           [2, 17.64, 20.1, "ちゃんと", [1, 0]]],
 "candidates": [{"mined_form": "約束", "lemma": "約束", "orth_base": "約束", "surface": "約束",
                 "expression_reading": "やくそく", "line": 0, "sentence_candidates": [0, 57, 203]}],
 "dropped": null}
```

- `lines`: every subtitle line in file order as `[index, start, end, text, merge]`. Times are seconds after the offset. `merge` is the automatic merge `[before, after]` that line would get; always `[0, 0]` with `merge_incomplete_cues` off.
- `candidates`: one per word. `mined_form` is the card front and the key `commit` takes. `line` is the word's own line (null in the rare case its line cannot be located). `sentence_candidates` lists every line the word can be mined from, always including `line`.
- `dropped` is always `null`: this build does not report removed words.

## commit

The commit file:

```json
{"schema": 1, "run_dir": "C:/Users/me/AppData/Local/Caller/runs",
 "runs": [{"run_id": "job-1234-ep05",
           "words": [{"mined_form": "約束", "line": 57},
                     {"mined_form": "今日", "line": 1, "line_expansion": [0, 1]}]}]}
```

- Name each word once. There is no "all".
- `line` is one of the word's `sentence_candidates`; left out, the word's own line.
- `line_expansion` is `[before, after]`: the lines merged into the sentence and the clip. Left out, it is the chosen line's `merge` from `lines`; `[0, 0]` means no merge. It has no length cap.

Each run is checked before any media is cut. A refused run has `file: null`:

| Code | When |
|---|---|
| `UNKNOWN_RUN` | the run folder has no prepared run |
| `BAD_LINE` | a `line` outside that word's `sentence_candidates`, or a negative `line_expansion` |
| `VIDEO_UNREADABLE` | the video no longer opens |
| `RUN_STALE` | something changed since `prepare`: Anki Miner's version, the video or subtitle files (size or modification time), the settings, or the enabled dictionary and frequency indexes |
| `SETUP_ERROR`, `ANKI_UNREACHABLE` | the run's settings fail the checks `prepare` makes |

For `RUN_STALE` the settings are resolved again from the run file's profile, language and `config`. Settings a video run never reads do not count, so using the window between the two calls does not make a run stale: window options (theme, zoom, shortcuts), other tabs' options (Condense, Download, Deck Builder, YouTube, transcription, Reading, "Review words before mining"), the yt-dlp, alass and mokuro locations, and other languages' settings.

`commit` then repeats `prepare`'s parsing and filtering with the saved settings and mines the named words. A named word the repeat does not produce is `not_found`. Runs in one call go one at a time, and each run's `result-<n>.json` is written as it ends, before the next starts. Nothing is retried: call `commit` again on the same run with any subset of words; the media is cut again.

`commit` writes to Anki, to its run folder, and to the pronunciation-audio cache in Anki Miner's data folder, which the app shares.

`result-<n>.json`:

```json
{"schema": 1, "run_id": "job-1234-ep05", "outcome": "success",
 "anki_write_state": "note_write_confirmed", "failure_is_transient": false,
 "error": null, "message": null, "media_store_failures": 0,
 "words": [{"mined_form": "約束", "status": "created", "note_id": 1727000000001, "media_missing": [],
            "line_range": [57, 57], "sentence": "…", "start": 812.3, "end": 815.0},
           {"mined_form": "今日", "status": "not_created", "note_id": null, "media_missing": [],
            "line_range": [1, 2], "sentence": "…", "start": 15.02, "end": 20.1}]}
```

- `outcome`: `success`, `failed` or `cancelled`. `error` and `message` say why a run failed.
- `anki_write_state`: `no_note_write`, `note_write_uncertain` or `note_write_confirmed`. After a failure with `note_write_uncertain`, check Anki before running the same words again.
- `failure_is_transient`: true when the failure was a dropped connection that running again could get past.
- `words`, in the commit file's order. `status` is `created` (with its `note_id`), `not_created` (Anki refused it as a duplicate, no dictionary had it, its media could not be cut, or the run stopped first) or `not_found`.
- `media_missing` lists `picture` and `audio` when that clip or picture could not be cut. Missing pronunciation audio is not reported.
- `media_store_failures` counts the media files Anki could not store during the run.
- `line_range` is the first and last line after any merge; `sentence`, `start` and `end` are the merged sentence and its time window.

## Progress and cancelling

While a run works, `progress.json` holds `{"schema": 1, "run_id": …, "stage": 3, "stages": 5, "done": 12, "total": 26}`. The stages are parsing, filtering, media, definitions and cards; `prepare` stops after filtering.

To stop a run, create an empty file named `cancel` in its folder. The run stops at its next step, reports `CANCELLED`, and Anki Miner deletes the file. Only that run stops; a call with several runs moves on to the next. A `cancel` file already there when a run starts cancels it at once. On Linux and macOS, SIGINT or SIGTERM stops the current run and every later run in the call. Notes already added to Anki stay.

## Error codes

| Code | Where | Meaning |
|---|---|---|
| `BUSY` | call | the window or another run is open |
| `BAD_ARGUMENTS` | call | the command line does not parse, or names an unknown language or an `--out` folder that does not exist |
| `BAD_RUN_FILE` | call | the run or commit file is not valid JSON or breaks the rules above |
| `PROFILE_UNREADABLE` | call, run | the profile does not exist or cannot be read |
| `SETUP_ERROR` | call, run | language pack, dictionary index, ffmpeg, deck, note type, fields or offline dictionary |
| `ANKI_UNREACHABLE` | call, run | AnkiConnect does not answer |
| `UNKNOWN_RUN` | run | no prepared run with that `run_id` |
| `BAD_LINE` | run | a line pick outside the word's candidates, or a negative expansion |
| `RUN_STALE` | run | inputs or settings changed since `prepare` |
| `VIDEO_UNREADABLE` | run | the video does not open |
| `SUBTITLE_UNREADABLE` | run | the subtitle file cannot be read |
| `MINING_FAILED` | run | the run itself failed; `message` says why |
| `CANCELLED` | run | stopped by a `cancel` file or a signal |
| `INTERNAL` | call, run | an unexpected error; details are in the log |

`message` carries the English text. Codes never change; new ones may be added.

## check, version, profiles, settings-export

`check --language CODE [--profile ID]` puts `{"ready": false, "items": [...]}` in `result`, one item per check: `anki`, `deck`, `note_type`, `fields`, `dictionary`, `resources` (dictionary or frequency indexes that need re-importing), `language_pack`, `ffmpeg` and `ffprobe`. Each item has `name`, `ok` and `message` (null when ok). When Anki does not answer, `deck`, `note_type` and `fields` are reported as not checked.

`version` puts `{"schema": 1, "app": "3.5.0", "commands": [...], "features": []}` in `result`. `features` will list later additions this build has.

`profiles` puts `{"profiles": [{"id": "anime", "name": "Anime", "active": true}, …]}` in `result`. Before the user has created any profile, the list holds one, `default`.

`settings-export --language CODE --out FILE [--profile ID]` writes what Settings → Export writes, with that language's deck, note type and fields. A language the profile has never used exports its defaults and `"configured": false`. As in the app's export, file paths and resource lists are left out. The file can be imported in Settings → Import.

`schema` goes up only for breaking changes. New keys can appear; ignore keys you do not know.

## Differences from the proposal

This build implements the proposal's "First" list (v6) and takes every "Smaller version": word statuses are `created`, `not_created` and `not_found`; the setup codes are one `SETUP_ERROR`; and `prepare` checks the Anki deck and note type. `profiles` is its own command, as in the full version. Otherwise:

- Two codes were added: `BAD_ARGUMENTS` for a command line that does not parse, and `MINING_FAILED` for a run that failed inside the pipeline.
- Verdict runs and result files carry a `message` beside `error`.
- `check` has a `resources` item for indexes that need re-importing.
- `dropped` is always `null`.
- `media_missing` covers the picture and the audio clip. Missing pronunciation audio is not reported, and media Anki failed to store is a count for the run, `media_store_failures`, not per word.
- `commit` also writes to the pronunciation-audio cache in Anki Miner's data folder.
- `file` is also `null` for runs refused with `SETUP_ERROR`, `ANKI_UNREACHABLE` or `SUBTITLE_UNREADABLE` before any media was cut.
- A `cancel` file present when a run starts cancels that run.
- `RUN_STALE` ignores settings a video run never reads (see commit).
- `line_expansion` has no length cap; the Word Curator's 30-second cap is not enforced.
- Candidate words are the words that pass the user's filters, so a word the caller wants must not be filtered out by the profile; turn off the filters the caller does not want with `config`.

## Example (Python)

```python
import json
import pathlib
import subprocess

EXE = "anki-miner"
runs = pathlib.Path("runs")
runs.mkdir(exist_ok=True)


def api(*args):
    proc = subprocess.run([EXE, "--api", *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr)
    return json.loads(proc.stdout)


pathlib.Path("run.json").write_text(json.dumps({
    "schema": 1, "run_dir": str(runs.resolve()), "language": "ja",
    "config": {"anki_deck_name": "Mining", "min_frequency_rank": 0, "max_frequency_rank": 0},
    "episodes": [{"run_id": "ep05", "video_file": "ep05.mkv", "subtitle_file": "ep05.ja.srt"}],
}), encoding="utf-8")
verdict = api("prepare", "run.json")
if not verdict["ok"]:
    raise SystemExit(verdict)

candidates = json.loads((runs / "ep05" / "candidates.json").read_text(encoding="utf-8"))
# Here: the first ten words, each on the last line it appears on (a word whose
# line could not be located has no candidates and keeps its own line).
picks = [{"mined_form": c["mined_form"], **({"line": c["sentence_candidates"][-1]} if c["sentence_candidates"] else {})}
         for c in candidates["candidates"][:10]]

pathlib.Path("commit.json").write_text(json.dumps({
    "schema": 1, "run_dir": str(runs.resolve()), "runs": [{"run_id": "ep05", "words": picks}],
}), encoding="utf-8")
verdict = api("commit", "commit.json")
result = json.loads((runs / "ep05" / verdict["runs"][0]["file"]).read_text(encoding="utf-8"))
for word in result["words"]:
    print(word["mined_form"], word["status"], word["note_id"])
```
