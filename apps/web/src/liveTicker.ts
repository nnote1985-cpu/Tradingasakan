import { useEffect, useState } from "react";

// Display only: the browser reads Binance's public market stream directly (no key, no backend),
// because the cloud jobs cannot reach Binance. Nothing here feeds a book or a decision.
export const STREAM_BASE = "wss://stream.binance.com:9443/ws/";
const STALE_MS = 30_000;
const MAX_BACKOFF_MS = 30_000;

export type LiveStatus = "connecting" | "live" | "stale" | "unsupported";
export type LiveTicker = { status: LiveStatus; price: string | null; open24h: string | null; at: number | null };

/** Last price from a `<symbol>@miniTicker` stream; reconnects with backoff and marks the price stale when quiet. */
export function useLiveTicker(stream: string | null): LiveTicker {
  const [t, setT] = useState<LiveTicker>({ status: "connecting", price: null, open24h: null, at: null });
  useEffect(() => {
    if (!stream) return;
    if (typeof WebSocket === "undefined") {
      setT((p) => ({ ...p, status: "unsupported" }));
      return;
    }
    let ws: WebSocket | null = null;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let backoff = 2_000;
    let last = 0;
    let closed = false;
    const open = () => {
      ws = new WebSocket(STREAM_BASE + stream);
      ws.onmessage = (ev) => {
        try {
          const m = JSON.parse(String(ev.data)) as { c?: string; o?: string; E?: number };
          if (!m.c) return;
          last = Date.now();
          backoff = 2_000;
          setT({ status: "live", price: m.c, open24h: m.o ?? null, at: m.E ?? last });
        } catch {
          /* ignore a malformed frame */
        }
      };
      ws.onclose = () => {
        if (closed) return;
        setT((p) => ({ ...p, status: p.price ? "stale" : "connecting" }));
        retry = setTimeout(open, backoff);
        backoff = Math.min(backoff * 2, MAX_BACKOFF_MS);
      };
    };
    open();
    const watch = setInterval(() => {
      if (last && Date.now() - last > STALE_MS) setT((p) => (p.status === "live" ? { ...p, status: "stale" } : p));
    }, 5_000);
    return () => {
      closed = true;
      clearTimeout(retry);
      clearInterval(watch);
      ws?.close();
    };
  }, [stream]);
  return t;
}
