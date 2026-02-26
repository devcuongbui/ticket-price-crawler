"""
Data transformation: raw API/DOM data -> MongoDB schema.

Schema webapp THỰC TẾ (từ phân tích main.py):
  movies:   { id: "movie-1", title, genre, duration, poster, rating, status }
            _id = ObjectId (auto, không set)
  cinemas:  { id: "cinema-1", name, address, chain, city }
            _id = ObjectId (auto, không set)
            backend query: find({"id": {"$in": [...]}})
  showtimes:{ id: "st-N", movieId: "movie-1", cinemaId: "cinema-1",
              date, time, format, seatType, prices: {momo, zalopay, vnpay} }
            _id = ObjectId (auto, không set)

Lưu ý: transform functions trả về dict với 'id' field (không phải '_id').
        db.py sẽ filter upsert bằng 'id' field.
"""
import re
import hashlib
import logging
import unicodedata

logger = logging.getLogger(__name__)

# ── ID helpers ────────────────────────────────────────────────────────────────

def _slugify(text: str) -> str:
    """Convert Vietnamese text to URL-safe ASCII slug."""
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text)
    return text[:60]


def make_movie_id(title: str, api_id: str = "") -> str:
    """
    Tạo movie id string. Ưu tiên dùng api_id nếu có (để stable mapping).
    Nếu api_id là số (zalopay), prefix thêm "movie-".
    """
    if api_id and str(api_id).strip():
        raw = str(api_id).strip()
        # Nếu đã có prefix movie- thì giữ nguyên
        if raw.startswith("movie-"):
            return raw
        return f"movie-{raw}"
    return f"movie-{_slugify(title)}"


def make_cinema_id(name: str, api_id: str = "") -> str:
    if api_id and str(api_id).strip():
        raw = str(api_id).strip()
        if raw.startswith("cinema-"):
            return raw
        return f"cinema-{raw}"
    return f"cinema-{_slugify(name)}"


def make_showtime_id(movie_id: str, cinema_id: str, date: str, time: str, seat_type: str) -> str:
    """MD5 hash của composite key → deterministic, idempotent."""
    key = f"{movie_id}|{cinema_id}|{date}|{time}|{seat_type}"
    h = hashlib.md5(key.encode()).hexdigest()[:10]
    return f"st-{h}"


# ── Value parsers ─────────────────────────────────────────────────────────────

