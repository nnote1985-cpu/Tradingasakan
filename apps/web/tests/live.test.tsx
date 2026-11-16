import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { applyEvent, LiveProvider, type OpenStream, StreamHttpError, useLive, useLiveInterval } from "../src/live/LiveProvider";
import { createSseParser, cursorTime, type SseEvent } from "../src/live/sse";
import { StatusBar, testnetQuote } from "../src/live/StatusBar";

vi.mock("../src/api", () => ({ supabase: null }));
vi.mock("../src/liveTicker", () => ({ useLiveTicker: () => ({ status: "live", price: "95100", open24h: null, at: Date.now() }) }));

afterEach(cleanup);

test("parser joins chunks split anywhere and ignores comments", () => {
  const got: SseEvent[] = [];
  const feed = createSseParser((e) => got.push(e));
  const text = 'retry: 5000\n\n: hi\nid: 1\nevent: hello\ndata: {"a":1}\n\nid: 2\r\nevent: ping\r\ndata: {}\r\n\r\n';
  for (const ch of text) feed(ch);
  expect(got).toEqual([{ id: "1", event: "hello", data: '{"a":1}' }, { id: "2", event: "ping", data: "{}" }]);
});

test("cursor time reads the Postgres timestamp inside a cursor", () => {
  expect(cursorTime("3/2026-11-16 11:00:01.5+00")).toBe(Date.parse("2026-11-16T11:00:01.5Z"));
  expect(cursorTime("0/")).toBeNull();
  expect(cursorTime(undefined)).toBeNull();
});

test("events invalidate only the queries they concern", () => {
  const cache = new QueryClient();
  const spy = vi.spyOn(cache, "invalidateQueries");
  const keys = () => spy.mock.calls.map((c) => JSON.stringify(c[0]?.queryKey));
  applyEvent(cache, { id: "1", event: "changed", data: JSON.stringify({ account: "BOT1", kinds: ["trades", "orders", "risk"], cursors: {} }) });
  expect(keys()).toEqual(['["worker-activity","BOT1"]', '["worker-control","BOT1"]']);
  spy.mockClear();
  applyEvent(cache, { id: "2", event: "changed", data: JSON.stringify({ account: "BOT1", kinds: ["reconcile"] }) });
  expect(keys()).toEqual(['["workers"]']);
  spy.mockClear();
  applyEvent(cache, { id: "3", event: "hello", data: JSON.stringify({ heartbeats: [], cursors: {} }) });
  expect(keys()).toEqual(['["workers"]', '["worker-activity"]', '["worker-control"]', '["bots"]']);
  expect(applyEvent(cache, { id: "4", event: "ping", data: '{"db_ok":false}' })).toEqual({ dbOk: false });
  expect(applyEvent(cache, { id: "5", event: "hello", data: "not json" })).toBeNull();
});

const now = () => new Date().toISOString();
const beat = (account: string, over = {}) => ({ account, updated_at: now(), silent: false, quote_bid: "95000.5", quote_ask: "95000.6",
  quote_age_ms: 500, safety: "RUNNING", mode: "TESTNET", ...over });

test("Testnet quote prefers DEFAULT and skips accounts without one", () => {
  expect(testnetQuote([beat("AI"), beat("DEFAULT", { quote_bid: "1" })])?.bid).toBe("1");
  expect(testnetQuote([beat("DEFAULT", { quote_bid: null }), beat("BOT1")])?.account).toBe("BOT1");
  expect(testnetQuote([])).toBeNull();
});

