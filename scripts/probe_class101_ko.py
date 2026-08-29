"""Probe whether the languagepod101 audio endpoint family serves Korean.

Run manually with network access:  .venv/bin/python scripts/probe_class101_ko.py
Prints one verdict line per candidate URL shape plus a PROBE_RESULT line. Not
imported by the app and never run in CI (the suite's socket tripwire blocks real
network).
"""

import hashlib
import sys

import requests

# services/expression_audio_fetcher.py:70 - JPod101 answers unknown words with
# HTTP 200 and this fixed placeholder mp3.
JPOD101_NOT_FOUND_SHA256 = "ae6398b5a27bc8c0a771df6c907ade794be15518174773c58c7c7ddd17098906"

URL = "https://assets.languagepod101.com/dictionary/korean/audiomp3.php"
CANDIDATES = [
    ("korean-kanji", URL, {"kanji": "학생"}),
    ("korean-kana", URL, {"kana": "학생"}),
    ("korean-word", URL, {"word": "학생"}),
]


def main() -> int:
    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0"
    any_hit = False
    for name, url, params in CANDIDATES:
        try:
            r = session.get(url, params=params, timeout=15)
        except requests.RequestException as exc:
            print(f"{name}: TRANSPORT-FAIL {type(exc).__name__}: {exc}")
            continue
        body = r.content
        digest = hashlib.sha256(body).hexdigest()
        ctype = r.headers.get("Content-Type", "")
        if r.status_code != 200:
            verdict = f"HTTP-{r.status_code}"
        elif digest == JPOD101_NOT_FOUND_SHA256:
            verdict = "MISS-PLACEHOLDER"
        elif "audio" not in ctype:
            verdict = f"NON-AUDIO ({ctype})"
        else:
            verdict = "HIT"
            any_hit = True
        print(f"{name}: {verdict} status={r.status_code} bytes={len(body)} sha256={digest[:16]} ctype={ctype}")
    print("PROBE_RESULT:", "PASS" if any_hit else "FAIL")
    return 0 if any_hit else 1


if __name__ == "__main__":
    sys.exit(main())
