import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { type ApiError, apiGet, type CompareBook, type PaperCompare as Compare } from "./api";
import { dayOf, money, pct } from "./format";
import { Legend, LineChart, type Series } from "./LineChart";

// Display names and colours of the paper books. 3a and R10a are the pair this page is built for.
const BOOKS: Record<string, { name: string; note: string; color: string; dash?: string }> = {
  "pathA-r10a-hedge": { name: "3a · R10a + short hedge", note: "ถือ spot ตาม R10a และ short perp เท่ากันเมื่อ R10a ไม่อยู่ในตลาด", color: "var(--series-ai)" },
  "pathA-r10a": { name: "R10a", note: "Path A + band ±2% รอบ SMA100 (spot)", color: "var(--series-strategy)" },
  "pathA-v1.1": { name: "Path A v1.1", note: "spot", color: "#7c5cc4" },
  "pathA-v1.2": { name: "Path A v1.2", note: "spot", color: "#a77fd9", dash: "6 3" },
  "futures-r7": { name: "Futures R7", note: "long/short perp 1x", color: "#0f9a8c" },
  "futures-r11": { name: "Futures R11", note: "perp + band", color: "#2fb8a8", dash: "6 3" },
  "spot-e1-v1": { name: "E1 Donchian H4", note: "spot", color: "#c2902b" },
};
const BENCH = { name: "ซื้อถือ BTC", note: "ซื้อ BTC spot ครั้งเดียวแล้วถือ", color: "var(--series-benchmark)", dash: "5 4" };
const FOCUS_PAIR = ["pathA-r10a-hedge/paper", "pathA-r10a/paper"];

export function bookInfo(b: Pick<CompareBook, "version" | "benchmark">) {
  if (b.benchmark) return BENCH;
  return BOOKS[b.version] ?? { name: b.version, note: "", color: "var(--series-cash)" };
}

function Signed({ v, plain = false }: { v: string | null; plain?: boolean }) {
  const n = v === null ? 0 : Number(v);
  return <span style={{ color: n > 0 ? "var(--gain)" : n < 0 ? "var(--loss)" : undefined }}>{pct(v, plain)}</span>;
}

/** "spot 51% · short 51%" from the signed leg shares (whole percents, display only). */
export function positionText(b: Pick<CompareBook, "spot_share" | "perp_share">) {
  if (b.spot_share === null || b.perp_share === null) return "–";
  const spot = Number(b.spot_share), perp = Number(b.perp_share);
  const whole = (n: number) => `${Math.round(Math.abs(n) * 100)}%`;
  const parts: string[] = [];
  if (spot !== 0) parts.push(`spot ${whole(spot)}`);
  if (perp < 0) parts.push(`short ${whole(perp)}`);
  if (perp > 0) parts.push(`long perp ${whole(perp)}`);
  return parts.length ? parts.join(" · ") : "เงินสด 100%";
}

function fundingText(v: string) {
  const n = Number(v);
  return n === 0 ? "–" : n > 0 ? `รับ ${money(v)}` : `จ่าย ${money(v.slice(1))}`;
}

function Headline({ a, b, from }: { a?: CompareBook; b?: CompareBook; from: string }) {
  if (!a || !b || a.return === null || b.return === null) return null;
  if (a.opened_on > from) {
    // 3a did not exist for most of this window, so a gap here would only measure R10a's past.
    return (
      <div data-testid="compare-headline" className="performance-explanation">
        3a เพิ่งเปิดเมื่อ {a.opened_on} ({a.days} วัน) ช่วงนี้จึงเทียบผลตอบแทนกับ R10a ตรงๆ ไม่ได้ ใช้ช่วง "ตั้งแต่ 3a เปิด" เพื่อเทียบ
        <span className="block text-xs text-[var(--text-muted)]">3a ตอนนี้ {positionText(a)} · R10a ตอนนี้ {positionText(b)}</span>
      </div>
    );
  }
  const diff = (Number(a.return) - Number(b.return)) * 100;       // percentage points, display only
  const lead = Math.abs(diff) < 0.005 ? "เท่ากับ" : diff > 0 ? "นำ" : "ตาม";
  return (
    <div data-testid="compare-headline" className="performance-explanation">
      ตั้งแต่ {from} (UTC) 3a <Signed v={a.return} /> · R10a <Signed v={b.return} /> → 3a {lead} R10a
      {lead !== "เท่ากับ" && ` ${Math.abs(diff).toFixed(2)} จุด`}
      <span className="block text-xs text-[var(--text-muted)]">3a ตอนนี้ {positionText(a)} · R10a ตอนนี้ {positionText(b)}</span>
    </div>
  );
}

