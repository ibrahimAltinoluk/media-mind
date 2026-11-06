import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import { useApp } from "../state";
import type { Job } from "../types";
import { cls, fmtBytes, fmtInt } from "../util";
import { Check, Close } from "./Icons";
import { Popover } from "./Popover";

export const STAGE: Record<string, string> = {
  walk: "Dosyalar listeleniyor", hash: "Dosyalar okunuyor", meta: "Tarih ve kamera bilgileri okunuyor", geo: "Konumlar bulunuyor",
  thumbs: "Önizlemeler hazırlanıyor", content: "İçerik tanınıyor", faces: "Yüzler tanınıyor", video: "Videolar inceleniyor",
  transcript: "Konuşmalar yazıya dökülüyor", proxy: "Video hazırlanıyor", clip: "Klip kaydediliyor", apply: "Uygulanıyor", undo: "Geri alınıyor",
  "model:siglip2-base": "İçerik modeli indiriliyor", "model:buffalo_l": "Yüz modeli indiriliyor", "model:whisper-small": "Konuşma modeli indiriliyor",
};

const TITLE: Record<string, string> = {
  scan: "Klasör taraması", track: "Kütüphaneye ekleme", analyze: "Analiz", smart: "Akıllı analiz", apply: "Değişiklik uygulama",
  undo: "Geri alma", proxy: "Video hazırlama", snapshot: "Kare kaydetme", clip: "Klip kaydetme", models: "Model indirme",
};

const isBytes = (j: Job) => j.stage === "hash" || j.stage.startsWith("model:");
const isTime = (j: Job) => j.stage === "proxy" || j.stage === "clip";

function dur(sec: number): string {
  sec = Math.max(0, Math.round(sec));
  if (sec < 60) return `${sec} sn`;
  const m = Math.floor(sec / 60);
  if (m < 60) return `${m} dk`;
  return `${Math.floor(m / 60)} sa ${m % 60} dk`;
}

function counts(j: Job): string {
  if (!j.total) return "";
  if (isBytes(j)) return `${fmtBytes(j.done)} / ${fmtBytes(j.total)}`;
  if (isTime(j)) return `%${Math.round((j.done / j.total) * 100)}`;
  return `${fmtInt(j.done)} / ${fmtInt(j.total)}`;
}

/** Bitmiş işin kısa sonucu (tarama: yeni/taşınmış/kayıp; uygulama: dosya sayısı...). */
function resultText(j: Job): string {
  const r = j.result;
  if (!r || typeof r !== "object") return "";
  if (r.counts) {
    const c = r.counts as Record<string, number>;
    const parts = [c.new && `${fmtInt(c.new)} yeni`, c.moved && `${fmtInt(c.moved)} taşınmış`, c.copies && `${fmtInt(c.copies)} kopya`,
      c.modified && `${fmtInt(c.modified)} değişmiş`, c.missing && `${fmtInt(c.missing)} kayıp`].filter(Boolean);
    return parts.length ? parts.join(", ") : "Değişiklik yok";
  }
  if (typeof r.rel_path === "string") return r.rel_path.split("/").pop();
  if (typeof r.done === "number") return `${fmtInt(r.done)} dosya${r.failed ? `, ${fmtInt(r.failed)} başarısız` : ""}`;
  const nums = Object.values(r).filter((v): v is number => typeof v === "number");
  const max = nums.length ? Math.max(...nums) : 0;
  return max ? `${fmtInt(max)} öğe işlendi` : "Yapılacak bir şey yoktu";
}

/** Adım içi hız ölçümü: adım (ya da panel) başladığında zaman ve ilerleme kaydedilir, kalan süre bu hıza göre tahmin edilir. */
function useStageBase(j: Job): { t: number; d: number } {
  const ref = useRef<{ key: string; t: number; d: number } | null>(null);
  const key = `${j.id}:${j.stage}`;
  if (!ref.current || ref.current.key !== key) ref.current = { key, t: Date.now() / 1000, d: j.done };
  return ref.current;
}

