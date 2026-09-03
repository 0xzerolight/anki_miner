# Contributing to Anki Miner

Thanks for helping out. Anki Miner is a solo-maintained mining tool for Japanese, Chinese and Korean, and contributions of any size are welcome — bug reports, fixes, dictionary integrations, GUI polish, doc improvements.

## Before you start

- Bugs and feature requests: open an [Issue](https://github.com/0xzerolight/anki_miner/issues) using the appropriate template.
- General questions and chat: use [Discussions](https://github.com/0xzerolight/anki_miner/discussions) or [Discord](https://discord.com/invite/aDtQyZzUVP).
- Security vulnerabilities: see [SECURITY.md](SECURITY.md). Do not open a public issue.
- Code of Conduct: see [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).


## Development setup

```bash
git clone https://github.com/0xzerolight/anki_miner.git
cd anki_miner

python -m venv .venv
source .venv/bin/activate          # Linux/macOS
# or: .venv\Scripts\activate       # Windows

pip install -e ".[dev,languages]"
pre-commit install
```

Anki Miner requires Python 3.11 or newer; CI runs the suite on 3.11, 3.12 and 3.13, with lint and type checks on 3.12.

The `languages` extra adds the Chinese and Korean engines. Plain `.[dev]` still runs green, because the zh/ko suites skip themselves through `pytest.importorskip` rather than failing - so a contributor without it gets a passing run that never exercised those languages.

External runtime dependencies:

- `ffmpeg` on PATH (`brew install ffmpeg`, `sudo apt install ffmpeg`, or the official Windows build).
- Anki running with the [AnkiConnect](https://ankiweb.net/shared/info/2055492159) add-on.
- Optional: a Yomitan-format dictionary installed via **Settings -> Dictionaries -> Add Dictionary**, or the legacy `JMdict_e` at `~/.anki_miner/JMdict_e` (auto-migrated on first launch).
- fugashi/MeCab may need system-level MeCab libraries on some platforms; the bundled `unidic-lite` provides the dictionary.
- Headless Linux (and CI) also needs the Qt runtime libs `libegl1 libpulse0 libxkbcommon0` for any test that imports a PyQt6 widget (`sudo apt-get install -y libegl1 libpulse0 libxkbcommon0`).

## Workflow

1. Fork the repo and create a branch from `main`. Branch names like `feat/...`, `fix/...`, or `docs/...` are appreciated but not required.
2. Keep PRs focused — one feature or fix per PR.
3. Style (`black` + `ruff`) is auto-fixed on your PR by [pre-commit.ci](https://pre-commit.ci) — a bot pushes a fix commit if needed, so you don't have to run anything to pass CI. Installing the local hook (`pre-commit install`) is recommended for faster feedback but no longer required.
4. Run the test suite. See [Tests](#tests).
5. Add an entry under `## [Unreleased]` in [CHANGELOG.md](CHANGELOG.md).
6. Open the PR against `main`. The PR template will populate automatically.

## Code style

- **black** with 120-character line length.
- **ruff** for linting; `ruff check . --fix` for autofixes.
- **mypy** must pass on the `anki_miner/` package.
- Conventional Commits are preferred (`feat:`, `fix:`, `docs:`, `chore:`, `refactor:`, `test:`, `ci:`). Not enforced — the maintainer may normalize commit messages on merge.

Quick checks before pushing:

```bash
black .
ruff check .
mypy anki_miner
pytest -m "not youtube and not asr and not e2e and not golden"
```

`scripts/health.sh` runs the full local gate in one command: the four above, then the ASR suite (`pytest -m "asr and not e2e"`), then vulture, shellcheck and the Linux launcher smoke. The first five are hard steps and gate "done"; the last three only warn, and skip when their binary is missing.

## Tests

Tests live under `tests/unit/` (external services mocked — most of the suite), `tests/integration/` (the assembled pipeline, still mocked at the process boundary), and `tests/e2e/` (an on-demand live harness; see `tests/e2e/README.md`). Shared fixtures go in `tests/conftest.py`.

```bash
# What CI runs, and what to run before pushing
pytest -m "not youtube and not asr and not e2e and not golden"

# Single file
pytest tests/unit/test_word_filter.py
```

Bare `pytest` is **not** that gate. `pyproject.toml` sets `addopts` to `-n auto --dist loadfile --max-worker-restart=0 -m "not e2e"`, and a `-m` on the command line *replaces* that marker expression instead of adding to it — so `pytest -m youtube` also drops the `not e2e` exclusion. Spell out the full expression every time. The run is parallel (pytest-xdist, one file per worker to keep the order-dependent Qt teardown stable); pass `-n0` to force it serial. A 120s per-test timeout names any worker that deadlocks.

Coverage is computed but never gated, so it is off by default. Opt in with `pytest --cov=anki_miner --cov-report=term-missing`.

### Markers

| Marker | Use |
|---|---|
| `youtube` | Needs the network and yt-dlp's real extractor. Kept out of CI so upstream breakage can't turn the tree red. |
| `asr` | Needs an ASR backend and a downloaded model. Runs in the dedicated `test-asr` CI job, which installs `.[dev,asr,asr-vulkan]`. |
| `e2e` | Drives the real GUI through the `tests/e2e/` harness. Excluded by default via `addopts`. Most of these need Anki running; a couple (motion timing, mpv playback cycles) do not. |
| `soak` | Multi-session soak runs through the same harness. |
| `real_ytdlp` | Exercises the real `_ytdlp_supports_js_runtimes` probe (no autouse stub). |
| `real_probe` | Exercises the real `AnkiService._probe_duplicates` (no autouse stub). |
| `network` | Genuinely needs the network; suppresses the socket tripwire in `tests/_network_tripwire.py`. |
| `golden` | Android-port engine parity contract. Clones a pinned revision and runs real exports, so it is on-demand only. |
| `motion` | Needs real animation timing; opts out of the autouse instant-motion fixture. |

Register new markers in `[tool.pytest.ini_options].markers` in `pyproject.toml`.

### Headless Qt

Any test importing a PyQt6 widget needs the offscreen platform plugin (`QT_QPA_PLATFORM=offscreen`). `tests/conftest.py` sets it, and so does CI. Widget tests take pytest-qt's `qtbot` fixture and call `qtbot.addWidget()` on every top-level widget they build, so teardown stays deterministic.

### Mocking

Patch at the smallest boundary that still exercises your code:

- **AnkiConnect** — `anki_miner.services._ankiconnect.requests.post`, the actual HTTP call site.
- **ffmpeg** — `subprocess.run`, returning canned probe/extraction output.
- **Jisho** — `requests.get`, with payloads stored as JSON fixtures where practical.
- **yt-dlp** — the subprocess boundary, leaving `YouTubeFetcherService` as the unit under test.

New code should add tests where reasonable; refactors should not regress existing coverage by a meaningful amount.

## Logging

The log is the only evidence a maintainer gets from a bug report, so it is a contract rather than a
convenience. Every rule below exists because a real report arrived that the log could not explain.

**Every failure record carries four things**: the operation, its subject (path, word, URL, deck, id),
`type(exc).__name__`, and `str(exc)`. A message that drops the exception text ("Audio fetch failed")
is a line that cost a round trip with the reporter. A typed `AnkiMinerException` is one WARNING line
and no traceback; anything else is logged with `exc_info=True`, because an unexpected type is exactly
the case where the stack is the diagnosis.

**Levels.**

| Level | When |
|---|---|
| `ERROR` | Unexpected failures — the `logger.exception` class. Something we did not model went wrong. |
| `WARNING` | A user-visible result changed, or the failure is user-facing. |
| `INFO` | Lifecycle receipts: start, end, inventory, effective configuration. |
| `DEBUG` | Per-item and cosmetic detail. Always written to disk (see below), so it is cheap to add. |

**What reaches the file.** The `anki_miner` logger is pinned to `DEBUG` and the rotating file handler
is at `DEBUG`, so the project's own DEBUG lines are always on disk — there is nothing for a user to
enable before reproducing a bug. The root logger sits at `WARNING` so third-party libraries
(yt-dlp, fugashi, PyQt) stay quiet; `ANKI_MINER_LOG_LEVEL` lowers the *root* level when you need
their chatter too. The ring is 16 MiB × 5 backups.

**Log through a choke point, not a fresh `logger.info`.** Each of these already stamps the fields the
contract asks for, and a new one drifts from them within a release:

| Choke point | Where | Covers |
|---|---|---|
| `CancellableWorker.log_start` / `log_end` / `report_failure` | `gui/workers/base_worker.py` | Worker lifecycle, thread naming, elapsed. `report_failure` is the *only* worker catch-all — extend it, never add a second. |
| `log_summary` | `utils/logging_ext.py` | Every `event: key=value` receipt. Paths render verbatim; values containing whitespace are quoted. |
| `timed_phase` | `utils/timing.py` | Pipeline phase boundaries with a duration. |
| `run_supervised` + `log_command` / `log_command_result` | `utils/process_supervisor.py`, `utils/subprocess_log.py` | Every external process: argv (secrets masked), cwd, timeout, exit state, stderr tail. |
| `log_resolution` / `log_resolution_refused` | `utils/resolver_log.py` | Which binary was chosen, from which tier, and why a tier was refused. |
| `TaskRegistry` | `gui/controllers/task_registry.py` | Task start, end, cancel, stall. Progress has one owner; so does its log. |
| `GUIPresenter` | `gui/presenters/gui_presenter.py` | Presenter messages are mirrored to the file log, so a screen message is never GUI-only. |
| `LogWidget` | `gui/widgets/log_widget.py` | The Activity Log; every line it shows is also written to disk. |
| `ScreenIssueBanner` | `gui/widgets/base/screen_issue_banner.py` | Recoverable failures shown inline — including the ones no screen was there to display. |
| `write_diagnostics_bundle` | `diagnostics/bundle.py` | The export a reporter attaches. Adding a member means bumping `BUNDLE_FORMAT`. |

**`suppressed()` is the only sanctioned broad swallow.** A `except Exception: pass`, or a
`contextlib.suppress(Exception)`, deletes the one record of a failure that already decided not to
surface itself:

```python
from anki_miner.utils.logging_ext import suppressed

with suppressed(logger, "SIGUSR1 stack dump registration"):
    faulthandler.register(signal.SIGUSR1, file=stream, all_threads=True, chain=False)
```

It writes `Ignored failure during <what>: <Type>: <message>` at DEBUG (raise `level=`, or pass
`exc_info=True`, when the swallow is load-bearing). `tests/unit/test_silent_except_ratchet.py`
enforces this against the per-file budget in `tests/unit/silent_except_budget.txt`: a new silent
broad handler fails the suite, and a file that drops to zero must have its budget entry removed, so
the ratchet only turns one way. Handlers narrow enough to be self-documenting (`except OSError: pass`
on a best-effort unlink) are not counted.

**Keep a broad `except` annotated** with why it is broad and what the caller loses, in the established
form — `# noqa: BLE001 — bucket A: boot continues without native-crash capture.`

**Bound every list you log.** `capped(items, 50)` renders at most the limit and appends `"+N more"`.
Per-row importer and parser failures are counted and reported once at the end, never one line per row —
a malformed 400k-entry dictionary must not become 400k log lines.

**Log strings are English and untranslated.** Never wrap one in `tr()`: the event names are grep
anchors, and a log written in the user's UI language is a log no maintainer can search.

**No new config for logging.** The locked rule for the whole project applies here with no exceptions:
no toggle, no verbosity preference, no "enable diagnostics" checkbox. A module constant or an
environment variable is the escape hatch when one is genuinely needed.

### Grep anchors

Event names are stable strings, so a report is answered by grepping rather than reading. The current
set, condensed — match them verbatim when adding to an area:

| Area | Anchors |
|---|---|
| Session | `Session start:` · `Session end:` · `Config effective:` · `Home directory unavailable` |
| Process | `Unhandled exception:` · `Unhandled exception in thread` · `Unraisable exception` |
| Watchdog | `GUI thread stall detected:` · `stall detection resumed:` · `stall watchdog stopped:` |
| GUI run | `Run start:` · `Run end:` · `Run refused:` · `Run control:` · `Run fatal:` |
| Tasks | `Task start:` · `Task end:` · `Task cancelling:` · `Task stalled:` · `Task cancel ignored:` |
| Queues | `<Worker> started:` · `<Worker> finished:` · `Queue refused:` · `Queue item:` · `Queue retry:` |
| Surfaces | `Screen issue:` · `Presenter warning:` · `Pipeline item error:` · `Curator decision:` |
| Off-thread | `<Parent>.<work> started:` · `Off-thread dispatch rejected` · `File picker:` |
| Shutdown | `Close requested:` · `Close join:` · `Deferring close:` · `Close finalized:` |
| Subprocess | `<op>: argv=` · `<op> spawn failed:` · `<op> failed: state= rc=` |
| yt-dlp | `yt-dlp cookie source:` · `yt-dlp capability` · `yt-dlp classify:` · `youtube fetch starting:` |
| Pipeline | `Pipeline start:` · `Pipeline end:` · `Phase N ...:` · `Frequency cutoff ignored:` |
| Lookup | `Definitions batch:` · `Definitions missed:` · `Audio fetch:` · `Audio packs mounted:` |
| Encoding | `Subtitle decode:` · `Subtitle parse failed:` · `Known words import done:` |
| Stores | `Index rejected:` · `Index meta invalid:` · `Yomitan import:` · `Audio pack import:` |
| Inventory | `Resource inventory:` · `Diagnostics exported:` · `Validation check:` · `AnkiConnect ready:` |
| Sweep | `Ignored failure during <what>:` |

### Privacy

Diagnosis comes first: file paths, mined words and readings, YouTube video ids, deck and note-type
names, and exception messages are logged **verbatim**. A basename cannot locate the pack folder that
stalled, and a log line that reads `https://www.youtube.com/watch` cannot say which video failed.

Redact secrets only, and only these: URL userinfo, the values of `--username` / `--password` /
`--ap-*` / `--video-password` in an argv (`mask_argv`), the query string of custom audio URLs
(`redact_url_for_log` — it may carry an API key), and cookie *contents*. A cookie file's path is a
path and is logged like any other.

The diagnostics bundle inherits this: it is inert, it preserves paths by design, and it carries a
privacy sentence telling the reporter to read it before uploading.

## Translations

If your change adds or edits a user-facing UI string, refresh the translation catalogs before committing:

```bash
pip install -e ".[i18n]"   # compile shells out to pyside6-lrelease, which ships only in this extra
python scripts/i18n.py extract
python scripts/i18n.py compile
```

### README translations

The README ships in every UI language under `i18n/README.<code>.md`, where
`<code>` matches the locale codes in `anki_miner/gui/i18n.py`.

- Editing `README.md` makes every translation's source stamp stale and turns
  `tests/unit/test_readme_translations.py` red. Update the affected passages in
  each `i18n/README.<code>.md`, then run `python scripts/readme_i18n.py stamp`.
- Adding a language: add the entry to `_LANGUAGES`, then
  `python scripts/readme_i18n.py scaffold <code>` and translate the result.
- Translations must keep the English structure exactly: same headings, same
  table rows, same `<details>` blocks, same URLs, and byte-identical code
  blocks. Only prose, headings, `<summary>` labels and table cell text change.
- GUI labels, menu paths and quoted error messages should match that language's
  shipped UI strings - look them up in
  `anki_miner/gui/resources/translations/anki_miner_<code>.ts`.
- `python scripts/readme_i18n.py check` runs the same gate as the test.

## Changelog

Add an entry under `## [Unreleased]` in `CHANGELOG.md` using the [Keep a Changelog](https://keepachangelog.com/) sections (Added / Changed / Fixed / Removed). Match the existing prose style — entries explain *what* changed and *why it matters to a user*, not just the implementation detail.

## Architecture

The 5-stage mining pipeline and package layout are documented in [ARCHITECTURE.md](ARCHITECTURE.md). Worth a skim before any contribution larger than a one-file change.
