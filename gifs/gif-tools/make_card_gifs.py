"""Generate frame-perfect, mutually-synced demo GIFs of live Anki cards.

Pulls rendered card HTML/CSS straight from a running Anki via AnkiConnect,
serves it from a local HTTP origin backed by AnkiConnect ``retrieveMediaFile``,
renders each card in a headless Chromium (Playwright) on a neutral background,
and records the card BACK (answer) for a fixed duration, then loops. Every
output GIF has the identical fps and total frame count, so they stay in phase
when looped side-by-side in the README. The card grows to its natural content
height so a long definition is shown in full; the vertical auto-crop tightens
the neutral margins afterwards.

Why a local HTTP server (not data-URI inlining):
    Real card templates (e.g. Senren) are JS-driven: they read settings from
    ``localStorage`` -- denied on the opaque origin of ``set_content`` / data
    URIs -- and they fetch assets (preset scripts, fonts, CSS) *dynamically* at
    runtime, so those filenames aren't known up front. Serving from
    ``http://localhost`` gives a real origin (localStorage works) and a catch-all
    handler resolves ANY requested file on demand via ``retrieveMediaFile``,
    including the dynamic ones. Animated AVIF Picture fields are served raw --
    Chromium decodes + animates AVIF natively (the same engine Anki uses); do
    NOT transcode via ffmpeg, whose AVIF decoder reads only the primary still.

Why real-time video capture (not a screenshot loop):
    The animated screenshot must play at 1x. A screenshot loop captures slower
    than wall-clock, so each frame jumps multiple animation frames -> playback
    runs fast. Instead the back is recorded as a real-time Playwright webm
    (browser plays the AVIF at 1x, screencast records at 1x); after settle the
    AVIF is restarted to frame 0 and exactly ``total_frames`` are extracted at the
    target fps anchored to that restart. Frame 0 == the scene start, so the muxed
    sentence audio (delay 0) plays in phase with the first loop, then the AVIF
    keeps looping to the fixed end. Real-time motion AND an identical frame count
    across cards keep them in phase when looped side-by-side.

Prerequisite:
    Re-mine the demo cards with ANIMATED SCREENSHOTS and PITCH ACCENT enabled
    first, so the live cards reflect current features. By default this script
    pulls the cards in ``deck:gifs``; pass explicit card IDs to override.

Usage:
    python3 gifs/gif-tools/make_card_gifs.py --names cowboy_bebop frieren 'steins;gate'

Optional, unofficial tooling -- not part of the shipped ``anki_miner`` package.
Run it from a cloned repo: it imports ``anki_miner.services._ankiconnect`` to talk
to AnkiConnect, so the package must be importable (``pip install -e .[dev]``).

Dependencies (none of the extra pip deps are in the project's ``[dev]`` extras --
install them yourself):
    * ffmpeg + ffprobe on PATH       -- encode/probe (already a project requirement)
    * a running Anki with AnkiConnect on :8765  -- source of the live cards
    * ``pip install playwright`` then ``playwright install chromium`` (~120 MB)
                                     -- headless render + real-time screencast
    * ``pip install pillow``         -- pixel-scan content-box measurement for the
                                        tight auto-crop (see ``measure_content_bbox``)

``playwright`` and ``pillow`` are imported lazily inside ``generate()`` so
``--help`` works without them.
"""

from __future__ import annotations

import argparse
import base64
import logging
import re
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

# Allow importing sibling helper module + the anki_miner package.
# This script lives in gifs/gif-tools/, so the repo root is three levels up.
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from _demo_encode import (  # noqa: E402
    encode_gif,
    encode_mp4,
    ensure_tools,
    human_size,
    probe,
    run,
)

from anki_miner.services._ankiconnect import post_action  # noqa: E402

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CARD_STYLES = REPO_ROOT / "anki_miner/services/dictionary/resources/default_card_styles.css"

# Audio leftovers to strip from a silent demo.
_SOUND_LITERAL_RE = re.compile(r"\[(?:sound|anki:play):[^\]]*\]", re.IGNORECASE)
_REPLAY_ANCHOR_RE = re.compile(
    r"<a\b[^>]*class=[\"'][^\"']*(?:replay-button|soundLink|sound-link)[^\"']*[\"'][^>]*>.*?</a>",
    re.IGNORECASE | re.DOTALL,
)

