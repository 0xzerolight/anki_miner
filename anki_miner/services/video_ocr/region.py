"""The subtitle region and its pixel geometry. Pure and numpy-free: safe to import at GUI startup."""

from __future__ import annotations

from dataclasses import dataclass

MAX_SAMPLE_WIDTH = 1920
MIN_REGION_PX = 16


@dataclass(frozen=True)
class Region:
    """The subtitle area as fractions of the displayed frame."""

    x: float
    y: float
    w: float
    h: float

    @classmethod
    def from_config(cls, values: tuple[float, ...]) -> Region | None:
        return cls(*values) if len(values) == 4 else None

    def as_config(self) -> tuple[float, float, float, float]:
        return (round(self.x, 4), round(self.y, 4), round(self.w, 4), round(self.h, 4))


def crop_box(region: Region, size: tuple[int, int]) -> tuple[int, int, int, int]:
    """Pixel crop (x, y, w, h) for ``region`` on a ``size`` frame: even, at least MIN_REGION_PX, inside."""
    frame_w, frame_h = size
    w = min(frame_w, max(MIN_REGION_PX, round(region.w * frame_w)))
    h = min(frame_h, max(MIN_REGION_PX, round(region.h * frame_h)))
    w -= w % 2
    h -= h % 2
    x = min(max(0, round(region.x * frame_w)), frame_w - w)
    y = min(max(0, round(region.y * frame_h)), frame_h - h)
    # Even offsets too: ffmpeg's crop on 4:2:0 video offsets the chroma planes by
    # floor(x/2), floor(y/2), so an odd offset shifts colour one pixel against the
    # still the dialog crops in numpy, and "Test this frame" would read other pixels.
    x -= x % 2
    y -= y % 2
    return x, y, w, h


def sample_size(crop_w: int, crop_h: int) -> tuple[int, int]:
    """The size samples are delivered at: the crop itself, capped at MAX_SAMPLE_WIDTH wide."""
    if crop_w <= MAX_SAMPLE_WIDTH:
        return crop_w, crop_h
    h = max(2, round(crop_h * MAX_SAMPLE_WIDTH / crop_w))
    return MAX_SAMPLE_WIDTH, h - h % 2
