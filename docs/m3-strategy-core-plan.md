# M3: แกนกลยุทธ์ (strategy contract + order planner + tn-r10a-code-v2)

เขียน 2026-11-15 ก่อนโค้ด หลัง Note อนุมัติ checkpoint ("โอเคเริ่มเลย") ตาม
[platform-development-plan.md](platform-development-plan.md) §3 (A1, A2) และ §9 (M3 เป็นงานแรก)
Code baseline: main 3643d93

## 1. เป้าหมาย

ตอนนี้กลยุทธ์ผูกกับ runtime ทีละตัว: `runtime/r10a.py` ทั้งตัดสินใจ คำนวณขนาดคำสั่ง และเดิน state machine เอง
ส่วน `runtime/ai_r2.py` สืบทอด `R10aBot` ไปใช้ M3 แยกสามชั้นออกจากกัน

1. **Strategy contract (A1):** กลยุทธ์รับข้อมูลที่ปิดแล้ว ส่งออก `TargetDecision` เท่านั้น ไม่รู้เรื่องบัญชี ราคา bid/ask หรือ lot size
2. **Order planner (A2):** แปลง target weight เป็นขาคำสั่ง (side, qty, reduce_only) ตาม `TradingRules` และ allocation
   ใช้ร่วมกันทั้ง Spot และ Perp
3. **`tn-r10a-code-v2`:** R10a เดิมบน contract + planner ใหม่ กฎไม่เปลี่ยน

## 2. สิ่งที่ไม่ทำใน M3

- **ไม่แก้** `runtime/r10a.py`, `runtime/ai_r2.py`, `scripts/testnet_worker.py` และ worker ที่รันบน Oracle VM
  (`tn-r10a-code-v1.1` และ `tn-r2-v1` รันต่อเหมือนเดิม; thread "ให้คะแนนแอปตอนนี้" ยังดูแล P6/CP0)
- **ไม่เปิด** v2 บน Testnet ใน M3: ยังไม่มีสวิตช์ใน worker และยังไม่ส่งคำสั่ง การรัน v2 จริงต้องใช้บัญชี Testnet
  ที่ว่าง (หนึ่งบัญชีหนึ่งกลยุทธ์, RT35) และเป็นการตัดสินใจของ Note ใน M4
- ไม่มี migration และไม่มี Futures adapter (Futures พักไว้ตาม §12.0 ของแผนหลัก); planner ฝั่ง Perp มีแค่ logic + test
- frozen branches และ paper books ไม่แตะ

## 3. การออกแบบ

### 3.1 Contract (`trading/contracts/strategy.py`)

```python
class Market(StrEnum): SPOT, PERP

@dataclass(frozen=True)
class TargetDecision:
    strategy_version: str        # เช่น "tn-r10a-code-v2"
    instrument: str              # "BTCUSDT"
    market: Market
    day: date                    # วันที่ของแท่งปิดที่ใช้ตัดสิน (รายวัน; intraday ใช้ bar_close ใน M7)
    target_weight: Decimal | None  # None = ข้อมูลไม่ครบ คงสถานะเดิม (NO_DECISION)
    reason: str
    inputs: Mapping[str, Any]    # snapshot ที่ใช้ตัดสิน
    inputs_sha256: str           # sha256 ของ json.dumps(inputs, sort_keys=True) ตรวจตอนสร้าง
    data_ready_at: datetime

class Strategy(Protocol):
    version: str; instrument: str; market: Market
    def load(self, day: date) -> StrategyInputs        # ดาวน์โหลด (นอก tick thread)
    def decide(self, inputs: StrategyInputs, day: date) -> TargetDecision | None   # None = รอข้อมูล
```

- weight ต้องเป็น Decimal จำกัดค่า อยู่ใน [−1, 1]; hash ต้องตรงกับ inputs (ผิด = ValueError)
- contract **ไม่ห้าม** weight ติดลบ เพราะ Perp ใช้ได้ การห้ามสำหรับ Spot อยู่ที่ planner (§3.2)
- `decide` เป็น pure function ของ inputs: เรียกซ้ำด้วย inputs เดิมได้ผลเดิม จึง replay ได้

### 3.2 Order planner (`trading/execution/planner.py`)

