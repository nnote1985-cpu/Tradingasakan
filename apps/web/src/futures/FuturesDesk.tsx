import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { type ApiError, apiGet, type FuturesCap, type FuturesDesk as Desk, type FuturesOrder, type FuturesPosition, type FuturesTrade } from "../api";
import { money, pct } from "../format";
import { OrderTicket, PositionActions, RequestList } from "./OrderTicket";

type Ready = Extract<Desk, { available: true }>;

const tone = (v: string | null | undefined) => (!v ? "" : v.startsWith("-") ? "text-[var(--loss)]" : Number(v) > 0 ? "text-[var(--gain)]" : "");
const SIDE = { LONG: "Long", SHORT: "Short" } as const;
const KIND: Record<FuturesOrder["kind"], string> = { SL: "Stop-loss", TP: "Take-profit", ENTRY: "เข้า", CLOSE: "ปิด", TRAILING: "Trailing stop" };
const LEVEL: Record<FuturesCap["level"], { text: string; cls: string }> = {
  OK: { text: "ปกติ", cls: "status-ok" },
  NEAR: { text: "ใกล้เพดาน", cls: "status-warn" },
  BLOCKED: { text: "ถึงเพดาน ห้ามเปิดไม้ใหม่", cls: "status-bad" },
};

/** Bangkok time for display; the API speaks UTC. */
export function bkk(iso: string | null) {
  if (!iso) return "–";
  return new Date(iso).toLocaleString("th-TH", { timeZone: "Asia/Bangkok", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false });
}

/** Holding time "2 ชม. 5 นาที" / "3 วัน 4 ชม." (display only). */
export function held(from: string, to: string | null, now = Date.now()) {
  const mins = Math.max(0, Math.round(((to ? Date.parse(to) : now) - Date.parse(from)) / 60_000));
  const d = Math.floor(mins / 1440), h = Math.floor((mins % 1440) / 60), m = mins % 60;
  if (d) return `${d} วัน ${h} ชม.`;
  if (h) return `${h} ชม. ${m} นาที`;
  return `${m} นาที`;
}

function Tile({ title, value, sub, cls = "" }: { title: string; value: string; sub?: string; cls?: string }) {
  return (
    <div className="metric">
      <div className="text-xs text-[var(--text-muted)]">{title}</div>
      <div className={`mt-0.5 text-lg font-medium tabular-nums ${cls}`}>{value}</div>
      {sub && <div className="text-xs text-[var(--text-muted)]">{sub}</div>}
    </div>
  );
}

function CapBar({ title, c, detail }: { title: string; c: FuturesCap; detail: string }) {
  const used = Math.min(100, Math.round(Number(c.used) * 100));
  const lv = LEVEL[c.level];
  return (
    <div data-testid={`cap-${title}`} className="rounded-lg border border-[var(--border)] p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium">{title}</span>
        <span className={`rounded px-1.5 text-xs ${lv.cls}`}>{lv.text}</span>
        <span className="ml-auto text-sm tabular-nums">ขาดทุน {pct(c.loss, true)} จากเพดาน {pct(c.cap, true)}</span>
      </div>
      <div className="mt-2 h-2 rounded bg-[var(--surface-0)]" role="meter" aria-label={title} aria-valuemin={0} aria-valuemax={100} aria-valuenow={used}>
        <div className="h-2 rounded" style={{ width: `${used}%`, background: c.level === "OK" ? "var(--gain)" : c.level === "NEAR" ? "var(--warn-fg)" : "var(--loss)" }} />
      </div>
      <p className="mt-1 text-xs text-[var(--text-muted)]">{detail} · ขาดทุนได้อีก {money(c.room, "USDT")} ก่อนถึงเพดาน</p>
    </div>
  );
}

