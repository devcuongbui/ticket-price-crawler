"""Playwright browser lifecycle management."""
import logging
from playwright.async_api import async_playwright, Playwright, Browser, BrowserContext

logger = logging.getLogger(__name__)


async def create_browser_context() -> tuple[Playwright, Browser, BrowserContext]:
    """Launch a Chromium browser with anti-bot settings."""
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
            "--disable-infobars",
            "--window-size=1440,900",
        ],
    )
    context = await browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        locale="vi-VN",
        viewport={"width": 1440, "height": 900},
        extra_http_headers={"Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8"},
    )
    logger.info("Browser context created (Chromium headless)")
    return pw, browser, context
