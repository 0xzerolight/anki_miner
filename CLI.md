# Anki Miner command line

Other programs can mine with an installed Anki Miner by running it with a `mine` command. The run uses the user's own Anki Miner settings (mining language, deck, note type, filters, dictionaries) and prints one JSON object per line, so a calling tool can follow progress and read the result.

Requirements: Anki is running with the AnkiConnect add-on, and Anki Miner has been set up once (its window has saved settings).

## Where the executable is

| Install | Command |
|---|---|
| pip | `anki-miner` |
| Linux .deb | `/usr/bin/anki-miner` |
| Linux AppImage | `./AnkiMiner-<version>-Linux-x86_64.AppImage` |
| macOS | `/Applications/AnkiMiner.app/Contents/MacOS/AnkiMiner` |
| Windows | `%LOCALAPPDATA%\Programs\AnkiMiner\AnkiMiner.exe` |

On Windows, `AnkiMiner.exe` is a GUI-subsystem program. Its output reaches a caller that captures stdout (for example `subprocess.run(..., capture_output=True)`), but `cmd.exe` shows nothing.

## Commands

```
anki-miner mine batch VIDEO_DIR SUBTITLE_DIR [--deck NAME]
anki-miner mine pairs --pair VIDEO SUBTITLE [--pair VIDEO SUBTITLE ...] [--deck NAME]
anki-miner mine reading PATH [PATH ...] [--deck NAME]
anki-miner mine youtube URL [URL ...] [--deck NAME]
anki-miner version
```

- `batch` pairs the videos and subtitles in two folders by episode number, the same way Video → Batch does.
  `anki-miner mine batch ~/anime/show ~/subs/show`
- `pairs` mines the video/subtitle pairs you name. One pair is a single episode.
  `anki-miner mine pairs --pair ep01.mkv ep01.ja.srt --pair ep02.mkv ep02.ja.srt`
- `reading` mines subtitle files without video. It also accepts what Reading accepts in the app: `.txt`/`.epub` novels and mokuro manga.
  `anki-miner mine reading ep01.ja.srt ep02.ja.srt`
- `youtube` downloads and mines YouTube videos. Playlist links are refused; pass video links.
  `anki-miner mine youtube "https://www.youtube.com/watch?v=VIDEO_ID"`
- `--deck NAME` adds the cards to NAME instead of the deck in the user's settings. The deck must exist.
- `version` prints the app version.

## Output

stdout is JSON Lines: one ASCII-only JSON object per line, each with an `"event"` key. Once the command has started, its last line is exactly one `result` event, including for `--help` and internal errors. The exceptions are a process that is killed, or one interrupted before it installs its signal handlers.

Events:

| event | fields |
|---|---|
| `start` | `schema`, `app_version`, `command` (`batch`, `pairs`, `reading`, `youtube`), `items` |
| `item_start` | `item`, `kind` (`episode`, `reading`, `youtube`), `input` |
| `stage` | `item`, `index`, `total`, `name` |
| `progress` | `item`, `current`, `total`, `desc` |
| `download` | `item`, `label`, `fraction` (0–1, or `null`) — YouTube only |
| `message` | `item` (or `null`), `level` (`info`, `success`, `warning`, `error`), `text` |
| `item_done` | an item report |
| `result` | `schema`, `status`, `error`, `cards_created`, `items` (item reports); `version` adds `app_version` |

An item report:

```json
{"item": 0, "kind": "episode", "input": {"video": "…", "subtitle": "…"},
 "status": "success", "cards_created": 12, "new_words_found": 12,
 "total_words_found": 140, "note_ids": [1726…], "errors": [], "retryable": false}
```

Item `status` is `success`, `failed`, `cancelled` or `skipped` (not reached after a cancel). File paths in `input` are absolute and resolved (symlinks followed), so they can differ from the paths you passed; match items by `item`, their position in the run. `note_ids` are the Anki note IDs created. `retryable` is true only when the failure was transient and no note was written, so running the item again cannot duplicate cards.

| `result.status` | exit code | meaning |
|---|---|---|
| `success` | 0 | Every item succeeded. 0 cards is a success when nothing was new. |
| `partial` | 1 | Some items succeeded, some failed. |
| `failed` | 1 | Every item failed. |
| `error` | 1 | Internal error; details are in Anki Miner's log. |
| `usage_error` | 2 | Bad arguments, a missing file or folder, no pairs matched, or an unsupported URL. |
| `busy` | 3 | The Anki Miner window or another command-line run is open. |
| `setup_error` | 4 | Nothing was mined: Anki Miner was never set up, the mining language's pack is missing, AnkiConnect is unreachable, the deck or note type is wrong, there is no offline dictionary, a dictionary needs re-importing, or yt-dlp is missing. |
| `cancelled` | 130 | Stopped by SIGINT/SIGTERM. |

`schema` is 1. New fields may be added without changing it; a change that breaks existing readers increases it.

## Behaviour

- stderr carries free-form diagnostics and the output of child processes such as ffmpeg. Read it or send it to `DEVNULL`; an unread stderr pipe can fill up and stall the run.
- The word curator never opens. Every word that passes the user's filters is mined, even when "Review words before mining" is on.
- Only one Anki Miner process runs at a time. While the Anki Miner window or another command-line run is open, the command exits with `busy`. Run jobs one after another.
- Nothing is retried automatically. Check `retryable` and run the item again if it is true.
- YouTube needs yt-dlp, which the app installs the first time it is used (Video → YouTube).
- To cancel, send SIGINT or SIGTERM on Linux and macOS; the run stops at its next step and reports `cancelled`. On Windows, terminate the process. Notes already added to Anki stay.
- Runs are written to Anki Miner's log and statistics like runs started in the app.

## Example (Python)

```python
import json
import subprocess

cmd = ["anki-miner", "mine", "pairs",
       "--pair", "ep01.mkv", "ep01.ja.srt",
       "--pair", "ep02.mkv", "ep02.ja.srt",
       "--deck", "My Show"]
proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
result = None
for line in proc.stdout:
    event = json.loads(line)
    if event["event"] == "item_done":
        print(event["input"]["video"], event["status"], event["cards_created"])
    elif event["event"] == "result":
        result = event
code = proc.wait()
if result is None:  # killed before it could report
    print("no result, exit code", code)
elif code != 0:
    print("run ended with", result["status"], result["error"])
```
