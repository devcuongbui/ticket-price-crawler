"""
DOM scraping fallback for ZaloPay movie ticket page.
Used when network interception doesn't capture the API data.

Selector strategy (priority order):
  1. data-* attributes (most stable in Next.js apps)
  2. CSS class names (may be minified — use multiple candidates)
  3. Semantic elements + text content

NOTE: On first run with DEBUG=1, review ./debug_responses/ to discover
the real API structure. If API interception works, this module is not needed.
"""
import asyncio
import logging
from playwright.async_api import Page

logger = logging.getLogger(__name__)

# ── Selector constants ───────────────────────────────────────────────────────
# Multiple candidates separated by comma — Playwright tries them in order.

MOVIE_CARD = (
    "[data-movie-id], "
    ".movie-item, .movie-card, "
    "article[class*='movie'], "
    "li[class*='movie']"
)

MOVIE_TITLE = "h2, h3, [class*='title'], [class*='name']"
MOVIE_POSTER = "img"
MOVIE_GENRE = "[class*='genre'], [class*='category'], [class*='the-loai']"
MOVIE_DURATION = "[class*='duration'], [class*='runtime'], [class*='thoi-luong']"
MOVIE_RATING = "[class*='rating'], [class*='age'], [class*='censorship']"

CINEMA_ITEM = (
    "[data-cinema-id], "
    ".cinema-item, .rap-item, "
    "li[class*='cinema'], li[class*='rap']"
)
CINEMA_NAME = "h3, h4, [class*='name'], [class*='cinema-name']"
CINEMA_ADDRESS = "[class*='address'], [class*='dia-chi'], [class*='location']"

DATE_TAB = (
    "[data-date], "
    "button[class*='date'], "
    "li[class*='date'], "
    ".date-tab, .ngay-tab"
)

SHOWTIME_ITEM = (
    "[data-showtime-id], "
    ".showtime-item, .suat-chieu-item, "
    "li[class*='showtime'], li[class*='suat']"
)
SHOWTIME_TIME = "[class*='time'], [class*='gio'], time"
SHOWTIME_FORMAT = "[class*='format'], [class*='dinh-dang']"
SHOWTIME_PRICE = "[class*='price'], [class*='gia'], [class*='amount']"

CITY_SELECTOR_TRIGGER = (
    "[data-testid*='city'], "
    "button[class*='city'], "
    "button[class*='location'], "
    ".location-selector, "
    "button:has-text('Chọn thành phố'), "
    "button:has-text('Thành phố'), "
    "span:has-text('Hồ Chí Minh'), "
    "span:has-text('TP. HCM')"
)

# ── Helper ───────────────────────────────────────────────────────────────────

async def _safe_text(element, selector: str, default: str = "") -> str:
    try:
        el = await element.query_selector(selector)
        if el:
            return (await el.inner_text()).strip()
    except Exception:
        pass
    return default


async def _safe_attr(element, selector: str, attr: str, default: str = "") -> str:
    try:
        el = await element.query_selector(selector)
        if el:
            val = await el.get_attribute(attr)
            return (val or "").strip()
    except Exception:
        pass
    return default


# ── City selection ────────────────────────────────────────────────────────────

async def select_city(page: Page, city_name: str) -> bool:
    """
    Attempt to select a city from the ZaloPay location picker.
    Returns True if successful, False otherwise.
    """
    try:
        # Try clicking the city selector trigger
        trigger = await page.query_selector(CITY_SELECTOR_TRIGGER)
        if trigger:
            await trigger.click()
            await asyncio.sleep(1)
        else:
            logger.warning("City selector trigger not found — trying text search")
            await page.click(f"text={city_name}", timeout=5000)
            return True

        # Wait for dropdown and click city
        await page.wait_for_selector(f"text={city_name}", timeout=8000)
        await page.click(f"text={city_name}")
        await page.wait_for_load_state("networkidle", timeout=10000)
        logger.info(f"Selected city: {city_name}")
        return True
    except Exception as e:
        logger.warning(f"Could not select city '{city_name}': {e}")
        return False


# ── Movie extraction ──────────────────────────────────────────────────────────

async def extract_movies(page: Page) -> list[dict]:
    """Extract movie list from DOM. Returns list of raw dicts."""
    try:
        await page.wait_for_selector(MOVIE_CARD, timeout=15000)
    except Exception:
        logger.warning("No movie cards found in DOM")
        return []

    cards = await page.query_selector_all(MOVIE_CARD)
    movies = []
    for card in cards:
        try:
            title = await _safe_text(card, MOVIE_TITLE)
            if not title:
                continue
            api_id = (
                await card.get_attribute("data-movie-id")
                or await card.get_attribute("data-id")
                or ""
            )
            poster = await _safe_attr(card, MOVIE_POSTER, "src")
            genre = await _safe_text(card, MOVIE_GENRE)
            duration = await _safe_text(card, MOVIE_DURATION)
            rating = await _safe_text(card, MOVIE_RATING)
            movies.append({
                "id": api_id,
                "title": title,
                "poster": poster,
                "genre": genre,
                "duration": duration,
                "rating": rating,
            })
        except Exception as e:
            logger.debug(f"Error extracting movie card: {e}")
    logger.info(f"DOM extracted {len(movies)} movies")
    return movies


