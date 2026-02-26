"""
Script 2: ZaloPay Showtime Crawler (Playwright DOM)
Target: https://zalopay.vn/dat-ve-phim

Dùng Playwright thao tác DOM để lấy suất chiếu và giá vé từ ZaloPay.
Chỉ cập nhật prices.zalopay trong showtimes collection.

Danh sách phim được lấy từ Script 1 (crawler/movies_fetcher.py).
"""
import asyncio
import logging

from crawler.movies_fetcher import fetch_movies_api
from crawler.transformer import transform_movie, transform_cinema, transform_showtime
from crawler.interceptor import ApiInterceptor
import crawler.extractor as extractor

logger = logging.getLogger(__name__)

ZALOPAY_URL = "https://zalopay.vn/dat-ve-phim"
PLATFORM = "zalopay"


async def crawl_zalopay(context, db, city: str = "Hải Phòng") -> None:
    """Main ZaloPay crawler entry point."""
    logger.info(f"[ZaloPay] Starting crawl for {city}")

    # Lấy danh sách phim qua REST API (không cần browser)
    movies_raw = await fetch_movies_api()
    if not movies_raw:
        logger.warning("[ZaloPay] No movies found from API")
        return

    interceptor = ApiInterceptor()
    page = await context.new_page()
    interceptor.attach(page)

    try:
        await page.goto(ZALOPAY_URL, wait_until="load", timeout=30000)
        await asyncio.sleep(3)

        await extractor.select_city(page, city)
        await asyncio.sleep(2)

        # Transform + replace toàn bộ movies collection
        movie_pairs = [(transform_movie(raw, order=i), raw) for i, raw in enumerate(movies_raw)]
        movie_pairs = [(doc, raw) for doc, raw in movie_pairs if doc]
        deleted, inserted = await db.replace_all_movies([doc for doc, _ in movie_pairs])
        logger.info(f"[ZaloPay] Movies — deleted={deleted}, inserted={inserted}")

        for movie_doc, movie_raw in movie_pairs:
            logger.info(f"  [ZaloPay] [{movie_doc['id']}] {movie_doc['title']}")

            interceptor.clear()
            clicked = await extractor.click_movie(page, movie_raw)
            if not clicked:
                continue
            await asyncio.sleep(1.5)

            cinemas_raw = interceptor.get_typed("cinemas") or await extractor.extract_cinemas(page)
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

                dates = await extractor.extract_dates(page)
                if not dates:
                    showtimes_raw = interceptor.get_typed("showtimes") or await extractor.extract_showtimes(page)
                    await _save_showtimes(db, showtimes_raw, movie_doc, cinema_doc, "")
                    continue

                for date_info in dates:
                    interceptor.clear()
                    await extractor.click_date_tab(page, date_info)

                    showtimes_raw = interceptor.get_typed("showtimes") or await extractor.extract_showtimes(page)
                    saved = await _save_showtimes(
                        db, showtimes_raw, movie_doc, cinema_doc, date_info.get("date", "")
                    )
                    logger.info(f"    [ZaloPay] {cinema_doc['name']} | {date_info.get('date')} → {saved} showtimes")

            await page.go_back()
            await asyncio.sleep(1)

    except Exception as e:
        logger.error(f"[ZaloPay] Crawl error: {e}", exc_info=True)
    finally:
        await page.close()

    logger.info("[ZaloPay] Crawl complete")


async def _save_showtimes(db, showtimes_raw, movie_doc, cinema_doc, date_override: str) -> int:
    if not showtimes_raw:
        return 0
    entries = []
    for st_raw in showtimes_raw:
        entry = transform_showtime(
            st_raw,
            movie_doc["id"],    # dùng 'id' field (string slug)
            cinema_doc["id"],   # dùng 'id' field (string slug)
            date_override,
            platform=PLATFORM,
        )
        if entry:
            entries.append(entry)
    await db.bulk_upsert_showtime_prices(entries, PLATFORM)
    return len(entries)
