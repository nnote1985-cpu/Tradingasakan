import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { type ApiError, apiPost, type FuturesPlan, type FuturesPosition, type FuturesRequest } from "../api";
import { ConfirmDialog } from "../bots/ConfirmDialog";
import { money, pct } from "../format";

const errText = (e: unknown) => {
  const x = e as Partial<ApiError & { reasons: string[] }>;
  return x?.reasons?.length ? x.reasons.join(" · ") : x?.message ?? String(e);
};

type Form = { symbol: string; side: "LONG" | "SHORT"; stop: string; tp: string; leverage: string };

/** Manual order ticket (futures-desk-plan.md §7). Size comes from the 1% rule; the trader picks only the stop. */
export function OrderTicket({ account, symbols, marks, canTrade, maxLeverage }: {
  account: string; symbols: string[]; marks: Record<string, string>; canTrade: boolean; maxLeverage: number;
}) {
  const qc = useQueryClient();
  const [f, setF] = useState<Form>({ symbol: symbols[0] ?? "BTCUSDT", side: "LONG", stop: "", tp: "", leverage: "3" });
  const [plan, setPlan] = useState<FuturesPlan | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const set = (p: Partial<Form>) => { setF({ ...f, ...p }); setPlan(null); setError(""); setNotice(""); };
  const body = () => ({ account, symbol: f.symbol, side: f.side, stop_price: f.stop, leverage: Number(f.leverage), ...(f.tp ? { take_profit: f.tp } : {}) });

  const check = async () => {
    setError(""); setNotice("");
    try { setPlan(await apiPost<FuturesPlan>("/api/v1/futures/orders/preview", body())); } catch (e) { setError(errText(e)); }
  };
  const send = async () => {
    setBusy(true);
    try {
      const r = await apiPost<{ id: number | null }>("/api/v1/futures/orders", { ...body(), request_id: crypto.randomUUID() });
      setNotice(`ส่งคำสั่ง #${r.id ?? ""} แล้ว worker จะตรวจอีกครั้งและส่งภายในไม่กี่วินาที ดูผลที่ "คำสั่งล่าสุด"`);
      setPlan(null);
      void qc.invalidateQueries({ queryKey: ["futures-desk"] });
    } catch (e) { setError(errText(e)); }
    setBusy(false); setConfirm(false);
  };
  const mark = marks[f.symbol];
  return (
    <section data-testid="order-ticket" className="panel space-y-3">
      <h2 className="text-lg font-semibold">เปิดไม้ใหม่</h2>
      {!canTrade && <p className="notice">ยังส่งคำสั่งไม่ได้: ต้องเป็นผู้ดูแล (operator), worker ต้องเปิดรับคำสั่ง และข้อมูลต้องไม่เก่าเกิน 1 นาที ตรวจขนาดไม้ได้ตามปกติ</p>}
      <div className="grid gap-3 sm:grid-cols-5">
        <label className="text-sm">คู่<select className="mt-1 w-full rounded border border-[var(--border)] bg-[var(--surface-1)] p-2" value={f.symbol} onChange={(e) => set({ symbol: e.target.value })}>{symbols.map((s) => <option key={s}>{s}</option>)}</select></label>
        <div className="text-sm">ฝั่ง<div className="mt-1 flex gap-1" role="group" aria-label="ฝั่ง">
          {(["LONG", "SHORT"] as const).map((s) => <button key={s} type="button" aria-pressed={f.side === s} className={`filter-button flex-1 ${f.side === s ? "filter-active" : ""}`} onClick={() => set({ side: s })}>{s === "LONG" ? "Long" : "Short"}</button>)}
        </div></div>
        <label className="text-sm">Stop-loss (บังคับ)<input inputMode="decimal" className="mt-1 w-full rounded border border-[var(--border)] bg-[var(--surface-1)] p-2" value={f.stop} onChange={(e) => set({ stop: e.target.value.trim() })} placeholder={mark ? `ราคาตอนนี้ ${money(mark)}` : ""} /></label>
        <label className="text-sm">Take-profit (ไม่บังคับ)<input inputMode="decimal" className="mt-1 w-full rounded border border-[var(--border)] bg-[var(--surface-1)] p-2" value={f.tp} onChange={(e) => set({ tp: e.target.value.trim() })} /></label>
        <label className="text-sm">Leverage<input type="number" min={1} max={maxLeverage} className="mt-1 w-full rounded border border-[var(--border)] bg-[var(--surface-1)] p-2" value={f.leverage} onChange={(e) => set({ leverage: e.target.value })} /></label>
      </div>
      <p className="text-xs text-[var(--text-muted)]">ขนาดไม้คำนวณให้เองจากกฎ 1%: ถ้าโดน Stop-loss จะเสียประมาณ 1% ของเงินในบัญชี Leverage มีผลแค่ margin ที่กันไว้กับราคา Liquidation ไม่ได้เพิ่มความเสี่ยงต่อไม้ · ส่งเป็นคำสั่ง Market แล้ววาง Stop-loss บน exchange ทันที ถ้าวาง SL ไม่ได้ระบบจะปิด position ให้</p>
      <div className="flex flex-wrap gap-2">
        <button type="button" className="action" disabled={!f.stop} onClick={() => void check()}>ตรวจขนาดไม้</button>
        <button type="button" className="action primary-action" disabled={!plan?.ok || !canTrade} onClick={() => setConfirm(true)}>ส่งคำสั่ง</button>
      </div>
      {plan && (
        <div data-testid="plan" className="rounded-lg border border-[var(--border)] p-3 text-sm">
          <dl className="grid grid-cols-2 gap-x-4 gap-y-1 sm:grid-cols-4 [&_dt]:text-xs [&_dt]:text-[var(--text-muted)] [&_dd]:tabular-nums">
            <div><dt>ขนาดไม้</dt><dd>{plan.qty} {f.symbol.replace("USDT", "")}</dd></div>
            <div><dt>มูลค่า / Margin</dt><dd>{money(plan.notional)} / {money(plan.margin)}</dd></div>
            <div><dt>ถ้าโดน SL เสีย</dt><dd>{money(plan.risk_usdt)} ({pct(plan.risk_pct, true)})</dd></div>
            <div><dt>Liquidation โดยประมาณ</dt><dd>{plan.liq_estimate ? money(plan.liq_estimate) : "–"}</dd></div>
            <div><dt>ราคาอ้างอิง</dt><dd>{money(plan.ref_price)}</dd></div>
            <div><dt>Stop-loss (ปัดตาม tick)</dt><dd>{money(plan.stop_price)}</dd></div>
            <div><dt>Take-profit</dt><dd>{plan.take_profit ? money(plan.take_profit) : "–"}</dd></div>
          </dl>
          {plan.notes.map((n) => <p key={n} className="mt-2 text-[var(--warn-fg)]">{n}</p>)}
          {plan.refusals.length > 0 && <ul role="alert" className="mt-2 list-disc pl-5 text-[var(--loss)]">{plan.refusals.map((r) => <li key={r}>{r}</li>)}</ul>}
        </div>
      )}
      {error && <p role="alert" className="text-sm text-[var(--loss)]">{error}</p>}
      {notice && <p className="text-sm text-[var(--gain)]">{notice}</p>}
      {confirm && plan && (
        <ConfirmDialog title={`${f.side === "LONG" ? "Long" : "Short"} ${plan.qty} ${f.symbol}`} confirmLabel="ยืนยันส่งคำสั่ง" busy={busy}
          onConfirm={() => void send()} onCancel={() => setConfirm(false)}>
          <p>Market ประมาณ {money(plan.ref_price)} · Stop-loss {money(plan.stop_price)}{plan.take_profit ? ` · Take-profit ${money(plan.take_profit)}` : ""} · {plan.leverage}x Isolated</p>
          <p>ถ้าโดน Stop-loss เสียประมาณ {money(plan.risk_usdt, "USDT")} ({pct(plan.risk_pct, true)}) ไม่รวม slippage</p>
          <p className="text-xs text-[var(--text-muted)]">บัญชี Demo (เงินทดลอง) · worker จะตรวจซ้ำด้วยราคาล่าสุดก่อนส่ง และไม่ส่งเกินขนาดนี้</p>
        </ConfirmDialog>
      )}
    </section>
  );
}

