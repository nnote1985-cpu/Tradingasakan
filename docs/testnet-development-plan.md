# Development Plan — Tradingasakan Binance Testnet Trading

**Version:** 1.2 — v1.1 ของ Note (17 ต.ค. 2026) + 6 ข้อแก้ใน §0.5  
**วันที่:** 17 ตุลาคม 2026 (Asia/Bangkok)  
**Repository:** https://github.com/nnote1985-cpu/Tradingasakan  
**Code baseline ที่ตรวจ:** bd98dd87443c97ffd7c8d98f8ea2fc99a3e45df9 (v1.1); ตรวจซ้ำที่ main dad4a65 (v1.2)  
**ขอบเขต:** ข้อมูลตลาด + Binance Testnet API + ยอดเงินทดสอบของ Exchange  
**สถานะ:** แผนพัฒนา ยังไม่ได้ implement, เชื่อมบัญชี หรือส่งคำสั่งจากการแก้เอกสารนี้

> ข้อกำหนดจากผู้ใช้: ใช้เงินทดสอบ และส่งคำสั่งไป Binance Testnet จริง  
> Binance Testnet จัดการ Order/Fill/Balance; โปรแกรมเราตัดสินใจ ตรวจความเสี่ยง ส่งคำสั่งและบันทึกผล ไม่มีการส่งคำสั่งซื้อขายไป Mainnet หรือใช้เงินจริง

## 0. เป้าหมายและวิธีใช้แผน

พัฒนาจาก archive-based paper books ไปสู่ระบบที่ Backend ทำงานต่อเนื่อง รับข้อมูลตลาด วิเคราะห์ด้วย Code/AI แล้วส่งคำสั่งซื้อขายบน Binance Testnet พร้อมติดตามผลและ reconcile Order/Fill/Balance เข้าสู่ Supabase

ใช้ร่วมกับ BUILDPLAN v4.2 โดยเอกสารนี้กำหนดงานถัดไป เริ่ม Spot Test Network ก่อน Futures test environment เป็น phase แยก ส่วน Shadow simulator ยังคงเป็น reference สำหรับวิจัยบนตลาดจริง ไม่ใช่ปลายทางซื้อขายหลักของงานนี้

### 0.1 ระดับของข้อกำหนด

- **LOCKED:** execution เฉพาะ Binance Testnet, ไม่มี Mainnet trading key/route, Backend ทำงานอิสระจาก Browser
- **DEFAULT:** ค่าเริ่มต้นเปลี่ยนได้ก่อนล็อก experiment และต้อง version
- **DISCOVERY:** ตรวจ API/สิทธิ์/Rate limit/account/endpoint จาก environment จริงก่อนใช้ ห้ามเดา
- **EXAMPLE:** ตัวอย่าง config/logic ไม่ใช่ผลการทดสอบหรือคำรับรองกำไร

### 0.2 ผลลัพธ์ที่ต้องได้

1. ปิด Browser แล้ว Market/Strategy/Execution/Guardian workers ยังทำงาน
2. ส่งคำสั่งไป Binance Spot Testnet และเห็น order ID, execution/fill IDs และยอดเงินทดสอบเปลี่ยนจริง
3. Supabase บันทึกคำตัดสิน intent, order events, fills, commissions, account reconciliation และ audit ตามข้อมูล Exchange
4. ส่งซ้ำ/restart/timeout ไม่ทำให้เปิดสถานะซ้ำหรือบันทึกบัญชีซ้ำ
5. แยก signal data, Testnet executable quotes, Exchange timestamps และเวลารับข้อมูลครบ
6. มี Testnet account epoch และวิธีรับมือการ reset โดยไม่ทำลายประวัติ
7. มี MAINNET_EXECUTION_DISABLED guard ที่ทดสอบได้; optional Mainnet public data เป็น read-only client แยก

### 0.3 สิ่งที่รวมและไม่รวม

**รวม:** Testnet credentials ที่ผู้ใช้ตั้งใน secret store, signed Testnet trading/account calls, private user-data stream, order lifecycle, risk reservations, recovery, Guardian และ Dashboard

**ไม่รวม:** Mainnet orders, เงินจริง, ถอน/ฝาก/โอนสินทรัพย์จริง, auto-enable Mainnet, ใช้ Futures/Options key กับ Spot, naked option selling หรือเปิดทุกผลิตภัณฑ์พร้อมกัน

Shadow/local simulator ใช้ unit tests และ research reference ได้ แต่ไม่ใช้ simulated fill แทนผล Testnet และการผ่าน validation endpoint อย่างเดียวไม่ถือว่าต่อ Exchange สำเร็จ

### 0.4 การแก้จาก v1.0

| เดิม | ฉบับแก้ |
| --- | --- |
| คำสั่งจำลองภายในโปรแกรมเท่านั้น | Testnet Execution Gateway ส่งคำสั่งให้ Binance จัดการ |
| ไม่ใช้ Private API | Testnet signed account/trading API และ private stream |
| คำนวณ fill/fee เอง | ใช้ fills/commissions/balances ที่ Testnet ส่งกลับ |
| จำนวนเงินเริ่มต้นกำหนดใน simulator | อ่าน virtual balances จริงและจัดสรร test budget ไม่เกิน free balance |
| ไม่มี reset handling | account epoch, reset investigation, separate trial segment |
| ไม่มี external-order failure cases | UNKNOWN/reconcile/partial fill/cancel race/reservation tests |

### 0.5 การแก้จาก v1.1 (v1.2)

