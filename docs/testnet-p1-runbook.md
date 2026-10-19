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
| ปิด worker แล้วได้ alert จากภายนอก (RT41) | `powershell -ExecutionPolicy Bypass -File scripts\windows\stop-worker.ps1` | healthchecks แจ้ง Telegram ภายใน ~6 นาที |
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

หลัง `git pull` ให้รีสตาร์ท worker เพื่อโหลดโค้ดใหม่ (อย่าใช้ `Stop-ScheduledTask` อย่างเดียว เพราะมันปิดแค่ลูป PowerShell
ส่วน Python ยังค้างอยู่ ถือ lease และไฟล์ log ไว้):
```
powershell -ExecutionPolicy Bypass -File scripts\windows\restart-worker.ps1
```
หยุดถาวร: `powershell -ExecutionPolicy Bypass -File scripts\windows\stop-worker.ps1`

ผลบน PC ของ Note (2026-10-17 03:57 UTC, lease token 3): klines/quotes/account เชื่อมต่อทั้งสาม, 1m ล่าสุด 03:56 UTC,
1d ล่าสุด 2026-10-16 (แท่งที่ปิดแล้ว), gaps 0, revisions 0, quote age ~2 s, reconcile MATCH (10,000 USDT, 1 BTC, ไม่มี order ค้าง)

Drill ตัดเน็ต ~1 นาที (2026-10-17 ~04:00 UTC): การต่อ Supabase เพื่อต่อ lease ล้มก่อน worker จึงหยุดเอง (exit 3, alert "database unreachable")
และ loop เริ่มใหม่เมื่อเน็ตกลับ (lease token 4, เริ่ม 04:00:18) ตัวใหม่ backfill จาก REST จน 1m ล่าสุด 03:59 UTC, bar_gaps 0, reconcile MATCH
นี่ทดสอบทาง restart + backfill (RT05) ส่วนทาง reconnect ใน process เดียว (RT03) ทดสอบด้วย fixture ใน tests/unit/test_ingest_p2.py

## 10. P3: เก็บลง Supabase และหน้า Worker บนเว็บ

ตั้งแต่ P3 worker เขียนข้อมูลเหล่านี้ลง Supabase ทุกประมาณ 10 วินาที โดยใช้การเชื่อมต่อเดียวกับ lease:

- แท่งราคาที่ปิดแล้ว (`trading.market_bars`) ถ้ามีค่าใหม่มาแก้ภายหลัง ค่าแรกจะคงอยู่ และค่าที่มาแก้จะเก็บแยกใน `market_bar_revisions`
- เทรดบน Testnet (`exec_trades`) พร้อมบัญชีคู่แยกตามสินทรัพย์ (`exec_journal_lines`) ค่าธรรมเนียมคิดในสินทรัพย์จริงที่ถูกหัก
- รอบบัญชี (`exec_account_epochs`) เปิดเองตอนตรวจบัญชีครั้งแรก โดยใช้ยอดจาก Exchange เป็นยอดเปิด ถ้า Testnet ถูก reset ให้ปิดรอบด้วย INTERRUPTED_TESTNET_RESET ซึ่งเป็นการตัดสินใจของคน ระบบไม่ปิดเอง
- ผลตรวจบัญชีกับ REST (`exec_reconciliations`) เก็บเมื่อผลเปลี่ยน เมื่อไม่ตรงทุกครั้ง และทุก 15 นาที ยอดเงินล่าสุดอยู่ใน `exec_balances`
- สถานะ worker (`worker_heartbeats`) ซึ่งเว็บแท็บ **Worker** อ่าน

ถ้าเขียนลง Supabase ไม่ได้ worker จะเก็บข้อมูลไว้ในหน่วยความจำ ส่ง alert หนึ่งครั้ง แล้วลองใหม่รอบถัดไป ถ้าฐานข้อมูลหายไปจริง worker จะเสีย lease แล้วหยุดตามเดิม พอเริ่มใหม่ก็จะส่งแท่งราคาที่ยังขาดจากไฟล์ในเครื่องให้ครบ ส่วนเทรดที่เกิดระหว่างที่ worker หยุด จะดึงย้อนจาก REST ได้ใน P4 ถ้าไม่อยากให้เขียนลง Supabase ให้ตั้ง `WORKER_PERSIST=off`

