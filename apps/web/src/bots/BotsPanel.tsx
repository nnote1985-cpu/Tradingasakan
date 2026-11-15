import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { apiGet, apiPost, type ApiError, type ProfileDrafts } from "../api";
import { BotWizard, WIZARD_KEY, type WizardState } from "./BotWizard";
import { ConfirmDialog } from "./ConfirmDialog";
import { blankConfig, STATUS_TH, type Bot, type BotAccount, type BotList, type BotTemplate } from "./types";

type Action = { bot: Bot; kind: "submit" | "approve" | "reject" | "retire" };
const ACTION_TH: Record<Action["kind"], { title: string; label: string; danger: boolean; what: (b: Bot) => string }> = {
  submit: { title: "ส่งอนุมัติ", label: "ส่งอนุมัติ", danger: false, what: (b) => `ส่ง revision ${b.latest.revision} ของ “${b.name}” ให้ operator ตรวจ ยังไม่มีอะไรรัน` },
  approve: { title: "อนุมัติให้ worker ใช้", label: "อนุมัติ", danger: true,
    what: (b) => `worker ของบัญชี ${b.account} (ที่เปิด WORKER_BOT=on) จะใช้ revision ${b.latest.revision} โหมด ${b.latest.config.mode} เงิน ${b.latest.config.allocation.amount} USDT` +
      (b.latest.config.mode === "TESTNET" ? " และส่งคำสั่งจริงบน Testnet" : " โดยไม่ส่งคำสั่ง") + " ถ้ามีสถานะเปิดอยู่ จะเปลี่ยนเมื่อ book ว่าง" },
  reject: { title: "ไม่อนุมัติ", label: "ไม่อนุมัติ", danger: false, what: (b) => `revision ${b.latest.revision} จะไม่ถูกใช้ ต้องแก้แล้วส่งใหม่` },
  retire: { title: "ปลดบอทถาวร", label: "ปลดบอท", danger: true, what: (b) => `“${b.name}” จะหยุดถาวร บันทึก revision ใหม่ไม่ได้ และบัญชี ${b.account} จะว่างให้บอทอื่น ต้อง HALT บัญชีก่อนถ้าบอทกำลังรัน` },
};

function Chip({ bot }: { bot: Bot }) {
  const tone = bot.status === "running" ? "status-ok" : bot.status === "halted" ? "status-bad" : "status-warn";
  return <span className={`status-chip ${tone}`}>{STATUS_TH[bot.status]}</span>;
}

