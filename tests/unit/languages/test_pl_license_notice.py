"""pl_core_news_sm is GPL-3.0: its notice travels with the app that delivers the model (kiwipiepy/ca precedent)."""

from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
LICENSE_DIR = ROOT / "licenses" / "pl_core_news_sm"
GPL3_SHA256 = "3972dc9744f6499f0f9b2dbf76696f2ae7ad8af9b23dde66d6af86c9dfb36986"


def test_the_notice_carries_the_gpl_text_and_the_source_pointer():
    copying = (LICENSE_DIR / "COPYING.GPLv3").read_bytes()
    assert hashlib.sha256(copying).hexdigest() == GPL3_SHA256
    sources = (LICENSE_DIR / "SOURCES.txt").read_text(encoding="utf-8")
    assert "https://github.com/explosion/spacy-models/releases/tag/pl_core_news_sm-3.8.0" in sources
    assert "UD_Polish-PDB" in sources and "nkjp.pl" in sources and "anki_miner/languages/pl/pack.py" in sources
    assert "LICENSES_SOURCES" in sources  # a pointer to the wheel's own full texts, not a copy
    assert sorted(path.name for path in LICENSE_DIR.iterdir()) == ["COPYING.GPLv3", "README.md", "SOURCES.txt"]
    assert "GPL-3.0" in (LICENSE_DIR / "README.md").read_text(encoding="utf-8")


def test_the_text_is_the_one_inside_the_model_wheel():
    import pl_core_news_sm

    shipped = Path(pl_core_news_sm.__file__).parent / "pl_core_news_sm-3.8.0" / "LICENSE"
    assert shipped.read_bytes() == (LICENSE_DIR / "COPYING.GPLv3").read_bytes()


def test_the_spec_ships_the_notice_and_excludes_the_model():
    spec = (ROOT / "anki_miner.spec").read_text(encoding="utf-8")
    assert '"licenses", "pl_core_news_sm"' in spec
    assert "+ pl_core_news_sm_license_datas" in spec
    _head, _, excludes = spec.partition("excludes=[")
    assert '"pl_core_news_sm",' in excludes
