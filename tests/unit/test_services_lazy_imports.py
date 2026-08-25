"""Import-order test for anki_miner.services' lazy service attributes.

A bare ``import anki_miner.services`` used to eagerly pull in the whole
``requests`` chain — ``AnkiService`` and ``ValidationService`` each import it
at their own module top, and the ``dictionary.providers`` re-export dragged in
``JishoProvider``'s module-level ``import requests`` too. That cost lands on
every caller of the package, including lightweight ones that only need a
narrow submodule (the ffsubsync child dispatch reaches ``anki_miner.services``
just by importing ``anki_miner.services.sync_engines._ffsubsync_child``).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _subprocess_env(home: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["ANKI_MINER_HOME"] = str(home)
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


def test_importing_services_does_not_import_requests(tmp_path: Path) -> None:
    result = _run_probe(
        "import sys; import anki_miner.services; assert 'requests' not in sys.modules",
        _subprocess_env(tmp_path / "home"),
    )
    assert result.returncode == 0, result.stderr


def test_lazy_services_are_still_reachable(tmp_path: Path) -> None:
    """AnkiService/ValidationService/ExportService stay importable, just deferred."""
    result = _run_probe(
        "import sys; import anki_miner.services as s; "
        "assert s.AnkiService.__name__ == 'AnkiService'; "
        "assert s.ValidationService.__name__ == 'ValidationService'; "
        "assert s.ExportService.__name__ == 'ExportService'; "
        "assert 'requests' in sys.modules",
        _subprocess_env(tmp_path / "home"),
    )
    assert result.returncode == 0, result.stderr


def test_jisho_provider_import_does_not_import_requests(tmp_path: Path) -> None:
    """JishoProvider is still eagerly re-exported; only its own `requests` use is deferred."""
    result = _run_probe(
        "import sys; from anki_miner.services import JishoProvider; "
        "assert JishoProvider.__name__ == 'JishoProvider'; "
        "assert 'requests' not in sys.modules",
        _subprocess_env(tmp_path / "home"),
    )
    assert result.returncode == 0, result.stderr
