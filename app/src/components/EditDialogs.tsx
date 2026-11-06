import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { api, isTauri, waitJob } from "../api";
import { useApp } from "../state";
import { cls, fmtBytes, fmtInt } from "../util";
import { MapView } from "./MapView";
import { revealInFinder } from "./Sidebar";

function Sheet({ title, children, footer, onClose, width = 460 }: { title: string; children: React.ReactNode; footer: React.ReactNode; onClose: () => void; width?: number }) {
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose]);
  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <section className="dialog" role="dialog" aria-modal="true" aria-label={title} style={{ maxWidth: width }}>
        <header><h2>{title}</h2></header>
        <div className="body">{children}</div>
        <footer>{footer}</footer>
      </section>
    </div>
  );
}

const pad = (n: number) => String(n).padStart(2, "0");
const toLocalInput = (iso: string | null | undefined) => (iso ? iso.slice(0, 16) : "");

/** Tarihi düzelt: hepsini kaydır (ör. fotoğraf makinesinin saati yanlıştı) ya da hepsine aynı tarihi ver. */
export function DateDialog({ repoId, assetIds, sample, onClose }: { repoId: string; assetIds: number[]; sample: string | null; onClose: () => void }) {
  const { toast } = useApp();
  const qc = useQueryClient();
  const [mode, setMode] = useState<"shift" | "set">(sample ? "shift" : "set");
  const [dir, setDir] = useState<1 | -1>(1);
  const [d, setD] = useState(0);
  const [h, setH] = useState(0);
  const [m, setM] = useState(0);
  const [value, setValue] = useState(toLocalInput(sample) || `${new Date().getFullYear()}-01-01T12:00`);
  const [busy, setBusy] = useState(false);
  const secs = dir * (d * 86400 + h * 3600 + m * 60);
  const preview = useMemo(() => {
    if (mode !== "shift" || !sample) return null;
    const t = new Date(sample);
    t.setSeconds(t.getSeconds() + secs);
    return `${t.getDate()}.${pad(t.getMonth() + 1)}.${t.getFullYear()} ${pad(t.getHours())}:${pad(t.getMinutes())}`;
  }, [mode, sample, secs]);
  const done = (text: string) => {
    qc.invalidateQueries();
    toast(text);
    onClose();
  };
  const apply = async () => {
    setBusy(true);
    try {
      const r = mode === "shift" ? await api.editDate(repoId, assetIds, { shift_seconds: secs }) : await api.editDate(repoId, assetIds, { value: `${value}:00` });
      done(`${fmtInt(r.updated)} öğenin tarihi düzeltildi`);
    } catch (e) {
      toast(String((e as Error).message), { error: true });
      setBusy(false);
    }
  };
  const revert = async () => {
    const r = await api.revertEdits(repoId, assetIds);
    done(r.reverted ? `${fmtInt(r.reverted)} öğe asıl bilgisine döndü` : "Geri alınacak düzeltme yok");
  };
  const num = (v: number, set: (n: number) => void, label: string) => (
    <label className="row" style={{ gap: 6 }}>
      <div className="field" style={{ width: 70 }}><input type="number" min={0} value={v} onChange={(e) => set(Math.max(0, Number(e.target.value) || 0))} aria-label={label} /></div>
      <span className="muted">{label}</span>
    </label>
  );
  return (
    <Sheet title={assetIds.length > 1 ? `${fmtInt(assetIds.length)} öğenin tarihini düzelt` : "Tarihi düzelt"} onClose={onClose}
      footer={<>
        <button className="btn ghost" style={{ marginRight: "auto" }} onClick={revert} title="Yaptığın tarih/konum düzeltmelerini kaldır">Asıl bilgiye dön</button>
        <button className="btn" onClick={onClose}>Vazgeç</button>
        <button className="btn pri" disabled={busy || (mode === "shift" && secs === 0)} onClick={apply}>Uygula</button>
      </>}>
      <div className="seg" style={{ alignSelf: "flex-start" }}>
        <button className={cls(mode === "shift" && "on")} disabled={!sample} onClick={() => setMode("shift")}>Kaydır</button>
        <button className={cls(mode === "set" && "on")} onClick={() => setMode("set")}>Tarih ver</button>
      </div>
      {mode === "shift" ? (
        <>
          <span className="muted" style={{ fontSize: 12 }}>Fotoğraf makinesinin saati yanlışsa ya da saat dilimi farklıysa hepsini aynı miktarda kaydırır.</span>
          <div className="row wrap" style={{ gap: 12 }}>
            <div className="seg"><button className={cls(dir === 1 && "on")} onClick={() => setDir(1)}>İleri</button><button className={cls(dir === -1 && "on")} onClick={() => setDir(-1)}>Geri</button></div>
            {num(d, setD, "gün")}{num(h, setH, "saat")}{num(m, setM, "dakika")}
          </div>
          {preview && <span style={{ fontSize: 12 }}>İlk öğe: <b>{preview}</b></span>}
        </>
      ) : (
        <>
          <span className="muted" style={{ fontSize: 12 }}>Seçilen her öğeye aynı tarih ve saat verilir.</span>
          <div className="field" style={{ width: 240 }}><input type="datetime-local" value={value} onChange={(e) => setValue(e.target.value)} aria-label="Tarih ve saat" /></div>
        </>
      )}
      <span className="muted" style={{ fontSize: 11.5 }}>Yalnızca MediaMind'da değişir, dosyalara yazılmaz. İstediğin zaman “Asıl bilgiye dön” ile geri alabilirsin.</span>
    </Sheet>
  );
}

