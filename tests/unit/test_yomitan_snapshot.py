"""Snapshot test for the full Yomitan import → lookup round-trip.

This is the canonical regression guard for the entire dictionary pipeline:
fixture zip → ``import_yomitan_zip`` → ``open_readonly`` → ``IndexedDictProvider.lookup``.
Each layer is unit-tested in isolation; this test pins the wire shape of the
HTML the provider hands to the Anki card template.

If a future change shifts whitespace, attribute order, a class name, or the
envelope structure of the rendered card, this test fails byte-for-byte and the
expected snapshot file must be regenerated deliberately — not silently updated.

Authoring procedure (see Task 8 plan):
1. Edit the fixture inputs below.
2. Run the test once; on failure, print the actual output (e.g. uncomment the
   debug line) and copy it into ``fixtures/yomitan_snapshot_expected.html``
   with no trailing newline.
3. Re-run; commit both files together.
"""

from pathlib import Path

from anki_miner.services.dictionary.importers.yomitan_importer import import_yomitan_zip
from anki_miner.services.dictionary.providers.indexed_provider import IndexedDictProvider
from tests.fixtures.dictionary.build_yomitan_fixture import build_yomitan_zip

# Minimal valid 1x1 transparent PNG. Kept inline so the test has zero extra
# fixture files to maintain — the bytes themselves never appear in the rendered
# HTML (they're written into the dict's media/ folder, not the SQLite row).
_PNG_1x1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c63000100000005000159c8e1740000000049454e44ae426082"
)

_EXPECTED_PATH = Path(__file__).parent / "fixtures" / "yomitan_snapshot_expected.html"


def test_import_lookup_roundtrip_matches_snapshot(tmp_path: Path) -> None:
    """Build a synthetic Yomitan zip, import it, look up the term, assert HTML
    matches the checked-in snapshot byte-for-byte.

    Fixture covers every shape the renderer + provider must lock down:
      - One term-bank entry with two senses (plain string + structured-content).
      - One POS tag (``v5r``) in column 3 (``definitionTags``).
      - One additional tag (``common``) in column 8 (``termTags``).
      - One inline structured-content table (thead/tbody, 1 row each).
      - One bundled image, exercising the
        ``<a class="gloss-image-link">…<img class="gloss-image anki-miner-dict-media">``
        envelope and the dict-namespaced flat src filename.
    """
    zip_path = build_yomitan_zip(
        tmp_path / "src" / "snap.zip",
        title="snap-dict",
        revision="1",
        term_banks=[
            [
                [
                    "する",  # term
                    "する",  # reading
                    "v5r",  # definitionTags → POS badge in italic line
                    "v5r",  # ruleIdentifiers (unused by renderer)
                    100,  # score
                    [
                        # Sense 1: plain string. Renderer wraps in
                        # <li class="gloss-item"><div class="gloss-content">…</div></li>.
                        "to do",
                        # Sense 2: structured-content tree carrying a table + bundled image.
                        {
                            "type": "structured-content",
                            "content": {
                                "tag": "div",
                                "content": [
                                    {
                                        "tag": "table",
                                        "content": [
                                            {
                                                "tag": "thead",
                                                "content": {
                                                    "tag": "tr",
                                                    "content": {
                                                        "tag": "th",
                                                        "content": "form",
                                                    },
                                                },
                                            },
                                            {
                                                "tag": "tbody",
                                                "content": {
                                                    "tag": "tr",
                                                    "content": {
                                                        "tag": "td",
                                                        "content": "する",
                                                    },
                                                },
                                            },
                                        ],
                                    },
                                    {"tag": "img", "path": "img/diagram.png"},
                                ],
                            },
                        },
                    ],
                    1,  # sequence
                    "common",  # termTags → extra badge after POS in italic line
                ],
            ],
        ],
        tag_banks=[],  # Tags resolve from columns 3 + 8; tag_bank files are optional now.
        media_files={"img/diagram.png": _PNG_1x1},
    )

    dest_root = tmp_path / "dicts"
    result = import_yomitan_zip(zip_path, dest_root)

    # display_name is what shows up in the italic line; pin it to the title so
    # the snapshot is human-readable instead of using the slugged dict_id.
    provider = IndexedDictProvider(
        result.dict_id,
        dest_root / result.dict_id / "index.sqlite",
        display_name="snap-dict",
    )
    assert provider.load() is True
    try:
        actual = provider.lookup("する")
    finally:
        provider.close()

    assert actual is not None
    expected = _EXPECTED_PATH.read_text(encoding="utf-8")
    # The snapshot file is authored with no trailing newline so this is a true
    # byte-for-byte comparison. If `actual` ever grows a trailing newline, that
    # is a real change in the provider's output and the snapshot should be
    # updated deliberately — do not normalize it away here.
    assert actual == expected, (
        "Yomitan import→lookup HTML drifted from the checked-in snapshot.\n"
        "If this change is intentional, update "
        "tests/unit/fixtures/yomitan_snapshot_expected.html with the new output "
        "(no trailing newline) and commit both files together.\n\n"
        f"--- expected ({len(expected)} bytes) ---\n{expected!r}\n"
        f"--- actual ({len(actual)} bytes) ---\n{actual!r}\n"
    )


def test_import_lookup_grouped_render_separates_sequences(tmp_path: Path) -> None:
    """Full import→lookup snapshot for the 5.1 sequence-grouping path: two term
    entries sharing a reading (はし) but carrying DIFFERENT sequences (1 vs 2)
    render as two sub-blocks, each with its OWN tag line, inside one
    <li data-dictionary> envelope."""
    zip_path = build_yomitan_zip(
        tmp_path / "src" / "grp.zip",
        title="grp-dict",
        revision="1",
        term_banks=[
            [
                ["橋", "はし", "n", "n", 0, ["bridge"], 1, ""],
                ["箸", "はし", "n", "n", 0, ["chopsticks"], 2, ""],
            ],
        ],
        tag_banks=[],
    )

    dest_root = tmp_path / "dicts"
    result = import_yomitan_zip(zip_path, dest_root)
    provider = IndexedDictProvider(
        result.dict_id,
        dest_root / result.dict_id / "index.sqlite",
        display_name="grp-dict",
    )
    assert provider.load() is True
    try:
        actual = provider.lookup("はし")
    finally:
        provider.close()

    assert actual is not None
    # One envelope, two grouped gloss-lists, two per-group tag lines.
    assert actual.count("<li data-dictionary=") == 1
    assert actual.count('<ul class="gloss-list"') == 2
    assert actual.count("<i>(n, grp-dict)</i>") == 2
    # Both lexemes present; bridge (seq1) precedes chopsticks (seq2).
    assert "bridge" in actual and "chopsticks" in actual
    assert actual.index("bridge") < actual.index("chopsticks")