/** Close and move-stop buttons under a position card. */
export function PositionActions({ account, p, canTrade }: { account: string; p: FuturesPosition; canTrade: boolean }) {
  const qc = useQueryClient();
  const [ask, setAsk] = useState<"close" | "stop" | null>(null);
  const [stop, setStop] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  if (!canTrade) return null;
  const go = async () => {
    setBusy(true); setMsg("");
    try {
      if (ask === "close") await apiPost(`/api/v1/futures/positions/${p.symbol}/close`, { account, request_id: crypto.randomUUID() });
      else await apiPost(`/api/v1/futures/positions/${p.symbol}/stop`, { account, stop_price: stop, request_id: crypto.randomUUID() });
      setMsg("ส่งแล้ว รอ worker ไม่กี่วินาที");
      void qc.invalidateQueries({ queryKey: ["futures-desk"] });
    } catch (e) { setMsg(errText(e)); }
    setBusy(false); setAsk(null);
  };
  return (
    <div className="mt-2 flex flex-wrap items-center gap-2">
      <button type="button" className="action danger-action" onClick={() => setAsk("close")}>ปิด position (Market)</button>
      <input aria-label={`Stop-loss ใหม่ ${p.symbol}`} inputMode="decimal" className="w-36 rounded border border-[var(--border)] bg-[var(--surface-1)] p-2 text-sm" placeholder="SL ใหม่" value={stop} onChange={(e) => setStop(e.target.value.trim())} />
      <button type="button" className="action" disabled={!stop} onClick={() => setAsk("stop")}>ย้าย Stop-loss</button>
      {msg && <span className="text-sm text-[var(--text-secondary)]">{msg}</span>}
      {ask && (
        <ConfirmDialog title={ask === "close" ? `ปิด ${p.symbol} ทั้งหมด` : `ย้าย Stop-loss ${p.symbol}`} confirmLabel="ยืนยัน" danger={ask === "close"} busy={busy}
          onConfirm={() => void go()} onCancel={() => setAsk(null)}>
          {ask === "close" ? <p>ขาย {p.qty} ที่ราคา Market ประมาณ {money(p.mark_price)} แล้วยกเลิกคำสั่งค้างของคู่นี้</p>
            : <p>วาง Stop-loss ใหม่ที่ {money(stop)} ก่อน แล้วค่อยยกเลิกอันเดิม ย้ายได้เฉพาะทางที่ความเสี่ยงตอนเข้าไม่เกิน 1%</p>}
        </ConfirmDialog>
      )}
    </div>
  );
}

