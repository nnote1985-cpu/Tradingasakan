import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { apiPost, type ApiError } from "../api";
import { ConfirmDialog } from "./ConfirmDialog";
import { configDiff, stepOf, type BotAccount, type BotConfig, type BotRevision, type BotTemplate, type FieldError } from "./types";

export type WizardState = { config: BotConfig; bot_id: string | null; revision: number; previous: BotConfig | null; request_id: string; step: number };
export const WIZARD_KEY = ["bot-wizard"];
const STEPS = ["ตลาดและบัญชี", "กลยุทธ์", "เงินและความเสี่ยง", "ทบทวน"];

function Field({ title, field, errors, note, children }: { title: string; field: string; errors: FieldError[]; note?: string; children: ReactNode }) {
  const mine = errors.filter((e) => e.field === field || e.field.startsWith(`${field}.`));
  return <div className="profile-field" data-field={field}><label className="profile-field"><span>{title}</span>{children}{note && <small>{note}</small>}</label>
    {mine.map((e) => <small key={e.code + e.field} role="alert" className="field-error">{e.message_th}</small>)}</div>;
}

function StepMarket({ c, set, accounts, errors, locked }: { c: BotConfig; set: (p: Partial<BotConfig>) => void; accounts: BotAccount[]; errors: FieldError[]; locked: boolean }) {
  return <div className="grid gap-4 sm:grid-cols-2">
    <Field title="ชื่อบอท" field="name" errors={errors}><input required maxLength={100} value={c.name} onChange={(e) => set({ name: e.target.value })} placeholder="เช่น R10a v2 BOT1" /></Field>
    <Field title="ตลาด" field="instrument" errors={errors} note="Perp พักไว้จนกว่าจะมีกลยุทธ์ Futures ที่ผ่านเกณฑ์">
      <select value={c.instrument.market} onChange={() => undefined}><option value="SPOT">BTCUSDT · Spot (Testnet)</option><option value="PERP" disabled>BTCUSDT · Perp (ยังไม่เปิด)</option></select></Field>
    <Field title="บัญชี Testnet" field="account" errors={errors} note={locked ? "บอทผูกกับบัญชีเดิมทุก revision" : "หนึ่งบัญชีรันได้หนึ่งบอท"}>
      <select required disabled={locked} value={c.account} onChange={(e) => set({ account: e.target.value })}>
        <option value="">เลือกบัญชี</option>
        {locked && !accounts.some((a) => a.account === c.account) && <option value={c.account}>{c.account}</option>}
        {accounts.map((a) => <option key={a.account} value={a.account} disabled={!a.available && a.account !== c.account}>
          {a.account}{a.usdt_free ? ` · USDT ว่าง ${Number(a.usdt_free).toLocaleString("en-US", { maximumFractionDigits: 2 })}` : ""}{a.available || a.account === c.account ? "" : ` · ใช้ไม่ได้: ${a.reason_th}`}</option>)}
      </select></Field>
    {!accounts.length && <p className="notice sm:col-span-2">ยังไม่มีบัญชีที่ worker รายงาน บัญชีใหม่ต้องเปิด worker ของบัญชีนั้นบน VM ก่อน</p>}
  </div>;
}

function StepStrategy({ c, set, templates, errors }: { c: BotConfig; set: (p: Partial<BotConfig>) => void; templates: BotTemplate[]; errors: FieldError[] }) {
  const t = templates.find((x) => x.template === c.strategy.template);
  return <div className="space-y-4">
    <Field title="กลยุทธ์" field="strategy.template" errors={errors}>
      <select value={c.strategy.template} onChange={(e) => { const n = templates.find((x) => x.template === e.target.value); if (n) set({ strategy: { template: n.template, version: n.version, timeframe: n.timeframes[0], params: Object.fromEntries(Object.entries(n.params).map(([k, p]) => [k, p.default])) } }); }}>
        {templates.map((x) => <option key={x.template} value={x.template}>{x.name_th}</option>)}
      </select></Field>
    {t && <p className="text-sm text-[var(--text-secondary)]">{t.summary_th} · version {t.version}</p>}
    <Field title="Timeframe" field="strategy.timeframe" errors={errors}><select value={c.strategy.timeframe} onChange={(e) => set({ strategy: { ...c.strategy, timeframe: e.target.value } })}>{(t?.timeframes ?? []).map((tf) => <option key={tf}>{tf}</option>)}</select></Field>
    {t && Object.keys(t.params).length === 0 && <p className="text-sm">กลยุทธ์นี้ไม่มีค่าให้ปรับ</p>}
    {t && Object.entries(t.params).map(([k, p]) => <Field key={k} title={p.label_th || k} field={`strategy.params.${k}`} errors={errors} note={`${p.min} – ${p.max}`}>
      <input type="number" min={p.min} max={p.max} value={c.strategy.params[k] ?? p.default} onChange={(e) => set({ strategy: { ...c.strategy, params: { ...c.strategy.params, [k]: Number(e.target.value) } } })} /></Field>)}
    <p className="text-xs text-[var(--text-secondary)]">ผล backtest ของกลยุทธ์ดูได้ที่ <a className="underline" href="#research">งานวิจัย</a></p>
  </div>;
}

