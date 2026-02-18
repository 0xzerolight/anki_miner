"""Jisho API dictionary provider."""

import logging
import time

import requests

logger = logging.getLogger(__name__)


class JishoProvider:
    """Online dictionary provider using Jisho.org API.

    Implements DictionaryProvider protocol.
    """

    def __init__(
        self,
        api_url: str = "https://jisho.org/api/v1/search/words",
        delay: float = 0.5,
    ):
        """Initialize with API URL and rate-limiting delay.

        Args:
            api_url: Jisho API endpoint URL.
            delay: Seconds to wait between API calls.
        """
        self._api_url = api_url
        self._delay = delay

    @property
    def name(self) -> str:
        return "Jisho API"

    def is_available(self) -> bool:
        return True

    def load(self) -> bool:
        return True

    def lookup(self, word: str) -> str | None:
        """Look up word via Jisho API.

        Args:
            word: Japanese word to look up.

        Returns:
            HTML-formatted definition, or None.
        """
        time.sleep(self._delay)

        try:
            response = requests.get(
                self._api_url,
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
            definitions = []
            for i, sense in enumerate(first.get("senses", [])[:5], 1):
                eng = sense.get("english_definitions", [])
                if eng:
                    definitions.append(f"{i}. {'; '.join(eng)}")

            return "<br>".join(definitions) if definitions else None

        except requests.exceptions.Timeout:
            return None
        except (requests.RequestException, ValueError, KeyError):
            return None
