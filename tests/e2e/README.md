# E2E GUI test harness

Real-service, end-to-end harness that drives the actual `SingleEpisodeTab` widget
(offscreen Qt) through the full mining pipeline against a live Anki.

Two complementary purposes:

1. **Multi-session accumulation / leak detection** — mines the same episode
   several sessions in a row and instruments cross-session state (widget counts,
   thread counts, RSS, sqlite rows, temp files, deck card count) to flag anything
   that grows unboundedly: leaked widgets, leaked threads, un-cleaned temp media,
   RSS creep.

2. **GUI-consistency and GUI/integration bug detection** — asserts widget state,
   mined word sets, cancel/error paths, and known-words accumulation against
   the *real* widget stack and *real* services. These bugs are invisible to unit
   tests, which mock at the service boundary and never exercise the full wiring.

## Prerequisites

- **Anki running** with the AnkiConnect add-on on `127.0.0.1:8765` — OR pass
  `--fake-anki` to run against an in-process fake AnkiConnect server
  (deterministic, no live Anki; loopback-only by design either way).
- **ffmpeg** on `PATH` (media extraction — every run is a full process run).
- fugashi/MeCab available (the real tokenizer).

## Running

The launcher is a thin shim that isolates the home + Qt env before importing:

```bash
python scripts/run_e2e.py smoke [--fake-anki]
python scripts/run_e2e.py soak [--mode inprocess|crossprocess] [--sessions N] \
    [--fake-anki] [--bypass-known-words] [--policy all|first_n|none] [--first-n K] \
    [--full-window] [--inject-cancel SECONDS] [--fresh-home] [--timeout SECONDS]
python scripts/run_e2e.py cleanup
```

- **smoke** — one real mining session + screenshots.
  Uses `--bypass-known-words` internally for a deterministic card count.
