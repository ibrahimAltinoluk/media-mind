import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { api, displayUrl, thumbUrl } from "../api";
import { useApp } from "../state";
import type { AssetItem } from "../types";
import { basename, cls, fmtDate, fmtDuration, fmtInt, placeText } from "../util";
import { MetaList, ModelTags, PhotoText, Rating, Similar, Tags, useTrashFlow } from "./DetailPanel";
import { FaceNames } from "../pages/People";
import { PersonPicker } from "./PersonPicker";
import { VideoPlayer } from "./VideoPlayer";
import { Ellipsis, Heart, Info, Left, Pause, Right } from "./Icons";
import { Popover } from "./Popover";
import { revealInFinder } from "./Sidebar";

const preloaded = new Map<string, HTMLImageElement>();
function preload(url: string) {
  if (preloaded.has(url)) return;
  const im = new window.Image();
  im.decoding = "async";
  im.src = url;
  preloaded.set(url, im);
  if (preloaded.size > 60) preloaded.delete(preloaded.keys().next().value!);
}

function Photo({ repoId, item, zoom, onToggleZoom }: { repoId: string; item: AssetItem; zoom: boolean; onToggleZoom: () => void }) {
  const mid = thumbUrl(repoId, item.blake3, "m");
  const large = thumbUrl(repoId, item.blake3, "l");
  // Tam çözünürlük sunucuda doğru yöne çevrilir (EXIF yönü); tarayıcının yorumuna bırakılmaz
  const full = displayUrl(repoId, item.blake3);
  const [src, setSrc] = useState(mid);
  const [ratio, setRatio] = useState<number | null>(null); // en / boy (doğru yönde)
  const ref = useRef<HTMLImageElement>(null);
  // Yakınlaştırma odağı: tıklanan noktanın görüntüdeki oranı ve ekrandaki yeri
  const focus = useRef<{ rx: number; ry: number; cx: number; cy: number } | null>(null);

  // Öğe değişince küçük önizlemeden başla
  useEffect(() => {
    setSrc(mid);
    setRatio(null);
  }, [mid]);
  // Daha keskin sürümü arka planda yükle; yüklenince yerine koy (boyut sabit kaldığı için görüntü zıplamaz)
  useEffect(() => {
    let alive = true;
    const target = zoom ? full : large;
    const im = new window.Image();
    im.onload = () => alive && setSrc(target);
    im.src = target;
    return () => { alive = false; };
  }, [large, full, zoom]);

  // Yakınlaştırınca görüntü gerçek boyutunda gösterilir (boyut baştan sabitlenir); tıklanan nokta imlecin altında kalır
  const longSide = Math.max(item.width ?? 0, item.height ?? 0) || 4000;
  const size = ratio ? (ratio >= 1 ? { w: longSide, h: Math.round(longSide / ratio) } : { w: Math.round(longSide * ratio), h: longSide }) : null;
  useLayoutEffect(() => {
    if (!zoom || !size) return;
    const stage = ref.current?.closest(".stage") as HTMLElement | null;
    if (!stage) return;
    const f = focus.current ?? { rx: 0.5, ry: 0.5, cx: stage.clientWidth / 2, cy: stage.clientHeight / 2 };
    stage.scrollLeft = Math.max(0, f.rx * size.w - f.cx);
    stage.scrollTop = Math.max(0, f.ry * size.h - f.cy);
    focus.current = null;
  }, [zoom]); // eslint-disable-line react-hooks/exhaustive-deps

  const onClick = (e: React.MouseEvent<HTMLImageElement>) => {
    if (!zoom) {
      const r = e.currentTarget.getBoundingClientRect();
      const stage = e.currentTarget.closest(".stage")!.getBoundingClientRect();
      focus.current = { rx: (e.clientX - r.left) / r.width, ry: (e.clientY - r.top) / r.height, cx: e.clientX - stage.left, cy: e.clientY - stage.top };
    }
    onToggleZoom();
  };
  return (
    <img key={item.blake3} ref={ref} src={src} className={zoom ? "full" : "fit"} onClick={onClick} alt={basename(item.rel_path)} draggable={false}
      style={zoom && size ? { width: size.w, height: size.h } : undefined}
      onLoad={(e) => { const im = e.currentTarget; if (!ratio && im.naturalWidth) setRatio(im.naturalWidth / im.naturalHeight); }} />
  );
}

