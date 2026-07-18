from pathlib import Path

from anki_miner.services import wordset_service
from anki_miner.services.wordset_service import WordsetService, load_wordset_catalog

FIXTURES = Path(__file__).parent.parent / "fixtures" / "wordsets"


def test_catalog_reads_header_metadata_without_loading_words():
    catalog = load_wordset_catalog(resource_dir=FIXTURES)
    by_id = {c.id: c for c in catalog}
    assert by_id["surnames"].label == "Surnames"
    assert by_id["surnames"].count == 2
    assert by_id["place-names"].label == "Place names"


def test_load_unions_only_enabled_sets():
    svc = WordsetService(enabled_ids=("surnames",), resource_dir=FIXTURES)
    svc.load()
    assert svc.is_available()
    assert svc.is_excluded("田中")
    assert svc.is_excluded("鈴木")
    assert not svc.is_excluded("渋谷")  # place-names not enabled


def test_load_with_no_enabled_sets_is_unavailable():
    svc = WordsetService(enabled_ids=(), resource_dir=FIXTURES)
    svc.load()
    assert not svc.is_available()


def test_unknown_id_is_skipped_gracefully():
    svc = WordsetService(enabled_ids=("surnames", "does-not-exist"), resource_dir=FIXTURES)
    svc.load()
    assert svc.is_excluded("田中")


def test_repeated_load_reuses_cached_union_without_rereading(monkeypatch):
    """Second load of the same (dir, ids) returns the SAME frozenset, no re-read.

    Guards the RSS-ratchet fix: the factory rebuilds a WordsetService every
    mining run, so a fresh ~45 MB read per load climbs RSS. The union is cached
    process-wide, so later loads must reuse the object and skip the file reads.
    """
    wordset_service._UNION_CACHE.clear()

    reads: list[Path] = []
    real_read = wordset_service._read_words
    monkeypatch.setattr(
        wordset_service,
        "_read_words",
        lambda path: (reads.append(path), real_read(path))[1],
    )

    first = WordsetService(enabled_ids=("surnames",), resource_dir=FIXTURES)
    first.load()
    assert len(reads) == 1  # file read exactly once on the cold load

    second = WordsetService(enabled_ids=("surnames",), resource_dir=FIXTURES)
    second.load()
    assert len(reads) == 1  # warm load did NOT re-read any file
    assert second._blacklist is first._blacklist  # same object reused
    assert second.is_excluded("田中")


def test_cache_key_distinguishes_id_sets(monkeypatch):
    """A different enabled-id set is a distinct cache entry (own union)."""
    wordset_service._UNION_CACHE.clear()

    surnames = WordsetService(enabled_ids=("surnames",), resource_dir=FIXTURES)
    surnames.load()
    places = WordsetService(enabled_ids=("place-names",), resource_dir=FIXTURES)
    places.load()

    assert surnames._blacklist is not places._blacklist
    assert surnames.is_excluded("田中") and not surnames.is_excluded("渋谷")
    assert places.is_excluded("渋谷") and not places.is_excluded("田中")
