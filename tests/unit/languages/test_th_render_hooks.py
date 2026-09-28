"""th render hooks: Paiboon reading and classifier out of dictionary HTML."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.th.render import TH_RENDER_HOOKS, ThaiClassifierHook, ThaiPaiboonHook

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "th"
WTY = json.loads((FIXTURES / "wty_grammar_rows.json").read_text(encoding="utf-8"))
VOL = json.loads((FIXTURES / "volubilis_rows.json").read_text(encoding="utf-8"))

CONFIG = AnkiMinerConfig()

_THAI_FIRST = "\N{THAI CHARACTER KO KAI}"
_THAI_LAST = "\N{THAI CHARACTER KHOMUT}"


def _word(html: str, mined_form: str = "หมา"):
    return SimpleNamespace(definition_html=html, mined_form=mined_form)


def test_hooks_declare_their_field_keys():
    assert [key for hook in TH_RENDER_HOOKS for key in hook.field_names()] == ["reading_paiboon", "classifier"]


def test_paiboon_from_the_wty_grammar_bullet():
    html = '<div class="gloss-sc-div" data-sc-content="Grammar-content">หมา • (mǎa) (classifier ตัว)</div>'
    assert ThaiPaiboonHook().render(_word(html), config=CONFIG) == {"reading_paiboon": "mǎa"}


def test_paiboon_from_the_volubilis_bracket():
    html = '<div class="gloss-content">[mǎa] n. classifier: ตัว<br>dog, bitch, hound</div>'
    assert ThaiPaiboonHook().render(_word(html), config=CONFIG) == {"reading_paiboon": "mǎa"}


def test_volubilis_variant_inside_the_bracket_is_kept_verbatim():
    html = '<div class="gloss-content">[náam (=-nám)] n.<br>water</div>'
    assert ThaiPaiboonHook().render(_word(html, "น้ำ"), config=CONFIG) == {"reading_paiboon": "náam (=-nám)"}


def test_no_dictionary_hit_leaves_the_field_blank():
    assert ThaiPaiboonHook().render(_word(""), config=CONFIG) == {}
    assert ThaiPaiboonHook().render(_word("<div>no reading here</div>"), config=CONFIG) == {}


def test_classifier_from_wty_single_and_alternatives():
    single = '<div data-sc-content="Grammar-content">หมา • (mǎa) (classifier ตัว)</div>'
    assert ThaiClassifierHook().render(_word(single), config=CONFIG) == {"classifier": "ตัว"}
    several = '<div data-sc-content="Grammar-content">สตรี (classifier คน or นาง)</div>'
    assert ThaiClassifierHook().render(_word(several), config=CONFIG) == {"classifier": "คน / นาง"}


def test_classifier_from_volubilis():
    html = '<div class="gloss-content">[mǎa] n. classifier: ตัว<br>dog</div>'
    assert ThaiClassifierHook().render(_word(html), config=CONFIG) == {"classifier": "ตัว"}


_BACKLINK = (
    '<div class="gloss-sc-div" data-sc-content="backlink"><a class="gloss-sc-a" href="https://en.wiktionary.org/">'
    'Wiktionary</a> | <a class="gloss-sc-a" href="https://kaikki.org/">Kaikki</a></div>'
)


@pytest.mark.parametrize(
    ("gloss", "expected"),
    [
        # ฟุตบอล: the colon form ran past its own ')' into the backlink.
        ("the inflated ball used in such a game (classifier: ลูก (lûuk))", "ลูก"),
        # บอล: readings go, both classifiers stay.
        ("ball (object) (classifier: ลูก (lûuk) or ใบ (bai))", "ลูก / ใบ"),
        # รถไฟ: English notes between the forms go.
        (
            "(classifier: คัน or ตู้ for separate sections, as cars, carriages, coaches, etc.; "
            "ขบวน for lines or processions) train: connected group of rolling stock",
            "คัน / ตู้ / ขบวน",
        ),
        ("(classifier ข้าง for a single one or คู่ for a pair) ear.", "ข้าง / คู่"),
        ("(classifiers: แคร่, คัน, หลัง) bogie.", "แคร่ / คัน / หลัง"),
        ("needle (classifier: เล่ม)</div></li><li><div>stitch", "เล่ม"),
    ],
)
def test_a_wty_classifier_holds_thai_forms_only(gloss, expected):
    html = f'<ol><li class="gloss-sc-li"><div class="gloss-sc-div">{gloss}</div></li></ol>{_BACKLINK}'
    assert ThaiClassifierHook().render(_word(html), config=CONFIG) == {"classifier": expected}


def test_a_classifier_note_with_no_thai_form_is_no_field():
    html = f'<div class="gloss-sc-div">(classifier for long, thin objects) line</div>{_BACKLINK}'
    assert ThaiClassifierHook().render(_word(html, "เส้น"), config=CONFIG) == {}


def test_a_later_classifier_group_is_read_past_an_empty_one():
    html = "From Proto-Tai (classifier for things).<br>item; piece (classifier อัน)"
    assert ThaiClassifierHook().render(_word(html, "อัน"), config=CONFIG) == {"classifier": "อัน"}


def test_an_abstract_noun_line_is_not_a_classifier():
    html = '<div data-sc-content="Grammar-content">พัฒนา (abstract noun การพัฒนา)</div>'
    assert ThaiClassifierHook().render(_word(html, "พัฒนา"), config=CONFIG) == {}


@pytest.mark.parametrize("line", WTY, ids=range(len(WTY)))
def test_no_wty_grammar_line_yields_a_reading_containing_thai(line):
    out = ThaiPaiboonHook().render(_word(line), config=CONFIG)
    reading = out.get("reading_paiboon", "")
    assert not any(_THAI_FIRST <= char <= _THAI_LAST for char in reading), line


@pytest.mark.parametrize("line", VOL, ids=range(len(VOL)))
def test_every_volubilis_row_yields_a_reading(line):
    assert ThaiPaiboonHook().render(_word(line), config=CONFIG).get("reading_paiboon")
