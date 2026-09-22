# Security Policy

## Reporting a vulnerability

Please do not open a public issue for security vulnerabilities.

Report privately via GitHub Security Advisories:
<https://github.com/0xzerolight/anki_miner/security/advisories/new>

Anki Miner is maintained by a single person on a best-effort basis. You can expect an acknowledgment within a reasonable time.

## Scope

In scope:

- Code execution or path traversal in subtitle parsing, media extraction, or AnkiConnect interaction.
- Network handling and integrity checks for everything the app downloads: dictionaries and other recommended resources, language, ASR and onnxruntime packs (sha256-pinned wheels extracted onto `sys.path`), the mokuro/uv, alass and yt-dlp binaries, update checks, and word and sentence audio sources (JapanesePod101, Google, Microsoft Edge, Naver Papago, custom URLs).
- yt-dlp subprocess handling and the YouTube workspace lifecycle.
- Bundled installers (PyInstaller, AppImage, `.deb`, Inno Setup, macOS `.dmg`).

Out of scope:

- Vulnerabilities in third-party services (Anki, yt-dlp, Jisho).
- Issues requiring local filesystem write access already granted to the user.

## Supported versions

The latest minor release on PyPI is supported. Older versions may receive critical patches at maintainer discretion.
