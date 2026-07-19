#!/usr/bin/env bash
# Shared bundled-smoke runner — single source of truth for release.yml AND the
# local release_preflight.sh. Given a PyInstaller onedir (e.g. dist/AnkiMiner),
# run the three bundle-validation smokes the release asserts and fail closed on
# any miss. Keep this in lock-step with the smokes documented in release.yml;
# both call this script so they cannot drift.
#
# Usage: scripts/bundle_smoke.sh <dist_dir>     # e.g. dist/AnkiMiner
#
# Smokes (all headless via QT_QPA_PLATFORM=offscreen; none touch the network):
#   1. youtube   ANKI_MINER_SMOKE=youtube                  -> BUNDLED_SMOKE_PASS
#   2. asr       ANKI_MINER_SMOKE=asr  HF_HUB_OFFLINE=1     -> BUNDLED_SMOKE_PASS
#   2c. mpv      ANKI_MINER_MPV_PROBE=1                     -> MPV_PROBE_OK
#   3. ffmpeg    bundled ffmpeg has the required encoders   -> encoders present
set -euo pipefail

DIST="${1:?Usage: bundle_smoke.sh <dist_dir> (e.g. dist/AnkiMiner)}"
if [ ! -d "$DIST" ]; then
  echo "::error::dist dir not found: $DIST" >&2
  exit 2
fi

# Locate the app binary at the onedir root (AnkiMiner / AnkiMiner.exe).
APP=""
for cand in "$DIST/AnkiMiner" "$DIST/AnkiMiner.exe"; do
  [ -f "$cand" ] && APP="$cand" && break
done
if [ -z "$APP" ]; then
  echo "::error::AnkiMiner binary not found in $DIST" >&2
  ls -la "$DIST" >&2 || true
  exit 2
fi
echo "App binary: $APP"

FAILED=()

# --- 1. YouTube smoke: yt-dlp extractor registry survived PyInstaller ---------
echo "=== smoke: youtube ==="
if ANKI_MINER_SMOKE=youtube QT_QPA_PLATFORM=offscreen "$APP" 2>&1 | tee smoke_youtube.log \
  && grep -q "BUNDLED_SMOKE_PASS" smoke_youtube.log; then
  echo "PASS youtube"
else
  echo "FAIL youtube"
  FAILED+=("youtube")
fi
echo

# --- 2. ASR smoke: faster-whisper + ctranslate2 + av resolve (no download) ----
# Skipped on builds without the [asr] extra (Intel macOS: onnxruntime has no
# x86_64-mac wheel). BUNDLE_SMOKE_SKIP_ASR=1 -> skip; the bundle ships no
# faster-whisper, so the smoke would (correctly) fail.
echo "=== smoke: asr ==="
if [ "${BUNDLE_SMOKE_SKIP_ASR:-}" = "1" ]; then
  echo "SKIP asr (BUNDLE_SMOKE_SKIP_ASR=1 — build has no [asr] extra)"
elif ANKI_MINER_SMOKE=asr HF_HUB_OFFLINE=1 QT_QPA_PLATFORM=offscreen "$APP" 2>&1 | tee smoke_asr.log \
  && grep -q "BUNDLED_SMOKE_PASS" smoke_asr.log; then
  echo "PASS asr"
else
  echo "FAIL asr"
  FAILED+=("asr")
fi
echo

