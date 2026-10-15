# หลักฐานตามข้อตรวจของ Note (2026-10-15)

หลักฐานผูกกับ commit ที่ระบุใน `/mnt/project-files/review-2026-10-15/evidence.md` (SHA เต็มของ PR #1 และ PR #2 พร้อมลิงก์ CI)
ไฟล์ที่อ้างถึงด้านล่างอยู่ใน branch ของ PR #2 ยกเว้นที่ระบุว่าอยู่ใน `/mnt/project-files`

| ข้อ | สิ่งที่แก้หรือเพิ่ม | หลักฐาน |
| --- | --- | --- |
| 1 ข้อสรุป ADR | ใช้ถ้อยคำของ Note ตรงตัว, ระบุว่ายังไม่ได้ทดสอบ AI, path A เป็นแค่ track วิจัยหนึ่ง, เป้าหมายระบบ Code+AI Futures/Spot/Options ไม่เปลี่ยน | `docs/adr/0001-path-a-after-phase-0.5.md` |
| 2 งานวิจัยทำซ้ำได้ | ช่วงวันที่ dev/val/holdout, แหล่งข้อมูล, ทุน, ต้นทุน, จำนวนเทรด, exposure, คำสั่งรัน + commit, วิธีคำนวณ B&H holdout −16.5%, ตารางแยก "ไม่กำไรสุทธิ / ไม่เหนือ benchmark / หลักฐานไม่พอ" | `docs/research/results-register.md` |
| 3 power analysis | ตารางสมมติฐาน (effect size, variance, block bootstrap, เกณฑ์ผ่าน), "10 ปี" เป็นค่าประมาณภายใต้สมมติฐาน, forward test ยังมีประโยชน์, บันทึกการเปิด holdout 2026-10-15 08:57 UTC และห้ามเรียกว่า unseen อีก | `docs/research/pathA-power-assumptions.md`, ADR ข้อ 4 |
| 4 Ledger | sync ซ้ำ 3 รอบไม่เกิดรายการซ้ำ, ยอดสมดุลแยกตามสกุลเงิน, sync ล้มกลางทางไม่เหลือรายการครึ่งเดียว, บน Supabase จริงรัน SQL ชุดเดิมซ้ำแล้วจำนวนแถวไม่เปลี่ยน | `tests/integration/test_migrations.py` (`test_paper_book_sync_is_balanced_and_idempotent`, `test_ledger_balances_per_currency`, `test_sync_that_fails_midway_leaves_nothing`) |
| 4 Auth | โทเคนผิดลายเซ็น/issuer/audience/หมดอายุ → 401, ไม่มีสิทธิ์ → 403, **ถูกถอนสิทธิ์ (active=false) → 403 แม้โทเคนยังไม่หมดอายุ** ทดสอบทั้งระดับ API และ SQL จริงบน Postgres | `tests/unit/test_api_auth.py`, `test_revoked_role_is_not_returned_by_the_api_role_query` · **ยังค้าง:** ทดสอบกับ Supabase จริง รอ Note เปิดโดเมนและสร้างผู้ใช้ทดสอบ |
| 5 reason code | ความหมายทุกโค้ด, แยก market / system / eligibility, system และ eligibility ต้อง ABSTAIN เสมอ, `reason_codes.v1`, `policy_decision.v1`, `filter_response.v1` | `docs/analysis-contracts.md`, `contracts/analysis.py`, test `test_every_reason_code_is_classified_and_system_reasons_abstain` |
| 5 average cost | ใช้กับ SPOT เท่านั้น; Futures = mark-to-market, Options = premium | `contracts/instruments.py`, `test_average_cost_is_spot_only` |
| 5 migration | ไฟล์ migration ตรงกับประวัติบน Supabase; ฐานใหม่สร้างจากไฟล์ใน repo แล้ว fingerprint ตรงกับฐานจริง (`179 32f56d95…`); ขั้นตอน "ตรวจก่อน ห้ามรันซ้ำ" | `supabase/README.md`, `supabase/migration_history.txt`, `supabase/schema_fingerprint.txt`, `test_rebuild_from_repo_matches_supabase_schema`, `test_migration_files_match_recorded_history` |
| 5 Telegram | ปิดบัง token ในทุก error (รวม URL), โหมด `--check` ไม่ส่งข้อความ; ตรวจแล้วว่า runtime ของงานรายวันเห็นตัวแปรจาก environment settings (getMe ผ่าน, bot @asakantradebot) และงานรายวันรัน `--check` ทุกวัน | `scripts/telegram_notify.py`, `tests/unit/test_telegram_notify.py` |
| 6 source of truth | JSON เป็นต้นฉบับ, `paper` และ `trading` เป็นสำเนาทางเดียวด้วย event key, วิธี reconcile, การย้ายไปใช้ DB เป็นการตัดสินใจแยก | `docs/source-of-truth.md` |
| 7 ขอบเขตเฟส | build นี้ปฏิเสธ LIVE ทุกกรณี แม้ตั้ง `APP_ENV=production` + `LIVE_ENABLED=true`; ไม่มี order adapter; สัญญา Instrument สำหรับ SPOT/PERP/DATED_FUTURE/OPTION (เปิดใช้แค่ SPOT/PERP); เลขงานวิจัย R1–R5 แยกจาก roadmap v1/v2/v3 | `config.py`, `test_production_env_and_flags_cannot_enable_live_in_this_build`, `results-register.md` |
| 8 CI | PR #1 CI ผ่านบน commit จริง 6c669ce; PR #2 CI ผ่านทุก commit ที่ push วันนี้ · **ยังค้าง:** รัน CI ของ #2 ใหม่หลัง base เปลี่ยนเป็น main (ทำได้หลัง merge #1) | GitHub Actions |
| 8 แก้รูปแบบไม่เปลี่ยนผล | รันงานวิจัยทั้ง 6 ชุด (R1–R4, power, holdout) บนหัว PR #1 และหัว PR #2 ด้วยข้อมูลชุดเดียวกัน: ไฟล์ผลทั้ง 44 ไฟล์ sha256 ตรงกันทุกไฟล์ และตรงกับผลที่เผยแพร่ไว้เดิมทุกไฟล์ | `/mnt/project-files/review-2026-10-15/outputs-*.sha256` |
| 8 manifest พอร์ตจำลอง | pathA-v1 รันจาก commit 42920baa…, tree 6e25a92e…, ไม่มีไฟล์แก้ค้าง; hash ของโค้ด 26 ไฟล์, config (กฎ+ต้นทุน), ไฟล์ข้อมูล 57 ไฟล์ที่ตรวจกับ .CHECKSUM ของ Binance แล้ว, และ state.json | `scripts/paper_manifest.py`, `/mnt/project-files/paper-trading/pathA-v1/manifests/2026-10-14.json` (สร้างย้อนหลังจาก archive ที่ไม่เปลี่ยนแล้ว) |

## รอบที่ 2: คำถามเรื่อง manifest

| เรื่อง | คำตอบสั้น | หลักฐาน |
| --- | --- | --- |
| ตลาดที่จำลอง | พอร์ตแบบ Spot ใช้ราคา perpetual เป็นตัวแทน fill/mark/B&H; basis เฉลี่ย −0.044% (holdout), holdout ด้วยราคา spot −6.93% เทียบ −6.90% | `docs/paper-book-data.md` §1, `results-register.md`, `/mnt/project-files/pathA-data-checks/report.json` |
| Funding ต.ค. | ประมาณจาก premium index (proxy) ไม่ใช่ funding จริง; ตรวจกับของจริง 12 เดือน คลาดเฉลี่ย 0.0027%/รอบ; ค่า 7 วันห่างจากเกณฑ์ราว 25 เท่า; ไม่มีการใช้ข้อมูลเก่าแทน | `docs/paper-book-data.md` §2 |
| เวลาของ paper trading | รายการถึงตอนนี้เป็น reconstructed; forward record เริ่มรอบ 2026-10-16 ผ่าน `scripts/paper_run.py` (hash chain + `trading.audit_events`); baseline ยึดไว้ที่ audit_events id 2 | `docs/paper-book-data.md` §3, `/mnt/project-files/paper-trading/pathA-v1/provenance.md` |
