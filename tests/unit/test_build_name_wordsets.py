"""Unit tests for scripts/build_name_wordsets.py — bucketing, overlap drop, header format."""

import sys
from pathlib import Path

# scripts/ is not a package; insert repo root so the module is importable.
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scripts.build_name_wordsets import build_wordsets  # noqa: E402

FIXTURES = Path(__file__).parent.parent / "fixtures"


def test_buckets_and_drops_common_overlaps(tmp_path):
    build_wordsets(
        jmnedict=FIXTURES / "jmnedict_mini.xml",
        jmdict=FIXTURES / "jmdict_mini.xml",
        out_dir=tmp_path,
    )
    surnames = (tmp_path / "surnames.txt").read_text(encoding="utf-8")
    given = (tmp_path / "given-names.txt").read_text(encoding="utf-8")
    places = (tmp_path / "place-names.txt").read_text(encoding="utf-8")
    org = (tmp_path / "org-product.txt").read_text(encoding="utf-8")

    assert "田中" in surnames
    assert "渋谷" in places
    assert "太郎" in given
    assert "ソニー" in org
    assert "春" not in given  # common JMdict word → dropped
    assert "# id: surnames" in surnames
    assert "# label: Surnames" in surnames
    assert "# count:" in surnames
