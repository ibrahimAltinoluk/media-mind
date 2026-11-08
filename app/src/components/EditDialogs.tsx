import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { api, isTauri, waitJob } from "../api";
import { useApp } from "../state";
import { cls, fmtBytes, fmtInt } from "../util";
import { Photos, Pin } from "./Icons";
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
  const { toast, trackJob } = useApp();
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
    if (r.job) trackJob(r.job);
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

type Spot = { lat: number; lon: number; label?: string; sub?: string; fly?: boolean };
type GeoHit = { label: string; sub: string; lat: number; lon: number; source: "library" | "offline" | "online"; n?: number };

/** Yazdıkça önerilen yerler: kütüphanedeki yerler + şehir/ilçe listesi (çevrimdışı) + adres araması (OpenStreetMap). */
function useGeoSuggest(text: string, places: { name: string; lat: number; lon: number; n: number }[]) {
  // Adres sonuçlarında kütüphanedeki fotoğrafların çoğunun çekildiği yere yakın olanlar öne çıkar
  const near = places.length ? { lat: places[0].lat, lon: places[0].lon } : null;
  const [deb, setDeb] = useState("");
  useEffect(() => {
    const t = setTimeout(() => setDeb(text.trim()), 300);
    return () => clearTimeout(t);
  }, [text]);
  const on = deb.length >= 2;
  const off = useQuery({ queryKey: ["geo", "off", deb], queryFn: () => api.geoSearch(deb, false), enabled: on, staleTime: Infinity });
  const onl = useQuery({ queryKey: ["geo", "on", deb, near?.lat], queryFn: () => api.geoSearch(deb, true, near), enabled: deb.length >= 3, staleTime: Infinity, retry: false });
  const f = text.trim().toLocaleLowerCase("tr");
  const lib: GeoHit[] = f ? places.filter((p) => p.name.toLocaleLowerCase("tr").includes(f)).slice(0, 4)
    .map((p) => ({ label: p.name, sub: `Kütüphanede · ${fmtInt(p.n)} öğe`, lat: p.lat, lon: p.lon, source: "library" as const, n: p.n })) : [];
  const seen = new Set(lib.map((h) => h.label.toLocaleLowerCase("tr")));
  const uniq = (hs: GeoHit[]) => hs.filter((h) => {
    const k = `${h.label}|${h.sub}`.toLocaleLowerCase("tr");
    if (seen.has(k) || seen.has(h.label.toLocaleLowerCase("tr"))) return false;
    seen.add(k);
    return true;
  });
  const hits = [...lib, ...uniq(off.data?.results ?? []).slice(0, 4), ...uniq(onl.data?.results ?? []).slice(0, 6)];
  return { hits, loading: on && (off.isFetching || onl.isFetching), offline: onl.isFetched && !onl.data?.results.length && deb.length >= 3 };
}