**อัปเดต worker:** ดับเบิลคลิก `scripts\windows\update-worker.cmd` ไฟล์นี้จะรัน `git pull` จาก main แล้ว restart worker ให้ ถ้า pull ไม่ผ่าน จะไม่ restart

**ดูสถานะ:** เปิดเว็บแล้วไปที่แท็บ Worker (`#worker`) จะเห็นการเชื่อมต่อ นาฬิกา ผลตรวจบัญชี และยอดเงินทดสอบ หน้านี้อ่านอย่างเดียว ปุ่มหยุดหรือเริ่มบอทจะมาใน P5

## 11. P4: ส่งคำสั่งบน Spot Testnet และ conformance

ตั้งแต่ P4 worker ส่งและติดตามคำสั่งบน Binance Spot Testnet ได้ (เงินทดสอบเท่านั้น) ทุก 2 วินาทีมันจะทำตามลำดับนี้

1. ตอนเริ่ม โหลดคำสั่งที่ค้างจาก Supabase คำสั่งที่บันทึกว่ากำลังส่ง (SUBMITTING) จะถูกถือเป็น UNKNOWN แล้วถาม Exchange ด้วย client order id ไม่ส่งซ้ำ ส่วนคำสั่งที่ worker ตัวก่อนรับไปแต่ยังไม่ได้ส่ง จะปิดเป็น EXPIRED
2. สถานะความปลอดภัยเริ่มที่ RECOVERING และจะเป็น RUNNING (เปิดคำสั่งซื้อใหม่ได้) ก็ต่อเมื่อคำสั่งที่เก็บไว้ไม่มีตัวที่ยังไม่รู้ผล ทุกคำสั่งที่เปิดอยู่บน Testnet เป็นของบอท และผลตรวจบัญชีล่าสุดเป็น "ตรงกัน"
3. คำสั่งที่ไม่ได้คำตอบ (timeout, 5xx) เป็น UNKNOWN แล้วถามซ้ำทุก 2 วินาที ถ้าผ่าน 30 วินาทียังไม่เจอ จะส่ง alert สีแดง หยุดเปิดคำสั่งใหม่ (SOFT_PAUSED) และถามต่อทุก 30 วินาทีโดยไม่ปล่อยเงินที่จองไว้ เมื่อเจอแล้วจะกลับมาเปิดคำสั่งใหม่เอง
4. ถ้า Binance ตอบ 429/418 worker จะหยุดเรียก REST จนครบเวลา Retry-After
5. ทุก 10 นาที (และตอนเริ่ม) ดึงเทรดจาก `myTrades` ต่อจากเทรดล่าสุดที่เก็บไว้ เทรดที่เกิดตอน worker ปิดอยู่จึงเข้าบัญชีคู่ครบ

คำสั่งส่งได้เฉพาะเมื่อนาฬิกาผ่าน (ไม่หยุดเซ็นคำสั่ง) และกุญแจผ่านการตรวจบัญชี ส่งได้เฉพาะ BTCUSDT และคำสั่งที่รอเกิน 60 วินาทีโดยยังไม่ได้ส่งจะหมดอายุ

**เปิด conformance (tn-conformance-v1):** ตั้งตัวแปรแล้ว restart worker

```
setx WORKER_CONFORMANCE on
```

แล้วดับเบิลคลิก `scripts\windows\update-worker.cmd` (ต้องเปิดหน้าต่างใหม่หลัง setx) ทุก 4 ชั่วโมงตามเวลา UTC (00, 04, 08, …) และทันทีที่เปิดครั้งแรก worker จะ

1. MARKET BUY ประมาณ 1.2 เท่าของ min notional (ราว 6 USDT)
2. MARKET SELL คืนเท่าที่ได้จริง (หักค่าธรรมเนียมถ้าหักเป็น BTC)
3. LIMIT BUY ต่ำกว่า bid 5% แล้วยกเลิกทันที
4. LIMIT BUY ที่ราคา bid รอ 10 นาทีเผื่อ fill บางส่วน แล้วยกเลิก
5. MARKET SELL ส่วนที่ได้จากข้อ 4 ถ้ามูลค่าเกิน min notional (ถ้าน้อยกว่านั้นจะเหลือเป็นเศษ)

เงินที่จองได้รวมไม่เกิน 200 USDT ผลใช้ตรวจ lifecycle, บัญชี และ reconcile เท่านั้น ไม่ใช่ผลกลยุทธ์ ปิดด้วย `setx WORKER_CONFORMANCE off` แล้ว restart ก่อนเริ่ม P5 soak

