"""SHARED-06: a lemmatised frequency list files each word under the front the parser would give it.

Tagged alone, a list word can come back with a lemma no dictionary knows (de ``welt`` -> ``Weln``, it
``ragazza`` -> ``ragazzare``), and its count then ranks a key no card ever shows. Given the
dictionaries folder, the lemmatiser also runs the language parser's own ``token_post_pass`` over the
dictionaries installed there for that language, with the existence probe and the form rows the parser
gets from ``service_factory``: the key is the card front. No dictionary for the language: the tagger's
lemma, as before.

The stub-tagger half pins the wiring; the real-engine half runs the shipped models (module-scoped
taggers: the autouse conftest fixture clears the tagger cache around every test).
"""

from __future__ import annotations

import json
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.workers import resource_download_worker
from anki_miner.gui.workers.import_worker import ImportWorker
from anki_miner.gui.workers.resource_download_worker import ResourceDownloadWorker
from anki_miner.languages import tagger_provider
from anki_miner.languages.token import LanguageToken
from anki_miner.services.dictionary.importers.yomitan_importer import import_yomitan_zip
from anki_miner.services.frequency import mode_probe
from anki_miner.services.frequency.lemmatize import build_frequency_lemmatizer, manual_import_lemmatizer
from anki_miner.services.frequency.providers.indexed_freq_provider import IndexedFreqProvider
from anki_miner.services.frequency.source_importer import import_frequency_source
from anki_miner.services.resource_catalog import ResourceSpec


def headword(term: str, tags: str, gloss: str = "a sense") -> list:
    """A wty lemma row: its first tag is the part of speech the form-of pass reads."""
    return [term, "", tags, "", 0, [gloss], 1, ""]


def form_of(term: str, target: str) -> list:
    """A wty ``non-lemma`` row naming one lemma, in the raw shape the importer renders."""
    return [term, "", "non-lemma", "", 0, [[target, ["inflection"]]], 0, ""]


def install_dictionary(dicts_root: Path, language: str, rows: list[list]) -> None:
    """Import *rows* as ``wty-<language>-en`` stamped for *language*, the way the catalogue does."""
    archive = dicts_root.parent / f"wty-{language}-en.zip"
    index = {"title": f"wty-{language}-en", "format": 3, "revision": "t", "sourceLanguage": language}
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("index.json", json.dumps(index))
        zf.writestr("term_bank_1.json", json.dumps(rows, ensure_ascii=False))
    import_yomitan_zip(archive, dicts_root, dict_id=f"wty-{language}-en", language=language)


class ScriptedTagger:
    """One token per word, tagged and lemmatised the way the table says (the model's mistakes)."""

    def __init__(self, table: dict[str, tuple[str, str]]) -> None:
        self._table = table

    def __call__(self, text: str) -> list[LanguageToken]:
        tokens = []
        for word in text.split():
            pos1, lemma = self._table.get(word, ("X", word))
            tokens.append(LanguageToken(word, pos1, lemma=lemma))
        return tokens


DE_TAGS = {"welt": ("NOUN", "Weln"), "häuser": ("NOUN", "Häuser"), "haus": ("NOUN", "Haus")}
DE_ROWS = [headword("Welt", "n"), headword("Haus", "n"), form_of("Häuser", "Haus")]


@pytest.fixture
def de_tagger(monkeypatch):
    monkeypatch.setitem(tagger_provider._TAGGERS, "de", ScriptedTagger(DE_TAGS))


def test_with_the_language_s_dictionary_the_key_is_the_card_front(tmp_path, de_tagger):
    install_dictionary(tmp_path / "dicts", "de", DE_ROWS)

    lemmatize = build_frequency_lemmatizer("de", tmp_path / "dicts")

    assert lemmatize(["welt", "häuser", "haus", "don't"]) == ["Welt", "Haus", "Haus", "don't"]


