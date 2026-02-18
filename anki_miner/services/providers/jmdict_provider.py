"""JMdict offline dictionary provider."""

import logging
import xml.etree.ElementTree as ET
from pathlib import Path

from anki_miner.exceptions import SetupError

logger = logging.getLogger(__name__)


class JMdictProvider:
    """Offline dictionary provider using JMdict XML file.

    Implements DictionaryProvider protocol.
    """

    def __init__(self, jmdict_path: Path):
        """Initialize with path to JMdict XML file.

        Args:
            jmdict_path: Path to the JMdict XML file.
        """
        self._path = jmdict_path
        self._dictionary: dict[str, list[str]] | None = None

    @property
    def name(self) -> str:
        return "JMdict Offline"

    def is_available(self) -> bool:
        return self._dictionary is not None

    def load(self) -> bool:
        """Load and parse the JMdict XML file.

        Returns:
            True if loaded successfully.

        Raises:
            SetupError: If file not found or XML parse error.
        """
        if not self._path.exists():
            raise SetupError(
                f"JMdict file not found at: {self._path}. "
                f"Download from http://ftp.edrdg.org/pub/Nihongo/ and decompress with: gunzip JMdict_e.gz"
            )

        dictionary: dict[str, list[str]] = {}

        try:
            tree = ET.parse(str(self._path))
            root = tree.getroot()

            for entry in root.findall("entry"):
                # Extract all readings (kanji and kana)
                readings = []

                for k_ele in entry.findall("k_ele"):
                    keb = k_ele.find("keb")
                    if keb is not None and keb.text:
                        readings.append(keb.text)

                for r_ele in entry.findall("r_ele"):
                    reb = r_ele.find("reb")
                    if reb is not None and reb.text:
                        readings.append(reb.text)

                # Extract definitions (senses)
                definitions = []
                for sense in entry.findall("sense"):
                    glosses = [g.text for g in sense.findall("gloss") if g.text]
                    if glosses:
                        definitions.append("; ".join(glosses))

                # Store all readings pointing to same definitions
                if definitions and readings:
                    for reading in readings:
                        if reading not in dictionary:
                            dictionary[reading] = definitions

            self._dictionary = dictionary
            return True

        except ET.ParseError as e:
            raise SetupError(f"Error parsing JMdict XML: {e}") from e
        except Exception as e:
            raise SetupError(f"Error loading JMdict: {e}") from e

    def lookup(self, word: str) -> str | None:
        """Look up word in the loaded JMdict dictionary.

        Args:
            word: Japanese word to look up.

        Returns:
            HTML-formatted definition, or None.
        """
        if not self._dictionary:
            return None

        definitions = self._dictionary.get(word)
        if not definitions:
            return None

        # Format as numbered list (max 5 definitions)
        formatted = [f"{i}. {defn}" for i, defn in enumerate(definitions[:5], 1)]
        return "<br>".join(formatted)
