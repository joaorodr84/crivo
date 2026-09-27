"""Bring every picked image to one size and format (FR6): crop, pad or fit.

- `crop`  keeps the largest centred part of the picture that has the target's shape and
          scales that to the target, so the overflow is cut evenly from both sides. Every
          output is exactly the target size; the edges of the picture may go.
- `pad`   scales until the picture fits inside the target and fills the rest with the
          background. Every output is exactly the target size; nothing is cut.
- `fit`   scales until the picture fits inside the target and stops there, so one side is
          the target and the other is at most that. Nothing is cut and nothing is added,
          and the sizes are *not* uniform, which is the point of choosing it.

All three scale up as well as down: a set that is "512x512" except for the three
thumbnails Pixabay had at 300 px is not the set the person asked for.

The scaling arithmetic is here and not `ImageOps.contain`/`pad`, which are what one would
reach for. On Pillow 12.3.0 `ImageOps.contain(Image.new("RGBA", (10000, 1)), (512, 512))`
raises `ValueError: height and width must be > 0`, because the short side rounds down to
zero. Pixabay serves panoramas and one-pixel-wide line art as readily as anything else, and
one of them must not end a run at the resize step. Here the short side is clamped to 1.

Everything is worked in RGBA and only reduced at the end, so each format follows from
what the pixels contain:

- A JPEG cannot hold transparency, so it is flattened onto the background (white unless
  one was given). This is the one place the background applies outside `pad`.
- PNG and WebP keep it, but only if there is some: a picture that ended up fully opaque is
  saved as RGB, which is smaller and opens the same everywhere.
- `pad` with no background on PNG/WebP pads with transparency, which is what a person
  putting these in a game or a page wants and cannot easily get back from a white fill.

EXIF orientation is applied before anything else, so a phone photo stored sideways is
cropped and padded as it looks and not as it is stored. Animated GIFs and WebPs are
reduced to their first frame. CMYK is converted to RGB without its ICC profile: the colour
shift is real and small, and colour management is out of scope for a thumbnail picker;
what would change the answer is a report from someone whose print-profile JPEGs look wrong.
"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageOps

RESAMPLE = Image.Resampling.LANCZOS
QUALITY = 90  # JPEG and WebP; visually lossless at these sizes, and far smaller than 100
EXTENSIONS = {"png": "png", "jpeg": "jpg", "webp": "webp"}
_WHITE = (255, 255, 255)


def extension(fmt: str) -> str:
    return EXTENSIONS[fmt]


def parse_color(text: str) -> tuple[int, int, int]:
    """`#rrggbb` (validated by Settings) as an RGB tuple."""
    return int(text[1:3], 16), int(text[3:5], 16), int(text[5:7], 16)


def _contained(source: tuple[int, int], box: tuple[int, int]) -> tuple[int, int]:
    """`source` scaled to fit inside `box`, keeping its shape; never below 1 px a side."""
    sw, sh = source
    scale = min(box[0] / sw, box[1] / sh)
    # min(): rounding must not push a side past the box.
    return min(max(1, round(sw * scale)), box[0]), min(max(1, round(sh * scale)), box[1])


def _cover_region(source: tuple[int, int], box: tuple[int, int]) -> tuple[float, ...]:
    """The centred part of `source` that has `box`'s shape, in source pixels.

    The axis that is kept whole is taken as exactly the source's size, not derived through a
    scale factor. The first version computed `(source - box / scale) / 2` for both axes, and
    for a real 1280x853 photograph cropped to a square the top came out as -5.7e-14: two
    nearly equal floats subtracted, and Pillow refuses a negative box offset. 2 of the 3
    real images in the first live run failed that way, while every round-numbered test
    passed. Comparing by cross-multiplication keeps the choice of axis in exact integers.
    """
    sw, sh = source
    bw, bh = box
    if sw * bh > sh * bw:  # the source is wider than the box: keep the full height
        width, height = sh * bw / bh, float(sh)
    else:  # taller, or the same shape: keep the full width
        width, height = float(sw), sw * bh / bw
    left, top = (sw - width) / 2, (sh - height) / 2
    # Belt and braces: whatever rounding remains must not put the box outside the image.
    left, top = max(0.0, left), max(0.0, top)
    return left, top, min(float(sw), left + width), min(float(sh), top + height)


def resize_image(
    image: Image.Image,
    size: tuple[int, int],
    mode: str = "crop",
    background: str | None = None,
) -> Image.Image:
    """`image` scaled to `size` by `mode`, as RGBA. `background` is used by `pad` only."""
    image = ImageOps.exif_transpose(image)
    image = image.convert("RGBA")
    if mode == "crop":
        # Resize just the region that survives the cut, not the whole picture up to
        # cover the box and then discard most of it. The first version did that and a
        # 10000x1 panorama into 512x512 built a 5,120,000 x 512 intermediate: the test
        # for it took 97 seconds and gigabytes instead of milliseconds.
        return image.resize(size, RESAMPLE, box=_cover_region(image.size, size))
    scaled = image.resize(_contained(image.size, size), RESAMPLE)
    if mode == "fit":
        return scaled
    if mode != "pad":
        raise ValueError(f"unknown resize mode {mode!r}")
    fill = (*parse_color(background), 255) if background else (0, 0, 0, 0)
    canvas = Image.new("RGBA", size, fill)
    canvas.alpha_composite(scaled, ((size[0] - scaled.width) // 2, (size[1] - scaled.height) // 2))
    return canvas


def _has_transparency(image: Image.Image) -> bool:
    return image.getchannel("A").getextrema()[0] < 255


def encode(image: Image.Image, fmt: str, background: str | None = None) -> bytes:
    """RGBA `image` as PNG, JPEG or WebP bytes; see the module docstring for the rules."""
    buffer = io.BytesIO()
    if fmt == "jpeg" or not _has_transparency(image):
        flat = Image.new("RGB", image.size, parse_color(background) if background else _WHITE)
        flat.paste(image, mask=image.getchannel("A"))
        if fmt == "jpeg":
            flat.save(buffer, "JPEG", quality=QUALITY, optimize=True)
        elif fmt == "png":
            flat.save(buffer, "PNG", optimize=True)
        else:
            flat.save(buffer, "WEBP", quality=QUALITY)
    elif fmt == "png":
        image.save(buffer, "PNG", optimize=True)
    else:
        image.save(buffer, "WEBP", quality=QUALITY)
    return buffer.getvalue()


def render(
    path: Path,
    size: tuple[int, int],
    mode: str = "crop",
    fmt: str = "png",
    background: str | None = None,
) -> bytes:
    """The file at `path`, resized and encoded: what goes into the zip."""
    with Image.open(path) as image:
        return encode(resize_image(image, size, mode, background), fmt, background)
