import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api, faceUrl } from "../api";
import { assetItem } from "../assetItem";
import { useApp } from "../state";
import { fmtInt } from "../util";
import { Close, Left, Pause, Play, Right } from "./Icons";

type TFace = { id: number; asset_id: number; quality: number; taken_at: string };

const MONTHS = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"];
const STEP_MS = 1100;

/** Doğum tarihine göre o tarihteki yaş: "8 aylık", "3 yaşında". */
export function ageAt(birth: string | null, iso: string): string | null {
  if (!birth) return null;
  const b = new Date(birth.slice(0, 10) + "T00:00:00");
  const d = new Date(iso.slice(0, 10) + "T00:00:00");
  if (isNaN(b.getTime()) || d < b) return null;
  let months = (d.getFullYear() - b.getFullYear()) * 12 + d.getMonth() - b.getMonth();
  if (d.getDate() < b.getDate()) months--;
  if (months < 1) return "yeni doğmuş";
  if (months < 24) return `${months} aylık`;
  return `${Math.floor(months / 12)} yaşında`;
}

function yearAges(birth: string | null, faces: TFace[]): string | null {
  if (!birth || !faces.length) return null;
  const a = ageAt(birth, faces[0].taken_at);
  const z = ageAt(birth, faces[faces.length - 1].taken_at);
  if (!a || !z) return a ?? z;
  if (a === z) return a;
  const n = (s: string) => s.match(/^(\d+) yaşında$/)?.[1];
  return n(a) && n(z) ? `${n(a)}–${n(z)} yaşında` : `${a} – ${z}`;
}

/** Ayda bir yüz (en neti): gösterim için. */
function monthly(faces: TFace[]): TFace[] {
  const by = new Map<string, TFace>();
  for (const f of faces) {
    const k = f.taken_at.slice(0, 7);
    const cur = by.get(k);
    if (!cur || f.quality > cur.quality) by.set(k, f);
  }
  return [...by.values()].sort((a, b) => a.taken_at.localeCompare(b.taken_at));
}

function Slideshow({ repoId, name, birth, frames, onClose }: { repoId: string; name: string; birth: string | null; frames: TFace[]; onClose: () => void }) {
  const [i, setI] = useState(0);
  const [playing, setPlaying] = useState(true);
  const last = frames.length - 1;
  useEffect(() => {
    if (!playing) return;
    if (i >= last) { setPlaying(false); return; }
    const t = setTimeout(() => setI((x) => Math.min(last, x + 1)), STEP_MS);
    return () => clearTimeout(t);
  }, [i, playing, last]);
  useEffect(() => {
    // Sonraki kareyi önceden yükle (geçişte boş daire görünmesin)
    if (frames[i + 1]) new Image().src = faceUrl(repoId, frames[i + 1].id, 400);
  }, [i, frames, repoId]);
  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      else if (e.key === "ArrowRight") { setPlaying(false); setI((x) => Math.min(last, x + 1)); }
      else if (e.key === "ArrowLeft") { setPlaying(false); setI((x) => Math.max(0, x - 1)); }
      else if (e.key === " ") { setPlaying((p) => !p); if (i >= last) setI(0); }
      else return;
      e.preventDefault();
      e.stopPropagation();
    };
    window.addEventListener("keydown", k, true);
    return () => window.removeEventListener("keydown", k, true);
  }, [onClose, last, i]);
  const f = frames[i];
  const age = ageAt(birth, f.taken_at);
  return (
    <div className="tl-show" role="dialog" aria-modal="true" aria-label={`${name} yıllar içinde`}>
      <div className="face"><img key={f.id} src={faceUrl(repoId, f.id, 400)} alt="" /></div>
      <div className="cap">
        {MONTHS[Number(f.taken_at.slice(5, 7)) - 1]} {f.taken_at.slice(0, 4)}
        <small>{age ? `${name}, ${age}` : name}</small>
      </div>
      <div className="bar"><i style={{ width: `${last ? (i / last) * 100 : 100}%` }} /></div>
      <div className="ctl">
        <button className="btn" onClick={() => { setPlaying(false); setI((x) => Math.max(0, x - 1)); }} aria-label="Önceki"><Left size={15} /></button>
        <button className="btn" onClick={() => { if (i >= last) setI(0); setPlaying((p) => !p); }} aria-label={playing ? "Duraklat" : "Oynat"}>
          {playing ? <Pause size={14} /> : <Play size={14} />}
        </button>
        <button className="btn" onClick={() => { setPlaying(false); setI((x) => Math.min(last, x + 1)); }} aria-label="Sonraki"><Right size={15} /></button>
        <button className="btn" onClick={onClose} aria-label="Kapat"><Close size={15} /></button>
      </div>
    </div>
  );
}

