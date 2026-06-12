"""JapanesePod101 pronunciation audio fetcher with on-disk cache."""

import hashlib
import logging
import os
import time
from pathlib import Path

import requests

from anki_miner.utils.file_utils import safe_filename

logger = logging.getLogger(__name__)

JPOD101_AUDIO_URL = "https://assets.languagepod101.com/dictionary/japanese/audiomp3.php"

# JPod101 answers unknown words with HTTP 200 and a fixed "audio not
# available" placeholder mp3. This is the SHA-256 of that placeholder
# (same value Yomitan hardcodes) — matching bodies are treated as misses.
JPOD101_NOT_FOUND_SHA256 = "ae6398b5a27bc8c0a771df6c907ade794be15518174773c58c7c7ddd17098906"


class JPod101AudioFetcher:
    """Fetches word pronunciation audio from JapanesePod101.

    Results are cached on disk: successful downloads as ``.mp3`` files,
    confirmed not-found words as zero-byte ``.miss`` markers so they are
    never re-requested. Transient failures (timeouts, non-200) are not
    cached and will be retried on the next call.
    """

    def __init__(self, cache_dir: Path, delay: float = 0.2):
        """Initialize with cache directory and rate-limiting delay.

        Args:
            cache_dir: Directory for cached mp3s and miss markers.
            delay: Seconds to wait before each network request.
        """
        self._cache_dir = cache_dir
        self._delay = delay

    def fetch(self, mined_form: str, reading: str) -> Path | None:
        """Fetch pronunciation audio for a word.

        Args:
            mined_form: Word as mined onto the card (kanji/surface form).
            reading: Kana reading of the word (may be empty).

        Returns:
            Path to a cached mp3, or None if unavailable.
        """
        if not mined_form.strip():
            return None

        stem = safe_filename(f"jpod101_{mined_form}_{reading}")
        mp3_path = self._cache_dir / f"{stem}.mp3"
        miss_path = self._cache_dir / f"{stem}.miss"

        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            if mp3_path.exists() and mp3_path.stat().st_size > 0:
                return mp3_path
            if miss_path.exists():
                return None

            time.sleep(self._delay)

            # Valid words 301-redirect to a CDN mp3; requests follows
            # redirects by default, so the final body is the audio itself.
            response = requests.get(
                JPOD101_AUDIO_URL,
                params={"kanji": mined_form, "kana": reading},
                timeout=10,
            )

            if response.status_code != 200:
                return None

            # Zero-byte 200 is ambiguous (network glitch, premature close) —
            # treat as transient failure, not a confirmed miss.
            if not response.content:
                return None

            if hashlib.sha256(response.content).hexdigest() == JPOD101_NOT_FOUND_SHA256:
                # Confirmed not-found: marker prevents re-requesting.
                # Miss markers are permanent by design (Yomitan-style); delete
                # the cache dir to retry words that were incorrectly marked.
                miss_path.touch()
                return None

            # Write atomically: stage to a .part file then rename so a killed
            # process cannot leave a truncated mp3 that passes the st_size > 0
            # cache-hit check on the next run.
            part_path = mp3_path.with_suffix(".mp3.part")
            part_path.write_bytes(response.content)
            os.replace(part_path, mp3_path)
            return mp3_path

        except requests.exceptions.Timeout:
            return None
        except (requests.RequestException, OSError):
            return None
