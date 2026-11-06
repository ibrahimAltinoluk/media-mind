import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, waitJob } from "../api";
import { useApp } from "../state";
import type { Probe } from "../types";
import { fmtBytes, fmtInt } from "../util";
import { Folder, Shield } from "./Icons";

export function AddRepoDialog({ probe, onClose, onPickAgain }: { probe: Probe; onClose: () => void; onPickAgain: () => void }) {
  const { setRepoId, toast, trackJob } = useApp();
  const qc = useQueryClient();
  const [name, setName] = useState(probe.name);
  const [autoTrack, setAutoTrack] = useState(true);
  const [busy, setBusy] = useState(false);

  const create = async () => {
    setBusy(true);
    try {
      const e = await api.addRepo(probe.path, name.trim() || probe.name);
      await qc.invalidateQueries({ queryKey: ["repos"] });
      setRepoId(e.id);
      onClose();
      // Seçim kalıcı ayardır: sonradan klasöre eklenen dosyalar da kendiliğinden kütüphaneye girer
      await api.updateConfig(e.id, { scan: { auto_track: autoTrack } });
      const scan = await api.scan(e.id);
      trackJob(scan);
      if (autoTrack) {
        await waitJob(scan.id);
        trackJob(await api.track(e.id));
      }
    } catch (err) {
      toast(String((err as Error).message), { error: true });
      setBusy(false);
    }
  };

  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <section className="dialog" role="dialog" aria-modal="true" aria-labelledby="add-title" style={{ maxWidth: 480 }}>
        <header>
          <h2 id="add-title">Kütüphane olarak ekle</h2>
        </header>
        <div className="body">
          <div className="group" style={{ boxShadow: "inset 0 0 0 1px var(--line)" }}>
            <div className="grow-row">
              <Folder size={18} style={{ color: "var(--accent)", flex: "none" }} />
              <div className="k">
                <span className="ellipsis" style={{ display: "block" }} title={probe.path}>{probe.path}</span>
                <small>{fmtInt(probe.photos)} fotoğraf, {fmtInt(probe.videos)} video · {fmtBytes(probe.bytes)}{!probe.complete && " (tahmini)"}</small>
              </div>
              <button className="btn sm" onClick={onPickAgain}>Değiştir…</button>
            </div>
            <label className="grow-row">
              <span className="k">Ad</span>
              <div className="field" style={{ width: 220 }}><input value={name} onChange={(e) => setName(e.target.value)} autoFocus /></div>
            </label>
            <div className="grow-row">
              <div className="k">Dosyaları kendiliğinden ekle<small>Şimdiki dosyalar hemen, klasöre sonradan eklediklerin de geldiği anda kütüphaneye girer. Kapatırsan yeni dosyalar “Yeni eklenenler”de onayını bekler.</small></div>
              <label className="switch"><input type="checkbox" role="switch" checked={autoTrack} onChange={(e) => setAutoTrack(e.target.checked)} aria-label="Dosyaları kendiliğinden ekle" /><span /></label>
            </div>
          </div>
          <div className="row" style={{ alignItems: "flex-start", gap: 8, fontSize: 12 }}>
            <Shield size={16} style={{ color: "var(--ok)", flex: "none", marginTop: 1 }} />
            <span className="muted">Dosyaların yerinde kalır, hiçbiri değiştirilmez. MediaMind bilgilerini bu klasördeki gizli <span className="mono">.mediamind</span> dizininde tutar; disk başka bir Mac'e takılsa da kütüphane tanınır.</span>
          </div>
        </div>
        <footer>
          <button className="btn" onClick={onClose}>Vazgeç</button>
          <button className="btn pri" onClick={create} disabled={busy}>Ekle</button>
        </footer>
      </section>
    </div>
  );
}
