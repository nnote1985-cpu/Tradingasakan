import type { WorkerPnl, WorkerStatus } from "./api";

// Display calculations keep API decimal strings exact, including BTC * mark.
function decimal(value: string) {
  if (!/^-?\d+(\.\d+)?$/.test(value)) throw new Error("Invalid decimal");
  const [whole, fraction = ""] = value.split(".");
  return { units: BigInt(whole + fraction), scale: fraction.length };
}
function format(units: bigint, scale: number) {
  const sign = units < 0n ? "-" : "";
  const digits = (units < 0n ? -units : units).toString().padStart(scale + 1, "0");
  return scale ? `${sign}${digits.slice(0, -scale)}.${digits.slice(-scale)}` : `${sign}${digits}`;
}
export function sumAmounts(values: (string | null)[]): string | null {
  if (!values.length || values.some((v) => v === null)) return null;
  const parts = values.map((v) => decimal(v!));
  const scale = Math.max(...parts.map((p) => p.scale));
  return format(parts.reduce((total, p) => total + p.units * 10n ** BigInt(scale - p.scale), 0n), scale);
}
export function difference(current: string | null, initial: string): string | null {
  return current === null ? null : sumAmounts([current, initial.startsWith("-") ? initial.slice(1) : `-${initial}`]);
}
export function accountEquity(p: WorkerPnl | null | undefined): string | null {
  const cash = p?.balances.find((b) => b.asset === "USDT")?.now;
  return cash == null ? null : sumAmounts([cash, btcValue(p)]);
}
export function btcValue(p: WorkerPnl | null | undefined): string | null {
  const btc = p?.balances.find((b) => b.asset === "BTC")?.now;
  if (btc == null) return null;
  if (decimal(btc).units === 0n) return "0";
  if (p?.mark == null) return null;
  const base = decimal(btc), mark = decimal(p.mark);
  return format(base.units * mark.units, base.scale + mark.scale);
}
export function pnlTotals(values: (string | null)[]) {
  const complete = values.length > 0 && values.every((v) => v !== null);
  return {
    net: sumAmounts(values),
    gains: complete ? sumAmounts(["0", ...values.filter((v) => Number(v) > 0)]) : null,
    losses: complete ? sumAmounts(["0", ...values.filter((v) => Number(v) < 0)]) : null,
  };
}
export function uniqueAccounts(workers: WorkerStatus[]) {
  const sorted = [...workers].sort((a, b) => (b.updated_at ?? "").localeCompare(a.updated_at ?? ""));
  const accounts = new Map<string, WorkerStatus>();
  for (const worker of sorted) if (!accounts.has(worker.account)) accounts.set(worker.account, worker);
  return [...accounts.values()];
}
export function isStrategyBook(book: string | null) {
  return book !== null && book !== "tn-conformance-v1";
}
