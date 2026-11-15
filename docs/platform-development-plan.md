# แผนพัฒนาแพลตฟอร์ม: บอท Spot + Futures, กราฟเทคนิคอล และ Frontend

**Version:** 0.2 (Note อนุมัติลำดับงาน 15 พ.ย. 2026)
**วันที่:** 15 พฤศจิกายน 2026
**Code baseline ที่ตรวจ:** main f1b9247 (v0.2 ตรวจซ้ำที่ 3643d93)
**สถานะ:** แผนเท่านั้น ยังไม่มีโค้ด ไม่ได้สร้าง key ไม่ได้ส่งคำสั่ง และไม่ได้เปลี่ยน worker ที่รันอยู่
**แผนย่อยของแต่ละ milestone:** M3 → [m3-strategy-core-plan.md](m3-strategy-core-plan.md)

เอกสารนี้ต่อจาก [testnet-development-plan.md](testnet-development-plan.md) (P0–P6 ทำแล้ว)
และรวมงานที่เหลือ 4 เรื่องไว้ในแผนเดียว: Spot, Futures, กราฟเทคนิคอล และ Frontend
ส่วน P7–P9 (ข่าว, context, specialists) และ P11 (Options) ยังอยู่ในแผนเดิม ไม่ได้ย้ายมา

## 0. เป้าหมายและขอบเขต

### 0.1 ผลลัพธ์ที่ต้องได้

1. บอทหนึ่งตัว = บัญชี + ตลาด (Spot หรือ USD-M Perp) + กลยุทธ์ + เงิน + risk profile ที่ตั้งค่าและ approve จากเว็บได้
2. บอท Spot และบอท Futures รันพร้อมกันบน Binance Testnet คนละบัญชี ใช้ executor และ Guardian ชุดเดียวกัน
3. หน้า monitor อัปเดตภายในไม่กี่วินาทีหลังเกิด order, fill, การเปลี่ยนสถานะ หรือเหตุการณ์ความเสี่ยง
4. กราฟแท่งเทียนที่แสดงอินดิเคเตอร์ จุดเข้า/ออก stop และเส้น liquidation โดยใช้ค่าเดียวกับที่บอทใช้ตัดสินใจ
5. มุมมองพอร์ตรวมที่เห็น exposure ข้ามบัญชีและตลาด

### 0.2 ข้อกำหนดที่ล็อกไว้ (ไม่เปลี่ยนในแผนนี้)

- **LOCKED:** execution เฉพาะ Binance Testnet; `LIVE_SUPPORTED_IN_BUILD=False` คงเดิม; ไม่มี Mainnet trading key
- **LOCKED:** หนึ่งบัญชีต่อหนึ่งผลิตภัณฑ์มีผู้ส่งคำสั่งตัวเดียว; Futures ใช้ key, client และ stream แยกจาก Spot
- **LOCKED:** Futures leverage 1x, isolated margin
- **LOCKED:** เว็บไม่ถือ trading key และไม่สั่ง worker โดยตรง; worker เป็นฝ่ายดึง config ที่ approve แล้ว
- **LOCKED:** แผนและเกณฑ์ผ่านประกาศก่อนโค้ดทุก PR; C2 (ม.ค.–ต.ค. 2026) ใช้ได้ครั้งเดียวตามที่ Note เลือก
- **DISCOVERY:** host, endpoint, rate limit และพฤติกรรม funding ของ Futures Testnet ต้องวัดจากของจริงก่อนใช้ ห้ามเดา

### 0.3 ไม่รวม

เงินจริงและ Mainnet orders, leverage เกิน 1x, cross margin, hedge mode, เหรียญอื่นนอกรายการที่ Note อนุมัติ,
Options, การวาดกราฟด้วยมือ (trend line, Fibonacci) และการส่งคำสั่งจากกราฟ

## 1. สถานะปัจจุบัน

