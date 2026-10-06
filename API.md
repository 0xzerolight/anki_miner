# Anki Miner mining API

Another program can run Anki Miner without its window. One `mine` call takes an episode and the words to mine, each optionally with the subtitle line its card should use. It reports a note ID for each word, or the reason there is none. The calling program picks the words with its own data.

`mine` writes to Anki only the words it is given. Anki Miner's known-words list and statistics are never touched: the calling program keeps its own record of what the user knows and mined.

Requirements: Anki running with the AnkiConnect add-on, an offline dictionary, and ffmpeg (bundled with the installers); `fetch` also needs yt-dlp. Sources are video episodes (a video file and a subtitle file), including YouTube videos that `fetch` downloads first; audiobooks, books and manga are not supported yet.

## Calling it

| Install | Program |
|---|---|
| pip | `anki-miner` |
| Linux .deb | `/usr/bin/anki-miner` |
| Linux AppImage | `./AnkiMiner-<version>-Linux-x86_64.AppImage` |
| macOS | `/Applications/AnkiMiner.app/Contents/MacOS/AnkiMiner` |
| Windows | `%LOCALAPPDATA%\Programs\AnkiMiner\AnkiMiner.exe` |

```
AnkiMiner --api mine RUN_FILE
AnkiMiner --api check --language CODE [--profile ID]
AnkiMiner --api version
AnkiMiner --api profiles
AnkiMiner --api settings-export --language CODE --out FILE [--profile ID]
AnkiMiner --api settings-import FILE --language CODE (--name NAME | --profile ID)
AnkiMiner --api render RUN_FILE
AnkiMiner --api media MEDIA_FILE
AnkiMiner --api setup --language CODE --progress FILE [--profile ID]
AnkiMiner --api fetch FETCH_FILE
```

Each call writes one JSON line to stdout: its verdict. Exit code 0 means the verdict was written; any other exit code is a crash.

stderr carries free-form diagnostics and the output of child processes such as ffmpeg. Read it or send it to `DEVNULL`: an unread stderr pipe can fill up and stall the call.

The log goes to `anki_miner.api.log` in Anki Miner's data folder (`~/.anki_miner`, or `ANKI_MINER_HOME` when set).

On Windows, `AnkiMiner.exe` is a GUI-subsystem program: a caller that captures stdout gets the verdict, but `cmd.exe` shows nothing.

A verdict:

```json
{"schema": 1, "command": "mine", "ok": true, "error": null, "message": null,
 "runs": [{"run_id": "job-1234-ep05", "ok": true, "error": null, "message": null, "file": "result-1.json"}]}
```

- A call refused as a whole has `ok: false`, an `error` code, a `message` and `runs: []`.
- With several runs, `ok` is false if any run failed. Each run carries its own `error` and `message`, and the top-level `error` stays null.
- `check`, `version`, `profiles`, `settings-import` and `setup` put their answer in `result`.

`mine` runs one at a time, and beside an open window that is not mining. It gives `BUSY` in two cases, and the `message` says which:
- the Anki Miner window is mining, or its Word Curator is open;
- another command-line or API run is working.

`settings-import` and `setup` change settings, so they also give `BUSY` while any Anki Miner window is open, including one opened past its "already running" warning.

A run beside the window does not stop the window from mining. If both add the same word, Anki keeps the first, and the other reports it `refused`. Importing, removing or downloading dictionaries in the window while a run works can fail (on Windows the run holds them open); do it again after the run.

A dry run, `render` and `media` take no lock.

`check`, `version`, `profiles`, `settings-export` and `fetch` run at any time. On Windows every call holds the app's mutex, so the installer waits for it.

## The run folder

`run_dir` must exist. Each episode uses `<run_dir>/<run_id>/`; a `run_id` is 1 to 64 letters, digits, `-` and `_`. Files are UTF-8 without a BOM and are replaced atomically.

