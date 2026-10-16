# ทะเบียนผลวิจัย Phase 0.5 (ตรวจซ้ำได้)

เขียน 2026-10-15 ตามข้อตรวจของ Note ทุกตัวเลขในหน้านี้คัดจากไฟล์ `report.json`
ของแต่ละการทดลองใน `/mnt/project-files/` ไม่ได้รันใหม่ ยกเว้นที่ระบุว่า "คำนวณเพิ่ม"

## ชื่อเรียก

ชื่อ v1/v2/v3 ในงานวิจัยไม่ใช่ v1/v2/v3 ของ roadmap ใน buildplan (§16) จากนี้ใช้รหัส R ด้านล่าง
ชื่อเดิมยังอยู่ในชื่อไฟล์และโค้ดเพื่อให้ commit เดิมตรวจซ้ำได้

| รหัสวิจัย | ชื่อเดิมในไฟล์ | กฎ | สินทรัพย์ |
| --- | --- | --- | --- |
| R1 | `trend_breakout_v1` (v1) | breakout 1h, TP 2R, time exit 24h, เสี่ยง 0.5%/เทรด | BTCUSDT perp |
| R2 | `trend_donchian_4h_v1_1` (v1.1) | Donchian 4h 20/10 ไม่มี TP | BTC+ETH+SOL perp |
| R3 | `trend_regime_v2` (v2) | ถือเต็มเมื่อปิดเหนือ SMA100 ไม่งั้นถือเงินสด | BTC+ETH+SOL พอร์ตแบบ spot (เงินสด+เหรียญ, ค่าธรรมเนียม spot, ไม่คิด funding) แต่ใช้ราคา perp เป็นตัวแทน |
| R4 | `funding_overlay_v3` (v3) | ถือ BTC ลดเหลือ 50% เมื่อ funding เฉลี่ย 7 วัน > 0.09%/วัน | BTC พอร์ตแบบ spot ใช้ราคา perp เป็นตัวแทน |
| R5 | `pathA-v1` | R3 บน BTC × R4 (น้ำหนัก = trend × funding) | BTC พอร์ตแบบ spot ใช้ราคา perp เป็นตัวแทน |
| R6a / R6b | `run_r6.py` | สัญญาณ v1 เฉพาะ LONG, ไม่มี TP/time exit, ออกด้วย stop / trend exit / channel exit 10 แท่ง 4h; R6a เสี่ยง 0.5%, R6b ใช้เงินทั้งหมด | BTCUSDT **spot** (ราคา spot จริง) |
| R8a / R8b | `run_r8.py` | path A × 0.5 when bid depth within 2% is in its trailing-90-day bottom 20% (R8a), and also 7-day OI change in its top 20% (R8b) | BTC spot model, perp prices, Binance bookDepth + metrics |
| R9a / R9b | `run_r9.py` | path A × 0.5 when 30-day realised vol > 80%/yr (R9a); path A × min(1, trailing-1y median vol / vol) floored to 0.25 steps (R9b) | BTC spot model, perp prices |
| R10a / R10b | `run_r10.py` | path A with a sticky trend state: in above SMA100 × 1.02, out below × 0.98 (R10a); in/out after 3 closes above/below SMA100 (R10b) | BTC spot model, perp prices |
| R11 | `run_r11.py` | R7 long/short with a sticky side: long above SMA100 × 1.02, short below × 0.98 | BTCUSDT **perp**, 1x, funding |
| R12 | `run_r12.py` | R10a weight × R9b vol scale | BTC spot model, perp prices |
| R7 | `run_r7.py` | long เมื่อปิดเหนือ SMA100 รายวัน, short เมื่อต่ำกว่า, 1x, คิด funding | BTCUSDT **perp** |

`pathA-v1` ยังเป็นชื่อของพอร์ตจำลองที่ตรึงไว้ เปลี่ยนไม่ได้โดยไม่เปิดพอร์ตใหม่

## ช่วงข้อมูล

