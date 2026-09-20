"""zeyrek behind the deterministic analyzer (Stage F spike; plan decisions 1-2), through the real engine."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import sys
import time
import tomllib
from pathlib import Path

import pytest

from anki_miner.languages.tr.analyzer import TurkishAnalyzer
from anki_miner.languages.tr.morphology import TrAnalysis

ROOT = Path(__file__).resolve().parents[3]
ZEYREK_LICENSE_SHA256 = "ee3498eecc1e397dcc1cb85a3958d64120d56a05cf33ef9604c79c9fe4dbe28a"


@pytest.fixture(scope="module")
def analyzer():
    return TurkishAnalyzer()


def test_a_word_parses_the_same_every_time(analyzer):
    """Stock zeyrek: one analysis for gecikecek, then none - advance() mutated the stem's attribute set."""
    first = analyzer.analyse("gecikecek")
    assert first == [TrAnalysis("gecikmek", "VERB")]
    assert analyzer.analyse("gecikecek") == first == analyzer.analyse("gecikecek")


def test_parsing_logs_and_prints_nothing(analyzer, caplog, capfd):
    caplog.set_level(logging.DEBUG)
    analyzer.analyse("kitapları")
    assert not [record for record in caplog.records if record.name.startswith("zeyrek")]
    out, err = capfd.readouterr()
    assert "APPENDING RESULT" not in out + err


@pytest.mark.parametrize(
    ("word", "first"),
    [
        ("kitapları", TrAnalysis("kitap", "NOUN")),
        ("okudu", TrAnalysis("okumak", "VERB")),
        ("bana", TrAnalysis("ben", "PRON", "Pers")),
        ("Sana", TrAnalysis("sen", "PRON", "Pers")),
        ("Kitabın", TrAnalysis("kitap", "NOUN")),
        ("ilacını", TrAnalysis("ilaç", "NOUN")),
        ("kâğıdı", TrAnalysis("kâğıt", "NOUN")),
        ("yıka", TrAnalysis("yıkamak", "VERB")),
        ("biliyorum", TrAnalysis("bilmek", "VERB")),
        ("ederim", TrAnalysis("etmek", "VERB")),
        ("kız", TrAnalysis("kız", "NOUN")),
        ("aman", TrAnalysis("aman", "INTJ")),
        ("mi", TrAnalysis("mi", "PART")),
        ("müdür", TrAnalysis("müdür", "NOUN")),
        ("Ahmet", TrAnalysis("Ahmet", "PROPN", "Prop")),
        ("vb", TrAnalysis("vb", "X", "Abbrv")),
    ],
)
def test_the_card_front_reading_comes_first(analyzer, word, first):
    assert analyzer.analyse(word)[0] == first


def test_every_reading_is_listed_once(analyzer):
    assert analyzer.analyse("bana") == [
        TrAnalysis("ben", "PRON", "Pers"),
        TrAnalysis("ban", "NOUN"),
        TrAnalysis("banmak", "VERB"),
        TrAnalysis("Ba", "PROPN", "Prop"),
    ]


def test_unanalysable_words_come_back_empty_and_fast(analyzer):
    start = time.perf_counter()
    assert analyzer.analyse("evlerimizdekilerinkilerdenmişsinizcesine") == []
    assert analyzer.analyse("lalalalalalalalalalalalala") == []
    assert time.perf_counter() - start < 1.0


_SEED_PROBE = (
    "import hashlib, json, sys\n"
    "from pathlib import Path\n"
    "import zeyrek\n"
    "from anki_miner.languages.tr.analyzer import TurkishAnalyzer\n"
    "analyzer = TurkishAnalyzer()\n"
    "words = Path(zeyrek.__file__).parent.joinpath('resources', 'tr', 'first-10K').read_text('utf-8').split()\n"
    "digest = hashlib.sha256()\n"
    "for word in words:\n"
    "    readings = [[r.lemma, r.pos1, r.pos2] for r in analyzer.analyse(word)]\n"
    "    digest.update((word + json.dumps(readings, ensure_ascii=False)).encode())\n"
    "probes = {w: [[r.lemma, r.pos1, r.pos2] for r in analyzer.analyse(w)] for w in sys.argv[1:]}\n"
    "print(json.dumps({'digest': digest.hexdigest(), 'probes': probes}, ensure_ascii=False, sort_keys=True))\n"
)


def _under_seed(seed: str, words: list[str]) -> dict:
    run = subprocess.run(
        [sys.executable, "-c", _SEED_PROBE, *words],
        env={**os.environ, "PYTHONHASHSEED": seed},
        capture_output=True,
        text=True,
        check=True,
        cwd=ROOT,
    )
    return json.loads(run.stdout.strip().splitlines()[-1])


def test_the_lexicon_does_not_depend_on_the_hash_seed():
    """Three seed dependences, all fixed (plan decision 1).

    Stock zeyrek under ``PYTHONHASHSEED=2`` loses every ``ad`` reading of adı/adım (the lru_cache'd set), and
    applies a root's ``RootAttribute``s in set-iteration order, so ``reddi`` kept both roots under seed 0 but
    only ``redd`` under seed 2, while ``tıbbı`` and ``zıddı`` lost every reading. The digest covers zeyrek's own
    9,984-word ``first-10K`` list, so the check is the whole lexicon, not the probe words.
    """
    words = ["adı", "adım", "gözlerin", "reddi", "tıbbı", "zıddı"]
    runs = [_under_seed(seed, words) for seed in ("0", "2")]
    assert runs[0]["digest"] == runs[1]["digest"]
    probes = runs[0]["probes"]
    assert ["ad", "NOUN", ""] in probes["adı"]
    assert probes["reddi"][0] == ["ret", "NOUN", ""]
    assert probes["tıbbı"][0] == ["tıp", "NOUN", ""]
    assert probes["zıddı"][0] == ["zıt", "NOUN", ""]


def test_the_port_ships_zeyreks_mit_notice():
    notice = ROOT / "licenses" / "zeyrek"
    assert sorted(path.name for path in notice.iterdir()) == ["LICENSE", "README.md"]
    assert hashlib.sha256((notice / "LICENSE").read_bytes()).hexdigest() == ZEYREK_LICENSE_SHA256
    readme = (notice / "README.md").read_text(encoding="utf-8")
    assert "anki_miner/languages/tr/analyzer.py" in readme and "Olga Bulat" in readme
    spec = (ROOT / "anki_miner.spec").read_text(encoding="utf-8")
    assert '"licenses", "zeyrek"' in spec and "+ zeyrek_license_datas" in spec
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert "licenses/zeyrek/LICENSE" in project["license-files"]
    assert '"licenses/zeyrek/LICENSE",' in (ROOT / "scripts" / "check_wheel_assets.py").read_text(encoding="utf-8")
