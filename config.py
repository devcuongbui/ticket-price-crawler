import os
from dotenv import load_dotenv

load_dotenv()

# ── MongoDB ───────────────────────────────────────────────────────────────────
# Set MONGO_URI in .env file (see .env.example)
MONGO_URI = os.getenv("MONGO_URI")
if not MONGO_URI:
    raise EnvironmentError("MONGO_URI is not set. Copy .env.example to .env and fill in your credentials.")

DB_NAME = os.getenv("DB_NAME", "movie_tickets")

# ── Crawler settings ──────────────────────────────────────────────────────────
TARGET_CITY = "Hải Phòng"

BASE_URL = "https://zalopay.vn/dat-ve-phim"   # kept for reference

# Timeouts (milliseconds)
PAGE_LOAD_TIMEOUT_MS = 30000
NETWORKIDLE_TIMEOUT_MS = 10000
INTERACTION_DELAY_MS = 1500

# Retry
MAX_RETRIES = 3
RETRY_DELAY_SEC = 2.0

# Debug: dump all API JSON responses to ./debug_responses/
DEBUG = os.getenv("DEBUG", "0") == "1"

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
