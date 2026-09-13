"""fr_core_news_sm is LGPL-LR: its notice travels with the app that delivers the model (kiwipiepy/ca precedent)."""

from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
LICENSE_DIR = ROOT / "licenses" / "fr_core_news_sm"
LGPL_LR_SHA256 = "fcfeb5f3a67aa25d659d1162b4f4a9c52a92171d4df68a11d87c52e3af982a17"


def test_the_notice_carries_the_licence_text_and_the_source_pointer():
    licence = (LICENSE_DIR / "LICENSE").read_bytes()
    assert hashlib.sha256(licence).hexdigest() == LGPL_LR_SHA256
    assert licence.lstrip().startswith(b"Lesser General Public License For Linguistic Resources")
    sources = (LICENSE_DIR / "SOURCES.txt").read_text(encoding="utf-8")
    assert "https://github.com/explosion/spacy-models/releases/tag/fr_core_news_sm-3.8.0" in sources
    assert "UD_French-Sequoia" in sources and "anki_miner/languages/fr/pack.py" in sources
    assert "LICENSES_SOURCES" in sources  # a pointer to the wheel's own file, not a copy
    assert sorted(path.name for path in LICENSE_DIR.iterdir()) == ["LICENSE", "README.md", "SOURCES.txt"]
    assert "LGPL-LR" in (LICENSE_DIR / "README.md").read_text(encoding="utf-8")


def test_the_text_is_the_one_inside_the_model_wheel():
    import fr_core_news_sm

    shipped = Path(fr_core_news_sm.__file__).parent / "fr_core_news_sm-3.8.0" / "LICENSE"
    assert shipped.read_bytes() == (LICENSE_DIR / "LICENSE").read_bytes()


def test_the_spec_ships_the_notice_and_excludes_the_model():
    spec = (ROOT / "anki_miner.spec").read_text(encoding="utf-8")
    assert '"licenses", "fr_core_news_sm"' in spec
    assert "+ fr_model_license_datas" in spec
    _head, _, excludes = spec.partition("excludes=[")
    assert '"fr_core_news_sm",' in excludes
