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

---

## Cập nhật data (chạy hàng ngày / hàng tuần)

```bash
# Activate virtualenv nếu chưa
.venv\Scripts\activate

# Chạy ZaloPay crawler (toàn bộ: 3 chain, 5 rạp, ~21 ngày)
python main.py --platforms zalopay
```

Crawler sẽ tự động:
1. Gọi API lấy danh sách 19 phim đang chiếu → replace toàn bộ `movies` collection
2. Xóa toàn bộ `showtimes` cũ
3. Lần lượt crawl CGV (3 rạp) → Lotte (1 rạp) → Galaxy Cinema (1 rạp)
4. Upsert cinemas + insert showtimes mới vào MongoDB

**Thời gian chạy**: ~10–15 phút (5 rạp × 21 ngày)

**Kết quả điển hình** (Hải Phòng):
| Chain | Rạp | Showtimes/run |
|-------|-----|---------------|
| CGV | Aeon Mall, Thùy Dương Plaza, Vincom | ~5,000 |
| Lotte | Lotte Cinema Hải Phòng | ~1,500 |
| Galaxy Cinema | Galaxy Hải Phòng | ~2,300 |
| **Tổng** | **5 rạp** | **~6,300** |

---

## Tùy chọn

```bash
# Chỉ crawl một platform cụ thể
python main.py --platforms zalopay

# Bật debug (dump raw responses vào ./debug_responses/)
set DEBUG=1 && python main.py --platforms zalopay   # Windows
DEBUG=1 python main.py --platforms zalopay           # macOS/Linux

# Verbose log
set LOG_LEVEL=DEBUG && python main.py
```

---

## MongoDB Schema

Database: `movie_tickets` (cấu hình trong `.env`)

**movies**
```json
{
  "_id": ObjectId,
  "id": "movie-5094",
  "title": "Tên phim",
  "genre": "Hành động",
  "duration": "120 phút",
  "poster": "https://...",
  "rating": "T13",
  "status": "now_showing"
}
```

**cinemas**
```json
{
  "_id": ObjectId,
  "id": "cinema-cgv-aeon-mall-hai-phong",
  "name": "CGV Aeon Mall Hải Phòng",
  "address": "Tầng 3 TTTM AEON MALL...",
  "chain": "CGV",
  "city": "Hải Phòng"
}
```

**showtimes**
```json
{
  "_id": ObjectId,
  "id": "st-a1b2c3d4",
  "movieId": "movie-5094",
  "cinemaId": "cinema-cgv-aeon-mall-hai-phong",
  "date": "2026-02-26",
  "time": "10:30",
  "format": "2D",
  "seatType": "Thường",
  "prices": {
    "zalopay": 90000,
    "momo": null,
    "vnpay": null
  }
}
```

---

## Xử lý sự cố

### ZaloPay thay đổi DOM selector
Khi crawler không tìm thấy rạp hoặc suất chiếu:

```bash
# 1. Chạy debug để inspect page
set DEBUG=1 && python main.py --platforms zalopay

# 2. Kiểm tra log — tìm dòng "No cinema items found" hoặc "0 showtimes"
# 3. Tạo debug script tạm để inspect HTML:
#    - Mở browser.py, đổi headless=True → headless=False
#    - Thêm await asyncio.sleep(30) sau bước cần inspect
#    - Chụp outerHTML của element cần phân tích
# 4. Cập nhật selector trong crawler/zalopay_crawler.py

# Các selector quan trọng (đầu file zalopay_crawler.py):
# SEL_CHAIN_BTN   = "button.relative.mr-1"       # nút switch chain
# SEL_CINEMA_ITEM = "span#movie-cinema-button"   # danh sách rạp
# SEL_DATE_BTN    = "button#movie-date-button"   # nút ngày
# SEL_SESSION_BTN = "button#movie-session-button"# nút suất chiếu
```

### Lỗi kết nối MongoDB
```
Cannot connect to MongoDB. Check MONGO_URI in .env
```
→ Kiểm tra `MONGO_URI` trong `.env`, đảm bảo IP đang dùng được whitelist trong MongoDB Atlas.

### Giá vé hiển thị sai (fake data)
Giá hiện tại là **giá cố định theo chain** (xem `_CHAIN_FAKE_PRICE` trong `zalopay_crawler.py`):
- CGV: 90,000đ
- Lotte: 60,000đ
- Galaxy Cinema: 65,000đ

ZaloPay không hiển thị giá trên trang lịch chiếu — cần click vào từng suất để lấy giá thực.
Để cập nhật giá thực, sửa `_CHAIN_FAKE_PRICE` hoặc implement deep crawl vào trang đặt vé.

---

## Lịch chạy tự động (tùy chọn)

Dùng Windows Task Scheduler hoặc cron để chạy hàng ngày:

```bash
# cron (Linux/macOS) — chạy lúc 6:00 sáng mỗi ngày
0 6 * * * cd /path/to/ticket-price-crawler && .venv/bin/python main.py --platforms zalopay >> logs/crawler.log 2>&1
```

```powershell
# Windows Task Scheduler — tạo task chạy script này
$action = New-ScheduledTaskAction -Execute "powershell" `
  -Argument '-NonInteractive -Command "cd \"C:\Work Space 4\ticket-price-crawler\"; .venv\Scripts\python main.py --platforms zalopay >> logs\crawler.log 2>&1"'
$trigger = New-ScheduledTaskTrigger -Daily -At 6am
Register-ScheduledTask -TaskName "MovieCrawler" -Action $action -Trigger $trigger
```