| File | Written by | Content |
|---|---|---|
| `progress.json` | mine, render, fetch | the current stage while a run works |
| `result-<n>.json` | mine | one per `mine` of that `run_id`; `n` counts up from 1 |
| `render-<n>.json` | render | one per `render` of that `run_id` |
| `render-<n>/` | render | the media its fields name; the caller deletes them |
| `media-<n>.json` | media | one per `media` of that `run_id` |
| `media-<n>/` | media | the clips and pictures it cut; the caller deletes them |
| `fetch-<n>.json` | fetch | one per successful `fetch` of that `run_id`; names the files below |
| `fetch-<n>/` | fetch | the downloaded video and subtitle; kept until the caller deletes them |
| `cancel` | the caller | stops a `mine`, `render` or `fetch` run (see Cancelling) |
| `media/` | mine, render, fetch | temporary clips and pictures of a `mine` or `render`, and the audio a `fetch` transcribes, removed before it ends |

A run folder serves one call at a time: calls on the same `run_id` share its `progress.json`, `cancel` file and `media/`. Calls on different `run_id`s can run side by side.

Mining a `run_id` again starts over and writes the next `result-<n>.json`; earlier results stay. The folder belongs to the caller, who deletes it when done.

## mine

The run file:

| Key | Value |
|---|---|
| `schema` | `1` |
| `run_dir` | an existing folder |
| `profile` | a profile `id` as `profiles` lists it; `null` or left out is the active profile |
| `language` | the mining language code (`ja`, `zh`, `ko`, …) |
| `config` | optional settings for this run only (below) |
| `episodes` | one or more episodes; several in one call share one dictionary load |
| `dry_run` | `true`: report what the run would do, without cutting media or writing to Anki (below) |

The settings come from the chosen profile:
- The active profile reads the settings the window uses. Before Anki Miner was ever set up, those are the defaults, as in the app.
- Any other profile reads that profile's file. A profile that does not exist or cannot be read gives `PROFILE_UNREADABLE`, never the defaults.

The settings then switch to `language` the way the window's language switch does, and `config` is applied on top. Nothing is saved.

Allowed `config` keys:
- `anki_deck_name`, `anki_note_type`, `anki_fields`, `card_type`, `card_type_marker_fields`
- `allow_duplicate_cards`, `merge_incomplete_cues`, `max_parallel_workers`
- `min_frequency_rank`, `max_frequency_rank`, `use_blacklist`, `use_whitelist`, `bold_target_in_sentence`
- `max_sentence_duration_seconds`, `max_sentence_chars`, `exclude_hiragana_only_words`, `exclude_katakana_only_words`

`anki_fields` and `card_type_marker_fields` merge key by key into the profile's. An unknown key, a value of the wrong type, or a value Anki Miner's settings refuse gives `BAD_RUN_FILE`. The ranges themselves are not checked.