| ส่วน | มีแล้ว | ยังขาด |
| --- | --- | --- |
| Spot execution | `execution/binance_spot.py`, executor, reservation, outbox, UNKNOWN/reconcile, lease | กลยุทธ์ผูกกับ runtime ทีละตัว (`runtime/r10a.py`, `runtime/ai_r2.py`) |
| Futures | `InstrumentType.PERP_FUTURE`, `shadow/trend_perp_book.py` (paper), วิจัย R7/R11 | adapter, position ledger, stop ฝั่ง Exchange, Guardian สำหรับ margin |
| Endpoint guard | `runtime/endpoints.py` มี `Venue.SPOT_TESTNET` ค่าเดียว | `Venue.USDM_TESTNET` |
| Guardian | HALT 25%, ขาดทุนรายวัน, ราคา Testnet ห่างจาก Mainnet | mark price, margin ratio, ระยะถึง liquidation, stop coverage |
| ตั้งค่าบอท | `bot_profile_drafts` (ร่าง, `execution_supported: false`) | entity บอท, การ approve, worker ดึง config |
| Monitor | แท็บ Worker poll ทุก 10–15 วินาที | push แบบ realtime, มุมมองตามบอท, หน้าจอ Futures |
| กราฟ | `LineChart.tsx` (เส้น equity รายวัน, SVG เขียนเอง) | แท่งเทียน, อินดิเคเตอร์, marker ของเทรด |
| อินดิเคเตอร์ | `features/indicators.py`: EMA, ATR, previous high/low; SMA, Donchian, ADX อยู่ในไฟล์กลยุทธ์ | API ที่ส่งแท่งราคาและอินดิเคเตอร์ให้เว็บ |
| ข้อมูลสด | worker ingest แท่ง 1m/1d และ quote; เว็บต่อ `stream.binance.com` เองเพื่อแสดงราคา | แท่งหลาย timeframe และ mark price ถึงเว็บ |

## 2. โครงสร้างเป้าหมาย

```
Strategy (กฎ + params + version)
   └─ ส่งออก: target weight ต่อ instrument  (Spot 0…1, Perp −1…+1)
Bot (บัญชี + instrument + strategy version + allocation + risk profile + mode)
   └─ Order planner: target weight → OrderIntent ตามกฎของตลาด
Executor (เดิม) ── ExchangeAdapter ──┬─ BinanceSpotTestnet (เดิม)
                                     └─ BinanceUsdmTestnet (ใหม่)
Guardian (เดิม + กฎ Futures)
Supabase (authority) ── Realtime ──> เว็บ
API (อ่านอย่างเดียว + คำขอของ operator)
```

หลักการข้อมูล: worker เขียน Supabase, เว็บรับการเปลี่ยนแปลงผ่าน Realtime และ poll เป็นทางสำรอง
ค่าที่แสดงบนกราฟคำนวณฝั่ง server ด้วยโค้ดเดียวกับที่กลยุทธ์ใช้ เว็บไม่คำนวณอินดิเคเตอร์เอง

## 3. Workstream A — แกนกลยุทธ์และ Spot

### A1. Strategy contract

- interface เดียว: รับแท่งราคาที่ปิดแล้ว + สถานะ ส่งออก `TargetDecision` (instrument, target weight, เหตุผล, input hash, version)
- Spot ปฏิเสธ weight ติดลบที่ชั้น planner ไม่ใช่ที่กลยุทธ์
- ย้าย R10a มาใช้ contract นี้เป็น `tn-r10a-code-v2` โดยกฎไม่เปลี่ยน

**เกณฑ์ผ่าน:** replay ข้อมูลช่วง soak ของ v1.1 แล้ว v2 ให้ decision และ intent ตรงกันทุกรายการ

### A2. Order planner

- แปลง target weight เป็นคำสั่งตาม `TradingRules` (lot, tick, min notional) และ allocation ของบอท
- ไม่ส่งคำสั่งเมื่อส่วนต่างต่ำกว่า min notional; บันทึกเหตุผล
- ใช้ร่วมกันทั้ง Spot และ Perp; ส่วนต่างอยู่ที่ flag (`reduce_only`) และเครื่องหมาย

**เกณฑ์ผ่าน:** unit test ครอบ rounding, min notional, เงินไม่พอ, เปลี่ยนฝั่ง (Perp), เพิ่ม/ลดบางส่วน

### A3. กลยุทธ์ Spot หลายตัว

- E1 Donchian (H4, มี stop) เป็นกลยุทธ์ intraday ตัวแรกบน contract ใหม่ หลังผล paper ครบตามแผน E1
- stop ของ Spot: OCO หรือ stop-limit ฝั่ง Exchange ถ้า discovery ยืนยันว่า Testnet รองรับ ไม่งั้น Guardian เฝ้าเอง
- หนึ่งบัญชีรันได้หนึ่งบอท บอทเพิ่มต้องใช้บัญชี Testnet ใหม่ (ข้อจำกัดเดิม RT35)

