# Bundled libmpv — license and source offer

Anki Miner's downloadable binaries ship the [mpv](https://mpv.io) media
library (libmpv), which powers the in-app video preview. The bundled builds
include GPL components (FFmpeg GPL codecs), so they are distributed under the
**GNU General Public License** — Anki Miner itself is GPL-3.0-or-later; the
full text is in [`COPYING.GPLv3`](COPYING.GPLv3). mpv's own per-file licensing
is documented in the `Copyright` file that CI drops next to this README in the
bundle. The `python-mpv` binding (bundled in the application archive) is
dual-licensed GPLv2+/LGPLv2.1+.

## What is bundled, and what is not

| Distribution | Bundles libmpv? |
|--------------|-----------------|
| Linux AppImage | yes |
| Windows installer | yes |
| macOS bundle (arm64 + Intel) | yes |
| `.deb` package | no — uses the system libmpv (`libmpv2`/`libmpv1`) |
| `pip` / `pipx` install | no — uses the system libmpv |

The `.deb`, `pip`, and `pipx` installs do not contain libmpv, so the GPL
source offer below does not apply to them. Without a system libmpv those
installs still work; only the video preview shows a notice.

## Upstream build sources

The bundled libraries come from the repo-owned
[`vendor-libmpv-*` releases](https://github.com/0xzerolight/anki_miner/releases),
produced by `.github/workflows/vendor-libmpv.yml`:

- **Linux** — built from source via
  [mpv-player/mpv-build](https://github.com/mpv-player/mpv-build) with FFmpeg,
  libass, and libplacebo linked statically into a single `libmpv.so.2`.
- **Windows** — `libmpv-2.dll` mirrored from
  [zhongfly/mpv-winbuild](https://github.com/zhongfly/mpv-winbuild)
  (build scripts are public in that repository).
- **macOS (arm64 + Intel)** — libmpv and its dependency libraries from
  [Homebrew](https://brew.sh)'s `mpv` formula, made relocatable and re-signed.

Every vendor artifact carries a `SOURCES.txt` recording the exact upstream
versions, git tags/SHAs, and build inputs used; CI copies it (and mpv's
`Copyright`) into this directory in the bundle.

## Written offer of source

The complete corresponding source for the bundled libmpv is the mpv project's
own source at <https://mpv.io> / <https://github.com/mpv-player/mpv>, plus the
component sources named in `SOURCES.txt` (FFmpeg, libass, libplacebo, and the
Homebrew/zhongfly build definitions).

On request we will provide, or point you to, the exact corresponding source
for the bundled version. Open an issue at
<https://github.com/0xzerolight/anki_miner/issues> and reference the versions
listed in `SOURCES.txt`.