/** Konum ata: yer ya da adres yaz (öneriler), bilinen bir yeri seç ya da haritada bir noktaya tıkla. */
export function LocationDialog({ repoId, assetIds, onClose }: { repoId: string; assetIds: number[]; onClose: () => void }) {
  const { toast, trackJob } = useApp();
  const qc = useQueryClient();
  const pts = useQuery({ queryKey: ["map", repoId], queryFn: () => api.mapPoints(repoId) });
  const st = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const writes = st.data?.settings.files?.write_location ?? true;
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [hi, setHi] = useState(0);
  const [pick, setPick] = useState<Spot | null>(null);
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
  const sug = useGeoSuggest(q, places);
  const showList = open && q.trim().length >= 2 && (sug.hits.length > 0 || sug.loading);
  const choose = (h: GeoHit) => {
    setPick({ lat: h.lat, lon: h.lon, label: h.label, sub: h.sub, fly: true });
    setQ(h.label);
    setOpen(false);
  };
  const onKey = (e: React.KeyboardEvent) => {
    if (!showList) return;
    if (e.key === "ArrowDown") { setHi((x) => Math.min(sug.hits.length - 1, x + 1)); e.preventDefault(); }
    else if (e.key === "ArrowUp") { setHi((x) => Math.max(0, x - 1)); e.preventDefault(); }
    else if (e.key === "Enter" && sug.hits[hi]) { choose(sug.hits[hi]); e.preventDefault(); }
    else if (e.key === "Escape") { setOpen(false); e.stopPropagation(); }
  };
  const apply = async () => {
    if (!pick) return;
    setBusy(true);
    try {
      const r = await api.editLocation(repoId, assetIds, pick.lat, pick.lon);
      if (r.job) trackJob(r.job);
      qc.invalidateQueries();
      toast(`${fmtInt(r.updated)} öğeye konum verildi: ${[r.place, r.region].filter(Boolean).join(", ")}${r.job ? ". Dosyalara da yazılıyor." : ""}`);
      onClose();
    } catch (e) {
      toast(String((e as Error).message), { error: true });
      setBusy(false);
    }
  };
  return (
    <Sheet title={assetIds.length > 1 ? `${fmtInt(assetIds.length)} öğeye konum ata` : "Konum ata"} onClose={onClose} width={620}
      footer={<>
        <span className="muted ellipsis" style={{ marginRight: "auto", fontSize: 12, maxWidth: 360 }}>
          {pick ? (pick.label ? [pick.label, pick.sub].filter(Boolean).join(" · ") : `${pick.lat.toFixed(5)}, ${pick.lon.toFixed(5)}`) : "Bir yer ya da adres yaz veya haritaya tıkla"}
        </span>
        <button className="btn" onClick={onClose}>Vazgeç</button>
        <button className="btn pri" disabled={!pick || busy} onClick={apply}>Uygula</button>
      </>}>
      <div style={{ position: "relative" }}>
        <label className="field">
          <span className="sr">Yer ya da adres ara</span>
          <input autoFocus placeholder="Yer ya da adres yaz (ör. Kadıköy, Bağdat Caddesi 300)" value={q} role="combobox" aria-expanded={showList} aria-autocomplete="list"
            onChange={(e) => { setQ(e.target.value); setOpen(true); setHi(0); }} onFocus={() => setOpen(true)} onBlur={() => setTimeout(() => setOpen(false), 120)} onKeyDown={onKey} />
          {open && sug.loading && <span className="spin" style={{ flex: "none" }} />}
        </label>
        {showList && (
          <div className="geo-sugg" role="listbox" onMouseDown={(e) => e.preventDefault()}>
            {sug.hits.map((h, i) => (
              <button key={`${h.source}-${h.label}-${h.sub}-${i}`} role="option" aria-selected={i === hi} className={cls("srow", i === hi && "hi")}
                onMouseEnter={() => setHi(i)} onClick={() => choose(h)}>
                <span className="geo-ic" aria-hidden>{h.source === "library" ? <Photos size={14} /> : <Pin size={14} />}</span>
                <span className="grow col" style={{ minWidth: 0, gap: 0 }}>
                  <span className="ellipsis">{h.label}</span>
                  {h.sub && <span className="muted ellipsis" style={{ fontSize: 11.5 }}>{h.sub}</span>}
                </span>
              </button>
            ))}
            {!sug.hits.length && <div className="shint" style={{ borderTop: 0, marginTop: 0 }}>Aranıyor…</div>}
          </div>
        )}
      </div>
      {!q && places.length > 0 && (
        <div className="row wrap" style={{ gap: 6 }}>
          {places.slice(0, 8).map((p) => (
            <button key={p.name} className={cls("chip", pick?.label === p.name && "on")} onClick={() => setPick({ lat: p.lat, lon: p.lon, label: p.name, fly: true })}>{p.name}</button>
          ))}
        </div>
      )}
      <div style={{ height: 300, borderRadius: 8, overflow: "hidden" }}>
        <MapView repoId={repoId} points={pts.data?.points ?? []} pick={pick} onPick={(lat, lon) => setPick({ lat, lon })} />
      </div>
      <span className="muted" style={{ fontSize: 11.5 }}>
        {writes ? "Konum dosyanın kendisine de yazılır; fotoğraf, çekim tarihi ve dosya tarihleri değişmez. " : "Yalnızca MediaMind'da değişir, dosyalara yazılmaz. "}
        Adres araması internetten (OpenStreetMap) yapılır; yalnızca yazdığın metin gönderilir.{sug.offline ? " İnternet yoksa yalnızca şehir/ilçe adları önerilir." : ""}
      </span>
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
