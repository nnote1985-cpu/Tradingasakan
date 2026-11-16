# M1: ราคาและสถานะ Spot แบบ realtime บนเว็บ (lean SSE)

เขียน 2026-11-16 ก่อนโค้ด ตาม [platform-development-plan.md](platform-development-plan.md) §6 (Workstream D),
§8 F1 และ §12.0 (Realtime เอนไปทาง SSE จาก API; M1 เป็น checkpoint ถัดจาก M4)
Code baseline: main 73aa0e3

## 1. เป้าหมาย

ตอนนี้แท็บ Worker poll API ทุก 10–15 วินาที และราคา Mainnet บนหน้าตลาดต่อ `stream.binance.com` จากเบราว์เซอร์เอง
M1 ทำให้สิ่งที่ worker เขียนลง Supabase (heartbeat, ราคา Testnet, orders, fills, decisions, risk events, คำขอ HALT/RESUME)
ขึ้นจอภายในไม่กี่วินาทีโดยไม่ต้องรอรอบ poll และมีแถบสถานะบนสุดที่บอกว่าข้อมูลบนจอสดหรือค้าง

"Lean" หมายถึง:

1. **ไม่มี migration และไม่แตะ worker:** อ่านจากตารางที่ worker เขียนอยู่แล้ว ไม่เปิด Supabase Realtime ไม่แก้ RLS
2. **SSE บอกแค่ว่า "มีอะไรเปลี่ยน":** ข้อมูลจริงยังมาจาก endpoint REST เดิม เว็บ refetch เมื่อได้รับสัญญาณ
   จึงไม่มีรายการหายหรือซ้ำตอนสายหลุด เพราะทุกครั้งที่ต่อใหม่เว็บ refetch ทั้งหมดจากต้นทางเดียว
3. **poll เดิมคงไว้เป็นทางสำรอง:** ระหว่าง stream ปกติ poll ห่างขึ้นเป็น 60 วินาที; stream หลุดกลับเป็น 10–15 วินาทีเหมือนเดิม

## 2. ขอบเขตและสิ่งที่ไม่ทำ

- อ่านอย่างเดียว ไม่มี endpoint ใหม่ที่เขียนข้อมูลหรือสั่ง worker; HALT/RESUME ยังใช้คำขอ operator เดิม
- **ราคา Testnet เร็วสุดเท่ารอบ heartbeat (~10 วินาที)** เพราะ worker เขียน `feeds.quote_bid` ลง heartbeat ทุก ~10 วินาที
  การให้ worker เขียน quote ทุก 1–2 วินาทีตาม §6 ต้องแก้ worker ที่รันอยู่ จึงแยกเป็นข้อเสนอหลัง M1 (§6) ไม่อยู่ใน M1
- ราคา Mainnet ยังมาจาก `liveTicker.ts` (เบราว์เซอร์ต่อ Binance เอง ไม่ผ่าน backend) แถบสถานะใช้ค่านี้คู่กับราคา Testnet
- ไม่ทำปุ่ม HALT ทั้งหมดบนแถบสถานะใน M1 (เป็นปุ่มอันตราย ทำพร้อมหน้าบอท F2 ภายหลัง); ปุ่ม HALT/RESUME ต่อบัญชีในแท็บ Worker ใช้ได้เหมือนเดิม
- ไม่ทำหน้ารายละเอียดบอท F2 และไม่เปลี่ยน hash routing เป็น `react-router` (แยกเป็นงานถัดไป)
- Futures ยังพักตาม §12.0

## 3. การออกแบบ

### 3.1 API: `GET /api/v1/stream` (Server-Sent Events)

- สิทธิ์ `VIEWER` ขึ้นไปผ่าน `authenticate` เดิม (Bearer token ใน header) ผู้ใช้ที่ไม่อยู่ใน `trading.operator_roles` ได้ 401/403 เหมือน endpoint อื่น
- เว็บเรียกด้วย `fetch` + `ReadableStream` ไม่ใช้ `EventSource` เพราะ `EventSource` ส่ง header `Authorization` ไม่ได้
  และไม่ใส่ token ใน URL (กัน token ลง log)
- server ปิด stream เองเมื่อครบ 10 นาที เว็บต่อใหม่ด้วย token ล่าสุดจาก `supabase.auth.getSession()` จึงไม่มี stream ที่ใช้ token หมดอายุ