function Position({ p, account, canTrade }: { p: FuturesPosition; account: string; canTrade: boolean }) {
  const long = p.side === "LONG";
  return (
    <div data-testid="position" className={`rounded-lg border p-3 ${p.has_stop ? "border-[var(--border)]" : "border-[var(--loss)]"}`}>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className={`rounded px-2 py-0.5 text-xs font-semibold text-white ${long ? "bg-[var(--gain)]" : "bg-[var(--loss)]"}`}>{SIDE[p.side].toUpperCase()}</span>
        <span className="font-medium">{p.symbol}</span>
        <span className="text-sm tabular-nums">{p.qty} · {p.leverage ? `${p.leverage}x ` : ""}{p.margin_type === "ISOLATED" ? "Isolated" : "Cross"}</span>
        <span className={`ml-auto text-lg font-medium tabular-nums ${tone(p.unrealized_pnl)}`}>{money(p.unrealized_pnl, "USDT")}</span>
      </div>
      <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-sm sm:grid-cols-4 [&_dt]:text-xs [&_dt]:text-[var(--text-muted)] [&_dd]:tabular-nums">
        <div><dt>ราคาเข้า</dt><dd>{money(p.entry_price)}</dd></div>
        <div><dt>ราคา Mark</dt><dd>{money(p.mark_price)}</dd></div>
        <div><dt>Stop-loss</dt><dd className={p.has_stop ? "" : "font-medium text-[var(--loss)]"}>{p.stop_price ? money(p.stop_price) : "ไม่มี"}</dd></div>
        <div><dt>Take-profit</dt><dd>{p.take_profit ? money(p.take_profit) : "–"}</dd></div>
        <div><dt>ราคา Liquidation</dt><dd>{p.liquidation_price ? money(p.liquidation_price) : "–"}{p.liq_distance_pct ? <span className="text-xs text-[var(--text-muted)]"> (ห่าง {pct(p.liq_distance_pct, true)})</span> : null}</dd></div>
        <div><dt>ความเสี่ยงตอนเข้า (ถึง SL)</dt><dd className={p.over_rule ? "text-[var(--loss)]" : ""}>{p.initial_risk ? `${money(p.initial_risk)} (${pct(p.initial_risk_pct ?? null, true)})` : "–"}</dd></div>
        <div><dt>ถ้าโดน SL ตอนนี้</dt><dd className={p.loss_at_stop ? (Number(p.loss_at_stop) > 0 ? "text-[var(--loss)]" : "text-[var(--gain)]") : ""}>{p.loss_at_stop ? `${Number(p.loss_at_stop) > 0 ? "เสีย" : "ได้"} ${money(p.loss_at_stop.replace("-", ""))}` : "–"}</dd></div>
        <div><dt>มูลค่า / Margin</dt><dd>{money(p.notional)} / {money(p.margin)}</dd></div>
      </dl>
      {!p.has_stop && <p role="alert" className="mt-2 text-sm text-[var(--loss)]">ไม่มี Stop-loss บน exchange ผิดกฎ ให้ตั้ง SL หรือปิด position นี้</p>}
      {p.over_rule && <p className="mt-2 text-sm text-[var(--loss)]">ความเสี่ยงตอนเข้าเกิน 1% ของเงินในบัญชี</p>}
      {p.stop_before_liq === false && <p role="alert" className="mt-2 text-sm text-[var(--loss)]">ราคา Liquidation มาก่อน Stop-loss ให้ลด leverage หรือขยับ SL</p>}
      <PositionActions account={account} p={p} canTrade={canTrade} />
    </div>
  );
}

