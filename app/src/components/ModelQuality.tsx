import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, waitJob, type Tier, type TierKind, type TierStatus, type TierTrial } from "../api";
import { useApp } from "../state";
import { cls, fmtBytes, fmtInt, fmtSpan } from "../util";

/**
 * Model kalitesi (Standart / Yüksek). Yüksek kademe kullanıcının tahminine bırakılmaz:
 * cihaz uygun değilse seçilemez; seçmeden önce bu kütüphanenin birkaç öğesiyle denenir ve süre tahmini gösterilir.
 */
export function ModelQuality({ Section, SRow }: {
  Section: (p: { title?: string; children: React.ReactNode; foot?: React.ReactNode }) => React.ReactElement;
  SRow: (p: { label: string; hint?: React.ReactNode; children?: React.ReactNode }) => React.ReactElement;
}) {
  const { repoId, toast } = useApp();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["modelTiers", repoId], queryFn: () => api.modelTiers(repoId), refetchInterval: 10_000 });
  const [dialog, setDialog] = useState<{ kind: TierKind; tier: Tier } | null>(null);
  const d = q.data;
  if (!d) return null;
  const hw = d.hardware;
  const remove = async (kind: TierKind) => {
    try {
      await api.tierRemove(kind, "high");
      toast("Yüksek kalite modeli silindi");
      qc.invalidateQueries({ queryKey: ["modelTiers"] });
    } catch (e) {
      toast(String((e as Error).message), { error: true });
    }
  };
  return (
    <>
      <Section title="Model kalitesi" foot={<>Bu Mac: {hw.chip}{hw.ram_gb ? ` · ${hw.ram_gb} GB bellek` : ""} · {fmtBytes(hw.free_disk)} boş disk. Yüksek kalite daha büyük bir model indirir; Türkçe konuşmalarda çok daha az hata yapar ama daha yavaştır.</>}>
        {(Object.keys(d.kinds) as TierKind[]).map((kind) => {
          const k = d.kinds[kind];
          const high = k.options.find((o) => o.tier === "high")!;
          const pr = d.progress?.[kind];
          const switching = pr && pr.total > 0 && pr.done < pr.total && k.current === "high";
          const hint = [
            k.options.find((o) => o.tier === k.current)?.model,
            switching ? `yeni modelle döküm: ${fmtInt(pr!.done)} / ${fmtInt(pr!.total)} video` : null,
            !high.allowed && k.current === "standard" ? `Yüksek: ${high.reason}` : null,
          ].filter(Boolean).join(" · ");
          return (
            <SRow key={kind} label={k.label} hint={hint}>
              <div className="row" style={{ gap: 6 }}>
                {high.installed && k.current === "standard" && !switching && (
                  <button className="btn sm ghost" onClick={() => remove(kind)} title="Kullanılmayan yüksek kalite modelini silerek yer aç">Sil ({fmtBytes(high.size)})</button>
                )}
                <div className="seg">
                  {(["standard", "high"] as Tier[]).map((t) => (
                    <button key={t} className={cls(k.current === t && "on")} disabled={t === "high" && !high.allowed && k.current !== "high"}
                      title={t === "high" && !high.allowed ? high.reason ?? "" : ""}
                      onClick={() => k.current !== t && setDialog({ kind, tier: t })}>
                      {t === "standard" ? "Standart" : "Yüksek"}
                    </button>
                  ))}
                </div>
              </div>
            </SRow>
          );
        })}
      </Section>
      {dialog && repoId && <TierDialog status={d} kind={dialog.kind} tier={dialog.tier} repoId={repoId} onClose={() => setDialog(null)} />}
    </>
  );
}

