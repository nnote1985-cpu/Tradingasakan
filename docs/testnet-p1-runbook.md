# P1 runbook: Testnet worker บน PC

แผน: docs/testnet-development-plan.md §10 P1 (SHADOW/TESTNET routing, LIVE ปิด,
heartbeat/lease, key ใน env, endpoint guards, Telegram + dead-man ภายนอก, process manager)

Worker ใน P1 **ไม่ส่งคำสั่งซื้อขาย** มันถือ writer lease, เขียน heartbeat,
เช็คนาฬิกากับ Testnet, เช็ค key, ping dead-man และส่ง alert เข้า Telegram

## 1. ตั้งค่าบน PC (ครั้งเดียว)

ใช้ `setx` (ไม่ใช่ `set`) เพื่อให้ scheduled task เห็นค่าด้วย แล้วเปิด cmd ใหม่หลังตั้ง
ห้ามวางค่าเหล่านี้ในแชท, repo หรือ Supabase

```
setx TRADING_MODE TESTNET
setx TESTNET_EXECUTION_ENABLED true
setx DATABASE_URL "<connection string ของ Supabase>"
setx BINANCE_TESTNET_API_KEY "<API key ของ testnet>"
setx BINANCE_TESTNET_PRIVATE_KEY_PATH "%USERPROFILE%\.binance\testnet-ed25519.pem"
setx TELEGRAM_BOT_TOKEN "<token ของบอท>"
setx TELEGRAM_CHAT_ID "<chat id>"
setx DEADMAN_PING_URL "https://hc-ping.com/<uuid>"
```

ถ้ามีตัวแปร `BINANCE_API_KEY` หรือ `BINANCE_API_SECRET` (key ของบัญชีจริง) อยู่ในเครื่อง
worker จะไม่ยอมเริ่ม ให้ลบออก (`reg delete HKCU\Environment /v BINANCE_API_KEY /f`)

## 2. Dead-man ภายนอก (healthchecks.io)

1. สมัคร https://healthchecks.io (ฟรี) แล้วสร้าง check ใหม่
2. ตั้ง Period = 1 minute, Grace = 5 minutes
3. Integrations → Telegram → เชื่อมกับบอทของ healthchecks แล้วเปิดให้ check นี้ใช้
4. คัดลอก Ping URL (`https://hc-ping.com/...`) ไปใส่ `DEADMAN_PING_URL`

ถ้า worker หยุด หรือ PC ดับ เกิน 5 นาที healthchecks จะแจ้ง Telegram เอง

## 3. นาฬิกา

P0 วัดได้ว่านาฬิกาช้ากว่า Testnet ~740 ms worker เตือนที่ 500 ms และหยุด signed calls ที่ 1,000 ms
Settings → Time & language → Date & time → เปิด "Set time automatically" แล้วกด **Sync now**
(หรือ cmd แบบ admin: `w32tm /resync /force`) แล้วเช็คด้วยขั้นที่ 4

## 4. ทดลองรันครั้งเดียว

```
cd /d D:\webapp\Tradingasakan
git pull
uv run python scripts/testnet_worker.py --once
```

บรรทัดสุดท้ายควรเป็น `safety RECOVERING, lease <เลข>, clock <ค่า> ms (OK), signing paused False, account ok True, dead-man ok True`
และ Telegram ได้ข้อความ "worker started" กับ "worker stopped"

## 5. ให้รันเองตลอด (process manager)

```
powershell -ExecutionPolicy Bypass -File scripts\windows\install-worker-task.ps1
powershell -Command "Start-ScheduledTask 'Tradingasakan Testnet worker'"
```

Task เริ่มเองตอน logon, รันแบบซ่อนหน้าต่าง และถ้า worker ล่ม `testnet-worker.ps1` จะสั่งเริ่มใหม่
(รอนานขึ้นถ้าล่มติดกัน) log อยู่ที่ `var\worker\worker.log`, สถานะล่าสุดอยู่ที่ `var\worker\heartbeat.json`

## 6. Drills ของ P1 gate

