"""
Script 4: VNPay Movie Ticket Crawler
Target: https://vnpay.vn/ve-xem-phim

Dùng Playwright thao tác DOM để lấy suất chiếu và giá vé từ VNPay.
Chỉ cập nhật prices.vnpay trong showtimes collection.
"""
import asyncio
import logging

from crawler.transformer import transform_movie, transform_cinema, transform_showtime
from crawler.interceptor import ApiInterceptor
import crawler.extractor as extractor

logger = logging.getLogger(__name__)

VNPAY_URL = "https://vnpay.vn/ve-xem-phim"
PLATFORM = "vnpay"

# VNPay-specific DOM selectors
MOVIE_CARD = (
    "[data-movie-id], .movie-item, .movie-card, "
    "div[class*='movie'], article[class*='movie'], "
    "div[class*='Film'], li[class*='movie']"
)
CINEMA_ITEM = (
    "[data-cinema-id], .cinema-item, .theater-item, "
    "div[class*='Cinema'], div[class*='Theater'], "
    "li[class*='cinema'], li[class*='theater']"
)
DATE_TAB = (
    "[data-date], button[class*='date'], "
    "li[class*='date'], div[class*='DateItem'], "
    "span[class*='date-tab']"
)
SHOWTIME_ROW = (
    ".showtime-item, [data-showtime-id], .session-item, "
    "div[class*='Showtime'], div[class*='Session'], "
    "li[class*='showtime']"
)


async def _extract_vnpay_movies(page: Page) -> list[dict]:
    try:
        await page.wait_for_selector(MOVIE_CARD, timeout=15000)
    except Exception:
        logger.warning("[VNPay] No movie cards found")
        return []

    return await page.evaluate("""
    () => {
        const selectors = [
            '[data-movie-id]', '.movie-item', '.movie-card',
            'div[class*="movie"]', 'div[class*="Film"]', 'article[class*="movie"]'
        ];
        let cards = [];
        for (const sel of selectors) {
            cards = Array.from(document.querySelectorAll(sel));
            if (cards.length > 0) break;
        }
        return cards.map(card => ({
            id: card.dataset.movieId || card.dataset.id || '',
            title: (
                card.querySelector('h2, h3, h4, [class*="title"], [class*="name"], [class*="Title"]')
                ?.innerText?.trim() || ''
            ),
            poster: card.querySelector('img')?.src || '',
            genre: card.querySelector('[class*="genre"], [class*="category"]')?.innerText?.trim() || '',
            duration: card.querySelector('[class*="duration"], [class*="runtime"]')?.innerText?.trim() || '',
            rating: card.querySelector('[class*="rating"], [class*="age"]')?.innerText?.trim() || '',
        })).filter(m => m.title);
    }
    """)


async def _extract_vnpay_cinemas(page: Page) -> list[dict]:
    try:
        await page.wait_for_selector(CINEMA_ITEM, timeout=12000)
    except Exception:
        logger.warning("[VNPay] No cinema items found")
        return []

    return await page.evaluate("""
    () => {
        const selectors = [
            '[data-cinema-id]', '.cinema-item', '.theater-item',
            'div[class*="Cinema"]', 'div[class*="Theater"]'
        ];
        let items = [];
        for (const sel of selectors) {
            items = Array.from(document.querySelectorAll(sel));
            if (items.length > 0) break;
        }
        return items.map(el => ({
            id: el.dataset.cinemaId || el.dataset.theaterId || el.dataset.id || '',
            name: el.querySelector('[class*="name"], [class*="Name"], h3, h4')?.innerText?.trim() || '',
            address: el.querySelector('[class*="address"], [class*="Address"], [class*="location"]')?.innerText?.trim() || '',
        })).filter(c => c.name);
    }
    """)


async def _extract_vnpay_dates(page: Page) -> list[dict]:
    try:
        await page.wait_for_selector(DATE_TAB, timeout=10000)
    except Exception:
        return []

    return await page.evaluate("""
    () => {
        const selectors = ['[data-date]', 'button[class*="date"]', 'li[class*="date"]', 'div[class*="DateItem"]'];
        let tabs = [];
        for (const sel of selectors) {
            tabs = Array.from(document.querySelectorAll(sel));
            if (tabs.length > 0) break;
        }
        return tabs.map((tab, i) => ({
            date: tab.dataset.date || '',
            label: tab.innerText?.trim() || '',
            index: i,
        }));
    }
    """)


