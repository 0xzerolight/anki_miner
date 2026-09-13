"""en_core_web_sm is MIT: its notice travels with the app that delivers the model (the ca notice shape)."""

from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
LICENSE_DIR = ROOT / "licenses" / "en_core_web_sm"
MIT_SHA256 = "3933c176979b68bc6d0bcc902c7d6c130f1d127f476f17ba5cdba8d99cfd0012"


def test_the_notice_carries_the_mit_text_and_the_source_pointer():
    text = (LICENSE_DIR / "LICENSE").read_bytes()
    assert hashlib.sha256(text).hexdigest() == MIT_SHA256
    sources = (LICENSE_DIR / "SOURCES.txt").read_text(encoding="utf-8")
    assert "https://github.com/explosion/spacy-models/releases/tag/en_core_web_sm-3.8.0" in sources
    assert "OntoNotes 5" in sources and "anki_miner/languages/en/pack.py" in sources
    assert "LICENSES_SOURCES" in sources  # a pointer to the wheel's own licence texts, not a copy
    assert sorted(path.name for path in LICENSE_DIR.iterdir()) == ["LICENSE", "README.md", "SOURCES.txt"]
    assert "MIT" in (LICENSE_DIR / "README.md").read_text(encoding="utf-8")


def test_the_text_is_the_one_inside_the_model_wheel():
    import en_core_web_sm

    shipped = Path(en_core_web_sm.__file__).parent / "en_core_web_sm-3.8.0" / "LICENSE"
    assert shipped.read_bytes() == (LICENSE_DIR / "LICENSE").read_bytes()


def test_the_spec_ships_the_notice_and_excludes_the_model():
    spec = (ROOT / "anki_miner.spec").read_text(encoding="utf-8")
    assert '"licenses", "en_core_web_sm"' in spec
    assert "+ en_model_license_datas" in spec
    _head, _, excludes = spec.partition("excludes=[")
    assert '"en_core_web_sm",' in excludes
