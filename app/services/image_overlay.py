"""Rendering text onto an image with HTML, not with the image model.

Handoff section 5: FLUX (and every other diffusion image model tried) mangles
Persian and Arabic script into nonsense glyphs. Every image is generated with
*no* text in the prompt, and any caption, headline or overlay the marketizer
asked for (``VisualBrief.overlay_text``) is drawn afterwards by rendering an
HTML/CSS box with a real browser and compositing the result — which gets
correct shaping, correct right-to-left ordering, and whatever font the panel's
brand kit specifies, none of which a diffusion model reliably gets right for
these scripts.

This is real, working code — unlike the diffusion model itself, drawing text
with a browser needs no GPU weights, so it runs and is tested exactly as it
will in production.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from app.core.config import settings

logger = logging.getLogger(__name__)

#: fa and ar read right-to-left; en does not. Getting this wrong reverses
#: word order, which silent mis-shaping does not — it is immediately visible,
#: but only after a real render, so it is worth being explicit about here.
RTL_LOCALES = {"fa", "ar"}

#: Kept short deliberately: an overlay is a headline or a short caption, not a
#: paragraph. A long string is *shrunk* rather than truncated, but a very
#: long one still degrades badly, so the contract already caps it at 200
#: chars (``VisualBrief.overlay_text``).
_DEFAULT_FONT_STACK = (
    "'Vazirmatn', 'Noto Sans Arabic', 'Noto Naskh Arabic', 'Segoe UI', Tahoma, sans-serif"
)


class OverlayRenderError(RuntimeError):
    """The browser could not render the overlay."""


@dataclass(frozen=True)
class OverlaySpec:
    text: str
    width: int
    height: int
    locale: str = "fa"
    #: Brand palette from the visual brief; the first colour is used for the
    #: text, a dark wash behind it for legibility over an arbitrary photo.
    palette: tuple[str, ...] = ()
    #: Where the box sits: bottom band reads best over a generated photo,
    #: since diffusion models tend to keep the lower third least busy.
    position: str = "bottom"
    font_family: str = _DEFAULT_FONT_STACK

    @property
    def direction(self) -> str:
        return "rtl" if self.locale in RTL_LOCALES else "ltr"

    @property
    def text_color(self) -> str:
        return self.palette[0] if self.palette else "#ffffff"


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


def render_html(spec: OverlaySpec) -> str:
    """The page rendered to produce the overlay.

    A transparent background: only the text and its legibility band are
    opaque, so compositing over the base image is a straight alpha blend with
    nothing to key out.
    """
    align_items = {"top": "flex-start", "middle": "center", "bottom": "flex-end"}.get(
        spec.position, "flex-end"
    )
    return f"""<!doctype html>
<html lang="{spec.locale}" dir="{spec.direction}">
<head>
<meta charset="utf-8">
<style>
  html, body {{
    margin: 0; padding: 0;
    width: {spec.width}px; height: {spec.height}px;
    background: transparent;
  }}
  .frame {{
    box-sizing: border-box;
    width: 100%; height: 100%;
    display: flex; flex-direction: column; justify-content: {align_items};
    padding: {max(24, spec.width // 20)}px;
  }}
  .band {{
    display: inline-block;
    max-width: 100%;
    background: rgba(0, 0, 0, 0.55);
    color: {spec.text_color};
    font-family: {spec.font_family};
    font-weight: 700;
    font-size: {max(20, spec.width // 16)}px;
    line-height: 1.35;
    padding: 0.5em 0.75em;
    border-radius: 8px;
    text-align: {"right" if spec.direction == "rtl" else "left"};
    unicode-bidi: plaintext;
    overflow-wrap: break-word;
  }}
</style>
</head>
<body>
  <div class="frame"><div class="band">{_escape(spec.text)}</div></div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# the browser
# ---------------------------------------------------------------------------
#: One Chromium process per GPU worker, reused across jobs — launching it is
#: the slow part (hundreds of ms), and the worker renders one overlay at a
#: time anyway (handoff section 6: it is the sole owner of the card, and this
#: runs on that same worker between model calls).
_lock = threading.Lock()
_playwright = None
_browser = None


def _ensure_browser():
    global _playwright, _browser
    with _lock:
        if _browser is not None:
            return _browser
        from playwright.sync_api import sync_playwright

        try:
            _playwright = sync_playwright().start()
            launch_kwargs = {}
            if settings.playwright_executable_path:
                launch_kwargs["executable_path"] = settings.playwright_executable_path
            _browser = _playwright.chromium.launch(**launch_kwargs)
        except Exception as exc:  # noqa: BLE001
            if _playwright is not None:
                _playwright.stop()
                _playwright = None
            raise OverlayRenderError(f"could not launch the renderer: {exc}") from exc
        return _browser


def shutdown() -> None:
    """Close the browser. Called on worker shutdown and between tests."""
    global _playwright, _browser
    with _lock:
        if _browser is not None:
            _browser.close()
            _browser = None
        if _playwright is not None:
            _playwright.stop()
            _playwright = None


@contextmanager
def _page(width: int, height: int) -> Iterator:
    browser = _ensure_browser()
    page = browser.new_page(viewport={"width": width, "height": height})
    try:
        yield page
    finally:
        page.close()


def render_overlay(spec: OverlaySpec) -> bytes:
    """Render ``spec`` to a transparent PNG the same size as the base image."""
    if not spec.text.strip():
        raise OverlayRenderError("overlay text is empty")
    if spec.width <= 0 or spec.height <= 0:
        raise OverlayRenderError(f"invalid overlay size: {spec.width}x{spec.height}")

    with _page(spec.width, spec.height) as page:
        page.set_content(render_html(spec), timeout=settings.overlay_render_timeout_seconds * 1000)
        return page.screenshot(
            type="png",
            omit_background=True,
            timeout=settings.overlay_render_timeout_seconds * 1000,
        )
