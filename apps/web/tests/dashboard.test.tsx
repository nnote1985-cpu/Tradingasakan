import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import type { DecisionsResponse, EquitySeries, PortfolioRow } from "../src/api";
import { BookGroupCard } from "../src/BookGroupCard";
import { armOf, groupBooks } from "../src/books";
import { money, pct } from "../src/format";
import { ShadowBanner } from "../src/ShadowBanner";

vi.mock("../src/api", () => ({
  supabase: null,
  apiGet: async (path: string): Promise<EquitySeries | DecisionsResponse> => {
    if (path.endsWith("/decisions")) {
      const base = { current_weight: "0", target_weight: "0.5", reason: null, model_id: "m", latency_ms: 8000, decided_at: "2026-10-16T08:25:00+00:00" };
      return {
        available: true, cost_usd: "0.025000000", limit: 200,
        counts: { filter: { ALLOW: 1, TIMEOUT: 1 }, diagnostic: { VETO: 1 } },
        items: [
          { ...base, proposal_id: "d", kind: "diagnostic", decided_on: "2026-10-15", decision: "VETO", outcome: "VETO", reason_code: "TREND_WEAK", attempt_status: "SUCCESS", error_category: null, cost_usd: "0.012" },
          { ...base, proposal_id: "f", kind: "filter", decided_on: "2026-10-15", decision: "ALLOW", outcome: "ALLOW", reason_code: "TREND_OK", attempt_status: "SUCCESS", error_category: null, cost_usd: "0.013" },
          { ...base, proposal_id: "t", kind: "filter", decided_on: "2026-10-14", decision: "ABSTAIN", outcome: "TIMEOUT", reason_code: "EXPIRED_DECISION", attempt_status: "TIMEOUT", error_category: "TIMEOUT", cost_usd: "0" },
        ],
      };
    }
    const label = path.replace("/api/v1/portfolios/", "").replace("/equity", "");
    const series = {
      label, mode: "SHADOW", currency: "USDT", initial_equity: "10000", max_drawdown: "0.1",
      points: [
        { at: "2026-10-14T23:59:59.999+00:00", trading_equity: "11000", economic_equity: "11000", mark_price: "70000", exposure: "11000", quality: "ok", return: "0.1", drawdown: "0" },
        { at: "2026-10-15T23:59:59.999+00:00", trading_equity: "9900", economic_equity: "9900", mark_price: "63000", exposure: "0", quality: "ok", return: "-0.01", drawdown: "0.1" },
      ],
    };
    return label.startsWith("one-day") ? { ...series, points: series.points.slice(0, 1) } : series;
  },
}));

const row = (label: string, over: Partial<PortfolioRow> = {}): PortfolioRow => ({
  label, mode: "SHADOW", currency: "USDT", initial_equity: "10000", as_of: "2026-10-15T23:59:59.999+00:00",
  trading_equity: "9900", economic_equity: "9900", high_water_mark: null, quality: "ok", stale: false,
  return: "-0.01", drawdown: "0.1", exposure_share: "0", ...over,
});

test("formats decimal strings without float money math", () => {
  expect(money("1234567.891", "USDT")).toBe("1,234,567.89 USDT");
  expect(money("-9.5")).toBe("-9.50");
  expect(pct("0.012345")).toBe("+1.23%");
  expect(pct("-0.1")).toBe("−10.00%");
  expect(pct("0.25", true)).toBe("25.00%");
});

test("groups books by version, strategy first and benchmarks last, newest group first", () => {
  const groups = groupBooks([
    row("pathA-v1.1/buy_and_hold", { as_of: "2026-10-14T23:59:59+00:00" }),
    row("pathA-v1.1/paper", { as_of: "2026-10-14T23:59:59+00:00" }),
    row("pathA-p5-r1/cash"), row("pathA-p5-r1/ai"), row("pathA-p5-r1/buy_and_hold"), row("pathA-p5-r1/code"),
  ]);
  expect(groups.map((g) => g.version)).toEqual(["pathA-p5-r1", "pathA-v1.1"]);
  expect(groups[0].rows.map((r) => armOf(r.label).key)).toEqual(["code", "ai", "buy_and_hold", "cash"]);
  expect(groups[0].paired).toBe(true);
  expect(groups[1].paired).toBe(false);
  expect(armOf("pathA-v1.2/buy_and_hold").benchmark).toBe(true);
});

test("SHADOW banner says simulated money; LIVE is red", () => {
  const { rerender } = render(<ShadowBanner mode="SHADOW" liveEnabled={false} />);
  expect(screen.getByTestId("shadow-banner").textContent).toContain("เงินสมมติ");
  rerender(<ShadowBanner mode="LIVE" liveEnabled />);
  expect(screen.getByTestId("shadow-banner").className).toContain("bg-red-600");
});

test("a book group shows a tile per arm and both charts", async () => {
  const [g] = groupBooks([row("pathA-v1.2/paper"), row("pathA-v1.2/buy_and_hold")]);
  render(<QueryClientProvider client={new QueryClient()}><BookGroupCard group={g} /></QueryClientProvider>);
  expect(screen.getAllByTestId("arm-tile")).toHaveLength(2);
  await waitFor(() => expect(screen.getByRole("img", { name: "มูลค่าพอร์ต pathA-v1.2" })).toBeTruthy());
  expect(screen.getByRole("img", { name: "drawdown pathA-v1.2" })).toBeTruthy();
  expect(screen.getAllByText("ซื้อถือเฉยๆ").length).toBeGreaterThan(0);
});

test("a halted book is marked HALT with its date and reason", () => {
  const [g] = groupBooks([row("pathA-v1.2/paper"), row("pathA-v1.2/buy_and_hold")]);
  render(<QueryClientProvider client={new QueryClient()}>
    <BookGroupCard group={g} risk={[{ label: "pathA-v1.2/paper", halted: true, halted_since: "2026-10-15T09:00:00+00:00", halt_reason: "drawdown 25%" }]} />
  </QueryClientProvider>);
  const halted = screen.getAllByTestId("halted");
  expect(halted).toHaveLength(1);
  expect(halted[0].textContent).toContain("2026-10-15");
  expect(halted[0].textContent).toContain("drawdown 25%");
});

test("the AI arm lists its decisions; diagnostic answers are marked as having no effect and a timeout as an error", async () => {
  const [g] = groupBooks([row("pathA-p5-r1/code"), row("pathA-p5-r1/ai")]);
  render(<QueryClientProvider client={new QueryClient()}><BookGroupCard group={g} /></QueryClientProvider>);
  await waitFor(() => expect(screen.getAllByTestId("decision-row")).toHaveLength(3));
  const rows = screen.getAllByTestId("decision-row");
  expect(rows[0].textContent).toContain("ความเห็น (ไม่มีผล)");
  expect(rows[2].textContent).toContain("ผิดพลาด");
  expect(screen.getByText("$0.0250")).toBeTruthy();
});

test("with a single close the charts wait and say why", async () => {
  const [g] = groupBooks([row("one-day/paper")]);
  const { container } = render(<QueryClientProvider client={new QueryClient()}><BookGroupCard group={g} /></QueryClientProvider>);
  await waitFor(() => expect(container.querySelector("[data-testid=few-days]")?.textContent).toContain("1 วัน"));
  expect(container.querySelector("svg[role=img]")).toBeNull();
});