**ตัวเฝ้าการเปลี่ยนแปลง (`api/stream.py`, `ChangeWatcher`):** task เดียวต่อ process ของ API
query ฐานข้อมูลทุก 1 วินาทีเมื่อมีผู้ฟังอย่างน้อยหนึ่งราย (ไม่มีผู้ฟัง = ไม่ query) แล้วกระจายผลให้ทุก stream
จำนวน query จึงไม่เพิ่มตามจำนวนแท็บที่เปิด query เป็น cursor ต่อบัญชี:

| cursor | มาจาก |
| --- | --- |
| `heartbeat` | `worker_heartbeats.updated_at` |
| `orders` | จำนวนแถวและเวลาล่าสุดใน `exec_order_events` ของ intent ในบัญชี |
| `trades` | จำนวนแถวและ `received_at` ล่าสุดใน `exec_trades` |
| `decisions` | `max(decided_at)`, `max(executed_at)` ใน `exec_strategy_decisions` |
| `risk` | `max(id)` ใน `exec_risk_events` |
| `requests` | `max(id)` และ `max(handled_at)` ใน `exec_operator_requests` |
| `reconcile` | `max(id)` ใน `exec_reconciliations` |

ตารางยังเล็ก (หลักร้อยแถวต่อบัญชี) query เดียวต่อวินาทีพอโดยไม่ต้องเพิ่ม index; ถ้าภายหลังช้าเกิน 100 ms จะเสนอ index แยก

**event ที่ส่ง** (บรรทัด `event:` + `data:` JSON, `id:` เป็นเลขลำดับใน stream):

| event | data | เว็บทำอะไร |
| --- | --- | --- |
| `hello` | cursor ทั้งหมด + `server_time` | refetch ทุก query ของแท็บที่เปิดอยู่ (รวมตอนต่อใหม่) |
| `heartbeat` | ต่อบัญชี: `updated_at`, `quote_bid`, `quote_ask`, `quote_age_ms`, `safety`, `mode`, `silent` | อัปเดตแถบสถานะทันที + invalidate `["workers"]` |
| `changed` | `{account, kinds: ["orders","trades",...], cursors}` (cursor มีเวลาล่าสุด ใช้วัดความหน่วง §5.2) | invalidate query ของบัญชีนั้น (`activity`, `control`) |
| `ping` | `server_time` ทุก 15 วินาที | ใช้ตรวจว่าสายยังอยู่ |

ค่า `heartbeat` ใช้ฟังก์ชันเดิมใน `api/workers.py` (`worker_items`) ตัดเหลือคอลัมน์ที่แถบสถานะใช้ ไม่เขียน serializer ซ้ำ

### 3.2 เว็บ

- `src/live/useLiveStream.ts`: hook ตัวเดียวทั้งแอป (อยู่ใน `App.tsx`) ต่อ stream, แยก event, backoff 2→30 วินาที,
  ถือว่า "ค้าง" เมื่อไม่ได้ event ใด ๆ (รวม `ping`) เกิน 30 วินาที แล้วสั่ง react-query ตามตาราง §3.1
- query เดิม (`["workers"]`, activity, control) ใช้ `refetchInterval` จาก hook: 60 วินาทีเมื่อ stream ปกติ, ค่าเดิมเมื่อหลุด
- `src/live/StatusBar.tsx` (F1) แสดงบนทุกหน้า:
  - สถานะการเชื่อมต่อ: สด / กำลังต่อ / ค้าง (ข้อความ + ไอคอน ไม่ใช้สีอย่างเดียว)
  - worker ที่ heartbeat ปกติ เช่น `worker 3/3` และบัญชีที่เงียบเกิน 60 วินาที
  - Guardian: `safety` ของแต่ละบัญชี (แสดงชัดเมื่อไม่ใช่ปกติ เช่น HALTED)
  - BTCUSDT: ราคา Testnet (bid จาก heartbeat + อายุข้อมูล) คู่ราคา Mainnet (จาก `liveTicker`) และส่วนต่าง %
  - จอแคบ 375px ย่อเหลือ สถานะ + worker + ราคา Testnet; ที่เหลือกดดูได้
- ทุกค่ามีอายุข้อมูล ถ้าเกินเกณฑ์ (ราคา Testnet > 30 วินาที, heartbeat > 60 วินาที) ขึ้น "ค้าง" พร้อมเวลาที่อัปเดตล่าสุด