async def _extract_vnpay_showtimes(page: Page) -> list[dict]:
    try:
        await page.wait_for_selector(SHOWTIME_ROW, timeout=10000)
    except Exception:
        logger.warning("[VNPay] No showtime rows found")
        return []

    return await page.evaluate("""
    () => {
        const selectors = [
            '.showtime-item', '[data-showtime-id]', '.session-item',
            'div[class*="Showtime"]', 'div[class*="Session"]'
        ];
        let rows = [];
        for (const sel of selectors) {
            rows = Array.from(document.querySelectorAll(sel));
            if (rows.length > 0) break;
        }
        return rows.map(row => ({
            id: row.dataset.showtimeId || row.dataset.sessionId || row.dataset.id || '',
            time: row.querySelector('[class*="time"], [class*="Time"], time')?.innerText?.trim() || '',
            format: row.querySelector('[class*="format"], [class*="Format"], [class*="type"]')?.innerText?.trim() || '2D',
            price: row.querySelector('[class*="price"], [class*="Price"], [class*="amount"]')?.innerText?.trim() || '',
            seatType: row.querySelector('[class*="seat"], [class*="Seat"], [class*="type"]')?.innerText?.trim() || 'Thường',
        })).filter(s => s.time);
    }
    """)


async def crawl_vnpay(context, db, city: str = "Hải Phòng") -> None:
    """Main VNPay crawler entry point."""
    logger.info(f"[VNPay] Starting crawl for {city}")
    interceptor = ApiInterceptor()
    page = await context.new_page()
    interceptor.attach(page)

    try:
        await page.goto(VNPAY_URL, wait_until="load", timeout=30000)
        await asyncio.sleep(3)

        await extractor.select_city(page, city)
        await asyncio.sleep(2)

        interceptor.clear()
        movies_raw = interceptor.get_typed("movies") or await _extract_vnpay_movies(page)
        if not movies_raw:
            logger.warning("[VNPay] No movies found")
            return

        logger.info(f"[VNPay] Found {len(movies_raw)} movies")

        for movie_raw in movies_raw:
            movie_doc = transform_movie(movie_raw)
            if not movie_doc:
                continue

            await db.upsert_movie(movie_doc)
            logger.info(f"  [VNPay] Movie: {movie_doc['title']}")

            interceptor.clear()
            clicked = await extractor.click_movie(page, movie_raw)
            if not clicked:
                continue
            await asyncio.sleep(1.5)

            cinemas_raw = interceptor.get_typed("cinemas") or await _extract_vnpay_cinemas(page)
            if not cinemas_raw:
                await page.go_back()
                await asyncio.sleep(1)
                continue

            for cinema_raw in cinemas_raw:
                cinema_doc = transform_cinema(cinema_raw, city)
                if not cinema_doc:
                    continue

                await db.upsert_cinema(cinema_doc)

                interceptor.clear()
                await extractor.click_cinema(page, cinema_raw)
                await asyncio.sleep(1)

                dates = await _extract_vnpay_dates(page)
                if not dates:
                    showtimes_raw = interceptor.get_typed("showtimes") or await _extract_vnpay_showtimes(page)
                    await _save_showtimes(db, showtimes_raw, movie_doc, cinema_doc, "")
                    continue

                for date_info in dates:
                    interceptor.clear()
                    await extractor.click_date_tab(page, date_info)

                    showtimes_raw = interceptor.get_typed("showtimes") or await _extract_vnpay_showtimes(page)
                    saved = await _save_showtimes(
                        db, showtimes_raw, movie_doc, cinema_doc, date_info.get("date", "")
                    )
                    logger.info(f"    [VNPay] {cinema_doc['name']} | {date_info.get('date')} → {saved} showtimes")

            await page.go_back()
            await asyncio.sleep(1)

    except Exception as e:
        logger.error(f"[VNPay] Crawl error: {e}", exc_info=True)
    finally:
        await page.close()

    logger.info("[VNPay] Crawl complete")


async def _save_showtimes(db, showtimes_raw, movie_doc, cinema_doc, date_override: str) -> int:
    if not showtimes_raw:
        return 0
    entries = []
    for st_raw in showtimes_raw:
        entry = transform_showtime(
            st_raw,
            movie_doc["id"],    # dùng 'id' field
            cinema_doc["id"],   # dùng 'id' field
            date_override,
            platform=PLATFORM,
        )
        if entry:
            entries.append(entry)
    await db.bulk_upsert_showtime_prices(entries, PLATFORM)
    return len(entries)