input: target weight, ตลาด, สถานะของ book (ถือ base เท่าไร, เงินสดเปลี่ยนไปเท่าไร), allocation, quote (bid/ask),
`TradingRules`, เพดานต่อคำสั่ง (`max_order_notional`), fee allowance

output: `Plan` = รายการขาคำสั่ง (0–2 ขา) + reason code เมื่อไม่ส่ง

| กรณี | ผล |
| --- | --- |
| Spot, weight < 0 | ไม่ส่ง, `SPOT_NEGATIVE_WEIGHT` (refused) |
| weight > 1 หรือ < −1 | ไม่ส่ง, `WEIGHT_OUT_OF_RANGE` (refused) |
| ส่วนต่างต่ำกว่า min qty / min notional หลังปัด | ไม่ส่ง, `BELOW_MIN` |
| ซื้อแต่เงินสดของ book ไม่พอแม้ขั้นต่ำ | ไม่ส่ง, `INSUFFICIENT_FUNDS` |
| Spot เพิ่ม | BUY ที่ ask, qty = floor(min(ส่วนต่าง, เพดาน × 0.999, เงินสด) / (ask × (1 + fee))) ปัดลงตาม step |
| Spot ลด | SELL ที่ bid, reduce_only; weight 0 = ขายที่ถือทั้งหมด; ไม่ขายเกินที่ถือ |
| Perp เพิ่ม/ลดฝั่งเดิม | หนึ่งขา; ขาที่เข้าหาศูนย์เป็น reduce_only |
| Perp เปลี่ยนฝั่ง | สองขา: ปิดทั้งหมด (reduce_only) แล้วเปิดฝั่งใหม่ (ขาเปิดปัดตาม min และเพดาน) |

- สูตร Spot ตรงกับ `R10aBot._execute` ของ v1.1 ทุกตัว (equity = allocation + เงินสด + ถือ × bid,
  ปัด Decimal ตำแหน่งที่ 8 ก่อน step) เพื่อให้ intent ตรงกัน
- `off_target()` (ตำแหน่งห่างจากเป้าเกิน max(min notional, 10% ของ equity)) ย้ายมาอยู่ที่ planner ด้วยสูตรเดิม
- planner ไม่สร้าง `OrderIntent` เอง (intent ปัจจุบันเป็น Spot เท่านั้น); bot เป็นคนสร้าง intent จากขาคำสั่ง

### 3.3 Runner และ R10a v2

- `trading/strategies/r10a_v2.py`: `R10aV2` implement `Strategy`; กฎคัดลอกจาก `decide()` ของ v1.1
  (band ±2% รอบ SMA100, funding factor, รอเมื่อ funding ของวันยังไม่ออก, เติมวันที่ archive ขาดจาก REST)
  ใช้ฟังก์ชันเดิม `signals`, `band_state`, `missing_inputs`; snapshot มี key เดิมทุกตัว จึงได้ hash เดียวกับ v1.1
- `trading/runtime/target_bot.py`: `TargetBot(strategy, store, allocation, ...)` state machine เดียวกับ `R10aBot`
  (ตัดสินวันละครั้งหลัง 00:10 UTC, ลองใหม่ทุก 30 นาที, HOLD/REBALANCE/OPEN/NO_DECISION, สูงสุด 5 ครั้ง,
  รอ RUNNING ก่อนซื้อ) แต่ขนาดคำสั่งมาจาก planner; interface `step/body/close` เท่ากับ `R10aBot`
  เพื่อให้ M4 ต่อเข้า executor ได้โดยไม่แก้ executor
- book `tn-r10a-code-v2`, rules_version `tn-r10a-code-v2`, decision_id prefix `r10a2-` (client order id ไม่ชนกับ v1.1)

## 4. เกณฑ์ผ่าน (ประกาศก่อนโค้ด)

### 4.1 Replay ข้อมูล soak ของ v1.1 (A1)

ข้อมูล: คำตัดสินที่บันทึกไว้ทั้งหมดของ `tn-r10a-code-v1` บน `DEFAULT` (18 วัน: 18 ต.ค., 19 ต.ค., 29 ต.ค.–13 พ.ย.)
ดึงจาก `trading.exec_strategy_decisions` เก็บเป็น fixture ใน `tests/fixtures/r10a_soak/` พร้อมแท่งรายวันจาก
data.binance.vision และ funding ที่ worker เห็นในวันนั้น (7 วันใน snapshot + ประวัติก่อนหน้าจาก archive)
วันก่อน 11 พ.ย. ตัด 28 ต.ค. ออกเพราะ archive ยังไม่มีในตอนนั้น

