"""
Script 2: ZaloPay Showtime Crawler (Playwright DOM)
Target: https://zalopay.vn/dat-ve-phim

Dùng Playwright thao tác DOM để lấy suất chiếu và giá vé từ ZaloPay.
Chỉ cập nhật prices.zalopay trong showtimes collection.

Flow:
  1. Mở trang, đảm bảo chọn Hải Phòng
  2. Với mỗi rạp (CGV / Lotte / Galaxy):
     - Click vào logo rạp
     - Click từng rạp cụ thể trong danh sách
     - Với mỗi ngày trong date slider:
       - Click ngày
       - Đọc tất cả movie section + format group + session buttons
       - Lưu showtime vào MongoDB
"""
import asyncio
import logging
import re
from datetime import datetime, timedelta

from crawler.movies_fetcher import fetch_movies_api
from crawler.transformer import transform_movie, transform_cinema, transform_showtime, make_cinema_id, make_showtime_id, parse_price
from crawler.db import MovieDB

logger = logging.getLogger(__name__)

ZALOPAY_URL = "https://zalopay.vn/dat-ve-phim"
PLATFORM = "zalopay"
CITY = "Hải Phòng"

# Giá tạm theo chain (chưa crawl được giá thực từ trang đặt vé)
_CHAIN_FAKE_PRICE = {
    "CGV":            90_000,
    "Lotte":          60_000,
    "Galaxy Cinema":  65_000,
}

# ── Selectors (từ HTML thực tế) ────────────────────────────────────────────────
# City
SEL_CITY_BTN       = "button:has(p.text-blue-500)"           # button thành phố đang chọn
SEL_CITY_HAIPHONG  = "span.label-bold-large.text-white"      # span Hải Phòng trong dropdown

# Cinema groups (CGV / Lotte / Galaxy)
SEL_CINEMA_GROUP   = "div.flex.overflow-auto button[data-gtm-movie-value]"

# Cinema list items (panel trái)
SEL_CINEMA_ITEM    = "ul li button span#movie-cinema-button"

# Date buttons
SEL_DATE_BTN       = "button#movie-date-button"

# Movie sections (panel phải) - mỗi section có 1 phim
SEL_MOVIE_SECTION  = "div.flex.flex-col.mt-3"
SEL_MOVIE_HREF     = "a[href*='/dat-ve-phim/chi-tiet/']"
SEL_MOVIE_TITLE    = "h3.font-bold.text-base.text-white"

# Format group header (h3 bên trong movie section)
SEL_FORMAT_GROUP   = "div.mt-3"
SEL_FORMAT_HEADER  = "h3.font-bold.text-white"

# Session buttons
SEL_SESSION_BTN    = "button#movie-session-button"


# ── Helpers ────────────────────────────────────────────────────────────────────

def _extract_api_id_from_href(href: str) -> str:
    """
    /dat-ve-phim/chi-tiet/tho-oi-5094 → "5094"
    """
    m = re.search(r"-(\d+)$", href.rstrip("/"))
    return m.group(1) if m else ""


def _parse_format_header(header: str) -> tuple[str, str]:
    """
    "2D Phụ đề English"                    → ("2D", "Thường")
    "2D Phụ đề English | Rạp STARIUM"     → ("2D", "STARIUM")
    "2D Phụ đề English | Rạp Cine & Suite"→ ("2D", "Cine & Suite")
    "3D Phụ đề English"                    → ("3D", "Thường")
    """
    upper = header.upper()
    if "IMAX" in upper:
        fmt = "IMAX"
    elif "3D" in upper:
        fmt = "3D"
    else:
        fmt = "2D"

    if "|" in header:
        rap_part = header.split("|", 1)[1].strip()
        seat_type = re.sub(r"^Rạp\s+", "", rap_part, flags=re.IGNORECASE).strip()
    else:
        seat_type = "Thường"

    return fmt, seat_type


def _build_full_date(day_num: int, ref_date: datetime) -> str:
    """
    Cho day_num (ngày trong tháng) và ngày tham chiếu, trả về YYYY-MM-DD.
    Nếu day_num < ref_date.day thì sang tháng sau.
    """
    year, month = ref_date.year, ref_date.month
    try:
        d = datetime(year, month, day_num)
    except ValueError:
        d = datetime(year, month, 1)

    if d < ref_date.replace(hour=0, minute=0, second=0, microsecond=0):
        # sang tháng sau
        if month == 12:
            d = datetime(year + 1, 1, day_num)
        else:
            try:
                d = datetime(year, month + 1, day_num)
            except ValueError:
                d = datetime(year, month + 1, 1)

    return d.strftime("%Y-%m-%d")


# ── City selection ─────────────────────────────────────────────────────────────

