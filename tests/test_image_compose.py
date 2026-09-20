"""The compositor and the simulated placeholder image.

Both are ordinary Pillow code — real, and tested as such.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image

from app.services.image_compose import (
    DEFAULT_ASPECT_RATIO,
    DIMENSIONS,
    compose,
    dimensions_for,
    simulated_base_image,
)


# ---------------------------------------------------------------------------
# dimensions
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("ratio", list(DIMENSIONS))
def test_every_visual_brief_aspect_ratio_has_dimensions(ratio: str) -> None:
    """VisualBrief.aspect_ratio's Literal values must all resolve to something."""
    from app.agents.contracts import VisualBrief

    assert ratio in {"1:1", "4:5", "16:9", "9:16"}
    width, height = dimensions_for(ratio)
    assert width > 0 and height > 0
    # Round-trips through the contract too.
    VisualBrief(scene="x", aspect_ratio=ratio)


def test_an_unknown_ratio_falls_back_to_the_default() -> None:
    assert dimensions_for("2:3") == DIMENSIONS[DEFAULT_ASPECT_RATIO]


def test_portrait_and_landscape_are_actually_oriented_that_way() -> None:
    w, h = dimensions_for("9:16")
    assert h > w
    w, h = dimensions_for("16:9")
    assert w > h


def test_dimensions_target_flux_schnells_resolution() -> None:
    """Handoff section 5: roughly 1024px."""
    w, h = dimensions_for("1:1")
    assert 900 <= w <= 1200
    assert 900 <= h <= 1200


# ---------------------------------------------------------------------------
# the simulated placeholder
# ---------------------------------------------------------------------------
def test_the_same_prompt_produces_the_same_image() -> None:
    """Needed so a test re-run is reproducible without a real model."""
    first = simulated_base_image("a drill on a workbench", 256, 256)
    second = simulated_base_image("a drill on a workbench", 256, 256)
    assert first == second


def test_different_prompts_produce_different_images() -> None:
    """Four package options built from the same visual brief but different
    seeds must actually look different — a gallery of four identical
    placeholders would defeat the point of offering options."""
    a = simulated_base_image("a drill", 256, 256)
    b = simulated_base_image("a drill on a workbench, seed=1", 256, 256)
    assert a != b


def test_the_output_is_a_valid_image_of_the_requested_size() -> None:
    png = simulated_base_image("x", 400, 300)
    image = Image.open(io.BytesIO(png))
    assert image.size == (400, 300)
    assert image.format == "PNG"


def test_the_palette_influences_the_colour() -> None:
    blue = simulated_base_image("x", 64, 64, ("#0000ff",))
    red = simulated_base_image("x", 64, 64, ("#ff0000",))
    assert blue != red

    image = Image.open(io.BytesIO(blue)).convert("RGB")
    r, g, b = image.getpixel((32, 0))
    assert b > r  # the top of the gradient is close to the palette colour


def test_a_malformed_hex_colour_does_not_raise() -> None:
    png = simulated_base_image("x", 64, 64, ("not-a-colour",))
    assert Image.open(io.BytesIO(png)).size == (64, 64)


def test_no_palette_still_produces_an_image() -> None:
    png = simulated_base_image("x", 64, 64)
    assert Image.open(io.BytesIO(png)).size == (64, 64)


# ---------------------------------------------------------------------------
# compositing
# ---------------------------------------------------------------------------
def _solid(size: tuple[int, int], color: tuple[int, int, int, int]) -> bytes:
    image = Image.new("RGBA", size, color)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_composing_with_no_overlay_returns_the_base_unchanged_in_content() -> None:
    base = _solid((64, 64), (10, 20, 30, 255))
    result = compose(base, None)
    image = Image.open(io.BytesIO(result.png_bytes)).convert("RGB")
    assert image.getpixel((0, 0)) == (10, 20, 30)


def test_an_opaque_overlay_fully_replaces_the_base_colour() -> None:
    base = _solid((64, 64), (10, 20, 30, 255))
    overlay = _solid((64, 64), (200, 100, 50, 255))
    result = compose(base, overlay)
    image = Image.open(io.BytesIO(result.png_bytes)).convert("RGB")
    assert image.getpixel((0, 0)) == (200, 100, 50)


def test_a_transparent_overlay_leaves_the_base_showing_through() -> None:
    base = _solid((64, 64), (10, 20, 30, 255))
    overlay = _solid((64, 64), (200, 100, 50, 0))
    result = compose(base, overlay)
    image = Image.open(io.BytesIO(result.png_bytes)).convert("RGB")
    assert image.getpixel((0, 0)) == (10, 20, 30)


def test_a_half_transparent_overlay_blends() -> None:
    base = _solid((64, 64), (0, 0, 0, 255))
    overlay = _solid((64, 64), (255, 255, 255, 128))
    result = compose(base, overlay)
    image = Image.open(io.BytesIO(result.png_bytes)).convert("RGB")
    r, g, b = image.getpixel((0, 0))
    assert 100 < r < 155  # roughly halfway between black and white


def test_a_differently_sized_overlay_is_resized_to_match() -> None:
    base = _solid((64, 64), (10, 20, 30, 255))
    overlay = _solid((32, 32), (200, 100, 50, 255))
    result = compose(base, overlay)
    assert result.width == 64
    assert result.height == 64


def test_the_result_dimensions_match_the_base() -> None:
    base = simulated_base_image("x", 400, 300)
    result = compose(base, None)
    assert (result.width, result.height) == (400, 300)


def test_the_output_is_always_a_valid_png() -> None:
    base = simulated_base_image("x", 64, 64)
    overlay = _solid((64, 64), (255, 0, 0, 128))
    result = compose(base, overlay)
    assert result.png_bytes.startswith(b"\x89PNG\r\n\x1a\n")