| ข้อ | สิ่งที่แก้ | ส่วนที่แก้ |
| --- | --- | --- |
| 1 | แผนนี้เป็นแผนหลักของ Phase 4; แผนเดิม docs/phase-4-execution-plan.md (PR #30) และโค้ด PR #31/#32 เป็นส่วนหนึ่งของ P1–P4 และค่า DEFAULT ที่ขัดกันตัดสินไว้แล้ว | §10.2 |
| 2 | Host เป็น decision ของ Note ใน P0: cloud ของเราได้ 451 จาก Binance และ PC ของ Note ไม่ได้เปิดตลอด | §2.2 |
| 3 | เพิ่มการแจ้งเตือนออกนอกระบบ (Telegram) และ uptime monitor ภายนอก | §2.3, P1, P5 |
| 4 | เพิ่ม conformance profile ที่ส่งคำสั่งเล็กตามตาราง เพราะกลยุทธ์รายวันส่งคำสั่งน้อยมาก | §5.4, P4 |
| 5 | กลยุทธ์ Testnet ตัวแรกเป็น R10a (tn-r10a-code-v1) แทน pathA | §5.1, §5.3 |
| 6 | แผนนี้รับมอบที่ P0–P5; P6–P11 ย้ายเป็นแผนถัดไป และ Futures ใส่ข้อกำหนดจากผลวัด intraday risk | §10, §10.3 |

ค่า AI/ข่าวและ Hosting อาจเป็นเงินจริง แม้สินทรัพย์บน Testnet ไม่มีมูลค่าจริง การสร้างเอกสารนี้ไม่ซื้อบริการ ไม่สร้าง key และไม่ส่ง order

## 1. สถานะโค้ดที่ตรวจแล้ว

| ส่วน | สิ่งที่มีใน baseline | สิ่งที่ต้องพัฒนาหรือยืนยัน |
| --- | --- | --- |
| Local launcher | npm run dev เปิด Vite + FastAPI | ยังไม่ได้เปิด Scheduler/Trading worker |
| API | Auth, overview, equity, risk, backtests, AI/strategy views | เพิ่ม ingestion/worker/data-quality views |
| Browser live ticker | รับ Binance miniTicker เพื่อแสดงราคา | ไม่ได้เป็นข้อมูลป้อนการตัดสินใจ ต้องทำ Backend feed |
| Data | Archive downloader/checksum, data/collector/point_in_time/raw_store/snapshot modules | ตรวจ interface จริงแล้ว reuse; ยังไม่ถือว่ามี exchange streaming ingestion ครบ |
| pathA-v1.2 | Decimal Spot book, SMA100 × Funding, daily marks, limits | เชื่อม feed สดผ่าน engine adapter ใหม่ |
| pathA-p5-r1 | AI กรองการเพิ่มน้ำหนัก; ไม่กรอง deterministic reductions | ทำ fresh snapshots และ Testnet execution mapping ใน experiment ใหม่ |
| pathA-p5-r2 | AI เสนอน้ำหนัก 0 / 0.5 / 1 โดยไม่เห็น Code suggested weight | รองรับข้อมูลบริบทใหม่เป็น revision แยก |
| R10a / R11 | Band ±2% รอบ SMA100, มี book/runner | เปิดเป็น Shadow paper books ตั้งแต่รอบ 2026-10-17 (PR #40, frozen bd98dd8); R10a ยืนยันบน C1 แล้ว (Sharpe 1.93 vs pathA 1.76) |
| Phase 4 execution | แผน PR #30, order core PR #31, exec store PR #32 (ทั้งหมด draft); migration trading.exec_* apply แล้วเป็น 20261016144036 | เป็นส่วนของ P1–P4 ตาม §10.2 |
| Daily job | Claude scheduled task ทุกวัน 08:20 UTC รัน frozen paper books, audit, sync, Telegram (อยู่นอก repo) | ไม่ใช่ worker ต่อเนื่อง; คงไว้เป็น Shadow reference |
| R7 | Perpetual Long/Short และ virtual funding | เพิ่ม Binance Futures test adapter ตามหลัง Spot; อย่าใช้ Spot endpoints |
| Strategy library | 13 กฎและ draft profiles | ไม่ใช่ 13 กลยุทธ์ที่ deploy/trade พร้อมกัน |
| Persistence | พอร์ตเดิมใช้ JSON และ mirror ไป trading schema | พอร์ต online ใหม่ให้ transactional DB เป็น authority ตาม §7 |
| LIVE guard | LIVE_SUPPORTED_IN_BUILD=False | คง Mainnet LIVE ปิด; เพิ่ม Testnet adapter โดยไม่ปลด LIVE guard |

Repository ไม่มี Scheduler ในโค้ด: งานรายวันปัจจุบันคือ Claude scheduled task ใน cloud ซึ่งเข้าถึงได้เฉพาะ data.binance.vision (REST/WS ของ Binance ได้ 451) จึงห้ามอ้างว่า worker รันทุกกี่นาทีหรืออยู่บน Host ใดแล้ว ต้องมี deployment record และ heartbeat มายืนยัน

## 2. Architecture ที่เสนอ

| Process / module | หน้าที่ | สิทธิ์ |
| --- | --- | --- |
| Market Worker | Public WS/REST, candles, quotes, gap/sequence checks | Testnet market data; optional Mainnet read-only data client แยก |
| Context Worker | ข่าว/calendar และ provenance | Read-only provider API |
| Strategy Worker | deterministic features และ proposals | ไม่มี Exchange signing key |
| Analysis Worker | existing AI filter/proposer และ future specialists | LLM key + immutable snapshot; ไม่มี order tool |
| Testnet Execution Gateway | sign/submit/query/cancel orders, reservations/outbox, reconcile | Testnet-only trading/account key |
| Account Stream Worker | order/fill/balance events + reconnect/resync | Testnet user-data permission |
| Guardian | freshness, inventory/protection/risk, emergency reduction | ส่ง scoped reduction ผ่าน Testnet safety path; ไม่รอ AI |
| FastAPI / React | authorized view/control/trace | Browser ไม่มี trading/LLM/server secret |

เริ่ม modular Python application บน supervised processes ไม่เพิ่ม microservice ต่อ Agent Gateway เป็น normal order writer เพียงตัวเดียวต่อ Testnet account/product; Guardian แยก process แต่ใช้ safety epoch/fencing และ protocol ร่วมกันตาม §8

### 2.1 Runtime modes

| Mode | ส่งคำสั่งที่ไหน | ยอดและผล Fill |
| --- | --- | --- |
| SHADOW | ไม่ส่ง Exchange order | simulated ledger แยกสำหรับวิจัย |
| TESTNET | Binance test endpoint ที่ล็อกไว้ | Testnet execution/balance + reconciled local ledger |
| LIVE | ไม่รองรับใน release นี้ | startup reject |

DEFAULT startup ใช้ SHADOW เพื่อพัฒนา; เมื่อมี Testnet key/account และ capability checks พร้อม จึงตั้ง TRADING_MODE=TESTNET พร้อม TESTNET_EXECUTION_ENABLED=true สำหรับทดลอง end-to-end ไม่มีการเปลี่ยน env แล้วเปิด LIVE ได้

### 2.2 Deployment

- Developer mode: เริ่ม web/API/workers ด้วย documented commands; npm run dev ปัจจุบันยังเปิดเฉพาะ web/API
- Continuous Testnet mode: VM/container host ที่เปิดต่อเนื่อง มี restart policy, clock sync, leases, heartbeat และ logs
- Streaming loops อยู่ Worker; Scheduler ใช้ polling ข่าว/backfill/reconcile/maintenance ไม่ใช้ Browser หรือ Edge Function เป็น long-running trading loop
- API worker scaling/multiple browser tabs ต้องไม่สร้าง order writer เพิ่มโดยอัตโนมัติ
- Key อยู่เฉพาะ Gateway/Account worker ที่ต้องใช้ ห้ามใส่ใน VITE_* หรือส่งใน Chat/logs
- **Host (decision ของ Note ใน P0):** Claude cloud ใช้ไม่ได้ (HTTP 451 จาก Binance) และ PC ของ Note ไม่ได้เปิดตลอด ตัวเลือก: (ก) PC ของ Note สำหรับ P1–P4 ที่ไม่ต้องเปิดตลอด แล้ว VPS ก่อน soak P5 (ข) VPS ตั้งแต่ P1 ต้องอยู่ในประเทศที่ Binance อนุญาต ตรวจจาก Host จริงว่า testnet.binance.vision และ stream เข้าได้ บันทึกประเทศ/ผู้ให้บริการ/งบต่อเดือน/clock sync (NTP) ใน ADR P0
- Process manager รีสตาร์ท worker เองเมื่อล่ม (systemd หรือ container restart policy) และเริ่มทุกครั้งผ่าน RECOVERING ตาม §8.3

### 2.3 การแจ้งเตือนออกนอกระบบ

- ใช้ Telegram ผ่าน scripts/telegram_notify.py ที่มีแล้ว (token ใน environment ของ Host; mask ใน log/error)
- **critical** (ส่งทันที): HALTING/HALTED, UNKNOWN order ค้างเกิน 30 วินาที, reconcile discrepancy, RESET_SUSPECTED, credential error
- **warning**: feed/account stream เงียบเกิน freshness, worker restart
- **info**: Testnet fills และสรุปรายวัน 07:05 น. เวลาไทย (หลังแท่งรายวันปิด)
- Dead-man switch ภายนอก: worker ping บริการ uptime monitor ภายนอก Host ทุก 1 นาที ถ้าไม่มี ping 5 นาที บริการนั้นแจ้ง Telegram เอง เพราะถ้า Host ดับทั้งเครื่อง heartbeat ใน DB จะไม่มีใครเห็น
- ช่องแจ้งเตือนล่มต้องไม่หยุด Guardian; บันทึก alert ที่ส่งไม่ได้ไว้ส่งซ้ำ

## 3. Data providers และ Binance Testnet

### 3.1 เลือก environment ให้ชัด

งานแรกใช้ **Binance Spot Test Network** ไม่ใช้คำว่า Testnet/Demo สลับกันโดยไม่ตรวจความเข้ากันของ key/API Binance มี Demo Trading อีกผลิตภัณฑ์หนึ่ง หากต้องเปลี่ยนไป Demo ต้องมี ADR และ capability test ใหม่

จุดเชื่อมต่อ Spot ที่ตรวจจาก official docs ณ 17 ต.ค. 2026:

| Purpose | Endpoint / contract |
| --- | --- |
| Spot Testnet REST | https://testnet.binance.vision/api |
| Signed order create | POST /api/v3/order บน Testnet host |
| Order validation เท่านั้น | POST /api/v3/order/test; ไม่เข้า matching engine และไม่มี fill |
| Query / cancel | GET /api/v3/order, DELETE /api/v3/order ตาม capabilities ปัจจุบัน |
| Testnet WebSocket API | wss://ws-api.testnet.binance.vision/ws-api/v3 |
| Testnet market streams | wss://stream.testnet.binance.vision/stream; ตรวจ subscription syntax กับ docs ตอน implement |
| Account/order/trade history | Testnet account/open-orders/my-trades APIs + supported user-data stream |

ให้ implementation ตรวจ official docs ปัจจุบันอีกครั้ง โดยเฉพาะ auth/user-data subscription อย่าคัดลอก listen-key lifecycle รุ่นเก่าโดยไม่ตรวจ Spot Testnet ไม่รองรับ /sapi; ไม่ใช้ wallet/withdraw routes

Endpoint ของ USDⓈ-M Futures test environment ที่ docs ระบุคือ https://demo-fapi.binance.com และ wss://demo-fstream.binance.com; **ยังไม่ activate ใน Spot phase** ต้องมี Futures account/key/client/capabilities แยกก่อนใช้

### 3.2 Endpoint และ key isolation

1. Execution client รับ environment enum ไม่รับ arbitrary base URL จาก Browser/Agent
2. Spot signed/mutating calls allowlist เฉพาะ testnet.binance.vision ที่ตั้งไว้; ปิด redirects ไป host อื่น
3. Mainnet order routes ถูก block แม้ env เป็น production หรือมี key อยู่ผิดที่; ไม่มี Mainnet trading credentials ใน deployment นี้
4. Optional Mainnet public signal client ไม่มี auth/signing capability และเปิดเฉพาะ documented public read-only endpoints ไม่แชร์ signing middleware กับ Testnet Gateway
5. Validate key provenance จาก Testnet portal/setup record และทดสอบ Testnet auth จริง ไม่เดาว่า key เป็น Testnet จากรูปแบบตัวอักษร
6. HTTP 451/access restriction เป็น blocker ของ connection นั้น ไม่ใช้ Browser/proxy เป็นทางเลี่ยง; ใช้ environment/service ที่มีสิทธิ์เข้าถึงหรือคง SHADOW พร้อมสถานะ BLOCKED
7. ไม่ตั้ง provider fallback ไป Exchange อื่นสำหรับ execution โดยเงียบ ๆ; งานนี้ล็อก Binance Testnet

### 3.3 ข้อมูลสัญญาณกับข้อมูล Testnet

| Data plane | ใช้อย่างไร |
| --- | --- |
| Signal plane | Mainnet public candles/funding ตาม existing pathA source policy หากเข้าถึงได้; pin venue/product/source |
| Execution plane | Testnet quotes/order book/instrument filters/account state สำหรับ quantity/price/risk และส่งคำสั่ง |
| Result plane | Testnet fills, fees, balances และ funding เมื่อเปิด Futures test phase |
| Research reference | Mainnet-data Shadow portfolio; ไม่เอา Testnet liquidity/P&L มาแทนผลตลาดจริง |

Mainnet กับ Testnet ราคา/สภาพคล่องอาจต่างกัน ให้บันทึกทั้งสองพร้อม divergence guard; DEFAULT block new entry หากราคา comparable instrument ต่างเกิน 1% เป็น engineering threshold ที่ต้องทบทวนก่อน lock trial ไม่แปลงราคาข้าม product ด้วยการสมมติ

หาก Testnet history ไม่ครบ warmup ห้ามเปลี่ยน source เป็น Mainnet/venue อื่นอย่างเงียบ ๆ เลือก source policy ล่วงหน้า หากใช้ Testnet-only candles ให้เป็น conformance profile แยกจาก pathA research และไม่อ้างว่าเป็นการทดลองสัญญาณเดิม

ทุก record มี environment/product/instrument/account/epoch scope ชัดเจน ส่วน clientOrderId ต้องผูกกับ intent เดิม ไม่ใช้ symbol อย่างเดียวเป็น identity

### 3.4 Sources เสริม

- ข่าว: CryptoPanic หรือ licensed provider หนึ่งราย; ตรวจ fields/rights/cost/latency ก่อนใช้
- Calendar: Trading Economics หรือ provider ที่ตรวจสิทธิ์แล้ว
- Mainnet funding/OI: source ที่เข้าถึงได้ตามสิทธิ์; แยก actual settled / predicted / premium-index proxy
- Options: data research/simulator ก่อน; Binance Options test/demo capability ยัง UNVERIFIED ห้ามสมมติว่า Spot/Futures key หรือ endpoint ใช้ได้
- Deribit/Bybit ใช้เป็นข้อมูล research เพิ่มได้เมื่อมี ADR; ไม่เป็น execution fallback ของแผนนี้

### 3.5 Funding และ initial test balances

Spot ไม่จ่าย Perp funding เพียงเพราะใช้ funding เป็นสัญญาณ Funding threshold/source/proxy policy เดิมต้อง version และ freeze snapshot; data ที่เผยแพร่ภายหลังไม่ rewrite คำตัดสิน

เมื่อเปิด Futures test phase บันทึก funding/commission ที่ test account ถูกหักหรือได้รับจริง ไม่แทนด้วย Mainnet rate; Mainnet Shadow แยกใช้ funding model ของมัน

อ่าน Testnet free/locked assets จริงก่อนเริ่ม และบันทึก opening balance journal + account epoch; ไม่สร้างยอด 10,000 ขึ้นเองใน Testnet ledger ค่า 10,000 USDT เป็น DEFAULT allocation เมื่อมี free test balance เพียงพอ และต้องกัน virtual funds ของ open orders/fees ก่อน

ใช้ account scope เฉพาะบอตหรือ inventory allocation ที่ตรวจได้ ห้ามขาย test assets ที่อยู่นอก allocation โดยไม่ระบุ คำว่า 100% BTC หมายถึง 100% ของ strategy allocation ไม่ใช่ขายทุกเหรียญที่ faucet แจก

ผู้ใช้ได้ test funds จาก Testnet ตามระบบของ Binance แต่ต้องอ่านยอดจริงทุกครั้งที่เริ่ม epoch ใหม่; การรีเซ็ตไม่ใช่ผลกำไรหรือการฝากเงินจริง

## 4. ความถี่และ clock

ทุกค่าด้านล่างเป็น DEFAULT สำหรับ BTC spot/perp แบบไม่ใช่ HFT ต้องวัดและปรับก่อนล็อก trial ไม่มีการเรียก API ทุกประเภททุกวินาที

| งาน | DEFAULT cadence | การเก็บ/ใช้งาน |
| --- | --- | --- |
| Trades / best bid-ask | WebSocket ต่อเนื่อง | latest state ใน Worker; evidence ช่วง decision/fill เก็บถาวร |
| Order book | WS snapshot/delta ตาม provider | ประกอบ local book; snapshot/sequence mismatch ทำให้ใช้ไม่ได้จน resync |
| Candle base | เก็บแท่งปิด 1m; stream รองรับ live updates | สร้าง 1h/4h/1d จากช่วงที่ครบ |
| Feature calculation | เมื่อแท่งของ timeframe ปิด | ไม่คำนวณใหม่ทุก Tick และไม่ใช้แท่งยังไม่ปิดแทนแท่งปิด |
| Existing daily strategy | เมื่อ valid 1d close พร้อม | วันละครั้ง; UTC day boundary คือ 07:00 ไทย ไม่ใช่ยืนยันเวลาที่ task ปัจจุบันรัน |
| Future intraday strategy | valid 1h/4h close ตาม profile | experiment แยก ไม่เปลี่ยน daily rules กลางทาง |
| Funding/OI REST หากไม่มี stream | เริ่ม poll 5 นาทีตาม provider resolution/rate limit | เก็บ timestamp จริง; poll บ่อยไม่ได้ทำให้ข้อมูลมี resolution สูงขึ้น |
| ข่าว | poll ทุก 5 นาที | dedup ก่อน LLM; หาก SLA ไม่พอสำหรับกลยุทธ์ต้องเปลี่ยน service/profile |
| Calendar schedule | refresh ทุก 6 ชั่วโมง; ตั้ง event timers จากเวลาที่ได้ | ใกล้ประกาศใช้ stream/short polling ตามแพ็กเกจเมื่อจำเป็น |
| Account user stream | ต่อเนื่องตาม Binance supported subscription | order/fill/balance ไม่รอรอบ UI |
| Order/account reconcile | DEFAULT ทุก 30 วินาที; ทันทีหลัง reconnect/UNKNOWN/startup | ใช้ Testnet REST, capped rate limit/backoff |
| Guardian / health | ทุก market/account event + heartbeat 2 วินาที | ไม่เรียก AI; account freshness และ safety epoch |
| Latest display projection | batch ทุก 5 วินาที | ข้อมูลล่าสุดให้ UI; ไม่ใช่ ledger authority |
| UI polling | overview 10 วินาที; health/risk 5 วินาทีเมื่อ active | SSE เพิ่มได้ภายหลัง; ไม่มีผลต่อ Worker cadence |
| Reconnect / backfill | ตาม gap และ capped backoff | reconcile market state ก่อนเปิดรับสัญญาณใหม่ |

### 4.1 เวลาที่ต้องบันทึก

source_event_at, bar_open_at, bar_close_at, provider_published_at (ถ้ามี), received_at, available_at, feature_ready_at, decision_started_at, decision_completed_at, intent_created_at, submit_started_at, exchange_order_at, exchange_fill_at, account_event_received_at, reconciled_at, recorded_at

available_at คือเวลาที่ระบบสามารถใช้ข้อมูลที่ normalize/validate แล้ว ไม่ใช่เวลาเริ่ม HTTP request ห้ามบันทึกว่าข่าว/แท่งราคาพร้อมก่อนรับข้อมูลจริง

### 4.2 Freshness profile

DEFAULT สำหรับ BTC Testnet: latest executable bid/ask อายุไม่เกิน 5 วินาทีก่อนส่ง entry, fresh account state พร้อม reservation และ healthy stream/reconcile; health warning เมื่อ market feed เงียบเกิน 10 วินาที เป็นค่าที่ต้องวัดกับ feed จริง ผล Fill เชื่อจาก Exchange แม้มาถึงล่าช้า ไม่ reject Fill ที่เกิดจริงเพราะ local quote stale

ข้อกำหนด news/calendar/funding มี TTL ตาม role และความถี่ต้นทาง ไม่ใช้ TTL 5 วินาทีครอบทุกประเภท Clock skew / negative latency / future timestamps เป็น incident ไม่แก้ด้วยการเชื่อ timestamp ใด timestamp หนึ่งทันที

### 4.3 Bootstrap และ gap recovery

เริ่มระบบด้วย historical closed bars ให้ครบ warmup ของแต่ละ feature เช่น SMA100 ต้องมีอย่างน้อย 100 daily closes; ไม่ต้องรอรับสด 100 วันและไม่ต้องโหลด 100 วันเป็น 1m ทั้งหมด หากโหลด native daily/4h bars ต้องตรวจ timezone/close boundary/source และ record aggregation lineage ให้ตรงกับวิธีสร้างแท่งสด

Backfill สร้าง indicator state ได้ แต่ไม่ย้อนออกคำตัดสินใหม่เพื่อเทรดอดีต เปิดการตัดสินใจออนไลน์หลัง warmup/freshness/cursors พร้อมแล้ว และ freeze Snapshot ที่ใช้ตัดสินใจแม้ provider แก้ไขประวัติภายหลัง

## 5. Features และกลยุทธ์

### 5.1 ทำ daily baseline ก่อน

พอร์ตใหม่เริ่ม BTC Spot บน Testnet ไม่มี margin/short/borrowing; DEFAULT strategy allocation 10,000 USDT เมื่อ free test balance เพียงพอ ย้าย daily pathA ไปอ่าน validated realtime-fed snapshots ก่อน ยังไม่เพิ่ม Indicator ทุกตัวพร้อมกัน

- Trend = daily Perp close > SMA100
- Funding factor = 0.5 เมื่อค่าเฉลี่ย daily funding 7 วัน > 0.0009 ต่อวัน; นอกนั้น 1
- Target BTC weight = trend × funding factor → 0 / 0.5 / 1
- กฎเดิมไม่ rebalance ทุกวันหาก target ไม่เปลี่ยน ต้องเก็บ semantics นี้ไว้ใน baseline
- daily price drawdown/volatility ที่คำนวณเพื่อแจ้งเตือนไม่ใช่ weight filter โดยอัตโนมัติ
- กลยุทธ์ Testnet ตัวแรกคือ **R10a** (ชื่อ tn-r10a-code-v1): pathA ข้างต้นแต่ Trend เป็น band latch ±2% รอบ SMA100 (เข้าเมื่อ close > SMA100×1.02 ออกเมื่อ < SMA100×0.98) ตาม trading.strategies.sma_band ที่ frozen bd98dd8 ไม่เปลี่ยนพารามิเตอร์
- เหตุผล: R10a ยืนยันบน C1 แล้วดีกว่า pathA (Sharpe 1.93 vs 1.76, DD 23.2% vs 27.4%) และเทรดน้อยกว่า (48 vs 82 trades ใน 2022–2025) จึงเป็นตัวที่ใกล้ live ที่สุด; Spot account เดียวรันได้ทีละกลยุทธ์ (§12.2) pathA และตัวอื่นคงอยู่ใน Shadow
- ใช้ชื่อใหม่ tn-r10a-code-v1 เพื่อไม่ปนกับผล archive-based pathA-r10a

### 5.2 Features ที่เพิ่มเพื่อวิเคราะห์ก่อนเปลี่ยนกลยุทธ์

| กลุ่ม | Features เริ่มต้น | ประโยชน์ / ขอบเขต |
| --- | --- | --- |
| Trend/regime | SMA/EMA และ slope, ระยะห่างเส้น, ADX | แยกแนวโน้ม/ช่วงแกว่ง; เริ่มจากชุดเล็ก |
| Volatility | ATR, realized volatility | ระยะเคลื่อนไหวและ risk context ไม่ใช่ทำนายทิศทางโดยลำพัง |
| Volume | volume / rolling median, breakout participation | แยกแรงซื้อขายจากราคาที่ขยับแต่ volume ต่ำ |
| Liquidity | spread bps, depth ภายในช่วงราคา, estimated slippage | เน้นคุณภาพ Fill; ไม่สมมติว่าทำนายหลายวันได้ |
| Derivatives | actual funding / predicted funding / OI change / basis | บริบท leverage/crowding ต้องแยกข้อมูลจริงกับการตีความ |
| News/macro | asset relevance, source credibility, event schedule | contextual evidence; ไม่ใช้ sentiment คะแนนเดียวสั่งซื้อทันที |

Features จาก OHLCV คำนวณใน Code ด้วย definitions/warmup/version ที่ล็อก ไม่ต่อ API ต่อ Indicator ตัวใหม่ทุกตัว ค่า Threshold และสูตรที่ยังไม่มีใน Repo ต้องเลือกก่อนเปิด experiment ไม่ใช่ให้ LLM คิดค่าปัจจุบันเอง

### 5.3 Candidate strategy ตัวถัดไป

หลัง daily baseline ผ่าน integrity จึงสร้าง daily-context challenger หรือ intraday challenger อย่างใดอย่างหนึ่งก่อน:

- ตัวอย่างสมมติฐาน: trend signal + regime filter ลดการซื้อขายในตลาดแกว่งได้คุ้ม missed gains หรือไม่
- ตัวอย่าง intraday: 1h setup โดยใช้ 4h regime เป็นบริบท
- Volume/ATR/ADX thresholds ต้องมี rationale, development partition, trial registry และ forward evaluation ของตนเอง
- Testnet order book guard เป็น execution condition; การเพิ่ม Mainnet directional order-book alpha เป็น hypothesis ใหม่
- R10a เป็น Testnet baseline แล้ว (§5.1); pathA/R11 เป็น Shadow reference เมื่อ pin code/config/source
- Strategy library profiles ยังไม่ auto-activate หรือให้ AI เลือกชุดใหม่โดยไม่ได้ version

### 5.4 Conformance profile (tn-conformance-v1)

R10a ประมาณ 13 trades ต่อปี soak 7 วันจึงอาจไม่มีคำสั่งจากกลยุทธ์เลย จึงมี profile แยกที่ไม่ใช่กลยุทธ์ ใช้ทดสอบ lifecycle จริงบน Testnet:

- ตามตาราง เช่น ทุก 4 ชั่วโมง: MARKET BUY ขนาด min notional จริงจาก exchangeInfo แล้ว MARKET SELL คืน; LIMIT ห่างราคา 5% แล้ว cancel; LIMIT ใกล้ราคาเพื่อลุ้น partial fill
- allocation แยกและเล็ก (DEFAULT 200 USDT) ใช้ account scope เดียวกับ strategy ไม่ได้ถ้าไม่มี inventory แยกตรวจได้; DEFAULT รันใน P4 ก่อนเปิดกลยุทธ์ และหยุดก่อนเริ่ม P5 soak (หรือรันคู่กันเฉพาะเมื่อมี Testnet account ที่สอง)
- ผลใช้ตรวจ lifecycle/ledger/reconcile เท่านั้น ไม่ใช่ผลกลยุทธ์ ไม่แสดงรวมกับ P&L

## 6. Events, triggers และ AI

### 6.1 Event types และ action

| Event | Code action | AI action |
| --- | --- | --- |
| CLOSED_BAR_READY | update features; ตรวจ one decision per strategy/bar | เรียกตาม analysis policy เมื่อ eligible |
| CANDIDATE_CREATED | preliminary risk/freshness/position checks | FILTER_ONLY เมื่อเพิ่ม exposure และ policy ระบุ |
| SCHEDULED_PROPOSAL | ตรวจข้อมูลและ portfolio eligibility | BOUNDED_PROPOSAL ตามรอบ เช่น daily; ไม่ต้องรอ Code เห็นด้วย |
| RELEVANT_NEWS_AVAILABLE | dedup/source/time/relevance check | default บันทึกบริบท; immediate reassessment เปิดเฉพาะ profile ที่กำหนด |
| MACRO_EVENT_WINDOW | ใช้ deterministic blackout ถ้ากำหนดใน strategy | ไม่ต้องเรียก LLM เพื่อทำ if-statement เดียวกัน |
| LIQUIDITY_GUARD_FAILED | block/defer Testnet entry ตาม TTL | ไม่เรียกเพื่อขอข้าม guard |
| RISK_LIMIT_BREACHED | Guardian cancel entries/reduce Testnet exposure ตาม policy | ไม่รอ AI และไม่ให้ override |
| STALE_DATA / GAP / CLOCK_ERROR | pause entries, resync, incident | ไม่ใช้ LLM แต่งข้อมูลที่ขาด |

News ใหม่ไม่เท่ากับคำสั่งใหม่ทุกข่าว สำหรับ release แรก ข่าวเข้าคลังบริบทและ Snapshot ของการตัดสินใจครั้งถัดไปก่อน; off-cycle news trading ต้อง experiment แยก

### 6.2 Pipeline ส่งคำสั่งไป Binance Testnet

1. Validate signal data → immutable snapshot/evidence manifest
2. Strategy event หรือ scheduled AI proposal → stable candidate/request ID
3. Preliminary eligibility: data/account/stream health, warmup, not HALT, no incompatible pending/UNKNOWN order
4. Call AI ตาม policy; no signing key/tools ใน Agent
5. Validate schema/IDs/evidence/capability/deadline และบันทึก errors
6. อ่าน Testnet executable quote/filters/free-locked balances ใหม่ คำนวณ quantity/fees budget ด้วย Code
7. Transaction เดียวสร้าง immutable intent + risk/balance reservation + outbox; ไม่มี network call ภายใน DB transaction
8. Gateway ตรวจ writer lease, epoch, TTL และ endpoint guard อีกครั้ง แล้ว sign/submit Testnet order
9. Account stream/REST query รับ order/fill/commission → idempotent ledger/projection/reconciliation
10. Dashboard แสดง intent/ACK/partial/filled/unknown ตามจริง; AI ALLOW หรือ HTTP success ไม่แปลว่า Fill แล้ว

ทุกขั้นใช้ Testnet funds เท่านั้น Mainnet public data (ถ้าเปิด) เป็นข้อมูลอ่าน ไม่มี Mainnet order capability

### 6.3 AI modes และการใช้ตัวเดิม

| Mode | Behavior |
| --- | --- |
| OFF | Code only |
| FILTER_ONLY | reuse pathA_filter/paired_book semantics: กรองการเพิ่ม weight; ลด weight/HALT ตาม Code |
| BOUNDED_PROPOSAL | reuse pathA_proposer/compiler: target 0/0.5/1; ไม่เห็น Code suggested weight; failure คง target เดิม |
| MULTI_ANALYST_FILTER | เปิดภายหลังด้วย role schemas + synthesis; ไม่ใช้ majority vote เป็น default |

เริ่ม Testnet lifecycle/conformance ด้วย Code-only ก่อน จากนั้นต่อ r1/r2 เป็นคนละ experiment โดยแยก test accounts หรือรันตามลำดับตาม §12.2 ไม่บังคับหลาย Agent เพื่อเริ่ม Testnet

Spec ของ graph enabled/model ใน Repo ไม่ใช่หลักฐานว่า provider/model ID ใช้งานได้จาก environment ปัจจุบัน ต้องผ่าน mock และ actual provider compatibility smoke ก่อนเปิด paid call โดยใช้ model/prompt/effort/schema ที่ pin แล้ว

DEFAULT ต่อคำถาม: 1 attempt, no silent fallback, max deadline 120 วินาที และต้องไม่เกิน signal valid_until; daily strategy ใช้ total signal TTL เริ่มต้น 5 นาทีหลัง decision event ready ส่วน intraday ต้องมี profile TTL แยกก่อนเปิด

เมื่อ budget ไม่พร้อม → ERROR/ABSTAIN สำหรับ filter หรือ no-change สำหรับ proposer ห้ามข้ามไป Code target เงียบ ๆ; timeout/invalid ไม่ให้เครดิตว่าเป็น prediction

### 6.4 Dedup / cooldown / budget

- Unique strategy decision key = experiment_id + strategy_version + instrument_id + closed_bar_id + decision_purpose
- News dedup ใช้ provider ID + canonical URL/content hash + source lineage ไม่ใช่ headline อย่างเดียว
- Quote ticks ไม่สร้าง AI call; one active analysis task per proposal
- Repeated trigger ภายใน event เดียว reuse task เดิม; retry policy ไม่สุ่มถามใหม่จนได้คำตอบที่ต้องการ
- Graph-level token/run/daily/monthly caps ต้องตั้งและ validate ก่อน enable; ปิด real LLM call จน cost fields ครบ
- ตั้ง daily cap ให้ครอบคลุมชุด role/synthesis ทั้งหมด; ใช้ existing cost tracking เป็นฐานและแสดง measured vs estimated cost ชัดเจน
- ค่า actual billed cost ของ failed/late calls ต้องถูกเก็บด้วย หากไม่ทราบให้ UNKNOWN ไม่ใส่ศูนย์

### 6.5 Specialist Agents หลัง single-agent พร้อม

Regime / Setup / News / Derivatives-Liquidity → validated reports → Synthesis → Decision Policy

Agent รอบแรกวิเคราะห์แยกกัน; แชร์ immutable context/time cutoff เดียวกัน มี required/optional roles, provenance, bounded concurrency และ budget เมื่อ required role หาย → ABSTAIN; optional missing ต้องแสดง partial coverage และอนุญาตไว้ก่อนเท่านั้น

ห้ามเปิด reviewer debate/recursive tool loop โดยไม่มี max rounds/deadline และ experiment ใหม่; เริ่ม 2–3 บทบาทที่มีข้อมูลจริงก่อน ไม่สร้าง role ที่ไม่มี evidence ให้ใช้

## 7. Supabase และ storage

### 7.1 Source of truth

- Existing archive/Shadow books คง JSON authority + one-way mirror ตามเดิม แยกจาก Testnet account
- Testnet Exchange เป็น authority ของ accepted orders, executions, commissions และ free/locked balances
- DB เป็น authority ของ strategy decisions, intents/reservations/outbox, ownership/epoch และ audit; balances/positions ใน DB เป็น reconciled projection ของ Exchange ไม่ใช่ยอดที่ UI แก้ได้
- JSON เป็น export/checkpoint ของ Testnet records ไม่เขียนย้อนทับ Exchange state
- พบ discrepancy ให้ incident/pause entries และ reconcile ไม่แก้ด้วยการเติม local balance ให้เท่าที่คาดโดยไร้หลักฐาน
- Raw market/context/account evidence ใช้ immutable pointer/hash และ retention ที่เหมาะสม; redact secrets/signatures

### 7.2 Logical records

| Record group | Essential information |
| --- | --- |
| exchange_accounts / account_epochs | environment, product, account_ref, key_ref, allocation, reset/reconciliation status |
| instruments / versions | venue/environment/product/symbol, tick/step/notional/quantity rules, observed times |
| market_bars / feed_status / gaps | source time/received/available, sequence/health, raw pointers |
| context_documents / macro_events | source/revision/hash, published/received/available, relevance |
| feature_snapshots / evidence_manifests | immutable input/evidence/policy hashes และ source coverage |
| strategy_events / analysis_runs / attempts | decision scope, graph/model/prompt, TTL, costs/errors |
| order_intents / reservations / outbox | business key, client ID, immutable payload hash, safety epoch, risk/free balance reserved |
| orders / order_events | local/exchange/client IDs, raw/normalized status, cumulative executed quantity, cancel/unknown state |
| fills / commissions | scoped exchange trade key, qty/price, commission amount + asset, exchange/received times |
| journal / positions / balances / equity | Decimal/NUMERIC, multi-asset journal, reconciled projections, data quality |
| reconciliation_runs / reset_events | queried state/cursors, discrepancies, evidence, resolved_by/epoch boundary |
| safety_state / writer_leases / commands | HALT/epoch/fencing, current writer, actor/reason/revision/idempotency |
| worker_heartbeats / checkpoints | version/cursor/last success/error |

Reuse tables ที่มีจริงก่อน extend migration ไม่สร้าง schema owner อีกชุด Index/unique scope ต้องรวม environment/account/product/epoch ตามความหมาย โดยเฉพาะ exchange IDs ที่อาจเกิดใหม่หลัง reset

### 7.3 Transaction / accounting / retention

- Unique intent business key และ clientOrderId ฝั่งเรา: ไม่ reuse ID แม้ Exchange อาจอนุญาตหลัง order จบ
- Fill unique key ใช้ semantics ที่ discovery ยืนยัน เช่น account+epoch+symbol+tradeId; duplicate event มี ledger effect ครั้งเดียว
- Atomic reservation กัน concurrent orders ใช้ free balance เดียวกัน; pending UNKNOWN ยังกันวงเงินไว้จนรู้ผล
- บันทึก commission ตาม asset จริง เช่น BTC/USDT/BNB; balanced journal ต่อ asset ไม่เปลี่ยน fee เป็น USDT โดยไม่มี FX evidence
- Economic report แปลงค่าใช้จ่ายเป็น reporting currency ด้วย conversion policy/time ที่บันทึกไว้ ไม่หัก fee/slippage ซ้ำจากยอด net ที่ Exchange ให้แล้ว
- Closed bars batch upsert; revised historical records ไม่เปลี่ยน frozen decision evidence
- Quote/order book latest state อยู่ Worker; display batch 5 วินาทีไม่ใช่ ledger clock
- Raw high-resolution buffer DEFAULT 24 ชั่วโมง; promote decision/order/fill/reconcile evidence ไป experiment archive ก่อน buffer หมดอายุ
- Retain experiment evidence ตาม review/retention ADR; ไม่ลบประวัติเมื่อ Testnet reset
- Account events/order raw payload ต้อง redact key/signature/auth headers

### 7.4 Access / migration

Browser อ่านและสั่งผ่าน authorized API ไม่เขียน balances/ledger/orders โดยตรง Testnet key และ privileged DB key ไม่อยู่ frontend

ใช้ supabase/migrations ชุดเดียว ตรวจ local/remote history กับ deployed schema ก่อนเปลี่ยน; fresh DB rebuild และ authorization/transaction tests ต้องผ่านตามส่วนที่กระทบ

## 8. Binance Testnet execution, risk และ recovery

### 8.1 Testnet adapter contract และ endpoint checks

Adapter ต้องรองรับ get_server_time, get_instrument_rules, get_account, get_open_orders, get_order, get_trades, submit_order, cancel_order และ supported user-data subscription โดย map จาก official endpoint ที่ตรวจจริง ไม่สมมติชื่อ SDK method

Testnet signed request ใช้ key/auth type ที่เลือกจาก official docs, timestamp/recvWindow และ clock-offset checks เก็บ secret reference เท่านั้น Rate limit/backoff ทำตาม API headers/docs; ปิด SDK blind retry สำหรับ create order

DEFAULT first product: BTCUSDT Spot เมื่อ Testnet exchangeInfo ระบุ Trading และ order type/filters รองรับ; ไม่มี margin, leverage, short หรือ order บน symbol ที่ไม่ allowlist

ใช้ POST /api/v3/order/test เพื่อตรวจพารามิเตอร์ได้ แต่ end-to-end gate ต้องส่ง POST /api/v3/order บน Testnet และได้ order/fill/balance effects กลับมา ไม่ถือ validation response ว่าเป็น fill

### 8.2 Order lifecycle

Local states: CREATED → RESERVED → SUBMITTING → ACKNOWLEDGED → PARTIALLY_FILLED → FILLED; terminal อื่น REJECTED/CANCELED/EXPIRED ตาม exchange semantics; เพิ่ม UNKNOWN/RECONCILING/CANCEL_PENDING สำหรับ local delivery uncertainty

- แยก order state ออกจาก inventory/position, protection และ account safety
- Intent TTL หมดก่อน submit → ไม่ส่ง; order ที่รับแล้วต้อง query/cancel/reconcile ไม่ลบด้วย TTL
- ACK ไม่เท่ากับ Fill; User stream อาจแจ้ง Fill ก่อน HTTP response ให้ merge ผ่าน IDs และ cumulative execution ไม่เรียงตามเวลา arrival อย่างเดียว
- BUY/SELL MARKET ไม่รับประกันราคา; ใช้ actual Testnet fills/commissions คำนวณ VWAP และ realized amounts
- Cancel response อาจแข่งกับ Fill; กัน reservation และคง inventory ที่ Fill ไปแล้วตามหลักฐาน ห้ามสรุปว่าคำสั่ง cancel ทำให้ไม่มี position
- ไม่ cancel/recreate อัตโนมัติก่อนยืนยัน state เดิม; repricing policy ต้อง version
- Inventory/locked balance ปัจจุบันและ orders ที่ยังเปิดเป็นฐาน sizing; ตรวจ guard อีกครั้งก่อน submit

### 8.3 UNKNOWN และ restart

Timeout, network disconnect หรือบาง server error อาจเกิดหลัง Exchange รับคำสั่งแล้ว ให้บันทึก UNKNOWN และ query ด้วย original clientOrderId/orderId พร้อมตรวจ user stream/trades ก่อนตัดสินใจส่งใหม่

หาก query ครั้งแรกยังไม่พบ ยังไม่ถือว่าปฏิเสธแน่นอน DEFAULT reconcile ทุก 2 วินาทีแบบ capped backoff ใน 30 วินาทีแรก แล้ว alert/คง pause entries หากยัง unresolved; ปรับตาม Rate limit และ evidence จริง ห้ามปล่อย reservation เพียงเพราะหมดเวลา

Startup: ตรวจ endpoint/mode/key scope → server clock → acquire writer lease → load intents → reconcile open/unknown orders/trades/balances → verify protection/safety → จึงอนุญาต entries

ต้องส่งซ้ำได้อย่างควบคุม แต่ไม่อ้าง exactly-once network order placement; guarantee ที่ทำได้คือ durable intent, query-before-retry และ idempotent ledger

### 8.4 Testnet account stream และ periodic reconcile

- ใช้ supported Testnet user-data subscription ของ Binance; verify auth/renewal/reconnect จาก docs ปัจจุบัน
- เก็บ order execution events และ account balances พร้อม exchange time/receive time
- REST reconcile DEFAULT 30 วินาที และทันทีหลัง reconnect/UNKNOWN/restart/cancel ambiguity; ทำ rate-aware scheduling
- ตรวจ event ซ้ำ/out-of-order/trade cursor gaps และ inventory discrepancy; ไม่อาศัย stream เพียงช่องทางเดียว
- Manual Testnet order นอกบอตต้องถูกพบและ pause entries จนรับ ownership/แยก allocation; ไม่ลบรายการนั้นทิ้ง

### 8.5 Guardian และ risk

Spot Testnet เริ่มจากค่าที่ผู้ใช้เลือกใน spot.v1: day loss 5% alert, high-water drawdown 15% alert, 25% HALT เป็นข้อกำหนดทดลอง ไม่ใช่ safe threshold ที่พิสูจน์แล้ว

การตรวจ fresh intraday Testnet equity เป็น risk profile ใหม่ เช่น spot.tn.v1 เพราะต่างจาก daily-close simulation:

- Mark ด้วย Testnet bid/conservative exit estimate และ actual allocated inventory; แยก equity change จาก reset/fund adjustment
- HALT: block new entries → increment safety epoch → cancel open entry orders → reconcile late/partial fills → reduce owned inventory ตาม scoped policy → verify exchange balance/order state
- UI แสดง HALTING/UNKNOWN จนยืนยัน ไม่ประกาศ flat จากการคลิกหรือ HTTP ACK
- Guardian แยกจาก Strategy/AI และไม่รอ LLM; normal Gateway กับ emergency path ต้องมี fencing/ownership protocol ป้องกันเพิ่ม exposure แข่งกัน
- DEFAULT emergency ใช้ Gateway safety path; หากมี independent emergency writer ต้อง revoke/fence normal writer และมี race tests ก่อน enable
- Testnet supported protective stop/OCO capability ต้อง discovery ก่อนเปิด protection policy; ไม่อ้างมี exchange-side protection จากชื่อ endpoint อย่างเดียว
- ผลวัด intraday risk (docs/research/intraday-risk-plan.md, PR #41, dev data): ที่ 1x การเช็กปิดวันพลาด drawdown เพียง 1–3 จุด แต่ HALT 25% ที่เช็กปิดวันปล่อย R11 ไหลถึง 32.6% ขณะเช็กรายชั่วโมงหยุดได้ที่ ~25.5% Guardian จึงเช็ก drawdown/day loss กับทุก market event ไม่ใช่ปิดวัน และ exchange-side stop ใช้ระยะกันเหตุร้าย (≥25% จากราคาเข้า) เท่านั้น ระยะ 5–20% เปลี่ยนผลกลยุทธ์
- Feed/account/DB outage: no new entries; ใช้ fresh independent Testnet state สำหรับ reduction เมื่อมีสิทธิ์และข้อมูลพร้อม; ถ้าส่งไม่ได้ให้ incident ไม่สร้าง local fill แทน
- Standalone resume ต้องมี actor/reason; paired trial HALT absorbing ถึง endpoint; lifetime breach history ไม่ถูกล้าง

### 8.6 Reset และ account epochs

Binance Spot Testnet มี periodic reset ซึ่งอาจลบ orders/history และเปลี่ยน virtual balances โดยไม่มีแจ้งล่วงหน้า; API key อาจยังใช้ได้ อย่าใช้ key เดิมเป็นหลักฐานว่าเป็น trial epoch เดิม

เมื่อพบ history/balance discontinuity ให้ RESET_SUSPECTED + pause/reconcile/investigate ก่อน ไม่สรุปจาก query ไม่พบครั้งเดียว หากยืนยัน reset:

1. ปิด epoch เดิมด้วย status INTERRUPTED_TESTNET_RESET พร้อม last verified equity/cursors และ unresolved records
2. เก็บ DB history/evidence เดิม ไม่ล้างให้ตรง Exchange
3. บันทึกยอดทดสอบใหม่เป็น opening adjustment/epoch ใหม่ ไม่ใช่ trading P&L
4. เริ่ม trial segment ใหม่หลัง reconcile และ explicit audited start; ไม่ auto-resubmit intent เก่าหลัง reset

### 8.7 Shadow reference คงไว้แยก

Mainnet-data Shadow simulator ใช้วัดกลยุทธ์และ costs ตามสมมติฐานได้; Testnet ใช้วัด order lifecycle/integration และพฤติกรรมบนตลาดทดสอบ ราคาหรือ liquidity ของ Testnet ไม่ใช่หลักฐานกำไรที่ทำได้บน Mainnet

Fill/commission/funding ของ Testnet ต้องมาจาก Testnet จริง ส่วน simulated Fill ของ Shadow อยู่คนละ mode/portfolio/ledger; replay ข้อมูล Exchange เดิมใช้ตรวจ reconstruction ไม่ยิง order ซ้ำ

## 9. Frontend และ operator flow

| หน้า | ข้อมูลที่เพิ่ม |
| --- | --- |
| Overview | BINANCE TESTNET — เงินทดสอบ ชัด, product/account masked ID/epoch, allocated equity, free/locked assets, last reconcile |
| Data Health | signal vs executable feed, quote age/divergence, account-stream health, gaps/resync, funding actual/proxy |
| Strategy | active Testnet profile vs draft, timeframe/triggers, last evaluation, pending intent/UNKNOWN blocker |
| Orders/Activity | snapshot → Code/AI → risk/reservation → submitted → ACK/partial/fill/cancel/unknown; order/client/trade IDs |
| AI Analysis | evidence/reasons/partial status, graph/model/prompt, costs/deadlines/error categories |
| Risk | inventory/protection, pending reservations, HALTING/HALTED, Testnet emergency/resume, account epoch |
| Experiments | TESTNET execution conformance แยก SHADOW strategy performance; ไม่รวม returns ต่าง venue/mode |
| Settings | endpoint identity + key health (ไม่แสดงค่า key), permission/activation/read-only source config |

ราคาสดบน Browser เป็น display เท่านั้น ไม่เป็น source ของ Gateway ปุ่มส่งคำสั่งจากกลยุทธ์ต้องผ่าน Backend ทั้งหมด ไม่มี generic place-order ที่รับ arbitrary payload จาก Browser

### 9.1 App API ที่เสนอ

GET /api/v1/data-health  
GET /api/v1/workers  
GET /api/v1/testnet/account  
GET /api/v1/testnet/orders  
GET /api/v1/testnet/reconciliation  
GET /api/v1/analysis-runs/{id}  
GET /api/v1/experiments/{id}/report  
POST /api/v1/testnet-controls/pause  
POST /api/v1/testnet-controls/emergency-halt  
POST /api/v1/testnet-controls/resume  
POST /api/v1/experiments/drafts

Reuse endpoints เดิมหากเหมาะ ทุก mutation ต้อง Auth/role/account scope/expected revision/reason/Idempotency-Key; แสดง target เป็น Testnet ก่อนยืนยันและบันทึก command state REQUESTED → PROCESSING → EXCHANGE_CONFIRMED/UNRESOLVED ไม่ optimistic close

## 10. Roadmap และ PR slices

| Milestone / PR | Deliverables | Acceptance gate |
| --- | --- | --- |
| P0 — Discovery | active books/jobs audit, **Host ADR (Note เลือก §2.2)**, Spot Testnet auth/metadata/stream access, source/account/rights ADR, จำนวน Testnet accounts | ยืนยัน Testnet portal/account/endpoint จาก Host จริง; Mainnet trading ปิด |
| P1 — Modes/workers/secrets | SHADOW/TESTNET routing, disabled LIVE, heartbeat/leases, secret references, endpoint guards, **Telegram alerts + external dead-man (§2.3)**, process manager | mode/host/redirect tests; ปิดเว็บแล้ว workers เดินต่อ; ปิด worker แล้วได้ alert จากภายนอก |
| P2 — Market + account ingestion | signal/execution feeds, closed bars, Testnet user stream, checkpoints/backfill | gaps/duplicate/reconnect และ REST reconciliation fixtures ผ่าน |
| P3 — Durable persistence | intents/reservations/outbox, order/fill/account epoch tables, ledger/projections | atomic/idempotent/concurrent reservation + migration/auth tests |
| P4 — Spot Testnet adapter | signed create/query/cancel, actual test fills/commissions, UNKNOWN recovery, **tn-conformance-v1 (§5.4)** | actual small test-order round trip + restart/cancel/unknown drills; conformance รันอย่างน้อย 3 วันมี fills/cancel จริง; order/test อย่างเดียวไม่พอ |
| P5 — Code-only Testnet bot + Guardian | tn-r10a-code-v1 allocation/risk, intraday Guardian (§8.5), source divergence, protection/reduction, alerts, UI | account-level exclusive writer, HALT/late-fill/reset drills, alert drills, operational soak |

แผนนี้รับมอบที่ P5 (Testnet Spot + Code-only + Guardian) งาน P6–P11 อยู่ใน §10.3 เป็นแผนถัดไป เริ่มได้หลัง P5 ผ่านโดยไม่ต้องรอกัน

DEFAULT operational soak 7 วันหลัง P5 พร้อม fault drills เป็นการรับมอบงานระบบ ไม่ใช่พิสูจน์ alpha; คำสั่ง lifecycle มาจาก conformance (P4) และ drills ไม่ใช่รอให้ R10a เทรด ไม่ต้องรอครบหลายเดือนเพื่อเริ่ม AI research; ห้ามข้าม integrity/recovery/endpoint gates

P7 ingestion ทำคู่ขนานได้ แต่ใช้ตัดสินใจหลัง policy lock; implementation estimate หลัง P0 ตาม provider/host/team จริง ไม่รับประกันจำนวนสัปดาห์จากแผนอย่างเดียว

### 10.1 Reuse และ modules ใหม่

- Reuse data/point_in_time/raw_store/snapshot, deterministic strategy/risk/features และ analysis contracts ที่เหมาะสม
- Reuse shadow ledger สำหรับ research/reference; เพิ่ม Testnet fill ingestion/reconciliation แทนการเรียก simulated fill ใน account จริง
- เพิ่ม trading.execution/gateway, reservations/outbox, account_stream และ adapters/binance_spot_testnet ตาม boundary จริง
- Futures test adapter แยกจาก Spot; ไม่ใช้ arbitrary base URL parameter เพื่อสลับ Mainnet
- scripts/paper_run.py เป็น provenance reference ไม่ใช่ Testnet execution loop
- apps/api/apps/web ใช้ stack เดิม; ตรวจ dependency/interface ก่อนเพิ่ม framework

### 10.2 ความสัมพันธ์กับแผน Phase 4 เดิม

แผนนี้แทน docs/phase-4-execution-plan.md (PR #30) เป็นแผนหลัก งานที่ทำแล้วนับเป็นส่วนของแผนนี้ ไม่ทำซ้ำ:

| งานเดิม | อยู่ใน |
| --- | --- |
| PR #31 order core (Instrument, OrderIntent + client_order_id, state machine, pre-trade limits, reservation, FakeExchange) | P3/P4 |
| PR #32 exec store (trading.exec_*, migration applied 20261016144036, emergency journal) | P3 |
| ccxt หลัง ExchangeAdapter ของเราเอง | P4 (ตรวจ method mapping กับ official endpoints ตาม §8.1) |
| scenario runner | tn-conformance-v1 (§5.4) |

ค่า DEFAULT ที่ขัดกัน ตัดสินดังนี้ (ยัง version ได้ก่อนล็อก trial):

- เพดาน 2,000 USDT ต่อคำสั่ง (แผนเดิม) กับ allocation 10,000 USDT (§5.1): **allocation ของ tn-r10a-code-v1 ใน soak แรกเป็น 2,000 USDT** ให้ rebalance หนึ่งครั้งเป็นคำสั่งเดียวภายใต้เพดาน ขยายเป็น 10,000 ใน profile ถัดไปหลัง soak ผ่าน
- ความสดของราคา: executable bid/ask ไม่เกิน 5 วินาทีก่อน submit (§4.2) แทน 60 วินาทีของแผนเดิม; ราคา limit ห่าง mark ไม่เกิน 1% คงเดิม
- คงจากแผนเดิม: 1 คำสั่ง/วินาที, 2 active orders, 20 orders/วัน UTC, 50 cancels/วัน, REDUCING ระหว่าง alert กับ HALT, hard kill ไม่ cancel-all แบบเหวี่ยงแห

PR #30–#32 เป็นของ thread งาน Phase 4 การแก้ branch เหล่านั้นทำผ่าน thread นั้น

### 10.3 แผนถัดไป (หลัง P5)

| Milestone | Deliverables | Acceptance gate |
| --- | --- | --- |
| P6 — AI r1/r2 | reuse contracts/adapters, pin snapshots/cost/deadline, account isolation | mock + actual provider compatibility; Code/AI independent accounts หรือ sequential runs |
| P7 — News/macro | one provider each, dedup/provenance/context snapshot | future/revised-late excluded; new analysis revision ก่อนใช้ตัดสินใจ |
| P8 — Context strategy challenger | selected features หรือ hypothesis ใหม่ | locked hypothesis/source/data partitions; Shadow alpha report แยก Testnet outcomes |
| P9 — Specialists | bounded role graph/synthesis | required/optional/budget/deadline tests + ablation |
| P10 — Futures test environment | separate Futures key/client/user stream, R11 1x, funding | ดูข้อกำหนดด้านล่าง |
| P11 — Options | research/simulator ก่อน; external test execution เฉพาะ venue ที่ยืนยันรองรับ | UNVERIFIED จน discovery; ไม่สมมติว่ามี Binance Options Testnet |

ข้อกำหนด P10 (Futures) เพิ่มจาก v1.1:

- Leverage cap 1x ใน code และตั้ง isolated margin ที่ Exchange ตอนเริ่ม; อ่านกลับมาตรวจก่อนทุก entry
- Exchange-side STOP_MARKET reduce-only วางภายใน 5 วินาทีหลัง entry fill ที่ ≥25% จากราคาเข้า (§8.5) และตรวจว่า active ถูก side/quantity หลัง partial fill; ถ้าวางไม่ได้ Guardian ลดสถานะ
- Guardian เฝ้า mark price, margin ratio และระยะถึง liquidation ทุก event; HALT 25% เช็กต่อเนื่อง ไม่รอปิดวัน
- Dead-man switch: ถ้า worker เงียบเกิน 5 นาที ระบบภายนอกแจ้งเตือน; stop ฝั่ง Exchange ยังคุ้มครองระหว่างนั้น
- Funding ที่ถูกหัก/ได้รับจริงจาก Testnet; ไม่ใช้ Spot endpoints/key

การมี source code ไม่ยืนยันว่า deploy/running แล้ว ทุก PR ส่ง run evidence/commit/config/account-epoch refs โดยไม่เปิดเผย secrets

## 11. Acceptance scenarios

นี่คือแผนการทดสอบ ยังไม่ได้รันจากการแก้เอกสาร ใช้ mock/recorded fixtures ก่อน แล้ว actual Testnet orders ด้วย test funds หลัง capability พร้อม ไม่มี Mainnet orders

| ID | Scenario | Expected result |
| --- | --- | --- |
| RT01 | ปิดเว็บ/หลาย tabs/restart API | workers ต่อเนื่อง; ไม่สร้าง writer/jobs ซ้ำ |
| RT02 | events ซ้ำ/out-of-order | bar/state deterministic; duplicate fills ไม่ลง ledger ซ้ำ |
| RT03 | market/account stream reconnect/gap | resync+REST reconciliation ก่อน entries; fills ที่เกิดแล้วไม่หาย |
| RT04 | incomplete/unclosed candle | ไม่ใช้เป็น closed feature input |
| RT05 | restart/backfill ประวัติหลายวัน | restore state; ไม่ยิง historical decisions ย้อน |
| RT06 | intent เก่าหรือ AI มาหลัง TTL | ไม่ submit entry ใหม่; orders/fills ที่มีอยู่ยัง reconcile |
| RT07 | worker สองตัว/duplicate trigger | หนึ่ง canonical intent และ account writer |
| RT08 | crash หลัง submit หรือก่อน ledger commit | UNKNOWN/reconcile/idempotent post; ไม่ซื้อซ้ำ |
| RT09 | free balance ไม่พอ/locked/tick-step-notional fail | reject/resize ตาม policy ไม่ปัดขึ้นหรือสร้างยอดเอง |
| RT10 | partial fill/cancel race | inventory/commission/reservation ตรง actual executions |
| RT11 | feed stale ขณะรอ AI | final guard block entries แต่ยังรับ actual fills |
| RT12 | AI invalid/timeout/refusal/budget exhausted | r1 no increase; r2 no change; reliability แยก VETO |
| RT13 | r2 prompt มี Code suggested weight | contract/request test reject |
| RT14 | news ซ้ำ/แก้ไข/received หลัง cutoff | evidence dedup และไม่เข้า snapshot เก่า |
| RT15 | news/AI ขอ order tool/secret | ไม่มี capability; compiler ตรวจ output |
| RT16 | actual funding มาแทน proxy ภายหลัง | old evidence unchanged; result Testnet ไม่แทนด้วย Mainnet funding |
| RT17 | risk breach ระหว่าง AI hang | Guardian cancel/reconcile/reduce บน Testnet ไม่รอ AI |
| RT18 | HALT แต่ส่ง reduction ไม่ได้ | HALTING/UNRESOLVED ไม่แสดง flat สมมติ |
| RT19 | replay recorded fills/AI responses | DB reconstruction เดิม; ไม่มีการส่ง Exchange order ซ้ำ |
| RT20 | Mainnet host/key/redirect/LIVE flag | mutating Mainnet calls blocked; approved public signal reads แยก client |
| RT21 | Testnet และ Shadow อยู่พร้อมกัน | account/mode/ledger/report แยก; no mixed P&L |
| RT22 | model/source/strategy เปลี่ยนกลาง trial | new revision ไม่ silent pooling |
| RT23 | mock AI ตอบ Code เท่ากัน | proposal target เท่ากัน; ไม่บังคับ Testnet fills เท่ากันเมื่อเวลา/liquidity ต่าง |
| RT24 | required/optional specialist fail | locked failure policy; partial status ชัด |
| RT25 | Browser แก้ ledger/secret/direct order | denied ด้วย Backend/grants/capability |
| RT26 | migration rebuild | schema/history/auth/transactions ตรง |
| RT27 | UTC/ไทย/clock skew/signature timestamp | boundaries ถูกต้อง; skew ทำให้ pause signed submits |
| RT28 | new Testnet trial allocation | opening balances จาก Exchange; ไม่เอา pending reconstructed orders มา submit |
| RT29 | POST order/test ผ่านแต่ไม่มี actual order | ยังไม่ผ่าน end-to-end; ต้อง actual Testnet matching order/fill |
| RT30 | submit timeout แต่ Exchange fill | query original client ID/trades; no blind retry; ledger once |
| RT31 | query UNKNOWN ครั้งแรก not found | ยัง reconcile ตาม evidence ไม่คืน reservation ทันที |
| RT32 | account reset/history หาย | investigate/epoch boundary; no P&L gain; no resubmit old intents |
| RT33 | fee จ่าย BTC/BNB ต่างจาก quote asset | balanced multi-asset journal + reporting conversion evidence |
| RT34 | Mainnet/Testnet price divergence เกิน guard | block entry แสดง source mismatch ไม่เปลี่ยน venue เงียบ ๆ |
| RT35 | Code/AI share Spot account และ BTC inventory | block independent-arm claim; separate accounts หรือ sequential mode |
| RT36 | late Fill หลัง cancel/HALT | owned inventory reconcile/reduce; safety epoch กัน new entry |
| RT37 | HTTP 429/rate cap/network outage | backoff+pause ตาม policy; ไม่ spam duplicate submit |
| RT38 | credential ขาด/ผิดenvironment/revoked | no new submit; incident; ไม่ fallback Mainnet |
| RT39 | manual Testnet orders นอกบอต | detect reconciliation discrepancy/ownership conflict; pause entries |
| RT40 | restart ขณะมี open order/partial inventory | reconcile ก่อน start strategy; protection/ownership verified |
| RT41 | Host ดับทั้งเครื่อง | external dead-man แจ้ง Telegram ภายใน 5 นาที |
| RT42 | Telegram ล่ม/ตอบช้า | Guardian และ Gateway ทำงานต่อ; alert ถูกเก็บไว้ส่งซ้ำ |
| RT43 | conformance กับ strategy อยู่ account เดียวกัน | block การรันคู่; inventory/ledger ไม่ปน |
| RT44 | Futures entry fill แล้ววาง stop ไม่ได้ใน 5 วินาที | Guardian ลดสถานะและแจ้ง critical |

## 12. Evaluation และ Definition of Done

### 12.1 Engineering DoD

- [ ] public data + signed Binance Spot Testnet auth ใช้ได้จาก Host จริง; endpoint/key provenance ชัด
- [ ] actual Testnet create/query/cancel/fill/account balance round trip มี evidence พร้อม IDs/times
- [ ] order/test validation ไม่ถูกนับแทน matching-engine test
- [ ] Mainnet execution ไม่มี route/key; mode/host/redirect guards ผ่าน
- [ ] private stream + REST reconciliation/restart/UNKNOWN/cancel/partial/reset recovery ผ่าน
- [ ] reservations/outbox/fills/commission ledger atomic/idempotent และ Decimal/NUMERIC
- [ ] exclusive writer และ Guardian fencing ตรวจได้; UI แสดง HALTING/UNKNOWN ตามจริง
- [ ] account epoch/opening balance/allocation/reconciliation ตรง Exchange ไม่มี synthetic money เข้า Testnet projection
- [ ] operation soak + fault drills มีผลจริง; ไม่อ้างผ่านจาก config/docs อย่างเดียว
- [ ] news/AI/cost/error provenance ครบตาม phase ที่เปิด ไม่ต้องสร้างทุก phase เพื่อรับมอบ Spot

### 12.2 Research และ account isolation

Testnet ใช้พิสูจน์ execution/integration และเก็บพฤติกรรมกลยุทธ์บนสภาพตลาดทดสอบ ไม่ใช้ P&L หรือ fill quality เป็นหลักฐานว่าจะทำได้เท่ากันบน Mainnet

Mainnet-data Shadow ยังคงเป็น reference สำหรับ forward strategy research; report label TESTNET/SHADOW และ source/price/funding/cost assumptions ทุกครั้ง ห้ามอ้าง Testnet liquidity เป็น Mainnet liquidity

- One active strategy writer ต่อ test account/product; Code/AI/B&H Spot แข่งพร้อมกันใน account BTC เดียวไม่ได้เพราะ inventory/order balances ปนกัน
- หาก provider รองรับและผู้ใช้มี test accounts ที่ได้รับอนุญาตอิสระ ใช้ account แยก; ไม่สมมติว่ามี subaccounts ให้สร้างได้
- หากมีบัญชีเดียว: ทดสอบ execution ทีละ strategy/ช่วง พร้อม explicit inventory transition; paired performance ใช้ independent Shadow ledgers ไม่อ้าง sequential Testnet runs ว่า market-matched paired
- Testnet starting equity มาจาก allocated observed balances; Shadow reference ตั้ง 10,000 USDT ได้ แสดง starting-capital differences ไม่รวมผลตรง ๆ
- Model/graph/strategy/execution/risk/source เปลี่ยนให้เปิด revision ใหม่
- DEFAULT exploratory reviews วัน 7/14/28; small sample = INSUFFICIENT_EVIDENCE ได้ ไม่ลดเกณฑ์หลังเห็นผล
- แยก Testnet commissions/funding ที่บันทึกจริง, modeled Mainnet Shadow costs และ actual paid AI/data/hosting costs
- Reset/faucet/manual balance adjustment เป็น non-trading flow/epoch event ไม่ใช่ alpha
- HALT/incident history ไม่หายเมื่อ resume/reset และไม่เอา diagnostic continuation มาทับ actual policy result

### 12.3 Deliverables ต่อ PR

problem → resulting behavior → contracts/modules → actual tests/evidence → limitations → rollback พร้อม commit/config hashes และ RT IDs

เก็บ secrets นอก Repo/Chat; evidence ใช้ masked account refs, order/trade IDs, redacted responses, usage/cost และ timestamps

## 13. งานแรกที่เริ่มทำได้ทันที

1. ตรวจ current deployment และ existing books โดยไม่แก้พอร์ตเก่า; Note เลือก Host (§2.2)
2. ทำ Binance Spot Testnet connectivity/metadata/account capability ADR จาก Host ที่เลือก; ผู้ใช้ตั้ง Testnet key ผ่าน secret store เมื่อเข้าสู่ implementation
3. ทำ endpoint guards + TESTNET mode + signing/server time + account/user stream
4. สร้าง intent/reservation/outbox/query/reconcile/ledger และทดสอบด้วย mocked responses
5. ส่งคำสั่งขนาดเล็กตาม instrument minimum จริงบน Testnet เพื่อยืนยัน lifecycle หลัง config/credentials พร้อม; ไม่ใช่ Mainnet
6. รัน tn-conformance-v1 แล้วต่อ tn-r10a-code-v1 + Guardian + Telegram/dead-man + Dashboard และ soak test
7. แผนถัดไป (§10.3): AI r1/r2 ตาม account isolation, ข่าว/Indicators/specialists, Futures/Options

การแก้เอกสารนี้ไม่ส่งคำสั่ง ไม่สร้าง key และไม่เปลี่ยน code ของ Repository ขอบเขตที่อนุญาตให้พัฒนาในแผนคือ Binance Testnet ด้วยยอดเงินทดสอบเท่านั้น

## 14. แหล่งอ้างอิง

### Repository ที่อ่านจริง (pin baseline)

- [Runtime launcher](https://github.com/nnote1985-cpu/Tradingasakan/blob/bd98dd87443c97ffd7c8d98f8ea2fc99a3e45df9/scripts/dev.mjs)
- [API](https://github.com/nnote1985-cpu/Tradingasakan/blob/bd98dd87443c97ffd7c8d98f8ea2fc99a3e45df9/apps/api/api/main.py)
- [Display-only ticker](https://github.com/nnote1985-cpu/Tradingasakan/blob/bd98dd87443c97ffd7c8d98f8ea2fc99a3e45df9/apps/web/src/liveTicker.ts)
- [Daily Code rules](https://github.com/nnote1985-cpu/Tradingasakan/blob/bd98dd87443c97ffd7c8d98f8ea2fc99a3e45df9/backend/src/trading/alerts/risk_check.py)
- [Archive/funding adapter](https://github.com/nnote1985-cpu/Tradingasakan/blob/bd98dd87443c97ffd7c8d98f8ea2fc99a3e45df9/backend/src/trading/alerts/live_data.py)
- [Spot book](https://github.com/nnote1985-cpu/Tradingasakan/blob/bd98dd87443c97ffd7c8d98f8ea2fc99a3e45df9/backend/src/trading/shadow/rebalance_book.py)
- [AI filter](https://github.com/nnote1985-cpu/Tradingasakan/blob/bd98dd87443c97ffd7c8d98f8ea2fc99a3e45df9/backend/src/trading/analysis/pathA_filter.py)
- [AI proposer](https://github.com/nnote1985-cpu/Tradingasakan/blob/bd98dd87443c97ffd7c8d98f8ea2fc99a3e45df9/backend/src/trading/analysis/pathA_proposer.py)
- [Strategy library scope](https://github.com/nnote1985-cpu/Tradingasakan/blob/bd98dd87443c97ffd7c8d98f8ea2fc99a3e45df9/docs/strategy-library.md)

### Provider documentation ที่ตรวจประกอบ

- [Binance Spot Testnet General Info](https://developers.binance.com/en/docs/products/spot/testnet/general-info) — endpoint, virtual funds, reset และ product boundaries
- [Binance Spot Testnet REST API](https://developers.binance.com/en/docs/products/spot/testnet/rest-api) — signed orders, order/test distinction, query/account/trades และ timeout semantics
- [Binance Futures General Info](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/general-info) — แยก Futures test endpoints จาก Spot
- [Binance Demo Trading FAQ](https://www.binance.com/en/support/faq/detail/9be58f73e5e14338809e3b705b9687dd) — Demo/Testnet ต้องระบุ service/account ที่ใช้ชัด

- [Bybit public Order book](https://bybit-exchange.github.io/docs/v5/websocket/public/orderbook)
- [Bybit public Trades](https://bybit-exchange.github.io/docs/v5/websocket/public/trade)
- [Bybit Open Interest](https://bybit-exchange.github.io/docs/v5/market/open-interest)
- [CryptoPanic API entry](https://cryptopanic.com/developers/api/) — ยังต้องตรวจ plan-specific fields/rights/cost
- [Trading Economics Calendar](https://docs.tradingeconomics.com/economic_calendar/snapshot/)
- [Deribit Options data collection](https://docs.deribit.com/articles/options-data-collection-best-practices)

Cadences/TTL/divergence/risk defaults และ architecture ใหม่เป็น design choices ของโปรเจกต์ ไม่ใช่ Rate limit ที่ Provider รับรองหรือผลทดสอบกำไร เริ่ม implementation ให้ตรวจเอกสาร/API versions และ account/data access ปัจจุบันอีกครั้ง
