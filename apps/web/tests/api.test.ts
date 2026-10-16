import { afterEach, expect, test, vi } from "vitest";

vi.mock("../src/config", () => ({ config: { apiBase: "http://localhost:8000", supabaseUrl: "", supabaseKey: "" } }));
const { apiGet } = await import("../src/api");

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

test("reports an unreachable backend", async () => {
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
  await expect(apiGet("/api/v1/overview")).rejects.toThrow("เชื่อมต่อ Backend ไม่ได้");
});

test("a stalled request times out and is aborted", async () => {
  vi.useFakeTimers();
  let signal: AbortSignal | undefined;
  vi.stubGlobal("fetch", vi.fn((_url, options) => {
    signal = options.signal;
    return new Promise(() => {});
  }));
  const check = expect(apiGet("/api/v1/overview")).rejects.toThrow("การเชื่อมต่อหมดเวลา");
  await vi.advanceTimersByTimeAsync(10_000);
  await check;
  expect(signal?.aborted).toBe(true);
});

test("reports non-JSON server failures", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("Bad gateway", { status: 502 })));
  await expect(apiGet("/api/v1/overview")).rejects.toThrow("502");
});
