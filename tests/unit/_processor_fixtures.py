"""Word and media builders shared by the EpisodeProcessor test files."""

from pathlib import Path

from anki_miner.models import MediaData, TokenizedWord


def make_word(lemma="食べる", surface=None, start_time=1.0, pos=None):
    """A two-second word on its own line; surface and sentence derive from *lemma*."""
    return TokenizedWord(
        surface=surface or f"{lemma}た",
        lemma=lemma,
        reading="タベル",
        sentence=f"{lemma}のテスト",
        start_time=start_time,
        end_time=start_time + 2.0,
        duration=2.0,
        pos=pos,
    )


def make_media(prefix="word"):
    """``MediaData`` naming /tmp/<prefix>.jpg and .mp3. No file is written."""
    return MediaData(
        screenshot_path=Path(f"/tmp/{prefix}.jpg"),
        audio_path=Path(f"/tmp/{prefix}.mp3"),
        screenshot_filename=f"{prefix}.jpg",
        audio_filename=f"{prefix}.mp3",
    )
