"""Chinese language engine (spec 9.1 / 10.1).

Every third-party zh dependency is imported function-locally so importing this
package — and building the zh LanguageProfile — never needs the ``anki-miner[zh]``
extra installed. Availability is reported by ``languages.zh.availability``.

``build_profile()`` is added by task 2A.12, once every input module exists.
"""
