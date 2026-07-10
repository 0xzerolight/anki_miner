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

            content = conn.execute("SELECT content FROM entries WHERE term = ?", ("食べる",)).fetchone()[0]
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


KANA_ONLY_XML = """<?xml version="1.0" encoding="UTF-8"?>
<JMdict>
<entry>
<ent_seq>2000001</ent_seq>
<r_ele><reb>すごい</reb></r_ele>
<sense><gloss>amazing</gloss></sense>
</entry>
</JMdict>"""


HTML_GLOSS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<JMdict>
<entry>
<ent_seq>2000002</ent_seq>
<k_ele><keb>例</keb></k_ele>
<r_ele><reb>れい</reb></r_ele>
<sense><gloss>example &lt;text&gt;</gloss></sense>
</entry>
</JMdict>"""


def _make_xml_with_n_senses(n: int) -> str:
    senses = "".join(f"<sense><gloss>def{i}</gloss></sense>" for i in range(1, n + 1))
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<JMdict><entry><ent_seq>3000001</ent_seq>"
        "<k_ele><keb>多義</keb></k_ele>"
        "<r_ele><reb>たぎ</reb></r_ele>"
        f"{senses}"
        "</entry></JMdict>"
    )


class TestImportJmdictXmlEdgeCases:
    def test_kana_only_entry_produces_single_row(self, tmp_path: Path):
        xml = tmp_path / "JMdict_e"
        xml.write_text(KANA_ONLY_XML, encoding="utf-8")

        import_jmdict_xml(xml, tmp_path / "dicts")

        db = tmp_path / "dicts" / JMDICT_DICT_ID / "index.sqlite"
        conn = open_readonly(db)
        try:
            count = conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
            assert count == 1
            row = conn.execute("SELECT term, reading, content FROM entries").fetchone()
            assert row[0] == "すごい"
            assert row[1] == "すごい"
            assert "amazing" in row[2]
        finally:
            conn.close()

    def test_gloss_html_is_escaped(self, tmp_path: Path):
        xml = tmp_path / "JMdict_e"
        xml.write_text(HTML_GLOSS_XML, encoding="utf-8")

        import_jmdict_xml(xml, tmp_path / "dicts")

        db = tmp_path / "dicts" / JMDICT_DICT_ID / "index.sqlite"
        conn = open_readonly(db)
        try:
            content = conn.execute("SELECT content FROM entries WHERE term = ?", ("例",)).fetchone()[0]
            # Original raw text should be entity-escaped; '<text>' must not appear as a tag
            assert "&lt;text&gt;" in content
            assert "<text>" not in content
        finally:
            conn.close()

    def test_senses_capped_at_max(self, tmp_path: Path):
        from anki_miner.services.dictionary.importers.jmdict_importer import MAX_SENSES

        xml = tmp_path / "JMdict_e"
        xml.write_text(_make_xml_with_n_senses(MAX_SENSES + 3), encoding="utf-8")

        import_jmdict_xml(xml, tmp_path / "dicts")

        db = tmp_path / "dicts" / JMDICT_DICT_ID / "index.sqlite"
        conn = open_readonly(db)
        try:
            content = conn.execute("SELECT content FROM entries WHERE term = ?", ("多義",)).fetchone()[0]
            # Exactly MAX_SENSES <li> tags
            assert content.count("<li>") == MAX_SENSES
            assert f"def{MAX_SENSES}" in content
            assert f"def{MAX_SENSES + 1}" not in content
        finally:
            conn.close()

    def test_kanji_row_attests_every_unrestricted_reading(self, tmp_path: Path):
        """A kanji headword with multiple readings (no re_restr) yields one
        kanji-keyed row per reading, so a reading-scoped lookup of either
        reading attests against the kanji headword."""
        xml_text = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<JMdict><entry><ent_seq>4000001</ent_seq>"
            "<k_ele><keb>行く</keb></k_ele>"
            "<r_ele><reb>いく</reb></r_ele>"
            "<r_ele><reb>ゆく</reb></r_ele>"
            "<sense><gloss>to go</gloss></sense>"
            "</entry></JMdict>"
        )
        xml = tmp_path / "JMdict_e"
        xml.write_text(xml_text, encoding="utf-8")

        import_jmdict_xml(xml, tmp_path / "dicts")

        db = tmp_path / "dicts" / JMDICT_DICT_ID / "index.sqlite"
        conn = open_readonly(db)
        try:
            readings = {r[0] for r in conn.execute("SELECT reading FROM entries WHERE term = ?", ("行く",)).fetchall()}
            assert readings == {"いく", "ゆく"}
        finally:
            conn.close()

    def test_re_restr_pairs_reading_only_with_permitted_kanji(self, tmp_path: Path):
        """A restricted reading (re_restr) is paired only with its permitted
        kanji headwords; an unrestricted reading applies to all of them."""
        xml_text = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<JMdict><entry><ent_seq>4000002</ent_seq>"
            "<k_ele><keb>甲</keb></k_ele>"
            "<k_ele><keb>乙</keb></k_ele>"
            "<r_ele><reb>こう</reb><re_restr>甲</re_restr></r_ele>"
            "<r_ele><reb>おつ</reb><re_restr>乙</re_restr></r_ele>"
            "<r_ele><reb>きのえ</reb></r_ele>"
            "<sense><gloss>marker</gloss></sense>"
            "</entry></JMdict>"
        )
        xml = tmp_path / "JMdict_e"
        xml.write_text(xml_text, encoding="utf-8")

        import_jmdict_xml(xml, tmp_path / "dicts")

        db = tmp_path / "dicts" / JMDICT_DICT_ID / "index.sqlite"
        conn = open_readonly(db)
        try:
            kou = {r[0] for r in conn.execute("SELECT reading FROM entries WHERE term = ?", ("甲",)).fetchall()}
            otsu = {r[0] for r in conn.execute("SELECT reading FROM entries WHERE term = ?", ("乙",)).fetchall()}
            # Restricted reading stays with its kanji; unrestricted applies to all.
            assert kou == {"こう", "きのえ"}
            assert otsu == {"おつ", "きのえ"}
        finally:
            conn.close()

    def test_always_overwrites_existing_index(self, tmp_path: Path):
        """Second import replaces the first; only the latest content is queryable."""
        xml1 = tmp_path / "first.xml"
        xml1.write_text(MINI_JMDICT_XML, encoding="utf-8")
        xml2 = tmp_path / "second.xml"
        xml2.write_text(KANA_ONLY_XML, encoding="utf-8")

        dest = tmp_path / "dicts"
        import_jmdict_xml(xml1, dest)
        import_jmdict_xml(xml2, dest)

        db = dest / JMDICT_DICT_ID / "index.sqlite"
        conn = open_readonly(db)
        try:
            # Only the second import's row should be present (1 kana-only entry)
            count = conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
            assert count == 1
            row = conn.execute("SELECT term FROM entries").fetchone()
            assert row[0] == "すごい"
        finally:
            conn.close()