_CONTENT_TYPE_BY_EXT = {
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".css": "text/css",
    ".json": "application/json",
    ".html": "text/html; charset=utf-8",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".avif": "image/avif",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".ttf": "font/ttf",
    ".otf": "font/otf",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
}


def strip_audio(html: str) -> str:
    """Remove sound literals and replay-button anchors (silent demo)."""
    html = _REPLAY_ANCHOR_RE.sub("", html)
    html = _SOUND_LITERAL_RE.sub("", html)
    return html


# ---------------------------------------------------------------------------
# AnkiConnect
# ---------------------------------------------------------------------------


def resolve_card_ids(url: str, ids: list[int], query: str) -> list[int]:
    """Return explicit card IDs, or look them up via a deck query."""
    if ids:
        return ids
    found = post_action(url, "findCards", {"query": query}) or []
    logger.info("findCards '%s' -> %d card(s)", query, len(found))
    return list(found)


_HTML_TAG_RE = re.compile(r"<[^>]+>")
_UNSAFE_NAME_RE = re.compile(r"[/\\\x00]")
_HEADWORD_FIELDS = ("word", "expression")  # Senren / Lapis (matched case-insensitively)


def derive_card_name(card: dict) -> str:
    """Output basename from the card's headword field (Senren ``word`` / Lapis
    ``Expression``), HTML-stripped and path-sanitized; cardId if unusable."""
    fields = card.get("fields", {})
    value = next((fields[n]["value"] for n in fields if n.lower() in _HEADWORD_FIELDS), "")
    name = _UNSAFE_NAME_RE.sub("_", _HTML_TAG_RE.sub("", value)).strip()
    return name or str(card.get("cardId", "card"))


def dedupe_names(names: list[str]) -> list[str]:
    """Append ``_2``, ``_3``, … to later collisions so filenames stay unique."""
    seen: dict[str, int] = {}
    out: list[str] = []
    for n in names:
        seen[n] = seen.get(n, 0) + 1
        out.append(n if seen[n] == 1 else f"{n}_{seen[n]}")
    return out


def fetch_cards(url: str, ids: list[int]) -> list[dict]:
    """Fetch rendered card info (question/answer/css) for each card ID."""
    info = post_action(url, "cardsInfo", {"cards": ids}) or []
    if len(info) != len(ids):
        raise RuntimeError(f"cardsInfo returned {len(info)} cards for {len(ids)} IDs")
    return info


def _sentence_audio_value(card: dict) -> str:
    """Return the card's sentence-audio field value, matching the field name
    case-insensitively (Senren ``sentenceAudio`` vs Lapis ``SentenceAudio``)."""
    for name, field in card.get("fields", {}).items():
        if name.lower() == "sentenceaudio":
            return field.get("value", "")
    return ""


def fetch_audio(url: str, card: dict, dest_dir: Path) -> Path | None:
    """Fetch the card's sentence audio file (for the MP4 soundtrack).

    Returns the local path, or None if the card has no sentence-audio field.
    """
    value = _sentence_audio_value(card)
    m = re.search(r"\[sound:([^\]]+)\]", value)
    if not m:
        return None
    filename = m.group(1)
    raw_b64 = post_action(url, "retrieveMediaFile", {"filename": filename})
    if not raw_b64:
        logger.warning("sentence audio not found in collection: %s", filename)
        return None
    out = dest_dir / filename
    out.write_bytes(base64.b64decode(raw_b64))
    return out


# ---------------------------------------------------------------------------
# Local media-serving HTTP origin (backed by AnkiConnect)
# ---------------------------------------------------------------------------