**เกณฑ์ผ่าน:** soak 14 วัน ไม่มี order ที่ค้าง UNKNOWN เกิน 5 นาที และ stop ครอบคลุมทุกสถานะที่เปิด

## 4. Workstream B — Futures (P10)

| ขั้น | งาน | เกณฑ์ผ่าน |
| --- | --- | --- |
| **B0** Discovery | สคริปต์แบบ `scripts/testnet_discovery.py` สำหรับ USD-M Testnet: host, สิทธิ์ key, rate limit, filters, position mode, ชนิดคำสั่งที่รองรับ, funding ถูกหักจริงหรือไม่, user stream | หลักฐาน JSON ใน `docs/adr/0003-evidence/` (ไม่มี secret) และ ADR 0003 ที่ Note อนุมัติ |
| **B1** Guard + credentials | `Venue.USDM_TESTNET`, host แยกใน `SIGNED_HOSTS`, key แยกไฟล์ env, Spot key ใช้กับ Futures ไม่ได้และกลับกัน | test: URL ของ Mainnet futures ถูกปฏิเสธ; key ข้ามผลิตภัณฑ์ถูกปฏิเสธตอนเริ่ม |
| **B2** Adapter | `execution/binance_usdm.py`: rules, submit (MARKET, STOP_MARKET reduce-only), cancel, query, position, leverage/margin set + read-back, income (funding); ขยาย `ExchangeAdapter`; เพิ่ม futures ใน `tests/fake_binance.py` | unit test: partial fill, timeout → UNKNOWN, cancel ชนกับ fill, reduce-only ถูกปฏิเสธเมื่อไม่มีสถานะ |
| **B3** Position ledger | ตาราง `trading.tn_positions`, `tn_funding`, realized PnL; reconcile กับ `positionRisk`; account epoch | restart, ส่งซ้ำ, replay stream แล้วสถานะไม่ซ้ำ; ยอดตรง Exchange ทุกรอบ reconcile |
| **B4** User stream + mark | `ORDER_TRADE_UPDATE`, `ACCOUNT_UPDATE`, listenKey keepalive, mark price stream, gap → resync | drill: ตัด stream 2 นาทีแล้วสถานะกลับมาถูกโดยไม่มีคำสั่งซ้ำ |
| **B5** Guardian Futures | ตรวจ leverage = 1x และ isolated ก่อนทุก entry; STOP_MARKET reduce-only ภายใน 5 วินาทีหลัง fill ที่ ≥25% จากราคาเข้า; ตรวจ stop ถูกฝั่ง/จำนวนหลัง partial fill; HALT 25% เช็กทุก mark event; ระยะถึง liquidation | drill 5 ข้อ: kill worker, stop หาย, stop จำนวนผิด, เปลี่ยน margin mode จากภายนอก, mark price ค้าง |
| **B6** R11 บน Testnet | `tn-r11-code-v1` บน contract ของ A1, allocation เท่า R10a | soak 28 วัน: ไม่มีสถานะที่ไม่มี stop เกิน 5 วินาที, funding บันทึกครบ, ไม่มี UNKNOWN ค้าง |

ผลตอบแทนบน Testnet ไม่ใช่หลักฐาน alpha (§12.2 ของแผนเดิม) B6 วัดความถูกต้องของระบบเท่านั้น

## 5. Workstream C — การตั้งค่าบอท

### C1. Entity และ lifecycle

ตารางใหม่ `trading.bots` (append-only ต่อ revision เหมือน `bot_profile_drafts`):

| ฟิลด์ | ค่า |
| --- | --- |
| `bot_id`, `revision`, `hash` | ตัวตนและเวอร์ชัน |
| `account`, `venue` | เช่น `DEFAULT` / `SPOT_TESTNET`, `FUT-1` / `USDM_TESTNET` |
| `instrument` | symbol + type |
| `strategy` | template, version, timeframe, params (pin) |
| `allocation`, `risk_profile` | เงินของบอทและชุดขีดจำกัด |
| `futures` | leverage (1), margin mode (isolated), ระยะ stop; ว่างเมื่อเป็น Spot |
| `mode` | SHADOW หรือ TESTNET |
| `state` | draft → approved → running → halted → retired |

