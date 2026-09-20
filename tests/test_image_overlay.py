"""Rendering overlay text with a real browser (handoff section 5).

These run a real headless Chromium — the one genuinely testable piece of the
image pipeline before phase 0 delivers FLUX, since drawing text with a
browser needs no GPU weights. Skipped where no Chromium is reachable (a CI
image that has not run ``playwright install``), never faked.
"""

from __future__ import annotations

import pytest

from app.services.image_overlay import (
    RTL_LOCALES,
    OverlayRenderError,
    OverlaySpec,
    render_html,
    render_overlay,
    shutdown,
)


def _chromium_available() -> bool:
    try:
        spec = OverlaySpec(text="probe", width=64, height=64)
        render_overlay(spec)
        return True
    except OverlayRenderError:
        return False
    finally:
        shutdown()


requires_chromium = pytest.mark.skipif(
    not _chromium_available(),
    reason="no Chromium reachable for Playwright; see docs/questionnaire.md's "
    "PLAYWRIGHT_EXECUTABLE_PATH note",
)


@pytest.fixture(autouse=True)
def _clean_browser():
    yield
    shutdown()


# ---------------------------------------------------------------------------
# the HTML itself — no browser needed
# ---------------------------------------------------------------------------
def test_fa_and_ar_are_right_to_left() -> None:
    assert "fa" in RTL_LOCALES
    assert "ar" in RTL_LOCALES
    assert "en" not in RTL_LOCALES


def test_the_page_is_marked_rtl_for_persian() -> None:
    html = render_html(OverlaySpec(text="سلام", width=400, height=200, locale="fa"))
    assert 'dir="rtl"' in html
    assert 'lang="fa"' in html


def test_the_page_is_marked_ltr_for_english() -> None:
    html = render_html(OverlaySpec(text="Hello", width=400, height=200, locale="en"))
    assert 'dir="ltr"' in html


def test_text_is_html_escaped() -> None:
    """An overlay string is customer-controlled content (a marketing hook),
    not markup — it must never be interpreted as HTML."""
    html = render_html(OverlaySpec(text="<script>alert(1)</script>", width=400, height=200))
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_quotes_and_ampersands_are_escaped_too() -> None:
    html = render_html(OverlaySpec(text='Tom & "Jerry"', width=400, height=200))
    assert "&amp;" in html
    assert "&quot;" in html


def test_the_page_size_matches_the_spec() -> None:
    html = render_html(OverlaySpec(text="x", width=777, height=333))
    assert "777px" in html
    assert "333px" in html


def test_the_background_is_transparent() -> None:
    """Only the legibility band is opaque — the compositor blends this
    straight over the base image with nothing to key out."""
    html = render_html(OverlaySpec(text="x", width=400, height=200))
    assert "background: transparent" in html


@pytest.mark.parametrize("position", ["top", "middle", "bottom"])
def test_every_position_produces_valid_html(position: str) -> None:
    html = render_html(OverlaySpec(text="x", width=400, height=200, position=position))
    assert "<html" in html


def test_the_first_palette_colour_is_the_text_colour() -> None:
    spec = OverlaySpec(text="x", width=400, height=200, palette=("#ff0000", "#00ff00"))
    assert spec.text_color == "#ff0000"


def test_a_missing_palette_falls_back_to_white_text() -> None:
    assert OverlaySpec(text="x", width=400, height=200).text_color == "#ffffff"


# ---------------------------------------------------------------------------
# real rendering
# ---------------------------------------------------------------------------
@requires_chromium
def test_persian_text_renders_to_a_valid_png() -> None:
    png = render_overlay(
        OverlaySpec(text="راهنمای خرید دریل برقی", width=400, height=400, locale="fa")
    )
    assert png.startswith(b"\x89PNG\r\n\x1a\n")


@requires_chromium
def test_the_rendered_png_has_an_alpha_channel() -> None:
    """Compositing depends on this: a fully opaque overlay would blot out
    the entire base image rather than just the text band."""
    import io

    from PIL import Image

    png = render_overlay(OverlaySpec(text="test", width=200, height=200))
    image = Image.open(io.BytesIO(png))
    assert image.mode == "RGBA"
    # Somewhere outside the text band, the pixel must be (near) fully
    # transparent — the page background is `transparent`, not black.
    corner_alpha = image.getpixel((5, 5))[3]
    assert corner_alpha < 10


@requires_chromium
def test_arabic_text_renders_without_raising() -> None:
    png = render_overlay(
        OverlaySpec(text="دليل شراء المثقاب الكهربائي", width=400, height=400, locale="ar")
    )
    assert len(png) > 0


@requires_chromium
def test_english_text_renders_left_to_right() -> None:
    png = render_overlay(OverlaySpec(text="Buying Guide", width=400, height=200, locale="en"))
    assert png.startswith(b"\x89PNG")


@requires_chromium
def test_the_output_matches_the_requested_dimensions() -> None:
    import io

    from PIL import Image

    png = render_overlay(OverlaySpec(text="x", width=333, height=222))
    image = Image.open(io.BytesIO(png))
    assert image.size == (333, 222)


@requires_chromium
def test_the_browser_is_reused_across_calls() -> None:
    """Launching Chromium costs hundreds of ms; the worker renders many
    overlays without paying that cost each time."""
    from app.services import image_overlay

    render_overlay(OverlaySpec(text="first", width=100, height=100))
    browser_after_first = image_overlay._browser
    render_overlay(OverlaySpec(text="second", width=100, height=100))
    assert image_overlay._browser is browser_after_first


@requires_chromium
def test_shutdown_lets_a_later_render_still_work() -> None:
    render_overlay(OverlaySpec(text="before", width=100, height=100))
    shutdown()
    png = render_overlay(OverlaySpec(text="after", width=100, height=100))
    assert png.startswith(b"\x89PNG")


def test_empty_text_is_refused() -> None:
    with pytest.raises(OverlayRenderError):
        render_overlay(OverlaySpec(text="   ", width=100, height=100))


@pytest.mark.parametrize(("width", "height"), [(0, 100), (100, 0), (-1, 100)])
def test_an_invalid_size_is_refused(width: int, height: int) -> None:
    with pytest.raises(OverlayRenderError):
        render_overlay(OverlaySpec(text="x", width=width, height=height))
