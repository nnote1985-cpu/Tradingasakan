# M4: ตั้งค่าบอทจากหน้าเว็บ (C1, C2, F3, worker ดึง config)

เขียน 2026-11-15 ก่อนโค้ด ตาม [platform-development-plan.md](platform-development-plan.md) §5 (Workstream C),
§8 F3 และ §9 (M4 ต่อจาก M3 และ M2)
Code baseline: main cf32c96

## 1. เป้าหมาย

ตอนนี้บอทบน Testnet ถูกเปิดด้วย environment variable บน VM (`WORKER_R10A=on`, `WORKER_AI_R2=on`)
ค่าทุกอย่าง (กลยุทธ์ เงิน book) อยู่ในโค้ด M4 ทำให้บอทเป็นข้อมูลที่ตั้งค่า ทบทวน และอนุมัติจากหน้าเว็บได้

1. **C1 Entity:** ตาราง `trading.bots` (revision แบบ append-only พร้อม hash) และ `trading.bot_approvals`
   (เหตุการณ์ submit / approve / reject / retire พร้อมผู้ทำ เวลา และ hash)
2. **C2 Validation ฝั่ง server:** ค่าทุกช่องตรวจที่ API; ตอน approve ตรวจซ้ำกับยอดเงินจริงของบัญชี
3. **F3 Wizard:** ตลาด/บัญชี → กลยุทธ์ → เงิน/ความเสี่ยง → ทบทวน/diff → บันทึกร่าง หรือส่งอนุมัติ
4. **Worker ดึง config:** worker อ่านเฉพาะ revision ที่ approve แล้ว ตรวจ hash เอง และรายงาน `bot_id@revision` + hash ใน heartbeat

หลัง M4 บอทตัวแรกคือ `tn-r10a-code-v2` (จาก M3) บนบัญชี Testnet ที่ว่าง เริ่มที่ SHADOW แล้วจึงเป็น TESTNET

## 2. ขอบเขตและสิ่งที่ไม่ทำ

- **LIVE ถูกปฏิเสธ** ทุกชั้น: `mode` มีแค่ `SHADOW` / `TESTNET`, `venue` มีแค่ `SPOT_TESTNET` (Perp ยังพักตาม §12.0)
- **เว็บไม่ถือ key และไม่สั่ง worker ตรง:** เว็บเขียนแถวใน Supabase ผ่าน API เท่านั้น worker เป็นฝ่ายดึง
- **ไม่เปลี่ยนพฤติกรรมของ worker ที่รันอยู่:** `DEFAULT` (tn-r10a-code-v1.1) และ `AI` (tn-r2-v1) รันด้วย env เดิม
  ส่วนใหม่ทำงานเฉพาะเมื่อตั้ง `WORKER_BOT=on` บนบัญชีนั้น (opt-in) ไม่ตั้ง = โค้ดเดิมทุกบรรทัด
- ไม่แก้ `runtime/r10a.py`, `runtime/ai_r2.py`; frozen branches และ paper books ไม่แตะ
- กลยุทธ์ที่รันได้มีแค่ `tn-r10a-code-v2` ใน M4 (E1 บน Testnet เป็น M7) และ v2 ไม่มี parameter ให้ปรับ
  (กฎตรึงไว้เท่ากับ v1.1 เพื่อให้ replay ตรง) ระบบตรวจ params ทำแบบทั่วไปเพื่อรอ template ที่มี params
- recipe ใน `bot_profile_drafts` ยังไม่มี engine รัน: อ่านได้ต่อ และแปลงเป็นร่างบอทได้ (คัดชื่อ หมายเหตุ ตลาด
  และลิงก์กลับ) แต่ต้องเลือก template ที่รันได้ในขั้นกลยุทธ์ เว็บบอกเหตุผลตรง ๆ

## 3. การออกแบบ

### 3.1 Config ของบอท (`bot-config-v1`)

```json
{
  "schema_version": "bot-config-v1",
  "name": "R10a v2 บัญชี BOT1",
  "account": "BOT1",
  "venue": "SPOT_TESTNET",
  "instrument": {"symbol": "BTCUSDT", "market": "SPOT"},
  "strategy": {"template": "tn-r10a-code-v2", "version": "tn-r10a-code-v2", "timeframe": "1d", "params": {}},
  "allocation": {"asset": "USDT", "amount": "2000"},
  "risk_profile": "spot.tn.v1",
  "futures": null,
  "mode": "SHADOW",
  "source_draft": null,
  "notes": ""
}
```