function JobRow({ j, now }: { j: Job; now: number }) {
  const { toast } = useApp();
  const running = j.status === "running";
  const pct = j.total ? Math.min(100, Math.round((j.done / j.total) * 100)) : null;
  const elapsed = j.started_at ? now - j.started_at : 0;
  // Süre tahmini o anki adım içindir (analiz birkaç adımdan oluşur)
  const base = useStageBase(j);
  const span = now - base.t;
  const gained = j.done - base.d;
  const eta = running && j.total && gained > 0 && j.done < j.total && span > 3 ? (span * (j.total - j.done)) / gained : null;
  const cancel = async () => {
    try {
      await api.cancelJob(j.id);
    } catch (e) {
      toast(String((e as Error).message), { error: true });
    }
  };
  let sub = "";
  if (running) sub = [STAGE[j.stage] ?? "Başlıyor", counts(j)].filter(Boolean).join(" · ");
  else if (j.status === "queued") sub = j.cancel_requested ? "Durduruluyor…" : "Sırada, önceki iş bitince başlayacak";
  else if (j.status === "done") sub = resultText(j);
  else if (j.status === "cancelled") sub = "Durduruldu. Yapılan kısım kaydedildi; sonra kaldığı yerden sürer.";
  else if (j.status === "failed") sub = j.error ?? "Başarısız";
  const ago = j.finished_at ? dur(now - j.finished_at) : "";
  return (
    <div className={cls("job", `job-${j.status}`)}>
      <div className="row" style={{ gap: 8, alignItems: "flex-start" }}>
        <span className="job-ic">
          {j.status === "done" ? <Check size={12} strokeWidth={3} /> : j.status === "failed" ? <Close size={12} strokeWidth={3} /> : j.status === "running" ? <span className="spin" /> : <span className="dotq" />}
        </span>
        <div className="grow" style={{ minWidth: 0 }}>
          <div className="row" style={{ gap: 6 }}>
            <b className="grow ellipsis">{TITLE[j.type] ?? j.title}</b>
            {j.finished_at && <span className="muted" style={{ fontSize: 11 }}>{ago} önce</span>}
          </div>
          {sub && <div className={cls("job-sub", j.status === "failed" && "err")}>{sub}</div>}
          {running && (
            <>
              <div className={cls("bar", pct === null && "indet")} style={{ marginTop: 6 }}><span style={{ width: `${pct ?? 0}%` }} /></div>
              <div className="job-meta">
                <span>{pct !== null ? `%${pct}` : ""}</span>
                <span>{elapsed > 1 ? `${dur(elapsed)} sürdü` : ""}{eta !== null && eta >= 2 ? ` · bu adımda yaklaşık ${dur(eta)} kaldı` : ""}</span>
              </div>
            </>
          )}
        </div>
        {(running || j.status === "queued") && !j.cancel_requested && (
          <button className="ibtn" style={{ width: 22, height: 22 }} onClick={cancel} title="Durdur" aria-label={`${TITLE[j.type] ?? j.title}: durdur`}><Close size={12} /></button>
        )}
      </div>
    </div>
  );
}

export function Activity() {
  const { jobs, repoId } = useApp();
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const close = useCallback(() => setAnchor(null), []);
  const [now, setNow] = useState(() => Date.now() / 1000);
  const mine = jobs.filter((j) => j.repo_id === repoId || j.repo_id === "global");
  const active = mine.filter((j) => j.status === "running" || j.status === "queued");
  const running = active.filter((j) => j.status === "running");
  const queued = active.filter((j) => j.status === "queued");
  const finished = mine.filter((j) => !active.includes(j)).sort((a, b) => (b.finished_at ?? 0) - (a.finished_at ?? 0));
  const failed = finished.filter((j) => j.status === "failed" && j.finished_at && now - j.finished_at < 600);

  useEffect(() => {
    if (!anchor && !active.length) return;
    const t = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(t);
  }, [anchor, active.length]);

  if (!mine.length) return null;
  const j = running[0] ?? queued[0];
  const pct = j?.total ? Math.round((j.done / j.total) * 100) : null;
  return (
    <>
      <button className={cls("activity", !!anchor && "open")} onClick={(e) => setAnchor(anchor ? null : e.currentTarget)} aria-expanded={!!anchor}
        title="İşlerin ayrıntısı" aria-live="polite">
        {j ? (
          <>
            <div className="row" style={{ gap: 6 }}>
              <b className="grow ellipsis">{j.status === "queued" ? "Sırada bekliyor" : STAGE[j.stage] ?? TITLE[j.type] ?? j.title}</b>
              {j.status === "running" && <span>{isBytes(j) || isTime(j) ? (pct !== null ? `%${pct}` : "") : counts(j)}</span>}
            </div>
            <div className={cls("bar", (pct === null || j.status === "queued") && "indet")}><span style={{ width: `${pct ?? 0}%` }} /></div>
            {active.length > 1 && <span>ve {active.length - 1} iş daha</span>}
          </>
        ) : failed.length ? (
          <div className="row" style={{ gap: 6, color: "var(--danger)" }}><Close size={12} /><b className="grow" style={{ color: "inherit" }}>{failed.length} iş başarısız oldu</b></div>
        ) : (
          <div className="row" style={{ gap: 6 }}><Check size={12} /><span className="grow">Tüm işler tamamlandı</span></div>
        )}
      </button>
      {anchor && (
        <Popover anchor={anchor} onClose={close} width={340} align="start">
          <div className="jobs-pop">
            {running.length > 0 && <div className="lbl">Şu anda</div>}
            {running.map((x) => <JobRow key={x.id} j={x} now={now} />)}
            {queued.length > 0 && <div className="lbl">Sırada</div>}
            {queued.map((x) => <JobRow key={x.id} j={x} now={now} />)}
            {finished.length > 0 && <div className="lbl">Son 10 dakika</div>}
            {finished.slice(0, 12).map((x) => <JobRow key={x.id} j={x} now={now} />)}
            <div className="muted" style={{ fontSize: 11, padding: "8px 10px 4px", lineHeight: 1.4 }}>
              İşler yarıda kalırsa (durdurulur ya da uygulama kapanırsa) yapılan kısım kaybolmaz; kütüphane bir sonraki açılışta kaldığı yerden devam eder.
            </div>
          </div>
        </Popover>
      )}
    </>
  );
}
