import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, thumbUrl } from "../api";
import { FaceNames } from "../pages/People";
import { EMPTY_FILTER } from "../state";
import { useApp } from "../state";
import type { AssetDetail, AssetItem } from "../types";
import { basename, cls, fmtBytes, fmtDate, fmtDuration, placeText, SOURCE_LABEL } from "../util";
import { Close, Copy, Heart, Star } from "./Icons";
import { revealInFinder } from "./Sidebar";

export function useTrashFlow() {
  const { repoId, showPlan, toast } = useApp();
  return async (fileIds: number[], onDone?: () => void) => {
    if (!repoId || !fileIds.length) return;
    try {
      const plan = await api.createPlan(repoId, { kind: "trash", file_ids: fileIds });
      showPlan(plan, onDone);
    } catch (e) {
      toast(String((e as Error).message), { error: true });
    }
  };
}

export function Rating({ repoId, asset }: { repoId: string; asset: { id: number; rating: number; favorite: number } }) {
  const qc = useQueryClient();
  const set = async (body: { rating?: number; favorite?: boolean }) => {
    await api.updateAsset(repoId, asset.id, body);
    qc.invalidateQueries({ queryKey: ["asset", repoId, asset.id] });
    qc.invalidateQueries({ queryKey: ["assets", repoId] });
  };
  return (
    <div className="row">
      <div className="stars" role="group" aria-label="Puan">
        {[1, 2, 3, 4, 5].map((n) => (
          <button key={n} className={n <= asset.rating ? "on" : ""} onClick={() => set({ rating: asset.rating === n ? 0 : n })} aria-label={`${n} yıldız`}>
            <Star size={17} filled={n <= asset.rating} />
          </button>
        ))}
      </div>
      <button className={asset.favorite ? "ibtn on" : "ibtn"} onClick={() => set({ favorite: !asset.favorite })} aria-label="Favori">
        <Heart size={18} filled={!!asset.favorite} />
      </button>
    </div>
  );
}

export function Tags({ repoId, assetId, tags }: { repoId: string; assetId: number; tags: string[] }) {
  const qc = useQueryClient();
  const [val, setVal] = useState("");
  const change = async (add: string[], remove: string[]) => {
    await api.setTags(repoId, assetId, add, remove);
    qc.invalidateQueries({ queryKey: ["asset", repoId, assetId] });
    qc.invalidateQueries({ queryKey: ["facets", repoId] });
  };
  return (
    <div className="col" style={{ gap: 6 }}>
      <span className="lbl">Etiketler</span>
      <div className="row wrap" style={{ gap: 6 }}>
        {tags.map((t) => (
          <span key={t} className="tag">{t}<button className="x" onClick={() => change([], [t])} aria-label={`${t} etiketini kaldır`}><Close size={11} /></button></span>
        ))}
        <label className="tag" style={{ background: "transparent", boxShadow: "inset 0 0 0 1px var(--line-strong)" }}>
          <span className="sr">Etiket ekle</span>
          <input value={val} onChange={(e) => setVal(e.target.value)} placeholder="Etiket ekle" style={{ width: 80, border: 0, background: "transparent", outline: "none", fontSize: 12 }}
            onKeyDown={(e) => { if (e.key === "Enter" && val.trim()) { change([val.trim()], []); setVal(""); } e.stopPropagation(); }} />
        </label>
      </div>
    </div>
  );
}

export function ModelTags({ tags }: { tags: { name: string }[] }) {
  const { setFilter, setPage, setView, closeViewer } = useApp();
  if (!tags.length) return null;
  return (
    <div className="col" style={{ gap: 6 }}>
      <span className="lbl">Fotoğrafta görülenler</span>
      <div className="row wrap" style={{ gap: 6 }}>
        {tags.map((t) => (
          <button key={t.name} className="tag" style={{ cursor: "pointer", background: "transparent" }}
            onClick={() => { closeViewer(); setView("all"); setFilter({ ...EMPTY_FILTER, tag: t.name }); setPage("library"); }}>{t.name}</button>
        ))}
      </div>
    </div>
  );
}