function StepMoney({ c, set, t, account, errors }: { c: BotConfig; set: (p: Partial<BotConfig>) => void; t: BotTemplate | undefined; account: BotAccount | undefined; errors: FieldError[] }) {
  return <div className="grid gap-4 sm:grid-cols-2">
    <Field title="เงินของบอท (USDT)" field="allocation.amount" errors={errors} note={`${t?.allocation.min ?? "?"} – ${t?.allocation.max ?? "?"} USDT${account?.usdt_free ? ` · ว่างในบัญชี ${Number(account.usdt_free).toLocaleString("en-US", { maximumFractionDigits: 2 })}` : ""} · ตรวจกับยอดจริงอีกครั้งตอน approve`}>
      <input inputMode="decimal" value={c.allocation.amount} onChange={(e) => set({ allocation: { asset: "USDT", amount: e.target.value.trim() } })} /></Field>
    <Field title="โหมด" field="mode" errors={errors} note="SHADOW บันทึกการตัดสินใจโดยไม่ส่งคำสั่ง · TESTNET ส่งคำสั่งบน Testnet">
      <select value={c.mode} onChange={(e) => set({ mode: e.target.value as BotConfig["mode"] })}><option value="SHADOW">SHADOW · ไม่ส่งคำสั่ง</option><option value="TESTNET">TESTNET · ส่งคำสั่งบน Testnet</option></select></Field>
    <Field title="ชุดความเสี่ยง" field="risk_profile" errors={errors} note="Guardian: เตือนที่ขาดทุน 5% ต่อวัน หรือ drawdown 15% · HALT ที่ 25%"><input readOnly value="spot.tn.v1" /></Field>
    <Field title="หมายเหตุ" field="notes" errors={errors}><textarea rows={2} maxLength={2000} value={c.notes} onChange={(e) => set({ notes: e.target.value })} /></Field>
  </div>;
}

function show(v: unknown) { return v === undefined || v === null ? "–" : typeof v === "object" ? JSON.stringify(v) : String(v); }

function StepReview({ c, previous, revision }: { c: BotConfig; previous: BotConfig | null; revision: number }) {
  const changes = configDiff(previous, c);
  return <div className="space-y-4">
    <dl className="review-grid">
      <dt>บอท</dt><dd>{c.name || "–"}</dd><dt>บัญชี</dt><dd>{c.account || "–"} · {c.venue}</dd>
      <dt>ตลาด</dt><dd>{c.instrument.symbol} {c.instrument.market}</dd><dt>กลยุทธ์</dt><dd>{c.strategy.template} · {c.strategy.timeframe}</dd>
      <dt>เงิน</dt><dd className="tabular-nums">{c.allocation.amount} USDT</dd><dt>โหมด</dt><dd>{c.mode}</dd>
    </dl>
    {previous && <div><h4 className="font-semibold">เทียบกับ revision {revision}</h4>
      {changes.length === 0 ? <p className="text-sm">ไม่มีค่าที่เปลี่ยน</p> : <table className="w-full text-sm"><thead><tr><th className="text-left">ช่อง</th><th className="text-left">เดิม</th><th className="text-left">ใหม่</th></tr></thead>
        <tbody>{changes.map((d) => <tr key={d.field} data-testid="diff-row"><td>{d.field}</td><td>{show(d.before)}</td><td className="font-medium">{show(d.after)}</td></tr>)}</tbody></table>}</div>}
    <p className="notice">บันทึกแล้วยังไม่รัน: ต้องส่งอนุมัติ และ operator approve ก่อน worker ของบัญชีจึงจะใช้ revision นี้ (เปลี่ยนเมื่อ book ว่างเท่านั้น)</p>
  </div>;
}

