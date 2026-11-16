// Minimal Server-Sent Events parser for a fetch() body (m1-realtime-plan.md §3.2).
// EventSource cannot send an Authorization header, so the stream is read with fetch and parsed here.

export type SseEvent = { id: string | null; event: string; data: string };

/** Returns a feed function: give it decoded text chunks in order, it calls onEvent for each complete event. */
export function createSseParser(onEvent: (e: SseEvent) => void): (chunk: string) => void {
  let buffer = "";
  let id: string | null = null;
  let event = "";
  let data: string[] = [];
  const line = (raw: string) => {
    if (raw === "") {
      if (data.length > 0) onEvent({ id, event: event || "message", data: data.join("\n") });
      event = "";
      data = [];
      return;
    }
    if (raw.startsWith(":")) return;               // comment
    const at = raw.indexOf(":");
    const field = at < 0 ? raw : raw.slice(0, at);
    const value = at < 0 ? "" : raw.slice(at + 1).replace(/^ /, "");
    if (field === "data") data.push(value);
    else if (field === "event") event = value;
    else if (field === "id") id = value;
  };
  return (chunk: string) => {
    buffer += chunk;
    let nl: number;
    while ((nl = buffer.search(/\r\n|\r|\n/)) >= 0) {
      if (buffer[nl] === "\r" && nl === buffer.length - 1) break;   // a "\n" may follow in the next chunk
      const sep = buffer.startsWith("\r\n", nl) ? 2 : 1;
      line(buffer.slice(0, nl));
      buffer = buffer.slice(nl + sep);
    }
  };
}

/** A Postgres timestamp text from a cursor ("2026-11-16 11:00:01.12+00") as epoch ms, or null. */
export function cursorTime(cursor: string | undefined): number | null {
  const ts = cursor?.split("/").find((p) => /^\d{4}-\d{2}-\d{2}[ T]/.test(p));
  if (!ts) return null;
  const iso = ts.replace(" ", "T").replace(/([+-]\d{2})$/, "$1:00");
  const ms = Date.parse(iso);
  return Number.isNaN(ms) ? null : ms;
}
