import { useQueries } from "@tanstack/react-query";
import { type ApiError, apiGet, type EquitySeries, type PortfolioRow, type RiskStatus } from "./api";
import { armOf, type BookGroup } from "./books";
import { dayOf, money, pct } from "./format";
import { Legend, LineChart, type Series } from "./LineChart";

function Signed({ v }: { v: string | null }) {
  const n = v === null ? 0 : Number(v);
  return <span style={{ color: n > 0 ? "var(--gain)" : n < 0 ? "var(--loss)" : undefined }}>{pct(v)}</span>;
}

function ArmTile({ row, risk }: { row: PortfolioRow; risk?: RiskStatus }) {
  const arm = armOf(row.label);
  const costed = row.trading_equity !== null && row.economic_equity !== null && row.trading_equity !== row.economic_equity;
  return (
    <div data-testid="arm-tile" className="rounded-lg border border-[var(--border)] bg-[var(--surface-1)] p-3">
      <div className="flex items-center gap-2 text-sm text-[var(--text-secondary)]">
        <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: arm.color }} />
        {arm.name}
        {row.stale && <span data-testid="stale" className="ml-auto rounded bg-[var(--warn-bg)] px-1 text-xs text-[var(--warn-fg)]">ข้อมูลเก่า</span>}
      </div>
      {risk?.halted && (
        <div data-testid="halted" role="status" className="mt-1 rounded bg-[var(--loss)] px-1.5 py-0.5 text-xs font-semibold text-white">
          หยุด (HALT){risk.halted_since && ` ตั้งแต่ ${dayOf(risk.halted_since)}`}{risk.halt_reason && `: ${risk.halt_reason}`}
        </div>
      )}
      <div className="mt-1 text-lg font-semibold">{money(row.economic_equity, row.currency)}</div>
      {costed && <div className="text-xs text-[var(--text-muted)]">ก่อนหักค่า AI {money(row.trading_equity, row.currency)}</div>}
      <dl className="mt-2 grid grid-cols-3 gap-1 text-xs">
        <div><dt className="text-[var(--text-muted)]">ผลตอบแทน</dt><dd className="font-medium"><Signed v={row.return} /></dd></div>
        <div><dt className="text-[var(--text-muted)]">ลดจากสูงสุด</dt><dd className="font-medium">{row.drawdown === null ? "–" : pct(row.drawdown, true)}</dd></div>
        <div><dt className="text-[var(--text-muted)]">ถือ BTC</dt><dd className="font-medium">{row.exposure_share === null ? "–" : pct(row.exposure_share, true)}</dd></div>
      </dl>
    </div>
  );
}

export function BookGroupCard({ group, risk = [] }: { group: BookGroup; risk?: RiskStatus[] }) {
  const qs = useQueries({
    queries: group.rows.map((r) => ({
      queryKey: ["equity", r.label],
      queryFn: () => apiGet<EquitySeries>(`/api/v1/portfolios/${r.label}/equity`),
      refetchInterval: 60_000,                     // snapshots are daily; a minute is plenty
    })),
  });
  const loaded = qs.map((q) => q.data).filter((d): d is EquitySeries => !!d);
  const failed = qs.find((q) => q.isError)?.error as ApiError | undefined;
  const lines = loaded.map((s) => ({ s, arm: armOf(s.label) }));
  const equity: Series[] = lines.map(({ s, arm }) => ({
    key: s.label, name: arm.name, color: arm.color, dash: arm.dash,
    points: s.points.map((p) => ({ t: dayOf(p.at), v: Number(p.economic_equity), label: money(p.economic_equity) })),
  }));
  const drawdown: Series[] = lines.map(({ s, arm }) => ({
    key: s.label, name: arm.name, color: arm.color, dash: arm.dash,
    points: s.points.map((p) => ({ t: dayOf(p.at), v: -Number(p.drawdown) * 100, label: Number(p.drawdown) ? `−${pct(p.drawdown, true)}` : "0.00%" })),
  }));
  const days = Math.max(0, ...loaded.map((s) => s.points.length));

  return (
    <section data-testid="book-group" className="space-y-3 rounded-xl border border-[var(--border)] bg-[var(--surface-1)] p-4">
      <header className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h2 className="text-lg font-semibold">{group.version}</h2>
        {group.paired && <span className="rounded bg-[var(--surface-0)] px-1.5 text-xs text-[var(--text-secondary)]">ทดลองคู่ Code เทียบ AI</span>}
        <span className="text-xs text-[var(--text-muted)]">
          {group.latest ? `ราคาปิดล่าสุด ${dayOf(group.latest)} (UTC)` : "ยังไม่มีข้อมูล"}{days > 0 && ` · ${days} วัน`}
        </span>
      </header>
      <div className={`grid gap-3 sm:grid-cols-2 ${group.rows.length > 2 ? "xl:grid-cols-4" : ""}`}>
        {group.rows.map((r) => <ArmTile key={r.label} row={r} risk={risk.find((x) => x.label === r.label)} />)}
      </div>
      {failed && <p role="alert" className="text-sm text-[var(--loss)]">{failed.message ?? "โหลดกราฟไม่สำเร็จ"}</p>}
      {loaded.length > 0 && days < 2 && (
        <p data-testid="few-days" className="text-sm text-[var(--text-muted)]">
          มีราคาปิดแล้ว {days} วัน กราฟจะเริ่มแสดงเมื่อมีอย่างน้อย 2 วัน
        </p>
      )}
      {loaded.length > 0 && days >= 2 && (
        <div className="space-y-3">
          {equity.length > 1 && <Legend series={equity} />}
          <div>
            <h3 className="text-sm font-medium text-[var(--text-secondary)]">มูลค่าพอร์ต (USDT หลังหักค่าใช้จ่าย)</h3>
            <LineChart title={`มูลค่าพอร์ต ${group.version}`} series={equity} yTick={(v) => Math.round(v).toLocaleString("en-US")} />
          </div>
          <div>
            <h3 className="text-sm font-medium text-[var(--text-secondary)]">ลดลงจากจุดสูงสุด (%)</h3>
            <LineChart title={`drawdown ${group.version}`} series={drawdown} height={140} zeroLine yTick={(v) => `${Number.isInteger(v) ? v : v.toFixed(1)}%`} />
          </div>
        </div>
      )}
      {group.paired && (
        <a href="#ai" className="inline-block text-sm text-[var(--text-secondary)] underline">ดูการตัดสินใจและสกอร์ของ AI ในแท็บ AI</a>
      )}
    </section>
  );
}
