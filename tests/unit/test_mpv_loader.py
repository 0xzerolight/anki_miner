"""Tests for anki_miner.utils.mpv_loader.

None of these tests may import mpv at module level: python-mpv dlopens libmpv
at import time and CI runners have no libmpv. Every path that would import mpv
is exercised through fakes injected into sys.modules or through
ctypes.util.find_library patched to return None.
"""

from __future__ import annotations

import ctypes.util
import locale
import logging
import os
import re
import subprocess
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from anki_miner.utils import mpv_loader

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = PROJECT_ROOT / "anki_miner"


@pytest.fixture(autouse=True)
def _clean_loader_state(monkeypatch):
    """Isolate the loader cache and the mpv module between tests."""
    mpv_loader._clear_cache()
    monkeypatch.delenv("ANKI_MINER_LIBMPV", raising=False)
    saved = sys.modules.pop("mpv", None)
    yield
    mpv_loader._clear_cache()
    if saved is not None:
        sys.modules["mpv"] = saved
    else:
        sys.modules.pop("mpv", None)


def _fake_mpv_module(events: list | None = None) -> types.ModuleType:
    module = types.ModuleType("mpv")
    module.MPV_VERSION = (2, 5)

    class FakeMPV:
        def __init__(self, **kwargs):
            if events is not None:
                events.append(("construct", locale.setlocale(locale.LC_NUMERIC)))
            self.kwargs = kwargs
            self.mpv_version = "mpv v0.40.0-fake"

        def terminate(self):
            if events is not None:
                events.append(("terminate", None))

    module.MPV = FakeMPV
    return module


