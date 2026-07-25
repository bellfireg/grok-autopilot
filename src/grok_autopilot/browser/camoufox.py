"""Grok Autopilot — Camoufox Browser Launcher
==============================================
Launch and configure Camoufox (anti-detect Firefox fork) browser instances.
Camoufox provides C++-level stealth fingerprinting to bypass bot detection.

WORKAROUND: browserforge 1.2.4 + apify_fingerprint_datapoints 0.13.0 generates
Chrome/Android user-agent tuples for Firefox browsers, causing header generation
to fail ("No headers based on this input"). Monkey-patch HeaderGenerator.generate
to strip user_agent and let browserforge pick its own compatible UA.
"""

import random
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from ..utils.logger import log


def _apply_browserforge_workaround():
    """Monkey-patch browserforge to auto-select user agents.

    Without this, Camoufox crashes on Linux with:
    ValueError: No headers based on this input can be generated.
    """
    try:
        from browserforge.headers.generator import HeaderGenerator
        _original = HeaderGenerator.generate

        def _no_ua_gen(self, **header_kwargs):
            header_kwargs.pop('user_agent', None)
            return _original(self, **header_kwargs)

        HeaderGenerator.generate = _no_ua_gen
    except ImportError:
        pass


_apply_browserforge_workaround()


@asynccontextmanager
async def launch_browser(
    headless: bool = True,
    window_width: int = 900,
    window_height: int = 600,
    proxy: str | None = None,
) -> AsyncIterator:
    """Launch a Camoufox browser instance with stealth settings.

    Args:
        headless: Run in headless mode (no visible window).
        window_width: Browser window width in pixels.
        window_height: Browser window height in pixels.
        proxy: Proxy URL (e.g. socks5://host:port, http://host:port).

    Yields:
        Camoufox browser context manager.
    """
    from camoufox.async_api import AsyncCamoufox

    log(f"   🦊 Launching Camoufox (headless={headless})...")

    # ponytail: headless=True forced on WSL2 — Weston/Wayland segfaults under
    # parallel browser load. Single browser works with :0 but 10 parallel kills weston.
    if not headless:
        headless = True

    kwargs: dict = {
        "headless": headless,
    }
    if proxy:
        kwargs["proxy"] = {"server": proxy}
        log(f"   🌐 Using proxy: {proxy}")

    async with AsyncCamoufox(**kwargs) as browser:
        yield browser


async def setup_page(page) -> None:
    """Apply standard page setup for stealth and stability.

    - Forces 100% zoom on every page load

    Note: pageerror listener intentionally omitted — Playwright's internal
    handler crashes on Node.js v24+ when pageError.location is undefined.
    Adding our own listener doesn't prevent the internal crash.

    Args:
        page: Playwright/Camoufox page object.
    """

    # Force 100% zoom on every page load
    await page.add_init_script("""() => {
        document.addEventListener('DOMContentLoaded', () => {
            document.body.style.zoom = '100%';
        });
    }""")
