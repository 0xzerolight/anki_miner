"""Curated catalogue of user-facing features for the "Find a Feature" browser.

This registry exists to answer the single most common support question —
*"Can it do X?"* — for features that already exist but are buried among the
~100 settings and several feature tabs. Each :class:`Capability` is a hand-written
entry (NOT introspected from config) because the value here is good phrasing and
search synonyms, which an auto-generated list cannot provide.

MAINTENANCE CONVENTION: when you add a user-facing feature or setting, add a
``Capability`` entry here so it shows up in Tools -> Find a Feature. A test
(``tests/unit/test_capabilities.py``) checks that every ``target`` resolves to a
real tab/sub-tab, but nothing forces coverage of new settings -- that is on you.

User-visible strings (``title``, ``description``, ``category``) are wrapped in
``QT_TRANSLATE_NOOP`` so ``pylupdate`` extracts them under the ``Capabilities``
context; they hold the English source verbatim and are localised at display time
via ``QCoreApplication.translate(TRANSLATION_CONTEXT, text)``. ``keywords`` stay
untranslated on purpose -- they are the search index and must match what users
actually type (often English/romaji jargon like "i+1", "tts", "ocr").
"""

from __future__ import annotations

from dataclasses import dataclass, field

from PyQt6.QtCore import QT_TRANSLATE_NOOP

TRANSLATION_CONTEXT = "Capabilities"

# Stable main-tab keys (resolved by MainWindow._main_tab_index, never indices).
MAIN_TABS: frozenset[str] = frozenset(
    {"video", "deckbuilder", "audiobook", "reading", "analytics", "subtitles", "settings"}
)
# Stable settings sub-tab keys (resolved by SettingsTab.open_subtab).
SETTINGS_SUBTABS: frozenset[str] = frozenset(
    {"anki", "media", "dictionaries", "audio", "frequency", "filtering", "youtube", "subtitles", "ui"}
)
# Valid sub-tab keys per container main tab (resolved by the container's
# duck-typed ``open_subtab``). Main tabs absent here have no sub-tabs.
SUBTAB_KEYS: dict[str, frozenset[str]] = {
    "settings": SETTINGS_SUBTABS,
    "video": frozenset({"single", "batch", "youtube"}),
    "reading": frozenset({"manga", "novels"}),
    "subtitles": frozenset({"generate", "retime"}),
}

# Display categories (deduped; translated at display time).
_CAT_WORKFLOWS = QT_TRANSLATE_NOOP("Capabilities", "Mining workflows")
_CAT_FILTERING = QT_TRANSLATE_NOOP("Capabilities", "Filtering: what gets mined")
_CAT_SOURCES = QT_TRANSLATE_NOOP("Capabilities", "Dictionaries, frequency & pitch")
_CAT_AUDIO = QT_TRANSLATE_NOOP("Capabilities", "Audio")
_CAT_MEDIA = QT_TRANSLATE_NOOP("Capabilities", "Media: clips & screenshots")
_CAT_CARDS = QT_TRANSLATE_NOOP("Capabilities", "Anki cards")
_CAT_APPEARANCE = QT_TRANSLATE_NOOP("Capabilities", "Appearance & language")


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
    """One searchable feature entry shown in the Find a Feature browser."""

    id: str
    title: str
    description: str
    category: str
    target: CapabilityTarget
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
        title=QT_TRANSLATE_NOOP("Capabilities", "Build a complete deck (skip per-episode filters)"),
        description=QT_TRANSLATE_NOOP(
            "Capabilities",
            "Deck Builder can bypass i+1/frequency/word-list filters and allow duplicates for full coverage.",
        ),
        category=_CAT_WORKFLOWS,
        target=CapabilityTarget("deckbuilder"),
        keywords=("bypass filters", "include known", "allow duplicates", "complete deck", "everything"),
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
        id="analytics",
        title=QT_TRANSLATE_NOOP("Capabilities", "View mining history & stats"),
        description=QT_TRANSLATE_NOOP("Capabilities", "See what you've mined over time with history and statistics."),
        category=_CAT_WORKFLOWS,
        target=CapabilityTarget("analytics"),
        keywords=("analytics", "stats", "statistics", "history", "progress", "count", "graph"),
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
        title=QT_TRANSLATE_NOOP("Capabilities", "Skip rare words (frequency cutoff)"),
        description=QT_TRANSLATE_NOOP("Capabilities", "Ignore words rarer than a chosen frequency rank."),
        category=_CAT_FILTERING,
        target=CapabilityTarget("settings", "filtering"),
        keywords=("max rank", "frequency cutoff", "common only", "rare", "threshold", "top n"),
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
            "Capabilities", "Curate your own list of known words that is always applied and survives cache rebuilds."
        ),
        category=_CAT_FILTERING,
        target=CapabilityTarget("settings", "filtering"),
        keywords=("manage known words", "user list", "mark known", "custom known"),
    ),
    Capability(
        id="kana-only-exclude",
        title=QT_TRANSLATE_NOOP("Capabilities", "Exclude kana-only words"),
        description=QT_TRANSLATE_NOOP("Capabilities", "Drop words written only in hiragana or katakana."),
        category=_CAT_FILTERING,
        target=CapabilityTarget("settings", "filtering"),
        keywords=("kana", "hiragana", "katakana", "kanji only", "script filter"),
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
        id="cross-episode-count",
        title=QT_TRANSLATE_NOOP("Capabilities", "Only words seen across N episodes"),
        description=QT_TRANSLATE_NOOP(
            "Capabilities", "In batch and deck builds, mine only words that appear in at least N episodes."
        ),
        category=_CAT_FILTERING,
        target=CapabilityTarget("settings", "filtering"),
        keywords=("min appearances", "recurring", "multiple episodes", "repeated", "cross episode"),
    ),
    Capability(
        id="pos-filter",
        title=QT_TRANSLATE_NOOP("Capabilities", "Filter by part of speech"),
        description=QT_TRANSLATE_NOOP(
            "Capabilities", "Choose which word types (nouns, verbs, particles, ...) are mined."
        ),
        category=_CAT_FILTERING,
        target=CapabilityTarget("settings", "filtering"),
        keywords=("part of speech", "pos", "nouns", "verbs", "particles", "word type", "proper noun"),
    ),
    Capability(
        id="sentence-length",
        title=QT_TRANSLATE_NOOP("Capabilities", "Limit sentence length"),
        description=QT_TRANSLATE_NOOP("Capabilities", "Skip sentences that are too long or too short."),
        category=_CAT_FILTERING,
        target=CapabilityTarget("settings", "filtering"),
        keywords=("sentence length", "too long", "too short", "duration", "char limit"),
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
        target=CapabilityTarget("settings", "subtitles"),
        keywords=("regex", "brackets", "music notes", "speaker labels", "clean subtitles", "strip", "parentheses"),
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
        target=CapabilityTarget("settings", "dictionaries"),
        keywords=("pitch", "accent", "intonation", "heiban", "nakadaka", "downstep"),
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
        title=QT_TRANSLATE_NOOP("Capabilities", "YouTube quality & playlist limits"),
        description=QT_TRANSLATE_NOOP(
            "Capabilities", "Cap video quality, max duration, and how many playlist videos are fetched."
        ),
        category=_CAT_WORKFLOWS,
        target=CapabilityTarget("settings", "youtube"),
        keywords=("playlist limit", "max videos", "duration", "quality", "resolution", "height"),
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
        haystack = (cap.title, cap.description, *cap.keywords)
        if any(q in part.lower() for part in haystack):
            out.append(cap)
    return out