# --- 2b. whisper.cpp (pywhispercpp Vulkan) IMPORT/LOADABILITY gate -------------
# IMPORT/LOADABILITY ONLY. This proves the from-source Vulkan pywhispercpp wheel
# replaced the CPU wheel and was collected into the frozen tree, and that the
# bundled binary can load the ggml/whisper native chain far enough to return an
# integer Vulkan device count. It proves NOTHING about Vulkan transcription: the
# CI runners have no GPU, so the device count is expected to be 0 (CPU fallback)
# and a real GPU transcription pass can only be validated manually on hardware.
#
# Four cheap, GPU-free assertions, all fail-closed (the (c) decode is GPU-free by
# construction — it forces the CPU backend on a GPU-less runner):
#   (a) the ggml-vulkan backend MODULE is present in the bundle (libggml-vulkan*
#       on Linux / ggml-vulkan*.dll on Windows). Its presence is what makes
#       _engine.whisper_cpp_available() report a GPU-capable build; if the wheel
#       replacement or the hook collection silently failed, this lib is missing
#       and the bundle would be a CPU-only build masquerading as Vulkan.
#   (a2) the ggml-cpu backend MODULE is present (libggml-cpu* / ggml-cpu.dll). It
#       is a separate GGML_BACKEND_DL module (NOT compiled into libggml-base);
#       absent means whisper.cpp cannot fall back to CPU when no Vulkan device
#       exists (DEFECT 2). File-presence only, no Model creation.
#   (b) the frozen binary's hidden Vulkan probe (ANKI_MINER_ASR_VULKAN_PROBE=1,
#       app.main routes it to _vulkan_probe before any Qt init) runs the cold
#       ctypes load of ggml-vulkan in a child and prints the device count — a
#       single integer on stdout, exit 0. This is exactly the value
#       _engine.vulkan_device_count() parses, so a clean integer here is the
#       "isinstance(vulkan_device_count(), int)" loadability gate.
#   (c) the frozen binary actually `import pywhispercpp.model`s (via
#       ANKI_MINER_SMOKE=whispercpp -> _run_whispercpp_bundled_smoke, which calls
#       get_whisper_cpp_model_cls()). This is the REAL runtime import path the
#       Vulkan engine takes — pywhispercpp.model pulls pywhispercpp.constants
#       (-> platformdirs) and pywhispercpp.utils (-> requests, tqdm) at module
#       load. Neither (a) the filesystem find nor (b) the ctypes probe imports
#       pywhispercpp.model, so only (c) catches a transitive runtime dep missing
#       from the bundle env (e.g. platformdirs not installed) or a frozen
#       find_spec.origin failure. When BUNDLE_SMOKE_GGML_MODEL points at a ggml
#       acoustic file, (c) ALSO constructs a Model + runs a minimal CPU-backend
#       decode — the only assertion that catches DEFECT 1 (no ggml_backend_load_all
#       -> SIGABRT) and DEFECT 2's runtime effect (no CPU fallback). With no model
#       set, (c) is IMPORT/LOADABILITY ONLY — no GPU, no decode.
#
# Skipped on macOS (BOTH arm64 and Intel): macOS stays on the CT2/Metal path and
# ships no Vulkan pywhispercpp wheel, so none of these assertions apply. Set
# BUNDLE_SMOKE_SKIP_WHISPERCPP=1 on the macOS builds.
echo "=== smoke: whispercpp-vulkan (import/loadability only — NOT a GPU test) ==="
if [ "${BUNDLE_SMOKE_SKIP_WHISPERCPP:-}" = "1" ]; then
  echo "SKIP whispercpp-vulkan (BUNDLE_SMOKE_SKIP_WHISPERCPP=1 — macOS CT2/Metal build)"
