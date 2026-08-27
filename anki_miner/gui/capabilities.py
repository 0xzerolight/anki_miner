"""Curated catalogue of user-facing features for the Usage Guide browser.

This registry exists to answer the single most common support question —
*"Can it do X?"* — for features that already exist but are buried among the
~100 settings and several feature tabs. Each :class:`Capability` is a hand-written
entry (NOT introspected from config) because the value here is good phrasing and
search synonyms, which an auto-generated list cannot provide.

MAINTENANCE CONVENTION: when you add a user-facing feature or setting, add a
``Capability`` entry here so it shows up in the Usage Guide (menu-bar button, F1).
A test (``tests/unit/test_capabilities.py``) checks that every ``target`` resolves
to a real tab/sub-tab, but nothing forces coverage of new settings -- that is on
you. Menu/dialog-only features omit ``target`` (no Open button); their
description must say where they live.

User-visible strings (``title``, ``description``, ``category``) are wrapped in
``QT_TRANSLATE_NOOP`` so ``pylupdate`` extracts them under the ``Capabilities``
context; they hold the English source verbatim and are localised at display time
via ``QCoreApplication.translate(TRANSLATION_CONTEXT, text)``. ``keywords`` stay
untranslated on purpose -- they preserve English/romaji jargon users type (like
"i+1", "tts", "ocr") alongside the translated title and description.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from PyQt6.QtCore import QT_TRANSLATE_NOOP, QCoreApplication

TRANSLATION_CONTEXT = "Capabilities"

# Stable main-tab keys (resolved by MainWindow._main_tab_index, never indices).
MAIN_TABS: frozenset[str] = frozenset(
    {"video", "deckbuilder", "audiobook", "reading", "analytics", "subtitles", "settings"}
)
# Stable settings sub-tab keys (resolved by SettingsTab.open_subtab).
SETTINGS_SUBTABS: frozenset[str] = frozenset(
    {"anki", "media", "dictionaries", "audio", "frequency", "pitch", "filtering", "youtube", "subtitles", "ui"}
)
# Valid sub-tab keys per container main tab (resolved by the container's
# duck-typed ``open_subtab``). Main tabs absent here have no sub-tabs.
SUBTAB_KEYS: dict[str, frozenset[str]] = {
    "settings": SETTINGS_SUBTABS,
    "video": frozenset({"single", "batch", "youtube"}),
    "reading": frozenset({"manga", "novels", "subtitles", "text"}),
    "subtitles": frozenset({"generate", "retime", "condense", "backfill", "deckfilter", "download"}),
}

# Display categories (deduped; translated at display time).
_CAT_WORKFLOWS = QT_TRANSLATE_NOOP("Capabilities", "Mining workflows")
_CAT_FILTERING = QT_TRANSLATE_NOOP("Capabilities", "Filtering: what gets mined")
_CAT_SOURCES = QT_TRANSLATE_NOOP("Capabilities", "Dictionaries, frequency & pitch")
_CAT_AUDIO = QT_TRANSLATE_NOOP("Capabilities", "Audio")
_CAT_MEDIA = QT_TRANSLATE_NOOP("Capabilities", "Media: clips & screenshots")
_CAT_CARDS = QT_TRANSLATE_NOOP("Capabilities", "Anki cards")
_CAT_APPEARANCE = QT_TRANSLATE_NOOP("Capabilities", "Appearance & language")
_CAT_TOOLS = QT_TRANSLATE_NOOP("Capabilities", "Tools & maintenance")


@dataclass(frozen=True)
class CapabilityTarget:
    """Where a capability lives, by stable key (never a hard-coded tab index).

    ``main_tab`` is one of :data:`MAIN_TABS`. ``subtab`` optionally names an
    inner sub-tab of a container main tab; valid keys per container are in
    :data:`SUBTAB_KEYS` and are resolved by the container widget's duck-typed
    ``open_subtab``. It MUST stay the second positional field — the catalogue
    constructs targets positionally.
    """

    main_tab: str
    subtab: str | None = None


@dataclass(frozen=True)
class Capability:
    """One searchable feature entry shown in the Usage Guide browser."""

    id: str
    title: str
    description: str
    category: str
    # None marks a menu/dialog-only feature: it is listed and searchable but
    # offers no "Open" button, so its description must say where it lives.
    target: CapabilityTarget | None = None
    keywords: tuple[str, ...] = field(default_factory=tuple)


CAPABILITIES: tuple[Capability, ...] = (
    # --- Mining workflows (whole feature tabs) -----------------------------
    Capability(
        id="episode-mining",
        title=QT_TRANSLATE_NOOP("Capabilities", "Mine a single episode"),
        description=QT_TRANSLATE_NOOP("Capabilities", "Mine vocabulary from one video paired with its subtitle file."),
        category=_CAT_WORKFLOWS,
        target=CapabilityTarget("video", "single"),
        keywords=("single", "episode", "movie", "film", "video", "one file"),
    ),
    Capability(
        id="batch-mining",
        title=QT_TRANSLATE_NOOP("Capabilities", "Batch-mine a whole folder"),
        description=QT_TRANSLATE_NOOP("Capabilities", "Queue an entire folder of episodes and mine them in one run."),
        category=_CAT_WORKFLOWS,
        target=CapabilityTarget("video", "batch"),
        keywords=("batch", "folder", "bulk", "season", "multiple", "queue", "many episodes"),
    ),
    Capability(
        id="multi-series-queue",
        title=QT_TRANSLATE_NOOP("Capabilities", "Queue several series at once"),
        description=QT_TRANSLATE_NOOP(
            "Capabilities",
            "Add multiple series to one batch run, each with its own video and subtitle folders and per-series subtitle offset.",
        ),
        category=_CAT_WORKFLOWS,
        target=CapabilityTarget("video", "batch"),
        keywords=("multiple series", "several shows", "per-series offset", "queue series", "different folders"),
    ),
    Capability(
        id="word-curator",
        title=QT_TRANSLATE_NOOP("Capabilities", "Review words before mining"),
        description=QT_TRANSLATE_NOOP(
            "Capabilities",
            "Approve or reject each word, pick its sentence and scene, trim its audio, and mark words known -- before any card is created.",
        ),
        category=_CAT_WORKFLOWS,
        target=CapabilityTarget("video", "single"),
        keywords=("curator", "review", "approve", "confirm", "pick words", "preview cards", "trim audio", "curation"),
    ),
    Capability(
        id="deck-builder",
        title=QT_TRANSLATE_NOOP("Capabilities", "Build a deck by coverage %"),
        description=QT_TRANSLATE_NOOP(
            "Capabilities",
            "Build a frequency-ordered deck that covers a chosen percentage of a whole corpus.",
        ),
        category=_CAT_WORKFLOWS,
        target=CapabilityTarget("deckbuilder"),
        keywords=("deck builder", "corpus", "coverage", "frequency deck", "premade", "premine", "top words"),
    ),
    Capability(
        id="deck-builder-modes",
        title=QT_TRANSLATE_NOOP("Capabilities", "Deck Builder modes (all / top N / coverage %)"),
        description=QT_TRANSLATE_NOOP(
            "Capabilities",
            "Deck Builder always skips per-episode filters and duplicate checks; pick every word, the top N, or a coverage target, and optionally skip known words.",
        ),
        category=_CAT_WORKFLOWS,
        target=CapabilityTarget("deckbuilder"),
        keywords=(
            "bypass filters",
            "include known",
            "allow duplicates",
            "complete deck",
            "top n",
            "coverage target",
            "everything",
        ),
    ),
    Capability(
        id="youtube-mining",
        title=QT_TRANSLATE_NOOP("Capabilities", "Mine from YouTube"),
        description=QT_TRANSLATE_NOOP(
            "Capabilities", "Mine straight from a YouTube URL or playlist -- no local files needed."
        ),
        category=_CAT_WORKFLOWS,
        target=CapabilityTarget("video", "youtube"),
        keywords=("youtube", "url", "playlist", "online", "stream", "web video"),
    ),
    Capability(
        id="audiobook-mining",
        title=QT_TRANSLATE_NOOP("Capabilities", "Mine from an audiobook"),
        description=QT_TRANSLATE_NOOP(
            "Capabilities", "Mine vocabulary from an audiobook or audio file using its transcript."
        ),
        category=_CAT_WORKFLOWS,
        target=CapabilityTarget("audiobook"),
        keywords=("audiobook", "audio", "mp3", "book", "listening", "ln"),
    ),
    Capability(
        id="manga-mining",
        title=QT_TRANSLATE_NOOP("Capabilities", "Mine from manga"),
        description=QT_TRANSLATE_NOOP("Capabilities", "Mine vocabulary from manga volumes processed with mokuro."),
        category=_CAT_WORKFLOWS,
        target=CapabilityTarget("reading", "manga"),
        keywords=("manga", "mokuro", "reading", "cbz", "comic"),
    ),
    Capability(
        id="novels-mining",
        title=QT_TRANSLATE_NOOP("Capabilities", "Mine from novels"),
        description=QT_TRANSLATE_NOOP(
            "Capabilities", "Mine vocabulary from novels and other text (EPUB, Aozora, plain text)."
        ),
        category=_CAT_WORKFLOWS,
        target=CapabilityTarget("reading", "novels"),
        keywords=("novel", "epub", "text", "book", "reading", "aozora", "ln", "light novel"),
    ),
    Capability(
        id="text-mining",
        title=QT_TRANSLATE_NOOP("Capabilities", "Mine pasted text"),
        description=QT_TRANSLATE_NOOP("Capabilities", "Paste any Japanese text and mine it straight into Anki cards."),
        category=_CAT_WORKFLOWS,
        target=CapabilityTarget("reading", "text"),
        keywords=("paste", "text", "clipboard", "copy paste", "raw text", "snippet", "article"),
    ),
    Capability(
        id="subtitle-file-mining",
        title=QT_TRANSLATE_NOOP("Capabilities", "Mine subtitle files without video"),
        description=QT_TRANSLATE_NOOP(
            "Capabilities",
            "Mine vocabulary straight from subtitle files (.srt/.ass/.vtt) as text -- no video needed.",
        ),
        category=_CAT_WORKFLOWS,
        target=CapabilityTarget("reading", "subtitles"),
        keywords=("subtitle only", "srt", "ass", "vtt", "no video", "script", "transcript"),
    ),
    Capability(
        id="subtitle-generate",
        title=QT_TRANSLATE_NOOP("Capabilities", "Generate subtitles from audio"),
        description=QT_TRANSLATE_NOOP(
            "Capabilities", "Create subtitles from audio with a local Whisper model -- as a standalone tool."
        ),
        category=_CAT_WORKFLOWS,
        target=CapabilityTarget("subtitles", "generate"),
        keywords=("generate subtitles", "asr", "whisper", "transcribe", "standalone"),
    ),
    Capability(
        id="subtitle-retime",
        title=QT_TRANSLATE_NOOP("Capabilities", "Re-time existing subtitles"),
        description=QT_TRANSLATE_NOOP(
            "Capabilities", "Re-sync existing subtitles against the video -- as a standalone tool."
        ),
        category=_CAT_WORKFLOWS,
        target=CapabilityTarget("subtitles", "retime"),
        keywords=("retime", "resync", "re-time", "alass", "sync subtitles", "offset", "standalone"),
    ),
    Capability(
        id="retime-workbench",
        title=QT_TRANSLATE_NOOP("Capabilities", "Fine-tune subtitle timing by ear"),
        description=QT_TRANSLATE_NOOP(
            "Capabilities",
            "Pick a line, nudge the offset with the arrow keys, and instantly hear it to check the sync against the video.",
        ),
        category=_CAT_WORKFLOWS,
        target=CapabilityTarget("subtitles", "retime"),
        keywords=("nudge", "manual sync", "by ear", "listen", "a/b compare", "offset preview"),
    ),
    Capability(
        id="subtitle-condense",
        title=QT_TRANSLATE_NOOP("Capabilities", "Condense audio from subtitles"),
        description=QT_TRANSLATE_NOOP(
            "Capabilities", "Build dialogue-only condensed audio from a video or audio file and its subtitles."
        ),
        category=_CAT_WORKFLOWS,
        target=CapabilityTarget("subtitles", "condense"),
        keywords=("condense", "condensed audio", "dialogue only", "immersion", "passive listening", "standalone"),
    ),
    Capability(
        id="deck-filter",
        title=QT_TRANSLATE_NOOP("Capabilities", "Filter a premade deck into a new deck"),
        description=QT_TRANSLATE_NOOP(
            "Capabilities",
            "Copy the notes of a premade Anki deck that survive your filters — known words, "
            "frequency band, blacklist, script type — into a new deck. The source deck is not modified.",
        ),
        category=_CAT_WORKFLOWS,
        target=CapabilityTarget("subtitles", "deckfilter"),
        keywords=(
            "deck filter",
            "premade deck",
            "shared deck",
            "core deck",
            "filter deck",
            "copy notes",
            "known words",
            "morphman",
        ),
    ),
    Capability(
        id="condense-options",
        title=QT_TRANSLATE_NOOP("Capabilities", "Condense: track pickers & extra outputs"),
        description=QT_TRANSLATE_NOOP(
            "Capabilities",
            "Pick the audio and subtitle tracks to condense, and also write condensed subtitles (.srt) and lyrics (.lrc).",
        ),
        category=_CAT_WORKFLOWS,
        target=CapabilityTarget("subtitles", "condense"),
        keywords=("audio track", "subtitle track", "lrc", "lyrics", "condensed subtitles", "track selection"),
    ),
    Capability(
        id="condense-metadata",
        title=QT_TRANSLATE_NOOP("Capabilities", "Tag condensed audio with metadata"),
        description=QT_TRANSLATE_NOOP(
            "Capabilities",
            "Optionally add title, album, artist and cover art to condensed audio outputs before the run.",
        ),
        category=_CAT_WORKFLOWS,
        target=CapabilityTarget("subtitles", "condense"),
        keywords=("metadata", "id3", "tags", "album", "artist", "cover art", "mp3 tags"),
    ),
    Capability(
        id="analytics",
        title=QT_TRANSLATE_NOOP("Capabilities", "View mining history & stats"),
        description=QT_TRANSLATE_NOOP("Capabilities", "See what you've mined over time with history and statistics."),
        category=_CAT_WORKFLOWS,
        target=CapabilityTarget("analytics"),
        keywords=("analytics", "stats", "statistics", "history", "progress", "count", "graph"),
    ),
    Capability(
        id="reset-stats",
        title=QT_TRANSLATE_NOOP("Capabilities", "Reset mining statistics"),
        description=QT_TRANSLATE_NOOP(
            "Capabilities", "Clear every recorded session and difficulty score to start counting again."
        ),
        category=_CAT_WORKFLOWS,
        target=CapabilityTarget("analytics"),
        keywords=("reset stats", "clear statistics", "wipe analytics", "erase history", "start over", "delete stats"),
    ),
    Capability(
        id="youtube-cookies",
        title=QT_TRANSLATE_NOOP("Capabilities", "YouTube cookies / bot bypass"),
        description=QT_TRANSLATE_NOOP(
            "Capabilities", "Use your browser cookies to get past YouTube sign-in and bot checks."
        ),
        category=_CAT_WORKFLOWS,
        target=CapabilityTarget("settings", "youtube"),
        keywords=("cookies", "bot", "sign in", "age restricted", "login", "403", "verify"),
    ),
    Capability(
        id="youtube-limits",
        title=QT_TRANSLATE_NOOP("Capabilities", "YouTube duration & playlist limits"),
        description=QT_TRANSLATE_NOOP(
            "Capabilities", "Cap the maximum video duration and how many playlist videos are fetched."
        ),
        category=_CAT_WORKFLOWS,
        target=CapabilityTarget("settings", "youtube"),
        keywords=("playlist limit", "max videos", "duration", "length cap", "too long"),
    ),
    Capability(
        id="ytdlp-maintenance",
        title=QT_TRANSLATE_NOOP("Capabilities", "Keep yt-dlp up to date"),
        description=QT_TRANSLATE_NOOP(
            "Capabilities",
            "Auto-update the bundled yt-dlp downloader, update it on demand, or point at your own binary.",
        ),
        category=_CAT_WORKFLOWS,
        target=CapabilityTarget("settings", "youtube"),
        keywords=("yt-dlp", "ytdlp", "update downloader", "youtube broken", "custom binary"),
    ),
    # --- Filtering ---------------------------------------------------------
    Capability(
        id="i-plus-one",
        title=QT_TRANSLATE_NOOP("Capabilities", "i+1 sentence mining"),
        description=QT_TRANSLATE_NOOP("Capabilities", "Mine only sentences that contain exactly one unknown word."),
        category=_CAT_FILTERING,
        target=CapabilityTarget("settings", "filtering"),
        keywords=("i+1", "n+1", "1t", "one unknown", "single unknown", "comprehensible input"),
    ),
    Capability(
        id="frequency-rank-filter",
        title=QT_TRANSLATE_NOOP("Capabilities", "Keep words inside a frequency band"),
        description=QT_TRANSLATE_NOOP(
            "Capabilities",
            "Skip words rarer than a maximum rank, more common than a minimum rank, or missing from your frequency lists.",
        ),
        category=_CAT_FILTERING,
        target=CapabilityTarget("settings", "filtering"),
        keywords=(
            "max rank",
            "min rank",
            "frequency cutoff",
            "range",
            "band",
            "too common",
            "too rare",
            "unranked",
            "missing from list",
            "threshold",
        ),
    ),
    Capability(
        id="known-words-db",
        title=QT_TRANSLATE_NOOP("Capabilities", "Skip words you already know"),
        description=QT_TRANSLATE_NOOP(
            "Capabilities", "Skip words already in your Anki collection or previously mined."
        ),
        category=_CAT_FILTERING,
        target=CapabilityTarget("settings", "filtering"),
        keywords=("known words", "already known", "skip known", "anki collection", "ignore known", "seen"),
    ),
    Capability(
        id="excluded-decks",
        title=QT_TRANSLATE_NOOP("Capabilities", "Exclude specific Anki decks from 'known'"),
        description=QT_TRANSLATE_NOOP(
            "Capabilities", "Stop chosen decks from counting as known so their words can still be mined."
        ),
        category=_CAT_FILTERING,
        target=CapabilityTarget("settings", "filtering"),
        keywords=("exclude deck", "ignore deck", "deck exclusion", "subdeck"),
    ),
    Capability(
        id="user-known-list",
        title=QT_TRANSLATE_NOOP("Capabilities", "Mark words as known by hand"),
        description=QT_TRANSLATE_NOOP(
            "Capabilities",
            "Curate your own list of known words -- always applied, survives cache rebuilds, exportable as plain text.",
        ),
        category=_CAT_FILTERING,
        target=CapabilityTarget("settings", "filtering"),
        keywords=("manage known words", "user list", "mark known", "custom known", "export known words", "txt"),
    ),
    Capability(
        id="kana-only-exclude",
        title=QT_TRANSLATE_NOOP("Capabilities", "Exclude kana-only words"),
        description=QT_TRANSLATE_NOOP(
            "Capabilities", "Drop words written without kanji; ticking both boxes leaves a kanji-only deck."
        ),
        category=_CAT_FILTERING,
        target=CapabilityTarget("settings", "filtering"),
        keywords=("kana", "hiragana", "katakana", "kanji only", "script filter", "loanwords"),
    ),
    Capability(
        id="word-lists",
        title=QT_TRANSLATE_NOOP("Capabilities", "Blacklist / whitelist words"),
        description=QT_TRANSLATE_NOOP(
            "Capabilities", "Force-skip or force-allow specific words with your own block/allow lists."
        ),
        category=_CAT_FILTERING,
        target=CapabilityTarget("settings", "filtering"),
        keywords=("blacklist", "whitelist", "word list", "allow list", "block list", "ignore list"),
    ),
    Capability(
        id="sentence-length",
        title=QT_TRANSLATE_NOOP("Capabilities", "Limit sentence length"),
        description=QT_TRANSLATE_NOOP(
            "Capabilities", "Skip sentences longer than a chosen duration or character count."
        ),
        category=_CAT_FILTERING,
        target=CapabilityTarget("settings", "filtering"),
        keywords=("sentence length", "too long", "duration", "char limit", "max length"),
    ),
    Capability(
        id="dedup",
        title=QT_TRANSLATE_NOOP("Capabilities", "Avoid duplicate cards"),
        description=QT_TRANSLATE_NOOP(
            "Capabilities", "Skip making a second card for a word you've already mined this run."
        ),
        category=_CAT_FILTERING,
        target=CapabilityTarget("settings", "filtering"),
        keywords=("duplicate", "dedupe", "deduplicate", "repeat", "unique"),
    ),
    Capability(
        id="subtitle-regex",
        title=QT_TRANSLATE_NOOP("Capabilities", "Strip junk from subtitles (regex)"),
        description=QT_TRANSLATE_NOOP(
            "Capabilities",
            "Remove names, music notes, or bracketed text from subtitles before parsing.",
        ),
        category=_CAT_FILTERING,
        target=CapabilityTarget("settings", "filtering"),
        keywords=("regex", "brackets", "music notes", "speaker labels", "clean subtitles", "strip", "parentheses"),
    ),
    Capability(
        id="name-wordsets",
        title=QT_TRANSLATE_NOOP("Capabilities", "Skip Japanese names"),
        description=QT_TRANSLATE_NOOP(
            "Capabilities",
            "Exclude bundled name lists -- surnames, given names, places, companies and products -- from mining.",
        ),
        category=_CAT_FILTERING,
        target=CapabilityTarget("settings", "filtering"),
        keywords=("names", "surname", "given name", "place names", "proper nouns", "jmnedict", "wordsets"),
    ),
    Capability(
        id="reading-min-occurrence",
        title=QT_TRANSLATE_NOOP("Capabilities", "Require repeat occurrences in a book"),
        description=QT_TRANSLATE_NOOP(
            "Capabilities",
            "Only mine a word from reading material once it appears a chosen number of times in the book or volume.",
        ),
        category=_CAT_FILTERING,
        target=CapabilityTarget("settings", "filtering"),
        keywords=("min occurrence", "occurrences", "repeated", "appears n times", "reading threshold"),
    ),
    Capability(
        id="kana-variant-known",
        title=QT_TRANSLATE_NOOP("Capabilities", "Kana spellings count as known"),
        description=QT_TRANSLATE_NOOP(
            "Capabilities",
            "Treat the kana spelling of a word you know in kanji as known too (on by default).",
        ),
        category=_CAT_FILTERING,
        target=CapabilityTarget("settings", "filtering"),
        keywords=("kana variant", "kana spelling", "alternate spelling", "hiragana form", "same word"),
    ),
    # --- Dictionaries, frequency & pitch -----------------------------------
    Capability(
        id="dictionary-chain",
        title=QT_TRANSLATE_NOOP("Capabilities", "Use & order multiple dictionaries"),
        description=QT_TRANSLATE_NOOP(
            "Capabilities", "Add, reorder, and enable/disable the dictionaries used for definitions."
        ),
        category=_CAT_SOURCES,
        target=CapabilityTarget("settings", "dictionaries"),
        keywords=("dictionary", "dictionaries", "order", "priority", "monolingual", "multiple dictionaries"),
    ),
    Capability(
        id="import-dictionary",
        title=QT_TRANSLATE_NOOP("Capabilities", "Import a Yomitan dictionary"),
        description=QT_TRANSLATE_NOOP(
            "Capabilities", "Add your own Yomitan-format dictionary zip as a definition source."
        ),
        category=_CAT_SOURCES,
        target=CapabilityTarget("settings", "dictionaries"),
        keywords=("import", "yomitan", "add dictionary", "custom dictionary", "zip", "jitendex", "jmdict"),
    ),
    Capability(
        id="jisho-fallback",
        title=QT_TRANSLATE_NOOP("Capabilities", "Jisho.org online fallback"),
        description=QT_TRANSLATE_NOOP(
            "Capabilities", "Fall back to Jisho.org when your offline dictionaries have no entry."
        ),
        category=_CAT_SOURCES,
        target=CapabilityTarget("settings", "dictionaries"),
        keywords=("jisho", "online", "fallback", "internet definition", "web lookup"),
    ),
    Capability(
        id="frequency-chain",
        title=QT_TRANSLATE_NOOP("Capabilities", "Add frequency lists"),
        description=QT_TRANSLATE_NOOP(
            "Capabilities", "Add and order multiple frequency lists used for ranking and the frequency field."
        ),
        category=_CAT_SOURCES,
        target=CapabilityTarget("settings", "frequency"),
        keywords=("frequency", "freq list", "ranking", "bccwj", "novel", "frequency source"),
    ),
    Capability(
        id="pitch-accent",
        title=QT_TRANSLATE_NOOP("Capabilities", "Pitch accent on cards"),
        description=QT_TRANSLATE_NOOP(
            "Capabilities", "Add pitch-accent information to your cards (numeric or romaji)."
        ),
        category=_CAT_SOURCES,
        target=CapabilityTarget("settings", "pitch"),
        keywords=("pitch", "accent", "intonation", "heiban", "nakadaka", "downstep"),
    ),
    Capability(
        id="card-backfill",
        title=QT_TRANSLATE_NOOP("Capabilities", "Fill missing fields on existing notes"),
        description=QT_TRANSLATE_NOOP(
            "Capabilities",
            "Fill missing pitch, frequency, definition, reading and word-audio fields on already-mined notes.",
        ),
        category=_CAT_SOURCES,
        target=CapabilityTarget("subtitles", "backfill"),
        # The screen has been called Card Backfill, Backfill and Update Notes
        # across releases; every one of those words stays a keyword. A rename
        # that drops the previous name makes the screen unfindable to exactly
        # the people who already knew it.
        keywords=(
            "backfill",
            "update notes",
            "fill fields",
            "pitch",
            "frequency",
            "word audio",
            "pronunciation",
            "existing notes",
            "existing cards",
            "bulk update",
            "old cards",
        ),
    ),
    Capability(
        id="asr",
        title=QT_TRANSLATE_NOOP("Capabilities", "Speech-to-text (no subtitles needed)"),
        description=QT_TRANSLATE_NOOP(
            "Capabilities",
            "Generate subtitles from audio with a local Whisper model when none exist.",
        ),
        category=_CAT_SOURCES,
        target=CapabilityTarget("settings", "subtitles"),
        keywords=("asr", "whisper", "speech to text", "stt", "transcribe", "no subtitles", "subtitle generation"),
    ),
    Capability(
        id="dictionary-storage-folder",
        title=QT_TRANSLATE_NOOP("Capabilities", "Move the resource storage folder"),
        description=QT_TRANSLATE_NOOP(
            "Capabilities",
            "Relocate where dictionaries and other indexed resources are stored, restore them from disk, or reimport everything.",
        ),
        category=_CAT_SOURCES,
        target=CapabilityTarget("settings", "dictionaries"),
        keywords=("storage folder", "move", "disk", "location", "reimport", "restore from disk"),
    ),
    Capability(
        id="asr-acceleration",
        title=QT_TRANSLATE_NOOP("Capabilities", "Speed up subtitle generation (GPU)"),
        description=QT_TRANSLATE_NOOP(
            "Capabilities",
            "Install CUDA or Vulkan acceleration and the silence-skipping pack for the local Whisper model.",
        ),
        category=_CAT_SOURCES,
        target=CapabilityTarget("settings", "subtitles"),
        keywords=("gpu", "cuda", "vulkan", "acceleration", "vad", "silence", "faster whisper", "slow transcription"),
    ),
    Capability(
        id="alass-tuning",
        title=QT_TRANSLATE_NOOP("Capabilities", "Tune subtitle alignment (alass)"),
        description=QT_TRANSLATE_NOOP(
            "Capabilities",
            "Configure the alass aligner used for re-timing: split penalty, frame-rate correction, and single-offset mode.",
        ),
        category=_CAT_SOURCES,
        target=CapabilityTarget("settings", "subtitles"),
        keywords=("alass", "alignment", "split penalty", "framerate", "drift", "sync settings"),
    ),
    # --- Audio -------------------------------------------------------------
    Capability(
        id="expression-audio",
        title=QT_TRANSLATE_NOOP("Capabilities", "Word pronunciation audio"),
        description=QT_TRANSLATE_NOOP(
            "Capabilities",
            "Attach native word audio to cards from audio packs, JPod101, or text-to-speech.",
        ),
        category=_CAT_AUDIO,
        target=CapabilityTarget("settings", "audio"),
        keywords=("word audio", "pronunciation", "jpod101", "tts", "expression audio", "vocab audio", "forvo"),
    ),
    Capability(
        id="audio-packs",
        title=QT_TRANSLATE_NOOP("Capabilities", "Import local audio packs"),
        description=QT_TRANSLATE_NOOP(
            "Capabilities", "Use your own local-audio-yomichan packs as a word-pronunciation source."
        ),
        category=_CAT_AUDIO,
        target=CapabilityTarget("settings", "audio"),
        keywords=("audio pack", "local audio", "yomichan audio", "import audio", "nhk", "shinmeikai"),
    ),
    Capability(
        id="sentence-audio",
        title=QT_TRANSLATE_NOOP("Capabilities", "Sentence audio from the video"),
        description=QT_TRANSLATE_NOOP(
            "Capabilities", "Extract the spoken sentence as an audio clip; tune its format and bitrate."
        ),
        category=_CAT_AUDIO,
        target=CapabilityTarget("settings", "media"),
        keywords=("sentence audio", "clip audio", "recording", "bitrate", "audio format", "mp3", "opus"),
    ),
    Capability(
        id="sentence-tts",
        title=QT_TRANSLATE_NOOP("Capabilities", "Sentence audio for reading (TTS)"),
        description=QT_TRANSLATE_NOOP(
            "Capabilities",
            "Synthesize spoken sentence audio for cards mined from books, manga and pasted text.",
        ),
        category=_CAT_AUDIO,
        target=CapabilityTarget("settings", "audio"),
        keywords=("tts", "text to speech", "sentence audio", "reading audio", "synthesized voice"),
    ),
    Capability(
        id="custom-audio-source",
        title=QT_TRANSLATE_NOOP("Capabilities", "Add a custom word-audio source"),
        description=QT_TRANSLATE_NOOP(
            "Capabilities",
            "Plug your own online pronunciation-audio server into the audio chain by URL template or JSON contract.",
        ),
        category=_CAT_AUDIO,
        target=CapabilityTarget("settings", "audio"),
        keywords=("custom audio", "url template", "json source", "own server", "local audio server"),
    ),
    # --- Media: clips & screenshots ----------------------------------------
    Capability(
        id="screenshots",
        title=QT_TRANSLATE_NOOP("Capabilities", "Screenshots on cards"),
        description=QT_TRANSLATE_NOOP("Capabilities", "Capture a still frame from the scene to put on the card."),
        category=_CAT_MEDIA,
        target=CapabilityTarget("settings", "media"),
        keywords=("screenshot", "image", "picture", "frame", "still", "snapshot"),
    ),
    Capability(
        id="animated-clips",
        title=QT_TRANSLATE_NOOP("Capabilities", "Animated clips (GIF/WebP)"),
        description=QT_TRANSLATE_NOOP("Capabilities", "Use a short animated clip instead of a still screenshot."),
        category=_CAT_MEDIA,
        target=CapabilityTarget("settings", "media"),
        keywords=("animated", "gif", "webp", "clip", "motion", "video card"),
    ),
    Capability(
        id="media-timing",
        title=QT_TRANSLATE_NOOP("Capabilities", "Pad or shift audio/screenshot timing"),
        description=QT_TRANSLATE_NOOP(
            "Capabilities", "Add padding or an offset so audio and screenshots line up with the dialogue."
        ),
        category=_CAT_MEDIA,
        target=CapabilityTarget("settings", "media"),
        keywords=("padding", "offset", "timing", "lead in", "trail", "sync", "delay"),
    ),
    Capability(
        id="parallel-workers",
        title=QT_TRANSLATE_NOOP("Capabilities", "Tune parallel media workers"),
        description=QT_TRANSLATE_NOOP(
            "Capabilities",
            "Choose how many media-extraction jobs run at once to trade speed against CPU and memory use.",
        ),
        category=_CAT_MEDIA,
        target=CapabilityTarget("settings", "media"),
        keywords=("parallel", "workers", "cpu", "ram", "performance", "speed", "slow extraction"),
    ),
    # --- Anki cards --------------------------------------------------------
    Capability(
        id="field-mapping",
        title=QT_TRANSLATE_NOOP("Capabilities", "Map data to your note fields"),
        description=QT_TRANSLATE_NOOP(
            "Capabilities", "Choose which note-type field receives the word, sentence, definition, audio, etc."
        ),
        category=_CAT_CARDS,
        target=CapabilityTarget("settings", "anki"),
        keywords=("fields", "field mapping", "note type", "expression field", "sentence field", "definition field"),
    ),
    Capability(
        id="deck-note-type",
        title=QT_TRANSLATE_NOOP("Capabilities", "Choose target deck & note type"),
        description=QT_TRANSLATE_NOOP("Capabilities", "Pick which Anki deck and note type new cards are created in."),
        category=_CAT_CARDS,
        target=CapabilityTarget("settings", "anki"),
        keywords=("deck", "note type", "model", "target deck", "destination"),
    ),
    Capability(
        id="card-styling",
        title=QT_TRANSLATE_NOOP("Capabilities", "Card styling / CSS"),
        description=QT_TRANSLATE_NOOP("Capabilities", "Apply a built-in card style or your own CSS."),
        category=_CAT_CARDS,
        target=CapabilityTarget("settings", "anki"),
        keywords=("css", "style", "card design", "template", "minimal", "appearance"),
    ),
    Capability(
        id="furigana",
        title=QT_TRANSLATE_NOOP("Capabilities", "Furigana / readings"),
        description=QT_TRANSLATE_NOOP("Capabilities", "Include the reading (furigana) for the word on your cards."),
        category=_CAT_CARDS,
        target=CapabilityTarget("settings", "anki"),
        keywords=("furigana", "reading", "kana reading", "ruby"),
    ),
    Capability(
        id="tags",
        title=QT_TRANSLATE_NOOP("Capabilities", "Auto-tag mined notes"),
        description=QT_TRANSLATE_NOOP("Capabilities", "Add tags to every note Anki Miner creates."),
        category=_CAT_CARDS,
        target=CapabilityTarget("settings", "anki"),
        keywords=("tags", "tag", "label"),
    ),
    Capability(
        id="anki-connection",
        title=QT_TRANSLATE_NOOP("Capabilities", "Connect to Anki (AnkiConnect)"),
        description=QT_TRANSLATE_NOOP(
            "Capabilities",
            "Set the AnkiConnect address and test the connection to your running Anki.",
        ),
        category=_CAT_CARDS,
        target=CapabilityTarget("settings", "anki"),
        keywords=("ankiconnect", "connection", "url", "port", "8765", "test connection", "cannot connect"),
    ),
    Capability(
        id="note-type-preset",
        title=QT_TRANSLATE_NOOP("Capabilities", "One-click note-type presets"),
        description=QT_TRANSLATE_NOOP(
            "Capabilities",
            "Apply a preset for a popular note type (Lapis, Kiku, Senren) that fills every field mapping for you.",
        ),
        category=_CAT_CARDS,
        target=CapabilityTarget("settings", "anki"),
        keywords=("preset", "lapis", "kiku", "senren", "note type setup", "auto map fields"),
    ),
    Capability(
        id="bold-target-word",
        title=QT_TRANSLATE_NOOP("Capabilities", "Bold the mined word in the sentence"),
        description=QT_TRANSLATE_NOOP(
            "Capabilities",
            "Wrap the mined word in bold inside the sentence fields on your cards.",
        ),
        category=_CAT_CARDS,
        target=CapabilityTarget("settings", "filtering"),
        keywords=("bold", "highlight", "emphasize", "target word", "sentence formatting"),
    ),
    # --- Appearance & language ---------------------------------------------
    Capability(
        id="themes",
        title=QT_TRANSLATE_NOOP("Capabilities", "Themes, dark mode, fonts & zoom"),
        description=QT_TRANSLATE_NOOP("Capabilities", "Switch light/dark themes and adjust font scale and UI zoom."),
        category=_CAT_APPEARANCE,
        target=CapabilityTarget("settings", "ui"),
        keywords=("theme", "dark mode", "light mode", "font", "zoom", "color", "appearance", "language"),
    ),
    Capability(
        id="ui-language",
        title=QT_TRANSLATE_NOOP("Capabilities", "Change the app language"),
        description=QT_TRANSLATE_NOOP("Capabilities", "Switch the interface to another language."),
        category=_CAT_APPEARANCE,
        target=CapabilityTarget("settings", "ui"),
        keywords=("language", "ui language", "localization", "locale", "translate interface"),
    ),
    Capability(
        id="settings-profiles",
        title=QT_TRANSLATE_NOOP("Capabilities", "Settings profiles"),
        description=QT_TRANSLATE_NOOP(
            "Capabilities",
            "Keep several named snapshots of every setting and switch between them from the Settings footer.",
        ),
        category=_CAT_APPEARANCE,
        target=CapabilityTarget("settings"),
        keywords=(
            "profile",
            "profiles",
            "settings profile",
            "preset",
            "switch settings",
            "multiple setups",
            "different decks",
            "anime vs novels",
        ),
    ),
    Capability(
        id="custom-themes",
        title=QT_TRANSLATE_NOOP("Capabilities", "Install custom themes"),
        description=QT_TRANSLATE_NOOP(
            "Capabilities",
            "Add your own theme JSON files and preview every theme in the gallery before applying it.",
        ),
        category=_CAT_APPEARANCE,
        target=CapabilityTarget("settings", "ui"),
        keywords=("custom theme", "theme json", "gallery", "install theme", "colors", "preview"),
    ),
    Capability(
        id="native-file-dialogs",
        title=QT_TRANSLATE_NOOP("Capabilities", "Use system file dialogs"),
        description=QT_TRANSLATE_NOOP(
            "Capabilities",
            "Switch between Anki Miner's built-in file pickers and your operating system's native ones.",
        ),
        category=_CAT_APPEARANCE,
        target=CapabilityTarget("settings", "ui"),
        keywords=("file dialog", "native picker", "browse window", "file chooser"),
    ),
    Capability(
        id="settings-search",
        title=QT_TRANSLATE_NOOP("Capabilities", "Search the settings"),
        description=QT_TRANSLATE_NOOP(
            "Capabilities",
            "Type in the search box at the top of Settings to jump straight to any control.",
        ),
        category=_CAT_APPEARANCE,
        target=CapabilityTarget("settings"),
        keywords=("settings search", "find setting", "where is", "jump to setting"),
    ),
    Capability(
        id="settings-export-import",
        title=QT_TRANSLATE_NOOP("Capabilities", "Export / import settings"),
        description=QT_TRANSLATE_NOOP(
            "Capabilities",
            "Save every setting to a portable file, load it on another machine, or reset everything to defaults -- from the Settings footer.",
        ),
        category=_CAT_APPEARANCE,
        target=CapabilityTarget("settings"),
        keywords=("export settings", "import settings", "backup", "transfer", "reset to defaults", "portable"),
    ),
    Capability(
        id="update-check",
        title=QT_TRANSLATE_NOOP("Capabilities", "Check for app updates"),
        description=QT_TRANSLATE_NOOP(
            "Capabilities",
            "Check for a new Anki Miner version from the Help menu, or toggle the automatic startup check in the Settings footer.",
        ),
        category=_CAT_APPEARANCE,
        target=CapabilityTarget("settings"),
        keywords=("update", "new version", "upgrade", "release", "check for updates"),
    ),
    # --- Tools & maintenance (standalone tools plus menu/dialog features) --
    Capability(
        id="media-downloader",
        title=QT_TRANSLATE_NOOP("Capabilities", "Download videos or audio"),
        description=QT_TRANSLATE_NOOP(
            "Capabilities",
            "Save videos, audio or subtitles from a URL to a folder using yt-dlp, without mining. "
            "Works with any site yt-dlp supports.",
        ),
        category=_CAT_TOOLS,
        target=CapabilityTarget("subtitles", "download"),
        keywords=(
            "download",
            "downloader",
            "yt-dlp",
            "save video",
            "mp3",
            "audio only",
            "url",
        ),
    ),
    Capability(
        id="restyle-mined-cards",
        title=QT_TRANSLATE_NOOP("Capabilities", "Restyle mined cards"),
        description=QT_TRANSLATE_NOOP(
            "Capabilities", "Re-apply the latest Anki Miner styling to cards you mined earlier -- Tools menu."
        ),
        category=_CAT_TOOLS,
        keywords=("restyle", "existing cards", "old cards", "card styling", "css", "update styles"),
    ),
    Capability(
        id="system-health",
        title=QT_TRANSLATE_NOOP("Capabilities", "System health check"),
        description=QT_TRANSLATE_NOOP(
            "Capabilities",
            "See whether Anki, ffmpeg and your resources are ready, with one-click fixes -- open it from the status-bar badge.",
        ),
        category=_CAT_TOOLS,
        keywords=("health", "status", "ready", "doctor", "diagnose", "checklist", "fix"),
    ),
    Capability(
        id="setup-wizard",
        title=QT_TRANSLATE_NOOP("Capabilities", "Setup wizard"),
        description=QT_TRANSLATE_NOOP(
            "Capabilities",
            "Re-run the guided first-time setup -- theme, Anki connection, deck, note type and resources -- from the Tools menu.",
        ),
        category=_CAT_TOOLS,
        keywords=("wizard", "first run", "onboarding", "guided setup", "start over"),
    ),
    Capability(
        id="download-resources",
        title=QT_TRANSLATE_NOOP("Capabilities", "Download recommended resources"),
        description=QT_TRANSLATE_NOOP(
            "Capabilities",
            "Get a curated dictionary, frequency list and pitch-accent data in one click from the Tools menu.",
        ),
        category=_CAT_TOOLS,
        keywords=("recommended", "download resources", "starter pack", "quick setup", "jitendex"),
    ),
    Capability(
        id="desktop-shortcut",
        title=QT_TRANSLATE_NOOP("Capabilities", "Create a desktop shortcut"),
        description=QT_TRANSLATE_NOOP(
            "Capabilities",
            "Add an Anki Miner launcher to your desktop from the Tools menu.",
        ),
        category=_CAT_TOOLS,
        keywords=("shortcut", "launcher", "desktop icon"),
    ),
    Capability(
        id="export-diagnostics",
        title=QT_TRANSLATE_NOOP("Capabilities", "Export diagnostics for a bug report"),
        description=QT_TRANSLATE_NOOP(
            "Capabilities",
            "Save a zip of logs and system details to attach to a bug report -- from the Help menu.",
        ),
        category=_CAT_TOOLS,
        keywords=("diagnostics", "logs", "bug report", "support", "zip", "troubleshoot"),
    ),
    Capability(
        id="mini-job-monitor",
        title=QT_TRANSLATE_NOOP("Capabilities", "Mini job monitor"),
        description=QT_TRANSLATE_NOOP(
            "Capabilities",
            "Pop out a small always-on-top window that tracks the current run -- from the status-bar task menu.",
        ),
        category=_CAT_TOOLS,
        keywords=("monitor", "floating window", "always on top", "watch progress", "background run"),
    ),
    Capability(
        id="session-recovery",
        title=QT_TRANSLATE_NOOP("Capabilities", "Crash & session recovery"),
        description=QT_TRANSLATE_NOOP(
            "Capabilities",
            "After an unexpected exit, Anki Miner offers to restore unfinished queues and resume interrupted downloads at the next launch.",
        ),
        category=_CAT_TOOLS,
        keywords=("recover", "restore", "crash", "resume download", "unfinished queue", "power loss"),
    ),
    Capability(
        id="undo-run",
        title=QT_TRANSLATE_NOOP("Capabilities", "Undo a mining run"),
        description=QT_TRANSLATE_NOOP(
            "Capabilities",
            "Delete the notes a run just created, straight from the results dialog.",
        ),
        category=_CAT_TOOLS,
        keywords=("undo", "delete notes", "revert", "rollback", "mistake", "wrong deck"),
    ),
    Capability(
        id="keyboard-shortcuts",
        title=QT_TRANSLATE_NOOP("Capabilities", "Keyboard shortcuts"),
        description=QT_TRANSLATE_NOOP(
            "Capabilities",
            "Ctrl+1..7 switches tabs, Ctrl+, opens Settings, Ctrl+Enter runs the screen's main action, F1 opens this guide -- full list in Help -> About.",
        ),
        category=_CAT_TOOLS,
        keywords=("shortcuts", "hotkeys", "keybindings", "keyboard", "f1"),
    ),
)


def search(query: str) -> list[Capability]:
    """Return capabilities matching ``query`` (case-insensitive substring).

    Matches against title, description, and every keyword. An empty/blank query
    returns the full catalogue in registry order. Results preserve registry
    order so the category grouping in the dialog stays stable.
    """
    q = query.strip().lower()
    if not q:
        return list(CAPABILITIES)
    out: list[Capability] = []
    for cap in CAPABILITIES:
        haystack = (
            cap.title,
            cap.description,
            QCoreApplication.translate(TRANSLATION_CONTEXT, cap.title),
            QCoreApplication.translate(TRANSLATION_CONTEXT, cap.description),
            *cap.keywords,
        )
        if any(q in part.lower() for part in haystack):
            out.append(cap)
    return out