/** Konum ata: bilinen bir yeri seç ya da haritada bir noktaya tıkla. */
export function LocationDialog({ repoId, assetIds, onClose }: { repoId: string; assetIds: number[]; onClose: () => void }) {
  const { toast } = useApp();
  const qc = useQueryClient();
  const pts = useQuery({ queryKey: ["map", repoId], queryFn: () => api.mapPoints(repoId) });
  const [q, setQ] = useState("");
  const [pick, setPick] = useState<{ lat: number; lon: number; label?: string } | null>(null);
  const [busy, setBusy] = useState(false);
  // Bilinen yerler: kütüphanedeki konumlu fotoğraflardan (yer başına orta nokta)
  const places = useMemo(() => {
    const by = new Map<string, number[][]>();
    for (const p of pts.data?.points ?? []) if (p[5]) by.set(p[5], [...(by.get(p[5]) ?? []), [p[0], p[1]]]);
    return [...by.entries()].map(([name, cs]) => {
      const lat = cs.map((c) => c[0]).sort((a, b) => a - b)[Math.floor(cs.length / 2)];
      const lon = cs.map((c) => c[1]).sort((a, b) => a - b)[Math.floor(cs.length / 2)];
      return { name, lat, lon, n: cs.length };
    }).sort((a, b) => b.n - a.n);
  }, [pts.data]);
  const f = q.trim().toLocaleLowerCase("tr");
  const shown = places.filter((p) => !f || p.name.toLocaleLowerCase("tr").includes(f)).slice(0, 8);
  const apply = async () => {
    if (!pick) return;
    setBusy(true);
    try {
      const r = await api.editLocation(repoId, assetIds, pick.lat, pick.lon);
      qc.invalidateQueries();
      toast(`${fmtInt(r.updated)} öğeye konum verildi: ${[r.place, r.region].filter(Boolean).join(", ")}`);
      onClose();
    } catch (e) {
      toast(String((e as Error).message), { error: true });
      setBusy(false);
    }
  };
  return (
    <Sheet title={assetIds.length > 1 ? `${fmtInt(assetIds.length)} öğeye konum ata` : "Konum ata"} onClose={onClose} width={620}
      footer={<>
        <span className="muted" style={{ marginRight: "auto", fontSize: 12 }}>{pick ? (pick.label ?? `${pick.lat.toFixed(4)}, ${pick.lon.toFixed(4)}`) : "Bir yer seç ya da haritaya tıkla"}</span>
        <button className="btn" onClick={onClose}>Vazgeç</button>
        <button className="btn pri" disabled={!pick || busy} onClick={apply}>Uygula</button>
      </>}>
      <label className="field"><span className="sr">Yer ara</span><input autoFocus placeholder="Bilinen yerlerde ara" value={q} onChange={(e) => setQ(e.target.value)} /></label>
      {shown.length > 0 && (
        <div className="row wrap" style={{ gap: 6 }}>
          {shown.map((p) => (
            <button key={p.name} className={cls("chip", pick?.label === p.name && "on")} onClick={() => setPick({ lat: p.lat, lon: p.lon, label: p.name })}>{p.name}</button>
          ))}
        </div>
      )}
      <div style={{ height: 300, borderRadius: 8, overflow: "hidden" }}>
        <MapView repoId={repoId} points={pts.data?.points ?? []} pick={pick} onPick={(lat, lon) => setPick({ lat, lon })} />
      </div>
      <span className="muted" style={{ fontSize: 11.5 }}>Yalnızca MediaMind'da değişir, dosyalara yazılmaz. Tarih düzeltme penceresindeki “Asıl bilgiye dön” ile geri alınır.</span>
    </Sheet>
  );
}