/** A fake server: each open() gives a new stream the test writes to. */
function fakeServer() {
  const enc = new TextEncoder();
  const streams: ReadableStreamDefaultController<Uint8Array>[] = [];
  let refuse: number | null = null;
  const open: OpenStream = async () => {
    if (refuse) throw new StreamHttpError(refuse);
    return new ReadableStream<Uint8Array>({ start: (c) => { streams.push(c); } });
  };
  const send = (event: string, data: unknown) => act(() => {
    streams.at(-1)!.enqueue(enc.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`));
  });
  return { open, streams, send, refuse: (s: number | null) => { refuse = s; } };
}

function Probe() {
  const live = useLive();
  return <p data-testid="probe">{live.status}:{useLiveInterval(15_000)}</p>;
}

function show(open: OpenStream, cache = new QueryClient()) {
  render(<QueryClientProvider client={cache}><LiveProvider open={open}><StatusBar /><Probe /></LiveProvider></QueryClientProvider>);
  return cache;
}

test("hello makes the bar live, slows polling, and shows workers, Guardian and prices", async () => {
  const srv = fakeServer();
  show(srv.open);
  expect(screen.getByTestId("probe").textContent).toBe("connecting:15000");
  await waitFor(() => expect(srv.streams.length).toBe(1));
  await srv.send("hello", { cursors: {}, heartbeats: [beat("DEFAULT"), beat("AI", { silent: true }), beat("BOT1", { safety: "HALTED" })] });
  expect(screen.getByTestId("probe").textContent).toBe("live:60000");
  expect(screen.getByTestId("live-status").textContent).toContain("ข้อมูลสด");
  expect(screen.getByTestId("live-workers").textContent).toContain("worker 2/3");
  expect(screen.getByTestId("live-workers").textContent).toContain("เงียบ: AI");
  expect(screen.getByTestId("live-guardian").textContent).toContain("BOT1 HALTED");
  const q = screen.getByTestId("live-quote").textContent!;
  expect(q).toContain("BTC Testnet 95,000.50");
  expect(q).toContain("Mainnet 95,100.00");
  expect(q).toContain("ต่าง -0.10%");
  await srv.send("heartbeat", { heartbeats: [beat("DEFAULT", { quote_bid: "95200" })] });
  expect(screen.getByTestId("live-quote").textContent).toContain("95,200.00");
});

test("a dropped stream shows stale and falls back to normal polling, then reconnects and refetches", async () => {
  const srv = fakeServer();
  const cache = show(srv.open);
  const spy = vi.spyOn(cache, "invalidateQueries");
  await waitFor(() => expect(srv.streams.length).toBe(1));
  await srv.send("hello", { cursors: {}, heartbeats: [beat("DEFAULT")] });
  await act(async () => { srv.streams[0].error(new Error("network")); });
  await waitFor(() => expect(screen.getByTestId("probe").textContent).toBe("stale:15000"));
  expect(screen.getByTestId("live-status").textContent).toContain("ข้อมูลค้าง");
  expect(screen.getByTestId("live-quote").textContent).toContain("ค้าง");
  spy.mockClear();
  await waitFor(() => expect(srv.streams.length).toBe(2), { timeout: 4_000 });
  await srv.send("hello", { cursors: {}, heartbeats: [beat("DEFAULT")] });
  expect(screen.getByTestId("probe").textContent).toBe("live:60000");
  expect(spy.mock.calls.map((c) => JSON.stringify(c[0]?.queryKey))).toContain('["worker-activity"]');
});

test("the planned end of a stream reconnects at once without showing stale", async () => {
  const srv = fakeServer();
  show(srv.open);
  await waitFor(() => expect(srv.streams.length).toBe(1));
  await srv.send("hello", { cursors: {}, heartbeats: [] });
  await srv.send("bye", { reason: "max_age" });
  await act(async () => { srv.streams[0].close(); });
  await waitFor(() => expect(srv.streams.length).toBe(2), { timeout: 1_000 });
  expect(screen.getByTestId("probe").textContent).toBe("live:60000");
});

test("a refused stream says why and keeps normal polling", async () => {
  const srv = fakeServer();
  srv.refuse(403);
  show(srv.open);
  await waitFor(() => expect(screen.getByTestId("live-status").textContent).toContain("ไม่มีสิทธิ์อ่านข้อมูลสด"));
  expect(screen.getByTestId("probe").textContent).toBe("connecting:15000");
});