/** Kişinin yıllar içindeki yüzleri: yıl yıl, yaşıyla; ayda bir yüzle gösterim. */
export function PersonTimeline({ repoId, personId, onClose }: { repoId: string; personId: number; onClose: () => void }) {
  const { openViewer, toast } = useApp();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["personTimeline", repoId, personId], queryFn: () => api.personTimeline(repoId, personId) });
  const [show, setShow] = useState(false);
  const closeShow = useCallback(() => setShow(false), []);
  const faces = q.data?.faces ?? [];
  const birth = q.data?.person.birth_date ?? null;
  const name = q.data?.person.name ?? "";
  const years = useMemo(() => {
    const by = new Map<string, TFace[]>();
    for (const f of faces) by.set(f.taken_at.slice(0, 4), [...(by.get(f.taken_at.slice(0, 4)) ?? []), f]);
    return [...by.entries()].sort((a, b) => b[0].localeCompare(a[0]));
  }, [faces]);
  const frames = useMemo(() => monthly(faces), [faces]);

  useEffect(() => {
    if (show) return;
    const k = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose, show]);

  const setBirth = async (v: string) => {
    try {
      await api.setPersonBirth(repoId, personId, v || null);
      qc.invalidateQueries({ queryKey: ["personTimeline", repoId, personId] });
    } catch (e) {
      toast(String((e as Error).message), { error: true });
    }
  };
  const open = async (f: TFace) => {
    try { openViewer([await assetItem(repoId, f.asset_id)], 0); } catch (e) { toast(String((e as Error).message), { error: true }); }
  };
  const span = faces.length ? `${faces[0].taken_at.slice(0, 4)}–${faces[faces.length - 1].taken_at.slice(0, 4)}` : "";

  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="dialog" style={{ maxWidth: 760, width: "92vw" }} role="dialog" aria-modal="true" aria-labelledby="tl-title">
        <div className="body" style={{ gap: 8, paddingTop: 20 }}>
          <div className="row" style={{ gap: 10, alignItems: "flex-start" }}>
            <div className="grow" style={{ minWidth: 0 }}>
              <h2 id="tl-title" style={{ margin: 0, font: "700 17px/1.3 var(--font-display)" }}>{name} yıllar içinde</h2>
              <span className="muted" style={{ fontSize: 12 }}>
                {faces.length ? `${fmtInt(faces.length)} fotoğraf · ${span}` : q.isLoading ? "Yükleniyor…" : "Tarihli fotoğraf yok"}
              </span>
            </div>
            <label className="row" style={{ gap: 6, fontSize: 12 }}>
              <span className="muted">Doğum tarihi</span>
              <input className="field" type="date" defaultValue={birth ?? ""} key={birth ?? ""} onBlur={(e) => e.target.value !== (birth ?? "") && setBirth(e.target.value)}
                onKeyDown={(e) => { e.stopPropagation(); if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }} title="Yaşı göstermek için (isteğe bağlı)" />
            </label>
            <button className="btn pri" disabled={frames.length < 2} onClick={() => setShow(true)} title="Ayda bir yüzle, yıllar içinde nasıl değiştiğini oynat">
              <Play size={12} /> Oynat
            </button>
          </div>
        </div>
        <div className="body" style={{ paddingTop: 0, maxHeight: "64vh", overflowY: "auto", gap: 16 }}>
          {q.isLoading && <div className="loading-msg" style={{ padding: 40 }}><span className="spin" />Yükleniyor…</div>}
          {years.map(([y, fs]) => {
            const asc = [...fs].sort((a, b) => a.taken_at.localeCompare(b.taken_at));
            const age = yearAges(birth, asc);
            return (
              <section key={y} className="tl-year">
                <h3>{y}<span className="muted" style={{ fontWeight: 400, fontSize: 12 }}>{[age, `${fmtInt(fs.length)} fotoğraf`].filter(Boolean).join(" · ")}</span></h3>
                <div className="tl-grid">
                  {asc.map((f) => (
                    <button key={f.id} onClick={() => open(f)} title={`${f.taken_at.slice(8, 10)} ${MONTHS[Number(f.taken_at.slice(5, 7)) - 1]} ${y}${ageAt(birth, f.taken_at) ? ` · ${ageAt(birth, f.taken_at)}` : ""}`}>
                      <img src={faceUrl(repoId, f.id)} alt="" loading="lazy" />
                    </button>
                  ))}
                </div>
              </section>
            );
          })}
        </div>
        <footer><button className="btn" onClick={onClose}>Kapat</button></footer>
      </div>
      {show && frames.length > 1 && <Slideshow repoId={repoId} name={name} birth={birth} frames={frames} onClose={closeShow} />}
    </div>
  );
}
