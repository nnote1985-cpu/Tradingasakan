# Testnet P6: AI r2 บน Testnet (แผน เขียนก่อนโค้ด)

เขียน 2026-11-08 หลัง Note สั่ง "เริ่ม p6 ก่อนเลย" ต่อจากแผนหลัก docs/testnet-development-plan.md
§10.3 (P6 — AI r1/r2) และ §6.3, §6.4, §12.2 ยังไม่มีโค้ด P6 และยังไม่มีการเรียก AI บน Testnet

## 1. สถานะก่อนเริ่ม

- P5 (R10a Code-only + Guardian) ผ่าน soak 7 วันในส่วนระบบ (31 ต.ค. ถึง 7 พ.ย.) และ drill ครบ 5 ข้อ
  แต่ R10a ยังไม่ได้ส่งคำสั่งระหว่าง soak เพราะ archive ของ Binance ไม่มีราคาวันที่ 28 ต.ค.
  PR #64 (tn-r10a-code-v1.1) แก้แล้ว รอ Note อัปเดต VM
- AI ที่มีอยู่แล้ว (paper, Shadow): r1 = pathA_filter (FILTER_ONLY), r2 = pathA_proposer
  (BOUNDED_PROPOSAL เลือก 0 / 0.5 / 1) ทั้งคู่ใช้ claude-opus-5-5 เรียกครั้งเดียว ไม่มี tools
  ไม่มี retry ไม่มี fallback ค่าใช้จ่ายจริงของ r2 ประมาณ 0.015 USD ต่อครั้ง (10 ครั้ง 0.153 USD ถึง 8 พ.ย.)
- r1 ถาม AI เฉพาะตอน Code จะเพิ่มน้ำหนัก บน R10a เกิดไม่กี่ครั้งต่อปี (R10a เทรด 48 ครั้งใน 4 ปี)
  ใน 28 วันอาจไม่มีคำถามเลย จึงทดสอบ pipeline ไม่ได้

## 2. ขอบเขต (ค่าเริ่มต้นที่ผมเลือก)

| เรื่อง | ค่าเริ่มต้น | เหตุผล |
| --- | --- | --- |
| เริ่มด้วย | **r2 อย่างเดียว** เป็น experiment `tn-r2-v1`; r1 เลื่อนไปทีหลัง | r2 ตัดสินทุกวัน ได้ ~28 ครั้งใน 28 วัน; r1 แทบไม่ถูกเรียก |
| บัญชี | **Spot Testnet บัญชีที่สอง** (account `AI`) แยกจาก `DEFAULT` ที่ R10a ใช้ | §12.2: Code กับ AI ใช้ BTC inventory ร่วมกันไม่ได้ (RT35) |
| เงิน | allocation 2,000 USDT, profile `spot.tn.v1` (Guardian เดิม, HALT 25%) | เท่ากับ R10a ให้เทียบกันได้ |
| Prompt / model | ใช้ pathA_proposer เดิมไม่แก้ (prompt v1, claude-opus-5-5, schema เดิม) | reuse ตาม §10.3; แก้ prompt = revision ใหม่ |
| Snapshot | quant_only จาก inputs ของ R10a วันเดียวกัน (close, SMA100, funding 7 วัน) ไม่มี Code weight | RT13 |
| เวลา | ถามหลัง R10a ได้ข้อมูลของวันนั้น; deadline 120 วินาที; TTL 5 นาที | §6.3 |
| ล้มเหลว | invalid / timeout / refusal / งบหมด = คงน้ำหนักเดิม บันทึกแยกประเภท | RT12 |
| ที่เรียก AI | worker บน Oracle VM เรียก Anthropic API เอง | cloud เข้า Binance ไม่ได้ (451); worker มีข้อมูลอยู่แล้ว |
| Key | `TN_ANTHROPIC_KEY` ใหม่ ใส่ใน /etc/tradingasakan/worker-ai.env (600) ตั้ง spend limit 5 USD/เดือนใน Anthropic Console | แยกงบจาก paper r1/r2 (P5_ANTHROPIC_KEY) |
| งบในโค้ด | 1 USD/วัน, 5 USD/เดือน; เกินงบ = ไม่เรียก ไม่ fallback | §6.4 |
| คำสั่ง | AI ไม่มี order tool; โค้ด compile คำตอบ แล้วส่งผ่าน executor + Guardian เดิม | RT15, RT17 |

