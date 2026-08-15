# Contributing to Anki Miner

Thanks for helping out. Anki Miner is a solo-maintained Japanese mining tool, and contributions of any size are welcome — bug reports, fixes, dictionary integrations, GUI polish, doc improvements.

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

pip install -e ".[dev]"
pre-commit install
```

External runtime dependencies:

- `ffmpeg` on PATH (`brew install ffmpeg`, `sudo apt install ffmpeg`, or the official Windows build).
- Anki running with the [AnkiConnect](https://ankiweb.net/shared/info/2055492159) add-on.
- Optional: a Yomitan-format dictionary installed via **Settings → Add Dictionary…**, or the legacy `JMdict_e` at `~/.anki_miner/JMdict_e` (auto-migrated on first launch).
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

`scripts/health.sh` runs the full local gate in one command (the above plus vulture and shellcheck).

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
