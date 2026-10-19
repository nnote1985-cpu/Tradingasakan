import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { AiTab } from "../src/AiTab";
import type { DecisionsResponse, EquitySeries, PortfolioRow } from "../src/api";
import { Dashboard } from "../src/App";
import { FuturesTab } from "../src/FuturesTab";
import { BookGroupCard } from "../src/BookGroupCard";
import { armOf, groupBooks, isFutures } from "../src/books";
import { money, pct } from "../src/format";
import { ShadowBanner } from "../src/ShadowBanner";

vi.mock("../src/api", () => ({
  supabase: null,
  apiGet: async (path: string): Promise<unknown> => {
    if (path === "/api/v1/overview") return { mode: "SHADOW", portfolios: [row("pathA-p5-r1/code"), row("pathA-p5-r1/ai")] };
    if (path === "/api/v1/risk-status") return { available: false, portfolios: [] };
    if (path === "/api/v1/workers") return { available: true, workers: [] };
    if (path === "/api/v1/bot-profile-drafts") return { items: [], can_edit: false };
    if (path === "/api/v1/backtests?market=futures") {
      const p = (day: string, s: string, b: string, sd: string, bd: string) => ({ day, strategy: s, buy_and_hold: b, strategy_drawdown: sd, buy_and_hold_drawdown: bd });
      return {
        items: [{
          id: "R7", name: "Long/short", market: "futures", instrument: "BTCUSDT perpetual 1x", status: "dev_passed_unconfirmed",
          plan: "docs/research/r7.md", commit: "f64ef0084e1c", set: "development", range: ["2022-04-12", "2026-01-01"], initial_equity: "10000",
          summary: { net: "1.033688", sharpe: "0.43", max_dd: "0.549605", bh_net: "1.214954", bh_sharpe: "0.42", bh_max_dd: "0.619679",
            flips: 57, days_long: 757, days_short: 603, fees: "903.89", funding_paid: "3478.14", log_return_long_days: "0.787719", log_return_short_days: "-0.077869", passes: true },
          note: "ยังไม่ได้ยืนยัน",
          points: [p("2022-04-11", "10000.00", "10000.00", "0", "0"), p("2022-04-12", "9850.05", "10128.35", "0.014995", "0")],
          trades: [
            { n: 1, side: "short", entry_day: "2022-04-12", entry_price: "39497.70", qty: "0.25317931", exit_day: "2022-04-20", exit_price: "41481.19", open: false, days: 8, pnl: "-513.09", return: "-0.051309" },
            { n: 2, side: "long", entry_day: "2022-04-20", entry_price: "41481.19", qty: "0.22870381", exit_day: null, exit_price: null, open: true, days: 2, pnl: "120.00", return: "0.012648" },
          ],
          trade_stats: { count: 2, wins: 1, long_pnl: "120.00", short_pnl: "-513.09",
            closed: 1, closed_wins: 0, win_rate: "0.000000", closed_pnl: "-513.09", open_pnl: "120.00", net_pnl: "-393.09" },
        }],
      };
    }
    if (path === "/api/v1/bot-profiles") {
      return {
        items: [{
          profile_id: "btc-macd-rsi", version: 1, name: "BTC MACD with trend filter", status: "draft", instrument: "BTCUSDT_SPOT", decision_clock: "1d",
          combine: "main_with_filters", risk_profile: "spot.v1", ai_mode: "off", notes: "", hash: "abcdef0123456789",
          strategies: [
            { number: 1, template_id: "macd_cross", version: "1.0", name: "MACD Crossover", timeframe: "4h", params: { fast: 12, slow: 26, signal: 9 } },
            { number: 7, template_id: "sma_regime", version: "1.0", name: "SMA trend regime", timeframe: "1d", params: { sma: 100 } },
          ],
        }],
      };
    }
    if (path === "/api/v1/strategies") {
      return {
        items: [{ number: 1, template_id: "macd_cross", version: "1.0", name: "MACD Crossover", family: "trend", timeframes: ["1h", "4h", "1d"], default_timeframe: "4h",
          summary: "Long while MACD is above signal.", params: { fast: { default: 12, min: 5, max: 20 } }, evidence: "untested" }],
      };
    }
    if (path === "/api/v1/ai/settings") {
      return {
        experiment_id: "pathA-p5-r1", graph_version: "g", locked: true, capability_stage: "FILTER_ONLY", model_id: "claude-opus-5-5", effort: "medium",
        prompt_version: "pv1", prompt_hash: "4564016eabcdef", policy_version: "pol1", context_profile: "quant_only", tools: [], timeout_ms: 120000,
        max_attempts: 1, max_run_cost_usd: "0.50", daily_cost_cap_usd: "1.00", monthly_cap_usd: "5", timebox_days: 28,
      };
    }
    if (path.endsWith("/ai-scorecard")) {
      return {
        available: true, horizon_days: 7, diagnostic_only: true,
        counts: { filter: { avoided_loss: 1, missed_gain: 1, pending: 1 }, diagnostic: { allowed_gain: 1 } },
        items: [
          { proposal_id: "a", kind: "filter", decided_on: "2026-10-01", outcome: "VETO", forward_return: "-0.05", verdict: "avoided_loss" },
          { proposal_id: "b", kind: "filter", decided_on: "2026-10-02", outcome: "TIMEOUT", forward_return: "0.03", verdict: "missed_gain" },
          { proposal_id: "c", kind: "diagnostic", decided_on: "2026-10-02", outcome: "ALLOW", forward_return: "0.03", verdict: "allowed_gain" },
          { proposal_id: "d", kind: "filter", decided_on: "2026-10-15", outcome: "ALLOW", forward_return: null, verdict: "pending" },
        ],
      };
    }
    if (path.endsWith("/decisions")) {
      const base = { current_weight: "0", target_weight: "0.5", reason: null, model_id: "m", latency_ms: 8000, decided_at: "2026-10-16T08:25:00+00:00" };
      return {
        available: true, cost_usd: "0.025000000", limit: 200,
        counts: { filter: { ALLOW: 1, TIMEOUT: 1 }, diagnostic: { VETO: 1 } },
        items: [
          { ...base, proposal_id: "d", kind: "diagnostic", decided_on: "2026-10-15", decision: "VETO", outcome: "VETO", reason_code: "TREND_WEAK", attempt_status: "SUCCESS", error_category: null, cost_usd: "0.012" },
          { ...base, proposal_id: "a", kind: "filter", decided_on: "2026-10-15", decision: "ALLOW", outcome: "ALLOW", reason_code: "TREND_OK", reason: "ราคายืนเหนือเส้นเฉลี่ย 50 วัน", attempt_status: "SUCCESS", error_category: null, cost_usd: "0.013" },
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

function row(label: string, over: Partial<PortfolioRow> = {}): PortfolioRow {
  return {
  label, mode: "SHADOW", currency: "USDT", initial_equity: "10000", as_of: "2026-10-15T23:59:59.999+00:00",
  trading_equity: "9900", economic_equity: "9900", high_water_mark: null, quality: "ok", stale: false,
  return: "-0.01", drawdown: "0.1", exposure_share: "0", ...over,
  };
}

afterEach(cleanup);   // unmount, so no query keeps rendering after jsdom is torn down

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

test("testnet banner explains exchange orders use test funds and scopes the API mode", () => {
  const { container } = render(<ShadowBanner mode="TESTNET" liveEnabled={false} />);
  expect(container.textContent).toContain("โหมด API: TESTNET");
  expect(container.textContent).toContain("ส่งคำสั่งไป Binance Testnet ด้วยเงินทดสอบ");
  expect(container.textContent).not.toContain("ไม่มีคำสั่งซื้อขายจริง");
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

test("the AI tab lists decisions; diagnostic answers are marked as having no effect and a timeout as an error", async () => {
  render(<QueryClientProvider client={new QueryClient()}><AiTab rows={[row("pathA-p5-r1/code"), row("pathA-p5-r1/ai")]} /></QueryClientProvider>);
  await waitFor(() => expect(screen.getAllByTestId("decision-row")).toHaveLength(3));
  const rows = screen.getAllByTestId("decision-row");
  expect(rows[0].textContent).toContain("ความเห็น (ไม่มีผล)");
  expect(rows[2].textContent).toContain("ผิดพลาด");
  expect(rows[2].textContent).toContain("ไม่ได้คำตอบ (TIMEOUT)");
  expect(rows[1].textContent).toContain("ราคายืนเหนือเส้นเฉลี่ย 50 วัน");
  await waitFor(() => expect(within(rows[1]).getByTestId("verdict").textContent).toBe("ช่วย: เลี่ยงขาดทุน"));
  expect(rows[1].textContent).toContain("BTC 7 วันถัดมา −5.00%");
  expect(within(rows[0]).queryByTestId("verdict")).toBeNull();   // diagnostic answers are not scored as help
  expect(screen.getByText("$0.0250")).toBeTruthy();
});

test("the AI tab shows locked settings and the helped-or-missed scorecard, counting filter answers only", async () => {
  const { container } = render(<QueryClientProvider client={new QueryClient()}><AiTab rows={[row("pathA-p5-r1/ai")]} /></QueryClientProvider>);
  await waitFor(() => expect(container.querySelector("[data-testid=ai-settings]")?.textContent).toContain("ล็อกไว้ตลอดรอบทดลอง"));
  expect(container.querySelector("[data-testid=ai-settings]")?.textContent).toContain("$5");
  await waitFor(() => expect(container.querySelectorAll("[data-testid=score-row]")).toHaveLength(4));
  const card = container.querySelector("[data-testid=ai-scorecard]")!;
  expect(card.textContent).toContain("ไม่มีผลกับพอร์ต");
  const stats = [...card.querySelectorAll(".text-lg")].map((e) => e.textContent);
  expect(stats).toEqual(["1", "1", "1"]);   // the diagnostic allowed_gain is not counted as help
  const rows = container.querySelectorAll("[data-testid=score-row]");
  expect(rows[0].textContent).toContain("−5.00%");
  expect(rows[1].textContent).toContain("TIMEOUT (ไม่เพิ่ม)");
  expect(rows[3].textContent).toContain("รอผล");
});

test("five main sections separate operations, research and settings and preserve subview hashes", async () => {
  window.location.hash = "";
  const { container } = render(<QueryClientProvider client={new QueryClient()}><Dashboard /></QueryClientProvider>);
  expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual(["ภาพรวม", "บอตและพอร์ต", "งานวิจัย", "ระบบ", "ตั้งค่า"]);
  await screen.findByText("ยังไม่มีบอตรายงานสถานะ");
  expect(container.querySelectorAll("[data-testid=arm-tile]")).toHaveLength(0);
  fireEvent.click(screen.getByRole("tab", { name: "งานวิจัย" }));
  expect(screen.queryByRole("button", { name: "คลังกลยุทธ์" })).toBeNull();
  await waitFor(() => expect(container.querySelectorAll("[data-testid=arm-tile]")).toHaveLength(2));
  expect(window.location.hash).toBe("#research");
  fireEvent.click(screen.getByRole("button", { name: "Futures / Backtest" }));
  expect(container.querySelector("[data-testid=not-open]")?.textContent).toContain("Futures");
  await waitFor(() => expect(container.querySelector("[data-testid=backtest]")?.textContent).toContain("ผ่านช่วง dev แต่ยังไม่ยืนยัน"));
  expect(container.querySelector("[data-testid=backtest]")?.textContent).toContain("+103.37%");
  expect(screen.getByRole("img", { name: "มูลค่าพอร์ต R7" })).toBeTruthy();
  const tiles = [...(container.querySelector("[data-testid=trade-summary]")?.children ?? [])].map((e) => e.textContent);
  expect(tiles).toEqual([
    "ไม้ทั้งหมด2ยังถืออยู่ 1 ไม้", "ปิดกำไร0", "ปิดขาดทุน1", "Win rate0.00%จาก 1 ไม้ที่ปิดแล้ว",
    "กำไรสุทธิ-393.09ปิดแล้ว -513.09 · ยังถือ 120.00",
  ]);
  const open = container.querySelector("[data-testid=open-trade]")?.textContent;
  expect(open).toContain("LONG");
  expect(open).toContain("41,481.19");
  expect(open).toContain("120.00 USDT (+1.26%)");
  expect(open).toContain("ตีราคาที่ราคาปิด 2022-04-12");
  const trades = container.querySelector("[data-testid=trades]");
  expect(trades?.querySelector("summary")?.textContent).toContain("รายไม้ 2 ไม้ · กำไร 1 ไม้ · ขาดทุน 1 ไม้");
  expect([...(trades?.querySelectorAll("tbody tr") ?? [])].map((r) => r.textContent)).toEqual([
    "1Short2022-04-1239,497.702022-04-2041,481.198-513.09−5.13%",
    "2Long2022-04-2041,481.19ยังถืออยู่ (ตีราคาสิ้นช่วง)–2120.00+1.26%",
  ]);
  fireEvent.click(screen.getByRole("button", { name: "ผลทดลอง AI" }));
  await waitFor(() => expect(container.querySelector("[data-testid=ai-settings]")).not.toBeNull());
  expect(screen.getByRole("tab", { name: "งานวิจัย" }).getAttribute("aria-selected")).toBe("true");
  expect(screen.getByRole("button", { name: "ผลทดลอง AI" }).getAttribute("aria-pressed")).toBe("true");
});

test("with a single close the charts wait and say why", async () => {
  const [g] = groupBooks([row("one-day/paper")]);
  const { container } = render(<QueryClientProvider client={new QueryClient()}><BookGroupCard group={g} /></QueryClientProvider>);
  await waitFor(() => expect(container.querySelector("[data-testid=few-days]")?.textContent).toContain("1 วัน"));
  expect(container.querySelector("svg[role=img]")).toBeNull();
});

test("futures books show in the Futures tab above the backtests, and not in Spot", async () => {
  const rows = [row("futures-r7/paper"), row("futures-r7/buy_and_hold"), row("pathA-v1.2/paper")];
  const { container } = render(<QueryClientProvider client={new QueryClient()}><FuturesTab rows={rows} /></QueryClientProvider>);
  expect(container.querySelectorAll("[data-testid=book-group]")).toHaveLength(1);
  expect(container.querySelector("[data-testid=book-group] h2")?.textContent).toBe("futures-r7");
  expect(container.querySelector("[data-testid=not-open]")).toBeNull();
  await waitFor(() => expect(container.querySelector("[data-testid=backtest]")).not.toBeNull());
  expect(groupBooks(rows.filter((r) => !isFutures(r.label))).map((g) => g.version)).toEqual(["pathA-v1.2"]);
});

test("settings separates read-only profiles from the strategy library", async () => {
  window.location.hash = "settings";
  const { container } = render(<QueryClientProvider client={new QueryClient()}><Dashboard /></QueryClientProvider>);
  const view = within(container);
  expect(view.getByRole("tab", { name: "ตั้งค่า" }).getAttribute("aria-selected")).toBe("true");
  expect(view.getByText(/ยังไม่เชื่อม worker, Backtest หรือการส่งคำสั่งเทรด/)).toBeTruthy();
  const card = await view.findByTestId("bot-profile");
  expect(card.textContent).toContain("#1, #7");
  expect(card.textContent).toContain("(หลัก)");
  expect(card.textContent).toContain("(ตัวกรอง)");
  expect(card.textContent).toContain("ร่าง ยังไม่ผูกกับพอร์ต");
  expect(view.queryByTestId("strategy-library")).toBeNull();
  fireEvent.click(view.getByRole("button", { name: "คลังกลยุทธ์" }));
  expect(window.location.hash).toBe("#strategies");
  expect(view.getByRole("tab", { name: "ตั้งค่า" }).getAttribute("aria-selected")).toBe("true");
  expect(view.queryByTestId("bot-profile")).toBeNull();
  const lib = await view.findByTestId("strategy-library");
  expect(lib.textContent).toContain("MACD Crossover");
  expect(lib.textContent).toContain("ยังไม่ทดสอบกับข้อมูลเรา");
  fireEvent.click(view.getByRole("button", { name: "Bot profiles" }));
  expect(window.location.hash).toBe("#settings");
  expect(await view.findByTestId("bot-profile")).toBeTruthy();
  window.location.hash = "";
});