async function pickDir(): Promise<string | null> {
  if (isTauri) {
    const { open } = await import("@tauri-apps/plugin-dialog");
    const r = await open({ directory: true, multiple: false, title: "Dışa aktarılacak klasörü seç" });
    return typeof r === "string" ? r : null;
  }
  return window.prompt("Hedef klasör (tam yol):");
}

/** Dışa aktar: seçilenlerin kopyaları istenen boyutta, istenirse konum bilgisi silinerek bir klasöre yazılır. */
export function ExportDialog({ repoId, fileIds, onClose }: { repoId: string; fileIds: number[]; onClose: () => void }) {
  const { toast, trackJob } = useApp();
  const [size, setSize] = useState<"original" | "large" | "small">("large");
  const [strip, setStrip] = useState(true);
  const [dest, setDest] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const choose = async () => {
    const d = await pickDir();
    if (d) setDest(d);
  };
  const run = async () => {
    let target = dest;
    if (!target) target = await pickDir();
    if (!target) return;
    setBusy(true);
    try {
      const j = await api.exportFiles(repoId, { file_ids: fileIds, dest: target, size, strip_location: strip });
      trackJob(j);
      onClose();
      const done = await waitJob(j.id);
      const r = done.result as { written: number; failed: { file: string }[]; dest: string };
      toast(`${fmtInt(r.written)} öğe dışa aktarıldı${r.failed.length ? `, ${fmtInt(r.failed.length)} başarısız` : ""}`, {
        error: r.failed.length > 0, action: isTauri ? { label: "Finder'da göster", run: () => revealInFinder(r.dest) } : undefined,
      });
    } catch (e) {
      toast(String((e as Error).message), { error: true });
      setBusy(false);
    }
  };
  return (
    <Sheet title={fileIds.length > 1 ? `${fmtInt(fileIds.length)} öğeyi dışa aktar` : "Dışa aktar"} onClose={onClose}
      footer={<><button className="btn" onClick={onClose}>Vazgeç</button><button className="btn pri" disabled={busy} onClick={run}>Dışa aktar</button></>}>
      <div className="group" style={{ boxShadow: "inset 0 0 0 1px var(--line)" }}>
        <div className="grow-row">
          <div className="k">Boyut<small>{size === "original" ? "Dosyalar olduğu gibi kopyalanır." : size === "large" ? "Uzun kenar 2048 piksel, JPEG. Paylaşmak için ideal." : "Uzun kenar 1280 piksel, JPEG. Mesajlaşma için küçük."}</small></div>
          <div className="seg">
            <button className={cls(size === "small" && "on")} onClick={() => setSize("small")}>Küçük</button>
            <button className={cls(size === "large" && "on")} onClick={() => setSize("large")}>Büyük</button>
            <button className={cls(size === "original" && "on")} onClick={() => setSize("original")}>Orijinal</button>
          </div>
        </div>
        <div className="grow-row">
          <div className="k">Konum bilgisini sil<small>Kopyalardaki GPS bilgisi kaldırılır; tarih ve kamera bilgisi kalır.</small></div>
          <label className="switch"><input type="checkbox" role="switch" checked={strip} onChange={(e) => setStrip(e.target.checked)} aria-label="Konum bilgisini sil" /><span /></label>
        </div>
        <div className="grow-row">
          <div className="k">Klasör<small className="ellipsis" title={dest ?? undefined}>{dest ?? "Seçilmedi"}</small></div>
          <button className="btn" onClick={choose}>Seç…</button>
        </div>
      </div>
      <span className="muted" style={{ fontSize: 11.5 }}>Orijinal dosyalarına dokunulmaz. Videolar her zaman olduğu gibi kopyalanır.</span>
    </Sheet>
  );
}

