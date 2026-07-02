"""Socket-level network tripwire for the test suite (wired up in ``conftest.py``).

WHY THIS EXISTS: unit tests must never reach real services. The definitive
incident (2026-07-01): with Anki open, SettingsTab save/reset tests ran a real
``StylingWorker`` against AnkiConnect on 127.0.0.1:8765 and stripped the managed
glossary CSS block from the user's real "Lapis" note type. At the time the
default was ``manage_card_styling=False`` so the sync ran mode="remove"; since
v2.7.8 the default is True so the analogous sync runs mode="apply" — either way
it connects to the real collection, and this tripwire blocks it. Silent on every
local suite run; invisible in CI (no Anki there → handled connection error).
``tests/_home_isolation.py`` guards the data dir; this module is its network
counterpart.

MECHANISM — record-and-assert, NOT raise-and-propagate. Every AnkiConnect call
in production runs on a worker QThread inside ``except Exception``
(``StylingWorker.run``, the validation workers), which swallows ANY exception
raised at connect time; even a ``BaseException`` escaping ``QThread.run()`` only
reaches the worker thread's excepthook and the test stays green (verified
empirically). So the ``NetworkTripwire`` raise below can only BLOCK the traffic
— the test-failure signal comes from ``RECORDED``, asserted on the test thread
by the ``_network_guard`` autouse fixture in conftest at test setup AND
teardown.

Known bounds (accepted, documented here so nobody "fixes" them blindly):

- A connect from an *unjoined* worker spawned by an earlier test attributes to
  whatever test window it lands in (``PYTEST_CURRENT_TEST`` is read at connect
  time), or never fires at all if the QThread dies first. The failure is still
  loud — the setup-assert of the next test reports it — just possibly against
  an innocent test name.
- ``_drain_qt_deletes``' post-yield ``processEvents()`` can deliver a queued
  slot that spawns a connect after the guard's teardown-assert already ran;
  that lands in the next test's setup-assert instead.
"""

from __future__ import annotations

import os
import socket

# (test id, "host:port") pairs for every blocked connect attempt, appended by
# the wrapper below and asserted+cleared by conftest's ``_network_guard``.
RECORDED: list[tuple[str, str]] = []

# While True (toggled by ``_network_guard`` around youtube/e2e/network-marked
# tests), the wrapper passes AF_INET/AF_INET6 connects through untouched.
SUPPRESSED = False

_ORIGINALS: dict[str, object] = {}

_GUARDED_FAMILIES = {socket.AF_INET, socket.AF_INET6}


class NetworkTripwire(Exception):
    """Raised in place of a real TCP connect during tests.

    Deliberately a plain ``Exception``: production code swallows it like any
    connection error (that is fine — the recorded entry is the failure signal),
    and a ``BaseException`` escaping mid urllib3 pool checkout risks corrupting
    the pool for zero extra signal.
    """


def _describe(address: object) -> str:
    if isinstance(address, tuple) and len(address) >= 2:
        return f"{address[0]}:{address[1]}"
    return repr(address)


def _record_and_raise(address: object) -> None:
    target = _describe(address)
    origin = os.environ.get("PYTEST_CURRENT_TEST", "<outside any test>")
    if os.environ.get("AMH_NET_DEBUG") == "1":
        # Opt-in forensics: the recorded origin names the WINDOW the connect
        # landed in, which for an unjoined worker is not the spawner. The
        # stack names the actual caller.
        import traceback

        origin += "\n" + "".join(traceback.format_stack())
    RECORDED.append((origin, target))
    raise NetworkTripwire(
        f"blocked real network connect to {target} during {origin!r}. "
        "Unit tests must not reach live services (AnkiConnect on :8765 once "
        "stripped the user's real note-type CSS). Stub the seam — "
        "AnkiProbeController._start_styling_write for SettingsTab/MainWindow "
        "styling syncs, the per-module post_action copy for direct service "
        "tests — or mark a genuinely networked test with @pytest.mark.network."
    )


def install() -> None:
    """Wrap ``socket.socket.connect``/``connect_ex`` once per process.

    Installed for the whole pytest session (per xdist worker) and only removed
    at session end: unpatching mid-session would open exactly the between-test
    window that leaked worker-thread writes historically.
    """
    if _ORIGINALS:
        return
    _ORIGINALS["connect"] = socket.socket.connect
    _ORIGINALS["connect_ex"] = socket.socket.connect_ex

    def guarded_connect(self: socket.socket, address):  # type: ignore[no-untyped-def]
        if self.family in _GUARDED_FAMILIES and not SUPPRESSED:
            _record_and_raise(address)
        return _ORIGINALS["connect"](self, address)  # type: ignore[operator]

    def guarded_connect_ex(self: socket.socket, address):  # type: ignore[no-untyped-def]
        if self.family in _GUARDED_FAMILIES and not SUPPRESSED:
            _record_and_raise(address)
        return _ORIGINALS["connect_ex"](self, address)  # type: ignore[operator]

    socket.socket.connect = guarded_connect  # type: ignore[method-assign]
    socket.socket.connect_ex = guarded_connect_ex  # type: ignore[method-assign]


def uninstall() -> None:
    """Restore the original socket methods (session teardown only)."""
    if not _ORIGINALS:
        return
    socket.socket.connect = _ORIGINALS.pop("connect")  # type: ignore[method-assign]
    socket.socket.connect_ex = _ORIGINALS.pop("connect_ex")  # type: ignore[method-assign]


def summarize_recorded(records: list[tuple[str, str]]) -> str | None:
    """Human-readable failure message for recorded connects, or None if clean.

    Pure and pytest-free so the self-test (test_network_tripwire.py) can prove
    the assert logic without an inner pytest session — the same pattern as
    ``guard_real_home`` in tests/_home_isolation.py.
    """
    if not records:
        return None
    lines = "\n".join(f"  - {target} (during {origin})" for origin, target in records)
    return (
        f"test attempted {len(records)} real network connect(s):\n{lines}\n"
        "Stub the AnkiConnect seam (see tests/_network_tripwire.py docstring) "
        "or mark the test with @pytest.mark.network if it genuinely needs the "
        "network."
    )
