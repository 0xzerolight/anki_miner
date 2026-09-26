"""Characterization test for the three URL-redaction wrappers.

Pins today's exact fail-closed output before
``anki_miner.utils.url_redaction.split_loggable_url`` is extracted as their
shared core (quality-audit finding services-03, merged core-03). This file
must pass unmodified both before and after that refactor — same file, same
assertions, same outputs.

Fail-closed means: on ANY doubt whether *url* carries credentials, return
``"<redacted-url>"`` rather than a partially-redacted string. The three
wrappers differ only in what happens to the query string on the clean path:
``redact_url_for_log`` drops it, ``redact_youtube_url_for_log`` keeps an
allow-listed subset, ``_redact_custom_audio_url`` writes the literal
``REDACTED``.
"""

import pytest

from anki_miner.diagnostics.bundle import _redact_custom_audio_url
from anki_miner.services.audio_fetch_common import redact_url_for_log
from anki_miner.utils.youtube_url import redact_youtube_url_for_log

CASES = {
    "userinfo_user_pass": (
        "https://user:pass@example.com:8443/api/audio?v=123&secret=abc#frag",
        "<redacted-url>",
        "<redacted-url>",
        "<redacted-url>",
    ),
    "userinfo_user_only": (
        "https://user@example.com:8443/api/audio?v=123&secret=abc#frag",
        "<redacted-url>",
        "<redacted-url>",
        "<redacted-url>",
    ),
    "percent_encoded_at_in_netloc": (
        "https://ex%40ample.com/api/audio?v=123&secret=abc#frag",
        "<redacted-url>",
        "<redacted-url>",
        "<redacted-url>",
    ),
    "bad_port": (
        "https://example.com:99999/api/audio?v=123&secret=abc#frag",
        "<redacted-url>",
        "<redacted-url>",
        "<redacted-url>",
    ),
    "ipv6_host": (
        "https://[2001:db8::1]:8443/api/audio?v=123&secret=abc#frag",
        "https://[2001:db8::1]:8443/api/audio",
        "https://[2001:db8::1]:8443/api/audio?v=123",
        "https://[2001:db8::1]:8443/api/audio?REDACTED",
    ),
    "missing_scheme_host": (
        "not-a-url-at-all",
        "<redacted-url>",
        "<redacted-url>",
        "<redacted-url>",
    ),
    "clean_url": (
        "https://example.com:8443/api/audio?v=123&secret=abc#frag",
        "https://example.com:8443/api/audio",
        "https://example.com:8443/api/audio?v=123",
        "https://example.com:8443/api/audio?REDACTED",
    ),
    "empty_host": (
        "http://:80/x",
        "<redacted-url>",
        "<redacted-url>",
        "<redacted-url>",
    ),
    "unclosed_ipv6_bracket": (
        "https://[::1/x",
        "<redacted-url>",
        "<redacted-url>",
        "<redacted-url>",
    ),
    "scheme_with_no_host": (
        "https:///path",
        "<redacted-url>",
        "<redacted-url>",
        "<redacted-url>",
    ),
    "empty_port": (
        "https://example.com:/x",
        "https://example.com/x",
        "https://example.com/x",
        "https://example.com/x?REDACTED",
    ),
}


@pytest.mark.parametrize("case", CASES, ids=list(CASES))
def test_redaction_wrappers_exact_output(case: str) -> None:
    url, expected_audio, expected_youtube, expected_bundle = CASES[case]
    assert redact_url_for_log(url) == expected_audio
    assert redact_youtube_url_for_log(url) == expected_youtube
    assert _redact_custom_audio_url(url) == expected_bundle