- **hash** = sha256 ของ JSON ที่ normalize แล้ว (`sort_keys`, ตัวเลขเงินเป็น string, params เติมค่า default ของ template)
  วิธีเดียวกับ `bot_profile_drafts`; API, DB trigger และ worker คำนวณด้วยฟังก์ชันเดียวกัน (`trading/bots/config.py`)
- **book** ของบอท = `bot-<8 ตัวแรกของ bot_id>` คงที่ทุก revision (ตำแหน่งและการตัดสินใจเดิมต่อเนื่อง)
- **account, allocation และ strategy คงที่ทุก revision:** Guardian วัด drawdown จาก allocation ถ้าเปลี่ยนเงินกลางทาง
  equity จะกระโดดและอาจ HALT ผิด ๆ; เปลี่ยนสามอย่างนี้ = สร้างบอทใหม่ (book ใหม่) ส่วน revision เปลี่ยนได้แค่ชื่อ หมายเหตุ และ mode
- `futures` ต้องเป็น null สำหรับ Spot; สำหรับ Perp (เก็บโครงไว้ แต่ approve ไม่ได้จนกว่าจะมี template Perp):
  `leverage` = 1 เท่านั้น, `margin_mode` = `isolated` เท่านั้น, `stop_percent` ต้องน้อยกว่าระยะ liquidation ที่ 1x
- template ที่รันได้อยู่ใน registry `trading/bots/templates.py`: id, version, ตลาด, symbol, timeframe ที่รองรับ,
  params พร้อมขอบเขต, allocation ต่ำสุด/สูงสุด, และ factory ที่สร้าง `Strategy`

### 3.2 ตาราง (migration `bots`)

| ตาราง | คอลัมน์หลัก | กติกา |
| --- | --- | --- |
| `trading.bots` | `bot_id`, `revision`, `body`, `hash`, `account`, `saved_by`, `saved_at`, `request_id` | append-only (trigger `forbid_change`); PK (`bot_id`, `revision`); `request_id` unique กันบันทึกซ้ำ; `account` คงที่ทุก revision |
| `trading.bot_approvals` | `id`, `bot_id`, `revision`, `hash`, `action`, `actor`, `at`, `note` | append-only; `action` ∈ submit / approve / reject / retire; FK ไป (`bot_id`, `revision`); trigger ตรวจว่า `hash` เท่ากับ hash ของ revision นั้น และ approve/reject/retire ต้องมาจากผู้ที่มี role operator หรือ admin ที่ active ใน `operator_roles` |

RLS เปิดและให้สิทธิ์แค่ `service_role` เหมือนตารางอื่นใน `trading`

### 3.3 สถานะ

| สถานะ | มาจาก |
| --- | --- |
| `draft` | มี revision ที่ยังไม่ submit |
| `submitted` | revision ล่าสุด submit แล้ว รอ approve (สถานะย่อยของ draft ตามแผนหลัก) |
| `approved` | มี revision ที่ approve แล้ว และ worker ยังไม่รายงานว่ารัน |
| `running` | heartbeat ของบัญชีรายงาน `bot_id@revision` ของบอทนี้ |
| `halted` | heartbeat รายงานบอทนี้ และ safety ของบัญชีเป็น HALTED |
| `retired` | มี event retire (จบถาวร revision ใหม่บันทึกไม่ได้) |

`running` / `halted` อ่านจาก heartbeat เท่านั้น เว็บและ API ไม่เขียนสถานะเหล่านี้เอง

### 3.4 API (`apps/api/api/bots.py`)

