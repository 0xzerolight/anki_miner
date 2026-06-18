# E2E GUI test harness

Real-service, end-to-end harness that drives the actual `SingleEpisodeTab` widget
(offscreen Qt) through the full mining pipeline against a live Anki, then
instruments cross-session state to surface bugs that only appear after several
mining sessions in a row.

## Prerequisites

- **Anki running** with the AnkiConnect add-on on `127.0.0.1:8765` (needed for
  `smoke` and any non-`--preview` soak; loopback-only by design).
- **ffmpeg** on `PATH` (media extraction during real mining).
- fugashi/MeCab available (the real tokenizer).

`--preview` soaks (parse + filter only) need neither Anki nor ffmpeg.

## Running

The launcher is a thin shim that isolates the home + Qt env before importing:

```bash
python scripts/run_e2e.py smoke
python scripts/run_e2e.py soak [--mode inprocess|crossprocess] [--sessions N] [--preview] [--bypass-known-words] [--policy all|first_n|none] [--first-n K] [--full-window]
python scripts/run_e2e.py cleanup
```

- **smoke** — one real mining session (process mode, needs Anki) + screenshots.
  Uses `--bypass-known-words` internally for a deterministic card count.
- **soak** — multi-session soak. `inprocess` (default) reuses ONE tab across
  sessions (catches widget/worker/QThread/RSS leaks); `crossprocess` spawns a
  fresh subprocess per session (clean process each time; leak signal lives in
  the on-disk deltas + each child's self-reported in-process snapshot).
- **cleanup** — delete the leftover test deck after inspecting a failure.

### Faithful vs `--bypass-known-words`

- **Faithful** (default soak): real known-words subtraction, dedup, dup-guard —
  this is the bug-hunt mode (needs Anki for the known-words query).
- **`--bypass-known-words`**: card-everything / no known-words AnkiConnect call /
  deterministic card count, independent of the user's collection. Smoke always
  uses it.

`--preview` defaults off (real card creation); pass it for parse+filter only.

### `--full-window` (in-process only)

By default the soak drives the bare `SingleEpisodeTab`. `--full-window` instead
drives a real `MainWindow` (the episode tab mounted + the post-run
`ResultsDialog` / preview `WordPreviewDialog` / first-run `WelcomeDialog` /
curation modal all patched to non-blocking no-ops), so dialog wiring, tab
switching, the menu bar, and the results-display slot are exercised too — the
GUI surface the bare-tab path skips. Startup is isolated by writing a disabling
`gui_config.json` into the test home (update check off, first-run flags done) and
no-op'ing the startup validation worker. In-process mode only.

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

## pytest markers

The harness's mocked/preview tests under `tests/e2e/` are unmarked and run in the
default suite. Only the two live (real-Anki) tests are marked `e2e` (one also
`soak`) and excluded by default (`addopts = -m "not e2e"`); they skip cleanly when
Anki is down. Run them explicitly with `pytest tests/e2e -m e2e`.
