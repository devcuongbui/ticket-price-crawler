"""
MongoDB async operations using Motor.

Schema webapp (QUAN TRỌNG - phải match chính xác):
  movies/cinemas:
    _id   = ObjectId (auto-generated, không set thủ công)
    id    = string slug ("movie-1", "cinema-1")  ← backend query bằng field này
    ...các fields khác

  showtimes:
    _id      = ObjectId (auto-generated)
    id       = string ("st-{counter}")
    movieId  = "movie-1"  (trỏ tới movies.id)
    cinemaId = "cinema-1" (trỏ tới cinemas.id)
    ...
    prices: {momo, zalopay, vnpay}

Upsert strategy:
  - movies/cinemas: replace_one filter by "id" field (không phải _id)
  - showtimes: update_one with $set platform price + $setOnInsert base fields
    filter by {movieId, cinemaId, date, time, format, seatType}
    chỉ update giá của platform đang crawl, không đụng giá platform khác
"""
import asyncio
import logging
from motor.motor_asyncio import AsyncIOMotorClient

logger = logging.getLogger(__name__)


class MovieDB:
    def __init__(self, uri: str, db_name: str):
        self.client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=15000)
        self.db = self.client[db_name]
        self._showtime_counter = None  # lazy-load

    async def ping(self) -> bool:
        try:
            await self.client.admin.command("ping")
            return True
        except Exception as e:
            logger.error(f"MongoDB ping failed: {e}")
            return False

    async def ensure_indexes(self) -> None:
        """Create indexes (idempotent)."""
        # movies.id — unique slug index
        await self.db.movies.create_index("id", unique=True, name="movies_id_idx")
        # cinemas.id — unique slug index (backend queries by this)
        await self.db.cinemas.create_index("id", unique=True, name="cinemas_id_idx")
        # showtimes indexes
        await self.db.showtimes.create_index("id", name="showtimes_id_idx")
        await self.db.showtimes.create_index(
            [("movieId", 1), ("cinemaId", 1), ("date", 1)],
            name="movie_cinema_date_idx",
        )
        await self.db.showtimes.create_index(
            [("movieId", 1), ("date", 1)],
            name="movie_date_idx",
        )
        logger.info("MongoDB indexes ensured")

    async def _next_showtime_id(self) -> str:
        """Get next showtime counter from DB to generate consistent id."""
        count = await self.db.showtimes.count_documents({})
        return f"st-{count + 1}"

    # ── Movies ────────────────────────────────────────────────────────────────

    async def replace_all_movies(self, docs: list[dict]) -> tuple[int, int]:
        """
        Xóa toàn bộ movies cũ và insert lại từ đầu.
        Trả về (deleted, inserted).
        """
        deleted = (await self.db.movies.delete_many({})).deleted_count
        if docs:
            result = await self.db.movies.insert_many(docs)
            inserted = len(result.inserted_ids)
        else:
            inserted = 0
        logger.info(f"Movies replaced: deleted={deleted}, inserted={inserted}")
        return deleted, inserted

    async def upsert_movie(self, doc: dict) -> None:
        """
        Upsert movie by 'id' field (string slug).
        doc must have 'id' key. _id is managed by MongoDB.
        """
        movie_id = doc.get("id")
        if not movie_id:
            logger.warning(f"Movie has no 'id' field, skipping: {doc}")
            return
        # filter by 'id' field (not _id) — matches webapp's query pattern
        result = await self.db.movies.update_one(
            {"id": movie_id},
            {"$set": {k: v for k, v in doc.items() if k != "_id"}},
            upsert=True,
        )
        action = "inserted" if result.upserted_id else "updated"
        logger.debug(f"Movie {action}: [{movie_id}] {doc.get('title', '')}")

    # ── Cinemas ───────────────────────────────────────────────────────────────

    async def upsert_cinema(self, doc: dict) -> None:
        """
        Upsert cinema by 'id' field.
        Backend queries cinemas with: find({"id": {"$in": [...]}})
        """
        cinema_id = doc.get("id")
        if not cinema_id:
            logger.warning(f"Cinema has no 'id' field, skipping: {doc}")
            return
        result = await self.db.cinemas.update_one(
            {"id": cinema_id},
            {"$set": {k: v for k, v in doc.items() if k != "_id"}},
            upsert=True,
        )
        action = "inserted" if result.upserted_id else "updated"
        logger.debug(f"Cinema {action}: [{cinema_id}] {doc.get('name', '')}")

    # ── Showtimes — partial price update ─────────────────────────────────────

    async def upsert_showtime_price(
        self,
        showtime_id: str,
        movie_id: str,
        cinema_id: str,
        date: str,
        time: str,
        fmt: str,
        seat_type: str,
        platform: str,
        price: int | None,
    ) -> None:
        """
        Upsert showtime và update CHỈ giá của platform này.
        Platform khác không bị ảnh hưởng.

        platform: "momo" | "zalopay" | "vnpay"
        """
        filter_doc = {
            "movieId":  movie_id,
            "cinemaId": cinema_id,
            "date":     date,
            "time":     time,
            "format":   fmt,
            "seatType": seat_type,
        }
        await self.db.showtimes.update_one(
            filter_doc,
            {
                "$set": {
                    f"prices.{platform}": price,
                },
                "$setOnInsert": {
                    "id":       showtime_id,
                    "movieId":  movie_id,
                    "cinemaId": cinema_id,
                    "date":     date,
                    "time":     time,
                    "format":   fmt,
                    "seatType": seat_type,
                    "prices": {
                        "momo":    None,
                        "zalopay": None,
                        "vnpay":   None,
                        platform:  price,
                    },
                },
            },
            upsert=True,
        )

    async def bulk_upsert_showtime_prices(
        self, entries: list[dict], platform: str
    ) -> None:
        """
        Bulk upsert showtime prices cho một platform.
        Mỗi entry: {showtime_id, movie_id, cinema_id, date, time, format, seat_type, price}
        """
        if not entries:
            return
        await asyncio.gather(*[
            self.upsert_showtime_price(
                showtime_id=e["showtime_id"],
                movie_id=e["movie_id"],
                cinema_id=e["cinema_id"],
                date=e["date"],
                time=e["time"],
                fmt=e["format"],
                seat_type=e["seat_type"],
                platform=platform,
                price=e["price"],
            )
            for e in entries
        ])
        logger.debug(f"Upserted {len(entries)} {platform} prices")

    # ── Stats ─────────────────────────────────────────────────────────────────

    async def get_stats(self) -> dict:
        movies    = await self.db.movies.count_documents({})
        cinemas   = await self.db.cinemas.count_documents({})
        showtimes = await self.db.showtimes.count_documents({})
        complete  = await self.db.showtimes.count_documents({
            "prices.momo":    {"$ne": None},
            "prices.zalopay": {"$ne": None},
            "prices.vnpay":   {"$ne": None},
        })
        return {
            "movies": movies,
            "cinemas": cinemas,
            "showtimes": showtimes,
            "showtimes_complete": complete,
        }

    def close(self) -> None:
        self.client.close()