class MediaResolver:
    """Fetch + cache collection media by filename via ``retrieveMediaFile``.

    Bytes are served raw, including animated AVIF Picture fields: headless
    Chromium decodes + animates AVIF natively (the same engine Anki's webview
    uses). Do NOT transcode AVIF via ffmpeg -- ffmpeg's AVIF decoder extracts
    only the primary still, which freezes the animation on frame 1. Results are
    cached (shared across all cards) to limit AnkiConnect
    round-trips for reused assets (fonts, preset scripts, icons).
    """

    def __init__(self, url: str) -> None:
        self._url = url
        self._cache: dict[str, tuple[bytes, str] | None] = {}
        self._lock = threading.Lock()

    def get(self, filename: str) -> tuple[bytes, str] | None:
        with self._lock:
            if filename in self._cache:
                return self._cache[filename]
        raw_b64 = post_action(self._url, "retrieveMediaFile", {"filename": filename})
        result: tuple[bytes, str] | None
        if not raw_b64:
            result = None
        else:
            raw = base64.b64decode(raw_b64)
            ctype = _CONTENT_TYPE_BY_EXT.get(Path(filename).suffix.lower(), "application/octet-stream")
            result = (raw, ctype)
        with self._lock:
            self._cache[filename] = result
        return result


def make_handler(docs: dict[str, str], resolver: MediaResolver):
    """Build a request handler serving in-memory docs + on-demand media."""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:  # noqa: D401 - silence default stderr spam
            logger.debug("http: " + args[0], *args[1:])

        def _send(self, body: bytes, ctype: str, code: int = 200) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            path = unquote(urlparse(self.path).path)
            if path in docs:
                self._send(docs[path].encode("utf-8"), "text/html; charset=utf-8")
                return
            filename = path.lstrip("/")
            media = resolver.get(filename)
            if media is None:
                self._send(b"not found", "text/plain", 404)
                return
            body, ctype = media
            self._send(body, ctype)

    return Handler


# ---------------------------------------------------------------------------
# HTML document (one card side)
# ---------------------------------------------------------------------------


def build_doc(*, css: str, content: str, bg: str, night: bool, pad: int) -> str:
    """Wrap one rendered card side into a self-contained HTML doc.

    Built by concatenation (NOT str.format): the injected CSS is full of literal
    ``{`` / ``}`` braces. ``html``/``body`` background is forced so the neutral
    stage shows through the card's margin instead of the template's page colour.
    """
    card_styles = CARD_STYLES.read_text(encoding="utf-8") if CARD_STYLES.exists() else ""
    card_class = "card nightMode night_mode" if night else "card"
    body_class = "nightMode night_mode" if night else ""
    # Force the card to the stage WIDTH so all cards share one width, but let it
    # grow to its natural content HEIGHT -- the back's full definition must not be
    # clipped (no fixed height / overflow:hidden). The viewport is sized tall
    # enough to contain the tallest card; the vertical auto-crop trims the rest.
    # `!important` overrides the template's content-driven width.
    wrapper_css = (
        f"html,body{{margin:0;padding:0;width:100%;height:100%;background:{bg} !important;}}"
        "body{display:flex;align-items:center;justify-content:center;overflow:hidden;}"
        "#stage{display:flex;align-items:center;justify-content:center;"
        f"width:100%;height:100%;box-sizing:border-box;padding:{pad}px;}}"
        "#stage .card{width:100% !important;max-width:100% !important;box-sizing:border-box;"
        "display:flex !important;flex-direction:column;}"
    )
    return "".join(
        [
            "<!doctype html><html><head><meta charset='utf-8'>",
            "<style>",
            css or "",
            "</style><style>",
            card_styles,
            "</style><style>",
            wrapper_css,
            "</style></head>",
            f"<body class='{body_class}'><div id='stage'>",
            f"<div class='{card_class}'>",
            content,
            "</div></div></body></html>",
        ]
    )


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------