API runs never subtract words the user already knows (Anki's cards, the known-words list, the ignore list): the caller names only words it wants mined. For the same reason the sentence rules are off (one card per sentence and i+1): `deduplicate_sentences` and `use_i_plus_one_filter` may be sent as `false`; `true` gives `BAD_RUN_FILE`. Every named word also counts as whitelisted for its episode: it gets past the profile's optional filters, the name lists and the part-of-speech and script rules, as a word in the profile's whitelist does. The profile's own whitelist still applies to its own words when `use_whitelist` is on. Particles, auxiliaries and other grammar words are never rescued. The dictionary check, Anki's duplicate check and, unless `allow_duplicate_cards` is on, the merge of words that share one dictionary entry still apply.

An episode:

| Key | Value |
|---|---|
| `run_id` | required |
| `video_file`, `subtitle_file` | required paths; a relative path is taken from the folder `mine` runs in |
| `words` | required: the words to mine (below) |
| `subtitle_offset` | seconds added to every subtitle time; `0` when left out (the profile's offset is not used) |
| `audio_track_override` | 0-based audio track, or `null` to find the mining language's track |
| `source_label_override` | the card's Source text instead of `<series> — <episode>` |
| `secondary_subtitle_file`, `secondary_subtitle_offset` | a second-language track for the sentence translation field |
| `series_name_override`, `episode_name_override` | instead of the video's folder and file names |
| `tags` | added to the profile's tags on this episode's cards |

A word:

| Key | Value |
|---|---|
| `word` | required: the word's card front (`mined_form`), or its dictionary form |
| `line_start` | the start of the line to use, in seconds, as written in the subtitle file |
| `line_text` | text the line to use contains |
| `line_expansion` | `[before, after]`: the lines merged into the sentence and the clip |
| `surface` | the word as written on its line, when it differs from `word` (走り出した for 走り出す) |
| `reading` | its reading, to choose among dictionary entries |

- **Matching the word.** `word` is compared after Unicode NFC normalization. It names the word whose card front equals it, else one whose dictionary form does, else one that matches through the fold: the comparison the language uses for one card per run (Chinese Simplified and Traditional, letter case). 头发 names 頭髮 on a Traditional subtitle, and the card keeps the subtitle's spelling. With a line named, only words on that line count (see Choosing the line); with none, it is the first such word, on its own line.
- **Choosing the line.** A word can be mined from any line it appears on.
  - `line_start` names the line starting nearest to it; a tie goes to the earlier line. `line_text` names the first line containing it, compared after the cleaning the subtitle lines get (markup, speaker tags, furigana readings, the profile's text filter) and NFKC normalization, with whitespace ignored, so a whole line copied from the file matches. With both, `line_start` wins.
  - On the named line the word is the one whose card front equals `word`, else whose dictionary form does, else that matches through the fold.
  - When the episode produces no such word on that line, the run makes the word from the line: it finds `surface` (else `word`) in the line, compared as `line_text` is, first match. The card's front is `word`; its reading is `reading`, else the one the episode or the parser gives the word, else the dictionary's when it has only one; its sentence, clip and picture are that line's, the match in bold when bold is on. The row says `from_line: true`. A word the dictionary check or the merge removed is never made from a line. Not in the line: `not_found`.
  - With neither key, or a `line_text` no line contains, the word keeps its own line: its first in the episode.
- **Merging lines.** Left out, `line_expansion` is the automatic merge Anki Miner gives the chosen line (none with `merge_incomplete_cues` off); `[0, 0]` means no merge. Merged lines stop at the file's ends and at 30 seconds including the audio padding, as in the Word Curator. Lines after the chosen one are added first, then lines before.
- **Repeats.** When two entries reach the same word (fronts compared as one card per run compares them), the first mines it and the rest come back `duplicate`. Entries for a word the run never produced, or that the dictionary check removed, all share that status (`not_found` or `no_definition`).
- **Refusals.** These refuse the whole call with `BAD_RUN_FILE` before anything runs: an empty or non-string `word`, `line_text`, `surface` or `reading`, a negative or non-finite `line_start`, or a negative `line_expansion`.

Before any episode runs, `mine` checks:
- the language pack;
- dictionary indexes that need re-importing;
- ffmpeg and ffprobe;
- the Anki deck, note type and field mapping;
- the offline dictionary.

A failure refuses the call with `SETUP_ERROR`, or with `ANKI_UNREACHABLE` when Anki does not answer. Per episode, a video that does not open gives `VIDEO_UNREADABLE`, and a subtitle that cannot be read gives `SUBTITLE_UNREADABLE`; the other episodes still run.

Episodes run one at a time, and each run's `result-<n>.json` is written as it ends, before the next starts. Nothing is retried: mine the same `run_id` again with any subset of the words. The media is cut again, and a word Anki now has comes back `duplicate` (with `allow_duplicate_cards` on, a word already in the run's deck).

`mine` writes to Anki, to its run folder, and to the pronunciation-audio cache in Anki Miner's data folder, which the app shares.

`result-<n>.json`:

```json
{"schema": 1, "run_id": "job-1234-ep05", "dry_run": false, "outcome": "success",
 "anki_write_state": "note_write_confirmed", "failure_is_transient": false,
 "error": null, "message": null, "media_store_failures": 0,
 "words": [{"word": "約束", "mined_form": "約束", "status": "created", "note_id": 1727000000001,
            "from_line": false, "media_missing": [], "line_start": 812.3, "sentence": "…",
            "start": 812.3, "end": 815.0, "filter": null},
           {"word": "頑張る", "mined_form": null, "status": "not_found", "note_id": null,
            "from_line": false, "media_missing": [], "line_start": null, "sentence": null,
            "start": null, "end": null, "filter": null}]}
```

- `outcome` is `success`, `failed` or `cancelled`. `error` and `message` say why a run failed.
- `anki_write_state` is `no_note_write`, `note_write_uncertain` or `note_write_confirmed`. After a failure with `note_write_uncertain`, check Anki before running the same words again.
- `failure_is_transient` is true when the failure was a dropped connection that running again could get past.
- `words` has one row per entry, in the run file's order. `status` is one of:

| Status | Meaning |
|---|---|
| `created` | a note was added; `note_id` is its ID |
| `duplicate` | Anki already has the word, or an earlier entry named the same word |
| `refused` | Anki added no note although its duplicate check passed |
| `no_definition` | no dictionary defines it |
| `media_failed` | no card: its picture could not be cut (its audio clip, when no picture field is mapped) |
| `not_found` | the episode does not produce the word, or the merge of words that share one dictionary entry removed it (then `filter` says `duplicate-expression`) |
| `not_attempted` | the run stopped (cancelled or failed) before reaching it |
| `uncertain` | its note was being added when the connection failed; check Anki before mining it again |
| `ready` | dry run only: the run would mine it |
| `rendered` | render only: its note's fields and media are in the run folder |

- `media_missing` lists `picture` and `audio` when that clip or picture could not be cut. Missing pronunciation audio is not reported.
- `media_store_failures` counts the media files Anki could not store during the run.
- `word` is the entry's own; `mined_form` is the card front it matched.
- `line_start` is the start of the chosen line as written in the subtitle file.
- `from_line` is `true` for a word made from its named line.
- `sentence`, `start` and `end` are the merged sentence and its window in the video, without the audio padding.
- Every key is always present. `filter` is `duplicate-expression` on a `not_found` word that the merge of words sharing one dictionary entry removed, with `mined_form` set to the word it merged into; otherwise `null`.

## A dry run

`dry_run: true` runs the same file without cutting media or writing anything to Anki, and needs neither Anki nor ffmpeg. It does not open the video either, so a `video_file` that does not open shows up only on a real `mine`. It runs any time, beside calls on other `run_id`s. Each word the run would mine comes back `ready`, `no_definition` (a word made from its line that no offline dictionary defines) or, when Anki answers, `duplicate`; the others as for `mine`. The result file says `"dry_run": true`.

## render

`render` takes the same run file as `mine` (`dry_run: true` gives `BAD_RUN_FILE`) and does everything `mine` does up to the notes. Instead of adding them, it writes each word's note fields and media to the run folder. Nothing goes to Anki and there is no duplicate check, so a word Anki already has is rendered too; Anki must still answer, for the note type's fields. It runs any time, beside calls on other `run_id`s. `render-<n>.json` has the result file's keys except `anki_write_state` and `dry_run`. A word that became a note is `rendered`; its row adds `fields` (the note's fields by Anki field name) and `files` (the files they name, in `render-<n>/`). Each file has the name `mine` sends Anki for it (Anki may shorten a very long one), so a caller can store it under that name and update a note with the fields as given. Other words have `fields: null` and `files: []`. Images inside dictionary definitions are not copied.

## media

`media` cuts a clip and a picture for each line it is given. The media file:

| Key | Value |
|---|---|
| `schema` | `1` |
| `run_dir` | an existing folder |
| `profile` | a profile `id`, as for `mine` |
| `language` | the mining language code |
| `still_height` | the picture's height in pixels, animated or still; left out, the profile's (a still is the video's own size) |
| `audio_bitrate` | kbps; left out, the profile's |
| `episodes` | one or more episodes |

An episode:

| Key | Value |
|---|---|
| `run_id` | required |
| `video_file`, `subtitle_file` | required paths, as for `mine` |
| `lines` | required: the lines to cut |
| `subtitle_offset` | seconds added to every subtitle time; `0` when left out |
| `audio_track_override` | 0-based audio track, or `null` to find the mining language's track |

Each line is a `line_start` and an optional `line_expansion`, chosen and merged as for `mine`. `media` needs no parse, no dictionary and no Anki, and runs any time. It takes no `cancel` file, and a signal ends it without a verdict. `media-<n>.json` lists per line its `line_start`, `start`, `end`, `text`, `picture` and `audio` (paths in the run folder, `null` when that cut failed). The files are `media-<n>/<k>.<ext>`, `k` being the line's place in `lines`, from 1. `still_height` and `audio_bitrate` are whole numbers of 1 or more. Without ffmpeg or ffprobe the call gives `SETUP_ERROR`; per episode, a video that does not open gives `VIDEO_UNREADABLE`, and a subtitle that cannot be read or has no lines gives `SUBTITLE_UNREADABLE`.

## fetch

`fetch FETCH_FILE` downloads YouTube videos for `mine`: per episode, what Video → YouTube does before mining. Nothing goes to Anki, and it runs at any time, beside the window too.

The fetch file has `schema` (`1`), `run_dir`, `profile` and `language` as in the run file, and `episodes`:

| Key | Value |
|---|---|
| `run_id` | required |
| `youtube_url` | required: one video's link. A playlist link gives `BAD_RUN_FILE` |
| `youtube_subtitle_source` | `auto` (captions, else transcribe), `captions` (refuse a video without them) or `transcribe`; left out, the profile's |
| `youtube_align_captions` | `true` aligns downloaded captions with the audio; left out, the profile's |

Per episode it probes the video, downloads it with its subtitle into `fetch-<n>/`, transcribes or aligns, and writes `fetch-<n>.json`: `video_file`, `subtitle_file` (absolute paths), `sub_source` (`manual`, `auto` for YouTube's automatic captions, or `generated` for a transcription), `video_id`, `title`, `duration` (whole seconds), and the `episode_name_override`, `series_name_override` and `source_label_override` a YouTube run in the window sets. Mine the files with an episode that sets those three, so its cards read like the window's YouTube cards.
- Fetching a `run_id` again downloads again, into the next `fetch-<n>/`. It first removes every `fetch-<n>/` in that run folder with no `fetch-<n>.json`, so keep the json while you need its folder.
- `progress.json` stages are probing, downloading, then transcribing or aligning; a run that does neither ends at stage 2 of 3. `done` and `total` count percent while downloading and transcribing, and stay 0 while probing and aligning. A `cancel` file stops the run as for `mine`.
- Each run verdict also carries `failure_is_transient`: true for a YouTube login wall, a locked browser cookie database and a timeout, which running again later can get past.
- Per run: `YOUTUBE_REFUSED`, `FETCH_FAILED`, `SETUP_ERROR` (the speech model a transcription needs, or yt-dlp or ffmpeg lost mid-call) and `CANCELLED`. A missing yt-dlp or ffmpeg refuses the whole call with `SETUP_ERROR`.

## Progress and cancelling

While a run works, `progress.json` holds `{"schema": 1, "run_id": …, "stage": 3, "stages": 5, "done": 12, "total": 26}`. The stages are parsing, filtering, media, definitions and cards.

To stop a run, create an empty file named `cancel` in its folder. The run stops at its next step, reports `CANCELLED`, and Anki Miner deletes the file. Only that run stops; a call with several runs moves on to the next.

A `cancel` file already there when a run starts cancels it at once, and its result lists every word as `not_attempted`. On Linux and macOS, SIGINT or SIGTERM stops the current run and every later run in the call. Notes already added to Anki stay.

## Error codes

| Code | Where | Meaning |
|---|---|---|
| `BUSY` | call | the window is mining, or another run is working; for `settings-import` and `setup`, any open window |
| `BAD_ARGUMENTS` | call | the command line does not parse, or names an unknown language or an `--out` or `--progress` folder that does not exist; for `settings-import`, a FILE it cannot apply, a name it refuses or the active profile; for `setup`, a language the profile has never used |
| `BAD_RUN_FILE` | call | the run file, media file or fetch file is not valid JSON or breaks the rules above |
| `PROFILE_UNREADABLE` | call | the profile does not exist or cannot be read |
| `SETUP_ERROR` | call, run | language pack, dictionary index, ffmpeg, deck, note type, fields or offline dictionary; for fetch, yt-dlp, ffmpeg or the speech model |
| `ANKI_UNREACHABLE` | call, run | AnkiConnect does not answer |
| `VIDEO_UNREADABLE` | run | the video does not open |
| `SUBTITLE_UNREADABLE` | run | the subtitle file cannot be read |
| `MINING_FAILED` | run | the run itself failed; `message` says why |
| `YOUTUBE_REFUSED` | run | fetch: a live stream, a video over the profile's length limit, an age-restricted video without cookies, or no captions with `captions` |
| `FETCH_FAILED` | run | fetch: probing, downloading or transcribing failed; see `failure_is_transient` |
| `CANCELLED` | call, run | stopped by a `cancel` file or a signal, or `setup` stopped by a signal |
| `INTERNAL` | call, run | an unexpected error; details are in the log |

`message` carries the English text. Codes never change; new ones may be added.

## check, version, profiles, settings-export, settings-import

`check --language CODE [--profile ID]` puts `{"ready": false, "items": [...]}` in `result`, one item per check. The items are `anki`, `deck`, `note_type`, `fields`, `dictionary`, `resources` (dictionary or frequency indexes that need re-importing), `language_pack`, `ffmpeg`, `ffprobe` and `yt_dlp`, then `speech_model` unless the profile's YouTube subtitles are Captions only. `yt_dlp` and `speech_model` are for `fetch` and do not count toward `ready`. Each item has `name`, `ok` and `message` (null when ok). When Anki does not answer, `deck`, `note_type` and `fields` are reported as not checked.

`version` puts `{"schema": 1, "app": "3.5.0", "commands": [...], "features": [...]}` in `result`. `features` names each addition this build has (table below).

| Feature | Adds |
|---|---|
| `sentence-rules-off` | the sentence rules are off for every run; `deduplicate_sentences` and `use_i_plus_one_filter` may still be sent as `false` |
| `bold-target` | `bold_target_in_sentence` in `config` |
| `named-words-whitelisted` | every named word counts as whitelisted |
| `script-fold` | `word` also matches through the language's comparison (Chinese Simplified and Traditional, letter case) |
| `filter-names` | `filter` is `duplicate-expression` on a word the merge of words sharing one dictionary entry removed |
| `word-from-line` | a named line is the one used, and a word the episode does not produce there is made from it; `surface`, `reading` and `from_line` |
| `dry-run` | `dry_run` in the run file, the result's `dry_run` and the `ready` status |
| `render` | the `render` command, `render-<n>.json` and the `rendered` status |
| `media` | the `media` command and `media-<n>.json` |
| `settings-import` | the `settings-import` command |
| `setup` | the `setup` command |
| `fetch` | the `fetch` command, `fetch-<n>.json`, and `check`'s `yt_dlp` and `speech_model` |
| `beside-window` | `mine` runs beside an open window that is not mining |

`profiles` puts `{"profiles": [{"id": "anime", "name": "Anime", "active": true}, …]}` in `result`. Before the user has created any profile, the list holds one, `default`.

`settings-export --language CODE --out FILE [--profile ID]` writes what the app's Export to file… writes (Manage profiles…, under This profile), with that language's deck, note type and fields.
- A language the profile has never used exports its defaults and `"configured": false`.
- As in the app's export, file paths and resource lists are left out.
- The file can be imported with Import from file… in the same place.

`settings-import FILE --language CODE (--name NAME | --profile ID)` applies FILE the way Import from file… does, and puts `{"profile": "surasura", "created": true, "invalid_fields": []}` in `result`.
- FILE is what `settings-export` writes, or a flat settings object. Keys left out keep their values; `anki_fields` and `card_type_marker_fields` merge key by key. File paths and resource lists in it are ignored, as in the app.
- `--name` adds a profile called NAME, starting as a copy of the active one. A blank name, a name already in use (ignoring case) or a 51st profile gives `BAD_ARGUMENTS`. For a name in use, `message` names the `--profile` to use, or says it is the active profile's. Before any profile exists, it first saves the current settings as `default`, as the window does; `default` stays active.
- `--profile` updates an existing profile. The active profile gives `BAD_ARGUMENTS`: settings-import never changes the settings the window uses.
- `--language` is the profile's mining language afterwards, switched the way the window's language switch does, and FILE applies to that language. A FILE whose `language` is another one gives `BAD_ARGUMENTS`.
- `invalid_fields` lists keys whose value was refused; they keep the profile's value. A subtitle filter that does not compile is listed as `subtitle_regex_filter`.
- A FILE that is not a settings object gives `BAD_ARGUMENTS`, and nothing is written.

`schema` goes up only for breaking changes. New keys can appear; ignore keys you do not know.

## setup

`setup --language CODE --progress FILE [--profile ID]` downloads what the setup wizard downloads for CODE: its language pack when it needs one, then its recommended dictionary, frequency and pitch resources. The deck and note type stay the caller's job.
- The resources are switched on for CODE in the active profile, or in `--profile`. The profile keeps its own mining language. CODE can be another language only when the profile has used it before; its resources then wait in that language's settings. Otherwise the call gives `BAD_ARGUMENTS`: make a profile for CODE with `settings-import FILE --language CODE --name NAME` (FILE may be `{}`) and pass it as `--profile`.
- A resource already installed is not downloaded again; it is switched on if the profile does not use it yet.
- While it works, FILE (its folder must exist) holds `{"schema": 1, "item": "jmdict-english", "stage": 2, "stages": 4, "done": 1048576, "total": 20971520}`. `item` is a resource `id` or `language_pack`; `done` and `total` are bytes while downloading. While a frequency or pitch list installs they count the files or terms it reads; for a dictionary, `done` counts the entries written and `total` stays 0.
- `result`: `{"language": "ja", "profile": "default", "language_pack": {"status": "not_needed", "message": null}, "resources": [{"id": "jmdict-english", "kind": "dict", "name": "JMdict", "status": "installed", "message": null}, …]}`. `status` is `installed`, `already_installed`, `not_needed` (language pack only), `failed` (`message` says why) or `not_attempted`.
- `ok` is false when an item failed; the top-level `error` stays null. SIGINT or SIGTERM stops the item in flight with `CANCELLED`. That item is `not_attempted`, and a download cut short resumes on the next call; what finished stays installed and switched on.

## Differences from the proposal

This build implements the v7 proposal's "First" list and the next requests (Z-1 to Z-13; Z-9 is held back). Where it took a request's smaller version or changed it, the bullets below say so.

- Of the "First" list it takes the full word matching (card front, then dictionary form) and the full word statuses, and the "Smaller version" of the setup codes: all six are one `SETUP_ERROR`. `profiles` is its own command.
- `filter` (v7 and Z-6) names only the merge: with every named word whitelisted and a named line made into a word (see Choosing the line), no other step after the parse can leave one `not_found`.
- Two codes were added: `BAD_ARGUMENTS` for a command line that does not parse, and `MINING_FAILED` for a run that failed inside the pipeline.
- Verdict runs and result files carry a `message` beside `error`, and result files carry a run-level `media_store_failures`.
- `check` has a `resources` item for indexes that need re-importing.
- `media_missing` covers the picture and the audio clip. Missing pronunciation audio is not reported, and media Anki failed to store is counted for the run, not per word.
- `file` is also `null` for a run its own checks refused (`SETUP_ERROR`, `ANKI_UNREACHABLE`), and for a run a signal cancelled before it started.
- `line_start` is compared with the line starts as written in the subtitle file, not after the offset. The two differ only where a negative offset moves lines before 0.
- `line_text` is also cleaned the way the subtitle lines are and ignores whitespace, and an empty `word` or `line_text` is refused.
- Where `line_expansion` is cut to 30 seconds, lines after the chosen one are added first.
- A word the merge removed is not made from its line (Z-2): it stays `not_found`, with `filter` naming the merge. A word made from its line without `reading` takes the reading the episode or the parser gives it, else the dictionary's when it has only one.
- `settings-import` (Z-3) has no `--whitelist` (named words are whitelisted already), and its `result` adds `invalid_fields`.
- A dry run (Z-7) is the "Smaller version": the per-word rows, without `offered`.
- `fetch` (Z-8) writes into `fetch-<n>/`, not the run folder itself, and no `secondary_subtitle_file` or `thumbnail_file`; its run verdicts add `failure_is_transient`.
- `render` (Z-10) does not copy images inside dictionary definitions.
- `media` (Z-10) files go to `media-<n>/`, not `media/`, which a `mine` removes when it ends.
- `setup` (Z-11) also takes `--profile`. CODE can differ from the profile's mining language only when the profile has used it, and `result` gives every item a `status`, not only what was installed.
- The fold that `word` matches through (Z-12) is the language's one-card-per-run comparison, so it covers letter case too.

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
    "episodes": [{"run_id": "ep05", "video_file": "ep05.mkv", "subtitle_file": "ep05.ja.srt",
                  "words": [{"word": "約束", "line_start": 812.3},
                            {"word": "今日", "line_start": 15.02, "line_expansion": [0, 1]},
                            {"word": "言う", "line_text": "今日こそは言うよ"}]}],
}), encoding="utf-8")
verdict = api("mine", "run.json")
run = (verdict["runs"] or [{}])[0]
if not run.get("file"):
    raise SystemExit(verdict)  # refused before mining: BUSY, SETUP_ERROR, VIDEO_UNREADABLE, …

result = json.loads((runs / "ep05" / run["file"]).read_text(encoding="utf-8"))
for word in result["words"]:
    print(word["word"], word["status"], word["note_id"])
```