export function BotWizard({ state, setState, templates, accounts, onDone }: {
  state: WizardState; setState: (s: WizardState | null) => void; templates: BotTemplate[]; accounts: BotAccount[]; onDone: (message: string) => void;
}) {
  const cache = useQueryClient();
  const [errors, setErrors] = useState<FieldError[]>([]);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [leaving, setLeaving] = useState(false);
  const form = useRef<HTMLDivElement>(null);
  const [focusField, setFocusField] = useState<string | null>(null);
  const c = state.config;
  const t = templates.find((x) => x.template === c.strategy.template);
  const set = (p: Partial<BotConfig>) => { setState({ ...state, config: { ...c, ...p }, request_id: crypto.randomUUID() }); setErrors((e) => e.filter((x) => !Object.keys(p).some((k) => x.field === k || x.field.startsWith(`${k}.`)))); };
  const go = (step: number) => setState({ ...state, step });

  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, []);
  useEffect(() => {   // after a refused save: focus the first field that is wrong, once its step is shown
    if (!focusField) return;
    const el = form.current?.querySelector<HTMLElement>(`[data-field="${focusField}"] :is(input, select, textarea)`)
      ?? form.current?.querySelector<HTMLElement>(`[data-field^="${focusField.split(".")[0]}"] :is(input, select, textarea)`);
    el?.focus();
    setFocusField(null);
  }, [focusField, state.step]);

  function local(): FieldError[] {
    const out: FieldError[] = [];
    if (!c.name.trim()) out.push({ field: "name", code: "missing", message_th: "ต้องตั้งชื่อบอท" });
    if (!c.account) out.push({ field: "account", code: "missing", message_th: "ต้องเลือกบัญชี" });
    const n = Number(c.allocation.amount);
    if (t && (!/^\d+(\.\d{1,2})?$/.test(c.allocation.amount) || n < Number(t.allocation.min) || n > Number(t.allocation.max)))
      out.push({ field: "allocation.amount", code: "allocation_bounds", message_th: `เงินของบอทต้องอยู่ระหว่าง ${t.allocation.min} ถึง ${t.allocation.max} USDT` });
    return out;
  }
  function fail(errs: FieldError[]) {
    setErrors(errs);
    if (errs.length) { const first = errs[0].field; setState({ ...state, step: stepOf(first) }); setFocusField(first); }
  }
  async function save(submit: boolean) {
    const errs = local();
    if (errs.length) return fail(errs);
    setBusy(true); setMessage("");
    try {
      const saved = await apiPost<BotRevision>("/api/v1/bots", { bot_id: state.bot_id, expected_revision: state.revision, request_id: state.request_id, config: c });
      if (submit) await apiPost(`/api/v1/bots/${saved.bot_id}/revisions/${saved.revision}/submit`, { note: "" });
      await cache.invalidateQueries({ queryKey: ["bots"] });
      setState(null);
      onDone(`บันทึก “${c.name}” revision ${saved.revision} แล้ว${submit ? " และส่งอนุมัติแล้ว" : " เป็นร่าง"}`);
    } catch (e) {
      const err = e as ApiError & { errors?: FieldError[] };
      if (err.errors?.length) fail(err.errors); else setMessage(err.message || "บันทึกไม่สำเร็จ กรุณาลองใหม่");
      if (err.code === "stale_revision") setMessage("บอทนี้มี revision ใหม่กว่าแล้ว ฟอร์มยังอยู่ ปิดแล้วเปิดบอทใหม่เพื่อดูค่าล่าสุด");
    } finally { setBusy(false); }
  }

  const steps = [
    <StepMarket key={0} c={c} set={set} accounts={accounts} errors={errors} locked={state.bot_id !== null} />,
    <StepStrategy key={1} c={c} set={set} templates={templates} errors={errors} />,
    <StepMoney key={2} c={c} set={set} t={t} account={accounts.find((a) => a.account === c.account)} errors={errors} />,
    <StepReview key={3} c={c} previous={state.previous} revision={state.revision} />,
  ];
  return <section className="panel space-y-5" aria-label="ตั้งค่าบอท">
    <div className="flex flex-wrap items-center justify-between gap-2"><h3 className="text-lg font-semibold">{state.bot_id ? `แก้ไขบอท · จะเป็น revision ${state.revision + 1}` : "สร้างบอทใหม่"}</h3><span className="status-chip status-warn">TESTNET เท่านั้น</span></div>
    <ol className="wizard-steps">{STEPS.map((s, i) => <li key={s}><button type="button" aria-current={state.step === i ? "step" : undefined} className={`filter-button ${state.step === i ? "filter-active" : ""}`} onClick={() => go(i)}>{i + 1}. {s}</button></li>)}</ol>
    <div ref={form}><fieldset disabled={busy} className="space-y-4">{steps[state.step]}</fieldset></div>
    {message && <p role="alert" className="notice">{message}</p>}
    {errors.length > 0 && <p role="alert" className="notice">มี {errors.length} ช่องที่ต้องแก้ ({errors.map((e) => STEPS[stepOf(e.field)]).filter((v, i, a) => a.indexOf(v) === i).join(", ")})</p>}
    <div className="flex flex-wrap justify-between gap-3">
      <div className="flex gap-2">
        <button type="button" className="action" disabled={state.step === 0 || busy} onClick={() => go(state.step - 1)}>ย้อนกลับ</button>
        {state.step < 3 && <button type="button" className="action" disabled={busy} onClick={() => go(state.step + 1)}>ถัดไป</button>}
      </div>
      <div className="flex flex-wrap gap-2">
        <button type="button" className="action" disabled={busy} onClick={() => setLeaving(true)}>ยกเลิก</button>
        <button type="button" className="action" disabled={busy} onClick={() => void save(false)}>บันทึกร่าง</button>
        {state.step === 3 && <button type="button" className="action primary-action" disabled={busy} onClick={() => void save(true)}>{busy ? "กำลังบันทึก…" : "บันทึกและส่งอนุมัติ"}</button>}
      </div>
    </div>
    {leaving && <ConfirmDialog title="ทิ้งการแก้ไขนี้?" confirmLabel="ทิ้งการแก้ไข" danger onCancel={() => setLeaving(false)} onConfirm={() => { setLeaving(false); setState(null); }}>
      <p>ค่าที่ยังไม่บันทึกในฟอร์มนี้จะหายไป revision ที่บันทึกไว้แล้วไม่เปลี่ยน</p></ConfirmDialog>}
  </section>;
}

