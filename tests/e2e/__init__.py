"""End-to-end GUI test harness (real services, safety-gated).

Two complementary purposes: (1) surface multi-session accumulation/leak bugs that
unit tests cannot reproduce; (2) catch GUI-consistency and GUI/integration bugs
(widget state, word-set correctness, cancel/error paths, known-words accumulation)
that only manifest with the real widget stack and real services wired together.
"""
