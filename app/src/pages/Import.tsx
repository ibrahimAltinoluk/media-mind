import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { api, ApiError, deviceThumbUrl, waitJob, type DeviceInfo, type DeviceItem } from "../api";
import { Check, Image, Play } from "../components/Icons";
import { Toolbar } from "../components/Toolbar";
import { useApp } from "../state";
import { cls, fmtBytes, fmtDuration, fmtInt, monthLabel } from "../util";

const TEMPLATES: { v: string; t: string; ex: string }[] = [
  { v: "{yıl}/{ay}-{ay_adı}/{dosya}", t: "Yıl / ay", ex: "2025/07-Temmuz/IMG_0421.HEIC" },
  { v: "{yıl}/{dosya}", t: "Yıl", ex: "2025/IMG_0421.HEIC" },
  { v: "{yıl}/{yıl}-{ay}-{gün}/{dosya}", t: "Yıl / gün", ex: "2025/2025-07-14/IMG_0421.HEIC" },
  { v: "{cihaz}/{yıl}/{dosya}", t: "Aygıt / yıl", ex: "Pixel 8/2025/IMG_0421.HEIC" },
];

export function DeviceIcon({ d, size = 18 }: { d: { kind: string; product?: string }; size?: number }) {
  // Telefon ya da kart simgesi
  return d.kind === "card" ? (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.7} aria-hidden="true"><path d="M7 3h7l4 4v13a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z" /><path d="M9 3v4M12 3v4M15 4v3" /></svg>
  ) : (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.7} aria-hidden="true"><rect x="6.5" y="2.5" width="11" height="19" rx="2.5" /><path d="M11 18.5h2" /></svg>
  );
}

function Help() {
  return (
    <div className="group" style={{ maxWidth: 640 }}>
      <div className="grow-row"><div className="k">Android<small>Telefonu USB ile bağla, kilidini aç. Bildirimlerdeki “USB” seçeneğinden <b>Fotoğraf aktarımı (PTP)</b>'yi seç. “Dosya aktarımı (MTP)” kipini macOS doğrudan okuyamaz.</small></div></div>
      <div className="grow-row"><div className="k">iPhone / iPad<small>Kabloyla bağla, kilidini aç ve “Bu bilgisayara güven” de. Asılları almak için iPhone'da Ayarlar › Fotoğraflar › “Mac'e veya PC'ye aktar” altında <b>Asılları Koru</b>'yu seç.</small></div></div>
      <div className="grow-row"><div className="k">SD kart / fotoğraf makinesi<small>Kartı tak; içinde DCIM klasörü olan diskler burada görünür.</small></div></div>
    </div>
  );
}

type Source = "all" | "camera" | "whatsapp" | "screens" | "other";
const SOURCES: { v: Source; t: string }[] = [
  { v: "all", t: "Tümü" }, { v: "camera", t: "Kamera" }, { v: "whatsapp", t: "WhatsApp" }, { v: "screens", t: "Ekran görüntüleri" }, { v: "other", t: "Diğer" },
];
/** Telefondaki klasöründen öğenin kaynağı */
function sourceOf(it: DeviceItem): Source {
  const f = (it.folder ?? "").toLowerCase();
  if (/screenshot|ekran/.test(f) || /^screenshot/i.test(it.name)) return "screens";
  if (f.includes("whatsapp")) return "whatsapp";
  if (!f || f.startsWith("dcim") || f.includes("/dcim") || f.includes("camera")) return "camera";
  return "other";
}