class TestSearchOrder:
    def test_env_override_fails_closed_when_missing(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ANKI_MINER_LIBMPV", str(tmp_path / "nope.so"))
        with pytest.raises(mpv_loader.MpvUnavailableError, match="fails closed"):
            mpv_loader.load_mpv()

    def test_env_override_wins_over_system(self, monkeypatch, tmp_path):
        lib = tmp_path / "libmpv.so.2"
        lib.write_bytes(b"")
        monkeypatch.setenv("ANKI_MINER_LIBMPV", str(lib))
        seen = {}

        def fake_import(path):
            seen["path"] = path
            return _fake_mpv_module()

        monkeypatch.setattr(mpv_loader, "_import_mpv_with_path", fake_import)
        mpv_loader.load_mpv()
        assert seen["path"] == lib

    def test_bundled_used_when_frozen(self, monkeypatch, tmp_path):
        (tmp_path / "libmpv.so.2").write_bytes(b"")
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
        seen = {}

        def fake_import(path):
            seen["path"] = path
            return _fake_mpv_module()

        monkeypatch.setattr(mpv_loader, "_import_mpv_with_path", fake_import)
        mpv_loader.load_mpv()
        assert seen["path"] == tmp_path / "libmpv.so.2"

    def test_bundled_load_failure_falls_back_to_system(self, monkeypatch, tmp_path):
        """An unloadable bundled copy (e.g. macOS min-OS too new) must fall
        through to the system libmpv, not dead-end — `brew install mpv`
        restores the preview."""
        (tmp_path / "libmpv.so.2").write_bytes(b"")
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
        monkeypatch.setattr(mpv_loader, "_import_mpv_with_path", MagicMock(side_effect=OSError("incompatible")))
        fake = _fake_mpv_module()
        monkeypatch.setitem(sys.modules, "mpv", fake)
        assert mpv_loader.load_mpv() is fake

    def test_system_fallback_plain_import(self, monkeypatch):
        fake = _fake_mpv_module()
        monkeypatch.setitem(sys.modules, "mpv", fake)
        assert mpv_loader.load_mpv() is fake

    def test_system_absent_raises_unavailable(self, monkeypatch):
        monkeypatch.setattr(ctypes.util, "find_library", lambda name: None)
        with pytest.raises(mpv_loader.MpvUnavailableError):
            mpv_loader.load_mpv()

    def test_bundled_libmpv_path_none_when_not_frozen(self):
        assert mpv_loader.bundled_libmpv_path() is None


class TestAvailability:
    def test_available_true_with_fake_module(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "mpv", _fake_mpv_module())
        assert mpv_loader.mpv_available() is True

    def test_available_false_never_raises(self, monkeypatch):
        monkeypatch.setattr(ctypes.util, "find_library", lambda name: None)
        assert mpv_loader.mpv_available() is False

    def test_failure_is_cached(self, monkeypatch):
        calls = []

        def counting(name):
            calls.append(name)
            return None

        monkeypatch.setattr(ctypes.util, "find_library", counting)
        assert mpv_loader.mpv_available() is False
        first = len(calls)
        assert mpv_loader.mpv_available() is False
        assert len(calls) == first  # second call served from cache


class TestLcNumeric:
    def test_factory_forces_c_numeric_before_construction(self, monkeypatch):
        events: list = []
        monkeypatch.setitem(sys.modules, "mpv", _fake_mpv_module(events))
        original = locale.setlocale

        def recording(category, value=None):
            if value is not None and category == locale.LC_NUMERIC:
                events.append(("setlocale", value))
            return original(category, value)

        monkeypatch.setattr(locale, "setlocale", recording)
        mpv_loader.create_mpv_player()
        set_idx = events.index(("setlocale", "C"))
        construct_idx = next(i for i, e in enumerate(events) if e[0] == "construct")
        assert set_idx < construct_idx

    def test_probe_forces_c_numeric_before_construction(self, monkeypatch, capsys):
        events: list = []
        monkeypatch.setitem(sys.modules, "mpv", _fake_mpv_module(events))
        original = locale.setlocale

        def recording(category, value=None):
            if value is not None and category == locale.LC_NUMERIC:
                events.append(("setlocale", value))
            return original(category, value)

        monkeypatch.setattr(locale, "setlocale", recording)
        assert mpv_loader.mpv_probe_main() == 0
        set_idx = events.index(("setlocale", "C"))
        construct_idx = next(i for i, e in enumerate(events) if e[0] == "construct")
        assert set_idx < construct_idx


class TestProbe:
    def test_probe_ok_marker_and_exit_zero(self, monkeypatch, capsys):
        events: list = []
        monkeypatch.setitem(sys.modules, "mpv", _fake_mpv_module(events))
        assert mpv_loader.mpv_probe_main() == 0
        out = capsys.readouterr().out
        assert "MPV_PROBE_OK" in out
        assert "mpv v0.40.0-fake" in out
        assert ("terminate", None) in events

    def test_probe_fail_marker_and_exit_one(self, monkeypatch, capsys):
        monkeypatch.setattr(ctypes.util, "find_library", lambda name: None)
        assert mpv_loader.mpv_probe_main() == 1
        assert "MPV_PROBE_FAIL" in capsys.readouterr().out


class TestFactoryOptions:
    def test_factory_options(self, monkeypatch):
        fake = _fake_mpv_module()
        monkeypatch.setitem(sys.modules, "mpv", fake)
        player = mpv_loader.create_mpv_player()
        assert player.kwargs["vo"] == "libmpv"
        assert player.kwargs["keep_open"] == "yes"
        assert player.kwargs["hwdec"] == "no"
        assert player.kwargs["pause"] is True
        assert player.kwargs["sid"] == "no"
        assert player.kwargs["input_default_bindings"] is False
        assert player.kwargs["input_vo_keyboard"] is False

    def test_factory_forwards_log_handler(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "mpv", _fake_mpv_module())
        handler = object()
        player = mpv_loader.create_mpv_player(log_handler=handler)
        assert player.kwargs["log_handler"] is handler


def _mpv_touching_modules() -> list[str]:
    """Dynamically enumerate anki_miner modules that reference python-mpv.

    Directory-driven so modules added later (mpv_video_widget, the migrated
    subtitle_player_widget) are covered automatically without editing this
    test.
    """
    pattern = re.compile(r"\bimport mpv\b|\bfrom mpv\b|\bmpv_loader\b")
    modules = []
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        if pattern.search(path.read_text(encoding="utf-8")):
            rel = path.relative_to(PROJECT_ROOT).with_suffix("")
            modules.append(".".join(rel.parts))
    return modules


class TestImportSafety:
    def test_enumeration_finds_the_loader(self):
        assert "anki_miner.utils.mpv_loader" in _mpv_touching_modules()

    def test_all_mpv_touching_modules_import_without_libmpv(self):
        """Positive import-safety gate: with libmpv unresolvable, every module
        that references mpv must still import cleanly (pip installs without a
        system libmpv must not crash at startup)."""
        modules = _mpv_touching_modules()
        code = (
            "import ctypes.util, importlib, sys\n"
            "ctypes.util.find_library = lambda name: None\n"
            f"for name in {modules!r}:\n"
            "    importlib.import_module(name)\n"
            "print('IMPORT_SAFETY_OK')\n"
        )
        env = dict(os.environ)
        # PYTHONPATH must point at THIS tree: an editable install would import
        # the main checkout's anki_miner, silently testing the wrong code.
        env["PYTHONPATH"] = str(PROJECT_ROOT)
        env["QT_QPA_PLATFORM"] = "offscreen"
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
            env=env,
        )
        assert result.returncode == 0, result.stderr
        assert "IMPORT_SAFETY_OK" in result.stdout

    def test_no_module_level_mpv_import_outside_loader(self):
        """Secondary lint: no top-level `import mpv` outside mpv_loader.
        TYPE_CHECKING-guarded imports are indented, so they pass naturally."""
        offenders = []
        top_level = re.compile(r"^(import mpv\b|from mpv\b)", re.MULTILINE)
        for path in sorted(PACKAGE_ROOT.rglob("*.py")):
            if path.name == "mpv_loader.py":
                continue
            if top_level.search(path.read_text(encoding="utf-8")):
                offenders.append(str(path.relative_to(PROJECT_ROOT)))
        assert offenders == []