const STATUS: Record<FuturesRequest["status"], { text: string; cls: string }> = {
  PENDING: { text: "รอ worker", cls: "status-warn" }, SENT: { text: "กำลังส่ง", cls: "status-warn" }, DONE: { text: "สำเร็จ", cls: "status-ok" },
  REFUSED: { text: "ไม่ผ่านการตรวจ", cls: "status-bad" }, FAILED: { text: "ล้มเหลว", cls: "status-bad" }, EXPIRED: { text: "หมดเวลา", cls: "status-warn" },
};
const KIND = { OPEN: "เปิด", CLOSE: "ปิด", MOVE_STOP: "ย้าย SL" } as const;

export function RequestList({ requests, when }: { requests: FuturesRequest[]; when: (iso: string | null) => string }) {
  if (!requests.length) return null;
  return (
    <details open className="panel">
      <summary className="cursor-pointer text-lg font-semibold">คำสั่งล่าสุดจากหน้านี้</summary>
      <ul data-testid="requests" className="mt-3 space-y-2 text-sm">
        {requests.map((r) => (
          <li key={r.id} className="flex flex-wrap gap-x-3 gap-y-1 border-b border-[var(--border)] pb-2">
            <span className="text-[var(--text-muted)]">#{r.id} · {when(r.requested_at)}</span>
            <span>{KIND[r.kind]} {r.symbol}{r.side ? ` ${r.side === "LONG" ? "Long" : "Short"}` : ""}{r.qty ? ` ${r.qty}` : ""}{r.stop_price ? ` SL ${money(r.stop_price)}` : ""}</span>
            <span className={`rounded px-1.5 text-xs ${STATUS[r.status].cls}`}>{STATUS[r.status].text}</span>
            {r.outcome && <span className="w-full text-[var(--text-secondary)]">{r.outcome}</span>}
          </li>
        ))}
      </ul>
    </details>
  );
}
