from pathlib import Path

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