function Orders({ orders }: { orders: FuturesOrder[] }) {
  if (!orders.length) return <p className="text-sm text-[var(--text-muted)]">ไม่มีคำสั่งค้าง</p>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm tabular-nums [&_td]:py-1 [&_td]:pr-4 [&_th]:pr-4 [&_th]:text-left [&_th]:font-normal [&_th]:text-[var(--text-muted)]">
        <thead><tr><th>คู่</th><th>ประเภท</th><th>ฝั่ง</th><th>ราคาทริกเกอร์</th><th>ราคา</th><th>จำนวน</th><th>ตั้งเมื่อ</th></tr></thead>
        <tbody>
          {orders.map((o) => (
            <tr key={`${o.source}-${o.id}`}>
              <td>{o.symbol}</td><td>{KIND[o.kind]}</td><td>{o.side === "BUY" ? "ซื้อ" : "ขาย"}</td>
              <td>{o.trigger_price ? money(o.trigger_price) : "–"}</td><td>{Number(o.price) ? money(o.price) : "Market"}</td>
              <td>{o.close_position ? "ทั้ง position" : o.qty}</td><td>{bkk(o.created_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Trades({ trades }: { trades: FuturesTrade[] }) {
  const [onlyClosed, setOnlyClosed] = useState(false);
  const shown = onlyClosed ? trades.filter((t) => !t.open) : trades;
  if (!trades.length) return <p className="text-sm text-[var(--text-muted)]">ยังไม่มีไม้ในช่วง 90 วันที่ผ่านมา</p>;
  return (
    <div>
      <label className="flex items-center gap-2 text-sm text-[var(--text-secondary)]"><input type="checkbox" checked={onlyClosed} onChange={(e) => setOnlyClosed(e.target.checked)} /> เฉพาะไม้ที่ปิดแล้ว</label>
      <div className="mt-2 max-h-[32rem] overflow-auto">
        <table data-testid="trades" className="w-full text-sm tabular-nums [&_td]:py-1 [&_td]:pr-4 [&_th]:sticky [&_th]:top-0 [&_th]:bg-[var(--surface-1)] [&_th]:pr-4 [&_th]:text-left [&_th]:font-normal [&_th]:text-[var(--text-muted)]">
          <thead><tr><th>เปิด</th><th>คู่</th><th>ฝั่ง</th><th>จำนวน</th><th>ราคาเข้า</th><th>ราคาออก</th><th>ถือ</th><th>ค่าธรรมเนียม</th><th>Funding</th><th>กำไรสุทธิ</th><th>% ของพอร์ต</th></tr></thead>
          <tbody>
            {shown.map((t) => (
              <tr key={`${t.symbol}-${t.opened_at}`}>
                <td>{bkk(t.opened_at)}</td><td>{t.symbol}</td><td>{SIDE[t.side]}</td><td>{t.qty}</td>
                <td>{t.entry_price ? money(t.entry_price) : "–"}</td>
                <td>{t.open ? <span className="rounded bg-[var(--warn-bg)] px-1.5 text-xs text-[var(--warn-fg)]">ยังเปิดอยู่</span> : t.exit_price ? money(t.exit_price) : "–"}</td>
                <td>{held(t.opened_at, t.closed_at)}</td><td>{money(t.fees)}</td><td className={tone(t.funding)}>{money(t.funding)}</td>
                <td className={`font-medium ${tone(t.net_pnl)}`}>{t.open ? <span title="ส่วนที่ปิดไปแล้ว ส่วนที่ยังถือดูที่ position">{money(t.net_pnl)}*</span> : money(t.net_pnl)}</td>
                <td className={tone(t.return_pct)}>{pct(t.return_pct)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-2 text-xs text-[var(--text-muted)]">กำไรสุทธิ = กำไรที่ปิดแล้ว − ค่าธรรมเนียม + funding ระหว่างถือ · * ไม้ที่ยังเปิดนับเฉพาะส่วนที่ปิดไปแล้ว · % เทียบเงินในบัญชีตอนนี้</p>
    </div>
  );
}

function DeskView({ d }: { d: Ready }) {
  const s = d.summary;
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-2 text-sm text-[var(--text-secondary)]">
        <span className="rounded bg-[var(--warn-bg)] px-1.5 text-xs text-[var(--warn-fg)]">Binance Futures Demo · เงินทดลอง</span>
        <span>บัญชี {d.account}</span>
        <span className={d.stale ? "live-stale" : ""}>อัปเดต {bkk(d.synced_at)}{d.stale ? " · ข้อมูลเก่า worker อาจหยุด" : ""}</span>
        <span>{d.worker.orders_enabled ? "worker รับคำสั่งจากหน้านี้" : "อ่านอย่างเดียว (worker ยังไม่เปิดรับคำสั่ง)"}</span>
      </div>
      {d.worker.last_error && <p role="alert" className="notice">อ่านบัญชีรอบล่าสุดไม่สำเร็จ: {d.worker.last_error}</p>}
      {d.unprotected.length > 0 && <p role="alert" className="notice">Position ที่ไม่มี Stop-loss: {d.unprotected.join(", ")}</p>}
      {d.caps?.blocked && <p role="alert" className="notice">ห้ามเปิดไม้ใหม่: {d.caps.reasons.join(" · ")}</p>}

      <section className="panel space-y-3">
        <h2 className="text-lg font-semibold">บัญชีและความเสี่ยง</h2>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          <Tile title="เงินในบัญชี (รวมกำไรที่ยังไม่ปิด)" value={money(d.equity)} sub="USDT" />
          <Tile title="กำไร/ขาดทุนที่ยังไม่ปิด" value={money(d.unrealized_pnl)} cls={tone(d.unrealized_pnl)} />
          <Tile title="ใช้เปิดไม้ใหม่ได้" value={money(d.available_balance)} />
          <Tile title="ความเสี่ยงต่อไม้ตามกฎ" value={d.caps ? money(String(Number(d.equity) * Number(d.rules.risk_per_trade))) : "–"} sub={`${pct(d.rules.risk_per_trade, true)} ของเงินในบัญชี`} />
        </div>
        {d.caps && (
          <div className="grid gap-2 sm:grid-cols-2">
            <CapBar title="ขาดทุนวันนี้" c={d.caps.day} detail={`เทียบเงินตอนเริ่มวัน ${money(d.caps.day.day_open)} (07:00 น.)`} />
            <CapBar title="ขาดทุนรวม" c={d.caps.total} detail={`เทียบจุดสูงสุด ${money(d.caps.total.hwm)}`} />
          </div>
        )}
      </section>

      <section className="panel space-y-3">
        <h2 className="text-lg font-semibold">Position ที่เปิดอยู่ ({d.positions.length})</h2>
        {d.positions.length ? d.positions.map((p) => <Position key={p.symbol} p={p} account={d.account} canTrade={d.can_trade} />) : <p className="text-sm text-[var(--text-muted)]">ไม่มี position เปิดอยู่</p>}
        <details>
          <summary className="cursor-pointer text-sm font-medium">คำสั่งค้าง ({d.orders.length})</summary>
          <div className="mt-2"><Orders orders={d.orders} /></div>
        </details>
      </section>

      <OrderTicket account={d.account} symbols={d.rules.symbols} marks={Object.fromEntries(Object.entries(d.markets).map(([k, v]) => [k, v.mark]))}
        canTrade={d.can_trade} maxLeverage={d.max_leverage} />
      <RequestList requests={d.requests} when={bkk} />

      <section className="panel space-y-3">
        <h2 className="text-lg font-semibold">บันทึกรายไม้</h2>
        <div data-testid="trade-summary" className="grid grid-cols-2 gap-2 sm:grid-cols-5">
          <Tile title="ไม้ที่ปิดแล้ว" value={String(s.closed)} sub={s.open ? `ยังเปิดอยู่ ${s.open} ไม้` : undefined} />
          <Tile title="Win rate" value={s.win_rate === null ? "–" : pct(s.win_rate, true)} sub={`กำไร ${s.wins} · ขาดทุน ${s.losses}`} />
          <Tile title="กำไรสุทธิ (ไม้ที่ปิด)" value={money(s.net_pnl)} cls={tone(s.net_pnl)} sub={`วันนี้ ${money(s.today_net)}`} />
          <Tile title="เฉลี่ยไม้กำไร / ขาดทุน" value={`${s.avg_win ? money(s.avg_win) : "–"} / ${s.avg_loss ? money(s.avg_loss) : "–"}`} />
          <Tile title="ค่าธรรมเนียม · Funding" value={money(s.fees)} sub={`funding ${money(s.funding)}`} />
        </div>
        <Trades trades={d.trades} />
      </section>
    </div>
  );
}

/** The Futures desk: one Binance Futures Demo account, read from what the futures worker stores. */
export function FuturesDesk() {
  const [account, setAccount] = useState<string | null>(null);
  const q = useQuery<Desk, ApiError>({
    queryKey: ["futures-desk", account],
    queryFn: () => apiGet<Desk>(`/api/v1/futures/desk${account ? `?account=${encodeURIComponent(account)}` : ""}`),
    refetchInterval: 10_000,
  });
  if (q.isPending) return <p>กำลังโหลด…</p>;
  if (q.isError) return <div role="alert"><p>{q.error.message ?? "โหลดข้อมูล Futures ไม่สำเร็จ"}</p><button className="action mt-2" onClick={() => void q.refetch()}>ลองใหม่</button></div>;
  const d = q.data;
  if (!d.available) {
    return (
      <div data-testid="not-open" className="panel space-y-2">
        <p className="font-medium">ยังไม่มีข้อมูลบัญชี Futures</p>
        <p className="text-sm text-[var(--text-secondary)]">
          {d.reason === "no_tables" ? "ยังไม่ได้สร้างตาราง Futures ในฐานข้อมูล" : "worker Futures ยังไม่ได้เริ่ม ต้องตั้ง Demo API key จาก demo.binance.com บน VM แล้วเปิด tradingasakan-futures"}
        </p>
        <p className="text-xs text-[var(--text-muted)]">กฎที่หน้านี้จะใช้: เสี่ยงไม่เกิน 1% ต่อไม้ · ขาดทุนวันละไม่เกิน 3% · ขาดทุนรวมไม่เกิน 10% · ทุก position ต้องมี Stop-loss บน exchange</p>
      </div>
    );
  }
  return (
    <div className="space-y-3">
      {d.accounts.length > 1 && (
        <div className="flex gap-2" role="group" aria-label="บัญชี Futures">
          {d.accounts.map((a) => <button key={a} className={`filter-button ${a === d.account ? "filter-active" : ""}`} aria-pressed={a === d.account} onClick={() => setAccount(a)}>{a}</button>)}
        </div>
      )}
      <DeskView d={d} />
    </div>
  );
}
