from pathlib import Path

from anki_miner.config import AnkiMinerConfig
from anki_miner.models import TokenizedWord
from anki_miner.services.word_filter import WordFilterService
from anki_miner.services.word_list_service import WordListService
from anki_miner.services.wordset_service import WordsetService

FIXTURES = Path(__file__).parent.parent / "fixtures" / "wordsets"


def _word(lemma: str) -> TokenizedWord:
    return TokenizedWord(
        surface=lemma,
        lemma=lemma,
        reading="",
        sentence="例文",
        start_time=0.0,
        end_time=1.0,
        duration=1.0,
    )


def test_filter_by_wordsets_drops_blacklisted_lemma():
    svc = WordsetService(enabled_ids=("surnames",), resource_dir=FIXTURES)
    svc.load()
    wf = WordFilterService(AnkiMinerConfig())
    words = [_word("田中"), _word("食べる")]
    result = wf.filter_by_wordsets(words, svc)
    assert [w.lemma for w in result] == ["食べる"]


def test_whitelist_rescues_wordset_word(tmp_path):
    wl_file = tmp_path / "wl.txt"
    wl_file.write_text("田中\n", encoding="utf-8")
    wls = WordListService(whitelist_path=wl_file)
    wls.load()
    svc = WordsetService(enabled_ids=("surnames",), resource_dir=FIXTURES)
    svc.load()
    wf = WordFilterService(AnkiMinerConfig())
    result = wf.filter_by_wordsets([_word("田中")], svc, wls)
    assert [w.lemma for w in result] == ["田中"]


def test_no_word_list_service_still_filters():
    svc = WordsetService(enabled_ids=("surnames",), resource_dir=FIXTURES)
    svc.load()
    wf = WordFilterService(AnkiMinerConfig())
    result = wf.filter_by_wordsets([_word("田中")], svc, None)
    assert result == []