else
  WHISPERCPP_OK=1
  # (a) ggml-vulkan backend MODULE present in the frozen tree.
  VK_LIB=""
  for pat in 'libggml-vulkan*.so*' 'ggml-vulkan*.dll' 'libggml-vulkan*.dylib'; do
    VK_LIB=$(find "$DIST" -type f -name "$pat" | head -1)
    [ -n "$VK_LIB" ] && break
  done
  if [ -z "$VK_LIB" ]; then
    echo "::error::ggml-vulkan backend lib not found under $DIST — Vulkan pywhispercpp wheel was not bundled (wheel replacement or hook collection failed)"
    find "$DIST" -name 'libggml*' -o -name 'ggml*.dll' 2>/dev/null | head -20 || true
    WHISPERCPP_OK=0
  else
    echo "Found ggml-vulkan backend lib: $VK_LIB"
  fi
  # (a2) ggml-cpu backend MODULE present — the CPU-fallback backend, a separate
  # GGML_BACKEND_DL module (NOT compiled into libggml-base). Required so
  # whisper.cpp can fall back to CPU when no Vulkan device exists (e.g. GPU-less
  # runners) and for non-offloaded ops. Absent = DEFECT 2 regressed (first Model
  # creation aborts / cannot fall back). File-presence only; no Model creation.
  CPU_LIB=""
  for pat in 'libggml-cpu*.so*' 'ggml-cpu*.dll' 'libggml-cpu*.dylib'; do
    CPU_LIB=$(find "$DIST" -type f -name "$pat" | head -1)
    [ -n "$CPU_LIB" ] && break
  done
  if [ -z "$CPU_LIB" ]; then
    echo "::error::ggml-cpu backend lib not found under $DIST — CPU fallback backend not bundled (DEFECT 2)"
    find "$DIST" -name 'libggml*' -o -name 'ggml*.dll' 2>/dev/null | head -20 || true
    WHISPERCPP_OK=0
  else
    echo "Found ggml-cpu backend lib: $CPU_LIB"
  fi
  # (b) frozen binary's Vulkan probe prints a single integer device count, exit 0.
  if PROBE_OUT=$(ANKI_MINER_ASR_VULKAN_PROBE=1 QT_QPA_PLATFORM=offscreen "$APP" 2>probe_err.log); then
    PROBE_OUT=$(printf '%s' "$PROBE_OUT" | tr -d '[:space:]')
    if printf '%s' "$PROBE_OUT" | grep -Eq '^[0-9]+$'; then
      echo "Vulkan device-count probe returned an integer: $PROBE_OUT (0 expected on GPU-less runners)"
    else
      echo "::error::Vulkan probe did not print a single integer device count: '$PROBE_OUT'"
      cat probe_err.log >&2 || true
      WHISPERCPP_OK=0
    fi
  else
    # The probe ALWAYS exits 0 by contract (its Python try/except prints "0" on any
    # error — an absent lib or a missing NEEDED dep raises OSError and is caught). The
    # ONE uncatchable case is a C++ abort: on a runner with the Vulkan loader but NO
    # ICD (no GPU driver — the norm for hosted CI), ggml-vulkan's get_device_count
    # calls vk::createInstance, which THROWS vk::IncompatibleDriverError ->
    # std::terminate -> SIGABRT, which ctypes/Python cannot catch, so the frozen
    # binary exits nonzero. That abort still PROVES loadability: the binary loaded
    # libggml-vulkan, resolved its symbols, and ran through libvulkan as far as
    # createInstance. The shipping app tolerates this identically — vulkan_device_count()
    # runs this probe as a subprocess and treats a nonzero exit as 0 devices (CPU
    # fallback). So an IncompatibleDriver abort is the GPU-less loadability-proven
    # outcome; any OTHER nonzero exit is a genuine load failure and still fails closed.
    if grep -qiE 'IncompatibleDriver|VK_ERROR_INCOMPATIBLE_DRIVER' probe_err.log; then
      echo "Vulkan probe aborted with IncompatibleDriver (loader present, no ICD on this GPU-less runner) — loadability proven, 0 devices"
    else
      echo "::error::Vulkan probe exited nonzero for a non-driver reason (frozen binary could not load the ASR/ggml chain)"
      cat probe_err.log >&2 || true
      WHISPERCPP_OK=0
    fi
  fi
  # (c) frozen binary imports pywhispercpp.model — the REAL runtime import chain
  # (pulls platformdirs/requests/tqdm) — AND, when a ggml acoustic model is
  # available via BUNDLE_SMOKE_GGML_MODEL, constructs a pywhispercpp Model +
  # runs a minimal CPU-backend decode. The construct+decode is what catches
  # DEFECT 1 (ggml_backend_load_all never called -> SIGABRT on first Model) and
  # DEFECT 2 (libggml-cpu not bundled -> no CPU fallback when no Vulkan device).
  # On a GPU-less runner the Model MUST fall back to the CPU backend and decode
  # without aborting; a SIGABRT (even IncompatibleDriver) is a FAIL here — unlike
  # the (b) probe, the decode has NO tolerated-abort case. With no model set the
  # decode is skipped (import/loadability only, as before) so CI stays green when
  # the release job ships no ggml model. The env array is empty (a safe no-op
  # prefix) when no model is provided.
  WHISPERCPP_MODEL_ENV=()
  if [ -n "${BUNDLE_SMOKE_GGML_MODEL:-}" ] && [ -f "${BUNDLE_SMOKE_GGML_MODEL}" ]; then
    echo "Using ggml model for construct+decode smoke: ${BUNDLE_SMOKE_GGML_MODEL}"
    WHISPERCPP_MODEL_ENV=(ANKI_MINER_SMOKE_GGML_MODEL="${BUNDLE_SMOKE_GGML_MODEL}")
  else
    echo "No BUNDLE_SMOKE_GGML_MODEL set — whispercpp smoke runs import-only (no decode)"
  fi
  if env "${WHISPERCPP_MODEL_ENV[@]}" ANKI_MINER_SMOKE=whispercpp QT_QPA_PLATFORM=offscreen "$APP" 2>&1 | tee smoke_whispercpp.log \
    && grep -q "BUNDLED_SMOKE_PASS" smoke_whispercpp.log; then
    echo "pywhispercpp.model resolved in the frozen bundle (import/construct/decode)"
  else
    echo "::error::pywhispercpp.model failed in the frozen bundle (import/construct/decode) — DEFECT 1/2 regressed or a transitive dep is missing (e.g. platformdirs, or find_spec.origin failed)"
    cat smoke_whispercpp.log >&2 || true
    WHISPERCPP_OK=0
  fi
  if [ "$WHISPERCPP_OK" = "1" ]; then
    echo "BUNDLED_WHISPERCPP_VULKAN_LOADABLE_PASS"
    echo "PASS whispercpp-vulkan"
  else
    echo "FAIL whispercpp-vulkan"
    FAILED+=("whispercpp-vulkan")
  fi