export function Similar({ repoId, assetId, name }: { repoId: string; assetId: number; name?: string }) {
  const { openViewer, closeViewer, setView, setFilter, setPage } = useApp();
  const q = useQuery({ queryKey: ["similar", repoId, assetId], queryFn: () => api.similar(repoId, assetId) });
  const items = q.data?.items ?? [];
  if (!items.length) return null;
  return (
    <div className="col" style={{ gap: 6 }}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <span className="lbl">Benzer fotoğraflar</span>
        <button className="btn sm ghost" onClick={() => { closeViewer(); setView("all"); setFilter({ ...EMPTY_FILTER, similar: `a:${assetId}`, simlabel: name ?? "" }); setPage("library"); }}>Tümünü gör</button>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, minmax(0, 1fr))", gap: 4 }}>
        {items.slice(0, 8).map((it, i) => (
          <button key={it.file_id} onClick={() => openViewer(items, i)} aria-label={it.rel_path} style={{ border: 0, padding: 0, borderRadius: 5, overflow: "hidden", aspectRatio: "1 / 1", cursor: "pointer", background: "var(--tile-bg)" }}>
            <img src={thumbUrl(repoId, it.blake3, "s")} alt="" style={{ width: "100%", height: "100%", objectFit: "cover" }} loading="lazy" />
          </button>
        ))}
      </div>
    </div>
  );
}

/** Fotoğraftaki yazı (OCR): seçilebilir metin ve tek tıkla kopyalama. */
export function PhotoText({ text, dark }: { text: string | null | undefined; dark?: boolean }) {
  const { toast } = useApp();
  const [all, setAll] = useState(false);
  if (!text) return null;
  const lines = text.split("\n");
  const shown = all ? lines : lines.slice(0, 6);
  const copy = async (t: string) => {
    try {
      await navigator.clipboard.writeText(t);
      toast("Metin kopyalandı");
    } catch {
      toast("Kopyalanamadı", { error: true });
    }
  };
  return (
    <div className="col" style={{ gap: 6 }}>
      <div className="row"><span className="lbl grow">Fotoğraftaki yazı</span>
        <button className="btn sm ghost" onClick={() => copy(text)}>Tümünü kopyala</button></div>
      <div className={cls("photo-text selectable", dark && "dark")}>
        {shown.map((l, i) => (
          <button key={i} className="pt-line" onClick={() => copy(l)} title="Bu satırı kopyala">{l}</button>
        ))}
      </div>
      {lines.length > 6 && <button className="btn sm ghost" style={{ alignSelf: "flex-start" }} onClick={() => setAll((x) => !x)}>{all ? "Daha az" : `${lines.length - 6} satır daha`}</button>}
    </div>
  );
}

export function MetaList({ d, path }: { d: AssetDetail; path: string }) {
  const lens = [d.lens].filter(Boolean).join(" ");
  return (
    <dl className="kv selectable">
      <dt>Tarih</dt>
      <dd>{fmtDate(d.taken_at)}{d.tz_offset ? ` (${d.tz_offset})` : ""}{d.taken_at_source && d.taken_at_source !== "exif" && <span className="muted"> · {SOURCE_LABEL[d.taken_at_source]}</span>}</dd>
      {placeText(d) && <><dt>Konum</dt><dd>{placeText(d)}{d.country_code ? ` · ${d.country_code}` : ""}</dd></>}
      {d.lat !== null && <><dt>Koordinat</dt><dd className="mono">{d.lat.toFixed(5)}, {d.lon?.toFixed(5)}</dd></>}
      {(d.camera_model || d.camera_make) && <><dt>Kamera</dt><dd>{d.camera_model ?? d.camera_make}</dd></>}
      {lens && <><dt>Lens</dt><dd>{lens}</dd></>}
      <dt>Boyut</dt>
      <dd>{d.width && d.height ? `${d.width} × ${d.height} · ` : ""}{fmtBytes(d.size)}</dd>
      {d.kind === "video" && <><dt>Video</dt><dd>{fmtDuration(d.duration_ms)}{d.video_codec ? ` · ${d.video_codec}` : ""}{d.bitrate ? ` · ${Math.round(d.bitrate / 1000)} kbps` : ""}</dd></>}
      <dt>Dosya</dt><dd className="mono">{path}</dd>
    </dl>
  );
}

