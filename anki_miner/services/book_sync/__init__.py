"""Audiobook → book alignment (Utilities → Audiobook Sync).

Transcribe-then-align, after SubPlz (kanjieater/SubPlz, MIT): a Whisper
transcript of the audio is aligned character-by-character to the book's own
sentences on a folded alphabet (:mod:`normalize`), window by window with a
monotonic cursor (:mod:`aligner`), and every sentence that received matched
characters gets a start/end interpolated from the Whisper segments those
characters fell into. :mod:`pipeline` strings the reading loader, the
windowed transcriber and the SRT writer together per audio file. No Qt here.
"""
