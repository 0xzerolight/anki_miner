# E2E GUI test harness

Real-service harness that drives the actual GUI (offscreen Qt) through the full mining pipeline
against a live Anki, or against an in-process fake. It exists to catch four classes of bug that
unit tests structurally cannot, because they mock at the service boundary:

1. **Accumulation and leaks** — mine the same episode several sessions in a row and watch widget
   counts, thread counts, RSS, sqlite rows, temp files, and deck card count for anything that
   grows without bound.
2. **GUI wiring** — widget state, mined word sets, cancel and error paths, and known-words
   accumulation asserted against the real widget stack and real services.
3. **Engine drift** — the note payloads a Japanese run writes must not change. `test_ja_drift_canary.py`
   pins the plain path and `test_ja_drift_canary_filters.py` the same guarantee through the optional
   filters; both are `e2e`-marked.
4. **Non-Japanese ingestion** — `test_ko_hangul_ingestion.py` drives Korean known-word ingestion
   through the real `AnkiService` collection scan, with the Japanese gate as a negative control. It is
   `network`-marked, for a real loopback socket rather than an external service.

## Prerequisites

- **Anki running** with AnkiConnect on `127.0.0.1:8765`, or `--fake-anki` for an in-process fake
  (loopback-only either way).
- **ffmpeg** on `PATH` — every run is a full process run.
- fugashi/MeCab available — the real Japanese tokenizer, needed by every run that mines Japanese.
  The Korean oracle exercises the collection scan and needs no tokenizer.

## Running

`scripts/run_e2e.py` is a thin shim that isolates the home and the Qt environment before importing
anything:

```bash
python scripts/run_e2e.py smoke [--fake-anki]
python scripts/run_e2e.py soak [--mode inprocess|crossprocess] [--sessions N] \
    [--fake-anki] [--bypass-known-words] [--policy all|first_n|none] [--first-n K] \
    [--full-window] [--inject-cancel SECONDS] [--fresh-home] [--timeout SECONDS]
python scripts/run_e2e.py cleanup
```

- **smoke** — one real mining session plus screenshots. Uses `--bypass-known-words` internally for
  a deterministic card count.
- **soak** — multi-session. `inprocess` (default) reuses ONE tab across sessions, which is what
  catches widget/worker/QThread/RSS leaks; `crossprocess` spawns a fresh subprocess per session, so
  the leak signal lives in the on-disk deltas instead.
- **cleanup** — delete the leftover test deck after inspecting a failure.

Flags worth knowing:

- **`--fake-anki`** — start a `FakeAnkiConnect` loopback server for the run. Implies `--fresh-home`,
  since an empty fake collection alongside stale on-disk state is incoherent. Works cross-process;
  children reach the fake over the forwarded `--ankiconnect-url`.
- **`--bypass-known-words`** — card everything, no known-words query, deterministic count.
  **Single-session only**, enforced by the runner: card creation is stateful, so session 2 of an
  identical bypass run would dup-skip everything. The default (faithful) mode instead does real
  known-words subtraction, dedup, and dup-guard — it *reads* the collection but writes only to the
  test deck.
- **`--full-window`** — drive a real `MainWindow` instead of the bare tab, so dialog wiring, tab
  switching, the menu bar, and the results slot are exercised. In-process only; the post-run dialogs
  and the setup wizard are patched to non-blocking no-ops.
- **`--inject-cancel SECONDS`** — append one extra session that cancels mid-run and asserts the tab
  is cleanly reusable, without corrupting the leak series.
- **`--fresh-home`** — wipe the test home first. The baseline is captured before the wipe.
- **`--timeout SECONDS`** — per-session wait budget.

## Isolation

This is the part to preserve when changing anything here:

- Test home is `~/.anki_miner_e2e`, overridable with `ANKI_MINER_E2E_HOME`. The real
  `~/.anki_miner` is never touched — a hard gate plus `guard_real_home` enforce it.
- Test deck is `"AnkiMiner E2E TEST"`, deliberately distinctive so the mutating and cleanup paths
  cannot plausibly hit a real study deck. The gateway refuses non-loopback Anki and refuses to
  adopt a pre-existing populated deck.

## Reading the output

Each run prints machine-readable lines to stdout:

```
RUN_DIR=<abs path to the run's artifact dir>
REPORT=<abs path to report.json>
VERDICT=<PASS|WARN|FAIL> (divergence=<...>)
```

Exit code is 0 on PASS/WARN, non-zero on FAIL, and 2 when Anki is required but unreachable (a clean
one-line `ERROR:`, no traceback). `report.json` carries the `SoakReport` — verdict, per-metric
divergence flags, and per-session counts and snapshots. Screenshots land in the run dir as
`NN_session-<i>.png`; `hang_session_<i>.txt` appears only when a wait blew past its budget. Run
directories are never pruned; delete them yourself.

A screenshot baseline diff runs when a baseline exists, and reports deviation as WARN, never FAIL.

## pytest markers

Not everything in this directory is `e2e`-marked, and `e2e` does not mean "needs Anki":

- The fake-Anki tests run in the **default suite**. They carry `network` (which suppresses the
  socket tripwire for the fake's real loopback HTTP and grants the timeout exemption), plus a
  per-test ffmpeg skipif where the run extracts media. Pure-logic tests here stay unmarked.
- The live real-Anki tests carry `e2e` (one also `soak`) and skip cleanly when Anki is down.
- Two `e2e`-marked files need no Anki at all: `test_motion_soak.py` gates on animation timing and
  `test_mpv_player_cycles.py` on libmpv availability. They are marked `e2e` because they are slow
  and environment-dependent, not because they touch a collection.

`addopts` excludes `e2e` by default. Run them with `pytest tests/e2e -m e2e`.