def parse_price(raw) -> int | None:
    """Convert '85.000đ', '85,000 VND', 85000 → int. None nếu invalid."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        val = int(raw)
    else:
        digits = re.sub(r"[^\d]", "", str(raw))
        if not digits:
            return None
        val = int(digits)
    # Vietnamese ticket prices: 10k – 1M VND
    if val < 10_000 or val > 1_000_000:
        return None
    return val


def parse_duration(raw) -> int:
    """Convert '132 phút', '2h12m', 132 → minutes."""
    if not raw:
        return 0
    if isinstance(raw, (int, float)):
        return int(raw)
    raw = str(raw)
    m = re.match(r"(\d+)\s*h\s*(\d+)?", raw, re.IGNORECASE)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2) or 0)
    m = re.match(r"(\d+):(\d+)", raw)
    if m:
        h, mn = int(m.group(1)), int(m.group(2))
        return h * 60 + mn if h < 10 else h
    digits = re.sub(r"[^\d]", "", raw)
    return int(digits) if digits else 0


def parse_time(raw) -> str:
    """Normalize → HH:MM (24h). '' nếu không parse được."""
    if not raw:
        return ""
    raw = str(raw).strip()
    if re.match(r"^\d{1,2}:\d{2}$", raw):
        h, m = raw.split(":")
        return f"{int(h):02d}:{m}"
    m = re.search(r"T(\d{2}:\d{2})", raw)
    if m:
        return m.group(1)
    if re.match(r"^\d{10,13}$", raw):
        import datetime
        ts = int(raw)
        if ts > 1e10:
            ts //= 1000
        dt = datetime.datetime.utcfromtimestamp(ts)
        return dt.strftime("%H:%M")
    return raw[:5]


def parse_date(raw) -> str:
    """Normalize → YYYY-MM-DD. '' nếu không parse được."""
    if not raw:
        return ""
    raw = str(raw).strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", raw):
        return raw
    m = re.match(r"(\d{4}-\d{2}-\d{2})T", raw)
    if m:
        return m.group(1)
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", raw)
    if m:
        return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    return raw[:10]


def infer_chain(cinema_name: str) -> str:
    n = cinema_name.upper()
    for chain in ["CGV", "LOTTE", "GALAXY", "BHD", "CINESTAR", "DCINE", "MEGA GS", "BETA"]:
        if chain in n:
            return chain.title()
    return "Other"


# ── Helper ────────────────────────────────────────────────────────────────────

def _get(d: dict, *keys, default=""):
    for k in keys:
        v = d.get(k)
        if v not in (None, "", [], {}):
            return v
    return default


# ── Transform functions ───────────────────────────────────────────────────────

def transform_movie(raw: dict, order: int = 0) -> dict | None:
    """
    Raw API/DOM data → movies document.
    Trả về dict với 'id' field (string slug), KHÔNG có '_id'.
    MongoDB tự generate _id = ObjectId khi insert.
    """
    api_id = str(_get(raw, "id", "movieId", "movie_id", "ma_phim", default=""))
    title  = str(_get(raw, "title", "ten_phim", "movieName", "name", "movie_name", default="")).strip()
    if not title:
        logger.warning(f"Movie has no title, skipping: {raw}")
        return None

    genre = _get(raw, "genre", "the_loai", "categories", "category", "genres", default="")
    if isinstance(genre, list):
        genre = ", ".join(str(g) for g in genre)
    genre = str(genre)

    duration_raw = _get(raw, "duration", "thoi_luong", "runtime", "runningTime", default=0)

    # ZaloPay lồng poster trong images.type1_path; fallback về flat fields
    images = raw.get("images") or {}
    poster = str(
        images.get("type1_path")
        or _get(raw, "poster", "poster_url", "thumbnail", "image", "img", "banner", default="")
    )

    # ZaloPay dùng age (số tuổi) thay vì string rating
    age_raw = raw.get("age")
    if age_raw is not None:
        rating = f"T{age_raw}" if int(age_raw) > 0 else "P"
    else:
        rating = str(_get(raw, "rating", "do_tuoi", "ageRating", "age_rating", "censorship", default=""))

    return {
        "id":       make_movie_id(title, api_id),   # ← 'id' field, not '_id'
        "title":    title,
        "genre":    genre,
        "duration": parse_duration(duration_raw),
        "poster":   poster,
        "rating":   rating,
        "status":   "now_showing",
        "order":    order,  # thứ tự từ API response (0 = ưu tiên cao nhất)
    }


def transform_cinema(raw: dict, city: str) -> dict | None:
    """
    Raw API/DOM data → cinemas document.
    Trả về dict với 'id' field. MongoDB tự generate _id.
    """
    api_id = str(_get(raw, "id", "cinemaId", "cinema_id", "ma_rap", default=""))
    name   = str(_get(raw, "name", "cinema_name", "cinemaName", "ten_rap", "rap", default="")).strip()
    if not name:
        logger.warning(f"Cinema has no name, skipping: {raw}")
        return None

    address = str(_get(raw, "address", "dia_chi", "location", "addr", default=""))
    chain   = str(_get(raw, "chain", "chainName", "chain_name", "cum_rap", default=""))
    if not chain:
        chain = infer_chain(name)

    return {
        "id":      make_cinema_id(name, api_id),    # ← 'id' field, not '_id'
        "name":    name,
        "address": address,
        "chain":   chain,
        "city":    city,
    }


def transform_showtime(
    raw: dict,
    movie_id: str,
    cinema_id: str,
    date_override: str = "",
    platform: str = "zalopay",
) -> dict | None:
    """
    Raw API/DOM data → showtime entry for db.upsert_showtime_price().

    movie_id  : 'id' của movie (e.g. "movie-1")
    cinema_id : 'id' của cinema (e.g. "cinema-1")
    platform  : "momo" | "zalopay" | "vnpay"

    Trả về dict với 'id' (showtime slug) và price parsed.
    """
    time_raw = _get(raw, "time", "gio_chieu", "startTime", "start_time", "showTime", default="")
    time_val = parse_time(time_raw)
    if not time_val:
        logger.debug(f"Showtime has no time: {raw}")
        return None

    date_raw = _get(raw, "date", "ngay_chieu", "showDate", "show_date", default=date_override)
    date_val = parse_date(date_raw) or date_override
    if not date_val:
        logger.debug(f"Showtime has no date: {raw}")
        return None

    fmt = str(_get(raw, "format", "dinh_dang", "screen_type", "screenType", default="2D"))
    if "3D" in fmt.upper():
        fmt = "3D"
    elif "IMAX" in fmt.upper():
        fmt = "IMAX"
    else:
        fmt = "2D"

    seat_type = str(_get(raw, "seatType", "seat_type", "loai_ghe", "type", default="Thường"))
    if any(w in seat_type.upper() for w in ["VIP", "PREMIUM", "GOLD"]):
        seat_type = "VIP"
    else:
        seat_type = "Thường"

    price_raw = _get(raw, "price", "gia_ve", "ticketPrice", "ticket_price", "amount", default=None)
    price = parse_price(price_raw)

    st_id = make_showtime_id(movie_id, cinema_id, date_val, time_val, seat_type)

    # Return format phù hợp với db.bulk_upsert_showtime_prices()
    return {
        "showtime_id": st_id,
        "movie_id":    movie_id,
        "cinema_id":   cinema_id,
        "date":        date_val,
        "time":        time_val,
        "format":      fmt,
        "seat_type":   seat_type,
        "price":       price,
    }