export function PaperCompare() {
  const [from, setFrom] = useState<string | null>(null);
  const [extra, setExtra] = useState<string[]>([]);
  const q = useQuery<Compare, ApiError>({
    queryKey: ["paper-compare", from],
    queryFn: () => apiGet<Compare>(`/api/v1/paper-compare${from ? `?from=${from}` : ""}`),
    refetchInterval: 60_000,                                 // books close once a day
  });
  if (q.isPending) return <p>กำลังโหลด…</p>;
  if (q.isError) return <div role="alert"><p>{q.error.message ?? "โหลดข้อมูลไม่สำเร็จ"}</p><button className="action mt-2" onClick={() => void q.refetch()}>ลองใหม่</button></div>;
  const v = q.data;
  if (v.books.length === 0 || !v.from) return <p>ยังไม่มีพอร์ตจำลองที่กำลังเดินอยู่</p>;

  const shown = v.books.filter((b) => FOCUS_PAIR.includes(b.label) || b.benchmark || extra.includes(b.label));
  const others = v.books.filter((b) => !FOCUS_PAIR.includes(b.label) && !b.benchmark);
  const a = v.books.find((b) => b.label === FOCUS_PAIR[0]);
  const b = v.books.find((x) => x.label === FOCUS_PAIR[1]);
  const rows = [...FOCUS_PAIR.map((l) => v.books.find((x) => x.label === l)).filter((x): x is CompareBook => !!x), ...others, ...v.books.filter((x) => x.benchmark)];
  const days = Math.max(0, ...shown.map((x) => x.points.length));
  const line = (x: CompareBook) => ({ key: x.label, ...bookInfo(x) });
  const ret: Series[] = shown.map((x) => ({ ...line(x), points: x.points.map((p) => ({ t: p.day, v: Number(p.return) * 100, label: `${pct(p.return)} · ${money(p.equity)}` })) }));
  const dd: Series[] = shown.map((x) => ({ ...line(x), points: x.points.map((p) => ({ t: p.day, v: -Number(p.drawdown) * 100, label: Number(p.drawdown) ? `−${pct(p.drawdown, true)}` : "0.00%" })) }));
  const windows = [
    { from: v.default_from, label: `ตั้งแต่ 3a เปิด (${v.default_from})` },
    { from: v.earliest, label: `ตั้งแต่บัญชีแรกเปิด (${v.earliest})` },
  ].filter((w, i, all) => w.from && all.findIndex((x) => x.from === w.from) === i);
  const asOf = v.books.map((x) => x.as_of).sort().at(-1);

  return (
    <div className="space-y-4">
      <p className="text-sm text-[var(--text-secondary)]">
        พอร์ตจำลองที่ยังเดินอยู่ เริ่มที่ 10,000 USDT ทุกพอร์ต วัดจากวันเริ่มเดียวกัน: พอร์ตที่เปิดก่อนวันนั้นนับจากราคาปิดของวันนั้น
        พอร์ตที่เปิดทีหลังนับจากเงินตั้งต้น ตัวเลขหลังหักค่าธรรมเนียมและ funding แล้ว
        {asOf && <> · ราคาปิดล่าสุด {dayOf(asOf)} (UTC)</>}
      </p>
      <div className="flex flex-wrap gap-2" role="group" aria-label="ช่วงที่เทียบ">
        {windows.map((w) => (
          <button key={w.from} className={`filter-button ${v.from === w.from ? "filter-active" : ""}`} aria-pressed={v.from === w.from} onClick={() => setFrom(w.from)}>{w.label}</button>
        ))}
      </div>
      <Headline a={a} b={b} from={v.from} />

      <section className="panel space-y-3">
        <Legend series={ret} />
        {days < 2 ? (
          <p data-testid="few-days" className="text-sm text-[var(--text-muted)]">ในช่วงนี้มีราคาปิดแล้ว {days} วัน กราฟจะเริ่มแสดงเมื่อมีอย่างน้อย 2 วัน (ตัวเลขในตารางด้านล่างเป็นค่าล่าสุดแล้ว)</p>
        ) : (
          <>
            <div>
              <h3 className="text-sm font-medium text-[var(--text-secondary)]">ผลตอบแทนตั้งแต่ {v.from} (%)</h3>
              <LineChart title="ผลตอบแทนเทียบกัน" series={ret} zeroLine yTick={(n) => `${Number.isInteger(n) ? n : n.toFixed(1)}%`} />
            </div>
            <div>
              <h3 className="text-sm font-medium text-[var(--text-secondary)]">ลดลงจากจุดสูงสุดในช่วงนี้ (%)</h3>
              <LineChart title="drawdown เทียบกัน" series={dd} height={140} zeroLine yTick={(n) => `${Number.isInteger(n) ? n : n.toFixed(1)}%`} />
            </div>
          </>
        )}
        {others.length > 0 && (
          <fieldset className="flex flex-wrap gap-x-4 gap-y-2 text-sm">
            <legend className="mb-1 text-xs text-[var(--text-muted)]">เพิ่มพอร์ตอื่นในกราฟ</legend>
            {others.map((x) => (
              <label key={x.label} className="flex min-h-11 items-center gap-2">
                <input type="checkbox" checked={extra.includes(x.label)} onChange={(e) => setExtra((s) => e.target.checked ? [...s, x.label] : s.filter((l) => l !== x.label))} />
                {bookInfo(x).name}
              </label>
            ))}
          </fieldset>
        )}
      </section>

      <div className="overflow-x-auto">
        <table data-testid="compare-table" className="w-full min-w-[880px] text-sm">
          <thead className="text-left text-xs text-[var(--text-muted)]">
            <tr>
              <th className="py-2 pr-3 font-medium">พอร์ต</th>
              <th className="py-2 pr-3 text-right font-medium">มูลค่า (USDT)</th>
              <th className="py-2 pr-3 text-right font-medium">ผลตอบแทนในช่วง</th>
              <th className="py-2 pr-3 text-right font-medium">ตั้งแต่เปิด</th>
              <th className="py-2 pr-3 text-right font-medium">DD สูงสุดในช่วง</th>
              <th className="py-2 pr-3 font-medium">ถืออยู่ตอนนี้</th>
              <th className="py-2 pr-3 text-right font-medium">Funding สะสม</th>
              <th className="py-2 pr-3 text-right font-medium">ค่าธรรมเนียมสะสม</th>
              <th className="py-2 font-medium">เปิดเมื่อ</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((x) => {
              const info = bookInfo(x);
              return (
                <tr key={x.label} className={`border-t border-[var(--border)] ${FOCUS_PAIR.includes(x.label) ? "font-medium" : ""}`}>
                  <td className="py-2 pr-3">
                    <span className="flex items-center gap-2"><span className="inline-block h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: info.color }} />{info.name}</span>
                    <span className="block text-xs font-normal text-[var(--text-muted)]">{info.note}{x.benchmark && ` (${x.version})`}</span>
                  </td>
                  <td className="py-2 pr-3 text-right tabular-nums">{money(x.equity)}</td>
                  <td className="py-2 pr-3 text-right tabular-nums"><Signed v={x.return} /></td>
                  <td className="py-2 pr-3 text-right tabular-nums"><Signed v={x.return_since_open} /></td>
                  <td className="py-2 pr-3 text-right tabular-nums">{Number(x.max_drawdown) ? `−${pct(x.max_drawdown, true)}` : "0.00%"}</td>
                  <td className="py-2 pr-3">{positionText(x)}</td>
                  <td className="py-2 pr-3 text-right tabular-nums">{fundingText(x.funding_received)}</td>
                  <td className="py-2 pr-3 text-right tabular-nums">{money(x.fees)}</td>
                  <td className="whitespace-nowrap py-2 tabular-nums">{x.opened_on}<span className="block text-xs text-[var(--text-muted)]">{x.days} วัน</span></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-[var(--text-muted)]">
        ผลเดินหน้าช่วงสั้นยืนยันว่ากลยุทธ์มี edge ไม่ได้ 3a เปิดราว 19:00 UTC ของวันแรก ส่วนพอร์ตอื่นนับจากราคาปิดของวันนั้น จึงต่างกันไม่กี่ชั่วโมง
        Funding และค่าธรรมเนียมเป็นยอดสะสมตั้งแต่เปิดพอร์ต
      </p>
    </div>
  );
}
