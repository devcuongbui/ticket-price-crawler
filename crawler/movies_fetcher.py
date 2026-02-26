"""
Script 1: Fetch danh sách phim đang chiếu (REST API — không cần browser)

Gọi API công khai của ZaloPay, transform và replace toàn bộ
movies collection trong MongoDB.

Usage (standalone):
    python -m crawler.movies_fetcher

Usage (import):
    from crawler.movies_fetcher import fetch_and_replace_movies
"""
import asyncio
import logging
import sys
import io

import httpx

logger = logging.getLogger(__name__)

ZALOPAY_MOVIES_API = "https://zlp-movie-api.zalopay.vn/v2/movie/web/data/film/showing"

_HEADERS = {
    "origin": "https://zalopay.vn",
    "referer": "https://zalopay.vn/",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
    ),
}


async def fetch_movies_api() -> list[dict]:
    """Gọi REST API, trả về danh sách phim thô (raw JSON)."""
    async with httpx.AsyncClient(headers=_HEADERS, timeout=15) as client:
        resp = await client.get(ZALOPAY_MOVIES_API)
        resp.raise_for_status()
        body = resp.json()
    movies = body.get("data") or []
    logger.info(f"[MoviesFetcher] API returned {len(movies)} movies")
    return movies


async def fetch_and_replace_movies(db) -> tuple[int, int]:
    """
    Fetch phim từ API, transform và replace toàn bộ movies collection.
    Trả về (deleted, inserted).
    """
    from crawler.transformer import transform_movie

    movies_raw = await fetch_movies_api()
    if not movies_raw:
        logger.warning("[MoviesFetcher] No movies returned from API")
        return 0, 0

    docs = [d for i, raw in enumerate(movies_raw) if (d := transform_movie(raw, order=i))]
    logger.info(f"[MoviesFetcher] Transformed {len(docs)}/{len(movies_raw)} movies")

    deleted, inserted = await db.replace_all_movies(docs)
    return deleted, inserted


# ── Standalone entry point ────────────────────────────────────────────────────

async def _main():
    import config
    from crawler.db import MovieDB

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    db = MovieDB(config.MONGO_URI, config.DB_NAME)
    if not await db.ping():
        print("Cannot connect to MongoDB. Check MONGO_URI in .env")
        sys.exit(1)

    deleted, inserted = await fetch_and_replace_movies(db)

    print(f"\n{'='*50}")
    print(f"Movies updated — {config.DB_NAME}.movies")
    print(f"  Deleted:  {deleted}")
    print(f"  Inserted: {inserted}")
    print(f"{'='*50}")

    db.close()


if __name__ == "__main__":
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    asyncio.run(_main())
