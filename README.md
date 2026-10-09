# ads-status-bot

Theo dõi [Google Ads publisher status](https://ads.google.com/status/publisher/summary) (AdMob, Ad Manager, AdSense) và báo về Discord khi có incident mới, có update, hoặc incident đã resolved.

- Nguồn dữ liệu: `https://ads.google.com/status/publisher/incidents.json`
- Lịch chạy: GitHub Actions, 10 phút/lần (cron của GitHub có thể bị trễ vài phút)
- State: file `state.json`, workflow tự commit lại sau mỗi lần thay đổi

## Setup

1. Discord: Channel → Edit Channel → Integrations → Webhooks → New Webhook → Copy URL
2. Repo GitHub: Settings → Secrets and variables → Actions → thêm secret `DISCORD_WEBHOOK_URL`
3. Vào tab Actions → chạy tay workflow **Check Google Ads status** lần đầu. Lần đầu bot chỉ ghi nhận các incident đang có, không gửi tin.

Muốn đổi danh sách product thì sửa `WATCHED_PRODUCTS` trong `check_status.py` (id lấy từ `products.json`).

## Chạy local

```bash
python3 check_status.py --dry-run                         # chỉ in ra, không gửi, không ghi state
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/... python3 check_status.py
```

> Repo private chỉ có 2.000 phút Actions/tháng. Chạy 10 phút/lần thì tốn khoảng 4.300 phút, nên nếu để private hãy đổi cron thành `*/30`. Repo public thì không giới hạn.
