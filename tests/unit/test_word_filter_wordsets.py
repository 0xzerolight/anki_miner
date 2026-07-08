from pathlib import Path

from anki_miner.config import AnkiMinerConfig
from anki_miner.models import TokenizedWord
from anki_miner.services.word_filter import WordFilterService
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


def test_filter_by_wordsets_keys_on_mined_form_not_lemma():
    """A name noun whose surface is in the wordset but whose unidic lemma
    diverges (the 豪腕→剛腕 class) must still be excluded: the wordset data
    is JMnedict surface forms and noun cards use mined_form (= surface),
    so the filter keys on mined_form, not lemma."""
    svc = WordsetService(enabled_ids=("surnames",), resource_dir=FIXTURES)
    svc.load()
    wf = WordFilterService(AnkiMinerConfig())
    # surface 田中 is in the surnames fixture; lemma 田仲 is not.
    name = TokenizedWord(
        surface="田中",
        lemma="田仲",
        reading="たなか",
        sentence="例文",
        start_time=0.0,
        end_time=1.0,
        duration=1.0,
        pos="名詞",
    )
    assert name.mined_form == "田中"
    result = wf.filter_by_wordsets([name], svc)
    assert result == []