def record_side(
    browser, url: str, settle_ms: int, hold_secs: float, width: int, height: int, scale: int, video_dir: Path
):
    """Record a real-time webm of one card side; return the webm Path.

    A fresh context records the screencast at wall-clock 1x while the browser
    plays the animated AVIF at 1x -- this is what makes playback real-time.

    After ``settle_ms`` warm-up the animated AVIF(s) are RESTARTED to animation
    frame 0 (each ``<img>`` is replaced by a fresh clone), then the page is held
    ``hold_secs`` more. This pins a KNOWN phase: the steady window can be anchored
    to the restart instant so the extracted frame 0 == the AVIF scene start. The
    seamless AVIF loop hides the scene-start otherwise, so it cannot be recovered
    from pixels post-hoc -- it must be set at capture time. Recorded at ``*scale``
    for crisp downscaling.
    """
    # record_video_size MUST equal the viewport: Playwright places the page at
    # 1:1 in the top-left of a larger canvas (it does not stretch to fill), so a
    # bigger size would leave the card in a corner. device_scale_factor still
    # renders internally at 2x and downsamples into the canvas for crisp text.
    ctx = browser.new_context(
        viewport={"width": width, "height": height},
        device_scale_factor=scale,
        record_video_dir=str(video_dir),
        record_video_size={"width": width, "height": height},
    )
    page = ctx.new_page()
    page.goto(url, wait_until="load")
    page.wait_for_timeout(settle_ms)
    # Restart every animated AVIF to frame 0 right before the hold. replaceWith a
    # synchronous clone -- no blank frame (the bytes are already cached on the
    # local origin, so the re-fetch is instant). This is the t0 the extraction
    # window anchors to; see extract_frames.
    page.evaluate(
        "() => { for (const img of document.querySelectorAll('img')) {"
        "   if (/\\.avif(\\?|$)/i.test(img.src)) img.replaceWith(img.cloneNode(true)); } }"
    )
    page.wait_for_timeout(int(hold_secs * 1000))
    video = page.video
    ctx.close()  # flushes the webm to disk
    return Path(video.path())


def extract_frames(
    webm: Path,
    frames_dir: Path,
    base_index: int,
    n_frames: int,
    fps: int,
    width: int,
    side_secs: float,
    smooth: bool,
) -> None:
    """Extract exactly ``n_frames`` real-time frames anchored to the AVIF restart.

    ``record_side`` restarts the AVIF to frame 0, then holds ``side_secs + 0.5``,
    so EOF == restart + side_secs + 0.5 (+ a little teardown flush). Seeking
    ``side_secs + 0.35`` before EOF lands the window ~0.15s after the restart
    (skips restart-decode jank) with ~0.35s of teardown kept past the window end.
    Frame 0 of the output therefore == the AVIF scene start, so the muxed sentence
    audio (delay 0) plays in phase with the first animation loop. Resamples to
    ``fps``, scales to ``width``, keeps the first ``n_frames``, numbered from
    ``base_index``.

    This headless environment captures the 20fps source AVIF at only ~6.5fps, so
    with ``smooth`` the frames are motion-interpolated up to ``fps`` (ffmpeg
    ``minterpolate``, motion-compensated) for fluid playback; otherwise the
    captured frames are plain-resampled (may judder).
    """
    rate = f"minterpolate=fps={fps}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1" if smooth else f"fps={fps}"
    run(
        [
            "ffmpeg",
            "-y",
            "-sseof",
            # 0.35 = hold margin (0.5) minus the ~0.15s lead past the restart.
            f"-{side_secs + 0.35}",
            "-i",
            str(webm),
            "-vf",
            f"{rate},scale={width}:-2:flags=lanczos",
            "-frames:v",
            str(n_frames),
            "-start_number",
            str(base_index),
            str(frames_dir / "f_%04d.png"),
        ]
    )


def assemble(
    frames_dir: Path, out_mp4: Path, fps: int, width: int, crop: tuple[int, int, int, int] | None = None
) -> None:
    """Encode the PNG frame sequence into a normalized intermediate MP4.

    With ``crop=(x, y, w, h)`` the frames are tightly cropped to the card's
    measured content box; the crop defines the output dimensions, so the
    width-normalizing ``scale`` is dropped (only the luma sharpen remains).
    Without it, frames are scaled to ``width`` as before (full-frame fallback).
    """
    if crop is not None:
        x, y, w, h = crop
        # crop first, then sharpen; w/h are even so yuv420p is satisfied
        vf = f"crop={w}:{h}:{x}:{y},unsharp=3:3:0.6:3:3:0.0"
    else:
        # mild luma sharpen to counter VP8 capture softness, then ensure width
        vf = f"unsharp=3:3:0.6:3:3:0.0,scale={width}:-2:flags=lanczos"
    run(
        [
            "ffmpeg",
            "-y",
            "-framerate",
            str(fps),
            "-i",
            str(frames_dir / "f_%04d.png"),
            "-vf",
            vf,
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-crf",
            "18",
            str(out_mp4),
        ]
    )