def test_without_a_dictionary_folder_the_tagger_lemma_stands(de_tagger):
    assert build_frequency_lemmatizer("de")(["welt", "häuser"]) == ["Weln", "Häuser"]


def test_a_folder_with_no_dictionary_for_the_language_keeps_the_tagger_lemma(tmp_path, de_tagger):
    install_dictionary(tmp_path / "dicts", "nl", [headword("welt", "n")])

    assert build_frequency_lemmatizer("de", tmp_path / "dicts")(["welt"]) == ["Weln"]
    assert build_frequency_lemmatizer("de", tmp_path / "missing")(["welt"]) == ["Weln"]


def test_a_dictionary_installed_after_the_lemmatiser_was_built_is_read(tmp_path, de_tagger):
    """The catalogue imports the dictionary first; the lemmatiser reads the folder when it runs."""
    lemmatize = build_frequency_lemmatizer("de", tmp_path / "dicts")
    install_dictionary(tmp_path / "dicts", "de", DE_ROWS)

    assert lemmatize(["welt"]) == ["Welt"]


FR_TAGS = {
    "gare": ("VERB", "gare"),
    "porte": ("VERB", "porte"),
    "viens": ("VERB", "vien"),
    "parle": ("VERB", "parle"),
    "est": ("AUX", "être"),
}
FR_ROWS = [
    headword("parler", "v"),
    form_of("parle", "parler"),
    headword("gare", "n fem"),
    headword("garer", "v"),
    form_of("gare", "garer"),
    headword("porte", "n fem"),
    headword("porter", "v"),
    form_of("porte", "porter"),
    headword("venir", "v"),
    form_of("viens", "venir"),
    headword("est", "n masc"),
    headword("être", "v"),
]


def test_a_list_word_the_dictionary_files_as_a_headword_keeps_its_own_key(tmp_path, monkeypatch):
    """E2E-1-03: tagged alone, fr ``gare`` is a verb, and the parser's ``-e`` repair would file it under ``garer``.

    The card for ``la gare`` fronts ``gare``. A word the dictionary files only as a form still moves (``parle`` ->
    ``parler``, ``viens`` -> ``venir``), and a lemma the tagger chose itself stands (``est`` -> ``être``, although
    ``est`` is a noun too).
    """
    monkeypatch.setitem(tagger_provider._TAGGERS, "fr", ScriptedTagger(FR_TAGS))
    install_dictionary(tmp_path / "dicts", "fr", FR_ROWS)

    lemmatize = build_frequency_lemmatizer("fr", tmp_path / "dicts")

    assert lemmatize(["gare", "porte", "parle", "viens", "est"]) == ["gare", "porte", "parler", "venir", "être"]


#: wty-el-en's second form-row shape: a row tagged as a verb whose gloss is an inflection of another.
EL_INFLECTION_GLOSS = {
    "type": "structured-content",
    "content": [
        {
            "tag": "ol",
            "data": {"content": "glosses"},
            "content": [{"tag": "li", "content": "second-person singular present of ξέρω (kséro)"}],
        }
    ],
}


@pytest.mark.parametrize(
    ("code", "tags", "rows", "word", "key"),
    [
        # A name row is no headword of the common word (tr_row_targets): sever is "loves".
        (
            "tr",
            {"sever": ("VERB", "sever")},
            [headword("sever", "name"), form_of("sever", "sevmek"), headword("sevmek", "v")],
            "sever",
            "sevmek",
        ),
        # The verb row the tagger's own lemma has is an inflection gloss: the move stays inside its class.
        (
            "el",
            {"ξέρεις": ("VERB", "ξέρεις")},
            [["ξέρεις", "", "v sg", "", 0, [EL_INFLECTION_GLOSS], 1, ""], headword("ξέρω", "v")],
            "ξέρεις",
            "ξέρω",
        ),
    ],
)
def test_a_word_kept_as_its_own_lemma_still_moves_off_a_name_or_inside_its_class(
    tmp_path, monkeypatch, code, tags, rows, word, key
):
    monkeypatch.setitem(tagger_provider._TAGGERS, code, ScriptedTagger(tags))
    install_dictionary(tmp_path / "dicts", code, rows)

    assert build_frequency_lemmatizer(code, tmp_path / "dicts")([word]) == [key]