- approve ต้องเป็น operator/admin และบันทึกชื่อ เวลา และ hash ที่ approve
- แก้ค่าของบอทที่รันอยู่ = revision ใหม่ที่ต้อง approve อีกครั้ง; worker เปลี่ยน config เมื่อไม่มีสถานะเปิด หรือเมื่อ operator ยืนยันให้ปิดก่อน
- worker รายงาน `bot_id@revision` และ hash ที่กำลังรันใน heartbeat

**เกณฑ์ผ่าน:** config ที่ไม่ได้ approve ไม่ถูก worker ใช้; hash ไม่ตรง = worker ไม่เริ่มและแจ้งเตือน;
สองบอทชี้บัญชีเดียวกันถูกปฏิเสธที่ API และที่ worker

### C2. Validation ฝั่ง server

- params อยู่ในขอบเขตของ catalog; timeframe ที่กลยุทธ์รองรับ
- allocation ไม่เกิน free balance ของบัญชีตอน approve
- Futures: leverage ≠ 1 หรือ margin ≠ isolated ถูกปฏิเสธ; ระยะ stop ต้องใกล้กว่าระยะ liquidation ที่ 1x
- เว็บตรวจซ้ำเพื่อบอกผู้ใช้เร็วขึ้น แต่ server เป็นตัวตัดสิน

## 6. Workstream D — Realtime

- เปิด Supabase Realtime บนตารางที่เว็บต้องเห็นสด: orders, fills, positions, risk events, heartbeat, decisions
  ผ่าน view หรือ policy แบบอ่านอย่างเดียวสำหรับผู้ใช้ที่ allowlist (ตอนนี้ RLS ปิดไม่ให้ client อ่านตรง ต้องออกแบบ policy ใหม่และผ่าน advisor)
- ทางเลือกสำรอง: SSE จาก API ถ้า policy ของ Realtime ซับซ้อนเกิน เลือกใน D0 หลังทดลองทั้งสองแบบ
- ราคา: worker เขียน quote/mark ล่าสุดของ Testnet ลงตารางสถานะทุก 1–2 วินาที; เว็บแสดงคู่กับราคา Mainnet และส่วนต่าง
- ทุกค่าบนจอมีอายุข้อมูล; เกินเกณฑ์ขึ้นสถานะ "ค้าง" ด้วยข้อความและไอคอน ไม่ใช้สีอย่างเดียว
- polling เดิมคงไว้เป็นทางสำรองเมื่อ channel หลุด

**เกณฑ์ผ่าน:** fill บน Testnet ขึ้นจอภายใน 3 วินาที (วัด p95 จาก 50 รายการ); ตัด channel แล้วกลับมาไม่มีรายการหายหรือซ้ำ;
ผู้ใช้ที่ไม่อยู่ใน allowlist อ่านไม่ได้

## 7. Workstream E — กราฟเทคนิคอล

### E1. ข้อมูลและ API

- `GET /api/v1/charts/{symbol}/bars?venue&timeframe&from&to` คืนแท่ง OHLCV ที่ปิดแล้ว + แท่งที่กำลังก่อตัวติดป้ายแยก
- `GET /api/v1/charts/{symbol}/indicators?...&set=` คืนค่าอินดิเคเตอร์ที่คำนวณฝั่ง server
- `GET /api/v1/bots/{bot_id}/overlays` คืน marker ของบอท: entry/exit (จาก fills จริง), stop ที่ active, เส้น HALT, ราคา liquidation, จุดตัดสินใจและเหตุผล
- timeframe: 1h, 4h, 1d ในรอบแรก (ตรงกับที่กลยุทธ์ใช้); 1m/5m/15m เพิ่มเมื่อมีกลยุทธ์ที่ใช้
- แหล่งแท่ง: ตารางแท่งของ worker สำหรับช่วงล่าสุด, archive ที่ตรวจ checksum สำหรับย้อนหลัง; ช่วงที่ขาดแสดงเป็นช่องว่าง ไม่เติมเอง

### E2. อินดิเคเตอร์ชุดแรก

| กลุ่ม | รายการ | ใช้กับ |
| --- | --- | --- |
| ซ้อนบนราคา | SMA100 + แถบ ±2%, EMA20/50/200, Donchian 20/10 | R10a, R11, E1 |
| แผงล่าง | ATR14, ADX14, Volume | E1, stop แบบ ATR |
| Futures | Mark price, funding rate รายรอบ, basis perp/spot | R11 |