| Endpoint | สิทธิ์ | ทำอะไร |
| --- | --- | --- |
| `GET /api/v1/bots` | viewer | บอททุกตัว: revision ล่าสุด, revision ที่ approve, สถานะ, revision ที่รันจริงจาก heartbeat |
| `GET /api/v1/bots/{id}` | viewer | revision ทั้งหมด + event อนุมัติ + diff ระหว่าง revision |
| `GET /api/v1/bot-templates` | viewer | registry ของ template ที่รันได้ พร้อมขอบเขต params และ allocation |
| `GET /api/v1/bot-accounts` | viewer | บัญชีที่ worker เคยรัน + USDT free ล่าสุด (`exec_balances`) + ว่าง/ไม่ว่างพร้อมเหตุผล |
| `POST /api/v1/bots` | operator | บันทึก revision ใหม่ (`expected_revision` → 409 เมื่อชนกัน; `request_id` idempotent เหมือนร่างเดิม) |
| `POST /api/v1/bots/{id}/revisions/{rev}/submit` | operator | ส่งอนุมัติ |
| `POST /api/v1/bots/{id}/revisions/{rev}/approve` | operator/admin | ตรวจ C2 ซ้ำกับข้อมูลปัจจุบันแล้วบันทึก approve พร้อม hash |
| `POST /api/v1/bots/{id}/revisions/{rev}/reject` | operator/admin | ปฏิเสธพร้อมเหตุผล |
| `POST /api/v1/bots/{id}/retire` | operator/admin | ได้เฉพาะบอทที่ไม่เคยรัน หรือ heartbeat บอกว่า HALTED และ book ว่าง |
| `POST /api/v1/bot-profile-drafts/{id}/convert` | operator | สร้างร่างบอทจาก recipe (`source_draft` ชี้กลับ) |

**กติกา C2 ที่ server (error เป็นรายช่อง `{field, code, message_th}` เพื่อให้เว็บแสดงใต้ช่อง):**

- template มีใน registry; version ตรง; timeframe อยู่ในรายการที่รองรับ; params ครบและอยู่ในขอบเขต; ไม่มีฟิลด์แปลก
- `venue` = `SPOT_TESTNET`, `mode` ∈ SHADOW/TESTNET; ค่าอื่น (รวม LIVE) ปฏิเสธ
- allocation อยู่ในขอบเขตของ template และไม่เกินเพดานต่อคำสั่ง 2,000 USDT (หนึ่ง rebalance เป็นหนึ่งคำสั่ง)
- ตอน approve: allocation ≤ USDT free ล่าสุดของบัญชี และยอดนั้นต้องมาจาก reconcile ที่ MATCH ไม่เกิน 15 นาที
  (ไม่มีข้อมูลสด = approve ไม่ได้ พร้อมเหตุผล)
- **หนึ่งบัญชีหนึ่งบอท:** บันทึก/approve ถูกปฏิเสธเมื่อบัญชีมีบอทอื่นที่ยังไม่ retire หรือ heartbeat ล่าสุดของบัญชี
  รายงานกลยุทธ์ที่เปิดด้วย env (`DEFAULT` = R10a v1.1, `AI` = tn-r2-v1) ตรวจใน transaction เดียวพร้อม advisory lock ต่อบัญชี
- Futures: leverage ≠ 1 หรือ margin ≠ isolated ปฏิเสธ; approve Perp ปฏิเสธ ("ยังไม่มีกลยุทธ์ Perp ที่รันได้")

### 3.5 Worker (`WORKER_BOT=on`)

- ตอนเริ่ม: อ่านบอทของ `WORKER_ACCOUNT` ที่ยังไม่ retire และมี revision ที่ approve แล้ว
  - ไม่มี / มีมากกว่าหนึ่ง / ใช้ร่วมกับ `WORKER_R10A`, `WORKER_AI_R2`, `WORKER_CONFORMANCE` → **ไม่เริ่ม** (exit 2) และแจ้ง Telegram
  - worker คำนวณ hash จาก body เอง: ไม่ตรงกับ `bots.hash` หรือ hash ใน event approve → **ไม่เริ่ม** + CRITICAL alert
  - ใช้ revision ที่ approve ล่าสุดเท่านั้น ร่างหรือ submit ที่ใหม่กว่าไม่ถูกใช้
- สร้าง `TargetBot` จาก template (M3) ด้วย allocation และ book ของบอท; Guardian ดู book นั้นด้วย spot.tn.v1
  `mode = SHADOW` บันทึกการตัดสินใจแต่ไม่ส่งคำสั่ง (outcome `shadow: not sent`)
- heartbeat: `execution.bot = {bot_id, revision, hash, mode, pending_revision, note}`
- ทุก 5 นาทีตรวจ revision ที่ approve ใหม่: เปลี่ยนเมื่อ book ว่างและไม่มีคำสั่งค้าง ไม่อย่างนั้นแสดง
  "revision N รอ: ยังมีสถานะเปิด" จนกว่า operator จะ HALT (ขายออก) ก่อน; hash ของ revision ใหม่ไม่ตรง = ไม่เปลี่ยน + alert
