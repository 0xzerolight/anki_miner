# gif-tools

Optional, unofficial tooling for regenerating the demo card GIFs in the README.
Not part of the shipped package — nothing here lands in the wheel, PyInstaller
bundle, or PyPI package; it just lives in the repo for anyone who wants to
reproduce or tweak the GIFs. Run it from a cloned repo (it imports `anki_miner`).
The output GIFs/MP4s it writes into `gifs/` are the committed artifacts.

| Script | Purpose |
|--------|---------|
| `make_card_gifs.py` | Generate the frame-synced demo card GIFs from live Anki cards. |
| `_demo_encode.py` | Shared ffmpeg/ffprobe encode helpers (imported by the above). |

## Setup

Needs **ffmpeg + ffprobe** on PATH (already a project requirement) and Playwright
Chromium. Playwright is intentionally NOT in the project's `[dev]` extras (this is
optional tooling), so install it on its own:

```bash
pip install playwright pillow
playwright install chromium      # one-time, ~120 MB
```

`pillow` is used to measure each card's rendered content box for the tight
auto-crop (see below).

## `make_card_gifs.py` — synced card GIFs

Renders the three demo cards from your **running Anki** (via AnkiConnect) and
captures an identical timeline for each — hold front → instant cut → hold back —
so all three GIFs share the same fps and frame count and the flip lands on the
same frame. They stay in phase when looped side-by-side in the README.

**Prerequisite:** re-mine the three demo cards with **animated screenshots** and
**pitch accent** enabled, so the live cards show current features. They live in a
deck named `gifs` (one card each, in README column order). The script pulls
`deck:gifs` by default; pass explicit card IDs to override (in Anki: Browse →
right-click card → Info shows the card ID, or use `findCards`).

```bash
# Anki must be running with AnkiConnect on :8765
python3 gifs/gif-tools/make_card_gifs.py --names cowboy_bebop frieren 'steins;gate'

# explicit IDs instead of the deck query:
python3 gifs/gif-tools/make_card_gifs.py 1502298036657 1502298036658 1502298036659 \
    --names cowboy_bebop frieren 'steins;gate'
```

Writes `gifs/<name>.gif` + `gifs/<name>.mp4` (the `--out-dir` default is the repo's
`gifs/`, regardless of where you run it from). At the end it `ffprobe`-checks that
all GIFs share an identical duration/frame count and exits non-zero on mismatch.

Key flags: `--front-secs 1.0 --back-secs 4.0 --fps 20 --width 994 --height 628
--scale 2 --bg '#1e1e2e'`. Animated AVIF Picture fields are decoded + animated
natively by headless Chromium (the same engine Anki uses); do not transcode AVIF
via ffmpeg, whose decoder reads only the primary still.

**Tight auto-crop:** the card centers its visible layers inside a larger frame,
leaving wide neutral bands (the back side wasted ~36% of the width). Each card's
content box is measured by pixel-scanning the back frames against `--bg`, then one
**uniform** crop (sized to the largest card, plus `--crop-pad` margin) is applied to
all of them — equal dimensions, minimal grey, no flip jump. `ffmpeg cropdetect` is
not used: dark screenshot edges blend into the stage and defeat it. Tune detection
with `--bg-tol` (raise if the screenshot is clipped, lower if grey leaks in) or skip
it entirely with `--no-autocrop`. Flags: `--crop-pad 8 --bg-tol 18`.

> GitHub-rendered READMEs autoplay animated **GIF** but not a relative `<video>`,
> so the GIF stays the embedded asset; the MP4 is committed only as a
> higher-quality download.

The MP4's animated screenshot is phase-aligned to the audio: the AVIF is restarted
to frame 0 at capture, so the first loop plays in sync with the sentence, then it
keeps looping silently to the fixed clip end. Requires the demo cards to be mined
with **match-audio** animated screenshots (so the AVIF length == the sentence).