# ── Cinema extraction ─────────────────────────────────────────────────────────

async def extract_cinemas(page: Page) -> list[dict]:
    """Extract cinema list from DOM after clicking a movie."""
    try:
        await page.wait_for_selector(CINEMA_ITEM, timeout=12000)
    except Exception:
        logger.warning("No cinema items found in DOM")
        return []

    items = await page.query_selector_all(CINEMA_ITEM)
    cinemas = []
    for item in items:
        try:
            name = await _safe_text(item, CINEMA_NAME)
            if not name:
                continue
            api_id = (
                await item.get_attribute("data-cinema-id")
                or await item.get_attribute("data-id")
                or ""
            )
            address = await _safe_text(item, CINEMA_ADDRESS)
            cinemas.append({
                "id": api_id,
                "name": name,
                "address": address,
            })
        except Exception as e:
            logger.debug(f"Error extracting cinema item: {e}")
    logger.info(f"DOM extracted {len(cinemas)} cinemas")
    return cinemas


# ── Date tabs ─────────────────────────────────────────────────────────────────

async def extract_dates(page: Page) -> list[dict]:
    """Extract available date tabs. Returns list of {date, label, element_index}."""
    try:
        await page.wait_for_selector(DATE_TAB, timeout=10000)
    except Exception:
        logger.warning("No date tabs found in DOM")
        return []

    tabs = await page.query_selector_all(DATE_TAB)
    dates = []
    for i, tab in enumerate(tabs):
        try:
            date_val = await tab.get_attribute("data-date") or ""
            label = (await tab.inner_text()).strip()
            dates.append({
                "date": date_val,
                "label": label,
                "index": i,
            })
        except Exception as e:
            logger.debug(f"Error extracting date tab: {e}")
    return dates


async def click_date_tab(page: Page, date_info: dict) -> None:
    """Click a date tab by data-date attribute or index."""
    date_val = date_info.get("date", "")
    label = date_info.get("label", "")
    try:
        if date_val:
            await page.click(f"[data-date='{date_val}']", timeout=5000)
        elif label:
            await page.click(f"text={label}", timeout=5000)
        else:
            # Fallback: click by index
            tabs = await page.query_selector_all(DATE_TAB)
            idx = date_info.get("index", 0)
            if idx < len(tabs):
                await tabs[idx].click()
        await asyncio.sleep(1)
    except Exception as e:
        logger.warning(f"Could not click date tab {date_info}: {e}")


# ── Showtime extraction ───────────────────────────────────────────────────────

async def extract_showtimes(page: Page) -> list[dict]:
    """Extract showtime rows with prices from DOM."""
    try:
        await page.wait_for_selector(SHOWTIME_ITEM, timeout=10000)
    except Exception:
        logger.warning("No showtime items found in DOM")
        return []

    items = await page.query_selector_all(SHOWTIME_ITEM)
    showtimes = []
    for item in items:
        try:
            time_val = await _safe_text(item, SHOWTIME_TIME)
            if not time_val:
                continue
            api_id = (
                await item.get_attribute("data-showtime-id")
                or await item.get_attribute("data-id")
                or ""
            )
            fmt = await _safe_text(item, SHOWTIME_FORMAT)
            price_text = await _safe_text(item, SHOWTIME_PRICE)
            showtimes.append({
                "id": api_id,
                "time": time_val,
                "format": fmt or "2D",
                "price": price_text,
            })
        except Exception as e:
            logger.debug(f"Error extracting showtime: {e}")
    logger.info(f"DOM extracted {len(showtimes)} showtimes")
    return showtimes


# ── Navigation helpers ────────────────────────────────────────────────────────

async def click_movie(page: Page, movie_raw: dict) -> bool:
    """Click on a movie card to open its showtimes view."""
    title = movie_raw.get("title", "")
    api_id = movie_raw.get("id", "")
    try:
        if api_id:
            el = await page.query_selector(f"[data-movie-id='{api_id}']")
            if el:
                await el.click()
                await page.wait_for_load_state("networkidle", timeout=10000)
                return True
        if title:
            await page.click(f"text={title}", timeout=8000)
            await page.wait_for_load_state("networkidle", timeout=10000)
            return True
    except Exception as e:
        logger.warning(f"Could not click movie '{title}': {e}")
    return False


async def click_cinema(page: Page, cinema_raw: dict) -> bool:
    """Click on a cinema to view its showtimes."""
    name = cinema_raw.get("name", "")
    api_id = cinema_raw.get("id", "")
    try:
        if api_id:
            el = await page.query_selector(f"[data-cinema-id='{api_id}']")
            if el:
                await el.click()
                await asyncio.sleep(1)
                return True
        if name:
            await page.click(f"text={name}", timeout=8000)
            await asyncio.sleep(1)
            return True
    except Exception as e:
        logger.warning(f"Could not click cinema '{name}': {e}")
    return False