async def _ensure_city(page) -> bool:
    """Đảm bảo đang chọn Hải Phòng. Trả về True nếu thành công."""
    try:
        # Kiểm tra text của button thành phố
        btn = page.locator(SEL_CITY_BTN).first
        text = (await btn.inner_text()).strip()
        if CITY in text:
            logger.info(f"[ZaloPay] City already set: {CITY}")
            return True

        # Chưa đúng → click mở dropdown
        await btn.click()
        await asyncio.sleep(1)

        # Tìm và click "Hải Phòng" trong dropdown
        hp = page.locator(SEL_CITY_HAIPHONG, has_text=CITY).first
        await hp.wait_for(timeout=8000)
        await hp.click()
        await asyncio.sleep(2)

        # Verify lại
        text = (await page.locator(SEL_CITY_BTN).first.inner_text()).strip()
        if CITY in text:
            logger.info(f"[ZaloPay] City selected: {CITY}")
            return True

        logger.warning(f"[ZaloPay] Could not verify city selection")
        return False

    except Exception as e:
        logger.warning(f"[ZaloPay] City selection error: {e}")
        return False


# ── Showtime extraction ────────────────────────────────────────────────────────

async def _extract_showtimes_from_panel(page, date_str: str, cinema_id: str, movie_id_map: dict, chain: str = "") -> list[dict]:
    """
    Đọc toàn bộ movie sections trong panel phải cho một ngày đã chọn.
    movie_id_map: {api_id_str: movie_doc_id}  e.g. {"5094": "movie-5094"}
    chain: tên chain để tra giá fake (CGV / Lotte / Galaxy Cinema)
    Trả về list entries cho bulk_upsert_showtime_prices.
    """
    fake_price = _CHAIN_FAKE_PRICE.get(chain)
    entries = []
    await asyncio.sleep(1.2)

    try:
        await page.wait_for_selector(SEL_MOVIE_SECTION, timeout=8000)
    except Exception:
        logger.debug(f"[ZaloPay] No movie sections found for {date_str}")
        return entries

    sections = await page.query_selector_all(SEL_MOVIE_SECTION)

    for section in sections:
        # Lấy movie_id từ href
        try:
            a_el = await section.query_selector(SEL_MOVIE_HREF)
            if not a_el:
                continue
            href = await a_el.get_attribute("href") or ""
            api_id = _extract_api_id_from_href(href)
            movie_id = movie_id_map.get(api_id)
            if not movie_id:
                # Fallback: thử title matching
                title_el = await section.query_selector(SEL_MOVIE_TITLE)
                title = (await title_el.inner_text()).strip() if title_el else ""
                logger.debug(f"[ZaloPay] Unknown movie api_id={api_id} title={title}")
                continue
        except Exception as e:
            logger.debug(f"[ZaloPay] Error parsing movie section: {e}")
            continue

        # Lấy các format groups (mỗi div.mt-3 chứa 1 h3 + grid session buttons)
        format_groups = await section.query_selector_all(SEL_FORMAT_GROUP)

        for group in format_groups:
            try:
                h3 = await group.query_selector(SEL_FORMAT_HEADER)
                if not h3:
                    continue
                header_text = (await h3.inner_text()).strip()
                fmt, seat_type = _parse_format_header(header_text)

                # Lấy tất cả session buttons trong group
                session_btns = await group.query_selector_all(SEL_SESSION_BTN)

                for btn in session_btns:
                    try:
                        # Start time: label đầu tiên có class text-white
                        time_labels = await btn.query_selector_all("label.text-white")
                        if not time_labels:
                            continue
                        start_time_raw = (await time_labels[0].inner_text()).strip()
                        # Normalize HH:MM
                        m = re.match(r"(\d{1,2}:\d{2})", start_time_raw)
                        if not m:
                            continue
                        time_val = m.group(1)
                        h, mn = time_val.split(":")
                        time_val = f"{int(h):02d}:{mn}"

                        st_id = make_showtime_id(movie_id, cinema_id, date_str, time_val, seat_type)

                        entries.append({
                            "showtime_id": st_id,
                            "movie_id":    movie_id,
                            "cinema_id":   cinema_id,
                            "date":        date_str,
                            "time":        time_val,
                            "format":      fmt,
                            "seat_type":   seat_type,
                            "price":       fake_price,
                        })

                    except Exception as e:
                        logger.debug(f"[ZaloPay] Session parse error: {e}")

            except Exception as e:
                logger.debug(f"[ZaloPay] Format group error: {e}")

    return entries


# ── Date iteration ─────────────────────────────────────────────────────────────

