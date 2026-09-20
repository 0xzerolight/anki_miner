"""pythainlp spec wiring: excluded from the graph, reached only through the pack.

The engine ships as an in-app download pack, never in the bundle, so what is
pinned here is the absence — plus the two ``anki_miner.languages.th`` modules the
spec must still pin INTO the graph, because both are importlib f-string targets
that bytecode analysis cannot see. The generic exclude/hiddenimport assertions
live in ``test_zh_bundling.py``.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SPEC = ROOT / "anki_miner.spec"


def test_the_engine_and_its_timezone_data_are_excluded_from_the_graph():
    """Both halves of the th pack, so their absence from a bundle is a guarantee.

    ``tzdata`` is excluded for the same reason as ``pythainlp``: it exists only
    to satisfy ``pythainlp.util.date``'s ``ZoneInfo("Asia/Bangkok")`` on Windows,
    inside the pack. Nothing in ``anki_miner`` imports ``zoneinfo``, so it would
    not be collected today — the exclude is what keeps a transitive pull from
    silently adding it.
    """
    spec = SPEC.read_text(encoding="utf-8")
    _hiddenimports, _, excludes = spec.partition("excludes=[")

    assert '"pythainlp",' in excludes
    assert '"tzdata",' in excludes


def test_the_th_tokenizer_and_pack_manifest_are_pinned_into_the_graph():
    """Both are importlib f-string targets; the pack manifest is what load_pack imports."""
    from tests.unit.languages.test_zh_bundling import generated_language_hiddenimports

    generated = generated_language_hiddenimports()
    assert "anki_miner.languages.th.tokenizer" in generated
    assert "anki_miner.languages.th.pack" in generated
