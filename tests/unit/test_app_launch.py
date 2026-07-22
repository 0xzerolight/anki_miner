"""Tests for the stdlib-only GUI bootstrap entry point."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CA_ENV_VARS = ("REQUESTS_CA_BUNDLE", "SSL_CERT_FILE", "CURL_CA_BUNDLE")

PRE_QT_FAILURE_PROBE = r"""
import builtins

real_import = builtins.__import__

def fail_app_import(name, globals=None, locals=None, fromlist=(), level=0):
    if name == "anki_miner.gui.app":
        raise ImportError("pre-Qt import boom")
    return real_import(name, globals, locals, fromlist, level)

builtins.__import__ = fail_app_import
from anki_miner.gui import launch
raise SystemExit(launch.main())
"""

WINDOWS_TRUSTSTORE_PROBE = r"""
import builtins
import sys

from anki_miner.gui import launch

sys.frozen = True
sys.platform = "win32"

real_import = builtins.__import__

def fail_app_import(name, globals=None, locals=None, fromlist=(), level=0):
    if name == "anki_miner.gui.app":
        raise ImportError("stop after bootstrap")
    return real_import(name, globals, locals, fromlist, level)

builtins.__import__ = fail_app_import
try:
    launch.main()
except ImportError as exc:
    assert str(exc) == "stop after bootstrap"
print(f"TRUSTSTORE_INJECTED={launch.TRUSTSTORE_INJECTED}")
"""

PATH_HOME_FAILURE_PROBE = r"""
import logging
import os
from pathlib import Path

os.environ.pop("ANKI_MINER_HOME", None)
from anki_miner.gui import launch

def fail_home(cls):
    raise RuntimeError("home unavailable")

Path.home = classmethod(fail_home)
launch._install_early_crash_sink()
from anki_miner.config.paths import ANKI_MINER_HOME

expected_log = ANKI_MINER_HOME / "anki_miner.log"
assert launch.get_effective_log_path(expected_log) == expected_log
logging.getLogger("anki_miner.home_fallback_probe").warning("home fallback probe")
for handler in logging.getLogger().handlers:
    handler.flush()
print(expected_log)
"""

SECURE_OPEN_PROBE = r"""
import logging
import os
from anki_miner.gui import launch

real_open = launch.os.open
open_calls = []

def recording_open(path, flags, mode=0o777):
    open_calls.append((flags, mode))
    return real_open(path, flags, mode)

def reject_chmod(*args, **kwargs):
    raise AssertionError("early log setup must not use chmod")

launch.os.open = recording_open
launch.os.chmod = reject_chmod
launch._install_early_crash_sink()
assert len(open_calls) == 1
flags, mode = open_calls[0]
assert flags & os.O_WRONLY
assert flags & os.O_APPEND
assert flags & os.O_CREAT
if hasattr(os, "O_NOFOLLOW"):
    assert flags & os.O_NOFOLLOW
assert mode == 0o600
logging.getLogger("anki_miner.secure_open_probe").warning("secure open probe")
for handler in logging.getLogger().handlers:
    handler.flush()
"""

DOUBLE_INSTALL_PROBE = r"""
import logging
import sys
from pathlib import Path
from anki_miner.gui import launch

previous_calls = []

def previous_hook(exc_type, exc_value, exc_tb):
    previous_calls.append((exc_type, exc_value, exc_tb))

sys.excepthook = previous_hook
launch._install_early_crash_sink()
first_hook = sys.excepthook
first_sink = next(
    handler
    for handler in logging.getLogger().handlers
    if getattr(handler, "_anki_miner_sink", False)
)
launch._install_early_crash_sink()
sinks = [
    handler
    for handler in logging.getLogger().handlers
    if getattr(handler, "_anki_miner_sink", False)
]
try:
    raise RuntimeError("double install probe")
except RuntimeError:
    exc_type, exc_value, exc_tb = sys.exc_info()
    assert exc_type is not None
    assert exc_value is not None
    sys.excepthook(exc_type, exc_value, exc_tb)