- ย้าย SMA, Donchian, ADX จากไฟล์กลยุทธ์มารวมใน `features/indicators.py` โดยกลยุทธ์เรียกใช้ที่เดียวกัน
- **เกณฑ์ผ่าน:** ค่าที่ API คืนเท่ากับค่าที่กลยุทธ์ใช้ตัดสินใจในวันเดียวกัน (test เทียบกับ decision ที่บันทึกไว้ ผิดได้ไม่เกิน 1e-9)

### E3. Component กราฟ

- ไลบรารี: `lightweight-charts` ของ TradingView (DISCOVERY: ตรวจ license, ข้อกำหนด attribution และขนาด bundle ก่อนเพิ่ม dependency)
  ทางเลือกสำรองคือขยาย `LineChart.tsx` เป็นแท่งเทียน SVG ถ้า license ไม่เหมาะ
- แท่งเทียน + volume, แผงอินดิเคเตอร์เปิด/ปิดได้, crosshair พร้อมค่าทุก series, เลือก timeframe, ซูมและเลื่อน
- marker: ลูกศรเข้า/ออกพร้อมราคาและจำนวน, เส้น stop (ทึบ = active บน Exchange, ประ = ตามแผน), เส้น liquidation, เส้น HALT
- คลิก marker เปิดรายละเอียด order/fill และ decision ที่ทำให้เกิดคำสั่งนั้น
- โหลดแบบ lazy (แยก bundle) เพราะใช้เฉพาะหน้ารายละเอียด
- การเข้าถึง: ตารางข้อมูลแทนกราฟสำหรับ screen reader, ขา/ลงแยกด้วยรูปทรงนอกจากสี, เคารพ `prefers-reduced-motion`

**เกณฑ์ผ่าน:** แท่งล่าสุดอัปเดตโดยไม่โหลดกราฟใหม่; 2,000 แท่ง + 4 อินดิเคเตอร์เลื่อนได้ลื่นบนเครื่องของ Note;
ใช้ได้ที่ความกว้าง 375px

### E4. โหมด backtest

- กราฟเดียวกันเปิดกับผล backtest (R10a, R11, E1): marker จากเทรดของ simulator, equity และ drawdown ในแผงล่าง
- ติดป้าย BACKTEST / SHADOW / TESTNET ชัดเจนบนกราฟทุกครั้ง ไม่ปนกัน

## 8. Workstream F — Frontend

### F1. โครงหน้า

| เมนู | เนื้อหา |
| --- | --- |
| ภาพรวม | พอร์ตรวม: equity, exposure ต่อตลาด, บอทที่ต้องดู, การแจ้งเตือนล่าสุด |
| บอท | รายการบอท (กรอง Spot/Futures/สถานะ) → หน้ารายละเอียดบอท |
| ตลาด | กราฟเทคนิคอลต่อ symbol, ราคา Testnet/Mainnet, funding |
| งานวิจัย | backtest, paper books, AI scorecard (ของเดิม) |
| ระบบ | worker, stream, reconcile, Guardian events (แท็บ Worker เดิม แยกเป็น component) |
| ตั้งค่า | คลังกลยุทธ์, risk profiles, สิทธิ์ |

- แถบสถานะบนสุดแสดงทุกหน้า: mode, จำนวน worker ที่ heartbeat ปกติ, สถานะ Guardian, ปุ่ม HALT ทั้งหมด
- เปลี่ยนจาก hash routing เป็น route จริงของ `react-router` (มีอยู่แล้วใน dependency) เพื่อ deep link ไปบอทหรือ order ได้

### F2. หน้ารายละเอียดบอท

| ส่วน | Spot | Futures |
| --- | --- | --- |
| หัว | ชื่อ, สถานะ, `revision` ที่รันจริงจาก heartbeat, mode | เหมือนกัน |
| สถานะ | USDT/BTC, exposure %, PnL วันนี้และรอบบัญชี | ฝั่ง, จำนวน, entry, mark, uPnL, liquidation และระยะ %, margin ratio |
| การป้องกัน | drawdown เทียบ HALT 25%, stop (ถ้ามี) | stop บน Exchange: active / หาย / จำนวนไม่ตรง, funding รอบถัดไป |
| กราฟ | E3 พร้อม overlay ของบอท | + mark price และเส้น liquidation |
| Feed | decisions, orders, fills, risk events เรียงตามเวลา | + funding ที่หักจริง |
| ควบคุม | HALT / RESUME ของบอท (คำขอของ operator แบบเดิม) | เหมือนกัน |

