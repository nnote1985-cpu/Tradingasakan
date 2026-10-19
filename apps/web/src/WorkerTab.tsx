import { useQuery, useQueryClient } from "@tanstack/react-query";
import { type ReactNode, useState } from "react";
import {
  type ApiError, apiGet, apiPost, type WorkerActivity, type WorkerControl, type WorkerPnl, type WorkerExecution, type WorkerStatus,
  type WorkersResponse,
} from "./api";
import { workerIssues } from "./Operations";

// Status of the Testnet worker (testnet-development-plan.md §9). The worker writes this to Supabase about every 10 s.
// The only command from this page is HALT/RESUME: a row in trading.exec_operator_requests that Guardian carries out (P5).

const STREAM_TH: Record<string, string> = { klines: "แท่งราคา (Mainnet)", quotes: "ราคา Testnet", account: "บัญชี Testnet" };
const STATE_TH: Record<string, string> = {
  INTENT: "รอส่ง", SUBMITTING: "กำลังส่ง", ACKNOWLEDGED: "เปิดอยู่", PARTIALLY_FILLED: "ได้บางส่วน", FILLED: "ได้ครบ",
  CANCELED: "ยกเลิก", REJECTED: "ถูกปฏิเสธ", EXPIRED: "หมดอายุ", UNKNOWN: "ยังไม่รู้ผล", RECONCILING: "กำลังตรวจผล",
};
const SIDE_TH: Record<string, string> = { BUY: "ซื้อ", SELL: "ขาย" };
const TABLE = "w-full text-sm [&_td]:py-2 [&_td]:pr-4 [&_th]:pr-4 [&_th]:text-left [&_th]:font-normal [&_th]:text-[var(--text-muted)]";
const VERDICT_TH: Record<string, string> = { MATCH: "ตรงกัน", DISCREPANCY: "ไม่ตรง", INCONCLUSIVE: "ยังสรุปไม่ได้", UNAVAILABLE: "อ่านบัญชีไม่สำเร็จ" };

function ago(iso: string | null): string {
  if (!iso) return "–";
  const s = Math.max(0, Math.round((Date.now() - Date.parse(iso)) / 1000));
  return s < 90 ? `${s} วินาทีที่แล้ว` : s < 5400 ? `${Math.round(s / 60)} นาทีที่แล้ว` : `${Math.round(s / 3600)} ชั่วโมงที่แล้ว`;
}

function utc(ms: number | null): string {
  return ms === null ? "–" : new Date(ms).toISOString().slice(0, 16).replace("T", " ") + " UTC";
}

function Dot({ ok }: { ok: boolean | null | undefined }) {
  const c = ok === null || ok === undefined ? "bg-[var(--series-cash)]" : ok ? "bg-[var(--gain)]" : "bg-[var(--loss)]";
  return <span aria-hidden className={`mr-1.5 inline-block h-2 w-2 shrink-0 rounded-full ${c}`} />;
}

function Row({ label, ok, children }: { label: string; ok?: boolean | null; children: ReactNode }) {
  return (
    <div className="grid gap-1 border-t border-[var(--grid)] py-3 text-sm sm:grid-cols-[12rem_minmax(0,1fr)] sm:gap-4">
      <span className="text-[var(--text-secondary)]">{label}</span>
      <span className="flex min-w-0 items-baseline break-words">{ok !== undefined && <Dot ok={ok} />}<span className="min-w-0">{children}</span></span>
    </div>
  );
}

