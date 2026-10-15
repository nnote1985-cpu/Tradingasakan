import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import type { PortfolioRow } from "../src/api";
import { PortfolioTable } from "../src/PortfolioTable";

const row = (over: Partial<PortfolioRow>): PortfolioRow => ({
  label: "pathA-v1.1", mode: "SHADOW", currency: "USDT", initial_equity: "10000", as_of: "2026-10-16T08:20:00+00:00",
  trading_equity: "10012.5", economic_equity: "10012.25", high_water_mark: "10020", quality: "ok", stale: false, ...over,
});

test("shows both equities as formatted decimal strings and flags stale data", () => {
  render(<PortfolioTable rows={[row({}), row({ label: "old", stale: true, trading_equity: "1234567.891", economic_equity: "9890" })]} />);
  expect(screen.getAllByTestId("portfolio-row")).toHaveLength(2);
  expect(screen.getByText("10,012.50 USDT")).toBeTruthy();
  expect(screen.getByText("10,012.25 USDT")).toBeTruthy();
  expect(screen.getByText("1,234,567.89 USDT")).toBeTruthy();
  expect(screen.getAllByTestId("stale")).toHaveLength(1);
});

test("empty list says so", () => {
  render(<PortfolioTable rows={[]} />);
  expect(screen.getByText("ยังไม่มีพอร์ต")).toBeTruthy();
});