### F3. Wizard ตั้งค่าบอท

1. ตลาดและบัญชี (Spot / Perp; บัญชีที่ถูกใช้แล้วเลือกไม่ได้พร้อมเหตุผล)
2. กลยุทธ์ (จาก catalog หรือ preset; แสดงผล backtest ของค่าที่เลือกถ้ามี)
3. เงินและความเสี่ยง (ช่อง Futures แสดงเมื่อเลือก Perp; leverage แสดงเป็น 1x แก้ไม่ได้)
4. ทบทวน: สรุป + diff กับ revision ก่อนหน้า → บันทึกร่าง หรือส่ง approve

- error ใต้ช่องที่ผิด, โฟกัสช่องแรกที่ผิดหลังส่ง, ร่างบันทึกอัตโนมัติ
- แทน `window.confirm` ด้วย dialog ของระบบ; ปุ่มอันตรายแยกตำแหน่งและสี พร้อมข้อความว่าจะเกิดอะไร
- `DraftProfiles.tsx` แยกเป็น component ต่อขั้น; ร่างเดิมใน `bot_profile_drafts` อ่านได้ต่อและแปลงเป็นบอทได้

### F4. คุณภาพ

- design token เดิม (`--surface-*`, `--text-*`) ใช้ต่อ; ตัวเลขใช้ `tabular-nums`; ไฮไลต์เฉพาะค่าที่เปลี่ยน 150–300ms
- สถานะโหลด/ว่าง/ผิดพลาดครบทุกแผง; ตารางที่เกิน 50 แถวใช้ virtual list
- test: vitest ต่อ component, และ flow ตั้งค่าบอทกับ fake API; ตรวจที่ 375 / 768 / 1440px

## 9. Milestones และลำดับ

| # | Milestone | ประกอบด้วย | ต้องเสร็จก่อน |
| --- | --- | --- | --- |
| **M1** | Monitor Spot แบบ realtime | D (Spot), F1 แถบสถานะ, F2 ส่วน Spot (ยังไม่มีกราฟ) | – |
| **M2** | กราฟเทคนิคอล | E1–E3 สำหรับ Spot, E4 | – |
| **M3** | แกนกลยุทธ์ | A1, A2, `tn-r10a-code-v2` | – |
| **M4** | บอทตั้งค่าได้ | C1, C2, F3, worker ดึง config | M3 |
| **M5** | Futures พื้นฐาน | B0–B4 | M3 |
| **M6** | Futures บน Testnet | B5, B6, F2 ส่วน Futures, อินดิเคเตอร์ Futures (E2) | M4, M5 |
| **M7** | Spot หลายกลยุทธ์ | A3 (E1 บน Testnet) | M4, ผล paper ของ E1 |
| **M8** | พอร์ตรวม | exposure ข้ามบัญชี, HALT ระดับพอร์ต, หน้าภาพรวมใหม่ | M6 |

M1, M2, M3 ไม่ขึ้นต่อกัน ทำขนานได้ และไม่แตะ worker ที่รันอยู่ (M3 ออกเป็น experiment ใหม่ ไม่แก้ v1.1)
แต่ละ milestone แตกเป็น PR ย่อยพร้อมแผนของตัวเองตามวิธีเดิม

## 10. Migration ที่คาดว่าต้องมี

| Migration | ตาราง |
| --- | --- |
| bots | `trading.bots`, `trading.bot_approvals` |
| realtime views | view อ่านอย่างเดียว + policy สำหรับ Realtime |
| chart bars | ดัชนีและ view สำหรับแท่งหลาย timeframe (ถ้าตารางแท่งเดิมไม่พอ) |
| futures | `trading.tn_positions`, `trading.tn_funding`, คอลัมน์ `venue`, `reduce_only`, `position_side` ใน orders |
| portfolio risk | `trading.portfolio_risk_state` |

ทุก migration ทดสอบบน PostgreSQL ชั่วคราวและอัปเดต `supabase/schema_fingerprint.txt` ตามวิธีเดิม

## 11. ความเสี่ยงของแผน