fi
echo

# --- 2c. libmpv smoke: bundled shared library present AND loadable -------------
# (a) file presence catches a vendor-fetch/spec-glob silent miss; (b) the frozen
# probe (ANKI_MINER_MPV_PROBE=1, routed in app.main before Qt init) dlopens the
# bundled libmpv through mpv_loader's resolution order and constructs a
# display-free core (vo=null/ao=null) — proving the library AND its dependency
# closure resolve inside the frozen tree (Linux loader path, macOS @loader_path,
# Windows add_dll_directory). Skippable per-leg via BUNDLE_SMOKE_SKIP_MPV=1.
echo "=== smoke: mpv (libmpv presence + frozen dlopen probe) ==="
if [ "${BUNDLE_SMOKE_SKIP_MPV:-}" = "1" ]; then
  echo "SKIP mpv (BUNDLE_SMOKE_SKIP_MPV=1)"
else
  LIBMPV=""
  for pat in 'libmpv-2.dll' 'mpv-2.dll' 'libmpv.so.2*' 'libmpv.2.dylib'; do
    LIBMPV=$(find "$DIST" -type f -name "$pat" | head -1)
    [ -n "$LIBMPV" ] && break
  done
  if [ -z "$LIBMPV" ]; then
    echo "::error::Bundled libmpv not found under $DIST — vendor fetch or spec glob missed"
    find "$DIST" -maxdepth 3 -name '*mpv*' || true
    FAILED+=("mpv")
  else
    mpv_ok=1
    # Windows: libmpv-2.dll has a LOAD-TIME import on vulkan-1.dll. windows-latest
    # resolves it from System32, so the frozen probe below passes even when it is
    # NOT bundled — the probe can't catch a dropped vulkan-1.dll. Assert it ships
    # in the tree so a bare machine (no Vulkan driver) can still load libmpv.
    case "$LIBMPV" in
      *-2.dll)
        if find "$DIST" -type f -name 'vulkan-1.dll' | grep -q .; then
          echo "Found bundled Vulkan loader (libmpv-2.dll load-time dep)"
        else
          echo "::error::vulkan-1.dll not bundled next to $LIBMPV — libmpv fails to load on machines without a Vulkan driver"
          mpv_ok=0
        fi
        ;;
    esac
    if ANKI_MINER_MPV_PROBE=1 QT_QPA_PLATFORM=offscreen "$APP" 2>&1 | tee smoke_mpv.log \
      && grep -q "MPV_PROBE_OK" smoke_mpv.log; then
      echo "Found bundled libmpv: $LIBMPV"
      # Informational: surface any host-only lib creep next to the bundled libmpv
      # (closure changes show up here in the log before they bite users).
      find "$DIST" -maxdepth 2 -name '*.so*' 2>/dev/null | grep -E 'asound|pulse|jack|pipewire|libGL|wayland' || true
    else
      mpv_ok=0
    fi
    if [ "$mpv_ok" = 1 ]; then
      echo "BUNDLED_MPV_PASS"
      echo "PASS mpv"
    else
      echo "FAIL mpv"
      FAILED+=("mpv")
    fi
  fi
