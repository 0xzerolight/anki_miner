"""Tests for the JMdict XML importer."""

from pathlib import Path

import pytest

from anki_miner.exceptions import SetupError
from anki_miner.services.dictionary.importers.jmdict_importer import (
    JMDICT_DICT_ID,
    JMdictImportResult,
    import_jmdict_xml,
)
from anki_miner.services.dictionary.storage import open_readonly, read_meta

MINI_JMDICT_XML = """<?xml version="1.0" encoding="UTF-8"?>
<JMdict>
<entry>
<ent_seq>1000001</ent_seq>
<k_ele><keb>食べる</keb></k_ele>
<r_ele><reb>たべる</reb></r_ele>
<sense><gloss>to eat</gloss></sense>
<sense><gloss>to live on</gloss><gloss>to survive</gloss></sense>
</entry>
<entry>
<ent_seq>1000002</ent_seq>
<k_ele><keb>飲む</keb></k_ele>
<r_ele><reb>のむ</reb></r_ele>
<sense><gloss>to drink</gloss></sense>
</entry>
</JMdict>"""


class TestImportJmdictXml:
    def test_import_creates_rows_for_each_reading(self, tmp_path: Path):
        xml = tmp_path / "JMdict_e"
        xml.write_text(MINI_JMDICT_XML, encoding="utf-8")

        result = import_jmdict_xml(xml, tmp_path / "dicts")

        assert isinstance(result, JMdictImportResult)
        assert result.dict_id == JMDICT_DICT_ID

        db = tmp_path / "dicts" / JMDICT_DICT_ID / "index.sqlite"
        conn = open_readonly(db)
        try:
            # Two entries, two readings each (kanji + kana) = 4 rows
            count = conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
            assert count == 4

            content = conn.execute(
                "SELECT content FROM entries WHERE term = ?", ("食べる",)
            ).fetchone()[0]
            assert "to eat" in content
            assert "<ol>" in content
        finally:
            conn.close()

        meta = read_meta(db)
        assert meta["format"] == "jmdict"
        assert meta["entry_count"] == "4"

    def test_missing_file_raises_setup_error(self, tmp_path: Path):
        with pytest.raises(SetupError, match="not found"):
            import_jmdict_xml(tmp_path / "missing", tmp_path / "dicts")

    def test_invalid_xml_raises_setup_error(self, tmp_path: Path):
        xml = tmp_path / "broken.xml"
        xml.write_text("<not-valid", encoding="utf-8")
        with pytest.raises(SetupError):
            import_jmdict_xml(xml, tmp_path / "dicts")
