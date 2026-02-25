"""
Movie Ticket Price Crawler — 3 Platforms
Crawls ZaloPay, MoMo, VNPay for Hải Phòng and inserts prices into MongoDB.

Each crawler only writes its own platform price field:
  zalopay_crawler → prices.zalopay
  momo_crawler    → prices.momo
  vnpay_crawler   → prices.vnpay

Usage:
  python main.py                          # All 3 platforms
  python main.py --platforms zalopay      # ZaloPay only
  python main.py --platforms momo vnpay   # MoMo + VNPay only
  set DEBUG=1 && python main.py           # Debug: dump API responses
"""
import asyncio
import argparse
import logging
import sys
import io
from pathlib import Path

# Fix Vietnamese UTF-8 output on Windows terminal
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import config
from crawler.browser import create_browser_context
from crawler.db import MovieDB
from crawler.zalopay_crawler import crawl_zalopay
from crawler.momo_crawler import crawl_momo
from crawler.vnpay_crawler import crawl_vnpay
import crawler.interceptor as interceptor_module

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

if config.DEBUG:
    interceptor_module.DEBUG_DIR = Path("./debug_responses")
    logger.info("DEBUG mode ON — API responses saved to ./debug_responses/")

# ── Platform registry ─────────────────────────────────────────────────────────

CRAWLERS = {
    "zalopay": crawl_zalopay,
    "momo":    crawl_momo,
    "vnpay":   crawl_vnpay,
}


# ── Main ──────────────────────────────────────────────────────────────────────

async def main(platforms: list[str]) -> None:
    logger.info(f"Starting crawl | City: {config.TARGET_CITY} | Platforms: {platforms}")

    db = MovieDB(config.MONGO_URI, config.DB_NAME)
    ok = await db.ping()
    if not ok:
        logger.error("Cannot connect to MongoDB. Check MONGO_URI in .env")
        sys.exit(1)
    logger.info(f"Connected to MongoDB: {config.DB_NAME}")
    await db.ensure_indexes()

    pw, browser, context = await create_browser_context()

    try:
        for platform in platforms:
            crawler_fn = CRAWLERS[platform]
            logger.info(f"\n{'='*50}")
            logger.info(f"  Platform: {platform.upper()}")
            logger.info(f"{'='*50}")
            try:
                await crawler_fn(context, db, city=config.TARGET_CITY)
            except Exception as e:
                logger.error(f"{platform} crawler failed: {e}", exc_info=True)

    finally:
        await browser.close()
        await pw.stop()

        stats = await db.get_stats()
        logger.info(
            f"\n{'='*50}\n"
            f"  Crawl complete\n"
            f"  Movies:             {stats['movies']}\n"
            f"  Cinemas:            {stats['cinemas']}\n"
            f"  Showtimes total:    {stats['showtimes']}\n"
            f"  Showtimes complete: {stats['showtimes_complete']} (all 3 prices)\n"
            f"{'='*50}"
        )
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Movie ticket price crawler")
    parser.add_argument(
        "--platforms",
        nargs="+",
        choices=list(CRAWLERS.keys()),
        default=list(CRAWLERS.keys()),
        help="Which platforms to crawl (default: all 3)",
    )
    args = parser.parse_args()
    asyncio.run(main(args.platforms))