- **soak** — multi-session soak. `inprocess` (default) reuses ONE tab across
  sessions (catches widget/worker/QThread/RSS leaks); `crossprocess` spawns a
  fresh subprocess per session (clean process each time; leak signal lives in
  the on-disk deltas + each child's self-reported in-process snapshot).
- **cleanup** — delete the leftover test deck after inspecting a failure.
- **`--fake-anki`** — start a `FakeAnkiConnect` loopback server for the run and
  point the pipeline + gateway at it. Implies `--fresh-home` (an empty fake
  collection with stale on-disk state is incoherent). Works cross-process —
  children reach the fake over the forwarded `--ankiconnect-url`.

Additional soak flags:

- **`--inject-cancel SECONDS`** — append one extra cancel session after the
  normal sessions: start a run, click Cancel after the given delay, assert the
  tab is cleanly reusable afterward. Tests the cancel path without corrupting
  the leak series.
- **`--fresh-home`** — wipe the test home before running so the soak starts
  from a clean slate (the home baseline — known-words row count + temp-file
  count recorded in `SoakReport.config` — is captured before the wipe).
- **`--timeout SECONDS`** — per-session wait budget (default varies by mode).

### Faithful vs `--bypass-known-words`

- **Faithful** (default soak): real known-words subtraction, dedup, dup-guard —
  this is the bug-hunt mode AND the multi-session mode. Faithful mode
  **reads** the collection to build the known-words set (a `findNotes` query);
  against a real Anki it is **read-only** on the user's collection — all writes
  go only to the `"AnkiMiner E2E TEST"` deck.
- **`--bypass-known-words`**: card-everything / no known-words AnkiConnect call /
  deterministic card count, independent of the collection. Smoke always uses it.
  **Single-session only** (the runner enforces this): card creation is stateful,
  so session 2+ of an identical bypass run would dup-skip everything.

### `--full-window` (in-process only)

By default the soak drives the bare `SingleEpisodeTab`. `--full-window` instead
drives a real `MainWindow` (the episode tab mounted + the post-run
`ResultsDialog` / first-run setup wizard /
curation modal all patched to non-blocking no-ops), so dialog wiring, tab
switching, the menu bar, and the results-display slot are exercised too — the
GUI surface the bare-tab path skips. Startup is isolated by writing a disabling
`gui_config.json` into the test home (update check off, yt-dlp auto-update off,
first-run flags done) and no-op'ing the startup validation worker. In-process
mode only.

## GUI-consistency coverage

The following checks are implemented and run on every in-process soak session
(unless noted):

- **Widget-state assertions** — after `click_start` the Start button must be
  disabled and the Cancel button visible; after completion both must revert. Any
  violation is a FAIL flag.
- **Mined word-set == `EXPECTED_LEMMAS`** — the set of lemmas returned by the
  real pipeline is compared against the fixture's known-correct set; divergence
  is a FAIL flag.
- **Cancel path (`--inject-cancel`)** — a dedicated cancel session asserts the
  run ends promptly, the tab is reusable, and no worker thread is left dangling.
- **Error path** — pipeline errors surface via the `error` signal; the driver
  raises `E2EMiningError` so the soak report records the failure rather than
  hanging.
- **Known-words accumulation (faithful mode)** — after N sessions the test deck
  card count and `known_words.db` row count must grow monotonically by expected
  amounts; stalling or double-counting is flagged.
- **Full-window driver (`--full-window`)** — drives a real `MainWindow` so dialog
  wiring, tab switching, the menu bar, and the results-display slot are covered;
  see `--full-window` section above.
- **Screenshot baseline diff (visual regression)** — screenshots are taken after
  each session; if a baseline exists, pixel-level diff is computed and a
  deviation above threshold is reported as WARN (not FAIL).
- **Keyboard-shortcut + tab-order coverage** — `assert_shortcuts_exist` and
  `assert_tab_order_sane` are exercised by the harness's own unit tests in
  `test_driver.py`, not by the soak session loop. `--full-window` adds a real
  `MainWindow` to the soak: patched `ResultsDialog`, tab switching, `QAction`
  menu trigger, and the results-display slot — it does not run shortcut or
  tab-order assertions during the soak.

## Isolation

- Test home: `~/.anki_miner_e2e` (override with `ANKI_MINER_E2E_HOME`). The real
  `~/.anki_miner` is never touched (hard safety gate + `guard_real_home`).
- Test deck: `"AnkiMiner E2E TEST"` — a deliberately distinctive name so the
  mutating/cleanup paths can never plausibly hit a real study deck. The gateway
  refuses non-loopback Anki and refuses to adopt a pre-existing populated deck.

## Reading the output

Each run prints machine-readable lines to stdout:

```
RUN_DIR=<abs path to the run's artifact dir>
REPORT=<abs path to report.json>
VERDICT=<PASS|WARN|FAIL> (divergence=<...>)
```

Exit code is 0 on PASS/WARN, non-zero on FAIL (or 2 if Anki is required but
unreachable — a clean one-line `ERROR:` message, no traceback).

- `report.json` — the `SoakReport`: `verdict`, `divergence` (per-metric flags +
  verdict), per-session `cards_created` / `words_found` / `delta` / snapshots.
- Screenshots — ordered `NN_session-<i>.png` (and `*-failed.png` on error) in the
  run dir; `hang_session_<i>.txt` appears only if a wait blew past its budget.

Artifacts are **always retained** — the harness never prunes old run dirs. Each
run gets a timestamped subdirectory under `RUN_DIR`; delete manually when no
longer needed.

## pytest markers

The harness's fake-Anki tests under `tests/e2e/` run in the default suite: they
carry `network` (suppresses the socket tripwire for the fake's real loopback
HTTP; also grants the per-test timeout exemption) and, where the run extracts
media, a per-test ffmpeg skipif so ffmpeg-less environments (CI's test job)
skip them cleanly. The pure-logic unit tests stay unmarked. Only the live
(real-Anki) tests are marked `e2e` (one also `soak`) and excluded by default
(`addopts = -m "not e2e"`); they skip cleanly when Anki is down. Run them
explicitly with `pytest tests/e2e -m e2e`.
