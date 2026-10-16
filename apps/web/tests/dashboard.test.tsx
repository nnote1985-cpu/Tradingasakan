import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import type { EquitySeries, PortfolioRow } from "../src/api";
import { BookGroupCard } from "../src/BookGroupCard";
import { armOf, groupBooks } from "../src/books";
import { money, pct } from "../src/format";
import { ShadowBanner } from "../src/ShadowBanner";

vi.mock("../src/api", () => ({
  supabase: null,
  apiGet: async (path: string): Promise<EquitySeries> => {
    const label = path.replace("/api/v1/portfolios/", "").replace("/equity", "");
    return {
      label, mode: "SHADOW", currency: "USDT", initial_equity: "10000", max_drawdown: "0.1",
      points: [
        { at: "2026-10-14T23:59:59.999+00:00", trading_equity: "11000", economic_equity: "11000", mark_price: "70000", exposure: "11000", quality: "ok", return: "0.1", drawdown: "0" },
        { at: "2026-10-15T23:59:59.999+00:00", trading_equity: "9900", economic_equity: "9900", mark_price: "63000", exposure: "0", quality: "ok", return: "-0.01", drawdown: "0.1" },
      ],
    };
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
