"""
Network response interceptor.
Captures XHR/fetch JSON responses from ZaloPay to extract API data directly,
which is more reliable than DOM scraping for Next.js SPAs.
"""
import json
import logging
from pathlib import Path
from typing import Optional
from playwright.async_api import Page, Response

logger = logging.getLogger(__name__)

# Will be set by main.py if DEBUG=True
DEBUG_DIR: Optional[Path] = None


class ApiInterceptor:
    """Captures and classifies JSON API responses from ZaloPay."""

    def __init__(self):
        self._responses: list[dict] = []
        self._seen_patterns: set[str] = set()

    def attach(self, page: Page) -> None:
        """Register response listener on a page."""
        page.on("response", self._handle_response)

    def clear(self) -> None:
        """Clear captured responses (call before each page interaction)."""
        self._responses.clear()

    def get_all(self) -> list[dict]:
        return list(self._responses)

    def get_typed(self, data_type: str) -> list[dict] | None:
        """Return captured data of a given type ('movies', 'cinemas', 'showtimes')."""
        for entry in self._responses:
            if entry.get("type") == data_type:
                return entry.get("items", [])
        return None

    async def _handle_response(self, response: Response) -> None:
        url = response.url
        status = response.status
        content_type = response.headers.get("content-type", "")

        # Skip static assets
        if any(ext in url for ext in [".js", ".css", ".png", ".jpg", ".woff", ".svg", "gtm", "analytics"]):
            return

        # Only capture JSON responses from zalopay domain
        if "zalopay" not in url:
            return
        if status != 200:
            return
        if "json" not in content_type and "javascript" not in content_type:
            return

        try:
            data = await response.json()
        except Exception:
            return

        # Log newly discovered endpoints
        pattern = self._url_pattern(url)
        if pattern not in self._seen_patterns:
            self._seen_patterns.add(pattern)
            logger.info(f"[Interceptor] New API endpoint: {pattern}")

        # Save debug dump if enabled
        if DEBUG_DIR is not None:
            self._dump_debug(url, data)

        # Classify and store
        items, data_type = self._classify(data)
        if items and data_type:
            logger.debug(f"[Interceptor] Captured {len(items)} {data_type} from {pattern}")
            self._responses.append({"type": data_type, "items": items, "url": url})

    def _url_pattern(self, url: str) -> str:
        """Strip query params for pattern matching."""
        return url.split("?")[0]

    def _dump_debug(self, url: str, data) -> None:
        try:
            DEBUG_DIR.mkdir(exist_ok=True)
            slug = url.split("?")[0].rstrip("/").split("/")[-1][:40]
            # Make filename unique by appending count
            idx = len(list(DEBUG_DIR.glob(f"{slug}*.json")))
            fname = DEBUG_DIR / f"{slug}_{idx}.json"
            fname.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.debug(f"Debug dump failed: {e}")

    def _classify(self, data) -> tuple[list, str | None]:
        """
        Try to extract a list of items from the response and classify its type.
        Returns (items_list, type_string) or ([], None).
        """
        # Unwrap common envelope patterns
        items = None
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            for key in ("data", "items", "result", "movies", "cinemas", "showtimes",
                        "listMovie", "listCinema", "listShowtime", "content"):
                val = data.get(key)
                if isinstance(val, list) and val:
                    items = val
                    break

        if not items:
            return [], None

        first = items[0] if items else {}
        if not isinstance(first, dict):
            return [], None

        # Classify by field names
        first_keys = set(str(k).lower() for k in first.keys())

        movie_signals = {"title", "ten_phim", "moviename", "movie_name", "phim", "poster", "genre", "duration", "thoi_luong"}
        cinema_signals = {"cinemaname", "cinema_name", "rap", "theater", "address", "dia_chi", "chain"}
        showtime_signals = {"starttime", "start_time", "showtime", "gio_chieu", "suat_chieu", "time", "price", "gia_ve"}

        if first_keys & movie_signals:
            return items, "movies"
        if first_keys & cinema_signals:
            return items, "cinemas"
        if first_keys & showtime_signals:
            return items, "showtimes"

        return [], None