function ExecutionRows({ x }: { x: WorkerExecution }) {
  const c = x.counts;
  return (
    <>
      <Row label="คำสั่ง Testnet" ok={!x.last_error && x.unresolved.length === 0}>
        วันนี้ส่ง {x.orders_today} · ยกเลิก {x.cancels_today} · เปิดอยู่ {x.open_orders}
        {" "}· รอบนี้ เต็ม {c.filled ?? 0} ยกเลิก {c.canceled ?? 0} ถูกปฏิเสธ {c.rejected ?? 0} หมดอายุ {c.expired ?? 0}
        {x.last_error && <span className="text-[var(--loss)]"> · {x.last_error}</span>}
      </Row>
      {x.unresolved.length > 0 && (
        <ul role="alert" className="ml-40 list-disc pl-6 text-sm text-[var(--loss)]">
          {x.unresolved.map((u) => <li key={u.client_order_id}>{u.client_order_id} {u.state} มา {u.for_s} วินาที (ยังไม่เปิดคำสั่งใหม่จนกว่าจะรู้ผล)</li>)}
        </ul>
      )}
      <Row label="Conformance (ทดสอบระบบ)">
        {x.conformance ? `${x.conformance.cycle ?? "–"} · ${x.conformance.note || "–"} · ครบ ${x.conformance.cycles_done} รอบ` : "ปิดอยู่"}
      </Row>
      {x.guardian && <GuardianRow g={x.guardian} />}
    </>
  );
}

// spot.tn.v1 (backend/src/trading/risk/profiles.py): alert at 5% day loss or 15% drawdown, HALT at 25% drawdown.
const DD_ALERT = 0.15;
const DD_HALT = 0.25;

function GuardianRow({ g }: { g: NonNullable<WorkerExecution["guardian"]> }) {
  const dds = g.books.map((b) => (b.drawdown === undefined ? null : Number(b.drawdown)));
  const ok = dds.every((d) => d === null || d < DD_ALERT);
  return (
    <Row label={`Guardian (${g.profile})`} ok={g.books.length ? ok && !g.price_off : null}>
      {g.price_off && <div className="text-[var(--loss)]">ราคา Testnet เพี้ยนจาก Mainnet จึงพักการตีมูลค่าไว้ ({g.price_off})</div>}
      {g.books.length === 0 && "ยังไม่ได้ดูแล book ไหน (รับคำสั่ง HALT/RESUME อย่างเดียว)"}
      {g.books.map((b, i) => {
        const dd = dds[i];
        if (b.equity === null || dd === null) return <div key={b.book}>{b.book} · รอราคาแรก</div>;
        const day = b.day_open ? Number(b.equity) / Number(b.day_open) - 1 : 0;
        return (
          <div key={b.book}>
            {b.book} · equity {b.equity} / สูงสุด {b.hwm} USDT · drawdown {(dd * 100).toFixed(2)}% (เตือน 15%, HALT 25%,
            {" "}เหลืออีก {Math.max(0, (DD_HALT - dd) * 100).toFixed(1)}%) · วันนี้ {(day * 100).toFixed(2)}%
          </div>
        );
      })}
    </Row>
  );
}

function when(iso: string): string {
  return new Date(iso).toLocaleString("th-TH", { dateStyle: "short", timeStyle: "medium" });
}

function signed(v: string | null): string {
  if (v === null) return "–";
  return Number(v) > 0 ? `+${v}` : v;
}

function tone(v: string | null): string {
  if (v === null || Number(v) === 0) return "";
  return Number(v) > 0 ? "text-[var(--gain)]" : "text-[var(--loss)]";
}

