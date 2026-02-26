# Movie Ticket Price Crawler

Crawler lấy lịch chiếu và giá vé phim từ **ZaloPay** cho thành phố Hải Phòng, lưu vào MongoDB.

> MoMo và VNPay crawler là stub — chưa implement thực tế.

---

## Cấu trúc

```
ticket-price-crawler/
├── crawler/
│   ├── browser.py          # Playwright browser setup (headless Chromium)
│   ├── db.py               # Motor async MongoDB operations
│   ├── extractor.py        # DOM scraping helpers
│   ├── interceptor.py      # Network response capture
│   ├── movies_fetcher.py   # Gọi ZaloPay API lấy danh sách phim
│   ├── transformer.py      # Raw data → MongoDB schema
│   ├── zalopay_crawler.py  # ZaloPay crawler (hoạt động)
│   ├── momo_crawler.py     # MoMo crawler (stub)
│   └── vnpay_crawler.py    # VNPay crawler (stub)
├── config.py               # Cấu hình từ .env
├── main.py                 # Entry point
├── requirements.txt
├── .env.example            # Template cấu hình
└── README.md
```

---

## Cài đặt lần đầu

```bash
# 1. Clone repo và tạo virtualenv
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS/Linux

# 2. Cài dependencies
pip install -r requirements.txt

# 3. Cài Chromium cho Playwright
playwright install chromium

# 4. Tạo .env từ template
cp .env.example .env
# Mở .env, điền MONGO_URI thực tế
```