- event retire: หยุดตัดสินใจและแจ้งเตือน (retire ได้เฉพาะเมื่อ book ว่างอยู่แล้ว ตาม §3.4)

### 3.6 Web (F3)

- หน้าใหม่ "บอท" ในส่วนกลยุทธ์: รายการบอท (ชื่อ บัญชี สถานะ revision ที่รันจริงจาก heartbeat mode) และ wizard
- Wizard 4 ขั้น: (1) ตลาดและบัญชี: Spot เลือกได้, Perp แสดงแต่เลือกไม่ได้พร้อมเหตุผล, บัญชีที่ถูกใช้เลือกไม่ได้พร้อมเหตุผล
  (2) กลยุทธ์จาก template + ลิงก์ผลทดสอบเดิมถ้ามี (3) เงินและ mode; leverage 1x แก้ไม่ได้ (เฉพาะ Perp)
  (4) ทบทวน: สรุป + diff กับ revision ก่อนหน้า → บันทึกร่าง / ส่งอนุมัติ; ผู้มีสิทธิ์เห็นปุ่ม approve / reject
- error ใต้ช่องจาก API, โฟกัสช่องแรกที่ผิด, ร่างที่ยังไม่บันทึกเก็บใน query cache (เหมือนร่างเดิม) และเตือนก่อน reload
- dialog ของระบบแทน `window.confirm`; ปุ่มอนุมัติและ retire แยกตำแหน่งและสี พร้อมข้อความว่าจะเกิดอะไร
- `DraftProfiles.tsx` แยกเป็น component ต่อขั้น; ร่างเดิมอ่านได้และมีปุ่ม "แปลงเป็นบอท"

## 4. ลำดับ PR

| PR | เนื้อหา | แตะ runtime? |
| --- | --- | --- |
| 1 | แผนนี้ (docs) | ไม่ |
| 2 | migration `bots` + `trading/bots/` (config, hash, templates, validation, store) + test บน PostgreSQL ชั่วคราว + fingerprint | ไม่ |
| 3 | API `bots.py` + test (สิทธิ์, 409, idempotent, C2, หนึ่งบัญชีหนึ่งบอท, approve ตรวจยอด) | ไม่ |
| 4 | Web: หน้า "บอท", wizard, แยก DraftProfiles, แปลงร่าง + vitest | ไม่ |
| 5 | Worker `WORKER_BOT=on`: โหลด/ตรวจ hash, TargetBot + SHADOW, heartbeat, เปลี่ยน revision ตอน book ว่าง + test | ใช่ (opt-in) ประสาน thread "ให้คะแนนแอปตอนนี้" ก่อนเปิด PR |
| 6 | เปิดใช้จริง (ไม่มีโค้ด): บัญชี Testnet สำหรับบอท, `worker@<บัญชี>` บน VM, บอท v2 SHADOW → approve TESTNET, drill ตามเกณฑ์ §5 | Note ทำบน VM |

migration ทดสอบบน PostgreSQL ชั่วคราว (`tests/integration/test_migrations.py`) และ apply บน Supabase หลัง Note merge เท่านั้น
แล้วเปลี่ยนชื่อไฟล์เป็น version ที่ Supabase บันทึก + อัปเดต `migration_history.txt` / `schema_fingerprint.txt`

## 5. เกณฑ์ผ่าน

