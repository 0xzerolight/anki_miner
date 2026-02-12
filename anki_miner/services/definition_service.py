"""Service for fetching word definitions from JMdict and Jisho API."""

import time
import xml.etree.ElementTree as ET

import requests

from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions import SetupError
from anki_miner.interfaces import ProgressCallback


class DefinitionService:
    """Fetch word definitions from offline dictionary or online API (stateless service)."""

    def __init__(self, config: AnkiMinerConfig):
        """Initialize the definition service.

        Args:
            config: Configuration for definition lookup
        """
        self.config = config
        self._jmdict: dict[str, list[str]] | None = None

    def load_offline_dictionary(self) -> bool:
        """Load JMdict XML dictionary into memory.

        Returns:
            True if loaded successfully, False otherwise

        Raises:
            SetupError: If dictionary file is missing or cannot be parsed
        """
        if not self.config.use_offline_dict:
            return False

        if not self.config.jmdict_path.exists():
            raise SetupError(
                f"JMdict file not found at: {self.config.jmdict_path}. "
                f"Download from http://ftp.edrdg.org/pub/Nihongo/ and decompress with: gunzip JMdict_e.gz"
            )

        dictionary = {}

        try:
            tree = ET.parse(str(self.config.jmdict_path))
            root = tree.getroot()

            entry_count = 0
            for entry in root.findall("entry"):
                # Extract all readings (kanji and kana)
                readings = []

                # Kanji writings
                for k_ele in entry.findall("k_ele"):
                    keb = k_ele.find("keb")
                    if keb is not None and keb.text:
                        readings.append(keb.text)

                # Kana readings
                for r_ele in entry.findall("r_ele"):
                    reb = r_ele.find("reb")
                    if reb is not None and reb.text:
                        readings.append(reb.text)

                # Extract definitions (senses)
                definitions = []
                for sense in entry.findall("sense"):
                    glosses = []
                    for gloss in sense.findall("gloss"):
                        if gloss.text:
                            glosses.append(gloss.text)

                    if glosses:
                        definitions.append("; ".join(glosses))

                # Store all readings pointing to same definitions
                if definitions and readings:
                    entry_count += 1
                    for reading in readings:
                        if reading not in dictionary:
                            dictionary[reading] = definitions

            self._jmdict = dictionary
            return True

        except ET.ParseError as e:
            raise SetupError(f"Error parsing JMdict XML: {e}") from e
        except Exception as e:
            raise SetupError(f"Error loading JMdict: {e}") from e

    def get_definition(self, word: str) -> str | None:
        """Get definition for a word (offline first, then API).

        Args:
            word: Japanese word to look up

        Returns:
            HTML-formatted definition string, or None if not found
        """
        # Try offline dictionary first
        if self._jmdict:
            offline_def = self._get_definition_offline(word)
            if offline_def:
                return offline_def

        # Fallback to Jisho API
        if not self.config.use_offline_dict or self._jmdict is None:
            return self._get_definition_jisho(word)

        return None

    def get_definitions_batch(
        self,
        words: list[str],
        progress_callback: ProgressCallback | None = None,
    ) -> list[str | None]:
        """Get definitions for multiple words.

        Args:
            words: List of words to look up
            progress_callback: Optional callback for progress reporting

        Returns:
            List of definitions (matching order of input words)
        """
        if progress_callback:
            progress_callback.on_start(len(words), "Fetching definitions")

        definitions = []
        for i, word in enumerate(words, 1):
            definition = self.get_definition(word)
            definitions.append(definition)

            if progress_callback:
                if definition:
                    progress_callback.on_progress(i, f"Definition found: {word}")
                else:
                    progress_callback.on_progress(i, f"No definition: {word}")

        if progress_callback:
            progress_callback.on_complete()

        return definitions

    def _get_definition_offline(self, word: str) -> str | None:
        """Look up word definition from local JMdict.

        Args:
            word: Japanese word to look up

        Returns:
            HTML-formatted definition string, or None if not found
        """
        if not self._jmdict:
            return None

        definitions = self._jmdict.get(word, None)

        if not definitions:
            return None

        # Format as numbered list (matching Jisho format)
        # Limit to first 5 definitions
        formatted = []
        for i, defn in enumerate(definitions[:5], 1):
            formatted.append(f"{i}. {defn}")

        return "<br>".join(formatted)

    def _get_definition_jisho(self, word: str, apply_delay: bool = True) -> str | None:
        """Fetch definition from Jisho API.

        Args:
            word: Japanese word to look up
            apply_delay: Whether to apply rate limiting delay

        Returns:
            HTML-formatted definition string, or None if not found
        """
        if apply_delay:
            time.sleep(self.config.jisho_delay)

        try:
            response = requests.get(
                self.config.jisho_api_url,
                params={"keyword": word},
                timeout=10,
            )

            if response.status_code != 200:
                return None

            data = response.json()
            results = data.get("data", [])

            if not results:
                return None

            first = results[0]

            # Build definition HTML
            definitions = []
            for i, sense in enumerate(first.get("senses", [])[:5], 1):
                eng = sense.get("english_definitions", [])

                if eng:
                    def_text = f"{i}. {'; '.join(eng)}"
                    definitions.append(def_text)

            if not definitions:
                return None

            return "<br>".join(definitions)

        except requests.exceptions.Timeout:
            return None
        except (requests.RequestException, ValueError, KeyError):
            return None
