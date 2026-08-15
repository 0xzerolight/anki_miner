# UI atlas

Renders and mechanically checks every screen of the composed application at a
named layout cell, plus a button/menu inventory, a resize sweep, and a
responsiveness receipt.

It drives the real `anki_miner.gui.app.main()` rather than `tests/e2e/app_driver`,
because `AppDriver` mounts a single tab and so cannot see the composed window, the
pinned action bars, the status strip, or the settings navigator — the exact things
the layout cells exist to check.

## Cells

| Cell | Size | Locale | Text scale |
|---|---|---|---|
| `reference` | 1280×800 | English | 1.0 |
| `hostile` | 1024×768 (the app's own window minimum) | German | 1.5 |
| `hostile_pseudo` | 1024×768 | German + 25 characters on every string | 1.5 |
| `firstrun` | 1280×800 | English | 1.0, `first_run_setup_done` cleared |

`hostile_pseudo` is a deliberate *upper bound*, not a prediction: +25 characters on
every string is far harsher than any real locale, so treat its findings as "this is
what breaks first", never as "German breaks here". It also insures against catalog
drift — the catalogs are fully translated today, but a German-only run renders any
newer, not-yet-translated string in English and would under-report width pressure.

## Running

```bash
.venv/bin/python scripts/ui_atlas/atlas.py --cell reference --out /tmp/atlas
.venv/bin/python scripts/ui_atlas/atlas.py --cell hostile   --out /tmp/atlas
.venv/bin/python scripts/ui_atlas/sweeps.py --out /tmp/atlas
.venv/bin/python scripts/ui_atlas/timeline.py --out /tmp/atlas
```

Each cell writes to `<out>/<cell>/`:

| File | Contents |
|---|---|
| `<cell>__<screen>.png` | `window.grab()` of every screen |
| `dumps.json` | Every visible widget: class, object name, absolute rect, size hints, text |
| `findings_raw.json` | One record per checker hit |
| `findings.json` | The same hits deduped by identity, with the screens each appears on |
| `modals.jsonl` | Every blocking modal the run suppressed, with its title and text |

`timeline.py` additionally writes `summary.json` with first paint, the longest
event-loop gap, per-theme apply times, tab-switch times, and idle CPU/RSS.

## Interpretation

* **`findings.json` is triage, not a defect list.** A hit in shared chrome is one
  defect seen on 24 screens; that is what `screen_count` and `scope` are for.
* **`10_primary_action_hidden` has two severities.** `unreachable` means the
  primary action is off the window or inside the pinned bar (which never
  scrolls) — that is a D6 regression. `below_fold` means it is inside ordinary
  page scroll and a scroll reaches it; Settings pages and Deck Builder
  legitimately scroll.
* **`11_tabbar_overflow` is the D10 oracle.** Settings is a grouped list
  navigator, not a tab strip, so it cannot appear here at all. Any hit is a
  different strip.
* **The timeline's freeze signal is `snapshot_age_ms`, not `loop_gap_ms`.** Qt
  releases the GIL inside `setStyleSheet`/`setPalette`, so the sampler thread
  keeps ticking at 50 ms through a multi-second GUI-thread block. What stops is
  the GUI-thread publisher, so snapshot age is what climbs.
* **The numbers are not a packaged build's numbers.** Everything here runs from
  a source checkout under the offscreen platform. Before/after on the same
  machine is the usable comparison; absolute figures are not.

## Safety

Nothing here may touch real user data, and every step of `isolation.py` is there
because something verified would otherwise reach it:

1. `bootstrap()` sets `ANKI_MINER_HOME` before any `anki_miner` import, because
   module-level constants capture the home once.
2. Config `Path` fields are redirected by introspection, not a hand-written list.
3. `ankiconnect_url` is pinned to a fake server, and asserted not to be port 8765.
4. `run_startup_store_recovery` is neutralised at `app.py`'s *own* binding.
5. Every blocking modal is patched to its safe branch and logged —
   `QApplication.quit()` cannot escape a nested modal loop.
6. The run refuses to start while a live Anki Miner holds the real instance lock.

`tests/unit/test_ui_atlas_harness.py` pins these contracts. If one of those patch
targets is renamed, that test fails rather than the harness quietly doing nothing.

## Real-motion and theme gates

The atlas is layout only. Motion and theme have their own gates:

```bash
# deterministic lifetime + all-theme smoke + rendered endpoints (default gate)
.venv/bin/python -m pytest tests/unit/gui/test_motion_lifetime.py tests/unit/gui/test_theme_gallery.py

# serial diagnostic soak with real animation timing (excluded from the default gate)
.venv/bin/python -m pytest -n0 -m e2e tests/e2e/test_motion_soak.py -s
```