/** Sağ tık menüsündeki “Albüme ekle” listesi. */
export function AlbumAddMenu({ repoId, assetIds, onDone }: { repoId: string; assetIds: number[]; onDone: () => void }) {
  const { toast } = useApp();
  const qc = useQueryClient();
  const albums = useQuery({ queryKey: ["albums", repoId], queryFn: () => api.albums(repoId) });
  const [creating, setCreating] = useState<string | null>(null);
  const manual = (albums.data?.albums ?? []).filter((a) => a.kind === "manual");
  const add = async (aid: number, name: string) => {
    onDone();
    const r = await api.albumAdd(repoId, aid, assetIds);
    qc.invalidateQueries({ queryKey: ["albums", repoId] });
    toast(r.added ? `${fmtInt(r.added)} öğe “${name}” albümüne eklendi` : `Zaten “${name}” albümünde`);
  };
  const create = async () => {
    const name = (creating ?? "").trim();
    if (!name) return;
    onDone();
    await api.createAlbum(repoId, { name, asset_ids: assetIds });
    qc.invalidateQueries({ queryKey: ["albums", repoId] });
    toast(`“${name}” albümü oluşturuldu`);
  };
  return (
    <>
      <div className="lbl" style={{ padding: "6px 10px 2px" }}>Albüme ekle</div>
      {manual.map((a) => <button key={a.id} className="mitem" onClick={() => add(a.id, a.name)}>{a.name}<span className="kbd-hint">{a.count ?? ""}</span></button>)}
      {creating === null ? (
        <button className="mitem" onClick={() => setCreating("")}>Yeni albüm…</button>
      ) : (
        <label className="field" style={{ margin: "4px 6px" }}>
          <span className="sr">Albüm adı</span>
          <input autoFocus placeholder="Albüm adı" value={creating} onChange={(e) => setCreating(e.target.value)}
            onKeyDown={(e) => { e.stopPropagation(); if (e.key === "Enter") create(); if (e.key === "Escape") setCreating(null); }} />
        </label>
      )}
    </>
  );
}

/** Videoları küçült: HEVC'ye çevirir; orijinaller Son silinenler'e gider (Geçmiş'ten geri alınabilir). */
export function CompressDialog({ repoId, fileIds, bytes, onClose, onDone }: { repoId: string; fileIds: number[]; bytes?: number; onClose: () => void; onDone?: () => void }) {
  const { toast, trackJob } = useApp();
  const qc = useQueryClient();
  const [busy, setBusy] = useState(false);
  const run = async () => {
    setBusy(true);
    try {
      const j = await api.compressVideos(repoId, fileIds);
      trackJob(j);
      onClose();
      const done = await waitJob(j.id);
      const r = done.result as { compressed: number; skipped: string[]; failed: { file: string }[]; saved_bytes: number };
      qc.invalidateQueries();
      onDone?.();
      const parts = [r.compressed ? `${fmtInt(r.compressed)} video küçültüldü, ${fmtBytes(r.saved_bytes)} yer açıldı` : "Küçültülen video olmadı",
        r.skipped.length ? `${fmtInt(r.skipped.length)} video zaten verimli (dokunulmadı)` : "", r.failed.length ? `${fmtInt(r.failed.length)} başarısız` : ""];
      toast(parts.filter(Boolean).join(" · "), { error: r.failed.length > 0 });
    } catch (e) {
      toast(String((e as Error).message), { error: true });
      setBusy(false);
    }
  };
  return (
    <Sheet title={fileIds.length > 1 ? `${fmtInt(fileIds.length)} video küçültülsün mü?` : "Video küçültülsün mü?"} onClose={onClose}
      footer={<><button className="btn" onClick={onClose}>Vazgeç</button><button className="btn pri" disabled={busy} onClick={run} autoFocus>Küçült</button></>}>
      <span className="muted" style={{ fontSize: 13, lineHeight: 1.5 }}>
        Videolar bu Mac'in donanımıyla HEVC biçimine çevrilir; görüntü kalitesi gözle ayırt edilmez, boyut çoğu zaman yarı yarıya iner.
        {bytes ? ` Şu an toplam ${fmtBytes(bytes)}.` : ""}
      </span>
      <span className="muted" style={{ fontSize: 12, lineHeight: 1.5 }}>
        Orijinaller Son silinenler'e taşınır; Geçmiş'ten geri alabilirsin. Etiketler, favoriler, albümler ve tarih bilgisi yeni dosyaya taşınır.
        En az %20 küçülmeyen videolara dokunulmaz.
      </span>
    </Sheet>
  );
}