export function DetailPanel({ repoId, item, onClose, onOpen }: { repoId: string; item: AssetItem; onClose: () => void; onOpen: () => void }) {
  const { setPage } = useApp();
  const qc = useQueryClient();
  const repoRoot = useQuery({ queryKey: ["summary", repoId], queryFn: () => api.summary(repoId) }).data?.repo.last_path;
  const trash = useTrashFlow();
  // Öğe değişirken önceki bilgiyi tut: panel boşalıp dolarak zıplamasın
  const d = useQuery({ queryKey: ["asset", repoId, item.asset_id], queryFn: () => api.asset(repoId, item.asset_id), placeholderData: keepPreviousData }).data;
  const others = d?.files.filter((f) => f.state === "present" && f.id !== item.file_id) ?? [];
  const errors = d?.analysis_state.errors;
  return (
    <aside className="detail" aria-label="Bilgi">
      <div className="row" style={{ minHeight: 28 }}>
        <span className="grow" style={{ fontWeight: 600 }}>Bilgi</span>
        <button className="ibtn" onClick={onClose} aria-label="Kapat"><Close size={15} /></button>
      </div>
      <button className="preview" onClick={onOpen} style={{ border: 0, padding: 0 }} aria-label="Aç" title="Açmak için tıkla (boşluk)">
        <img src={thumbUrl(repoId, item.blake3, "m")} alt="" draggable={false} />
      </button>
      <div>
        <div style={{ fontWeight: 600, fontSize: 14 }} className="selectable ellipsis" title={item.rel_path}>{basename(item.rel_path)}</div>
        <div className="muted" style={{ fontSize: 12, marginTop: 2 }}>
          {!item.tracked ? "Henüz kütüphanede değil" : [fmtDate(item.taken_at), placeText(item)].filter(Boolean).join(" · ")}
        </div>
      </div>
      {d && <Rating repoId={repoId} asset={d} />}
      {(others.length > 0 || d?.dup_group) && (
        <button className="banner warn" style={{ cursor: "default", textAlign: "left", fontSize: 12 }} onClick={() => setPage("dups")}>
          <Copy size={15} />
          <span className="grow">{others.length > 0 ? `${others.length} kopyası daha var` : "Benzer bir fotoğraf var"}</span>
          <span style={{ color: "var(--accent)" }}>Göster</span>
        </button>
      )}
      {d && <FaceNames repoId={repoId} assetId={d.id} faces={d.faces} onChange={() => { qc.invalidateQueries({ queryKey: ["asset", repoId, d.id] }); qc.invalidateQueries({ queryKey: ["people", repoId] }); }} />}
      {d && <Tags repoId={repoId} assetId={d.id} tags={d.tags} />}
      {d && <ModelTags tags={d.model_tags} />}
      {d && <PhotoText text={d.text} />}
      {d && <MetaList d={d} path={item.rel_path} />}
      {d && <Similar repoId={repoId} assetId={d.id} name={item.rel_path.split("/").pop()} />}
      {errors && Object.keys(errors).length > 0 && (
        <div className="muted" style={{ fontSize: 11.5 }}>Bazı bilgiler okunamadı: {Object.values(errors).join("; ")}</div>
      )}
      <div className="row" style={{ marginTop: "auto", paddingTop: 6 }}>
        <button className="btn grow" onClick={() => repoRoot && revealInFinder(`${repoRoot}/${item.rel_path}`)}>Finder'da göster</button>
        <button className="btn danger" onClick={() => trash([item.file_id], onClose)}>Sil</button>
      </div>
    </aside>
  );
}