function TierDialog({ status, kind, tier, repoId, onClose }: { status: TierStatus; kind: TierKind; tier: Tier; repoId: string; onClose: () => void }) {
  const { toast, trackJob } = useApp();
  const qc = useQueryClient();
  const k = status.kinds[kind];
  const opt = k.options.find((o) => o.tier === tier)!;
  const [phase, setPhase] = useState<"intro" | "trying" | "result">("intro");
  const [prog, setProg] = useState<{ stage: string; done: number; total: number } | null>(null);
  const [trial, setTrial] = useState<TierTrial | null>(null);
  const [busy, setBusy] = useState(false);
  const toHigh = tier === "high";
  const left = Math.max(0, opt.size - opt.downloaded);

  const tryIt = async () => {
    setPhase("trying");
    try {
      const j = await api.tierTry(kind, tier, repoId);
      trackJob(j);
      const done = await waitJob(j.id, (x) => setProg({ stage: x.stage, done: x.done, total: x.total }));
      if (done.status !== "done") throw new Error(done.error || "Deneme tamamlanamadı");
      setTrial(done.result as TierTrial);
      setPhase("result");
      qc.invalidateQueries({ queryKey: ["modelTiers"] });
    } catch (e) {
      toast(String((e as Error).message), { error: true });
      setPhase("intro");
    }
  };
  const apply = async () => {
    setBusy(true);
    try {
      await api.tierApply(kind, tier);
      qc.invalidateQueries({ queryKey: ["modelTiers"] });
      qc.invalidateQueries({ queryKey: ["smart"] });
      qc.invalidateQueries({ queryKey: ["jobs"] });
      toast(toHigh ? `Yüksek kalite seçildi. Kütüphane arka planda yeniden analiz ediliyor; bitene kadar şimdiki model kullanılır.`
        : "Standart kaliteye dönüldü. Kütüphane arka planda yeniden analiz ediliyor.");
      onClose();
    } catch (e) {
      toast(String((e as Error).message), { error: true });
      setBusy(false);
    }
  };

  const about = (x: number | null | undefined) => (x != null && x < 60 ? <>bir dakikadan <b>kısa</b> sürer</> : <>yaklaşık <b>{fmtSpan(x)}</b> sürer</>);
  const secs = (x: number | null | undefined) => (x == null ? "—" : x < 1 ? `${x.toFixed(2).replace(".", ",")} sn` : `${x.toFixed(1).replace(".", ",")} sn`);
  const stageText = prog?.stage === "trial" ? "Bu kütüphanedeki birkaç öğeyle deneniyor…" : prog && prog.total ? `İndiriliyor… ${fmtBytes(prog.done)} / ${fmtBytes(prog.total)}` : "Hazırlanıyor…";

  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && phase !== "trying" && onClose()}>
      <div className="dialog" style={{ maxWidth: 480 }} role="dialog" aria-modal="true" aria-labelledby="tier-title">
        <header><h2 id="tier-title">{k.label}: {toHigh ? "Yüksek kalite" : "Standart"}</h2></header>
        <div className="body" style={{ gap: 10 }}>
          {!toHigh ? (
            <span>Standart modele dönülür. Bundan sonraki konuşma dökümleri standart modelle yapılır.</span>
          ) : phase === "intro" ? (
            <>
              <span><b>{opt.model}</b> · {opt.license}</span>
              <span className="muted">{left ? `${fmtBytes(left)} indirilecek.` : "Model indirilmiş."} Seçmeden önce bu kütüphanedeki bir videonun sesiyle denenir; bu Mac'teki gerçek hızı ve tüm kütüphanenin ne kadar süreceğini görüp sonra karar verirsin.</span>
            </>
          ) : phase === "trying" ? (
            <div className="col" style={{ gap: 8 }}>
              <div className="row" style={{ gap: 8 }}><span className="spin" />{stageText}</div>
              {prog && prog.stage !== "trial" && prog.total > 0 && <div className="bar"><span style={{ width: `${Math.round((prog.done / prog.total) * 100)}%` }} /></div>}
              {left > 0 && <span className="muted" style={{ fontSize: 12 }}>Pencereyi kapatma; indirme yarıda kalırsa bir sonraki denemede kaldığı yerden sürer.</span>}
            </div>
          ) : trial ? (
            trial.rtf == null ? (
              <span>Bu kütüphanede konuşma içeren bir video bulunamadığı için hız ölçülemedi. Model indirildi; geçiş yaparsan yeni dökümler bu modelle yapılır.</span>
            ) : (
              <>
                <span>1 dakikalık konuşma bu Mac'te yaklaşık <b>{secs((trial.rtf ?? 0) * 60)}</b>'de yazıya dökülür{trial.rtf_current ? ` (şimdiki model: ${secs(trial.rtf_current * 60)})` : ""}.</span>
                <span>Bu kütüphanedeki videoların tamamı {about(trial.estimate_s)}.</span>
                {!trial.transcripts_on && <span className="muted">Konuşma dökümü bu kütüphanede şu an kapalı; açtığında bu model kullanılır.</span>}
              </>
            )
          ) : null}
        </div>
        <footer>
          <button className="btn" onClick={onClose} disabled={phase === "trying"}>{phase === "result" ? "Şimdilik vazgeç" : "Vazgeç"}</button>
          {!toHigh ? (
            <button className="btn pri" disabled={busy} onClick={apply}>Standart'a dön</button>
          ) : phase === "result" ? (
            <button className="btn pri" disabled={busy} onClick={apply}>Geçiş yap</button>
          ) : (
            <button className="btn pri" disabled={phase === "trying" || !opt.allowed} onClick={tryIt}>{left ? "İndir ve dene" : "Dene"}</button>
          )}
        </footer>
      </div>
    </div>
  );
}
