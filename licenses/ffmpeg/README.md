# Bundled FFmpeg — license and source offer

Anki Miner's downloadable binaries (Linux AppImage, Windows installer, macOS
bundle) ship with a static build of [FFmpeg](https://ffmpeg.org). These builds
are licensed under the **GNU General Public License, version 3** — the full text
is in [`COPYING.GPLv3`](COPYING.GPLv3).

## What is bundled, and what is not

| Distribution | Bundles FFmpeg? |
|--------------|-----------------|
| Linux AppImage | yes |
| Windows installer / portable zip | yes |
| macOS bundle | yes |
| `.deb` package | no — uses ffmpeg from the system / PATH |
| `pip` / `pipx` install | no — uses ffmpeg from the system / PATH |

The `.deb`, `pip`, and `pipx` installs do not contain FFmpeg, so the GPL source
offer below does not apply to them.

## Upstream build sources

The bundled binaries are pre-built static builds taken from:

- **Linux & Windows** — [BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds),
  release tag `autobuild-2026-06-02-14-20`, FFmpeg version
  `n8.1.1-9-g58d4114d36` (the `gpl-8.1` variant).
- **macOS (arm64)** — [osxexperts.net](https://www.osxexperts.net) static
  FFmpeg 8.1 arm64 build.

## Written offer of source

The complete corresponding source for these FFmpeg versions is the FFmpeg
project's own source. It is available from the FFmpeg project at
<https://ffmpeg.org>, its release archives at <https://ffmpeg.org/releases/>,
and its git repository at <https://github.com/FFmpeg/FFmpeg>, as well as from
the build providers listed above.

On request we will provide, or point you to, the exact corresponding source for
the bundled version. Open an issue at
<https://github.com/0xzerolight/anki_miner/issues> and reference the FFmpeg
version string shown above.
