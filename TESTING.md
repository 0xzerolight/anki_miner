# Testing

Anki Miner has a substantial test suite — ~1,400 tests across roughly 130 unit test files plus the integration layer. This page documents how it is organized and how to run it.

## Layout

```
tests/
├── conftest.py            # shared fixtures
├── unit/                  # ~130 files, external services mocked
│   └── gui/               # a handful of widget tests; most live in unit/ root
└── integration/           # 6 files, real adapters where possible
```

Most widget tests (panels, tabs, dialogs) live directly under `tests/unit/` (e.g. `test_anki_settings_panel.py`, `test_deck_builder_tab.py`), not in `tests/unit/gui/`. Any test importing a PyQt6 widget needs `QT_QPA_PLATFORM=offscreen` in headless environments — see [Headless Qt](#headless-qt).

- **Unit tests** mock external services (AnkiConnect, ffmpeg, Jisho, yt-dlp). They should run fast and never touch the network.
- **Integration tests** exercise the assembled pipeline against real adapters. Slow, may require ffmpeg on PATH.

Fixtures live in `tests/conftest.py`. Add a fixture there when more than one test would otherwise duplicate setup.

## Running

```bash
# Full default suite (excludes the `youtube` marker)
pytest

# Single file
pytest tests/unit/test_word_filter.py

# Network-dependent YouTube tests
pytest -m youtube

# Skip slow integration tests
pytest tests/unit
```

Coverage is enabled by default via `pyproject.toml`'s `addopts`:

```
--cov=anki_miner --cov-report=term-missing --cov-report=html
```

HTML coverage lands in `htmlcov/`.

## Markers

| Marker | Use |
|---|---|
| `youtube` | Tests that require network access and yt-dlp's real extractor. Excluded from default CI to avoid yt-dlp upstream breakage. |

Register new markers in `[tool.pytest.ini_options].markers` in `pyproject.toml`.

## Headless Qt

Any test that imports a `PyQt6` widget needs the offscreen platform plugin in a headless environment; setting it everywhere is recommended for parity with CI:

```bash
QT_QPA_PLATFORM=offscreen pytest tests/unit
```

CI sets this automatically. Locally, the project exposes `tests/conftest.py` configuration that ensures Qt widgets do not pop a window during tests.

## Mocking conventions

External boundaries are mocked in unit tests:

- **AnkiConnect** — patched at `anki_miner.services.anki_service.requests.post` or the higher-level `AnkiService` interface.
- **ffmpeg** — `subprocess.run` patched to return canned probe / extraction output.
- **Jisho** — `requests.get` patched; payloads stored as JSON fixtures where possible.
- **yt-dlp** — patched at the subprocess boundary; the `YouTubeFetcherService` is the unit under test.

Prefer mocking at the smallest interface that still exercises your code. Patch at the service-method level only when the wrapped library is genuinely untestable in isolation.

## Python matrix

CI runs the suite on Python 3.11, 3.12, and 3.13. The lint and typecheck jobs run only on Python 3.12.

## Coverage

There is no enforced coverage floor today. New code should add tests where reasonable; refactors should not regress existing coverage by a meaningful amount.

## CI behavior

`.github/workflows/ci.yml` defines four jobs:

1. **lint** — `ruff check .` + `black --check .` (Python 3.12).
2. **typecheck** — `mypy anki_miner` (Python 3.12).
3. **test** — `pytest -m "not youtube"` with coverage on the full matrix.
4. **wheel-assets** — builds a wheel, runs `scripts/check_wheel_assets.py`.

All four must pass for a PR to be mergeable.
