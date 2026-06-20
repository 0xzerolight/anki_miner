# Contributing to Anki Miner

Thanks for helping out. Anki Miner is a solo-maintained Japanese mining tool, and contributions of any size are welcome — bug reports, fixes, dictionary integrations, GUI polish, doc improvements.

## Before you start

- Bugs and feature requests: open an [Issue](https://github.com/0xzerolight/anki_miner/issues) using the appropriate template.
- General questions and chat: use [Discussions](https://github.com/0xzerolight/anki_miner/discussions) or [Discord](https://discord.com/invite/aDtQyZzUVP).
- Security vulnerabilities: see [SECURITY.md](SECURITY.md). Do not open a public issue.


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
4. Run the test suite. See [TESTING.md](TESTING.md).
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
pytest
```

## Tests

See [TESTING.md](TESTING.md) for the full strategy. Quick reference:

- `pytest` — default suite (excludes the `youtube` marker).
- `pytest -m youtube` — network-dependent YouTube tests.
- Headless Qt: `QT_QPA_PLATFORM=offscreen` matches CI behavior.

New code should add tests where reasonable; refactors should not regress existing coverage by a meaningful amount.

## Changelog

Add an entry under `## [Unreleased]` in `CHANGELOG.md` using the [Keep a Changelog](https://keepachangelog.com/) sections (Added / Changed / Fixed / Removed). Match the existing prose style — entries explain *what* changed and *why it matters to a user*, not just the implementation detail.

## Architecture

The 5-stage mining pipeline and package layout are documented in [ARCHITECTURE.md](ARCHITECTURE.md). Worth a skim before any contribution larger than a one-file change.
