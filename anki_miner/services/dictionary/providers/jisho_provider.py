"""Jisho API dictionary provider."""

import logging
import time
from html import escape

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

    @property
    def is_online(self) -> bool:
        return True

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
            senses = []
            for sense in first.get("senses", [])[:5]:
                eng = sense.get("english_definitions", [])
                if eng:
                    # HTML-escape each API-sourced leaf string before it lands
                    # in card HTML. AnkiService stores this raw and Anki's
                    # QtWebEngine renders it at review time, so unescaped markup
                    # from Jisho would be stored XSS (the offline path sanitizes
                    # via yomitan_renderer; mirror that leaf-text escaping here).
                    joined = "; ".join(escape(str(d)) for d in eng)
                    senses.append(joined)

            if not senses:
                return None

            # Emit the same markup shape as the offline IndexedDictProvider so the
            # card-style presets style Jisho-fallback cards identically: a muted
            # `<i>` chip line plus a numbered `gloss-list`/`gloss-item` structure.
            # Sense numbering is left to the preset's `list-style-type: decimal` —
            # no hand-written "1." prefix — so single-sense entries drop the ordinal
            # via the `gloss-list[data-count="1"]` rule, matching the offline path.
            items = "".join(f'<li class="gloss-item"><div class="gloss-content">{sense}</div></li>' for sense in senses)
            return (
                '<div class="yomitan-glossary">'
                '<ol data-count="1">'
                f'<li data-dictionary="{escape(self.name)}">'
                f"<i>({escape(self.name)})</i>"
                f'<ul class="gloss-list" data-count="{len(senses)}">{items}</ul>'
                "</li>"
                "</ol>"
                "</div>"
            )

        except requests.exceptions.Timeout:
            return None
        except (requests.RequestException, ValueError, KeyError):
            return None
