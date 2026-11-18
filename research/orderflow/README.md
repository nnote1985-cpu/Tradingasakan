# ต้นแบบ Order Book: OBI + Order Flow กรองสัญญาณ EMA/ADX/ATR

วันที่ 2026-11-18 · ข้อมูล BTCUSDT USD-M futures 2026-11-01 → 16 (หลังช่วง C2 จึงไม่แตะ C2)

> **นี่คือการทดสอบเครื่องมือ ไม่ใช่หลักฐาน** ข้อมูลแค่ 12 วันที่ประเมินผล (1–4 พ.ย. ใช้วอร์มอินดิเคเตอร์) ได้ 6–11 ไม้ต่อบอท
> ตลาดช่วงนี้เป็นขาลงเกือบทั้งหมด (ไม้ส่วนใหญ่เป็น short) ตัวเลขด้านล่างเปลี่ยนได้ทั้งหมดถ้าเพิ่มอีกไม่กี่ไม้ **ไม่นำกฎใดๆ ไปใช้จากผลนี้**

## แหล่งข้อมูล

- ตั้งใจใช้ Tardis.dev free sample แต่ `datasets.tardis.dev` ถูก proxy บล็อก (403) ทั้งบน cloud ของ Claude และ shell บนเครื่อง Note
- ใช้ data.binance.vision แทน (ฟรี, ทางการ, มี checksum) `research/orderflow/download.py`
  - `aggTrades`: ทุกเทรดพร้อมฝั่ง taker → Order Flow (delta, delta ratio, CVD)
  - `bookDepth`: ผลรวม depth ภายใน ±0.2%, ±1..5% จาก mid ทุก ~30 วินาที → OBI แบบหยาบ
  - `klines 1m`: สร้างแท่ง 5m/15m และใช้หาจุดชน SL/TP