**ดูผล:** แท็บ Worker มีแถว "คำสั่ง Testnet" (ส่ง/ยกเลิกวันนี้, ผลแต่ละแบบ) และ "Conformance" (รอบปัจจุบันและขั้นที่ทำอยู่) คำสั่งที่ยังไม่รู้ผลจะขึ้นเป็นรายการสีแดง

**Gate ของ P4:** ต้องเห็นคำสั่งเล็กไป-กลับจริงบน Testnet (รอบแรกของ conformance), conformance รันอย่างน้อย 3 วันมี fill และ cancel จริง และ drill เหล่านี้ผ่าน

| Drill | ทำอย่างไร | ต้องเห็น |
| --- | --- | --- |
| Restart ระหว่างรอบ | ปิด worker (Task Scheduler → End) ตอนที่ LIMIT ข้อ 4 ยังเปิดอยู่ แล้วเปิดใหม่ | เริ่มที่ RECOVERING, ถามคำสั่งเดิมแล้วทำรอบต่อ ไม่ส่งซ้ำ |
| คำสั่งที่บอทไม่รู้จัก | วาง LIMIT เองบนเว็บ Testnet แล้ว restart worker | alert "open Testnet orders the bot does not know" และไม่เป็น RUNNING จนกว่าจะยกเลิกคำสั่งนั้น |
| ปิดเน็ตตอนส่ง | ยากจะจับจังหวะบน PC จึงทดสอบด้วยโค้ด (`tests/integration/test_executor_p4.py`) | UNKNOWN → ถามซ้ำ → หยุดเปิดคำสั่งใหม่หลัง 30 วินาที |

## 12. P5: Guardian และ HALT drill

Guardian รันใน worker ทุก tick (ภายใต้ writer lease เดียวกับคำสั่ง) ไม่รอ strategy หรือ LLM

- ทุกครั้งที่มีราคาสด (ไม่เกิน 5 วินาที) จะ mark แต่ละ book ที่ bid: allocation + เงินสดที่ fill ของ book นั้นขยับ + BTC ที่ถือ × bid
- ระดับ `spot.tn.v1` (เท่ากับ `spot.v1`): ขาดทุน 5% ในวัน UTC เดียว = แจ้งเตือน, drawdown 15% จากจุดสูงสุด = แจ้งเตือน, drawdown 25% = HALT
- HALT ทั้งบัญชีและ latch: ขึ้น safety epoch, บล็อก entry, ยกเลิกคำสั่งที่เปิดอยู่, ขาย BTC ของแต่ละ book ด้วย MARKET ผ่าน gateway ปกติ, ขายซ้ำถ้ามี fill มาช้า แล้วเป็น HALTED เมื่อไม่มีคำสั่งเปิด ไม่มีคำสั่งที่ไม่รู้ผล และทุก book ถือน้อยกว่าขั้นต่ำของ exchange
- เที่ยงคืนหรือ restart ไม่ล้าง HALT; ทุกการแจ้งเตือน HALT, FLAT_CONFIRMED และ RESUME เก็บถาวรใน `trading.exec_risk_events`
- ตอนนี้ guard เฉพาะ `tn-conformance-v1` (200 USDT) เมื่อ `WORKER_CONFORMANCE=on`; R10a เพิ่มใน P5 PR 2

คำสั่ง (รันบน PC หรือ VPS ที่มี `DATABASE_URL`; สคริปต์ไม่คุยกับ Binance):

```
uv run python scripts/testnet_request.py status
uv run python scripts/testnet_request.py halt --actor note --reason "HALT drill"
uv run python scripts/testnet_request.py resume --actor note --reason "drill done"
```

RESUME ทำได้เฉพาะตอน HALTED และจะผ่าน RECOVERING ก่อน: entry เริ่มอีกครั้งเมื่อการเช็กบัญชีเป็น MATCH เท่านั้น และจุดสูงสุดของทุก book เริ่มใหม่จาก equity ตอน resume

HALT drill (P5 gate): หยุด conformance ก่อน (§5.4) แล้ว `halt` → ดูว่า Telegram ได้ CRITICAL, Worker tab เป็น HALTING แล้ว HALTED, บน Testnet ไม่มีคำสั่งค้าง → `resume` → RECOVERING แล้ว RUNNING