## 3. ตัวเปรียบเทียบ

- `DEFAULT` / tn-r10a-code-v1.1 บน Testnet (Code arm ที่รันอยู่แล้ว)
- Shadow paper books ที่มีอยู่: pathA-p5-r2/ai, /code, /buy_and_hold (Mainnet data)
- Testnet ไม่ได้พิสูจน์ alpha (§12.2) รายงานติดป้าย TESTNET แยกจาก SHADOW เสมอ และสอง arm บน
  Testnet ไม่ได้ match fills กัน ผลรีวิววัน 7/14/28 คาดว่าเป็น INSUFFICIENT_EVIDENCE ด้านผลตอบแทน
  สิ่งที่วัดได้คือ pipeline ทำงานครบ ความน่าเชื่อถือ latency และค่าใช้จ่ายจริง

## 4. งานแบ่งเป็น PR

1. **AI book runtime (mock เท่านั้น)**: `runtime/ai_r2.py` ใช้ `pathA_proposer.ask` เดิม, snapshot จาก
   R10a inputs, บันทึก attempt ลง `trading.agent_decisions` (มีคอลัมน์ model/cost/latency แล้ว) และ
   decision ลง `trading.exec_strategy_decisions` (book `tn-r2-v1`), เปิดด้วย `WORKER_AI_R2=on`
   ไม่เปิดพร้อม `WORKER_R10A=on` ใน process เดียว; tests: RT06, RT11, RT12, RT13, RT15, RT17, RT23, RT35
2. **Worker ตัวที่สอง**: systemd template `tradingasakan-worker@.service` (`WORKER_ACCOUNT=AI`,
   EnvironmentFile แยก), lease/heartbeat/dead-man แยกต่อบัญชี, Telegram ขึ้นชื่อบัญชี;
   provider smoke test 1 ครั้งจริงบน VM (§6.3) ก่อนเปิด paid call
3. **หน้าเว็บ**: แท็บ Worker แสดงสองบัญชี, คำตอบ AI แต่ละวัน (น้ำหนัก เหตุผล ค่าใช้จ่าย) เทียบ R10a

## 5. Gate ของ P6

- mock tests ผ่านครบตาม PR 1 และ actual provider smoke ผ่าน 1 ครั้ง
- รัน 7 วันบนบัญชี AI: AI ตอบทุกวัน หรือความล้มเหลวถูกบันทึกถูกประเภท; คำสั่งที่ส่งตรงกับน้ำหนักที่ compile;
  reconcile MATCH; ไม่มี UNKNOWN ค้าง; ค่าใช้จ่ายจริงไม่เกินงบ
- HALT drill บนบัญชี AI ระหว่างรอคำตอบ AI (RT17)

## 6. สิ่งที่ Note ต้องทำ

- สร้าง Spot Testnet บัญชีที่สองที่ testnet.binance.vision (ผมเข้าใจว่าแต่ละบัญชีผูกกับ GitHub login หนึ่งบัญชี
  key หลายอันในบัญชีเดียวกันใช้ยอดเงินร่วมกัน จึงแยก inventory ไม่ได้; ยังไม่ได้ยืนยันจากเอกสาร Binance)
  สร้าง Ed25519 key ใหม่ และวาง .pem บน VM เอง ห้าม paste ลงแชท
- สร้าง Anthropic API key ใหม่พร้อม spend limit แล้วใส่ใน env file บน VM เอง
- ถ้าไม่มีบัญชีที่สอง ทางเลือกคือรันแบบสลับช่วง (§12.2): หยุด R10a, เปลี่ยน inventory ให้ชัด แล้วรัน r2 บน DEFAULT

## 7. ที่ไม่อยู่ใน P6

r1 บน Testnet, ข่าว/macro (P7), multi-analyst หรือ agent-v1 (5 agents), Futures (P10), Mainnet / LIVE