- ข้อจำกัด: bookDepth ไม่มีราคารายระดับ จึงหา Liquidity Wall ตรงๆ ไม่ได้ ใช้ตัวแทนคือ "ความหนาภายใน 1% เทียบกับช่วง 1–5%" (`near_depth_ratio_from_bookdepth`) ตัวหา wall จริง (`find_walls`) ใช้กับข้อมูล 20 ระดับจาก VM collector (PR #101) ได้ทันที

## ตัวคำนวณ (`backend/src/trading/features/orderbook.py`)

| ฟังก์ชัน | ใช้กับ | ค่า |
| --- | --- | --- |
| `obi_levels`, `obi_band` | L2 snapshot (VM) | (bid−ask)/(bid+ask) ใน N ระดับแรก หรือภายใน x% |
| `obi_from_bookdepth` | bookDepth | OBI ของ band ±0.2% หรือ ±1..5% |
| `trade_flow_bars`, `cvd_slope` | aggTrades / trades | buy, sell, delta, delta_ratio, CVD ต่อแท่ง |
| `find_walls`, `nearest_wall_bps` | L2 snapshot (VM) | ระดับที่ใหญ่ ≥ k เท่าของ median ภายใน x bps |
| `near_depth_ratio_from_bookdepth` | bookDepth | ตัวแทน wall แบบหยาบ |
| `snapshot_features` | 1 บรรทัดจาก VM collector | รวม OBI5/20, OBI band, ระยะ wall ทั้งสองฝั่ง |

เครื่องหมาย: บวก = ฝั่งซื้อแรงกว่า มี unit test 6 ตัวใน `tests/unit/test_orderbook_features.py`

## บอท (`research/orderflow/run_obi_filter.py`)

- **Bot A**: กฎ EMA_Trend_Pullback เดิม (EMA20/50/200 + HTF 1h EMA50/200 + slope ≥ 0.3 ATR + ADX ≥ 20 + ตัด EMA50 < 3 ครั้ง/20 แท่ง, เข้าตอน pullback กลับเหนือ EMA20 หรือ EMA20/50 ตัดกัน) long/short
- **Bot B**: สัญญาณเดียวกัน เข้าเฉพาะเมื่อ OBI (±0.2%, เฉลี่ยในแท่งสัญญาณ) **และ** taker delta ของแท่งสัญญาณ มีทิศเดียวกับไม้ (ใช้แค่เครื่องหมาย ไม่จูนค่า)
- แยกดูเพิ่ม: B_obi_only, B_flow_only
- Risk (มาตรฐาน futures): SL ฝั่ง exchange ทุกไม้ที่ 1.5 ATR, ขนาดให้โดน SL เสีย 1% ของทุน (notional ไม่เกิน 3x), TP 2R, ออกเมื่อปิดตัด EMA50, หยุดวันละ −3%, หยุดทั้งหมดที่ DD 10%, ค่าใช้จ่าย 0.05%/ข้าง, หาจุดชน SL/TP ด้วยแท่ง 1m (ชนทั้งคู่ในแท่งเดียวถือว่าโดน SL)
- หมายเหตุ 5m: SL 1.5 ATR บน 5m แคบมาก ทำให้ติดเพดาน 3x จริงเสี่ยงต่อไม้ ~0.6%

## ผล (ประเมิน 2026-11-05 → 16 รวมวันสุดท้าย)

| TF | บอท | ไม้ | Win | ผลรวม R | ผลตอบแทน | Max DD |
| --- | --- | --- | --- | --- | --- | --- |
| 15m | A | 6 | 50% | +3.2 | +1.8% | 3.3% |
| 15m | B | 1 | 100% | +2.0 | +1.9% | 0.0% |
| 15m | B_obi_only | 4 | 50% | +2.2 | +1.2% | 2.1% |
| 15m | B_flow_only | 3 | 67% | +3.0 | +2.5% | 1.2% |
| 5m | A | 11 | 9% | −6.6 | −9.3% | 9.3% |
| 5m | B | 5 | 0% | −4.5 | −4.4% | 4.4% |
| 5m | B_obi_only | 8 | 13% | −3.8 | −6.0% | 6.0% |
| 5m | B_flow_only | 8 | 0% | −7.4 | −7.9% | 7.9% |

ความสัมพันธ์ (Spearman) ของ feature กับผลตอบแทนแท่งถัดไป ทุกแท่ง (15m: 1,151 แท่ง, 5m: 3,455 แท่ง):

| feature | 15m | 5m |
| --- | --- | --- |
| OBI ±0.2% | +0.058 | +0.038 |
| taker delta ratio | −0.060 | −0.025 |
| bid near-depth ratio | +0.031 | +0.024 |
| ask near-depth ratio | −0.022 | −0.009 |
| OBI กับ delta (กันเอง) | −0.34 | −0.35 |

## อ่านผลอย่างไร

1. **เครื่องมือทำงานครบ**: ข้อมูลไม่หาย (OBI ขาด 0 แท่ง), feature สร้างได้ทุกแท่ง, harness A/B, risk caps, trade log/equity ออกมาครบ
2. **Bot B ทำให้ไม้น้อยลงมาก** (15m: 11 สัญญาณผ่านแค่ 2) เพราะ OBI กับ delta มักชี้คนละทาง (สหสัมพันธ์ −0.34) การบังคับให้ทั้งคู่ตรงกันจึงตัดแทบทุกไม้
3. 5m ขาดทุนน้อยลงเมื่อกรอง แต่เพราะเทรดน้อยลง ไม่ใช่เพราะเลือกไม้ดีขึ้น (win rate ไม่ดีขึ้น) 15m ไม้น้อยเกินจะบอกอะไรได้
4. ค่าสหสัมพันธ์ ~0.03–0.06 อยู่ในระดับสัญญาณรบกวนสำหรับ 12 วันตลาดเดียว (ขาลง) และ delta ติดลบ (ซื้อแรงแล้วแท่งถัดไปลง) ก็อาจเป็นแค่ช่วงนี้
5. สอดคล้องกับงานก่อนหน้า: สัญญาณ EMA/ADX/ATR ระยะสั้นไม่มี edge ใน 5m แย่ที่สุด

## ขั้นต่อไปที่สมเหตุสมผล

- รอข้อมูลจาก VM collector (20 ระดับ ทุก 10 วินาที) ~3 เดือน แล้วรันซ้ำด้วย `snapshot_features` + `find_walls` แบ่ง dev/holdout ตามเวลาก่อนดู
- ถ้าอยากทดสอบระยะยาวขึ้นตอนนี้: aggTrades + bookDepth ย้อนหลังได้หลายปี แต่ต้องเลือกช่วงให้ไม่ชน C2 และตั้งแผนก่อนรันแบบ R-series

รันซ้ำ:

```
uv run python research/orderflow/download.py --start 2026-11-01 --end 2026-11-16
uv run python research/orderflow/run_obi_filter.py --tf 15min --htf 1h
uv run python research/orderflow/run_obi_filter.py --tf 5min --htf 1h
```
