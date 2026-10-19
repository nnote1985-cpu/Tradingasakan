import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { apiGet, apiPost, type ApiError, type BotProfile, type DraftRecipe, type DraftRule, type LibraryStrategy, type ProfileDraft, type ProfileDrafts } from "./api";

type Editor = { profile: DraftRecipe; profile_id: string | null; revision: number; request_id?: string };
const EDITOR_KEY = ["draft-profile-editor"];
function rule(s: LibraryStrategy, timeframe = s.default_timeframe): DraftRule {
  return { number: s.number, version: s.version, timeframe, params: Object.fromEntries(Object.entries(s.params).map(([key, p]) => [key, p.default])) };
}
function initial(s: LibraryStrategy): DraftRecipe {
  return { schema_version: "bot-recipe-draft-v1", status: "draft", name: "", instrument: "BTCUSDT_SPOT", timeframe: s.default_timeframe,
    setup_mode: "custom", preset_profile: null, preset_strategy_versions: {}, entry: rule(s), trend_filter: null,
    stop_loss: { kind: "atr", atr_period: 14, atr_multiple: 2, percent: null }, exit_kind: "entry_signal", exit_rule: null,
    risk_per_trade_percent: 0.5, max_exposure_percent: 25, notes: "" };
}
function Field({ title, children, note }: { title: string; children: ReactNode; note?: string }) {
  return <label className="profile-field"><span>{title}</span>{children}{note && <small>{note}</small>}</label>;
}
function Numeric({ title, value, onChange, min = 0, max = 100, step = "any", note }: { title: string; value: number; onChange: (n: number) => void; min?: number; max?: number; step?: string; note?: string }) {
  return <Field title={title} note={note}><input type="number" required min={min} max={max} step={step} value={Number.isFinite(value) ? value : ""} onChange={(e) => onChange(e.target.value === "" ? NaN : Number(e.target.value))} /></Field>;
}
function RuleFields({ title, value, strategies, onChange, fixedTimeframe }: { title: string; value: DraftRule; strategies: LibraryStrategy[]; onChange: (r: DraftRule) => void; fixedTimeframe?: string }) {
  const spec = strategies.find((s) => s.number === value.number);
  return <fieldset className="profile-rule"><legend>{title}</legend><div className="grid gap-3 sm:grid-cols-2">
    <Field title={`${title} · กลยุทธ์`}><select value={value.number} onChange={(e) => { const s = strategies.find((x) => x.number === Number(e.target.value))!; onChange(rule(s, fixedTimeframe ?? (s.timeframes.includes(value.timeframe) ? value.timeframe : s.default_timeframe))); }}>{strategies.map((s) => <option key={s.number} value={s.number}>#{s.number} {s.name}</option>)}</select></Field>
    <Field title={`${title} · Timeframe`}><select disabled={!!fixedTimeframe} value={value.timeframe} onChange={(e) => onChange({ ...value, timeframe: e.target.value })}>{(spec?.timeframes ?? [value.timeframe]).map((tf) => <option key={tf}>{tf}</option>)}</select></Field>
    {spec && Object.entries(spec.params).map(([key, bound]) => <Numeric key={key} title={`${title} · ${key}`} value={value.params[key] ?? bound.default} min={bound.min} max={bound.max} step={bound.integer ? "1" : "any"} onChange={(v) => onChange({ ...value, params: { ...value.params, [key]: v } })} />)}
  </div><p className="mt-3 text-xs text-[var(--text-secondary)]">{spec?.summary} · เวอร์ชัน {value.version}</p></fieldset>;
}

export function DraftProfiles() {
  const cache = useQueryClient();
  const drafts = useQuery<ProfileDrafts, ApiError>({ queryKey: ["profile-drafts"], queryFn: () => apiGet("/api/v1/bot-profile-drafts") });
  const catalog = useQuery<{ items: LibraryStrategy[] }, ApiError>({ queryKey: ["strategies"], queryFn: () => apiGet("/api/v1/strategies"), staleTime: 60_000 });
  const presets = useQuery<{ items: BotProfile[] }, ApiError>({ queryKey: ["bot-profiles"], queryFn: () => apiGet("/api/v1/bot-profiles"), staleTime: 60_000 });
  const [editor, setEditor] = useState<Editor | null>(() => cache.getQueryData<Editor>(EDITOR_KEY) ?? null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [conflict, setConflict] = useState<ProfileDraft | null>(null);   // the newer revision a save ran into
  const strategies = catalog.data?.items ?? [];
  const p = editor?.profile;
  const canEdit = drafts.data?.can_edit === true;
  useEffect(() => { cache.setQueryData(EDITOR_KEY, editor); }, [cache, editor]);
  useEffect(() => {
    if (!editor) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [editor]);
  const update = (patch: Partial<DraftRecipe>) => { if (editor) setEditor({ ...editor, request_id: crypto.randomUUID(), profile: { ...editor.profile, ...patch } }); setNotice(""); };
  const close = () => { if (window.confirm("ยกเลิกการแก้ไขที่ยังไม่บันทึก?")) { setEditor(null); setError(""); setConflict(null); } };
  const open = (next: Editor) => { if (!editor || window.confirm("ทิ้งการแก้ไขที่ยังไม่บันทึก แล้วเปิดร่างอื่น?")) { setEditor({ ...next, request_id: crypto.randomUUID() }); setError(""); setNotice(""); setConflict(null); } };
  // After a 409: keep this form and save it on top of the newer revision, or open the newer revision instead.
  const keepMine = (latest: ProfileDraft) => { if (editor) setEditor({ ...editor, revision: latest.revision, request_id: crypto.randomUUID() }); setConflict(null); setError(""); setNotice(`ฟอร์มนี้จะบันทึกเป็นเวอร์ชัน ${latest.revision + 1} ต่อจากของล่าสุด · กดบันทึกอีกครั้ง`); };
  const takeTheirs = (latest: ProfileDraft) => { setEditor({ profile: structuredClone(latest.profile), profile_id: latest.profile_id, revision: latest.revision, request_id: crypto.randomUUID() }); setConflict(null); setError(""); setNotice(""); };
  function usePreset(preset: BotProfile) {
    if (!strategies.length) return;
    update({ setup_mode: "preset", timeframe: "1d", entry: null, trend_filter: null, exit_kind: "entry_signal", exit_rule: null,
      preset_strategy_versions: Object.fromEntries(preset.strategies.map((s) => [String(s.number), s.version])),
      preset_profile: { profile_id: preset.profile_id, version: preset.version, name: preset.name, status: "draft", instrument: "BTCUSDT_SPOT", decision_clock: "1d", combine: preset.combine, risk_profile: preset.risk_profile, ai_mode: "off", notes: preset.notes,
        strategies: preset.strategies.map((s) => ({ number: s.number, timeframe: s.timeframe, params: { ...s.params } })) } });
  }
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!editor || !canEdit || busy) return;
    if (editor.profile.risk_per_trade_percent > editor.profile.max_exposure_percent) { setError("Risk / Trade ต้องไม่เกิน Max Exposure"); return; }
    setBusy(true); setError(""); setNotice("");
    try {
      const saved = await apiPost<ProfileDraft>("/api/v1/bot-profile-drafts", { profile_id: editor.profile_id, expected_revision: editor.revision, request_id: editor.request_id, profile: editor.profile });
      cache.setQueryData<ProfileDrafts>(["profile-drafts"], (old) => ({ can_edit: old?.can_edit ?? true, items: [saved, ...(old?.items ?? []).filter((d) => d.profile_id !== saved.profile_id)] }));
      setEditor(null); setNotice(`บันทึก “${saved.profile.name}” เวอร์ชัน ${saved.revision} แล้ว · ยังไม่ผูกกับบอต`);
    } catch (e) {
      setError((e as ApiError).message || "บันทึกร่างไม่สำเร็จ กรุณาลองใหม่");
      if ((e as ApiError).code === "draft_conflict" && editor.profile_id) {
        const fresh = await drafts.refetch();
        setConflict(fresh.data?.items.find((d) => d.profile_id === editor.profile_id) ?? null);
      }
    }
    finally { setBusy(false); }
  }
  const nameOf = (r: DraftRule | null | undefined) => r ? `${strategies.find((s) => s.number === r.number)?.name ?? `#${r.number}`} (${r.timeframe})` : "ไม่ใช้";
  return <section className="space-y-5" aria-label="ร่าง Bot profiles">
    <div className="flex flex-wrap items-center justify-between gap-3"><div><h2 className="text-lg font-semibold">ร่าง Bot profiles</h2><p className="text-sm text-[var(--text-secondary)]">เลือก preset หรือประกอบกฎเอง แล้วบันทึกไว้เตรียมทดสอบ</p></div>
      {canEdit && <button type="button" className="action primary-action" disabled={!strategies.length || busy} onClick={() => open({ profile: initial(strategies[0]), profile_id: null, revision: 0 })}>สร้าง profile</button>}
    </div>
    {drafts.isPending && <p role="status">กำลังโหลดร่าง…</p>}
    {drafts.isError && <p role="alert" className="notice">โหลดร่างไม่ได้: {drafts.error.message} <button onClick={() => void drafts.refetch()}>ลองใหม่</button></p>}
    {catalog.isError && <p role="alert" className="notice">โหลดคลังไม่ได้: {catalog.error.message} <button onClick={() => void catalog.refetch()}>ลองใหม่</button></p>}
    {drafts.data && !canEdit && <p className="notice">ดูร่างได้ · การสร้างและบันทึกต้องใช้สิทธิ์ Operator หรือ Admin</p>}
    {notice && <p role="status" className="notice">{notice}</p>}
    {error && <div role="alert" className="notice">{error}{conflict && <div className="mt-2 space-y-2"><p>ล่าสุดคือเวอร์ชัน {conflict.revision} บันทึกเมื่อ {new Date(conflict.saved_at).toLocaleString("th-TH")} · ฟอร์มของคุณยังอยู่</p><div className="flex flex-wrap gap-2"><button type="button" className="action" onClick={() => keepMine(conflict)}>ใช้ฟอร์มนี้ บันทึกเป็นเวอร์ชัน {conflict.revision + 1}</button><button type="button" className="action" onClick={() => { if (window.confirm("ทิ้งที่แก้ในฟอร์มนี้ แล้วเปิดเวอร์ชันล่าสุด?")) takeTheirs(conflict); }}>เปิดเวอร์ชัน {conflict.revision} แทน</button></div></div>}</div>}
    {editor && p && <form onSubmit={(e) => void save(e)} className="panel space-y-5" aria-label="ตั้งค่า Draft Bot Profile">
      <fieldset disabled={busy || !canEdit} className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-2"><h3 className="text-lg font-semibold">{editor.profile_id ? `แก้ไขร่าง · จะบันทึกเป็นเวอร์ชัน ${editor.revision + 1}` : "สร้างร่างใหม่"}</h3><span className="status-chip status-warn">DRAFT · ยังไม่เชื่อมการเทรด</span></div>
      <div className="grid gap-4 sm:grid-cols-2"><Field title="ชื่อ profile"><input required maxLength={100} value={p.name} onChange={(e) => update({ name: e.target.value })} placeholder="เช่น BTC MACD 4h" /></Field><Field title="ตลาด"><input readOnly value="BTCUSDT · Spot · Long / Flat · ไม่มี leverage" /></Field></div>
      <Field title="วิธีตั้งค่า"><select value={p.setup_mode} onChange={(e) => { if (e.target.value === "preset") { if (presets.data?.items[0]) usePreset(presets.data.items[0]); } else { update({ setup_mode: "custom", preset_profile: null, preset_strategy_versions: {}, timeframe: strategies[0].default_timeframe, entry: rule(strategies[0]), trend_filter: null, exit_kind: "entry_signal", exit_rule: null }); } }}><option value="custom">กำหนดเอง · เลือกกฎแยกแต่ละส่วน</option><option value="preset" disabled={!presets.data?.items.length}>เริ่มจาก preset · ใช้ชุดสัญญาณเดิม</option></select></Field>
      {presets.isError && <p className="notice">โหลด preset ไม่สำเร็จ · <button type="button" onClick={() => void presets.refetch()}>ลองใหม่</button></p>}
      {p.setup_mode === "preset" ? <div className="profile-rule space-y-3"><Field title="Preset"><select value={`${p.preset_profile?.profile_id}@${p.preset_profile?.version}`} onChange={(e) => { const found = presets.data?.items.find((x) => `${x.profile_id}@${x.version}` === e.target.value); if (found) usePreset(found); }}>
        {p.preset_profile && !presets.data?.items.some((x) => x.profile_id === p.preset_profile?.profile_id && x.version === p.preset_profile?.version) && <option value={`${p.preset_profile.profile_id}@${p.preset_profile.version}`}>{p.preset_profile.name} · สำเนาที่บันทึกไว้</option>}
        {presets.data?.items.map((x) => <option key={`${x.profile_id}@${x.version}`} value={`${x.profile_id}@${x.version}`}>{x.name} · v{x.version}</option>)}
      </select></Field><p className="text-sm">{p.preset_profile?.strategies.map((s) => `#${s.number} (${s.timeframe})`).join(" + ")}</p><p className="text-sm">{p.preset_profile?.combine === "average" ? "เฉลี่ยน้ำหนักของทุกกลยุทธ์ ปัดลงเป็น 0 / 0.5 / 1" : "กลยุทธ์แรกเป็นหลัก ที่เหลือเป็นตัวกรอง"} · ตัดสินใจ 1d ตาม preset</p><p className="text-xs text-[var(--text-secondary)]">เก็บชุดสัญญาณและวิธีรวมเดิมทั้งชุด ไม่แปลงเป็น Entry/Exit แยกโดยอัตโนมัติ · ตั้ง Stop-loss และ Risk ของร่างเพิ่มด้านล่างได้</p></div> : <>
        <Field title="Timeframe หลัก" note="ประเมิน Entry เมื่อแท่งราคาปิด"><select value={p.timeframe} onChange={(e) => update({ timeframe: e.target.value, entry: { ...p.entry!, timeframe: e.target.value } })}>{(strategies.find((s) => s.number === p.entry?.number)?.timeframes ?? ["1h", "4h", "1d"]).map((tf) => <option key={tf}>{tf}</option>)}</select></Field>
        <Field title="Trend Filter"><select value={p.trend_filter ? "on" : "off"} onChange={(e) => update({ trend_filter: e.target.value === "off" ? null : rule(strategies.find((s) => s.family === "trend")!) })}><option value="off">ไม่ใช้ตัวกรอง</option><option value="on" disabled={!strategies.some((s) => s.family === "trend")}>ใช้กลยุทธ์ตามเทรนด์เป็นตัวกรอง</option></select></Field>
        {p.trend_filter && <RuleFields title="Trend Filter" strategies={strategies.filter((s) => s.family === "trend")} value={p.trend_filter} onChange={(r) => update({ trend_filter: r })} />}
        {p.entry && <RuleFields title="Entry" strategies={strategies.filter((s) => s.timeframes.includes(p.timeframe))} fixedTimeframe={p.timeframe} value={p.entry} onChange={(r) => update({ entry: r })} />}
        <Field title="Exit"><select value={p.exit_kind} onChange={(e) => update({ exit_kind: e.target.value as DraftRecipe["exit_kind"], exit_rule: e.target.value === "entry_signal" ? null : rule(strategies[0], p.timeframe) })}><option value="entry_signal">ใช้สัญญาณลด/ออกของกลยุทธ์ Entry</option><option value="strategy_signal">เลือกกลยุทธ์สำหรับ Exit แยก</option></select></Field>
        {p.exit_rule && <RuleFields title="Exit" strategies={strategies} value={p.exit_rule} onChange={(r) => update({ exit_rule: r })} />}
        <p className="text-xs text-[var(--text-secondary)]">กติกาของร่าง: Entry ใช้การเพิ่มน้ำหนักสัญญาณ, Trend Filter ต้องมีน้ำหนักมากกว่า 0, Exit ใช้การลดน้ำหนักสัญญาณ · ยังไม่มี engine ประเมินชุดกฎนี้</p>
      </>}
      <fieldset className="profile-rule"><legend>Stop-loss และความเสี่ยง</legend><div className="grid gap-4 sm:grid-cols-2">
        <Field title="Stop-loss"><select value={p.stop_loss.kind} onChange={(e) => update({ stop_loss: e.target.value === "fixed_percent" ? { kind: "fixed_percent", percent: 2, atr_period: null, atr_multiple: null } : { kind: e.target.value as "atr" | "trailing_atr", percent: null, atr_period: 14, atr_multiple: 2 } })}><option value="atr">ATR จากราคาเข้า</option><option value="fixed_percent">เปอร์เซ็นต์จากราคาเข้า</option><option value="trailing_atr">Trailing ATR</option></select></Field>
        {p.stop_loss.kind === "fixed_percent" ? <Numeric title="ระยะ Stop-loss (%)" value={p.stop_loss.percent!} min={0.01} max={99.99} onChange={(v) => update({ stop_loss: { ...p.stop_loss, percent: v } })} /> : <><Numeric title="ATR period" value={p.stop_loss.atr_period!} min={2} max={300} step="1" onChange={(v) => update({ stop_loss: { ...p.stop_loss, atr_period: v } })} /><Numeric title="ATR multiplier" value={p.stop_loss.atr_multiple!} min={0.01} max={20} onChange={(v) => update({ stop_loss: { ...p.stop_loss, atr_multiple: v } })} /></>}
        <Numeric title="Risk / Trade (%)" note="สัดส่วน equity ที่ยอมขาดทุนหากถึง stop ไม่ใช่ขนาดเงินซื้อ" value={p.risk_per_trade_percent} min={0.01} onChange={(v) => update({ risk_per_trade_percent: v })} />
        <Numeric title="Max Exposure (%)" note="เพดานมูลค่า BTC เทียบกับ equity ของบอต · สูงสุด 100%" value={p.max_exposure_percent} min={0.01} onChange={(v) => update({ max_exposure_percent: v })} />
      </div><p className="mt-3 text-xs text-[var(--text-secondary)]">Stop ใช้ timeframe หลัก · ATR stop วัดจากราคาเข้า, Trailing ATR อ้างอิงจุดสูงสุดหลังเข้า · ทั้งหมดเป็นข้อกำหนดของร่าง ยังไม่ใช่คำสั่งป้องกันบน Exchange</p></fieldset>
      <Field title="หมายเหตุ"><textarea rows={2} maxLength={2000} value={p.notes} onChange={(e) => update({ notes: e.target.value })} /></Field>
      <div className="notice"><h4 className="font-semibold">สรุปก่อนบันทึก</h4><p className="mt-1">{p.name || "ยังไม่ตั้งชื่อ"} · {p.timeframe} · {p.setup_mode === "preset" ? `Preset: ${p.preset_profile?.name}` : `Entry: ${nameOf(p.entry)} · Filter: ${nameOf(p.trend_filter)} · Exit: ${p.exit_kind === "entry_signal" ? "ตาม Entry" : nameOf(p.exit_rule)}`}</p><p>Stop: {p.stop_loss.kind === "fixed_percent" ? `${p.stop_loss.percent}%` : `${p.stop_loss.kind === "trailing_atr" ? "Trailing " : ""}${p.stop_loss.atr_multiple} × ATR(${p.stop_loss.atr_period})`} · เสี่ยง {p.risk_per_trade_percent}% / เทรด · Exposure ไม่เกิน {p.max_exposure_percent}%</p><p className="mt-1">ค่าเริ่มต้นเป็นตัวอย่างสำหรับร่าง ไม่ใช่ผลทดสอบหรือคำแนะนำการลงทุน</p></div>
      <div className="flex flex-wrap gap-3"><button className="action primary-action" type="submit">{busy ? "กำลังบันทึก…" : "บันทึก Draft Profile"}</button><button className="action" type="button" onClick={close}>ยกเลิก</button></div>
      </fieldset>
    </form>}
    {drafts.data?.items.length === 0 && !editor && <div className="panel text-sm text-[var(--text-secondary)]">ยังไม่มีร่างที่บันทึกไว้ เริ่มจาก preset หรือเลือกกฎเองด้วยปุ่ม “สร้าง profile”</div>}
    {drafts.data?.items.map((d) => <article className="panel" key={d.profile_id} data-testid="saved-draft"><div className="flex flex-wrap items-center justify-between gap-3"><div><h3 className="font-semibold">{d.profile.name}</h3><p className="text-sm text-[var(--text-secondary)]">เวอร์ชัน {d.revision} · Draft · {d.profile.setup_mode === "preset" ? `Preset: ${d.profile.preset_profile?.name}` : `Entry: ${nameOf(d.profile.entry)}`} · {d.profile.timeframe}</p></div>{canEdit && <div className="flex flex-wrap gap-2"><button className="action" disabled={busy || !strategies.length} onClick={() => open({ profile: structuredClone(d.profile), profile_id: d.profile_id, revision: d.revision })}>แก้ไข {d.profile.name}</button><button className="action" disabled={busy || !strategies.length} onClick={() => open({ profile: { ...structuredClone(d.profile), name: `${d.profile.name} (สำเนา)`.slice(0, 100) }, profile_id: null, revision: 0 })}>ทำสำเนา</button></div>}</div><p className="mt-3 text-sm">เสี่ยง {d.profile.risk_per_trade_percent}% / เทรด · Max Exposure {d.profile.max_exposure_percent}%</p><p className="mt-2 text-xs text-[var(--text-muted)]">บันทึก {new Date(d.saved_at).toLocaleString("th-TH")} · ยังไม่ผูกกับบอต</p></article>)}
    <p className="text-xs text-[var(--text-muted)]">ร่างเก็บในฐานข้อมูล Supabase แยกจาก preset เดิม ทุกครั้งที่แก้ไขจะเก็บเวอร์ชันใหม่ ยังใช้ส่งคำสั่งหรือ Backtest ไม่ได้</p>
  </section>;
}