| # | เงื่อนไข | ผ่านเมื่อ |
| --- | --- | --- |
| R1 | v2 กับ v1.1 บน inputs เดียวกัน | weight, reason, inputs และ hash ตรงกันทุกวัน (18/18) รวมกรณี "รอข้อมูล" |
| R2 | v2 กับแถวที่บันทึกไว้ | hash ตรงกันทุกวันที่ v1.1 ตัดสิน (17/17); 29 ต.ค. v1 บันทึก NO_DECISION แต่ v1.1 รอ funding ของวัน (กฎที่เพิ่มใน #64) จึงตรวจว่า v2 รอเหมือน v1.1 |
| R3 | action ผ่าน TargetBot | OPEN / HOLD / NO_DECISION / REBALANCE ลำดับเดียวกับที่บันทึก เมื่อ replay ตำแหน่งตามจริง |

ผลทดลองก่อนเขียนแผน (โค้ด v1.1 บน fixture นี้): hash ตรง 17/17 วัน, 29 ต.ค. = รอ

### 4.2 Intent

| # | เงื่อนไข | ผ่านเมื่อ |
| --- | --- | --- |
| I1 | คำสั่งจริงครั้งเดียวของ soak: BUY วัน 18 ต.ค. | planner ได้ BUY 0.02411 BTC MARKET เมื่อ ask = 82,786.01 (ราคา fill) เหมือน v1.1 |
| I2 | 8 พ.ย. REBALANCE 1 → 0 หลัง HALT drill ขายไปแล้ว | planner ได้ `BELOW_MIN` (เหลือ ~0.0000059 BTC) เหมือน outcome ที่บันทึก |
| I3 | เทียบสูตร v1.1 กับ planner แบบสุ่ม | ≥ 5,000 สถานะ (weight 0/0.5/1, ถือ, เงินสด, bid/ask) ได้ side, qty, reduce_only เท่ากันทุกกรณี |

### 4.3 Unit test ของ planner (A2)

rounding (step, ปัดลง, ไม่ส่งเกินแผน), min qty และ min notional, เงินไม่พอ, เพดานต่อคำสั่ง, Spot ติดลบถูกปฏิเสธ,
weight นอกช่วง, Perp เพิ่ม/ลดบางส่วน, Perp ปิดหมด, Perp เปลี่ยนฝั่ง (สองขา, ขาแรก reduce_only),
ขาเปิดที่ต่ำกว่าขั้นต่ำหลังเปลี่ยนฝั่ง (เหลือแค่ขาปิด), `off_target` ตรงกับ v1.1

### 4.4 ทั่วไป

- test เดิมทั้งหมดผ่าน, ruff และ mypy ผ่านเหมือน main
- `git diff main -- backend/src/trading/runtime/r10a.py backend/src/trading/runtime/ai_r2.py scripts/testnet_worker.py` ว่าง

## 5. แบ่ง PR

1. **แผน (PR นี้):** `docs/platform-development-plan.md` v0.2 (ลำดับที่ Note เลือก) + เอกสารนี้ ไม่มีโค้ด
2. **Contract + planner:** `contracts/strategy.py`, `execution/planner.py`, unit tests §4.3 และ I3
3. **R10a v2:** `strategies/r10a_v2.py`, `runtime/target_bot.py`, fixture + replay tests §4.1, I1, I2,
   สคริปต์ `scripts/replay_r10a_v2.py` สำหรับ replay ใหม่จาก Supabase เมื่อมีวันเพิ่ม

Note merge แต่ละ PR เอง ("merge") PR 2 และ 3 อาจเปิดซ้อนกันได้ แต่ merge ตามลำดับ

## 6. หลัง M3

- M3 เสร็จเมื่อ PR 3 merge และเกณฑ์ §4 ผ่านครบ ต่อด้วย M2 (กราฟ) ตามลำดับที่ Note เลือก
- การรัน v2 แทน v1.1 บน `DEFAULT` หรือบนบัญชีใหม่ และการย้าย `tn-r2-v1` มาใช้ TargetBot เป็นงานของ M4
  (worker ดึง config ที่ approve แล้ว) ต้องมีแผนและการอนุมัติของ Note แยก