/** Yüz kutuları: görüntünün ekrandaki konumunu ölçüp göreli koordinatları yerleştirir. */
function FaceOverlay({ faces }: { faces: { id: number; x1: number; y1: number; x2: number; y2: number; name: string | null }[] }) {
  const [box, setBox] = useState<DOMRect | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const stage = ref.current?.closest(".stage");
    const img = stage?.querySelector("img");
    if (!img || !stage) return;
    const update = () => {
      const r = img.getBoundingClientRect();
      const s = stage.getBoundingClientRect();
      setBox(new DOMRect(r.left - s.left, r.top - s.top, r.width, r.height));
    };
    update();
    const ro = new ResizeObserver(update);
    ro.observe(img);
    img.addEventListener("load", update);
    return () => { ro.disconnect(); img.removeEventListener("load", update); };
  }, [faces]);
  return (
    <div ref={ref} style={{ position: "absolute", top: 0, right: 0, bottom: 0, left: 0, pointerEvents: "none" }}>
      {box && faces.map((f) => (
        <div key={f.id} style={{ position: "absolute", left: box.x + f.x1 * box.width, top: box.y + f.y1 * box.height, width: (f.x2 - f.x1) * box.width, height: (f.y2 - f.y1) * box.height,
          border: `2px solid ${f.name ? "#f0b443" : "#7fb0ff"}`, borderRadius: 6 }}>
          <span style={{ position: "absolute", left: -2, top: "100%", marginTop: 4, background: f.name ? "#f0b443" : "#7fb0ff", color: "#111", fontSize: 12, fontWeight: 600, padding: "2px 8px", borderRadius: 5, whiteSpace: "nowrap" }}>
            {f.name ?? "Bu kim?"}
          </span>
        </div>
      ))}
    </div>
  );
}

