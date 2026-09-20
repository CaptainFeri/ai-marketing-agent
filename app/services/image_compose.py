"""Putting the overlay on top of the base image, and the simulated base image
itself while phase 0 has not produced a real one.

Two unrelated jobs live here on purpose: both are Pillow one-liners, and
splitting them into separate modules would just mean importing Pillow twice.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass

#: (width, height) for each aspect ratio the marketizer can ask for
#: (``VisualBrief.aspect_ratio``), at a resolution FLUX.1-schnell targets
#: (handoff section 5: ~1024px).
DIMENSIONS: dict[str, tuple[int, int]] = {
    "1:1": (1024, 1024),
    "4:5": (896, 1120),
    "16:9": (1280, 720),
    "9:16": (720, 1280),
}
DEFAULT_ASPECT_RATIO = "1:1"


def dimensions_for(aspect_ratio: str) -> tuple[int, int]:
    return DIMENSIONS.get(aspect_ratio, DIMENSIONS[DEFAULT_ASPECT_RATIO])


@dataclass(frozen=True)
class ComposedImage:
    png_bytes: bytes
    width: int
    height: int


def compose(base_png: bytes, overlay_png: bytes | None) -> ComposedImage:
    """Alpha-composite ``overlay_png`` over ``base_png``.

    ``overlay_png`` is expected to be the same size as the base — the overlay
    renderer is always called with the base's own dimensions — and is a
    no-op pass-through when absent (``video_mode`` layouts or a variant with
    no ``overlay_text``).
    """
    from PIL import Image

    base = Image.open(io.BytesIO(base_png)).convert("RGBA")
    if overlay_png is not None:
        overlay = Image.open(io.BytesIO(overlay_png)).convert("RGBA")
        if overlay.size != base.size:
            overlay = overlay.resize(base.size)
        base = Image.alpha_composite(base, overlay)

    buffer = io.BytesIO()
    base.convert("RGB").save(buffer, format="PNG")
    return ComposedImage(png_bytes=buffer.getvalue(), width=base.width, height=base.height)


# ---------------------------------------------------------------------------
# the simulated base image
# ---------------------------------------------------------------------------
def simulated_base_image(
    prompt: str, width: int, height: int, palette: tuple[str, ...] = ()
) -> bytes:
    """A placeholder that stands in for a diffusion model's output.

    Deterministic from the prompt (same prompt, same image) so a re-run in
    tests is reproducible, and visibly synthetic — a flat gradient with a
    seed-derived stripe, never anything a customer could mistake for a real
    generated photo. Real FLUX output replaces this entirely; nothing
    downstream depends on its content, only on it being a same-sized image
    the overlay can sit on.
    """
    from PIL import Image, ImageDraw

    # A wide hash gives two independent draws of entropy — one for the
    # stripe position, one to nudge the base shade — rather than one number
    # reused for everything. A single shared value means two different
    # prompts can coincidentally land on the same stripe offset and render
    # byte-for-byte identical placeholders, which defeats the one thing this
    # function exists for: four gallery "options" that actually look like
    # four different things.
    digest = hashlib.sha256(prompt.encode("utf-8")).digest()
    stripe_seed = int.from_bytes(digest[:4], "big")
    shade_seed = int.from_bytes(digest[4:8], "big")

    top = _hex_to_rgb(palette[0] if palette else "#3a3a3a")
    # ±20% brightness, so even two seeds that land on the same stripe offset
    # still differ in colour.
    top = _shade(top, 0.8 + 0.4 * (shade_seed % 1000) / 1000)
    bottom = _shade(top, 0.5)

    image = Image.new("RGB", (width, height), top)
    draw = ImageDraw.Draw(image)
    for y in range(height):
        t = y / max(1, height - 1)
        row = _lerp(top, bottom, t)
        draw.line([(0, y), (width, y)], fill=row)

    # A cheap per-image variation so four "options" tied to different
    # prompts are visibly distinct without a real model behind them.
    stripe = max(40, width // 8)
    for x in range((stripe_seed % stripe), width, stripe):
        draw.line([(x, 0), (x, height)], fill=_shade(top, 1.15), width=2)

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    if len(value) != 6:
        return (58, 58, 58)
    try:
        return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))
    except ValueError:
        return (58, 58, 58)


def _clamp_channel(value: float) -> int:
    return max(0, min(255, int(value)))


def _shade(rgb: tuple[int, int, int], factor: float) -> tuple[int, int, int]:
    r, g, b = rgb
    return (_clamp_channel(r * factor), _clamp_channel(g * factor), _clamp_channel(b * factor))


def _lerp(start: tuple[int, int, int], end: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    r0, g0, b0 = start
    r1, g1, b1 = end
    return (int(r0 + (r1 - r0) * t), int(g0 + (g1 - g0) * t), int(b0 + (b1 - b0) * t))