| Drill | ทำอย่างไร | ผลที่ต้องได้ |
| --- | --- | --- |
| ปิดเว็บแล้ว worker เดินต่อ (RT01) | ปิด browser และ API | `last_tick_at` ใน heartbeat.json ยังขยับทุก 2 วินาที |
| ปิด worker แล้วได้ alert จากภายนอก (RT41) | `Stop-ScheduledTask 'Tradingasakan Testnet worker'` | healthchecks แจ้ง Telegram ภายใน ~6 นาที |
| Worker ตัวที่สอง (RT07) | รัน `--once` อีกหน้าต่างขณะ task เดินอยู่ | ตัวที่สองหยุดพร้อม alert "another worker holds the writer lease" |
| Telegram ล่ม (RT42) | ตั้ง `TELEGRAM_BOT_TOKEN` ผิดชั่วคราวแล้วรัน `--once` | worker ไม่ล่ม; alert ค้างใน `var\worker\alerts.jsonl` และส่งเมื่อ token ถูก |

## 7. VPS (ก่อน P5)

ใช้ `deploy/systemd/tradingasakan-worker.service` ใส่ค่าใน `/etc/tradingasakan/worker.env` (mode 600) และเปิด NTP
แล้วรัน `scripts/testnet_discovery.py` บน VPS ก่อน (ADR 0002 §2)

## 8. ผล drill บน PC ของ Note (2026-10-17)

| Drill | ผล |
| --- | --- |
| `--once` ครั้งแรก | key ผ่าน (Ed25519), lease token 1, dead-man ok; นาฬิกา +567 ms (WARN) |
| แก้นาฬิกา | `w32tm /stripchart` เดิม +0.54 s เทียบ time.windows.com → หลังตั้ง `MaxAllowedPhaseOffset=0` และ resync เหลือ ±2 ms; worker วัดได้ +25 ms (OK) |
| ปิด worker แล้วได้ alert จากภายนอก (RT41) | ผ่าน: healthchecks ส่ง "My First Check is DOWN" เข้า Telegram ~6 นาทีหลัง ping สุดท้าย |
| Scheduled task | ผ่าน: เริ่ม 03:40:02 UTC, lease token 2, clock OK, account ok, dead-man ok |
| Worker ตัวที่สอง (RT07) | ผ่าน: ตัวที่สองหยุดเองด้วย "another worker holds the writer lease" (exit 3) |
| ปิดเว็บแล้ว worker เดินต่อ (RT01) | worker เป็น process แยกจาก API และไม่ import โค้ดเว็บ (test); heartbeat เดินขณะที่ไม่ได้เปิดเว็บ |
| Alert ของ worker เข้า Telegram | ผ่าน: started, clock WARN, stopped และ CRITICAL lease เข้าแชทบอทของ Note ครบ |

## 9. P2: feeds (ราคาและบัญชี)

ตั้งแต่ P2 worker รัน feed สามตัวใน thread ของตัวเอง (`trading.ingest`)
- แท่งราคา 1m และ 1d ของ BTCUSDT จาก Mainnet public (`data-stream.binance.vision`) เก็บที่ `var\ingest\bars\` และ backfill จาก REST (`data-api.binance.vision`) ตอนเริ่มและทุกครั้งที่ต่อใหม่ (1m ย้อน 2 วัน, 1d ย้อน 150 วันสำหรับ SMA100)
- ราคา bid/ask ของ BTCUSDT บน Testnet (bookTicker)
- user stream ของบัญชี Testnet ผ่าน WebSocket API (`session.logon` ด้วย key Ed25519) และเทียบกับ REST account/openOrders ตอนเริ่ม หลังต่อใหม่ และทุก 30 วินาที

ถ้าไม่ตรงกัน = CRITICAL; ราคาเงียบเกิน 10 วินาที, kline เงียบเกิน 3 นาที หรือ user stream หลุด = WARNING (หลังเริ่มได้ 30 วินาที)
สถานะอยู่ใน `feeds` ของ `var\worker\heartbeat.json` ปิด feed ได้ด้วย `setx WORKER_FEEDS off`

หลัง `git pull` ให้รีสตาร์ท task เพื่อโหลดโค้ดใหม่:
```
powershell -Command "Stop-ScheduledTask 'Tradingasakan Testnet worker'; Start-ScheduledTask 'Tradingasakan Testnet worker'"
```