/** Seçilen aygıtın öğeleri: aya göre gruplu, yeni olanlar seçili; hedef kütüphane ve klasör düzeni seçilir. */
function DeviceView({ device }: { device: DeviceInfo }) {
  const { repoId, toast, trackJob, setPage } = useApp();
  const qc = useQueryClient();
  const repos = useQuery({ queryKey: ["repos"], queryFn: api.repos });
  const [target, setTarget] = useState<string | null>(repoId);
  const [tpl, setTpl] = useState(TEMPLATES[0].v);
  const [onlyNew, setOnlyNew] = useState(true);
  const [del, setDel] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const items = useQuery({
    queryKey: ["deviceItems", device.id, target], queryFn: () => api.deviceItems(device.id, target!),
    // Android katalogu bağlandıktan sonra da dolabilir: liste boşken birkaç saniyede bir yeniden sor
    refetchInterval: (q) => (q.state.data?.items.length ? false : 3000),
    enabled: !!target, retry: (n, e) => e instanceof ApiError && (e.status === 409 || e.status === 423) && n < 60, retryDelay: 3000,
  });
  const all = useMemo(() => items.data?.items ?? [], [items.data]);
  const [src, setSrc] = useState<Source>("all");
  const pool = onlyNew ? all.filter((i) => !i.imported) : all;
  const srcCounts = useMemo(() => {
    const c: Record<Source, number> = { all: pool.length, camera: 0, whatsapp: 0, screens: 0, other: 0 };
    for (const i of pool) c[sourceOf(i)]++;
    return c;
  }, [pool]);
  useEffect(() => { if (src !== "all" && !srcCounts[src]) setSrc("all"); }, [src, srcCounts]);
  const shown = useMemo(() => (src === "all" ? pool : pool.filter((i) => sourceOf(i) === src)), [pool, src]);
  const [sel, setSel] = useState<Set<string>>(new Set());
  useEffect(() => setSel(new Set(all.filter((i) => !i.imported).map((i) => i.key))), [all]);
  const groups = useMemo(() => {
    const g: { month: string; items: DeviceItem[] }[] = [];
    for (const it of shown) {
      const m = it.created?.slice(0, 7) ?? "?";
      if (!g.length || g[g.length - 1].month !== m) g.push({ month: m, items: [] });
      g[g.length - 1].items.push(it);
    }
    return g;
  }, [shown]);
  const chosen = shown.filter((i) => sel.has(i.key)); // yalnız görünen öğeler aktarılır
  const bytes = chosen.reduce((a, i) => a + i.size, 0);
  const newCount = all.filter((i) => !i.imported).length;
  const toggle = (k: string) => setSel((s) => { const n = new Set(s); if (n.has(k)) n.delete(k); else n.add(k); return n; });
  const toggleMonth = (its: DeviceItem[]) => setSel((s) => {
    const n = new Set(s);
    const on = its.every((i) => n.has(i.key));
    for (const i of its) if (on) n.delete(i.key); else n.add(i.key);
    return n;
  });
  const run = async () => {
    setConfirm(false);
    if (!target || !chosen.length) return;
    setBusy(true);
    try {
      const j = await api.importItems(target, { device: device.id, keys: chosen.map((i) => i.key), template: tpl, delete_after: del });
      trackJob(j);
      const done = await waitJob(j.id);
      const r = done.result as { imported: number; duplicates: number; failed: { file: string }[]; deleted: number };
      const parts = [`${fmtInt(r.imported)} öğe aktarıldı`, r.duplicates ? `${fmtInt(r.duplicates)} tanesi zaten kütüphanedeydi` : "",
        r.deleted ? `${fmtInt(r.deleted)} öğe aygıttan silindi` : "", r.failed.length ? `${fmtInt(r.failed.length)} başarısız` : ""];
      toast(parts.filter(Boolean).join(" · "), { error: r.failed.length > 0, action: { label: "Kütüphanede göster", run: () => setPage("library") } });
      qc.invalidateQueries();
    } catch (e) {
      toast(String((e as Error).message), { error: true });
    }
    setBusy(false);
  };
  const err = items.error as ApiError | null;
  const ex = TEMPLATES.find((t) => t.v === tpl)?.ex;
  return (
    <>
      <Toolbar title={device.name} subtitle={items.data ? `${fmtInt(all.length)} öğe · ${fmtInt(newCount)} yeni` : device.product || undefined}>
        <button className="btn pri" disabled={busy || !chosen.length || !target} onClick={() => (del ? setConfirm(true) : run())}>
          {busy ? "Aktarılıyor…" : chosen.length ? `${fmtInt(chosen.length)} öğeyi aktar (${fmtBytes(bytes)})` : "Aktar"}
        </button>
      </Toolbar>
      <div className="scroll">
        <div className="page" style={{ gap: 16 }}>
          <div className="group">
            <div className="grow-row">
              <div className="k">Hedef kütüphane</div>
              <select className="sel" value={target ?? ""} onChange={(e) => setTarget(e.target.value || null)} aria-label="Hedef kütüphane">
                {(repos.data ?? []).filter((r) => r.online).map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
              </select>
            </div>
            <div className="grow-row">
              <div className="k">Klasör düzeni<small>Örnek: <span className="mono">{ex}</span></small></div>
              <div className="seg">{TEMPLATES.map((t) => <button key={t.v} className={cls(tpl === t.v && "on")} onClick={() => setTpl(t.v)}>{t.t}</button>)}</div>
            </div>
            <div className="grow-row">
              <div className="k">Yalnız yenileri göster<small>Daha önce aktarılanlar ve kütüphanede zaten olanlar gizlenir.</small></div>
              <label className="switch"><input type="checkbox" role="switch" checked={onlyNew} onChange={(e) => setOnlyNew(e.target.checked)} aria-label="Yalnız yenileri göster" /><span /></label>
            </div>
            <div className="grow-row">
              <div className="k">Aktardıktan sonra aygıttan sil<small>Yalnızca kütüphanede sağlam kopyası doğrulananlar silinir.</small></div>
              <label className="switch"><input type="checkbox" role="switch" checked={del} onChange={(e) => setDel(e.target.checked)} aria-label="Aktardıktan sonra aygıttan sil" /><span /></label>
            </div>
          </div>

          {items.isLoading || (err && (err.status === 409 || err.status === 423) && items.isFetching) ? (
            <div className="loading-msg"><span className="spin" />{err?.status === 423 ? "Telefonun kilidini aç…" : "Aygıttaki fotoğraflar okunuyor… (çok fotoğraf varsa biraz sürebilir)"}</div>
          ) : err ? (
            <div className="empty"><h2>Aygıt okunamadı</h2><span>{err.message}</span><button className="btn" onClick={() => items.refetch()}>Tekrar dene</button></div>
          ) : shown.length === 0 ? (
            <div className="empty"><Check size={40} /><h2>{all.length ? "Hepsi zaten aktarılmış" : "Aygıtta fotoğraf bulunamadı"}</h2>
              {!all.length && <span>Telefonun kilidinin açık ve USB kipinin <b>Fotoğraf aktarımı (PTP)</b> olduğundan emin ol. Liste kendiliğinden yenilenir.</span>}
              {all.length > 0 && <button className="btn" onClick={() => setOnlyNew(false)}>Hepsini göster</button>}</div>
          ) : (
            <>
              {SOURCES.filter((o) => o.v !== "all" && srcCounts[o.v] > 0).length > 1 && (
                <div className="seg" role="tablist" aria-label="Kaynak" style={{ alignSelf: "flex-start" }}>
                  {SOURCES.filter((o) => o.v === "all" || srcCounts[o.v] > 0).map((o) => (
                    <button key={o.v} role="tab" aria-selected={src === o.v} className={cls(src === o.v && "on")} onClick={() => setSrc(o.v)}>
                      {o.t} <span className="muted">{fmtInt(srcCounts[o.v])}</span>
                    </button>
                  ))}
                </div>
              )}
              <div className="row" style={{ gap: 8, fontSize: 12 }}>
                <button className="btn sm ghost" onClick={() => setSel(new Set(shown.map((i) => i.key)))}>Hepsini seç</button>
                <button className="btn sm ghost" onClick={() => setSel(new Set())}>Seçimi kaldır</button>
              </div>
              {groups.map((g) => (
                <section key={g.month}>
                  <div className="group-h" style={{ paddingTop: 6 }}>
                    <h2>{g.month === "?" ? "Tarihsiz" : monthLabel(g.month)}</h2>
                    <span className="muted">{fmtInt(g.items.length)} öğe</span>
                    <button className="btn sm ghost" onClick={() => toggleMonth(g.items)}>{g.items.every((i) => sel.has(i.key)) ? "Seçimi kaldır" : "Hepsini seç"}</button>
                  </div>
                  <div className="trash-grid">
                    {g.items.map((it) => (
                      <button key={it.key} className={cls("tile", sel.has(it.key) && "sel")} onClick={() => toggle(it.key)}
                        title={`${it.name} · ${fmtBytes(it.size)}${it.folder ? `\n${it.folder}` : ""}${it.imported ? " · zaten aktarılmış" : ""}`} aria-pressed={sel.has(it.key)}>
                        <img src={deviceThumbUrl(device.id, it.key)} alt="" loading="lazy" draggable={false} style={it.imported ? { opacity: 0.45 } : undefined}
                          onError={(e) => { e.currentTarget.style.visibility = "hidden"; }} />
                        <span className="ph" style={{ position: "absolute", top: 0, right: 0, bottom: 0, left: 0, zIndex: -1 }}><Image size={22} /></span>
                        {it.kind === "video" && <span className="badge b-vid"><Play size={10} />{it.duration_ms ? fmtDuration(it.duration_ms) : "Video"}</span>}
                        {it.imported && <span className="badge" style={{ left: 6, bottom: 5, fontSize: 10 }}>Aktarıldı</span>}
                        {sel.has(it.key) && <span className="sel-check"><Check size={11} strokeWidth={3} /></span>}
                      </button>
                    ))}
                  </div>
                </section>
              ))}
            </>
          )}
        </div>
      </div>
      {confirm && (
        <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && setConfirm(false)}>
          <div className="dialog" style={{ maxWidth: 420 }} role="alertdialog" aria-modal="true">
            <header><h2>Aktardıktan sonra {fmtInt(chosen.length)} öğe aygıttan silinsin mi?</h2></header>
            <div className="body"><span className="muted">Her öğe ancak kütüphanede sağlam kopyası doğrulandıktan sonra silinir. Aygıttan silinenler geri alınamaz.</span></div>
            <footer>
              <button className="btn" onClick={() => setConfirm(false)} autoFocus>Vazgeç</button>
              <button className="btn danger solid" onClick={run}>Aktar ve sil</button>
            </footer>
          </div>
        </div>
      )}
    </>
  );
}