แหล่งข้อมูล: [data.binance.vision](https://data.binance.vision/data/futures/um) USD-M futures
(kline 1h/1m และ funding) ไฟล์ zip ทุกไฟล์ตรวจ SHA256 กับ `.CHECKSUM` แล้ว
(`data/binance_um*/manifest.json`, สถานะ VERIFIED) ขอบเขตที่โหลด 2022-01-01 → 2025-12-31

ช่วงแบ่งจาก `configs/spike.json` (50/25/25 หลังตัด warm-up 2,400 ชั่วโมง) แบบ `[เริ่ม, สิ้นสุด)` เวลา UTC:

| ช่วง | เริ่ม | สิ้นสุด (ไม่รวม) | วัน |
| --- | --- | --- | --- |
| development | 2022-04-12 | 2024-02-20 | 679 |
| validation | 2024-02-20 | 2025-01-25 | 340 |
| holdout | 2025-01-25 | 2026-01-01 | 341 |

holdout จึงเป็น **25 ม.ค. → 31 ธ.ค. 2025** ไม่ใช่ทั้งปี 2025

## ผลแต่ละการทดลอง

ค่าที่ต้นทุนตั้งต้นของแต่ละการทดลอง Sharpe = ผลตอบแทนรายวัน × √365, DD = drawdown สูงสุดจากราคาปิดรายวัน
R1 ใช้ fill แบบ conservative/absorbing ส่วน R2 ใช้ record_only ตามตารางหลักในเอกสารแผนของแต่ละตัว

| รหัส | ช่วง | เงินตั้งต้น | ต้นทุน | Net | Sharpe | Max DD | B&H net / Sharpe / DD | เทรด | Exposure |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| R1 | dev | 10,000 USDT | taker 0.05% + slip 2 bps (stop 5 bps) + funding | −4.8% | −0.77 | 7.9% | +31.0% / 0.26 / 62.0% | 79 | อยู่ในตลาด 6.2% ของเวลา ขนาดเฉลี่ย 21% ของ equity |
| R1 | val | 10,000 USDT | เหมือนกัน | −1.4% | −0.22 | 4.8% | +102.7% / 1.41 / 26.3% | 148 | 22.9% ของเวลา ขนาด 30% ของ equity |
| R2 | dev | 30,000 USDT (3 เหรียญ) | เหมือน R1 | +11.2% | 1.22 | 4.4% | EW B&H +11.1% / 0.09 / 71.9% | 177 | ไม่ได้บันทึก ¹ |
| R2 | val | 30,000 USDT | เหมือน R1 | −0.4% | −0.10 | 3.7% | EW B&H +78.9% / 1.01 / 34.3% | 99 | ไม่ได้บันทึก ¹ |
| R3 | dev | 30,000 USDT | spot 0.10% + 2 bps | +47.5% | 0.56 | 34.7% | EW B&H +13.9% / 0.11 / 72.0% | ไม่ได้บันทึก | ไม่ได้บันทึก |
| R3 | val | 30,000 USDT | spot 0.10% + 2 bps | +8.0% | 0.18 | 45.5% | EW B&H +80.2% / 1.02 / 34.2% | ไม่ได้บันทึก | ไม่ได้บันทึก |
| R4 | dev | 10,000 USDT | spot 0.10% + 2 bps | +28.0% | 0.25 | 62.0% | +31.0% / 0.27 / 62.0% | 8 | ลดน้ำหนัก 0.9% ของวัน |
| R4 | val | 10,000 USDT | spot 0.10% + 2 bps | +109.5% | 1.58 | 24.8% | +102.0% / 1.43 / 26.3% | 22 | ลดน้ำหนัก 5.6% ของวัน |
| R5 | dev+val | 10,000 USDT | spot 0.10% + 2 bps | +188.6% | ไม่ได้บันทึก | 35.1% | +165.0% / ไม่ได้บันทึก / 62.0% | 65 | ไม่อยู่ใน BTC 42.7% ของวัน |
| R5 | holdout | 10,000 USDT | spot 0.10% + 2 bps | −6.9% | −0.32 | 18.0% | −16.5% / −0.47 / 32.0% | 20 | ไม่อยู่ใน BTC 49.3% ของวัน ไม่เคยถือ 50% |
| R6a | dev (2022-04-12 → 2026-01-01, spot) | 10,000 USDT | spot 0.10% + 2 bps (stop 5 bps) | +10.3% | 0.31 | 13.9% | +121.5% / 0.42 / 62.0% | 156 | อยู่ในตลาด 25% ของเวลา |
| R6b | dev (เดียวกัน) | 10,000 USDT | เหมือนกัน | +9.3% | 0.09 | 41.8% | +121.5% / 0.42 / 62.0% | 156 | อยู่ในตลาด 25% ของเวลา |

¹ R1 และ R2 เสี่ยง 0.5% ต่อเทรด จึงถือเฉลี่ยราว 5% ของ equity ตลอดช่วง (`docs/research/v1.1-plan.md`)
ค่า "ไม่ได้บันทึก" คำนวณเพิ่มได้จาก commit เดิม แต่ยังไม่ได้ทำ

## วิธีคำนวณ B&H ช่วง holdout (−16.5%)

- ซื้อ BTCUSDT ที่ราคาเปิด 2025-01-25 00:00 UTC = **104,808.0** เสียค่าธรรมเนียม 0.10% และ slippage 2 bps
- ถือถึงราคาปิด 2025-12-31 23:00 UTC = **87,608.2** (ราคาเปลี่ยน −16.4%)
- equity 10,000 → 8,348.89 USDT = **−16.5%** (`pathA-holdout/equity_buy_and_hold.csv`)
- ถ้านับทั้งปีปฏิทิน 2025 (เปิด 1 ม.ค. 93,548.8) ราคาเปลี่ยนแค่ −6.4% ตัวเลข −16.5% จึงใช้เทียบได้เฉพาะช่วง holdout นี้
- ใช้ราคา perpetual แทน spot และไม่ได้คิด funding ใน B&H

## ตลาดที่จำลอง และข้อจำกัดของราคาตัวแทน (R3–R5 และพอร์ตจำลอง pathA-v1)

| ส่วน | ข้อมูลที่ใช้ |
| --- | --- |
| พอร์ตที่จำลอง | **Spot**: ถือเงินสด USDT + BTC, ไม่มี leverage, ไม่จ่าย/รับ funding, ค่าธรรมเนียม 0.10% + 2 bps |
| สัญญาณ | ราคาปิดรายวันของ BTCUSDT perpetual (USD-M) และ funding ของ perpetual |
| ราคา fill, mark-to-market, B&H | **ราคา perpetual เป็นตัวแทนราคา spot** (holdout: แท่งรายวันจาก 1h perp; พอร์ตจำลอง: ราคาเปิด 1h และราคาปิดรายวันของ perp) |

ผลวัดผลกระทบ (`research/spike/check_pathA_data.py`, ผลที่ `/mnt/project-files/pathA-data-checks/report.json`):
basis ราคาปิดรายวัน perp/spot − 1 ช่วง holdout เฉลี่ย −0.044% สูงสุด 0.069% (341 วัน), ต.ค. 2026 เฉลี่ย −0.053%
เมื่อรัน holdout ใหม่ด้วยน้ำหนักเดิมแต่ใช้แท่ง spot สำหรับ fill/mark/B&H: R5 net −6.93% (เดิม −6.90%),
Sharpe −0.32, DD 18.0%; B&H −16.52% (เดิม −16.51%) ผลแบบใช้ราคา perp ยังคงอยู่ ไม่ถูกแทนที่ ข้อสรุปไม่เปลี่ยน

## ผลแต่ละข้อเทียบเกณฑ์ (แยก 3 กรณี)

ใช้เกณฑ์เดียวกันทุกแถว (เกณฑ์ของ Note): ทำกำไรสุทธิ = net > 0, เหนือ benchmark = Sharpe สูงกว่า B&H
หรือ DD ไม่เกินครึ่งของ B&H ผ่านทั้งสองข้อจึงนับว่า "ผ่าน" R1 และ R2 เคยประกาศเกณฑ์ของตัวเองไว้ต่างจากนี้
(ดูเอกสารแผน) ตารางนี้จึงเป็นการอ่านย้อนด้วยเกณฑ์ปัจจุบัน

| รหัส | ช่วง | ทำกำไรสุทธิ | เหนือ benchmark | หลักฐานพอหรือไม่ |
| --- | --- | --- | --- | --- |
| R1 | dev | ไม่ผ่าน | ผ่านด้าน DD เท่านั้น ² | ไม่พอ |
| R1 | val | ไม่ผ่าน | ผ่านด้าน DD เท่านั้น ² | ไม่พอ |
| R2 | dev | ผ่าน | ผ่าน (Sharpe และ DD) | ไม่พอ: กำไรมาจาก 3 เทรดใหญ่ |
| R2 | val | ไม่ผ่าน | ผ่านด้าน DD เท่านั้น ² | ไม่พอ |
| R3 | dev | ผ่าน | ผ่าน (Sharpe และ DD) | ไม่พอ |
| R3 | val | ผ่าน | ไม่ผ่าน | ไม่พอ |
| R4 | dev | ผ่าน | ไม่ผ่าน | ไม่พอ |
| R4 | val | ผ่าน | ผ่าน (Sharpe) | ไม่พอ |
| R6a/R6b | dev | ผ่าน | **ไม่ผ่าน** (Sharpe 0.31 และ 0.09 < 0.42) ³ | ไม่ผ่านตามเกณฑ์ที่ประกาศไว้ จึงไม่ได้ใช้ข้อมูลยืนยัน C1 (2018–2021) และ C2 (ม.ค.–ก.ย. 2026) |
| R7 | dev | ผ่าน | ผ่านแบบเฉียด (Sharpe 0.43 > 0.42); ฝั่ง short ขาดทุน | ไม่พอ: Note เลือกไม่ใช้ข้อมูลยืนยัน C1 (perp 2020-04→2021) และ C2 (2026) (`docs/research/r7-futures-longshort-plan.md`) |
| R5 | holdout | **ไม่ผ่าน** (−6.9%) | **ผ่าน** ด้าน Sharpe (−0.32 > −0.47); DD 18.0% ไม่ถึงครึ่งของ 32.0% | **ไม่พอ**: ปีเดียว power ราว 46% และกฎที่ไม่มีฝีมือผ่านได้ราว 29% |

³ R6 ใช้เกณฑ์เข้มกว่า: ต้อง Sharpe สูงกว่า B&H เท่านั้น ไม่ใช้ทางเลือก DD (`docs/research/r6-v1-spot-plan.md`) 113 จาก 156 เทรดออกที่ stop 2×ATR(1h)

² กลยุทธ์ที่ถือแค่ส่วนน้อยของ equity ผ่านเกณฑ์ DD ได้ง่ายโดยไม่ต้องมีฝีมือ เกณฑ์นี้ควรทบทวนก่อนใช้กับ R1/R2 แบบเดิม

สรุปที่ใช้: **ยังไม่พบหลักฐานเพียงพอว่ากลยุทธ์ที่ทดลองในข้อมูลและเงื่อนไขชุดนี้เหนือกว่า Buy & Hold**
R5 ขาดทุนน้อยกว่า B&H ในช่วงตลาดลง ซึ่งน่าสนใจด้านลดความเสียหาย แต่ยังไม่ใช่หลักฐานว่าทำกำไรได้
ไม่มีการทดลองไหนใช้ AI จึงสรุปเรื่อง AI ไม่ได้

## ตรวจซ้ำ

| รหัส | commit ที่บันทึกผล | คำสั่ง (จาก repo root) | ผลลัพธ์ |
| --- | --- | --- | --- |
| R1 | ab63a19 | `uv run python research/spike/run_backtest.py` | `/mnt/project-files/phase-0.5-results/` |
| R2 | bf94919 | `uv run python research/spike/run_v11.py` | `/mnt/project-files/v1.1-results/` |
| R3 | 32355b1 | `uv run python research/spike/run_v2.py` | `/mnt/project-files/v2-results/` |
| R4 | 6800dec | `uv run python research/spike/run_v3.py` | `/mnt/project-files/v3-results/` |
| R5 power | b0de161 | `uv run python research/spike/run_pathA_power.py` | `/mnt/project-files/pathA-power/` |
| R6 dev | 2cdeb01 | `PYTHONPATH=research/spike uv run python research/spike/run_r6.py development` | `/mnt/project-files/r6-results/` |
| R7 dev | f64ef00 | `PYTHONPATH=research/spike uv run python research/spike/run_r7.py development` | `/mnt/project-files/r7-results/` |
| R5 holdout | d4f77a7 (กฎจาก 42920ba) | `PYTHONPATH=<worktree ของ frozen/pathA-v1>/backend/src:research/spike uv run python research/spike/run_pathA_holdout.py` | `/mnt/project-files/pathA-holdout/` |

ข้อมูลโหลดด้วย `uv run python research/spike/download_data.py` (ต้องใช้ environment ที่อนุญาต data.binance.vision)
hash ของไฟล์ parquet ที่ใช้ (sha256):

| ไฟล์ | sha256 |
| --- | --- |
| BTCUSDT_1h.parquet | f317d97a6d27ff85accd4cdfed4b56aef1dbadaa0c1e356fb9c994c60d7076ab |
| BTCUSDT_1m.parquet | 28000dd9d6c0f6027025b78f1aa5a272b81e3e70023e79da76570ee9815e3a68 |
| BTCUSDT_funding.parquet | db064ef66f3e7f4ffa16d96015bce2a60b8f54b07cc4dcc68c34f6425b36e93e |
| ETHUSDT_1h.parquet | 5090bd5cb76b11511542f075a64d3e82bfaf3732917fc731a4100dc0a8aebda7 |
| SOLUSDT_1h.parquet | 1d03cf2a0075a9ab057db52f07be22da117b1dccee2f65309c26cb7929bf7b0a |

hash ของ parquet อาจเปลี่ยนตามเวอร์ชัน pyarrow ส่วน hash ของไฟล์ zip ต้นทางอยู่ใน `manifest.json` และไม่เปลี่ยน

## บันทึกการใช้ holdout

- ข้อมูล: BTCUSDT kline 1h และ funding ช่วง 2025-01-25 → 2025-12-31 (ไฟล์ข้างบน)
- ก่อนวันที่ 2026-10-15 08:57 UTC ไฟล์ช่วงนี้ถูกดาวน์โหลดและโหลดเข้าหน่วยความจำ (จำเป็นต่อการหาจุดแบ่ง)
  แต่ไม่มีสคริปต์ไหนจำลองผลกลยุทธ์บนช่วงนี้ ทุกรายงานก่อนหน้ามี `holdout_simulated: false`
- ครั้งแรกที่จำลองผล: **2026-10-15 08:57 UTC** commit d4f77a7 กฎ pathA-v1 (42920ba) อนุมัติโดย Note
- จากนี้ช่วงนี้ **ใช้พัฒนาต่อได้ แต่ห้ามเรียกว่าข้อมูลยืนยันที่ไม่เคยเห็น** ข้อมูลยืนยันใหม่ต้องเป็นข้อมูลหลัง 2025-12-31 ที่ยังไม่ได้ใช้ หรือข้อมูล forward

## R8 (2026-10-16, docs/research/r8-depth-oi-filter-plan.md)

2023-04-01 → 2026-01-01, spot 0.10% + 2 bps: path A +103.3% / Sharpe 0.75 / DD 35.1%; R8a +55.9% / 0.50 / 33.0%
(280 trades); R8b +84.1% / 0.66 / 34.3% (134 trades); B&H +207.5% / 0.90 / 32.0%. Both fail (Sharpe below path A).
Window overlaps data path A was built on; C2 not used.

## R9 (2026-10-16, docs/research/r9-volatility-filter-plan.md)

2022-07-01 → 2026-01-01, spot 0.10% + 2 bps: path A +176.5% / Sharpe 0.84 / DD 35.1% (82 trades); R9a +172.3% / 0.84 / 35.1%
(92 trades, 9 days cut); R9b +142.1% / 0.83 / 26.8% (362 trades); B&H +339.2% / 0.88 / 35.4%. Both fail (Sharpe below
path A). R9b cut max DD by 8.3 points at nearly the same Sharpe. Window overlaps data path A was built on; C2 not used.

## R10 (2026-10-16, docs/research/r10-trend-hysteresis-plan.md)

2022-05-01 → 2026-01-01, spot 0.10% + 2 bps: path A +176.5% / Sharpe 0.82 / DD 35.1% (82 trades); R10a band +244.8% / 1.00 /
26.0% (48 trades); R10b confirm +135.1% / 0.67 / 39.0% (52 trades); B&H +132.6% / 0.46 / 60.3%. **R10a passes, R10b fails.**
Window fully seen before and the idea came from the 2025 analysis: R10a is a candidate for C1/C2 confirmation, not evidence.

## R11 (2026-10-16, docs/research/r11-futures-band-plan.md)

2022-04-12 → 2026-01-01, perp 1x, taker 0.05% + 2 bps, funding: R7 +103.4% / Sharpe 0.43 / DD 55.0% (57 flips, reproduced);
R11 +221.0% / 0.71 / 39.4% (21 flips); B&H +121.5% / 0.42 / 62.0%. **R11 passes.** Short days turned positive (+0.148 log vs
−0.078). Window seen before and band idea came from the 2025 analysis: candidate only; C1/C2 unspent.

## R12 (2026-10-16, docs/research/r12-band-voltarget-plan.md)

2022-07-01 → 2026-01-01, spot 0.10% + 2 bps: R10a +244.8% / Sharpe 1.02 / DD 26.0% (48 trades); R12 +182.8% / 0.97 / 21.7%
(331 trades); B&H +339.2% / 0.88 / 35.4%. **R12 fails** (Sharpe below R10a; DD cut 4.3 points, short of the declared 5).
Vol sizing helped only the high-vol 2024 drawdown; the low-vol sideways drawdowns of 2023 and 2025 are unchanged.