class _WinLoadError(OSError):
    """Stand-in for a Windows ctypes.CDLL OSError: real ``.winerror`` can't be
    produced on the Linux CI gate, so synthesise the attribute directly."""

    def __init__(self, winerror: int, strerror: str):
        super().__init__(strerror)
        self.winerror = winerror
        self.strerror = strerror


def _wrapped_winerror(winerror: int, strerror: str) -> OSError:
    """python-mpv wraps the ctypes OSError via ``raise OSError(...) from e``; the
    original (carrying ``.winerror``) survives on ``__cause__``. Reproduce that
    chain so tests exercise the real diagnostic path."""
    wrapper = OSError("ctypes.find_library found mpv.dll ..., but ctypes.CDLL could not load it.")
    wrapper.__cause__ = _WinLoadError(winerror, strerror)
    return wrapper


class TestDiagnostics:
    """The bundled-DLL-load-failure diagnostic (confirmed field bug: a present
    libmpv-2.dll whose CDLL load fails with WinError 126 for a missing
    vulkan-1.dll). python-mpv hides the WinError in its wrapper string; the
    loader must surface ``exc.__cause__.winerror`` in the log."""

    def test_cause_detail_surfaces_winerror(self):
        detail = mpv_loader._cause_detail(_wrapped_winerror(126, "The specified module could not be found"))
        assert "winerror=126" in detail
        assert "module could not be found" in detail

    def test_cause_detail_empty_without_cause(self):
        assert mpv_loader._cause_detail(OSError("no chained cause")) == ""

    def test_bundled_failure_logs_winerror(self, monkeypatch, tmp_path, caplog):
        (tmp_path / "libmpv.so.2").write_bytes(b"")
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
        monkeypatch.setattr(
            mpv_loader,
            "_import_mpv_with_path",
            MagicMock(side_effect=_wrapped_winerror(126, "The specified module could not be found")),
        )
        # No system libmpv rescues it → final unavailability.
        monkeypatch.setattr(ctypes.util, "find_library", lambda name: None)
        with (
            caplog.at_level(logging.WARNING, logger="anki_miner.utils.mpv_loader"),
            pytest.raises(mpv_loader.MpvUnavailableError),
        ):
            mpv_loader.load_mpv()
        # The :191 bundled-failure warning now carries the real WinError…
        assert "winerror=126" in caplog.text
        # …and the previously-silent final raise leaves a trace naming the bundle.
        assert "bundled_found=True" in caplog.text

    def test_not_found_path_logs_once_and_names_bundle(self, monkeypatch, caplog):
        # Not frozen, no bundle, no system libmpv: the "no DLL shipped" hypothesis
        # (self-built/fork bundle) — previously entirely silent.
        monkeypatch.setattr(ctypes.util, "find_library", lambda name: None)
        with caplog.at_level(logging.WARNING, logger="anki_miner.utils.mpv_loader"):
            assert mpv_loader.mpv_available() is False
            assert mpv_loader.mpv_available() is False  # cached: must not re-log
        msg = "libmpv unavailable via system search"
        assert caplog.text.count(msg) == 1
        assert "bundled_found=False" in caplog.text


