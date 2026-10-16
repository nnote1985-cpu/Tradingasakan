import type { PortfolioRow } from "./api";

// Portfolio labels are "<version>/<arm>" (pathA-v1.2/paper, pathA-p5-r1/ai, ...).
export type Arm = { key: string; name: string; color: string; dash?: string; benchmark: boolean };

const ARMS: Record<string, Omit<Arm, "key">> = {
  paper: { name: "กลยุทธ์", color: "var(--series-strategy)", benchmark: false },
  code: { name: "Code", color: "var(--series-strategy)", benchmark: false },
  ai: { name: "AI", color: "var(--series-ai)", benchmark: false },
  buy_and_hold: { name: "ซื้อถือเฉยๆ", color: "var(--series-benchmark)", dash: "5 4", benchmark: true },
  cash: { name: "เงินสด", color: "var(--series-cash)", dash: "2 4", benchmark: true },
};
const ORDER = ["paper", "code", "ai", "buy_and_hold", "cash"];

export function armOf(label: string): Arm {
  const key = label.includes("/") ? label.slice(label.indexOf("/") + 1) : label;
  return { key, ...(ARMS[key] ?? { name: key, color: "var(--series-strategy)", benchmark: false }) };
}

export type BookGroup = { version: string; rows: PortfolioRow[]; latest: string | null; paired: boolean };

export function groupBooks(rows: PortfolioRow[]): BookGroup[] {
  const by = new Map<string, PortfolioRow[]>();
  for (const r of rows) {
    const v = r.label.includes("/") ? r.label.slice(0, r.label.indexOf("/")) : r.label;
    by.set(v, [...(by.get(v) ?? []), r]);
  }
  const rank = (r: PortfolioRow) => {
    const i = ORDER.indexOf(armOf(r.label).key);
    return i < 0 ? ORDER.length : i;
  };
  const groups = [...by].map(([version, rs]) => ({
    version,
    rows: [...rs].sort((a, b) => rank(a) - rank(b) || a.label.localeCompare(b.label)),
    latest: rs.map((r) => r.as_of).filter((x): x is string => !!x).sort().at(-1) ?? null,
    paired: rs.some((r) => armOf(r.label).key === "ai"),
  }));
  // Most recently updated first; ties by version, newest name first.
  return groups.sort((a, b) => (b.latest ?? "").localeCompare(a.latest ?? "") || b.version.localeCompare(a.version));
}
