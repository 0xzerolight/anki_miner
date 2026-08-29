# kiwipiepy

Anki Miner uses kiwipiepy (Kiwi) as the Korean morphological analyser.
kiwipiepy is licensed LGPL-3.0-or-later; the full licence text is in
`COPYING.LGPLv3` and the source pointer in `SOURCES.txt`.

The release artifact is a PyInstaller *onedir* distribution: the kiwipiepy
extension module and its model data are ordinary files inside the distribution
directory and can be replaced with a modified build of the same version, which
is the LGPL relinking/replaceability requirement for this configuration.