fi
echo

# --- 3. ffmpeg encoder smoke: bundled ffmpeg ships the required encoders -------
# libwebp_anim is asserted separately because still-image libwebp builds would
# otherwise pass while animated-WebP screenshots fail at runtime.
# The required set is overridable via BUNDLE_SMOKE_FFMPEG_ENCODERS (space-
# separated). Default is the full set. Intel macOS drops libsvtav1: evermeet.cx
# x86_64 ffmpeg ships no SVT-AV1, and the app degrades gracefully (AVIF animated
# screenshots fall back / report unavailable; WebP animated + static still work).
REQUIRED_ENCODERS="${BUNDLE_SMOKE_FFMPEG_ENCODERS:-libmp3lame libopus libsvtav1 libwebp libwebp_anim}"
echo "=== smoke: ffmpeg encoders (required: $REQUIRED_ENCODERS) ==="
FF=""
for name in ffmpeg ffmpeg.exe; do
  FF=$(find "$DIST" -type f -name "$name" | head -1)
  [ -n "$FF" ] && break
done
if [ -z "$FF" ]; then
  echo "::error::Bundled ffmpeg not found under $DIST — spec did not bundle vendor/ffmpeg/"
  find "$DIST" -maxdepth 3 -name 'ffmpeg*' || true
  FAILED+=("ffmpeg-encoders")
else
  echo "Found bundled ffmpeg: $FF"
  chmod +x "$FF" 2>/dev/null || true
  ENC=$("$FF" -hide_banner -encoders 2>/dev/null || true)
  MISSING=""
  for e in $REQUIRED_ENCODERS; do
    echo "$ENC" | grep -q "$e" || MISSING="$MISSING $e"
  done
  if [ -n "$MISSING" ]; then
    echo "::error::Bundled ffmpeg is missing required encoder(s):$MISSING"
    FAILED+=("ffmpeg-encoders")
  else
    echo "BUNDLED_FFMPEG_ENCODERS_PASS: $REQUIRED_ENCODERS"
    echo "PASS ffmpeg-encoders"
  fi
fi
echo

# --- summary ------------------------------------------------------------------
if [ ${#FAILED[@]} -gt 0 ]; then
  echo "BUNDLE_SMOKE_FAILED: ${FAILED[*]}"
  exit 1
fi
echo "BUNDLE_SMOKE_ALL_PASS"