# ---------------------------------------------------------------------------
# Content-bbox detection (tight auto-crop)
# ---------------------------------------------------------------------------


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    """Parse a ``#rrggbb`` stage colour into an (r, g, b) tuple for detection."""
    h = color.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _frame_bbox(px, w: int, h: int, bg_rgb: tuple[int, int, int], tol: int) -> tuple[int, int, int, int] | None:
    """Bounding box of pixels differing from ``bg_rgb`` by > ``tol`` on any channel.

    Scans every other row/column (2px step) for speed. Returns ``(l, t, r, b)``
    inclusive, or ``None`` if the frame is entirely background.
    """
    br, bgc, bb = bg_rgb

    def off(x: int, y: int) -> bool:
        p = px[x, y]
        return abs(p[0] - br) > tol or abs(p[1] - bgc) > tol or abs(p[2] - bb) > tol

    cols = [x for x in range(0, w, 2) if any(off(x, y) for y in range(0, h, 2))]
    if not cols:
        return None
    rows = [y for y in range(0, h, 2) if any(off(x, y) for x in cols)]
    return min(cols), min(rows), max(cols), max(rows)


def measure_content_bbox(
    frames_dir: Path, sample_indices: list[int], bg_rgb: tuple[int, int, int], tol: int
) -> tuple[int, int, int, int]:
    """Union the content bounding box across several back-frame PNGs.

    ``cropdetect`` is unreliable here -- a dark screenshot edge blends into the
    neutral stage and it reports no crop. Instead each sampled frame is scanned
    pixel-wise for anything that differs from the *known* stage ``bg_rgb`` by
    more than ``tol`` on any channel; the bounding box is unioned across frames
    because the animated AVIF shifts pixels inside the screenshot (a single
    frame can under-detect a momentarily-dark region). Returns
    ``(left, top, right, bot)`` inclusive pixel coords.
    """
    from PIL import Image  # local import: dev-only dep (like playwright)

    lo_x = lo_y = 1 << 30
    hi_x = hi_y = -1
    for idx in sample_indices:
        png = frames_dir / f"f_{idx:04d}.png"
        if not png.exists():
            continue
        im = Image.open(png).convert("RGB")
        bbox = _frame_bbox(im.load(), im.width, im.height, bg_rgb, tol)
        if bbox is None:
            continue  # blank frame, nothing differs from bg
        left, top, right, bot = bbox
        lo_x, hi_x = min(lo_x, left), max(hi_x, right)
        lo_y, hi_y = min(lo_y, top), max(hi_y, bot)

    if hi_x < 0:
        raise RuntimeError("content-bbox detection found no non-background pixels (check --bg / --bg-tol)")
    return lo_x, lo_y, hi_x, hi_y


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def generate(args: argparse.Namespace) -> int:
    ensure_tools()
    from playwright.sync_api import sync_playwright  # local import: dev-only dep

    total_frames = round(args.secs * args.fps)
    logger.info("timeline: %d frames @ %dfps (back only, %.1fs)", total_frames, args.fps, args.secs)

    card_ids = resolve_card_ids(args.ankiconnect_url, args.card_ids, args.query)
    if args.names and len(card_ids) != len(args.names):
        raise RuntimeError(
            f"{len(card_ids)} card(s) but {len(args.names)} name(s): "
            f"pass --names with one name per card (cards: {card_ids})"
        )
    cards = fetch_cards(args.ankiconnect_url, card_ids)
    names = list(args.names) if args.names else dedupe_names([derive_card_name(c) for c in cards])
    logger.info("output names: %s", ", ".join(names))

    # Build one back doc per card; serve them all from one origin.
    docs: dict[str, str] = {}
    night = not args.no_night
    for idx, card in enumerate(cards):
        back = strip_audio(card["answer"])
        docs[f"/c{idx}_back.html"] = build_doc(
            css=card.get("css", ""), content=back, bg=args.bg, night=night, pad=args.pad
        )

    resolver = MediaResolver(args.ankiconnect_url)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(docs, resolver))
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    logger.info("serving cards on http://127.0.0.1:%d", port)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []

    # Sample a few frames spread across the steady window for bbox detection.
    back_samples = sorted({1, total_frames // 2, total_frames - 2})

    # capture+measure (pass 1) keeps every card's frames on disk until the
    # uniform crop is known, so one parent temp dir spans both passes.
    with tempfile.TemporaryDirectory() as parent_td:
        parent = Path(parent_td)
        records: list[dict] = []  # {name, card, dir, frames_dir, bbox|None}
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch()
                try:
                    # -- Pass 1: capture the back, extract frames, measure bbox --
                    for idx, name in enumerate(names):
                        logger.info("rendering %s (card %s)", name, cards[idx].get("cardId"))
                        card_dir = parent / f"card{idx}"
                        frames_dir = card_dir / "frames"
                        frames_dir.mkdir(parents=True)
                        vdir = card_dir / "vid_back"
                        vdir.mkdir()
                        url = f"http://127.0.0.1:{port}/c{idx}_back.html"
                        # hold = secs + 0.5 PAST the AVIF restart: secs of usable
                        # footage + a teardown margin the extraction window trims.
                        webm = record_side(
                            browser, url, args.settle_ms, args.secs + 0.5, args.width, args.height, args.scale, vdir
                        )
                        extract_frames(webm, frames_dir, 0, total_frames, args.fps, args.width, args.secs, args.smooth)
                        bbox = None
                        if args.autocrop:
                            bbox = measure_content_bbox(frames_dir, back_samples, _hex_to_rgb(args.bg), args.bg_tol)
                            logger.info(
                                "  content bbox: %dx%d at x[%d..%d] y[%d..%d]",
                                bbox[2] - bbox[0] + 1,
                                bbox[3] - bbox[1] + 1,
                                bbox[0],
                                bbox[2],
                                bbox[1],
                                bbox[3],
                            )
                        records.append(
                            {"name": name, "card": cards[idx], "dir": card_dir, "frames_dir": frames_dir, "bbox": bbox}
                        )
                finally:
                    browser.close()
        finally:
            server.shutdown()

        # -- Compute one uniform crop box from the largest card's content --
        crops: dict[str, tuple[int, int, int, int] | None] = {r["name"]: None for r in records}
        if args.autocrop:
            pad = args.crop_pad
            max_w = max(r["bbox"][2] - r["bbox"][0] + 1 for r in records)
            max_h = max(r["bbox"][3] - r["bbox"][1] + 1 for r in records)
            box_w = min(args.width, max_w + 2 * pad)
            box_h = min(args.height, max_h + 2 * pad)
            box_w -= box_w & 1  # even dims for yuv420p (round down stays in frame)
            box_h -= box_h & 1
            for r in records:
                left, top, right, bot = r["bbox"]
                cx, cy = (left + right) // 2, (top + bot) // 2
                x = max(0, min(args.width - box_w, round(cx - box_w / 2)))
                y = max(0, min(args.height - box_h, round(cy - box_h / 2)))
                x -= x & 1
                y -= y & 1
                crops[r["name"]] = (x, y, box_w, box_h)
            logger.info("uniform crop: %dx%d (pad %d) applied to all %d cards", box_w, box_h, pad, len(records))

        # -- Pass 2: assemble (crop) + encode each card --
        for r in records:
            name = r["name"]
            frames_dir = r["frames_dir"]
            crop = crops[name]
            out_w = crop[2] if crop else args.width
            norm = frames_dir / "normalized.mp4"
            assemble(frames_dir, norm, args.fps, args.width, crop=crop)
            if args.gif:
                gif = out_dir / f"{name}.gif"
                encode_gif(norm, gif, args.fps, out_w, args.max_colors)
                outputs.append(gif)
                logger.info("  wrote %s (%s)", gif, human_size(gif))
            if args.mp4:
                mp4 = out_dir / f"{name}.mp4"
                audio = fetch_audio(args.ankiconnect_url, r["card"], r["dir"]) if args.audio else None
                encode_mp4(norm, mp4, audio_path=audio, audio_delay_secs=0.0)
                kind = "with audio" if audio else "silent"
                logger.info("  wrote %s (%s, %s)", mp4, human_size(mp4), kind)

    return _verify_sync(outputs)


def _dimensions(path: Path) -> tuple[int, int]:
    """Return (width, height) of an image/GIF via PIL."""
    from PIL import Image  # local import: dev-only dep

    with Image.open(path) as im:
        return im.size


def _verify_sync(gifs: list[Path]) -> int:
    """Fail loudly unless every GIF shares duration, frame count, and dimensions."""
    if len(gifs) < 2:
        return 0
    stats = {g: probe(g) for g in gifs}
    dims = {g: _dimensions(g) for g in gifs}
    durations = {round(d, 2) for d, _ in stats.values()}
    frames = {f for _, f in stats.values()}
    sizes = set(dims.values())
    for g, (d, f) in stats.items():
        logger.info("sync-check %s: %.2fs, %d frames, %dx%d", g.name, d, f, *dims[g])
    if len(durations) != 1 or len(frames) != 1 or len(sizes) != 1:
        logger.error("SYNC MISMATCH: durations=%s frames=%s sizes=%s", durations, frames, sizes)
        return 1
    logger.info(
        "sync OK: all %d GIFs share %s frames / %ss / %dx%d",
        len(gifs),
        frames.pop(),
        durations.pop(),
        *sizes.pop(),
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("card_ids", nargs="*", type=int, help="Explicit card IDs (default: query deck:gifs)")
    p.add_argument("--query", default="deck:gifs", help="findCards query when no IDs given")
    p.add_argument(
        "--names",
        nargs="+",
        default=None,
        help="Output basename per card, in order; omit to auto-name from the headword field",
    )
    p.add_argument("--out-dir", default=str(REPO_ROOT / "gifs"), help="Output dir (default: repo gifs/)")
    p.add_argument("--ankiconnect-url", default="http://127.0.0.1:8765")
    p.add_argument("--secs", type=float, default=6.0, help="Clip length in seconds (back only)")
    p.add_argument("--fps", type=int, default=20)
    p.add_argument("--width", type=int, default=994)
    p.add_argument(
        "--height", type=int, default=1000, help="Capture viewport height (tall enough for the longest card)"
    )
    p.add_argument("--scale", type=int, default=2, help="device_scale_factor (supersample, then downscale)")
    p.add_argument("--pad", type=int, default=6, help="Neutral border px around the card (pre-render stage)")
    p.add_argument("--crop-pad", type=int, default=8, help="Margin px left around detected content when auto-cropping")
    p.add_argument("--bg-tol", type=int, default=18, help="Per-channel colour tolerance for content detection")
    p.add_argument(
        "--no-autocrop",
        dest="autocrop",
        action="store_false",
        help="Skip the tight content crop (keep the full --width x --height frame)",
    )
    p.add_argument("--max-colors", type=int, default=256, help="GIF palette size (lower = smaller file)")
    p.add_argument("--bg", default="#1e1e2e", help="Stage background colour")
    p.add_argument("--settle-ms", type=int, default=1500, help="Delay after load before capture")
    p.add_argument(
        "--no-smooth",
        dest="smooth",
        action="store_false",
        help="Disable motion interpolation (raw captured frames; may judder)",
    )
    p.add_argument("--no-night", action="store_true", help="Disable nightMode card classes")
    p.add_argument("--no-gif", dest="gif", action="store_false")
    p.add_argument("--no-mp4", dest="mp4", action="store_false")
    p.add_argument("--no-audio", dest="audio", action="store_false", help="Do not mux sentence audio into the MP4")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )
    try:
        return generate(args)
    except Exception as e:  # noqa: BLE001 - top-level CLI guard
        logger.error("%s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