def test_a_hand_added_list_reads_the_same_dictionaries(tmp_path, de_tagger):
    install_dictionary(tmp_path / "dicts", "de", DE_ROWS)

    lemmatize = manual_import_lemmatizer("de", tmp_path / "dicts")

    assert lemmatize is not None and lemmatize(["welt"]) == ["Welt"]


# --------------------------------------------------------------------------
# Every route that lemmatises a list hands it the dictionaries folder
# --------------------------------------------------------------------------


def _slot(freqs_root: Path, source_id: str) -> IndexedFreqProvider:
    provider = IndexedFreqProvider(source_id, freqs_root / source_id / "index.sqlite", "x")
    assert provider.load()
    return provider


def test_the_catalogue_download_ranks_its_list_by_the_dictionary_it_just_imported(tmp_path, monkeypatch, de_tagger):
    """The worker imports the dictionary first, then the list, whose ``welt`` rank lands on the ``Welt`` card."""
    staged = tmp_path / "staged"
    staged.mkdir()
    install_dictionary(staged / "unused", "de", DE_ROWS)  # leaves the zip at staged/wty-de-en.zip
    (staged / "de_50k.txt").write_text("haus 50\nwelt 40\nhäuser 30\n", encoding="utf-8")
    specs = [
        ResourceSpec(id="wty-de-en", kind="dict", display_name="D", url="https://x/wty-de-en.zip", license_note="n"),
        ResourceSpec(
            id="opensubtitles-de",
            kind="freq",
            display_name="OS",
            url="https://x/de_50k.txt",
            license_note="n",
            lemmatise=True,
        ),
    ]

    def fake_download(url, *, dest_dir, **_kwargs):
        name = url.rsplit("/", 1)[1]
        part = Path(dest_dir) / f"{name}.part"
        part.write_bytes((staged / name).read_bytes())
        return part

    monkeypatch.setattr(resource_download_worker, "download_to_temp", fake_download)
    (tmp_path / "dl").mkdir()
    worker = ResourceDownloadWorker(
        specs,
        dicts_root=tmp_path / "dicts",
        freqs_root=tmp_path / "freqs",
        pitch_root=tmp_path / "pitch",
        download_dir=tmp_path / "dl",
        language="de",
    )

    worker.run()

    provider = _slot(tmp_path / "freqs", "opensubtitles-de")
    assert (provider.lookup("haus"), provider.lookup("welt"), provider.lookup("weln")) == (1, 2, None)


def test_every_catalogue_lists_its_dictionary_before_its_lemmatised_list():
    """The download worker imports in catalogue order; a list ahead of its dictionary would key by the tagger alone."""
    from anki_miner.languages import AVAILABLE_LANGUAGES
    from anki_miner.languages.registry import get_profile

    for code in AVAILABLE_LANGUAGES:
        kinds = [spec.kind for spec in get_profile(code).catalog]
        for index, spec in enumerate(get_profile(code).catalog):
            if spec.lemmatise:
                assert "dict" in kinds[:index], code


