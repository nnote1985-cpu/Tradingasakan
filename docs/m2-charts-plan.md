# M2: กราฟเทคนิคอล (Workstream E1–E4, Spot ก่อน)

เขียน 2026-11-15 ก่อนโค้ด ตาม [platform-development-plan.md](platform-development-plan.md) §7 (Workstream E) และ §9
(ลำดับ M3 → **M2** → M4 → M7 → M1) หลัง M3 เสร็จ (#73–#75)
Code baseline: main 41b468d

## 1. เป้าหมาย

หน้ากราฟแท่งเทียนบนเว็บที่ใช้ดูว่าบอททำอะไรและเพราะอะไร:

1. **API แท่งเทียน (E1):** แท่ง OHLCV ที่ปิดแล้ว 1h / 4h / 1d ของ BTCUSDT (Spot Mainnet และ Perp สำหรับสัญญาณของ R10a)
2. **อินดิเคเตอร์ฝั่ง server (E2):** คำนวณด้วยฟังก์ชันเดียวกับที่กลยุทธ์ใช้ตัดสินใจ อยู่ที่ `features/indicators.py` ที่เดียว
3. **Overlay ของบอท (E1):** จุดซื้อขายจาก fill จริง, จุดตัดสินใจพร้อมเหตุผล, เส้น HALT
4. **Component กราฟ (E3):** แท่งเทียน + volume, แผงอินดิเคเตอร์, marker, เลือก timeframe, ซูม/เลื่อน, ใช้ได้ที่ 375px, โหลดแบบ lazy
5. **โหมด backtest (E4):** กราฟเดียวกันเปิดกับผล backtest และ paper book ติดป้าย BACKTEST / SHADOW / TESTNET

## 2. สิ่งที่ไม่ทำใน M2

- **ไม่แก้** worker ที่รันอยู่: `runtime/r10a.py`, `runtime/ai_r2.py`, `runtime/worker.py`, `scripts/testnet_worker.py`,
  `alerts/risk_check.py`, `alerts/live_data.py` (ตรวจด้วย `git diff` ว่างใน §5.5)
- **ไม่แตะ** frozen branches และ paper books (กราฟอ่านผลอย่างเดียว)
- ไม่มี realtime (M1): กราฟ poll ทุก 30 วินาที อัปเดตแท่งล่าสุดโดยไม่โหลดกราฟใหม่; SSE มาทีหลังใน M1
- ไม่มี Futures: mark price, funding รายรอบ, basis และเส้น liquidation รอจน Futures กลับมา (§12.0 แผนหลัก)
  Perp ใน M2 มีแค่แท่งรายวันที่ R10a ใช้เป็นสัญญาณ
- ไม่มี migration: อ่านจากตารางที่มีแล้ว (`market_bars`, `exec_strategy_decisions`, `exec_trades`, `exec_risk_events`)
- timeframe 1m/5m/15m ยังไม่ทำ (ยังไม่มีกลยุทธ์ใช้)
- ไม่เติมข้อมูลเอง: ช่วงที่ขาดแสดงเป็นช่องว่าง

## 3. Discovery ที่ทำแล้ว

### 3.1 ไลบรารีกราฟ: `lightweight-charts` 5.2.1

| ข้อ | ผล |
| --- | --- |
| License | Apache-2.0 (ใช้ได้ในโปรเจกต์ส่วนตัวและเชิงพาณิชย์) |
| Attribution | ต้องแสดง attribution notice จากไฟล์ NOTICE และลิงก์ไป tradingview.com บนหน้าที่ผู้ใช้เห็น; ตัวเลือก `attributionLogo` (เปิดเป็นค่าเริ่มต้น) บนกราฟนับเป็นลิงก์แล้ว เราจะเก็บไว้ และใส่ notice ในหน้า "เกี่ยวกับ / third-party" ของเว็บ + `docs/third_party/` |
| ขนาด | `lightweight-charts.production.mjs` 187 KB, gzip 60 KB (dependency เดียว `fancy-canvas` 2.1.0) |
| เวอร์ชัน | 5.2.1 (ออก 2026-08-12) ล็อกเลขตรง; 5.3.0 เพิ่งออก 2026-11-10 ยังไม่ใช้ |
| ทางเลือกสำรอง | ขยาย `LineChart.tsx` เป็น SVG ถ้าภายหลังพบปัญหา (ไม่คาดว่าจะต้องใช้) |

60 KB gzip อยู่ใน chunk แยกที่โหลดเฉพาะหน้ากราฟ หน้าอื่นไม่เพิ่มขนาด (§5.4 C4)

### 3.2 แหล่งแท่งเทียน (ตรวจ Supabase 2026-11-15)

| ตาราง / แหล่ง | มีอะไร |
| --- | --- |
| `trading.market_bars` venue `BINANCE_SPOT_MAINNET` 1d | 179 แท่ง 2026-05-20 → ปัจจุบัน |
| `trading.market_bars` venue `BINANCE_SPOT_MAINNET` 1m | ~44,600 แท่ง 2026-10-15 → ปัจจุบัน (ช่วงที่ worker ดับมีช่อง) |
| data.binance.vision (ตรวจ checksum, โค้ดเดิมใน `alerts/live_data.py`) | spot 1h/1d และ futures um 1d/1h ย้อนหลังทั้งหมด |

- **1d Spot:** `market_bars` 1d; ก่อน 2026-05-20 จาก archive
- **1h Spot:** รวมจาก 1m ใน `market_bars` เฉพาะชั่วโมงที่มีครบ 60 แท่ง; ชั่วโมงที่ไม่ครบใช้ archive 1h ถ้ามี ไม่อย่างนั้นเป็นช่องว่าง
- **4h:** รวมจาก 1h แบบเดียวกับ `argus_e1.h4_bars` (origin epoch, label/closed left) เฉพาะ 4h ที่มีครบ 4 ชั่วโมง
- **1d Perp (สัญญาณ R10a):** archive futures um อย่างเดียว (ไม่มีในฐานข้อมูล)
- แท่งที่กำลังก่อตัว (Spot 1h/4h/1d) รวมจาก 1m ที่มีถึงตอนนี้ ติด `closed: false` แยกจากแท่งที่ปิดแล้ว ไม่ใช้คำนวณอินดิเคเตอร์
- archive ถูก cache บนเครื่องที่รัน API (โฟลเดอร์ cache เดิมของ `live_data`) ไม่โหลดซ้ำทุก request

### 3.3 อินดิเคเตอร์ที่กลยุทธ์ใช้ตอนนี้ (กระจายอยู่หลายไฟล์)

| อินดิเคเตอร์ | ที่อยู่ตอนนี้ | หมายเหตุ |
| --- | --- | --- |
| SMA100 | `alerts/risk_check.signals`, `strategies/sma_band.band_state` | `pandas.rolling(n, min_periods=n).mean()` บนวันที่ reindex เต็ม (วันที่ขาด = NaN) |
| แถบ ±2% + latch | `strategies/sma_band.band_state` | |
| EMA (MT5) | `strategies/argus_e1.ema` | `ewm(alpha=2/(n+1), adjust=False)` seed ที่ค่าแรก |
| EMA (BUILDPLAN) | `features/indicators.ema` | seed ด้วย SMA ของ n ค่าแรก — **คนละสูตร** กับข้างบน |
| ADX14 (MT5) | `strategies/argus_e1.adx` | EMA smoothing, NaN ก่อน 2n แท่ง |
| Donchian 20 | `argus_e1.E1Signals` (`rolling(20).max/min` รวมแท่งปัจจุบัน) | `features.previous_high/low` เป็นแบบไม่รวมแท่งปัจจุบัน |
| ATR14 Wilder | `features/indicators.atr_wilder` | |

EMA มีสองสูตรที่ให้ค่าต่างกัน จะเก็บไว้ทั้งคู่แต่ตั้งชื่อชัด (`ema_sma_seed`, `ema_mt5`) ไม่ให้ใครเรียกผิด
เส้น EMA20/50/200 บนกราฟใช้สูตร MT5 เพราะเป็นสูตรที่ E1 ใช้ตัดสินจริง และ API ส่งชื่อสูตรไปด้วย
ช่วง warm-up (เช่น 3×n แท่งแรกของ EMA MT5, 2n แท่งของ ADX) ส่งเป็น `null` หรือมีธง `warming` เพื่อไม่ให้อ่านผิด

## 4. การออกแบบ

### 4.1 `trading/features/indicators.py` (E2)

ฟังก์ชัน pure รับ `pd.Series`/`np.ndarray` ของแท่งที่ปิดแล้ว ความยาวเท่ากับ input:

`sma(close, n)`, `band(sma, pct)`, `band_latch(close, sma_n, pct, signed)`, `ema_mt5(x, n)`, `ema_sma_seed(x, n)` (ชื่อใหม่ของ `ema` เดิม, คง alias `ema`),
`adx_mt5(h, l, c, n)`, `donchian(h, l, n)` (รวมแท่งปัจจุบัน), `previous_high/low` (เดิม), `true_range`, `atr_wilder` (เดิม)

- ย้ายโค้ดมาแบบคัดลอกบรรทัดต่อบรรทัด (การดำเนินการ numpy/pandas ลำดับเดิม) เพื่อให้ได้ float เดิมทุก bit
- `strategies/sma_band.py`, `strategies/argus_e1.py`, `strategies/r10a_v2.py` เปลี่ยนไปเรียกจาก `features/indicators.py`
  (`band_state`, `ema`, `adx` เดิมคงชื่อไว้เป็น wrapper บาง ๆ เพื่อไม่ให้ import เดิมพัง)
- `alerts/risk_check.py` และ `runtime/r10a.py` **ไม่แก้** (worker ใช้อยู่); test ยืนยันว่าค่าจากที่เดิมกับที่ใหม่เท่ากันทุก bit

### 4.2 API (E1)

ทุก endpoint ต้องล็อกอิน (role viewer ขึ้นไปเหมือนหน้าอื่น) และอ่านอย่างเดียว

```
GET /api/v1/charts/{symbol}/bars?venue=spot|perp&timeframe=1h|4h|1d&from&to
  → { symbol, venue, timeframe, bars: [{t, o, h, l, c, v}], forming: {…, closed:false} | null,
      gaps: [{from, to}], sources: [{from, to, source}], as_of }
GET /api/v1/charts/{symbol}/indicators?venue&timeframe&from&to&set=r10a|e1|basic
  → { set, series: [{key, label, panel: "price"|"lower", formula, values: [{t, v|null}]}] }
GET /api/v1/bots/{account}/{book}/overlays?from&to
  → { mode: "TESTNET", fills: [...], decisions: [...], halts: [...] }
GET /api/v1/charts/runs/{run_id}?…            (E4: backtest / shadow)
  → { mode: "BACKTEST"|"SHADOW", trades: [...], equity: [...], drawdown: [...] }
```

- ตัวเลขราคาส่งเป็น string ทศนิยม (แบบ endpoint อื่น) เว็บแปลงเป็น float เพื่อวาดเท่านั้น
- ชุดอินดิเคเตอร์:
  - `r10a`: SMA100 + แถบ ±2% + band state บน **Perp 1d** (ชุดเดียวกับที่ R10a ตัดสิน; ถ้าขอบน Spot จะคำนวณบน Spot และบอกในชื่อ)
  - `e1`: EMA50/200 (MT5), Donchian 20, ADX14 บน 4h
  - `basic`: EMA20/50/200 (MT5), Donchian 20/10, ATR14, Volume
- ขีดจำกัด: ไม่เกิน 5,000 แท่งต่อ request; อินดิเคเตอร์คำนวณจากแท่งย้อนหลังเพิ่ม (warm-up) ก่อน `from` แล้วตัดออก
- overlays:
  - `fills`: จาก `trading.exec_trades` ของบัญชี (ราคา, จำนวน, side, client order id → decision);
    เทรดของ conformance (`tn-conformance-v1`) ไม่ใช่ของ book จะซ่อนไว้ เปิดดูได้ด้วยตัวเลือก `include=conformance`
  - `decisions`: จาก `trading.exec_strategy_decisions` ของ book (วัน, action, weight เก่า→ใหม่, notes, inputs_sha256)
  - `halts`: จาก `trading.exec_risk_events` (HALT / RESUME / FLAT_CONFIRMED) เป็นช่วงเวลา
  - stop ที่ active ยังไม่มี (R10a ไม่มี stop) — ใส่ช่องไว้สำหรับ E1 บน Testnet ใน M7

### 4.3 Component กราฟ (E3)

- `apps/web/src/charts/CandleChart.tsx` โหลดด้วย `React.lazy` + dynamic import ของ `lightweight-charts` (chunk แยก)
- แท่งเทียน + volume (แผงล่าง), อินดิเคเตอร์เปิด/ปิดรายตัว, crosshair พร้อมค่าทุก series ใน legend, เลือก 1h/4h/1d,
  ซูม/เลื่อนด้วยเมาส์และนิ้ว, ปุ่ม "กลับไปล่าสุด"
- marker: ▲ ซื้อ / ▼ ขาย (รูปทรงต่างกัน ไม่ใช้สีอย่างเดียว) พร้อมราคาและจำนวน; จุดตัดสินใจเป็นวงกลมเล็ก;
  ช่วง HALT เป็นแถบพื้นหลังพร้อมข้อความ
- คลิก marker เปิดแผงรายละเอียด: fill / order และ decision ที่ทำให้เกิด (เหตุผล, weight, inputs หลัก)
- ป้ายโหมด TESTNET / SHADOW / BACKTEST มุมบนซ้ายของกราฟทุกครั้ง (ใช้ `ModeBadge` เดิม)
- ตารางข้อมูลแทนกราฟสำหรับ screen reader (ปุ่ม "ดูเป็นตาราง"), เคารพ `prefers-reduced-motion`, ธีมมืด/สว่างตามหน้าเดิม
- ที่วาง: เมนู "ตลาด" (กราฟ BTCUSDT) และหน้ารายละเอียดบอทใต้ "บอต" (กราฟเดียวกัน + overlay ของ book นั้น)
- อัปเดต: poll ทุก 30 วินาที (หยุดเมื่อแท็บซ่อน) ใช้ `series.update()` กับแท่งล่าสุดแทนการ `setData` ใหม่ทั้งชุด

### 4.4 โหมด backtest / shadow (E4)

- BACKTEST: ผลใน `research/results/` (R7 dev) และ `c1-band-results` (R10a, R11) — marker จากเทรดของ simulator,
  equity และ drawdown ในแผงล่าง
- SHADOW: paper books (pathA-r10a, futures-r11, spot-e1-v1) จาก journal/snapshot ที่ sync ไว้ใน Supabase
- E1 backtest: ผล R13 ถ้ามีไฟล์เทรดที่ commit ไว้; ถ้าไม่มี ใช้ `argus_e1.replay` บนแท่ง archive (คำนวณบน server, cache)
- ไม่ปนกัน: หนึ่งกราฟหนึ่งโหมด, ป้ายโหมดอยู่บนกราฟเสมอ, API ตอบ `mode` และเว็บไม่ยอมวาดถ้าไม่มี

## 5. เกณฑ์ผ่าน (ประกาศก่อนโค้ด)

### 5.1 อินดิเคเตอร์ตรงกับที่กลยุทธ์ใช้ (E2)

| # | เงื่อนไข | ผ่านเมื่อ |
| --- | --- | --- |
| I1 | R10a: `sma100`, `close_perp`, `band_state` ที่ API คืนสำหรับ Perp 1d | เท่ากับ `inputs` ของทุกแถวที่บันทึกใน `exec_strategy_decisions` (`tn-r10a-code-v1` / `v1.1`, 18 แถว 10-18 → 11-13 ตอนเขียน) ผิดไม่เกิน 1e-9 (relative) และทำซ้ำได้จาก fixture `tests/fixtures/r10a_soak` |
| I2 | E1: EMA50/200, ADX14, Donchian 20 บน 4h | เท่ากับ array ของ `E1Signals` บนแท่งชุดเดียวกันทุกแท่ง (ผิดไม่เกิน 1e-9) และ entry/stop ของ `argus_e1.replay` ก่อน/หลังย้ายโค้ดเท่ากันทุกเทรด |
| I3 | ย้ายโค้ดไม่เปลี่ยนค่า | ฟังก์ชันใหม่เทียบกับโค้ดเดิม (คัดลอกไว้ใน test) บนข้อมูลสุ่ม + ข้อมูลจริงที่มีช่องว่าง: เท่ากันทุก bit (`np.array_equal` รวม NaN) |
| I4 | replay ของ M3 | `tests` ของ `r10a_v2` (hash 17/17) ยังผ่าน |

### 5.2 แท่งเทียน (E1)

| # | เงื่อนไข | ผ่านเมื่อ |
| --- | --- | --- |
| B1 | 1h ที่รวมจาก 1m เทียบ archive 1h | OHLCV ตรงกันทุกชั่วโมงที่ 1m ครบ (ตรวจกับข้อมูลจริง ≥ 7 วัน) |
| B2 | 4h รวมจาก 1h | เท่ากับ `argus_e1.h4_bars` บนข้อมูลเดียวกัน |
| B3 | ช่วงที่ขาด | ชั่วโมง/วันที่ข้อมูลไม่ครบไม่มีแท่ง และอยู่ใน `gaps`; ไม่มีแท่งที่ถูกเติมเอง (test ตัด 1m ออกบางช่วง) |
| B4 | แท่งที่กำลังก่อตัว | ไม่อยู่ใน `bars`, ไม่เข้าการคำนวณอินดิเคเตอร์, ติด `closed: false` |
| B5 | ขอบเขต | timeframe/venue ผิด = 422, เกิน 5,000 แท่ง = 422, ไม่ล็อกอิน = 401 |

### 5.3 Overlay (E1)

| # | เงื่อนไข | ผ่านเมื่อ |
| --- | --- | --- |
| O1 | fill จริงของ soak | BUY 0.02411 BTC @ 82,786.01 (2026-10-19 08:26Z, decision วัน 10-18) และ SELL 0.02408 @ 82,569.99 ตอน HALT drill (10-20 15:40Z) ขึ้นเป็น marker ตรงเวลาและราคา; เทรด conformance ไม่ขึ้นโดยค่าเริ่มต้น |
| O2 | decision | ทุกแถวของ book มี marker พร้อม action, weight และ notes; NO_DECISION แสดงแยกด้วยรูปทรง |
| O3 | HALT | ช่วง HALT ของ drill (10-20 15:40Z → 15:43Z, 10-31 01:32Z → 04:37Z, จาก `exec_risk_events` HALT/RESUME) ขึ้นเป็นแถบ |

### 5.4 Component (E3)

| # | เงื่อนไข | ผ่านเมื่อ |
| --- | --- | --- |
| C1 | แท่งล่าสุดอัปเดตโดยไม่โหลดกราฟใหม่ | test ยืนยันว่า poll รอบสองเรียก `update()` ไม่เรียก `setData()`; ตำแหน่งซูมคงเดิม |
| C2 | ลื่น | 2,000 แท่ง + 4 อินดิเคเตอร์: เลื่อน/ซูมบนเครื่องของ Note ไม่กระตุก (Note ลองเอง) และวัดใน headless Chromium: render แรก < 500 ms |
| C3 | 375 px | screenshot ที่ 375×812: ไม่มี scroll แนวนอน, ปุ่ม timeframe/อินดิเคเตอร์กดได้ (≥ 40 px), legend ไม่ทับแท่ง |
| C4 | lazy bundle | `vite build` แยก chunk ของ lightweight-charts; chunk หลักโตไม่เกิน 5 KB gzip เทียบ main |
| C5 | ป้ายโหมด | ทุกกราฟมีป้าย TESTNET / SHADOW / BACKTEST; test ยืนยันว่าไม่มี `mode` = ไม่วาด |
| C6 | attribution | ลิงก์ TradingView บนกราฟ + notice ใน `docs/third_party/` และหน้าเว็บ |
| C7 | การเข้าถึง | ขึ้น/ลงแยกด้วยรูปทรง (แท่งกลวง/ทึบ, ▲/▼), มีตารางแทนกราฟ, `prefers-reduced-motion` ปิด animation |

### 5.5 ทั่วไป

- test เดิมทั้งหมดผ่าน, ruff, mypy, `pnpm typecheck`, `pnpm test`, `pnpm build` ผ่าน
- `git diff main -- backend/src/trading/runtime/ backend/src/trading/alerts/ scripts/testnet_worker.py` ว่าง

## 6. แบ่ง PR

1. **แผน (PR นี้):** เอกสารนี้ ไม่มีโค้ด
2. **อินดิเคเตอร์กลาง:** `features/indicators.py` + strategies เรียกใช้, test I2 (ส่วน replay) I3 I4
3. **API แท่งเทียน + อินดิเคเตอร์:** `apps/api/api/charts.py`, test B1–B5, I1, I2
4. **API overlay:** `/bots/{account}/{book}/overlays`, test O1–O3 (fixture จาก Supabase)
5. **หน้ากราฟบนเว็บ:** `lightweight-charts` 5.2.1, `CandleChart.tsx`, เมนูตลาด + หน้ารายละเอียดบอท, test C1 C3–C7
6. **โหมด backtest / shadow (E4):** endpoint runs + โหมดบนกราฟ

Note merge แต่ละ PR เอง ("merge") ตามลำดับ; PR ถัดไปเปิดซ้อนได้แต่ merge ทีละตัว

## 7. หลัง M2

- M2 เสร็จเมื่อ PR 6 merge และเกณฑ์ §5 ผ่านครบ (C2 ส่วนที่ Note ลองบนเครื่องตัวเอง)
- ต่อด้วย M4 (ตั้งค่าบอทจากเว็บ) ตามลำดับ; overlay ของ stop และ E1 บน Testnet มาพร้อม M7
- realtime ของกราฟ (แท่งล่าสุดผ่าน SSE) เป็นงานของ M1