| ความเสี่ยง | ผลกระทบ | การรับมือ |
| --- | --- | --- |
| Futures Testnet ต่างจาก Mainnet (funding, liquidity, reset) | ผลทดสอบไม่สะท้อนของจริง | B0 วัดก่อน; ติดป้าย TESTNET; ไม่ใช้เป็นหลักฐาน alpha |
| เปิด Realtime ทำให้ข้อมูลรั่ว | ผู้ไม่มีสิทธิ์เห็นคำสั่งและยอด | view เฉพาะคอลัมน์ที่ต้องใช้, policy ตาม allowlist, ตรวจด้วย advisor ก่อน merge |
| กราฟแสดงค่าต่างจากที่บอทใช้ | ผู้ใช้ตัดสินใจจากข้อมูลผิด | คำนวณที่ server ที่เดียว + test เทียบ decision |
| config จากเว็บผิดแล้วบอทรันทันที | คำสั่งผิดขนาดหรือผิดตลาด | approve แยกขั้น, validation ฝั่ง server, hash ตรวจที่ worker, เริ่มที่ SHADOW |
| stop ฝั่ง Exchange ไม่ถูกวาง | สถานะ Futures ไม่มีการป้องกัน | Guardian ลดสถานะเมื่อวางไม่ได้ใน 5 วินาที; drill ใน B5 |
| งานขนานหลายสายชนกัน | merge ยาก, regress | M1–M3 แตะไฟล์คนละกลุ่ม; contract ของ A1 ล็อกก่อน M4/M5 |
| ยังไม่มีกลยุทธ์ที่พิสูจน์ edge | ระบบพร้อมแต่ไม่มีอะไรควรรัน | งานวิจัยเดินขนาน; แผนนี้ไม่ตัดสินเรื่องเงินจริง |

## 12. เรื่องที่ต้องให้ Note ตัดสิน

### 12.0 ที่ Note ตัดสินแล้ว (15 พ.ย. 2026)

- **ลำดับ:** ทำทีละ milestone ตามลำดับ **M3 → M2 → M4 → M7 → M1** (ไม่ทำ M1–M3 ขนานกัน)
- **Futures (M5, M6, M8):** พักไว้จนกว่าจะมีกลยุทธ์ Futures ที่แสดง edge; A2 ยังออกแบบ planner ให้รองรับ Perp ไว้
  แต่ไม่มี adapter หรือคำสั่ง Futures
- **Realtime:** เอนไปทาง SSE จาก API; ยืนยันใน D0 ก่อนเริ่ม M1
- ข้อ 3–7 ด้านล่างยังเปิดอยู่

### 12.1 ที่ยังเปิด

1. ~~ลำดับเริ่ม~~ ตัดสินแล้วใน §12.0
2. ~~Realtime~~ เอนไปทาง SSE (§12.0); ยืนยันใน D0
3. ไลบรารีกราฟ: `lightweight-charts` หรือเขียน SVG ต่อจาก `LineChart.tsx`
4. บัญชี Futures Testnet และ key ใหม่ (Note สร้างเอง ใส่ใน secret store บน VM)
5. กลยุทธ์ Futures ตัวแรก: R11 ตามแผนเดิม หรือตัวอื่น
6. เหรียญนอกจาก BTCUSDT: ยังไม่รวมในแผนนี้ ต้องการเพิ่มเมื่อไร
7. C2 จะใช้ยืนยันกลยุทธ์ไหน และเมื่อไร

## 13. สิ่งที่แผนนี้ไม่ได้ยืนยัน

- ไม่ได้ทดสอบ Futures Testnet จริง ข้อมูล endpoint ทั้งหมดเป็น DISCOVERY จนกว่า B0 จะเสร็จ
- ไม่ได้ตรวจ license ของ `lightweight-charts` และไม่ได้วัดขนาด bundle
- ไม่ได้ทดลอง Supabase Realtime กับ schema `trading` และ RLS ปัจจุบัน
- ไม่มีประมาณเวลา เพราะขึ้นกับลำดับที่ Note เลือกและเวลาที่ soak ต้องใช้ (B6 28 วัน, A3 14 วัน)
- การมีแผนนี้ไม่ได้แปลว่าระบบพร้อมใช้เงินจริง การเปิด LIVE ต้องมี ADR แยกและเป็นการตัดสินใจของ Note
