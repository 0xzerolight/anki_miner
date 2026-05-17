# Security Policy

## Reporting a vulnerability

Please do not open a public issue for security vulnerabilities.

Report privately via GitHub Security Advisories:
<https://github.com/0xzerolight/anki_miner/security/advisories/new>

Anki Miner is maintained by a single person on a best-effort basis. You can expect an acknowledgment within a reasonable time.

## Scope

In scope:

- Code execution or path traversal in subtitle parsing, media extraction, or AnkiConnect interaction.
- Network handling for dictionary providers (Jisho, Yomitan-imported dictionaries).
- yt-dlp subprocess handling and the YouTube workspace lifecycle.
- Bundled installers (PyInstaller, AppImage, `.deb`, Inno Setup).

Out of scope:

- Vulnerabilities in third-party services (Anki, yt-dlp, Jisho).
- Issues requiring local filesystem write access already granted to the user.

## Supported versions

The latest minor release on PyPI is supported. Older versions may receive critical patches at maintainer discretion.
