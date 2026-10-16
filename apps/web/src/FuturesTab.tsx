import { useQuery } from "@tanstack/react-query";
import { type ApiError, apiGet, type Backtest, type PortfolioRow, type RiskStatusResponse } from "./api";
import { BookGroupCard } from "./BookGroupCard";
import { groupBooks, isFutures } from "./books";
import { money, pct } from "./format";
import { Legend, LineChart, type Series } from "./LineChart";
import { NotOpen } from "./tabs";

const STATUS_TH: Record<string, string> = {
  dev_passed_unconfirmed: "ผ่านช่วง dev แต่ยังไม่ยืนยัน",
  confirmed: "ยืนยันแล้ว",
  failed: "ไม่ผ่าน",
};
const STRAT = { key: "strategy", name: "กลยุทธ์", color: "var(--series-strategy)" };
const BH = { key: "bh", name: "ซื้อถือเฉยๆ", color: "var(--series-benchmark)", dash: "6 4" };

function BacktestCard({ b }: { b: Backtest }) {
  const s = b.summary;
  const equity: Series[] = [
    { ...STRAT, points: b.points.map((p) => ({ t: p.day, v: Number(p.strategy), label: money(p.strategy) })) },
    { ...BH, points: b.points.map((p) => ({ t: p.day, v: Number(p.buy_and_hold), label: money(p.buy_and_hold) })) },
  ];
  const dd = (v: string) => ({ v: -Number(v) * 100, label: Number(v) ? `−${pct(v, true)}` : "0.00%" });
  const drawdown: Series[] = [
    { ...STRAT, points: b.points.map((p) => ({ t: p.day, ...dd(p.strategy_drawdown) })) },
    { ...BH, points: b.points.map((p) => ({ t: p.day, ...dd(p.buy_and_hold_drawdown) })) },
  ];
  return (
    <section data-testid="backtest" className="space-y-3 rounded-xl border border-[var(--border)] bg-[var(--surface-1)] p-4">
      <header className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h2 className="text-lg font-semibold">{b.id} · {b.name}</h2>
        <span className="rounded bg-[var(--warn-bg)] px-1.5 text-xs text-[var(--warn-fg)]">backtest ย้อนหลัง · {STATUS_TH[b.status] ?? b.status}</span>
        <span className="text-xs text-[var(--text-muted)]">{b.instrument} · {b.range[0]} ถึง {b.range[1]} (ช่วง {b.set})</span>
      </header>
      <div className="overflow-x-auto">
        <table className="text-sm [&_td]:py-1 [&_td]:pr-6 [&_th]:pr-6 [&_th]:text-left [&_th]:font-normal [&_th]:text-[var(--text-muted)]">
          <thead><tr><th /><th>ผลตอบแทน</th><th>Sharpe</th><th>ลดจากสูงสุด</th></tr></thead>
          <tbody>
            <tr><td>กลยุทธ์</td><td>{pct(s.net)}</td><td>{s.sharpe}</td><td>{pct(s.max_dd, true)}</td></tr>
            <tr className="text-[var(--text-secondary)]"><td>ซื้อถือเฉยๆ</td><td>{pct(s.bh_net)}</td><td>{s.bh_sharpe}</td><td>{pct(s.bh_max_dd, true)}</td></tr>
          </tbody>
        </table>
      </div>
      <p className="text-xs text-[var(--text-secondary)]">
        ถือ long {s.days_long} วัน, short {s.days_short} วัน, เปิดสถานะ {s.flips} ครั้ง · ค่าธรรมเนียม {money(s.fees, "USDT")} · funding จ่ายสุทธิ {money(s.funding_paid, "USDT")}
      </p>
      <p className="text-sm">{b.note}</p>
      <Legend series={equity} />
      <div>
        <h3 className="text-sm font-medium text-[var(--text-secondary)]">มูลค่าพอร์ต (USDT หลังหักค่าใช้จ่าย)</h3>
        <LineChart title={`มูลค่าพอร์ต ${b.id}`} series={equity} yTick={(v) => Math.round(v).toLocaleString("en-US")} />
      </div>
      <div>
        <h3 className="text-sm font-medium text-[var(--text-secondary)]">ลดลงจากจุดสูงสุด (%)</h3>
        <LineChart title={`drawdown ${b.id}`} series={drawdown} height={140} zeroLine yTick={(v) => `${Math.round(v)}%`} />
      </div>
      <p className="text-xs text-[var(--text-muted)]">แผนและกติกา: {b.plan} (commit {b.commit.slice(0, 7)})</p>
    </section>
  );
}

/** Futures: daily SHADOW books will appear here once they exist; until then, backtests, clearly marked as history. */
export function FuturesTab({ rows }: { rows: PortfolioRow[] }) {
  const books = groupBooks(rows.filter((r) => isFutures(r.label)));
  const risk = useQuery<RiskStatusResponse, ApiError>({ queryKey: ["risk-status"], queryFn: () => apiGet<RiskStatusResponse>("/api/v1/risk-status"), refetchInterval: 30_000, enabled: books.length > 0 });
  const q = useQuery<{ items: Backtest[] }, ApiError>({
    queryKey: ["backtests", "futures"], queryFn: () => apiGet<{ items: Backtest[] }>("/api/v1/backtests?market=futures"), staleTime: 300_000,
  });
  return (
    <div className="space-y-4">
      {books.length === 0 ? (
        <NotOpen title="ยังไม่มีพอร์ต Futures ที่รันทุกวัน">พอร์ตจำลอง R7 กำลังเตรียม ระหว่างนี้ดูผล backtest ย้อนหลังด้านล่างได้</NotOpen>
      ) : (
        <>
          <p className="text-sm text-[var(--text-secondary)]">BTCUSDT perpetual 1x ด้วยเงินสมมติ ราคาปิดรายวันตามเวลา UTC ตัวเลขหลังหักค่าธรรมเนียมและ funding</p>
          {books.map((g) => <BookGroupCard key={g.version} group={g} risk={risk.data?.portfolios} />)}
        </>
      )}
      {q.isError && <p role="alert" className="text-sm text-[var(--loss)]">{q.error.message ?? "โหลดผล backtest ไม่สำเร็จ"}</p>}
      {q.data?.items.map((b) => <BacktestCard key={b.id} b={b} />)}
    </div>
  );
}
