"""Shared assertion for the resume keys every download caller must supply (D16-C).

A resume key is what lets a partial transfer be picked up after quitting, and
what stops two artifacts continuing each other's bytes. Every ``download_to_temp``
caller has to pass one, and it has to be stable across app runs and unique to the
artifact. The stubs in the installer tests call this so that dropping a key —
or generating a random one per run — fails a test rather than silently going back
to "start over from zero".
"""

from __future__ import annotations

import re

# Same shape ``anki_miner.services.download_resume.safe_key`` enforces: the key
# names a file, so nothing that could escape the directory is allowed.
_SHAPE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

# A key derived from the clock, a uuid or an object id is not stable across runs,
# so the partial it names could never be found again.
_UNSTABLE = re.compile(r"0x[0-9a-f]{6,}|\b1[6-9]\d{8}\b")


def assert_stable_resume_key(resume_key: object) -> None:
    """Fail unless ``resume_key`` is a usable, stable, file-safe identifier."""
    assert isinstance(resume_key, str) and resume_key, f"caller passed no resume key: {resume_key!r}"
    assert _SHAPE.match(resume_key), f"resume key is not file-safe: {resume_key!r}"
    assert not _UNSTABLE.search(resume_key), f"resume key is not stable across runs: {resume_key!r}"