for handler in sinks:
    handler.flush()
log_text = Path(sinks[0].baseFilename).read_text(encoding="utf-8")
assert len(sinks) == 1
assert sinks[0] is first_sink
assert sys.excepthook is first_hook
assert len(previous_calls) == 1
assert log_text.count("Unhandled exception during early startup") == 1
assert log_text.count("Traceback (most recent call last)") == 1
assert log_text.count("RuntimeError: double install probe") == 1
"""


def _subprocess_env(home: Path, **overrides: str) -> dict[str, str]:
    env = os.environ.copy()
    for name in CA_ENV_VARS:
        env.pop(name, None)
    env["ANKI_MINER_HOME"] = str(home)
    env.update(overrides)
    env["PYTHONPATH"] = os.pathsep.join((str(PROJECT_ROOT), env.get("PYTHONPATH", "")))
    return env


def _run_probe(code: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=PROJECT_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )


def _write_fake_truststore(module_dir: Path, body: str) -> None:
    module_dir.mkdir()
    (module_dir / "truststore.py").write_text(body, encoding="utf-8")


def test_importing_launch_does_not_import_qt_or_app(tmp_path: Path) -> None:
    result = _run_probe(
        "import sys; import anki_miner.gui.launch; "
        "assert 'anki_miner.gui.app' not in sys.modules; "
        "assert not any(name == 'PyQt6' or name.startswith('PyQt6.') for name in sys.modules)",
        _subprocess_env(tmp_path / "home"),
    )

    assert result.returncode == 0, result.stderr


def test_early_sink_and_root_are_warning_level(tmp_path: Path) -> None:
    result = _run_probe(
        "import logging; from anki_miner.gui import launch; "
        "launch._install_early_crash_sink(); "
        "root = logging.getLogger(); "
        "sinks = [handler for handler in root.handlers if getattr(handler, '_anki_miner_sink', False)]; "
        "assert root.level == logging.WARNING; "
        "assert len(sinks) == 1; "
        "assert sinks[0].level == logging.WARNING",
        _subprocess_env(tmp_path / "home"),
    )

    assert result.returncode == 0, result.stderr


def test_pre_qt_import_failure_logs_traceback_under_env_home(tmp_path: Path) -> None:
    home = tmp_path / "custom-home"

    result = _run_probe(PRE_QT_FAILURE_PROBE, _subprocess_env(home))

    assert result.returncode != 0
    log_text = (home / "anki_miner.log").read_text(encoding="utf-8")
    assert "CRITICAL" in log_text
    assert "Unhandled exception during early startup" in log_text
    assert "Traceback (most recent call last)" in log_text
    assert "ImportError: pre-Qt import boom" in log_text
    assert "ImportError: pre-Qt import boom" in result.stderr
    if os.name == "posix":
        assert stat.S_IMODE((home / "anki_miner.log").stat().st_mode) == 0o600


def test_empty_env_home_uses_default_user_home(tmp_path: Path) -> None:
    user_home = tmp_path / "user-home"

    result = _run_probe(
        PRE_QT_FAILURE_PROBE,
        _subprocess_env(
            tmp_path / "ignored-home",
            ANKI_MINER_HOME="",
            HOME=str(user_home),
            USERPROFILE=str(user_home),
        ),
    )

    assert result.returncode != 0
    log_path = user_home / ".anki_miner" / "anki_miner.log"
    assert "ImportError: pre-Qt import boom" in log_path.read_text(encoding="utf-8")


def test_path_home_failure_uses_runtime_temp_home_before_emergency(tmp_path: Path) -> None:
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()

    result = _run_probe(
        PATH_HOME_FAILURE_PROBE,
        _subprocess_env(
            tmp_path / "ignored-home",
            ANKI_MINER_HOME="",
            TMPDIR=str(temp_dir),
        ),
    )

    expected_log = temp_dir / ".anki_miner" / "anki_miner.log"
    assert result.returncode == 0, result.stderr
    assert str(expected_log) in result.stdout
    assert "home fallback probe" in expected_log.read_text(encoding="utf-8")
    assert not (temp_dir / "AnkiMiner-early-crash.log").exists()


def test_early_sink_uses_owner_only_no_follow_open(tmp_path: Path) -> None:
    home = tmp_path / "home"

    result = _run_probe(SECURE_OPEN_PROBE, _subprocess_env(home))

    assert result.returncode == 0, result.stderr
    assert "secure open probe" in (home / "anki_miner.log").read_text(encoding="utf-8")


@pytest.mark.skipif(not hasattr(os, "O_NOFOLLOW"), reason="O_NOFOLLOW is unavailable")
def test_emergency_log_does_not_follow_symlink(tmp_path: Path) -> None:
    blocked_parent = tmp_path / "blocked"
    blocked_parent.write_text("not a directory", encoding="utf-8")
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()
    victim = tmp_path / "victim.txt"
    victim.write_text("do not touch\n", encoding="utf-8")
    (temp_dir / "AnkiMiner-early-crash.log").symlink_to(victim)

    result = _run_probe(
        "import logging; from anki_miner.gui import launch; "
        "launch._install_early_crash_sink(); "
        "assert not any(getattr(handler, '_anki_miner_sink', False) "
        "for handler in logging.getLogger().handlers)",
        _subprocess_env(blocked_parent / "home", TMPDIR=str(temp_dir)),
    )

    assert result.returncode == 0, result.stderr
    assert victim.read_text(encoding="utf-8") == "do not touch\n"


def test_double_early_sink_install_reuses_handler_and_hook(tmp_path: Path) -> None:
    result = _run_probe(DOUBLE_INSTALL_PROBE, _subprocess_env(tmp_path / "home"))

    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits are needed to make the home unwritable")
def test_unwritable_home_falls_back_to_temp_log(tmp_path: Path) -> None:
    blocked_parent = tmp_path / "blocked"
    blocked_parent.mkdir()
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()
    blocked_parent.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        result = _run_probe(
            PRE_QT_FAILURE_PROBE,
            _subprocess_env(blocked_parent / "home", TMPDIR=str(temp_dir)),
        )
    finally:
        blocked_parent.chmod(stat.S_IRWXU)

    assert result.returncode != 0
    fallback_log = temp_dir / "AnkiMiner-early-crash.log"
    log_text = fallback_log.read_text(encoding="utf-8")
    assert "ImportError: pre-Qt import boom" in log_text
    assert not (blocked_parent / "home" / "anki_miner.log").exists()


def test_frozen_windows_injects_truststore_and_sets_flag(tmp_path: Path) -> None:
    fake_modules = tmp_path / "fake-modules"
    marker = tmp_path / "injected"
    _write_fake_truststore(
        fake_modules,
        "import os\n"
        "def inject_into_ssl():\n"
        "    with open(os.environ['TRUSTSTORE_MARKER'], 'w', encoding='utf-8') as stream:\n"
        "        stream.write('yes')\n",
    )
    env = _subprocess_env(
        tmp_path / "home",
        PYTHONPATH=os.pathsep.join((str(fake_modules), os.environ.get("PYTHONPATH", ""))),
        TRUSTSTORE_MARKER=str(marker),
    )

    result = _run_probe(WINDOWS_TRUSTSTORE_PROBE, env)

    assert result.returncode == 0, result.stderr
    assert marker.read_text(encoding="utf-8") == "yes"
    assert "TRUSTSTORE_INJECTED=True" in result.stdout


@pytest.mark.parametrize("ca_env", CA_ENV_VARS)
def test_frozen_windows_skips_truststore_when_ca_env_is_set(tmp_path: Path, ca_env: str) -> None:
    fake_modules = tmp_path / "fake-modules"
    marker = tmp_path / "imported"
    _write_fake_truststore(
        fake_modules,
        "import os\n"
        "with open(os.environ['TRUSTSTORE_MARKER'], 'w', encoding='utf-8') as stream:\n"
        "    stream.write('imported')\n"
        "def inject_into_ssl():\n"
        "    raise AssertionError('must not inject with an explicit CA environment variable')\n",
    )
    env = _subprocess_env(
        tmp_path / "home",
        PYTHONPATH=os.pathsep.join((str(fake_modules), os.environ.get("PYTHONPATH", ""))),
        TRUSTSTORE_MARKER=str(marker),
    )
    env[ca_env] = str(tmp_path / "corporate-ca.pem")

    result = _run_probe(WINDOWS_TRUSTSTORE_PROBE, env)

    assert result.returncode == 0, result.stderr
    assert not marker.exists()
    assert "TRUSTSTORE_INJECTED=False" in result.stdout


def test_truststore_injection_failure_logs_one_warning_and_fails_open(tmp_path: Path) -> None:
    fake_modules = tmp_path / "fake-modules"
    _write_fake_truststore(
        fake_modules,
        "def inject_into_ssl():\n    raise RuntimeError('truststore unavailable')\n",
    )
    home = tmp_path / "home"
    env = _subprocess_env(
        home,
        PYTHONPATH=os.pathsep.join((str(fake_modules), os.environ.get("PYTHONPATH", ""))),
    )

    result = _run_probe(WINDOWS_TRUSTSTORE_PROBE, env)

    assert result.returncode == 0, result.stderr
    assert "TRUSTSTORE_INJECTED=False" in result.stdout
    log_text = (home / "anki_miner.log").read_text(encoding="utf-8")
    assert log_text.count("Failed to inject Windows trust store") == 1
    assert "RuntimeError: truststore unavailable" in log_text


def test_script_entry_shares_public_truststore_flag(tmp_path: Path) -> None:
    fake_modules = tmp_path / "fake-modules"
    _write_fake_truststore(fake_modules, "def inject_into_ssl():\n    pass\n")
    (fake_modules / "sitecustomize.py").write_text(
        "import builtins\n"
        "import logging\n"
        "import os\n"
        "import pathlib\n"
        "import sys\n"
        "import tempfile\n"
        "import types\n"
        "import typing\n"
        "sys.frozen = True\n"
        "sys.platform = 'win32'\n"
        "real_import = builtins.__import__\n"
        "def inspect_app_import(name, globals=None, locals=None, fromlist=(), level=0):\n"
        "    if name == 'anki_miner.gui.app':\n"
        "        canonical = sys.modules.get('anki_miner.gui.launch')\n"
        "        print(f'CANONICAL_ALIAS={canonical is sys.modules[\"__main__\"]}', flush=True)\n"
        "        print(f'CANONICAL_FLAG={canonical.TRUSTSTORE_INJECTED}', flush=True)\n"
        "        raise ImportError('stop after script bootstrap')\n"
        "    return real_import(name, globals, locals, fromlist, level)\n"
        "builtins.__import__ = inspect_app_import\n",
        encoding="utf-8",
    )
    env = _subprocess_env(
        tmp_path / "home",
        PYTHONPATH=os.pathsep.join((str(fake_modules), os.environ.get("PYTHONPATH", ""))),
    )

    result = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "anki_miner" / "gui" / "launch.py")],
        cwd=PROJECT_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert result.returncode != 0
    assert "CANONICAL_ALIAS=True" in result.stdout
    assert "CANONICAL_FLAG=True" in result.stdout


def test_gui_entrypoints_and_dependency_use_launch() -> None:
    pyproject = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    spec_text = (PROJECT_ROOT / "anki_miner.spec").read_text(encoding="utf-8")
    main_text = (PROJECT_ROOT / "anki_miner" / "__main__.py").read_text(encoding="utf-8")

    assert pyproject["project"]["scripts"]["anki_miner_gui"] == "anki_miner.gui.launch:main"
    assert "truststore>=0.10" in pyproject["project"]["dependencies"]
    assert '"anki_miner", "gui", "launch.py"' in spec_text
    assert '"anki_miner", "gui", "app.py"' not in spec_text
    assert "from anki_miner.gui.launch import main" in main_text
