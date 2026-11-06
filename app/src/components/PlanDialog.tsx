import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, waitJob } from "../api";
import { useApp } from "../state";
import type { Job } from "../types";
import { cls, fmtBytes, fmtInt } from "../util";
import { Arrow, Close } from "./Icons";

export function PlanDialog() {
  const { plan, showPlan, planDone, repoId, toast, trackJob } = useApp();
  const qc = useQueryClient();
  const [job, setJob] = useState<Job | null>(null);
  const [showList, setShowList] = useState(false);
  if (!plan || !repoId) return null;
  const items = plan.items ?? [];
  const s = plan.summary;
  const close = () => {
    setJob(null);
    setShowList(false);
    showPlan(null);
  };

  const apply = async () => {
    let started = false;
    try {
      const j = await api.applyPlan(repoId, plan.id);
      started = true;
      setJob(j);
      trackJob(j);
      const done = await waitJob(j.id, setJob);
      const r = done.result as { done: number; skipped: number; failed: number };
      await qc.invalidateQueries();
      close();
      planDone?.();
      const verb = plan.kind === "trash" || plan.kind === "dedup" ? "silindi" : "taşındı";
      toast(`${fmtInt(r.done)} öğe ${verb}${r.failed ? `, ${fmtInt(r.failed)} başarısız` : ""}${r.skipped ? `, ${fmtInt(r.skipped)} atlandı` : ""}`, {
        error: r.failed > 0,
        action: { label: "Geri al", run: async () => { const u = await api.undoPlan(repoId, plan.id); trackJob(u); await waitJob(u.id); qc.invalidateQueries(); toast("Geri alındı"); } },
      });
    } catch (e) {
      setJob(null);
      // İş başladıktan sonraki hata iş izleyicisinde zaten bildirilir; aynı uyarı iki kez çıkmasın
      if (started) close();
      else toast(String((e as Error).message), { error: true });
    }
  };

  const reject = async () => {
    try {
      await api.rejectPlan(repoId, plan.id);
      qc.invalidateQueries({ queryKey: ["plans", repoId] });
    } catch {
      /* zaten işlenmiş olabilir */
    }
    close();
  };

  const pct = job && job.total ? Math.round((job.done / job.total) * 100) : 0;
  const del = plan.kind === "trash" || plan.kind === "dedup";
  const n = fmtInt(s.files);
  const title = del ? `${n} dosya silinsin mi?` : `${n} dosya taşınsın mı?`;
  const external = plan.source !== "ui" && plan.source !== "cli";
  const who = plan.source === "assistant" ? "Asistan" : plan.source.startsWith("mcp:") ? plan.source.slice(4) : "";
  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && !job && close()}>
      <section className="dialog" role="alertdialog" aria-modal="true" aria-labelledby="plan-title" style={{ maxWidth: 520 }}>
        <header>
          <h2 id="plan-title">{title}</h2>
        </header>
        <div className="body">
          {external && <span style={{ fontSize: 12 }}><b>{who}</b> öneriyor{plan.title ? `: ${plan.title}` : ""}</span>}
          <span className="muted" style={{ fontSize: 13 }}>
            {del
              ? <>Dosyalar Son silinenler'e taşınır ({fmtBytes(s.bytes)}). İstediğin zaman Geçmiş'ten geri alabilirsin.</>
              : <>Hiçbir dosya silinmez. İstediğin zaman Geçmiş'ten geri alabilirsin.{s.renamed ? ` Aynı adlı ${fmtInt(s.renamed)} dosyaya numara eklenecek.` : ""}</>}
            {s.items !== s.files && <> Düzenleme dosyaları (.AAE, .XMP) da birlikte {del ? "silinir" : "taşınır"}.</>}
          </span>
          <button className="btn ghost" style={{ alignSelf: "flex-start", paddingLeft: 0 }} onClick={() => setShowList((x) => !x)} aria-expanded={showList}>
            {showList ? "Ayrıntıları gizle" : "Ayrıntıları göster"}
          </button>
          {showList && (
            <div className="group" style={{ maxHeight: 300, overflowY: "auto", boxShadow: "inset 0 0 0 1px var(--line)" }}>
              {items.slice(0, 300).map((it) => (
                <div key={it.id} className="mv">
                  <span className="muted" title={it.src}>{it.src}</span>
                  <Arrow size={13} style={{ color: "var(--muted)" }} />
                  <span title={it.dst}>{it.op === "trash" ? "Son silinenler" : it.dst}</span>
                </div>
              ))}
              {(plan.item_count ?? items.length) > 300 && <div className="mv"><span className="muted">ve {fmtInt((plan.item_count ?? 0) - 300)} dosya daha</span></div>}
            </div>
          )}
          {job && (
            <div className="col" style={{ gap: 6 }}>
              <div className={cls("bar", !job.total && "indet")}><span style={{ width: `${pct}%` }} /></div>
              <span className="muted" style={{ fontSize: 12 }}>{del ? "Siliniyor" : "Taşınıyor"}… {job.total ? `${fmtInt(job.done)} / ${fmtInt(job.total)}` : ""}</span>
            </div>
          )}
        </div>
        <footer>
          <button className="btn" onClick={plan.status === "pending" ? reject : close} disabled={!!job}>{external ? "Reddet" : "Vazgeç"}</button>
          <button className={cls("btn", del ? "danger solid" : "pri")} onClick={apply} disabled={!!job} autoFocus>{del ? "Sil" : "Taşı"}</button>
        </footer>
      </section>
    </div>
  );
}

export function Toasts() {
  const { toasts, dismissToast } = useApp();
  return (
    <div className="toasts" aria-live="polite">
      {toasts.map((t) => (
        <div key={t.id} className={cls("toast", t.error && "err")}>
          <span className="grow">{t.text}</span>
          {t.action && <button className="btn sm" onClick={() => { t.action!.run(); dismissToast(t.id); }}>{t.action.label}</button>}
          <button className="ibtn" onClick={() => dismissToast(t.id)} aria-label="Kapat"><Close size={16} /></button>
        </div>
      ))}
    </div>
  );
}
