import type { PortfolioRow } from "./api";
import { ModeBadge } from "./ModeBadge";

// Amounts arrive as decimal strings from the ledger; they are only formatted here, never summed.
function money(v: string | null, currency: string) {
  if (v === null) return "–";
  const [int, frac = ""] = v.split(".");
  const grouped = int.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${grouped}.${(frac + "00").slice(0, 2)} ${currency}`;
}

export function PortfolioTable({ rows }: { rows: PortfolioRow[] }) {
  if (rows.length === 0) return <p>ยังไม่มีพอร์ต</p>;
  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="text-left">
          <th>พอร์ต</th>
          <th>โหมด</th>
          <th className="text-right">Trading equity</th>
          <th className="text-right">Economic equity</th>
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
            <td>
              {p.as_of ?? "ยังไม่มีข้อมูล"}
              {p.stale && <span data-testid="stale" className="ml-2 rounded bg-amber-200 px-1 text-amber-950">ข้อมูลเก่า</span>}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