def test_the_manual_flow_hands_both_workers_the_config_s_dictionaries(qapp, tmp_path, monkeypatch, de_tagger):
    from anki_miner.gui.controllers.frequency_import_flow import FrequencyImportFlow

    install_dictionary(tmp_path / "dicts", "de", DE_ROWS)
    config = replace(AnkiMinerConfig(), language="de", dicts_root=tmp_path / "dicts")
    added: dict = {}
    repaired: dict = {}
    monkeypatch.setattr(ImportWorker, "for_source", classmethod(lambda cls, *a, **kw: added.update(kw)))
    monkeypatch.setattr(ImportWorker, "for_source_repair", classmethod(lambda cls, *a, **kw: repaired.update(kw)))
    flow = FrequencyImportFlow(None, None, lambda: config, lambda _chain: None, lambda: None)

    flow._make_add_worker(tmp_path / "a.txt", tmp_path / "freqs")
    flow._make_repair_worker(tmp_path / "a.txt", tmp_path / "freqs", source_id="a", source_name="A")

    assert added["lemmatize"](["welt"]) == ["Welt"]
    assert repaired["dicts_root"] == tmp_path / "dicts"


def test_a_re_import_rebuilds_a_lemmatised_list_by_card_front(tmp_path, de_tagger):
    """A slot built before the fix changes on re-import: the repair worker reads the dictionaries too."""
    source = tmp_path / "de_50k.txt"
    source.write_text("haus 50\nwelt 40\n", encoding="utf-8")
    freqs = tmp_path / "freqs"
    result = import_frequency_source(
        source,
        freqs,
        language="de",
        declared_mode=mode_probe.OCCURRENCE_BASED,
        lemmatize=build_frequency_lemmatizer("de"),
    )
    assert _slot(freqs, result.source_id).lookup("weln") == 2
    install_dictionary(tmp_path / "dicts", "de", DE_ROWS)

    worker = ImportWorker.for_source_repair(
        freqs / result.source_id / "source.txt",
        freqs,
        source_id=result.source_id,
        source_name=result.source_name,
        dicts_root=tmp_path / "dicts",
    )
    worker._runner(lambda *_a: None, lambda: False)

    provider = _slot(freqs, result.source_id)
    assert (provider.lookup("welt"), provider.lookup("weln")) == (2, None)


# --------------------------------------------------------------------------
# The shipped models: the words the review found filed under non-words
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def real_taggers():
    from anki_miner.languages.de.tokenizer import build_tagger as de_tagger
    from anki_miner.languages.fr.tokenizer import build_tagger as fr_tagger
    from anki_miner.languages.it.tokenizer import build_tagger as it_tagger

    return {"de": de_tagger(), "fr": fr_tagger(), "it": it_tagger()}


@pytest.mark.parametrize(
    ("code", "words", "rows"),
    [
        # de_core_news_sm alone: welt -> Weln, karte -> karn.
        ("de", ["welt", "karte"], [headword("Welt", "n"), headword("Karte", "n")]),
        # it_core_news_sm alone: ragazza -> ragazzare, torta -> tortare; AttestedLemmaPass needs the probe.
        ("it", ["ragazza", "torta"], [headword("ragazza", "n"), headword("torta", "n")]),
    ],
)
def test_real_models_key_a_common_noun_by_its_own_headword(tmp_path, monkeypatch, real_taggers, code, words, rows):
    monkeypatch.setitem(tagger_provider._TAGGERS, code, real_taggers[code])
    install_dictionary(tmp_path / "dicts", code, rows)
    fronts = [row[0] for row in rows]

    assert build_frequency_lemmatizer(code)(words) != fronts
    assert build_frequency_lemmatizer(code, tmp_path / "dicts")(words) == fronts


def test_real_fr_model_keeps_a_noun_it_tags_as_a_verb_alone(tmp_path, monkeypatch, real_taggers):
    """fr_core_news_sm alone tags ``gare``/``porte`` VERB with the surface as lemma; ``viens`` comes back ``vien``."""
    monkeypatch.setitem(tagger_provider._TAGGERS, "fr", real_taggers["fr"])
    install_dictionary(tmp_path / "dicts", "fr", FR_ROWS)

    lemmatize = build_frequency_lemmatizer("fr", tmp_path / "dicts")

    assert lemmatize(["gare", "porte", "viens"]) == ["gare", "porte", "venir"]