export function BotsPanel() {
  const cache = useQueryClient();
  const bots = useQuery<BotList, ApiError>({ queryKey: ["bots"], queryFn: () => apiGet("/api/v1/bots"), refetchInterval: 30_000 });
  const templates = useQuery<{ templates: BotTemplate[] }, ApiError>({ queryKey: ["bot-templates"], queryFn: () => apiGet("/api/v1/bot-templates"), staleTime: 300_000 });
  const accounts = useQuery<{ accounts: BotAccount[] }, ApiError>({ queryKey: ["bot-accounts"], queryFn: () => apiGet("/api/v1/bot-accounts"), staleTime: 30_000 });
  const drafts = useQuery<ProfileDrafts, ApiError>({ queryKey: ["profile-drafts"], queryFn: () => apiGet("/api/v1/bot-profile-drafts") });
  const [wizard, setWizardState] = useState<WizardState | null>(() => cache.getQueryData<WizardState>(WIZARD_KEY) ?? null);
  const [action, setAction] = useState<Action | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  useEffect(() => { cache.setQueryData(WIZARD_KEY, wizard); }, [cache, wizard]);   // unsaved form survives tab changes
  const t0 = templates.data?.templates[0];
  const canEdit = bots.data?.can_edit === true;
  const start = (w: Omit<WizardState, "request_id" | "step">) => { setWizardState({ ...w, request_id: crypto.randomUUID(), step: 0 }); setNotice(""); setError(""); };

  async function run(a: Action) {
    setBusy(true); setError("");
    try {
      const path = a.kind === "retire" ? `/api/v1/bots/${a.bot.bot_id}/retire` : `/api/v1/bots/${a.bot.bot_id}/revisions/${a.bot.latest.revision}/${a.kind}`;
      await apiPost(path, { note });
      setNotice(`${ACTION_TH[a.kind].title}: “${a.bot.name}” แล้ว`);
      await cache.invalidateQueries({ queryKey: ["bots"] });
      await cache.invalidateQueries({ queryKey: ["bot-accounts"] });
    } catch (e) {
      const err = e as ApiError & { errors?: { message_th: string }[] };
      setError(err.errors?.map((x) => x.message_th).join(" · ") || err.message || "ไม่สำเร็จ กรุณาลองใหม่");
    } finally { setBusy(false); setAction(null); setNote(""); }
  }

  const list = bots.data?.bots ?? [];
  return <section className="space-y-5" aria-label="บอท">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div><h2 className="text-lg font-semibold">บอท</h2><p className="text-sm text-[var(--text-secondary)]">ตั้งค่า ส่งอนุมัติ แล้ว worker ของบัญชีจะดึง revision ที่อนุมัติไปใช้เอง เว็บไม่สั่ง worker ตรง</p></div>
      {canEdit && !wizard && <div className="flex flex-wrap gap-2">
        {drafts.data?.items.length ? <select className="action" aria-label="เริ่มจากร่าง Bot profile" value="" onChange={(e) => {
          const d = drafts.data?.items.find((x) => x.profile_id === e.target.value);
          if (d) start({ config: { ...blankConfig(t0), name: d.profile.name.slice(0, 100), notes: d.profile.notes, source_draft: { profile_id: d.profile_id, revision: d.revision } }, bot_id: null, revision: 0, previous: null });
        }}><option value="">แปลงร่างเดิมเป็นบอท…</option>{drafts.data.items.map((d) => <option key={d.profile_id} value={d.profile_id}>{d.profile.name} (v{d.revision})</option>)}</select> : null}
        <button type="button" className="action primary-action" disabled={!t0} onClick={() => start({ config: blankConfig(t0), bot_id: null, revision: 0, previous: null })}>สร้างบอท</button>
      </div>}
    </div>
    {bots.isPending && <p role="status">กำลังโหลดบอท…</p>}
    {bots.isError && <p role="alert" className="notice">โหลดบอทไม่ได้: {bots.error.message} <button onClick={() => void bots.refetch()}>ลองใหม่</button></p>}
    {bots.data && !canEdit && <p className="notice">ดูได้อย่างเดียว · การสร้าง อนุมัติ และปลดบอทต้องใช้สิทธิ์ Operator หรือ Admin</p>}
    {notice && <p role="status" className="notice">{notice}</p>}
    {error && <p role="alert" className="notice">{error}</p>}
    {wizard && templates.data && <BotWizard state={wizard} setState={setWizardState} templates={templates.data.templates} accounts={accounts.data?.accounts ?? []} onDone={setNotice} />}
    {wizard?.config.source_draft && <p className="text-xs text-[var(--text-secondary)]">ร่าง Bot profile ยังไม่มี engine รัน จึงคัดมาเฉพาะชื่อและหมายเหตุ เลือกกลยุทธ์ที่รันได้ในขั้นที่ 2</p>}
    {bots.data && list.length === 0 && !wizard && <div className="panel text-sm text-[var(--text-secondary)]">ยังไม่มีบอท เริ่มด้วยปุ่ม “สร้างบอท”</div>}
    {list.map((b) => <article key={b.bot_id} className="panel space-y-3" data-testid="bot">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div><h3 className="font-semibold">{b.name}</h3><p className="text-sm text-[var(--text-secondary)]">บัญชี {b.account} · book {b.book} · {b.latest.config.strategy.template} · {b.latest.config.allocation.amount} USDT · {b.latest.config.mode}</p></div>
        <Chip bot={b} />
      </div>
      <p className="text-sm tabular-nums">revision ล่าสุด {b.latest.revision}{b.latest.submitted && !b.latest.verdict ? " (รออนุมัติ)" : b.latest.verdict ? ` (${b.latest.verdict.action === "approve" ? "อนุมัติ" : "ไม่อนุมัติ"})` : " (ร่าง)"}
        {" · "}อนุมัติล่าสุด {b.approved ? `rev ${b.approved.revision} เมื่อ ${new Date(b.approved.at).toLocaleString("th-TH")}` : "–"}
        {" · "}worker รายงาน {b.running ? `rev ${b.running.revision ?? "?"} (${b.running.mode ?? "?"}, ${b.running.safety ?? "?"})` : "ยังไม่รัน"}</p>
      {b.running?.pending_revision && <p className="notice text-sm">revision {b.running.pending_revision} รอ: {b.running.note ?? "รอ book ว่าง"}</p>}
      {canEdit && b.status !== "retired" && <div className="flex flex-wrap justify-between gap-2">
        <div className="flex flex-wrap gap-2">
          <button type="button" className="action" disabled={busy || !!wizard} onClick={() => start({ config: structuredClone(b.latest.config), bot_id: b.bot_id, revision: b.latest.revision, previous: b.latest.config })}>แก้ไข</button>
          {!b.latest.submitted && <button type="button" className="action" disabled={busy} onClick={() => setAction({ bot: b, kind: "submit" })}>ส่งอนุมัติ</button>}
          {b.latest.submitted && !b.latest.verdict && <button type="button" className="action" disabled={busy} onClick={() => setAction({ bot: b, kind: "reject" })}>ไม่อนุมัติ</button>}
          {b.latest.submitted && !b.latest.verdict && <button type="button" className="action primary-action" disabled={busy} onClick={() => setAction({ bot: b, kind: "approve" })}>อนุมัติ…</button>}
        </div>
        <button type="button" className="action danger-action" disabled={busy} onClick={() => setAction({ bot: b, kind: "retire" })}>ปลดบอท…</button>
      </div>}
    </article>)}
    {action && <ConfirmDialog title={ACTION_TH[action.kind].title} confirmLabel={ACTION_TH[action.kind].label} danger={ACTION_TH[action.kind].danger} busy={busy}
      onCancel={() => { setAction(null); setNote(""); }} onConfirm={() => void run(action)}>
      <p>{ACTION_TH[action.kind].what(action.bot)}</p>
      <label className="profile-field"><span>หมายเหตุ (ไม่บังคับ)</span><input maxLength={500} value={note} onChange={(e) => setNote(e.target.value)} /></label>
    </ConfirmDialog>}
  </section>;
}