export function ImportPage() {
  const { deviceId, setDeviceId } = useApp();
  const q = useQuery({ queryKey: ["devices"], queryFn: api.devices, refetchInterval: 4000 });
  const list = q.data?.devices ?? [];
  const dev = list.find((d) => d.id === deviceId);
  if (dev) return <DeviceView key={dev.id} device={dev} />;
  return (
    <>
      <Toolbar title="İçe aktar" subtitle={list.length ? `${fmtInt(list.length)} aygıt bağlı` : "Bağlı aygıt yok"} />
      <div className="scroll">
        <div className="page" style={{ gap: 18 }}>
          {list.length > 0 && (
            <div className="memory-grid" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))" }}>
              {list.map((d) => (
                <button key={d.id} className="preset" style={{ flexDirection: "row", alignItems: "center", gap: 12 }} onClick={() => setDeviceId(d.id)}>
                  <DeviceIcon d={d} size={28} />
                  <span className="grow" style={{ minWidth: 0 }}>
                    <b className="ellipsis" style={{ display: "block" }}>{d.name}</b>
                    <span className="muted" style={{ fontSize: 12 }}>{d.locked ? "Kilidi aç" : !d.ready ? "Hazırlanıyor…" : d.count != null ? `${fmtInt(d.count)} öğe` : d.product}</span>
                  </span>
                </button>
              ))}
            </div>
          )}
          {!list.length && (
            <div className="empty" style={{ padding: "30px 0 10px" }}>
              <DeviceIcon d={{ kind: "device" }} size={44} />
              <h2>Telefonunu ya da kartını bağla</h2>
              <span>Bağlandığında burada görünür; fotoğraflarını seçtiğin kütüphaneye kopyalayabilirsin. Daha önce aktarılanlar atlanır.</span>
            </div>
          )}
          <Help />
        </div>
      </div>
    </>
  );
}