## 4. ลำดับ PR

| PR | เนื้อหา | ขึ้นกับ |
| --- | --- | --- |
| 1 | แผนนี้ (`docs/m1-realtime-plan.md`) | – |
| 2 | API: `api/stream.py` (`ChangeWatcher`, SSE), route ใน `main.py`, test ด้วย fake store + PostgreSQL ชั่วคราว | 1 |
| 3 | เว็บ: `useLiveStream`, ผูก react-query, `StatusBar`, vitest (fake stream: ต่อ/หลุด/ค้าง/ต่อใหม่) | 2 |
| 4 | Note เปิดบน PC (`npm run dev` + API เดิม) และตรวจตามเกณฑ์ §5 | 3 |

ไม่มีขั้นบน VM และไม่มี migration; ไม่ต้อง restart worker

## 5. เกณฑ์ผ่าน

1. **ความเร็ว:** ใน test กับ PostgreSQL ชั่วคราว insert แถว `exec_trades` 50 แถวทีละแถว วัดจาก commit ถึง event `changed`
   ที่ client ได้รับ p95 ≤ 3 วินาที (คาดราว 1–1.5 วินาทีจากรอบ query 1 วินาที)
2. **fill จริง:** เมื่อ BOT1 หรือ DEFAULT มี fill บน Testnet ครั้งถัดไป เว็บบันทึกเวลาที่ event มาถึงเทียบ `received_at`
   (แสดงใน console ของ dev) เพื่อยืนยันกับของจริง; ข้อนี้รอตลาด ไม่บล็อกการ merge
3. **สายหลุด:** test ตัด stream กลางทาง → เว็บขึ้น "ค้าง" ภายใน 30 วินาที, poll กลับเป็นรอบเดิม, ต่อใหม่แล้ว refetch
   และตาราง orders/trades ตรงกับ REST ไม่มีแถวหายหรือซ้ำ
4. **สิทธิ์:** token ไม่มี/ผิด = 401; ผู้ใช้ที่ไม่มี role = 403; ไม่มีข้อมูลใน URL
5. **ภาระ DB:** เปิด 3 แท็บพร้อมกันยังเป็น 1 query ต่อวินาที; ปิดทุกแท็บแล้วหยุด query ภายใน 5 วินาที
6. test เดิมของ API และเว็บผ่านทั้งหมด; ตรวจแถบสถานะที่ 375 / 768 / 1440px

## 6. หลัง M1 (ไม่อยู่ในแผนนี้ ต้องให้ Note ตัดสินแยก)

- **ราคา Testnet ทุก 1–2 วินาที:** ให้ worker เขียน quote ล่าสุดแยกจาก heartbeat (แถวเล็กในตารางสถานะ)
  ต้องมี migration และแก้ worker ที่รันอยู่ จึงต้องประสานกับเธรดที่ดูแล worker และ restart บน VM
- ปุ่ม HALT ทั้งหมดบนแถบสถานะ และหน้ารายละเอียดบอท F2 พร้อม route จริง
- ถ้าวันหนึ่ง API ย้ายจาก PC ไปรันบน server ที่มีหลาย process ค่อยพิจารณา `LISTEN/NOTIFY` หรือ Supabase Realtime แทนการ query ทุกวินาที

## 7. ความเสี่ยง

| ความเสี่ยง | ผล | วิธีรับมือ |
| --- | --- | --- |
| stream ค้างแต่ TCP ไม่ปิด (proxy, sleep) | จอดูเหมือนสดแต่ไม่สด | `ping` ทุก 15 วินาที; ไม่ได้อะไรเกิน 30 วินาที = ค้าง และต่อใหม่ |
| query ทุกวินาทีกิน connection pool | endpoint อื่นช้า | watcher ยืม connection ทีละครั้งแล้วคืน; มีตัวเดียวต่อ process; หยุดเมื่อไม่มีผู้ฟัง |
| `uvicorn --reload` ตัด stream ตอนแก้โค้ด | เว็บขึ้นค้างชั่วครู่ | ต่อใหม่อัตโนมัติ; เป็นเรื่องปกติตอน dev |
| ราคา Testnet ช้ากว่า Mainnet ~10 วินาที | ส่วนต่างที่เห็นคลาดเคลื่อน | แสดงอายุข้อมูลคู่ราคาเสมอ; ข้อเสนอ §6 |
