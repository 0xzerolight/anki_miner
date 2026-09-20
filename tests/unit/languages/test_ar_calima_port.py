"""The in-tree CAMeL Tools port (``languages/ar/_calima``) against the real calima-msa-r13 database.

``calima_parity.jsonl`` pins, per word, the digest of every analysis upstream camel_tools
(``be79ca9f``) returns; the port must return the same analyses. The port imports nothing outside the
standard library: upstream's ``camel_tools.data`` downloads a catalogue from GitHub at import.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from anki_miner.languages.ar._calima.analyzer import Analyzer
from anki_miner.languages.ar._calima.database import MorphologyDB
from tests._pack_seeds import seeded_component

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "ar"
ROWS = [json.loads(line) for line in (FIXTURES / "calima_parity.jsonl").read_text(encoding="utf-8").splitlines()]
DB_SHA256 = "195bc25a333237a2126470da888d7936b59ed3729f9210e0a4194ba43497dd70"


@pytest.fixture(scope="module")
def db_path() -> Path:
    return seeded_component("ar", "calima_msa", "morphology.db")


@pytest.fixture(scope="module")
def analyzer(db_path: Path) -> Analyzer:
    return Analyzer(MorphologyDB(db_path))


def _digest(analyses: list[dict]) -> str:
    canon = sorted(json.dumps(analysis, sort_keys=True, ensure_ascii=False) for analysis in analyses)
    return hashlib.sha256(json.dumps(canon, ensure_ascii=False).encode("utf-8")).hexdigest()


def test_the_seed_is_the_pinned_database(db_path):
    assert hashlib.sha256(db_path.read_bytes()).hexdigest() == DB_SHA256


@pytest.mark.parametrize("row", ROWS, ids=[row["sha256"][:10] for row in ROWS])
def test_the_port_returns_upstreams_analyses(analyzer, row):
    analyses = analyzer.analyze(row["word"])
    assert len(analyses) == row["count"]
    assert _digest(analyses) == row["sha256"]


def test_importing_the_port_loads_only_the_standard_library():
    """What the import ADDS must be stdlib: a bare interpreter already carries the venv's bootstrap."""
    probe = (
        "import sys\n"
        "tops = lambda: {m.split('.')[0] for m in list(sys.modules)}\n"
        "before = tops()\n"
        "import anki_miner.languages.ar._calima.analyzer\n"
        "added = tops() - before - set(sys.stdlib_module_names) - {'anki_miner'}\n"
        "print(sorted(added))"
    )
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True).stdout
    assert out.strip() == "[]"