/** Kişi etiketleme katmanı: fotoğrafın üzerine kutu çizilir, ardından kişi seçilir. */
function TagLayer({ repoId, onPick, onCancel }: { repoId: string; onPick: (box: [number, number, number, number], name: string) => void; onCancel: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const [img, setImg] = useState<DOMRect | null>(null);
  // Sürükleme durumu ref'te tutulur: hızlı olaylar yeniden çizimden önce gelse de kaybolmaz
  const startRef = useRef<{ x: number; y: number } | null>(null);
  const rectRef = useRef<{ x: number; y: number; w: number; h: number } | null>(null);
  const [rect, setRectState] = useState<{ x: number; y: number; w: number; h: number } | null>(null);
  const [done, setDone] = useState(false);
  const setRect = (r: { x: number; y: number; w: number; h: number } | null) => { rectRef.current = r; setRectState(r); };
  useEffect(() => {
    const stage = ref.current?.closest(".stage");
    const el = stage?.querySelector("img");
    if (!el || !stage) return;
    const r = el.getBoundingClientRect();
    const st = stage.getBoundingClientRect();
    setImg(new DOMRect(r.left - st.left, r.top - st.top, r.width, r.height));
  }, []);
  const local = (e: React.MouseEvent) => {
    const b = ref.current!.getBoundingClientRect();
    const x = Math.max(img!.x, Math.min(img!.x + img!.width, e.clientX - b.left));
    const y = Math.max(img!.y, Math.min(img!.y + img!.height, e.clientY - b.top));
    return { x, y };
  };
  const down = (e: React.MouseEvent) => {
    if (!img || done) return;
    const p = local(e);
    startRef.current = p;
    setRect({ x: p.x, y: p.y, w: 0, h: 0 });
  };
  const move = (e: React.MouseEvent) => {
    const st = startRef.current;
    if (!st || done || !img) return;
    const p = local(e);
    setRect({ x: Math.min(st.x, p.x), y: Math.min(st.y, p.y), w: Math.abs(p.x - st.x), h: Math.abs(p.y - st.y) });
  };
  const up = (e: React.MouseEvent) => {
    if (!startRef.current) return;
    move(e); // son konumu da işle
    startRef.current = null;
    const r = rectRef.current;
    if (r && r.w > 10 && r.h > 10) setDone(true);
    else setRect(null);
  };
  const box = (): [number, number, number, number] => [
    (rect!.x - img!.x) / img!.width, (rect!.y - img!.y) / img!.height,
    (rect!.x + rect!.w - img!.x) / img!.width, (rect!.y + rect!.h - img!.y) / img!.height,
  ];
  return (
    <div ref={ref} onMouseDown={down} onMouseMove={move} onMouseUp={up}
      style={{ position: "absolute", top: 0, right: 0, bottom: 0, left: 0, zIndex: 3, cursor: done ? "default" : "crosshair" }}>
      <div style={{ position: "absolute", top: 12, left: "50%", transform: "translateX(-50%)", background: "rgba(20,22,25,.85)", color: "#eceef1", padding: "6px 12px", borderRadius: 8, fontSize: 13, pointerEvents: "none" }}>
        {done ? "Bu kim?" : "Kişinin üzerine bir kutu çiz"} · Esc ile çık
      </div>
      {rect && (
        <div style={{ position: "absolute", left: rect.x, top: rect.y, width: rect.w, height: rect.h, border: "2px solid #f0b443", borderRadius: 6, background: "rgba(240,180,67,.12)" }} />
      )}
      {done && rect && (
        <div onMouseDown={(e) => e.stopPropagation()} style={{ position: "absolute", left: Math.max(8, rect.x), top: rect.y + rect.h + 8, width: 260, display: "flex" }}>
          <PersonPicker repoId={repoId} autoFocus onPick={(n) => onPick(box(), n)} onCancel={onCancel} placeholder="Bu kim? Ara ya da yeni ad yaz" />
        </div>
      )}
    </div>
  );
}

export function Viewer() {
  const { viewer, closeViewer, setViewerIndex, repoId, openViewer, toast } = useApp();
  const qc = useQueryClient();
  const trash = useTrashFlow();
  const [info, setInfo] = useState(() => localStorage.getItem("mm.viewer.info") !== "0");
  const [zoom, setZoom] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [showFaces, setShowFaces] = useState(false);
  const [tagging, setTagging] = useState(false);
  const [more, setMore] = useState<HTMLElement | null>(null);
  const closeMore = useCallback(() => setMore(null), []);
  const stripRef = useRef<HTMLDivElement>(null);
  const items = viewer?.items ?? [];
  const index = viewer?.index ?? 0;
  const item = items[index];
  const detail = useQuery({ queryKey: ["asset", repoId, item?.asset_id], queryFn: () => api.asset(repoId!, item!.asset_id), enabled: !!item && !!repoId });
  const repoRoot = useQuery({ queryKey: ["summary", repoId], queryFn: () => api.summary(repoId!), enabled: !!repoId }).data?.repo.last_path;

  const go = (d: number) => {
    if (!items.length) return;
    setZoom(false);
    setViewerIndex(Math.max(0, Math.min(items.length - 1, index + d)));
  };

  useEffect(() => localStorage.setItem("mm.viewer.info", info ? "1" : "0"), [info]);

  // Komşu kareleri önceden yükle
  useEffect(() => {
    if (!repoId) return;
    for (const d of [1, -1, 2, -2]) {
      const it = items[index + d];
      if (it) {
        preload(thumbUrl(repoId, it.blake3, "m"));
        if (Math.abs(d) === 1 && it.kind === "photo") preload(thumbUrl(repoId, it.blake3, "l"));
      }
    }
  }, [index, items, repoId]);

  // Slayt gösterisi
  useEffect(() => {
    if (!playing) return;
    const t = setInterval(() => {
      if (index >= items.length - 1) setPlaying(false);
      else setViewerIndex(index + 1);
    }, 3500);
    return () => clearInterval(t);
  }, [playing, index, items.length, setViewerIndex]);

  const d = detail.data;
  const setMeta = async (body: { rating?: number; favorite?: boolean }) => {
    if (!d || !repoId) return;
    await api.updateAsset(repoId, d.id, body);
    qc.invalidateQueries({ queryKey: ["asset", repoId, d.id] });
    qc.invalidateQueries({ queryKey: ["assets", repoId] });
  };

  const trashMap = viewer?.trash;
  const trashId = item && trashMap ? trashMap[item.file_id] : undefined;
  const [purgeAsk, setPurgeAsk] = useState(false);
  const dropCurrent = () => {
    if (!item) return;
    const rest = items.filter((x) => x.file_id !== item.file_id);
    if (!rest.length) closeViewer();
    else openViewer(rest, Math.min(index, rest.length - 1), trashMap ? { trash: trashMap } : undefined);
  };
  const restoreCurrent = async () => {
    if (trashId === undefined) return;
    try {
      await api.restoreTrash(repoId!, [trashId]);
      toast("Geri getirildi");
      qc.invalidateQueries();
      dropCurrent();
    } catch (e) {
      toast(String((e as Error).message), { error: true });
    }
  };
  const purgeCurrent = async () => {
    setPurgeAsk(false);
    if (trashId === undefined) return;
    try {
      await api.deleteTrash(repoId!, [trashId]);
      toast("Kalıcı olarak silindi");
      qc.invalidateQueries();
      dropCurrent();
    } catch (e) {
      toast(String((e as Error).message), { error: true });
    }
  };
  const removeCurrent = () => {
    if (!item) return;
    // Son silinenler'den açılan dosya zaten silinmiş: yeniden silmek yerine kalıcı silmeyi sor
    if (trashId !== undefined) return setPurgeAsk(true);
    trash([item.file_id], dropCurrent);
  };

  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (!viewer || (e.target as HTMLElement)?.closest("input, textarea, select")) return;
      const inVideo = (e.target as HTMLElement)?.tagName === "VIDEO";
      switch (e.key) {
        case "ArrowRight": go(1); break;
        case "ArrowLeft": go(-1); break;
        case "Escape": if (purgeAsk) setPurgeAsk(false); else if (tagging) setTagging(false); else if (zoom) setZoom(false); else closeViewer(); break;
        case " ": if (item?.kind === "video" || inVideo) return; setPlaying((p) => !p); break;
        case "i": case "I": setInfo((v) => !v); break;
        case "z": case "Z": if (item?.kind === "photo") setZoom((z) => !z); break;
        case "f": case "F": setMeta({ favorite: !d?.favorite }); break;
        case "y": case "Y": setShowFaces((v) => !v); break;
        case "k": case "K": if (item?.kind === "photo") { setZoom(false); setTagging((v) => !v); } break;
        case "Delete": case "Backspace": removeCurrent(); break;
        case "0": case "1": case "2": case "3": case "4": case "5": setMeta({ rating: Number(e.key) }); break;
        default: return;
      }
      e.preventDefault();
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  });

  // Film şeridinde aktif kareyi ortala
  useEffect(() => {
    stripRef.current?.querySelector(".on")?.scrollIntoView({ inline: "center", block: "nearest" });
  }, [index]);

  const strip = useMemo(() => {
    const from = Math.max(0, index - 25);
    return items.slice(from, index + 26).map((it, k) => ({ it, i: from + k }));
  }, [items, index]);

  if (!viewer || !item || !repoId) return null;
  return (
    <div className="viewer" role="dialog" aria-label="Görüntüleyici">
      <header data-tauri-drag-region>
        <button className="vback" onClick={closeViewer} aria-label="Kapat (Esc)" title="Kapat (Esc)"><Left size={16} />Geri</button>
        <div className="vtitle" data-tauri-drag-region>
          <b className="ellipsis">{item.taken_at ? fmtDate(item.taken_at, false) : basename(item.rel_path)}</b>
          <span className="ellipsis">{[trashId !== undefined ? "Son silinenler" : null, placeText(item), item.taken_at?.slice(11, 16), items.length > 1 ? `${fmtInt(index + 1)} / ${fmtInt(items.length)}` : null].filter(Boolean).join(" · ")}</span>
        </div>
        <span className="grow" data-tauri-drag-region />
        {playing && <button className="ibtn on" onClick={() => setPlaying(false)} aria-label="Slaytı durdur"><Pause size={16} /></button>}
        {trashId !== undefined
          ? <button className="vback" onClick={restoreCurrent} title="Eski yerine geri getir">Geri getir</button>
          : <button className={cls("ibtn", !!d?.favorite && "on")} onClick={() => setMeta({ favorite: !d?.favorite })} aria-label="Favori (F)" title="Favori (F)"><Heart size={18} filled={!!d?.favorite} /></button>}
        <button className={cls("ibtn", info && "on")} onClick={() => setInfo((v) => !v)} aria-label="Bilgi (I)" title="Bilgi (I)"><Info size={18} /></button>
        <button className={cls("ibtn", !!more && "on")} onClick={(e) => setMore(more ? null : e.currentTarget)} aria-label="Diğer" title="Diğer"><Ellipsis size={18} /></button>
        {more && (
          <Popover anchor={more} onClose={closeMore} width={240}>
            {item.kind === "photo" && (
              <>
                <button className="mitem" onClick={() => { closeMore(); setZoom((z) => !z); }}>{zoom ? "Sığdır" : "Gerçek boyut"}<span className="kbd-hint">Z</span></button>
                <button className="mitem" onClick={() => { closeMore(); setPlaying((p) => !p); }}>{playing ? "Slaytı durdur" : "Slayt gösterisi"}<span className="kbd-hint">Boşluk</span></button>
                <div className="msep" />
                <button className="mitem" disabled={!d?.faces.length} onClick={() => { closeMore(); setShowFaces((v) => !v); }}>{showFaces ? "Yüzleri gizle" : "Yüzleri göster"}<span className="kbd-hint">Y</span></button>
                <button className="mitem" onClick={() => { closeMore(); setZoom(false); setTagging(true); }}>Kişi etiketle…<span className="kbd-hint">K</span></button>
                <div className="msep" />
              </>
            )}
            {trashId !== undefined ? (
              <>
                <button className="mitem" onClick={() => { closeMore(); restoreCurrent(); }}>Geri getir</button>
                <div className="msep" />
                <button className="mitem danger" onClick={() => { closeMore(); setPurgeAsk(true); }}>Kalıcı olarak sil…<span className="kbd-hint">⌫</span></button>
              </>
            ) : (
              <>
                {d?.text && <button className="mitem" onClick={async () => { closeMore(); try { await navigator.clipboard.writeText(d.text!); toast("Fotoğraftaki yazı kopyalandı"); } catch { toast("Kopyalanamadı", { error: true }); } }}>Fotoğraftaki yazıyı kopyala</button>}
            <button className="mitem" onClick={() => { closeMore(); if (repoRoot) revealInFinder(`${repoRoot}/${item.rel_path}`); }}>Finder'da göster</button>
                <div className="msep" />
                <button className="mitem danger" onClick={() => { closeMore(); removeCurrent(); }}>Sil<span className="kbd-hint">⌫</span></button>
              </>
            )}
          </Popover>
        )}
      </header>
      {tagging && <div className="vhint">Kişinin yüzünün çevresine bir kutu çiz. Vazgeçmek için Esc.</div>}
      {purgeAsk && (
        <div className="overlay" style={{ zIndex: 90 }} onMouseDown={(e) => e.target === e.currentTarget && setPurgeAsk(false)}>
          <div className="dialog" style={{ maxWidth: 400 }} role="alertdialog" aria-modal="true" aria-labelledby="vp-purge">
            <header><h2 id="vp-purge">Bu öğe kalıcı olarak silinsin mi?</h2></header>
            <div className="body"><span className="muted">Bu işlem geri alınamaz.</span></div>
            <footer>
              <button className="btn" onClick={() => setPurgeAsk(false)} autoFocus>Vazgeç</button>
              <button className="btn danger solid" onClick={purgeCurrent}>Sil</button>
            </footer>
          </div>
        </div>
      )}

      <div style={{ flex: 1, display: "flex", minHeight: 0 }}>
        <section className={cls("stage", zoom && "zoom")} aria-label="Medya">
          {item.kind === "video" ? <VideoPlayer repoId={repoId} item={item} /> : (
            <div style={{ position: "relative", display: zoom ? "block" : "contents" }}>
              <Photo repoId={repoId} item={item} zoom={zoom} onToggleZoom={() => setZoom((z) => !z)} />
              {showFaces && !zoom && !tagging && d && d.faces.length > 0 && <FaceOverlay faces={d.faces.filter((f) => !(f.manual && f.x1 === 0 && f.y1 === 0 && f.x2 === 1 && f.y2 === 1))} />}
              {tagging && !zoom && d && (
                <TagLayer repoId={repoId} onCancel={() => setTagging(false)} onPick={async (box, name) => {
                  setTagging(false);
                  try {
                    const r = await api.tagPerson(repoId, d.id, name, box);
                    toast(r.animal ? `${name} eklendi; ${r.animal} tanındı, aynı hayvanın diğer fotoğrafları da etiketlenecek`
                      : r.detected ? `${name} eklendi; yüz bulundu ve tanımaya katıldı` : `${name} eklendi (bu alanda yüz ya da hayvan bulunamadı, etiket olarak kaydedildi)`);
                    setShowFaces(true);
                    qc.invalidateQueries({ queryKey: ["asset", repoId, d.id] });
                    qc.invalidateQueries({ queryKey: ["people", repoId] });
                  } catch (e) {
                    toast(String((e as Error).message), { error: true });
                  }
                }} />
              )}
            </div>
          )}
          {!zoom && index > 0 && <button className="arrow" style={{ left: 14 }} onClick={() => go(-1)} aria-label="Önceki (←)"><Left size={22} /></button>}
          {!zoom && index < items.length - 1 && <button className="arrow" style={{ right: 14 }} onClick={() => go(1)} aria-label="Sonraki (→)"><Right size={22} /></button>}
        </section>
        {info && (
          <aside className="info" aria-label="Bilgi">
            {d ? (
              <>
                <Rating repoId={repoId} asset={d} />
                <MetaList d={d} path={item.rel_path} />
                <FaceNames repoId={repoId} assetId={d.id} faces={d.faces} onChange={() => { qc.invalidateQueries({ queryKey: ["asset", repoId, d.id] }); qc.invalidateQueries({ queryKey: ["people", repoId] }); }} />
                <ModelTags tags={d.model_tags} />
                <PhotoText text={d.text} dark />
                <Tags repoId={repoId} assetId={d.id} tags={d.tags} />
                <Similar repoId={repoId} assetId={d.id} name={item.rel_path.split("/").pop()} />
                {d.files.filter((f) => f.state === "present").length > 1 && (
                  <div className="col" style={{ gap: 4 }}>
                    <span className="lbl">Kopyalar</span>
                    {d.files.filter((f) => f.state === "present").map((f) => <span key={f.id} className="mono">{f.rel_path}</span>)}
                  </div>
                )}
              </>
            ) : <span className="muted">Yükleniyor…</span>}
          </aside>
        )}
      </div>

      <div ref={stripRef} className="strip">
        {strip.map(({ it, i }) => (
          <button key={it.file_id} className={i === index ? "on" : ""} onClick={() => { setZoom(false); setViewerIndex(i); }} aria-label={basename(it.rel_path)}>
            <img src={thumbUrl(repoId, it.blake3, "s")} alt="" loading="lazy" draggable={false} />
            {it.kind === "video" && <span style={{ position: "absolute", right: 3, bottom: 3, background: "rgba(0,0,0,.65)", color: "#fff", fontSize: 10, padding: "1px 4px", borderRadius: 3 }}>▶ {fmtDuration(it.duration_ms)}</span>}
          </button>
        ))}
      </div>
    </div>
  );
}
