import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import type { CompareBook, PaperCompare as Compare } from "../src/api";
import { PaperCompare, positionText } from "../src/PaperCompare";
// Real output of /api/v1/paper-compare?from=2026-10-16 for the 2026-11-17 closes.
import earliest from "./fixtures/paper-compare-2026-11-17.json";

const calls: string[] = [];
function windowFrom(from: string): Compare {
  // The default window: every book measured from 3a's open day. Only the shape matters here.
  if (from === "2026-10-16") return earliest as Compare;
  return {
    ...(earliest as Compare), from,
    books: (earliest as Compare).books.map((b) => {
      const last = b.points.at(-1)!;
      const ret = b.label === "pathA-r10a-hedge/paper" ? last.return : "0.000000";
      return { ...b, return: ret, drawdown: ret === "0.000000" ? "0.000000" : String(-Number(ret)), points: [{ ...last, day: "2026-11-17", return: ret }] };
    }),
  };
}

vi.mock("../src/api", () => ({
  supabase: null,
  apiGet: async (path: string): Promise<unknown> => {
    calls.push(path);
    const from = new URL(path, "http://x").searchParams.get("from");
    return windowFrom(from ?? "2026-11-17");
  },
}));

afterEach(() => { cleanup(); calls.length = 0; });

function renderPage() {
  return render(<QueryClientProvider client={new QueryClient()}><PaperCompare /></QueryClientProvider>);
}

test("3a against R10a from 3a's open day: headline, positions, funding and fees", async () => {
  const { container } = renderPage();
  const headline = await screen.findByTestId("compare-headline");
  expect(calls[0]).toBe("/api/v1/paper-compare");
  expect(headline.textContent).toContain("3a −0.09%");
  expect(headline.textContent).toContain("R10a 0.00%");
  expect(headline.textContent).toContain("3a ตาม R10a 0.09 จุด");
  expect(headline.textContent).toContain("3a ตอนนี้ spot 51% · short 51%");
  expect(headline.textContent).toContain("R10a ตอนนี้ เงินสด 100%");
  expect(screen.getByTestId("few-days").textContent).toContain("1 วัน");

  const rows = [...container.querySelectorAll("[data-testid=compare-table] tbody tr")].map((r) => r.textContent ?? "");
  expect(rows[0]).toContain("3a · R10a + short hedge");
  expect(rows[0]).toContain("9,990.64");
  expect(rows[0]).toContain("รับ 0.49");
  expect(rows[0]).toContain("7.50");
  expect(rows[1]).toContain("R10a");
  expect(rows[1]).toContain("−16.12%");                     // since it opened on 10-17
  expect(rows.at(-1)).toContain("ซื้อถือ BTC");
  expect(rows.find((r) => r.includes("Futures R7"))).toContain("จ่าย 52.68");
  expect(rows.find((r) => r.includes("Futures R7"))).toContain("short 79%");
});

test("switching to the earliest window draws the charts and lets other books join", async () => {
  renderPage();
  fireEvent.click(await screen.findByRole("button", { name: /ตั้งแต่บัญชีแรกเปิด/ }));
  await waitFor(() => expect(screen.getByRole("img", { name: "ผลตอบแทนเทียบกัน" })).toBeTruthy());
  expect(calls.at(-1)).toBe("/api/v1/paper-compare?from=2026-10-16");
  // 3a opened long after this start, so no lead/lag figure is claimed
  expect(screen.getByTestId("compare-headline").textContent).toContain("เทียบผลตอบแทนกับ R10a ตรงๆ ไม่ได้");
  expect(screen.getByRole("button", { name: /ตั้งแต่บัญชีแรกเปิด/ }).getAttribute("aria-pressed")).toBe("true");
  const box = screen.getByRole("checkbox", { name: "Futures R7" }) as HTMLInputElement;
  fireEvent.click(box);
  expect(box.checked).toBe(true);
});

test("position text covers cash, spot, short and missing marks", () => {
  const b = (spot: string | null, perp: string | null) => ({ spot_share: spot, perp_share: perp }) as CompareBook;
  expect(positionText(b("0.000000", "0.000000"))).toBe("เงินสด 100%");
  expect(positionText(b("0.509672", "-0.509914"))).toBe("spot 51% · short 51%");
  expect(positionText(b("1.000000", "0.000000"))).toBe("spot 100%");
  expect(positionText(b(null, "0.000000"))).toBe("–");
});
