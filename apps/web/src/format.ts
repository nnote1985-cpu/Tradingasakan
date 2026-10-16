// Display only. Amounts and ratios arrive as decimal strings computed by the API;
// nothing here feeds back into a ledger.

export function money(v: string | null, currency?: string) {
  if (v === null) return "–";
  const neg = v.startsWith("-");
  const [int, frac = ""] = (neg ? v.slice(1) : v).split(".");
  const grouped = int.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${neg ? "-" : ""}${grouped}.${(frac + "00").slice(0, 2)}${currency ? ` ${currency}` : ""}`;
}

/** "0.012345" -> "+1.23%"; signed unless `plain`. */
export function pct(v: string | null, plain = false) {
  if (v === null) return "–";
  const n = Number(v) * 100;
  const s = Math.abs(n).toFixed(2);
  if (plain) return `${s}%`;
  return `${n > 0 ? "+" : n < 0 ? "−" : ""}${s}%`;
}

export function dayOf(iso: string) {
  return iso.slice(0, 10);
}
