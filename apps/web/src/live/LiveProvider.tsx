import { type QueryClient, useQueryClient } from "@tanstack/react-query";
import { createContext, type ReactNode, useContext, useEffect, useState } from "react";
import { supabase } from "../api";
import { config } from "../config";
import { createSseParser, cursorTime, type SseEvent } from "./sse";

// One stream for the whole app (m1-realtime-plan.md §3.2). The stream only says what changed;
// the data still comes from the REST endpoints, so a dropped stream never loses or repeats a row.

export type LiveStatus = "off" | "connecting" | "live" | "stale";
export type LiveHeartbeat = {
  account: string; updated_at: string | null; silent: boolean; quote_bid: string | null; quote_ask: string | null;
  quote_age_ms: number | null; safety: string | null; mode: string | null;
};
export type LiveState = {
  status: LiveStatus;
  heartbeats: LiveHeartbeat[] | null;   // null until the first hello
  heartbeatsAt: number | null;          // when the summary last arrived (ms)
  dbOk: boolean;
  error: string | null;
};

export const STALE_MS = 30_000;          // nothing (not even a ping) for this long = stale
export const LIVE_POLL_MS = 60_000;      // fallback polling while the stream is healthy
const MAX_BACKOFF_MS = 30_000;

const OFF: LiveState = { status: "off", heartbeats: null, heartbeatsAt: null, dbOk: true, error: null };
const LiveContext = createContext<LiveState>(OFF);

export const useLive = () => useContext(LiveContext);

/** The polling interval for a query that the stream refreshes: slower while live, the old interval otherwise. */
export function useLiveInterval(normalMs: number): number {
  return useLive().status === "live" ? Math.max(normalMs, LIVE_POLL_MS) : normalMs;
}

export type OpenStream = (signal: AbortSignal) => Promise<ReadableStream<Uint8Array>>;

export class StreamHttpError extends Error {
  constructor(public status: number) { super(`stream ${status}`); }
}

export const openApiStream: OpenStream = async (signal) => {
  const session = supabase ? (await supabase.auth.getSession()).data.session : null;
  const res = await fetch(`${config.apiBase}/api/v1/stream`, {
    headers: { Accept: "text/event-stream", ...(session ? { Authorization: `Bearer ${session.access_token}` } : {}) },
    signal, cache: "no-store",
  });
  if (!res.ok || !res.body) throw new StreamHttpError(res.status);
  return res.body;
};

const KIND_QUERIES: Record<string, string> = {
  orders: "worker-activity", trades: "worker-activity",
  decisions: "worker-control", risk: "worker-control", requests: "worker-control",
};

export function applyEvent(cache: QueryClient, e: SseEvent, now = Date.now()): Partial<LiveState> | null {
  let data: Record<string, unknown>;
  try {
    data = JSON.parse(e.data) as Record<string, unknown>;
  } catch {
    return null;
  }
  if (e.event === "hello") {
    for (const key of ["workers", "worker-activity", "worker-control", "bots"]) void cache.invalidateQueries({ queryKey: [key] });
    return { heartbeats: data.heartbeats as LiveHeartbeat[], heartbeatsAt: now, dbOk: true };
  }
  if (e.event === "heartbeat") {
    void cache.invalidateQueries({ queryKey: ["workers"] });
    return { heartbeats: data.heartbeats as LiveHeartbeat[], heartbeatsAt: now };
  }
  if (e.event === "changed") {
    const account = String(data.account);
    const kinds = (data.kinds as string[]) ?? [];
    const keys = new Set(kinds.map((k) => KIND_QUERIES[k] ?? "workers"));
    for (const key of keys) void cache.invalidateQueries({ queryKey: key === "workers" ? [key] : [key, account] });
    if (import.meta.env.DEV && kinds.includes("trades")) {
      const at = cursorTime((data.cursors as Record<string, string> | undefined)?.trades);
      if (at !== null) console.info(`[live] fill ${account}: received_at → screen ${now - at} ms`);
    }
    return {};
  }
  if (e.event === "ping") return { dbOk: data.db_ok !== false };
  return {};
}

export function LiveProvider({ children, open = openApiStream }: { children: ReactNode; open?: OpenStream }) {
  const cache = useQueryClient();
  const [state, setState] = useState<LiveState>({ ...OFF, status: "connecting" });
  useEffect(() => {
    let stopped = false;
    let ctrl: AbortController | null = null;
    let last = 0;
    let backoff = 2_000;
    let wake: (() => void) | null = null;
    const sleep = (ms: number) => new Promise<void>((resolve) => {
      const t = setTimeout(resolve, ms);
      wake = () => { clearTimeout(t); resolve(); };
    });
    const run = async () => {
      while (!stopped) {
        ctrl = new AbortController();
        let bye = false;
        try {
          const reader = (await open(ctrl.signal)).getReader();
          const decoder = new TextDecoder();
          const feed = createSseParser((e) => {
            last = Date.now();
            backoff = 2_000;
            if (e.event === "bye") bye = true;   // the server ends every stream after 10 minutes
            const patch = applyEvent(cache, e, last);
            if (patch) setState((s) => ({ ...s, ...patch, status: "live", error: null }));
          });
          for (;;) {
            const { done, value } = await reader.read();
            if (done) break;
            feed(decoder.decode(value, { stream: true }));
          }
        } catch (err) {
          if (stopped) return;
          const msg = err instanceof StreamHttpError && (err.status === 401 || err.status === 403)
            ? "ไม่มีสิทธิ์อ่านข้อมูลสด" : null;
          setState((s) => ({ ...s, error: msg }));
        }
        if (stopped) return;
        if (bye) {                           // planned end: reconnect at once with a fresh token
          await sleep(200);
          continue;
        }
        setState((s) => ({ ...s, status: s.heartbeats ? "stale" : "connecting" }));
        await sleep(backoff);
        backoff = Math.min(backoff * 2, MAX_BACKOFF_MS);
      }
    };
    void run();
    const watch = setInterval(() => {
      if (last && Date.now() - last > STALE_MS) {
        last = 0;
        setState((s) => ({ ...s, status: "stale" }));
        ctrl?.abort();                       // a silent connection: drop it and reconnect
      }
    }, 5_000);
    return () => {
      stopped = true;
      clearInterval(watch);
      ctrl?.abort();
      (wake as (() => void) | null)?.();
    };
  }, [cache, open]);
  return <LiveContext.Provider value={state}>{children}</LiveContext.Provider>;
}
