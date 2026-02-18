"""Export service for vocabulary data in various formats."""

import csv
from pathlib import Path

from anki_miner.config import AnkiMinerConfig
from anki_miner.models.word import WordData


class ExportService:
    """Export vocabulary data to CSV, TSV, and vocabulary list formats."""

    def __init__(self, config: AnkiMinerConfig):
        self.config = config

    def export_csv(
        self,
        words: list[WordData],
        output_path: Path,
        include_media_refs: bool = False,
    ) -> int:
        """Export words to CSV format with all available fields.

        Args:
            words: List of WordData to export
            output_path: Path for the output CSV file
            include_media_refs: Whether to include screenshot/audio filename columns

        Returns:
            Number of rows written (excluding header)
        """
        return self._write_delimited(words, output_path, ",", include_media_refs)

    def export_tsv(self, words: list[WordData], output_path: Path) -> int:
        """Export words to TSV format with all available fields.

        Args:
            words: List of WordData to export
            output_path: Path for the output TSV file

        Returns:
            Number of rows written (excluding header)
        """
        return self._write_delimited(words, output_path, "\t", include_media_refs=True)

    def export_vocab_list(
        self,
        words: list[WordData],
        output_path: Path,
        fmt: str = "plain",
    ) -> int:
        """Export a deduplicated vocabulary list.

        Args:
            words: List of WordData to export
            output_path: Path for the output text file
            format: One of "plain", "takoboto", or "jpdb"

        Returns:
            Number of unique words written
        """
        # Deduplicate by lemma, preserving order
        seen: dict[str, WordData] = {}
        for w in words:
            if w.word.lemma not in seen:
                seen[w.word.lemma] = w

        lines: list[str] = []
        for lemma, word_data in seen.items():
            if format == "takoboto":
                lines.append(f"{lemma}\t{word_data.word.reading}")
            else:
                # "plain" and "jpdb" are both one lemma per line
                lines.append(lemma)

        output_path.write_text("\n".join(lines) + "\n" if lines else "", encoding="utf-8")
        return len(lines)

    def _write_delimited(
        self,
        words: list[WordData],
        output_path: Path,
        delimiter: str,
        include_media_refs: bool,
    ) -> int:
        """Write words to a delimited file (CSV or TSV).

        Args:
            words: List of WordData to export
            output_path: Path for the output file
            delimiter: Field delimiter ("," for CSV, "\\t" for TSV)
            include_media_refs: Whether to include screenshot/audio columns

        Returns:
            Number of data rows written (excluding header)
        """
        header = [
            "Lemma",
            "Surface",
            "Reading",
            "Sentence",
            "Definition",
            "Expression Furigana",
            "Sentence Furigana",
            "Pitch Accent",
            "Frequency Rank",
        ]
        if include_media_refs:
            header.extend(["Screenshot", "Audio"])
        header.extend(["Start Time", "End Time"])

        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f, delimiter=delimiter)
            writer.writerow(header)

            for w in words:
                row = [
                    w.word.lemma,
                    w.word.surface,
                    w.word.reading,
                    w.word.sentence,
                    w.definition or "",
                    w.word.expression_furigana,
                    w.word.sentence_furigana,
                    w.pitch_accent or "",
                    str(w.frequency_rank) if w.frequency_rank is not None else "",
                ]
                if include_media_refs:
                    row.extend(
                        [
                            w.screenshot_filename or "",
                            w.audio_filename or "",
                        ]
                    )
                row.extend(
                    [
                        f"{w.word.start_time:.2f}",
                        f"{w.word.end_time:.2f}",
                    ]
                )
                writer.writerow(row)

        return len(words)
