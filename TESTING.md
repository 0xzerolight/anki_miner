# Testing

Anki Miner has a substantial test suite — ~4,800 tests across roughly 260 unit test files plus the integration layer. This page documents how it is organized and how to run it.

## Layout

```
tests/
├── conftest.py            # shared fixtures
├── unit/                  # ~260 files, external services mocked
│   └── gui/               # a handful of widget tests; most live in unit/ root
├── integration/           # 6 files, real adapters where possible
└── e2e/                   # on-demand live harness (see E2E harness below)
```

Most widget tests (panels, tabs, dialogs) live directly under `tests/unit/` (e.g. `test_anki_settings_panel.py`, `test_deck_builder_tab.py`), not in `tests/unit/gui/`. Any test importing a PyQt6 widget needs `QT_QPA_PLATFORM=offscreen` in headless environments — see [Headless Qt](#headless-qt).

- **Unit tests** mock external services (AnkiConnect, ffmpeg, Jisho, yt-dlp). They should run fast and never touch the network.
- **Integration tests** exercise the assembled pipeline against real adapters. Slow, may require ffmpeg on PATH.

Fixtures live in `tests/conftest.py`. Add a fixture there when more than one test would otherwise duplicate setup.

## Running

```bash
# Default suite — runs in parallel, excludes only the `e2e` marker
pytest

# Single file
pytest tests/unit/test_word_filter.py

# Network-dependent YouTube tests
pytest -m youtube

# ASR tests (need faster-whisper + a downloaded model)
pytest -m asr

# Skip slow integration tests
pytest tests/unit
```

`pyproject.toml`'s `addopts` is `-n auto --dist loadfile --max-worker-restart=0 -m "not e2e"`, so the default run is parallel (pytest-xdist, one file per worker to keep the order-dependent pytest-qt teardown stable), aborts on the first worker crash instead of hanging to the CI kill, and excludes only the `e2e` marker. A 120s per-test `pytest-timeout` (`thread` method) names any deadlocked worker. Pass `-n0` to force serial.

Coverage is **not** on by default — it is computed but never gated, so it is dropped from default runs. Opt in explicitly when you need it:

```bash
pytest --cov=anki_miner --cov-report=term-missing --cov-report=html
```

HTML coverage lands in `htmlcov/`.

## Markers

| Marker | Use |
|---|---|
| `youtube` | Requires network access and yt-dlp's real extractor. Excluded from default CI to avoid yt-dlp upstream breakage. |
| `asr` | Requires `faster-whisper` and a downloaded ASR model. Run in the dedicated `test-asr` CI job. |
| `e2e` | Real-service end-to-end GUI tests (need running Anki); excluded by default via `addopts`. |
| `soak` | Multi-session soak tests run through the e2e harness. |
| `real_ytdlp` | Exercises the real `_ytdlp_supports_js_runtimes` probe (no autouse stub). |

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

`.github/workflows/ci.yml` defines five jobs:

1. **lint** — `ruff check .` + `black --check .` (Python 3.12).
2. **typecheck** — `mypy anki_miner` (Python 3.12).
3. **test** — `pytest -m "not youtube and not e2e and not asr"` on the full matrix (Python 3.11–3.13), no coverage.
4. **test-asr** — `pytest -m "asr and not e2e"` with the `[asr]` extra installed (Python 3.12).
5. **wheel-assets** — builds a wheel, runs `scripts/check_wheel_assets.py`.

All jobs must pass for a PR to be mergeable.

## E2E harness

`tests/e2e/` is an on-demand harness that drives the real GUI against real Anki + ffmpeg (offscreen Qt). It is explicit-activation-only — never part of the default suite. Live tests carry `@pytest.mark.e2e` (excluded via `addopts`); invoke the harness with `python scripts/run_e2e.py smoke|soak|cleanup`. It runs against an isolated `~/.anki_miner_e2e` home and only ever mutates the `"AnkiMiner E2E TEST"` deck.
