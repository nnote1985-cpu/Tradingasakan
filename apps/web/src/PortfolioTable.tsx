import type { PortfolioRow } from "./api";
import { money, pct } from "./format";
import { ModeBadge } from "./ModeBadge";

// Amounts arrive as decimal strings from the ledger; they are only formatted here, never summed.

export function PortfolioTable({ rows }: { rows: PortfolioRow[] }) {
  if (rows.length === 0) return <p>ยังไม่มีพอร์ต</p>;
  return (
    <table className="w-full text-sm [&_td]:py-1 [&_td]:pr-3 [&_th]:pr-3">
      <thead>
        <tr className="text-left">
          <th>พอร์ต</th>
          <th>โหมด</th>
          <th className="text-right">Trading equity</th>
          <th className="text-right">Economic equity</th>
          <th className="text-right">ผลตอบแทน</th>
          <th className="text-right">ลดจากจุดสูงสุด</th>
          <th>ข้อมูลล่าสุด (UTC)</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((p) => (
          <tr key={p.label} data-testid="portfolio-row">
            <td>{p.label}</td>
            <td><ModeBadge mode={p.mode} /></td>
            <td className="text-right">{money(p.trading_equity, p.currency)}</td>
            <td className="text-right">{money(p.economic_equity, p.currency)}</td>
            <td className="text-right">{pct(p.return)}</td>
            <td className="text-right">{p.drawdown === null ? "–" : pct(p.drawdown, true)}</td>
            <td>
              {p.as_of ?? "ยังไม่มีข้อมูล"}
              {p.stale && <span data-testid="stale" className="ml-2 rounded bg-[var(--warn-bg)] px-1 text-[var(--warn-fg)]">ข้อมูลเก่า</span>}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