async def _crawl_cinema_dates(page, cinema_id: str, cinema_name: str, movie_id_map: dict, db, chain: str = "") -> int:
    """
    Lấy tất cả date buttons, click từng cái và extract showtimes.
    Trả về tổng số showtime entries đã lưu.
    """
    total = 0
    today = datetime.now()

    try:
        await page.wait_for_selector(SEL_DATE_BTN, timeout=8000)
    except Exception:
        logger.warning(f"[ZaloPay] No date buttons for {cinema_name}")
        return 0

    date_btns = await page.query_selector_all(SEL_DATE_BTN)
    logger.info(f"  [ZaloPay] {cinema_name} — {len(date_btns)} dates")

    for i, date_btn in enumerate(date_btns):
        try:
            # Lấy text ngày (p.heading-bold-small chứa số ngày)
            day_el = await date_btn.query_selector("p.heading-bold-small, p:last-child")
            day_text = (await day_el.inner_text()).strip() if day_el else ""
            day_num = int(re.sub(r"\D", "", day_text)) if re.sub(r"\D", "", day_text) else 0

            if day_num == 0:
                continue

            date_str = _build_full_date(day_num, today)

            await date_btn.click()
            await asyncio.sleep(1)

            entries = await _extract_showtimes_from_panel(page, date_str, cinema_id, movie_id_map, chain=chain)

            if entries:
                await db.bulk_upsert_showtime_prices(entries, PLATFORM)
                total += len(entries)
                logger.info(f"    [ZaloPay] {cinema_name} | {date_str} → {len(entries)} showtimes")

        except Exception as e:
            logger.warning(f"[ZaloPay] Date {i} error for {cinema_name}: {e}")

    return total


# ── Main crawler ───────────────────────────────────────────────────────────────

async def crawl_zalopay(context, db, city: str = CITY) -> None:
    """Main ZaloPay crawler entry point."""
    logger.info(f"[ZaloPay] Starting crawl for {city}")

    # Script 1: lấy và replace movies
    movies_raw = await fetch_movies_api()
    if not movies_raw:
        logger.warning("[ZaloPay] No movies from API")
        return

    movie_pairs = [(transform_movie(raw, order=i), raw) for i, raw in enumerate(movies_raw)]
    movie_pairs = [(doc, raw) for doc, raw in movie_pairs if doc]
    deleted, inserted = await db.replace_all_movies([doc for doc, _ in movie_pairs])
    logger.info(f"[ZaloPay] Movies — deleted={deleted}, inserted={inserted}")

    # Map: api_id_str → movie_id  (e.g. "5094" → "movie-5094")
    movie_id_map = {
        str(raw.get("id", "")): doc["id"]
        for doc, raw in movie_pairs
        if raw.get("id")
    }

    page = await context.new_page()

    try:
        await page.goto(ZALOPAY_URL, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(3)

        await _ensure_city(page)
        await asyncio.sleep(2)

        # Lấy các nhóm rạp (CGV, Lotte, Galaxy)
        cinema_groups = await page.query_selector_all(SEL_CINEMA_GROUP)
        group_names = []
        for g in cinema_groups:
            val = await g.get_attribute("data-gtm-movie-value") or ""
            group_names.append(val)
        logger.info(f"[ZaloPay] Cinema groups: {group_names}")

        for gi, group_btn in enumerate(cinema_groups):
            group_name = group_names[gi]
            logger.info(f"\n[ZaloPay] ── Group: {group_name} ──")

            try:
                await group_btn.click()
                await asyncio.sleep(1.5)
            except Exception as e:
                logger.warning(f"[ZaloPay] Cannot click group {group_name}: {e}")
                continue

            # Lấy danh sách rạp trong group
            cinema_spans = await page.query_selector_all(SEL_CINEMA_ITEM)
            cinema_names = [(await s.inner_text()).strip() for s in cinema_spans]
            logger.info(f"  [ZaloPay] Cinemas in {group_name}: {cinema_names}")

            for ci, cinema_name in enumerate(cinema_names):
                if not cinema_name:
                    continue

                # Upsert cinema doc
                cinema_doc = {
                    "id":      make_cinema_id(cinema_name),
                    "name":    cinema_name,
                    "address": "",
                    "chain":   group_name,
                    "city":    city,
                }
                await db.upsert_cinema(cinema_doc)
                cinema_id = cinema_doc["id"]

                # Click vào rạp cụ thể
                try:
                    spans = await page.query_selector_all(SEL_CINEMA_ITEM)
                    if ci >= len(spans):
                        continue
                    await spans[ci].click()
                    await asyncio.sleep(1.5)
                except Exception as e:
                    logger.warning(f"  [ZaloPay] Cannot click cinema {cinema_name}: {e}")
                    continue

                # Crawl từng ngày
                total = await _crawl_cinema_dates(page, cinema_id, cinema_name, movie_id_map, db, chain=group_name)
                logger.info(f"  [ZaloPay] {cinema_name} → {total} total showtimes")

    except Exception as e:
        logger.error(f"[ZaloPay] Crawl error: {e}", exc_info=True)
    finally:
        await page.close()

    logger.info("[ZaloPay] Crawl complete")