| # | เกณฑ์ | ตรวจด้วย |
| --- | --- | --- |
| G1 | config ที่ยังไม่ approve ไม่ถูก worker ใช้: มีแค่ร่าง/submit = worker ไม่เริ่ม; approve rev 1 แล้วมีร่าง rev 2 = รัน rev 1 | unit test worker + drill บน VM |
| G2 | hash ไม่ตรง = worker ไม่เริ่มและแจ้งเตือน CRITICAL; revision ใหม่ hash ไม่ตรง = ไม่เปลี่ยนและแจ้งเตือน | unit test (body ถูกแก้ระหว่างทาง) |
| G3 | สองบอทชี้บัญชีเดียวกันถูกปฏิเสธที่ API (409) และที่ worker (ไม่เริ่ม); บัญชีที่รันบอท env (DEFAULT, AI) เลือกไม่ได้ | API test + worker test |
| G4 | approve ต้องเป็น operator/admin; viewer ได้ 403; DB ปฏิเสธ approve จาก user ที่ไม่มี role; event บันทึกผู้ทำ เวลา hash | API test + test บน PostgreSQL |
| G5 | C2: params นอกขอบเขต, timeframe ไม่รองรับ, ฟิลด์แปลก, LIVE, allocation > 2,000 หรือ > USDT free ตอน approve, ยอดเก่ากว่า 15 นาที, Perp leverage ≠ 1 / margin ≠ isolated ถูกปฏิเสธพร้อม error รายช่อง | API test |
| G6 | `bots` และ `bot_approvals` แก้/ลบไม่ได้ (append-only); `request_id` ซ้ำได้ revision เดิม; ชนกัน = 409 | test บน PostgreSQL |
| G7 | heartbeat รายงาน `bot_id@revision` + hash; หน้าเว็บแสดง revision ที่รันจริงจาก heartbeat ไม่ใช่จากตาราง | worker test + vitest |
| G8 | revision ใหม่ที่ approve ขณะมีสถานะเปิดไม่ถูกใช้จนกว่า book ว่าง; เปลี่ยนแล้ว heartbeat แสดง revision ใหม่ | worker test + drill |
| G9 | ไม่ตั้ง `WORKER_BOT` = พฤติกรรมเดิม: test ของ worker/r10a/ai_r2 ผ่านทั้งหมดโดยไม่แก้; env บน VM ของ DEFAULT/AI ไม่เปลี่ยน | ชุด test เดิม |
| G10 | ร่างเดิมใน `bot_profile_drafts` อ่านได้ทุกตัว และแปลงเป็นร่างบอทได้พร้อมลิงก์กลับ | API test + vitest |
| G11 | Wizard: error ใต้ช่องและโฟกัสช่องแรกที่ผิด, diff กับ revision ก่อน, dialog แทน `window.confirm`, ใช้ได้ที่ 375 / 768 / 1440px | vitest + screenshot 3 ขนาด |
| G12 | บอทแรก (`tn-r10a-code-v2`) รัน SHADOW ≥ 1 decision แล้ว approve เป็น TESTNET ได้ด้วย revision ใหม่; การตัดสินใจของ v2 ตรงกับ v1.1 ในวันเดียวกัน (weight และ `inputs_sha256`) | ข้อมูลจริงบน Supabase |

## 6. เรื่องที่ต้องให้ Note ตัดสิน

1. **บัญชีของบอทแรก:** (แนะนำ) Spot Testnet บัญชีที่สาม เช่น `BOT1` เพื่อให้ v1.1 บน `DEFAULT` รันต่อและเทียบกับ v2
   วันต่อวันได้ หรือย้าย `DEFAULT` จาก v1.1 (env) มาเป็นบอท v2 (หยุด v1.1 และต้องปิด `WORKER_R10A`)
   ทั้งสองแบบ Note ต้องทำบน VM: สร้าง key (แบบแรก) และตั้ง `WORKER_BOT=on` ใน env ของ worker นั้น
2. **ผู้ approve:** โปรเจกต์มีผู้ใช้คนเดียว จึงให้คนที่บันทึกร่าง approve เองได้ (บันทึกชื่อและเวลาไว้)
   ถ้าต้องการให้คนอื่น approve ต้องเพิ่ม operator อีกคนก่อน

## 7. ความเสี่ยง

| ความเสี่ยง | การรับมือ |
| --- | --- |
| config ผิดแล้วบอทรันทันที | approve แยกขั้น, C2 ที่ server, worker ตรวจ hash เอง, เริ่มที่ SHADOW |
| hash ต่างกันระหว่าง API กับ worker เพราะ normalize ไม่เหมือนกัน | ฟังก์ชันเดียวใน `trading/bots/config.py` + test ที่ fix ค่า hash ของตัวอย่าง |
| ยอดเงินที่ใช้ตอน approve เก่า | ต้องมาจาก reconcile MATCH ไม่เกิน 15 นาที; Guardian และ budget ของ executor ยังตรวจซ้ำตอนส่งคำสั่ง |
| เปลี่ยน revision กลางสถานะเปิด | เปลี่ยนเฉพาะเมื่อ book ว่าง; ไม่อย่างนั้นรอ HALT |
| โค้ดใหม่กระทบ worker ที่รันอยู่ | opt-in `WORKER_BOT`, test เดิมต้องผ่านโดยไม่แก้, ประสาน thread ที่ดูแล VM ก่อน PR 5 |
