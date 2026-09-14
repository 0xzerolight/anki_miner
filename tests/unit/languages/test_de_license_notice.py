"""de_core_news_sm is MIT: its notice travels with the app that delivers the model (the en notice shape)."""

from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
LICENSE_DIR = ROOT / "licenses" / "de_core_news_sm"
MIT_SHA256 = "3933c176979b68bc6d0bcc902c7d6c130f1d127f476f17ba5cdba8d99cfd0012"


def test_the_notice_carries_the_mit_text_and_the_source_pointer():
    text = (LICENSE_DIR / "LICENSE").read_bytes()
    assert hashlib.sha256(text).hexdigest() == MIT_SHA256
    sources = (LICENSE_DIR / "SOURCES.txt").read_text(encoding="utf-8")
    assert "https://github.com/explosion/spacy-models/releases/tag/de_core_news_sm-3.8.0" in sources
    assert "TIGER Corpus" in sources and "anki_miner/languages/de/pack.py" in sources
    assert "LICENSES_SOURCES" in sources  # a pointer to the wheel's own licence texts, not a copy
    assert sorted(path.name for path in LICENSE_DIR.iterdir()) == ["LICENSE", "README.md", "SOURCES.txt"]
    assert "MIT" in (LICENSE_DIR / "README.md").read_text(encoding="utf-8")


def test_the_text_is_the_one_inside_the_model_wheel():
    import de_core_news_sm

    shipped = Path(de_core_news_sm.__file__).parent / "de_core_news_sm-3.8.0" / "LICENSE"
    assert shipped.read_bytes() == (LICENSE_DIR / "LICENSE").read_bytes()


def test_the_spec_ships_the_notice_and_excludes_the_model():
    spec = (ROOT / "anki_miner.spec").read_text(encoding="utf-8")
    assert '"licenses", "de_core_news_sm"' in spec
    assert "+ de_model_license_datas" in spec
    _head, _, excludes = spec.partition("excludes=[")
    assert '"de_core_news_sm",' in excludes