class TestWin32Loader:
    """Direct coverage of the Windows resolution path — previously untested (the
    other loader tests use the Linux ``libmpv.so.2`` soname and mock
    ``_import_mpv_with_path``). Runs on the Linux gate via ``sys.platform``
    monkeypatching; the real dlopen still only executes on Windows."""

    def test_bundled_libmpv_path_win32_prefers_mpv2(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
        (tmp_path / "mpv-1.dll").write_bytes(b"")
        (tmp_path / "mpv-2.dll").write_bytes(b"")
        assert mpv_loader.bundled_libmpv_path() == tmp_path / "mpv-2.dll"

    def test_import_win32_patches_find_library_and_registers_dll_dir(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "platform", "win32")
        lib = tmp_path / "libmpv-2.dll"
        lib.write_bytes(b"")
        added: list[str] = []
        # os.add_dll_directory is Windows-only; inject it so the hasattr guard
        # passes and the call is observable on the Linux gate.
        monkeypatch.setattr(os, "add_dll_directory", lambda p: added.append(p), raising=False)
        # Give the loader a controlled `original` find_library: the real one
        # probes _winapi under the faked win32 platform and blows up on Linux.
        monkeypatch.setattr(ctypes.util, "find_library", lambda name: f"SENTINEL:{name}")

        # A throwaway `mpv` module that records what find_library resolves DURING
        # its import — i.e. while the loader's monkeypatch is installed.
        modpkg = tmp_path / "mpvpkg"
        modpkg.mkdir()
        (modpkg / "mpv.py").write_text(
            "import ctypes.util\n"
            "seen = {n: ctypes.util.find_library(n) for n in "
            "('mpv', 'mpv-2.dll', 'libmpv-2.dll', 'zzz-not-mpv')}\n"
        )
        monkeypatch.syspath_prepend(str(modpkg))

        original = ctypes.util.find_library
        module = mpv_loader._import_mpv_with_path(lib)

        assert added == [str(lib.parent)]  # DLL directory registered for transitive deps
        assert module.seen["mpv"] == str(lib)
        assert module.seen["mpv-2.dll"] == str(lib)
        assert module.seen["libmpv-2.dll"] == str(lib)
        assert module.seen["zzz-not-mpv"] == "SENTINEL:zzz-not-mpv"  # non-mpv names fall through
        assert ctypes.util.find_library is original  # restored in finally