function Pnl({ p }: { p: WorkerPnl }) {
  if (p.epoch === null) return <p className="text-sm text-[var(--text-muted)]">ยังไม่เปิดรอบบัญชี</p>;
  const markNote = p.mark === null ? "ยังไม่มีราคา BTC" : `BTC คิดที่ ${p.mark} USDT (${p.mark_source === "quote" ? "ราคา bid ล่าสุดบน Testnet" : "ราคาเทรดล่าสุด"})`;
  return (
    <section data-testid="worker-pnl">
      <h3 className="mb-1 text-sm font-semibold">ยอดเงินและกำไร/ขาดทุน · รอบบัญชี #{p.epoch}</h3>
      <div className="flex flex-wrap items-baseline gap-x-6 gap-y-1">
        <p className="text-sm">
          รวม BTC + USDT เทียบเป็น USDT ตั้งแต่เริ่มรอบ{" "}
          <span className={`text-base font-semibold tabular-nums ${tone(p.equity_change_usdt)}`}>{signed(p.equity_change_usdt)} USDT</span>
        </p>
        <p className="text-xs text-[var(--text-muted)]">{markNote}</p>
      </div>
      <div className="mt-1 overflow-x-auto">
        <table className={TABLE}>
          <thead>
            <tr><th>สินทรัพย์</th><th className="!text-right">ยอดตั้งต้น</th><th className="!text-right">ตอนนี้</th><th className="!text-right">เปลี่ยนไป</th></tr>
          </thead>
          <tbody>
            {p.balances.map((b) => (
              <tr key={b.asset} className="border-t border-[var(--grid)]">
                <td>{b.asset}</td>
                <td className="text-right tabular-nums">{b.opening}</td>
                <td className="text-right tabular-nums">{b.now ?? "–"}</td>
                <td className={`text-right tabular-nums ${tone(b.change)}`}>{signed(b.change)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {p.books.length > 0 && (
        <div className="mt-2 overflow-x-auto">
          <table className={TABLE}>
            <thead>
              <tr><th>ที่มา</th><th className="!text-right">ซื้อ/ขาย (ครั้ง)</th><th className="!text-right">ซื้อขายรวม (USDT)</th>
                <th className="!text-right">BTC สุทธิ</th><th className="!text-right">USDT สุทธิ</th><th className="!text-right">ค่าธรรมเนียม BNB</th>
                <th className="!text-right">กำไร/ขาดทุน (USDT)</th></tr>
            </thead>
            <tbody>
              {p.books.map((b) => (
                <tr key={b.book ?? "-"} className="border-t border-[var(--grid)]">
                  <td className="whitespace-nowrap">{b.book ?? "ไม่ใช่ของบอท"}</td>
                  <td className="text-right tabular-nums">{b.buys}/{b.sells}</td>
                  <td className="text-right tabular-nums">{b.volume}</td>
                  <td className="text-right tabular-nums">{signed(b.btc_net)}</td>
                  <td className="text-right tabular-nums">{signed(b.usdt_net)}</td>
                  <td className="text-right tabular-nums">{b.bnb_fees}</td>
                  <td className={`text-right font-semibold tabular-nums ${tone(b.pnl_usdt)}`}>{signed(b.pnl_usdt)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="mt-1 text-xs text-[var(--text-muted)]">
        กำไร/ขาดทุนของแต่ละที่มา = USDT ที่ได้จากการขาย − ที่จ่ายซื้อ − ค่าธรรมเนียม + BTC ที่ยังถืออยู่คิดที่ราคาข้างบน
        ค่าธรรมเนียมที่จ่ายเป็น BNB แสดงแยก ไม่ได้แปลงเป็น USDT · conformance เป็นการทดสอบระบบ ไม่ใช่ผลกลยุทธ์
      </p>
    </section>
  );
}

function Activity({ account }: { account: string }) {
  const q = useQuery<WorkerActivity, ApiError>({
    queryKey: ["worker-activity", account],
    queryFn: () => apiGet<WorkerActivity>(`/api/v1/workers/${encodeURIComponent(account)}/activity`),
    refetchInterval: 15_000,
  });
  if (q.isPending) return <p className="mt-3 text-sm">กำลังโหลดคำสั่งและเทรด…</p>;
  if (q.isError) return <p role="alert" className="mt-3 text-sm text-[var(--loss)]">{q.error.message ?? "โหลดคำสั่งและเทรดไม่สำเร็จ"}</p>;
  const { orders, trades, pnl } = q.data;
  return (
    <div className="mt-4 space-y-4">
      {pnl && <Pnl p={pnl} />}
      <section>
        <h3 className="mb-1 text-sm font-semibold">เทรดล่าสุดบน Testnet</h3>
        {trades.length === 0 ? <p className="text-sm text-[var(--text-muted)]">ยังไม่มีเทรด</p> : (
          <div className="overflow-x-auto">
            <table data-testid="worker-trades" className={TABLE}>
              <thead>
                <tr><th>เวลา</th><th>ฝั่ง</th><th className="!text-right">จำนวน</th><th className="!text-right">ราคา</th>
                  <th className="!text-right">มูลค่า (USDT)</th><th className="!text-right">ค่าธรรมเนียม</th><th>ที่มา</th></tr>
              </thead>
              <tbody>
                {trades.map((t) => (
                  <tr key={`${t.epoch}-${t.trade_id}`} className="border-t border-[var(--grid)]">
                    <td className="whitespace-nowrap">{when(t.traded_at)}</td>
                    <td className={t.side === "BUY" ? "text-[var(--gain)]" : "text-[var(--loss)]"}>{SIDE_TH[t.side]}</td>
                    <td className="text-right tabular-nums">{t.qty}</td>
                    <td className="text-right tabular-nums">{t.price}</td>
                    <td className="text-right tabular-nums">{t.quote_qty}</td>
                    <td className="text-right whitespace-nowrap tabular-nums">{t.commission} {t.commission_asset ?? ""}</td>
                    <td className="whitespace-nowrap">{t.book ? `${t.book} · ${t.leg}` : "ไม่ใช่ของบอท"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      <section>
        <h3 className="mb-1 text-sm font-semibold">คำสั่งล่าสุด</h3>
        {orders.length === 0 ? <p className="text-sm text-[var(--text-muted)]">ยังไม่มีคำสั่ง</p> : (
          <div className="overflow-x-auto">
            <table data-testid="worker-orders" className={TABLE}>
              <thead>
                <tr><th>สร้างเมื่อ</th><th>ที่มา</th><th>ฝั่ง</th><th>ชนิด</th><th className="!text-right">จำนวน</th>
                  <th className="!text-right">ราคาที่ตั้ง</th><th>สถานะ</th><th className="!text-right">ได้แล้ว</th><th className="!text-right">ราคาเฉลี่ย</th></tr>
              </thead>
              <tbody>
                {orders.map((o) => (
                  <tr key={o.client_order_id} className="border-t border-[var(--grid)]" title={`${o.client_order_id} · ${o.reason}`}>
                    <td className="whitespace-nowrap">{when(o.created_at)}</td>
                    <td className="whitespace-nowrap">{o.book} · {o.leg}</td>
                    <td>{SIDE_TH[o.side]}</td>
                    <td>{o.type}</td>
                    <td className="text-right tabular-nums">{o.qty}</td>
                    <td className="text-right tabular-nums">{o.limit_price ?? "ตลาด"}</td>
                    <td className="whitespace-nowrap">{STATE_TH[o.state] ?? o.state}</td>
                    <td className="text-right tabular-nums">{o.filled_qty}</td>
                    <td className="text-right tabular-nums">{o.avg_price ?? "–"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

const ACTION_TH: Record<string, string> = { OPEN: "เปิดพอร์ต", REBALANCE: "ปรับสัดส่วน", HOLD: "คงเดิม", NO_DECISION: "ข้อมูลไม่พอ" };
const EVENT_TH: Record<string, string> = {
  DAY_LOSS_ALERT: "เตือนขาดทุนรายวัน", DRAWDOWN_ALERT: "เตือนลงจากจุดสูงสุด", HALT: "HALT", FLAT_CONFIRMED: "ขายหมดแล้ว", RESUME: "กลับมาทำงาน",
};

function pct(w: string | null): string {
  return w === null ? "–" : `${Math.round(Number(w) * 100)}%`;
}

function HaltControl({ account, safety }: { account: string; safety: string | null }) {
  const cache = useQueryClient();
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const kind = safety === "HALTED" ? "RESUME" : safety === "HALTING" ? null : "HALT";
  if (kind === null) return <p className="text-sm">กำลัง HALT: ยกเลิกคำสั่งซื้อและขาย BTC ของบอทอยู่ รอให้เป็น HALTED ก่อนจึงจะ RESUME ได้</p>;
  async function send() {
    const text = kind === "HALT"
      ? "HALT จะหยุดเปิดคำสั่งใหม่ ยกเลิกคำสั่งซื้อที่เปิดอยู่ และขาย BTC ของบอทออกทั้งหมด แล้วรอจนกว่าจะสั่ง RESUME ยืนยันไหม"
      : "RESUME จะให้ worker ตรวจบัญชีใหม่แล้วกลับมาเทรดตามกลยุทธ์ จุดสูงสุดของพอร์ตจะเริ่มนับใหม่จากตอนนี้ ยืนยันไหม";
    if (!window.confirm(text)) return;
    setBusy(true);
    setMsg(null);
    try {
      await apiPost(`/api/v1/workers/${encodeURIComponent(account)}/requests`, { kind, reason: reason.trim() });
      setReason("");
      setMsg({ ok: true, text: `ส่งคำสั่ง ${kind} แล้ว worker จะทำภายในไม่กี่วินาที ดูผลในรายการด้านล่าง` });
      await cache.invalidateQueries({ queryKey: ["worker-control", account] });
    } catch (e) {
      setMsg({ ok: false, text: (e as { message?: string }).message ?? "ส่งคำสั่งไม่สำเร็จ" });
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="flex flex-wrap items-center gap-2 text-sm">
      <input aria-label="เหตุผล" value={reason} onChange={(e) => setReason(e.target.value)} maxLength={200}
        placeholder={kind === "HALT" ? "เหตุผลที่ HALT เช่น ซ้อม HALT" : "เหตุผลที่ RESUME"}
        className="min-w-0 flex-1 rounded border border-[var(--border)] bg-[var(--surface-0)] px-2 py-1" />
      <button type="button" disabled={busy || !reason.trim()} onClick={send}
        className={`rounded px-3 py-1 font-medium text-white disabled:opacity-50 ${kind === "HALT" ? "bg-[var(--loss)]" : "bg-[var(--gain)]"}`}>
        {kind}
      </button>
      {msg && <p role={msg.ok ? "status" : "alert"} className={`w-full ${msg.ok ? "" : "text-[var(--loss)]"}`}>{msg.text}</p>}
    </div>
  );
}

function Control({ account, safety }: { account: string; safety: string | null }) {
  const q = useQuery<WorkerControl, ApiError>({
    queryKey: ["worker-control", account],
    queryFn: () => apiGet<WorkerControl>(`/api/v1/workers/${encodeURIComponent(account)}/control`),
    refetchInterval: 15_000,
  });
  if (q.isPending) return <p className="mt-3 text-sm">กำลังโหลดการตัดสินใจของบอท…</p>;
  if (q.isError) return <p role="alert" className="mt-3 text-sm text-[var(--loss)]">{q.error.message ?? "โหลดการตัดสินใจของบอทไม่สำเร็จ"}</p>;
  if (!q.data.available) return null;
  const { decisions, risk_events, requests } = q.data;
  return (
    <div className="mt-4 space-y-4">
      <section data-testid="worker-halt">
        <h3 className="mb-1 text-sm font-semibold">หยุดฉุกเฉิน (HALT) และกลับมาทำงาน (RESUME)</h3>
        <HaltControl account={account} safety={safety} />
        {requests.length > 0 && (
          <ul className="mt-2 space-y-1 text-sm text-[var(--text-secondary)]">
            {requests.slice(0, 5).map((r) => (
              <li key={r.id}>{when(r.requested_at)} · {r.kind} โดย {r.actor.startsWith("web:") ? "เว็บ" : r.actor} ({r.reason}) · {r.outcome ?? "รอ worker"}</li>
            ))}
          </ul>
        )}
      </section>
      <section>
        <h3 className="mb-1 text-sm font-semibold">การตัดสินใจรายวันของบอท</h3>
        {decisions.length === 0 ? <p className="text-sm text-[var(--text-muted)]">ยังไม่มี (บอทตัดสินใจวันละครั้งหลัง 00:10 UTC)</p> : (
          <div className="overflow-x-auto">
            <table data-testid="worker-decisions" className={TABLE}>
              <thead><tr><th>วัน (ปิดแท่ง)</th><th>บอท</th><th>ตัดสินใจ</th><th>เป้าถือ BTC</th><th>ผล</th><th>เหตุผล</th></tr></thead>
              <tbody>
                {decisions.map((d) => (
                  <tr key={`${d.book}-${d.day}`} className="border-t border-[var(--grid)] align-top">
                    <td className="whitespace-nowrap">{d.day}</td>
                    <td className="whitespace-nowrap">{d.book}</td>
                    <td className="whitespace-nowrap">{ACTION_TH[d.action] ?? d.action}</td>
                    <td className="whitespace-nowrap">{d.previous_weight !== null && d.previous_weight !== d.weight ? `${pct(d.previous_weight)} → ` : ""}{pct(d.weight)}</td>
                    <td>{d.outcome ?? (d.action === "HOLD" || d.action === "NO_DECISION" ? "ไม่ต้องเทรด" : `รอส่ง (ลองแล้ว ${d.attempts})`)}</td>
                    <td className="text-[var(--text-secondary)]">{d.notes}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      {risk_events.length > 0 && (
        <section>
          <h3 className="mb-1 text-sm font-semibold">เหตุการณ์ของ Guardian</h3>
          <ul data-testid="worker-risk-events" className="space-y-1 text-sm">
            {risk_events.slice(0, 10).map((e, i) => (
              <li key={`${e.at}-${i}`}><span className="whitespace-nowrap">{when(e.at)}</span> · {EVENT_TH[e.kind] ?? e.kind} · {e.detail}</li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

const hasActivity = (w: WorkerStatus) => w.epoch !== null || w.execution !== null;

function WorkerCard({ w }: { w: WorkerStatus }) {
  const r = w.reconcile;
  const issues = workerIssues(w);
  return (
    <article data-testid="worker-card" className="rounded-xl border border-[var(--border)] bg-[var(--surface-1)] p-4">
      <div className="flex flex-wrap items-baseline gap-2">
        <h2 className="font-semibold">Worker บน {w.host}</h2>
        <span className="text-sm text-[var(--text-muted)]">บัญชี {w.account} · {w.mode ?? "–"}</span>
        <span className={`ml-auto rounded px-2 py-0.5 text-xs ${w.silent ? "bg-[var(--loss)] text-white" : "bg-[var(--warn-bg)] text-[var(--warn-fg)]"}`}>
          {w.silent ? "ไม่ตอบ" : "ทำงานอยู่"}
        </span>
      </div>
      <div className="mt-4 rounded-xl bg-[var(--surface-0)] p-4"><p className="font-medium">{issues.length ? "สถานะที่ต้องตรวจสอบ" : "สถานะล่าสุดผ่านการตรวจ"}</p>{issues.length > 0 && <ul className="mt-2 list-disc space-y-1 pl-5 text-sm">{issues.map((issue) => <li key={issue}>{issue}</li>)}</ul>}<p className="mt-2 text-xs text-[var(--text-muted)]">สถานะเครื่องทำงานแยกจากความพร้อมส่งคำสั่ง · ไม่ใช่คำยืนยันว่าเปิดเทรดได้อย่างปลอดภัย</p></div>
      {w.silent && (
        <p role="alert" className="mt-2 text-sm text-[var(--loss)]">
          Worker ไม่ได้ส่งสถานะมา {ago(w.updated_at)} ให้ดูที่เครื่อง {w.host} ว่ายังเปิดอยู่ หรือดับเบิลคลิก update-worker.cmd เพื่อเริ่มใหม่
        </p>
      )}
      {w.execution && <Control account={w.account} safety={w.execution.safety ?? w.safety} />}
      {hasActivity(w) && <Activity account={w.account} />}
      <details className="mt-5 rounded-xl border border-[var(--border)] p-4">
        <summary className="cursor-pointer font-medium">รายละเอียดการเชื่อมต่อและระบบ</summary>
      <div className="mt-3">
        <Row label="อัปเดตล่าสุด" ok={!w.silent}>{ago(w.updated_at)} · เริ่มรัน {ago(w.started_at)}</Row>
        <Row label="สถานะความปลอดภัย">{w.safety ?? "–"}{w.lease_token !== null && ` · lease ${w.lease_token}`}</Row>
        <Row label="นาฬิกาเทียบ Testnet" ok={w.clock.status === null ? null : w.clock.status === "OK"}>
          {w.clock.offset_ms === null ? "–" : `${w.clock.offset_ms > 0 ? "+" : ""}${w.clock.offset_ms} ms`} {w.clock.status ?? ""}
          {w.clock.signing_paused && " · หยุดเซ็นคำสั่งชั่วคราว"}
        </Row>
        <Row label="กุญแจ Testnet" ok={w.account_ok}>{w.account_ok === null ? "ยังไม่ตรวจ" : w.account_ok ? "ใช้ได้" : "ใช้ไม่ได้"}</Row>
        <Row label="Dead-man ภายนอก" ok={w.deadman_ok}>{w.deadman_ok === null ? "ไม่ได้ตั้ง" : w.deadman_ok ? "ping ได้" : "ping ไม่ได้"}</Row>
        {Object.entries(w.streams).map(([name, s]) => (
          <Row key={name} label={STREAM_TH[name] ?? name} ok={s.connected}>
            {s.connected ? "เชื่อมอยู่" : "หลุด"} · ต่อแล้ว {s.connects} ครั้ง
            {s.silent_ms !== null && s.silent_ms !== undefined && ` · ข้อความล่าสุด ${Math.round(s.silent_ms / 1000)} วินาทีก่อนส่งสถานะ`}
            {!s.connected && s.error && <span className="text-[var(--text-muted)]"> · {s.error}</span>}
          </Row>
        ))}
        <Row label="แท่งราคาล่าสุด">1m {utc(w.bars.last_1m)} · 1d {utc(w.bars.last_1d)} · ช่องว่าง {w.bars.gaps ?? 0} · แก้ย้อนหลัง {w.bars.revisions ?? 0}</Row>
        <Row label="ตรวจบัญชีกับ REST" ok={r ? r.verdict === "MATCH" : null}>
          {r ? `${VERDICT_TH[r.verdict] ?? r.verdict} · ${ago(r.checked_at)} · order เปิด ${r.open_orders}` : "ยังไม่มี"}
        </Row>
        {r && r.differences.length > 0 && (
          <ul className="ml-40 list-disc pl-6 text-sm text-[var(--loss)]">{r.differences.slice(0, 5).map((d) => <li key={d}>{d}</li>)}</ul>
        )}
        <Row label="ยอดเงินทดสอบ">
          {w.balances.length === 0 ? "–" : w.balances.map((b) => `${b.asset} ${b.free}${b.locked !== "0" && Number(b.locked) !== 0 ? ` (ล็อก ${b.locked})` : ""}`).join(" · ")}
          {w.asset_count > w.balances.length && <span className="text-[var(--text-muted)]"> · รวม {w.asset_count} สินทรัพย์</span>}
        </Row>
        <Row label="รอบบัญชี (epoch)">{w.epoch ? `#${w.epoch.epoch} เริ่ม ${ago(w.epoch.opened_at)} · เทรด ${w.trades} รายการ` : "ยังไม่เปิด"}</Row>
        {w.execution && <ExecutionRows x={w.execution} />}
        {w.persist && (
          <Row label="บันทึกลง Supabase" ok={w.persist.ok}>
            {w.persist.ok === null ? "ยังไม่ยืนยันผล" : w.persist.ok === false ? "เขียนไม่ได้ กำลังลองใหม่" : "ปกติ"} · รอเขียน {w.persist.pending}
          </Row>
        )}
      </div>
      </details>
    </article>
  );
}

export function WorkerTab() {
  const q = useQuery<WorkersResponse, ApiError>({ queryKey: ["workers"], queryFn: () => apiGet<WorkersResponse>("/api/v1/workers"), refetchInterval: 10_000 });
  if (q.isPending) return <p>กำลังโหลด…</p>;
  if (q.isError) return <p role="alert" className="text-sm text-[var(--loss)]">{q.error.message ?? "โหลดสถานะ worker ไม่สำเร็จ"}</p>;
  return (
    <div className="space-y-4">
      <p className="text-sm text-[var(--text-secondary)]">
        ตรวจสถานะเครื่องและบัญชีของแต่ละบอต ปุ่มเดียวที่สั่งงานได้คือ HALT/RESUME ซึ่ง Guardian จะทำให้ภายในไม่กี่วินาที
      </p>
      {!q.data.available ? (
        <p data-testid="workers-unavailable">ยังไม่มีตารางสถานะ worker ในฐานข้อมูล (รอ apply migration ของ P3)</p>
      ) : q.data.workers.length === 0 ? (
        <p>ยังไม่มี worker ส่งสถานะมา</p>
      ) : (
        q.data.workers.map((w) => <WorkerCard key={w.account} w={w} />)
      )}
    </div>
  );
}
