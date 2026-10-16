"""F-16: which Commons file is the recording of a word (spec Stage W step 2, R29)."""

from __future__ import annotations

from anki_miner.services.wikimedia_audio_fetcher import canonical_file_name, match_commons_file, transcoded_mp3_url

ES = ("LL-Q1321 (spa)-", "Es-", "Es-am-lat-")
PL = ("Pl-", "LL-Q809 (pol)-")
RU = ("Ru-", "LL-Q7737 (rus)-")
NB = ("No-", "NB - Pronunciation of Norwegian Bokmål «{term}».ogg", "LL-Q25167 (nob)-", "LL-Q9043 (nor)-")
BOK = "NB - Pronunciation of Norwegian Bokmål «bok».ogg"
BOK2 = "NB - Pronunciation of Norwegian Bokmål «bok2».ogg"
BOKEN = "NB - Pronunciation of Norwegian Bokmål «boken».ogg"


def test_spanish_recordings_lead_the_lingua_libre_prefix():
    names = [
        "Pt-pt-casa.ogg",
        "LL-Q5146 (por)-Santamarcanda-casa.wav",
        "LL-Q1321 (spa)-Millars-casa.wav",
        "It-casa.ogg",
        "Es-am-lat-casa.ogg",
    ]

    assert match_commons_file(names, "casa", ES) == "LL-Q1321 (spa)-Millars-casa.wav"


def test_a_later_prefix_answers_when_the_first_has_nothing():
    assert match_commons_file(["Pt-pt-agua.ogg", "Es-am-lat-agua.ogg"], "agua", ES) == "Es-am-lat-agua.ogg"


def test_the_word_itself_beats_a_numbered_take():
    names = ["Pl-dom-2.ogg", "Pl-dom.ogg", "LL-Q809 (pol)-KaMan-dom.wav"]

    assert match_commons_file(names, "dom", PL) == "Pl-dom.ogg"
    assert match_commons_file(["De-Haus2.ogg"], "Haus", ("De-",)) == "De-Haus2.ogg"


def test_speakers_tie_alphabetically():
    names = ["LL-Q809 (pol)-Olaf-tak.wav", "LL-Q809 (pol)-Gower-tak.wav"]

    assert match_commons_file(names, "tak", PL) == "LL-Q809 (pol)-Gower-tak.wav"


def test_another_languages_file_never_matches():
    names = ["Cs-tak.ogg", "Sv-tak.ogg", "LL-Q9056 (ces)-Ghost4Man-tak.wav"]

    assert match_commons_file(names, "tak", PL) is None


def test_a_longer_word_with_the_same_ending_does_not_match():
    assert match_commons_file(["Pl-katak.ogg", "LL-Q809 (pol)-Olaf-katak.wav"], "tak", PL) is None


def test_a_legacy_prefix_takes_no_speaker_segment():
    assert match_commons_file(["En-us-ice-cream.ogg"], "cream", ("En-us-",)) is None


def test_non_audio_files_are_ignored():
    assert match_commons_file(["Pl-tak.svg", "Pl-tak.jpg"], "tak", PL) is None


def test_the_norwegian_template_matches_only_the_word():
    assert match_commons_file([BOKEN, BOK2, BOK], "bok", NB) == BOK
    assert match_commons_file([BOKEN, BOK2], "bok", NB) == BOK2
    assert match_commons_file([BOKEN], "bok", NB) is None


def test_russian_cyrillic_titles_match():
    names = ["Uk-вода.ogg", "Ru-вода.ogg", "LL-Q7737 (rus)-Tatiana Kerbush-вода.wav"]

    assert match_commons_file(names, "вода", RU) == "Ru-вода.ogg"


def test_no_prefixes_never_match():
    assert match_commons_file(["Pl-tak.ogg"], "tak", ()) is None


def test_lowercase_linked_titles_are_read_in_commons_form():
    assert canonical_file_name("File:pl-woda.ogg") == "Pl-woda.ogg"
    assert canonical_file_name("File:LL-Q809 (pol)-Olaf-tak.wav") == "LL-Q809 (pol)-Olaf-tak.wav"


def test_the_transcode_url_for_ogg_and_wav():
    commons = "https://upload.wikimedia.org/wikipedia/commons"

    assert transcoded_mp3_url(f"{commons}/c/c2/Sv-tak.ogg") == f"{commons}/transcoded/c/c2/Sv-tak.ogg/Sv-tak.ogg.mp3"
    wav = "LL-Q809_%28pol%29-Olaf-tak.wav"
    assert transcoded_mp3_url(f"{commons}/7/79/{wav}") == f"{commons}/transcoded/7/79/{wav}/{wav}.mp3"


def test_an_mp3_original_is_used_as_is_and_the_query_is_dropped():
    commons = "https://upload.wikimedia.org/wikipedia/commons"

    assert transcoded_mp3_url(f"{commons}/a/ab/Fr-eau.mp3?utm_source=en.wiktionary.org") == f"{commons}/a/ab/Fr-eau.mp3"
    assert (
        transcoded_mp3_url(f"{commons}/b/b0/En-us-tack.ogg?utm_source=en.wiktionary.org&utm_content=original")
        == f"{commons}/transcoded/b/b0/En-us-tack.ogg/En-us-tack.ogg.mp3"
    )


def test_a_url_outside_commons_has_no_transcode():
    assert transcoded_mp3_url("https://example.org/a/ab/Pl-tak.ogg") is None
