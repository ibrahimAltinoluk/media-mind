import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { api, thumbUrl, waitJob, type MapPoint } from "../api";
import { MapView } from "../components/MapView";
import { Arrow, Clock, Close, Pin } from "../components/Icons";
import { Toolbar } from "../components/Toolbar";
import { EMPTY_FILTER, useApp } from "../state";
import type { AssetItem, Plan } from "../types";
import { cls, fmtBytes, fmtDate, fmtInt } from "../util";

const STATUS: Record<string, string> = {
  pending: "Onay bekliyor", running: "Uygulanıyor", applied: "Yapıldı", partial: "Kısmen yapıldı", rejected: "Vazgeçildi", undone: "Geri alındı", failed: "Başarısız", interrupted: "Yarıda kaldı",
};
const KIND: Record<string, string> = { trash: "Silme", dedup: "Tekrarları temizleme", organize: "Klasörleri düzenleme", move: "Taşıma", compress: "Video küçültme" };
const SOURCE = (src: string) => (src === "ui" ? "" : src === "cli" ? "Komut satırı" : src === "assistant" ? "Asistan" : src.startsWith("mcp:") ? src.slice(4) : src);

export function History() {
  const { repoId, showPlan, trackJob, toast } = useApp();
  const qc = useQueryClient();
  const q0 = useQuery({ queryKey: ["plans", repoId], queryFn: () => api.plans(repoId!), enabled: !!repoId });
  // Arayüzde hazırlanıp “Vazgeç” denen işlemler geçmişi kalabalıklaştırmasın
  const q = { ...q0, data: q0.data?.filter((p) => !(p.status === "rejected" && p.source === "ui")) };
  const [open, setOpen] = useState<number | null>(null);
  const detail = useQuery({ queryKey: ["plan", repoId, open], queryFn: () => api.plan(repoId!, open!, 200), enabled: open !== null });

  const run = async (p: Plan, what: "apply" | "undo" | "reject" | "resume") => {
    try {
      if (what === "resume") {
        const j = await api.applyPlan(repoId!, p.id);
        trackJob(j);
        await waitJob(j.id);
        toast("Kaldığı yerden tamamlandı");
        qc.invalidateQueries();
        return;
      }
      if (what === "apply") return showPlan(await api.plan(repoId!, p.id), () => q0.refetch());
      if (what === "reject") await api.rejectPlan(repoId!, p.id);
      else {
        const j = await api.undoPlan(repoId!, p.id);
        trackJob(j);
        await waitJob(j.id);
        toast("Geri alındı");
      }
      qc.invalidateQueries();
    } catch (e) {
      toast(String((e as Error).message), { error: true });
    }
  };

  return (
    <>
      <Toolbar title="Geçmiş" subtitle="Dosyalarında yapılan değişiklikler. Çoğu geri alınabilir." />
      <div className="scroll">
        <div className="page" style={{ maxWidth: 820 }}>
          {q.data?.length === 0 && <div className="empty"><Clock size={40} /><h2>Henüz değişiklik yok</h2><span>Sildiğin, taşıdığın ya da düzenlediğin dosyalar burada listelenir.</span></div>}
          {!!q.data?.length && (
            <div className="group">
              {q.data.map((p) => (
                <div key={p.id}>
                  <div className="grow-row" style={{ cursor: "default" }} onClick={() => setOpen(open === p.id ? null : p.id)}>
                    <div className="k">
                      {p.title || KIND[p.kind] || p.kind}
                      <small>{[KIND[p.kind], `${fmtInt(p.summary.files)} dosya`, fmtBytes(p.summary.bytes), fmtDate(p.applied_at ?? p.created_at), SOURCE(p.source)].filter(Boolean).join(" · ")}</small>
                    </div>
                    <span className={cls("status", `st-${p.status}`)}>{STATUS[p.status] ?? p.status}</span>
                    <div className="row" style={{ gap: 6 }} onClick={(e) => e.stopPropagation()}>
                      {p.status === "pending" && <><button className="btn sm" onClick={() => run(p, "reject")}>Vazgeç</button><button className="btn sm pri" onClick={() => run(p, "apply")}>İncele</button></>}
                      {p.status === "interrupted" && <button className="btn sm pri" onClick={() => run(p, "resume")}>Devam et</button>}
                      {(p.status === "applied" || p.status === "partial" || p.status === "interrupted") && <button className="btn sm" onClick={() => run(p, "undo")}>Geri al</button>}
                    </div>
                  </div>
                  {open === p.id && (
                    <div style={{ background: "var(--panel-2)", maxHeight: 320, overflowY: "auto" }}>
                      {detail.data?.items?.map((it) => (
                        <div key={it.id} className="mv">
                          <span className="muted" title={it.src}>{it.src}</span>
                          <Arrow size={13} style={{ color: "var(--muted)" }} />
                          <span title={it.dst ?? ""}>{it.op === "trash" ? "Son silinenler" : it.dst}{it.error ? <span style={{ color: "var(--danger)" }}> · {it.error}</span> : null}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </>
  );
}

function pointItem(p: MapPoint): AssetItem {
  return {
    file_id: p[3], rel_path: p[8], tracked: 1, size: 0, asset_id: p[7], blake3: p[2], kind: p[6], mime: null, width: null, height: null,
    duration_ms: null, taken_at: p[4], taken_at_source: null, camera_model: null, rating: 0, favorite: 0, lat: p[0], lon: p[1],
    place: p[5], region: null, country_code: null, copies: 1, in_dup: 0,
  };
}

export function Places() {
  const { repoId, setPage, setView, setFilter, openViewer } = useApp();
  const q = useQuery({ queryKey: ["facets", repoId, "tracked"], queryFn: () => api.facets(repoId!, "tracked"), enabled: !!repoId });
  const pts = useQuery({ queryKey: ["map", repoId], queryFn: () => api.mapPoints(repoId!), enabled: !!repoId });
  const [mode, setModeRaw] = useState<"map" | "list">(() => { try { return (localStorage.getItem("mm.places") as "map" | "list") || "map"; } catch { return "map"; } });
  const setMode = (m: "map" | "list") => { setModeRaw(m); try { localStorage.setItem("mm.places", m); } catch { /* yok */ } };
  const [chosen, setChosen] = useState<MapPoint[] | null>(null);
  const n = q.data?.places.length ?? 0;
  const items = useMemo(() => (chosen ?? []).map(pointItem), [chosen]);
  const names = useMemo(() => {
    const c = new Map<string, number>();
    for (const p of chosen ?? []) if (p[5]) c.set(p[5], (c.get(p[5]) ?? 0) + 1);
    return [...c.entries()].sort((a, b) => b[1] - a[1]).map(([k]) => k);
  }, [chosen]);
  if (!repoId) return null;
  return (
    <>
      <Toolbar title="Yerler" subtitle={n ? `${fmtInt(n)} yer · ${fmtInt(pts.data?.points.length ?? 0)} konumlu öğe` : undefined}>
        <div className="seg">
          <button className={cls(mode === "map" && "on")} onClick={() => setMode("map")}>Harita</button>
          <button className={cls(mode === "list" && "on")} onClick={() => setMode("list")}>Liste</button>
        </div>
      </Toolbar>
      {q.data && pts.data && n === 0 && !pts.data.points.length ? (
        <div className="scroll"><div className="empty"><Pin size={40} /><h2>Konumu olan fotoğraf yok</h2><span>Konum bilgisi olan fotoğraf ve videolar burada haritada ve şehirlere göre görünür. Konumsuz fotoğraflara sağ tıklayıp “Konum ata…” diyebilirsin.</span></div></div>
      ) : mode === "map" ? (
        <div className="content">
          <div className="main" style={{ position: "relative", isolation: "isolate" }}>
            <MapView repoId={repoId} points={pts.data?.points ?? []} onSelect={setChosen} />
            <div className="map-note">Harita görüntüleri internetten (OpenStreetMap) yüklenir; fotoğrafların gönderilmez.</div>
          </div>
          {chosen && (
            <aside className="detail" aria-label="Seçilen yer">
              <div className="row" style={{ minHeight: 28 }}>
                <div className="grow" style={{ minWidth: 0 }}>
                  <div style={{ fontWeight: 600 }} className="ellipsis">{names.slice(0, 2).join(", ") || "Seçilen yer"}</div>
                  <div className="muted" style={{ fontSize: 12 }}>{fmtInt(chosen.length)} öğe</div>
                </div>
                <button className="ibtn" onClick={() => setChosen(null)} aria-label="Kapat"><Close size={15} /></button>
              </div>
              <div className="trash-grid" style={{ gridTemplateColumns: "repeat(3, minmax(0, 1fr))" }}>
                {items.slice(0, 300).map((it, i) => (
                  <button key={it.file_id} className="tile" onClick={() => openViewer(items, i)} title={it.rel_path}>
                    <img src={thumbUrl(repoId, it.blake3, "s")} alt="" loading="lazy" draggable={false} />
                  </button>
                ))}
              </div>
              {names.length === 1 && (
                <button className="btn" onClick={() => { setView("all"); setFilter({ ...EMPTY_FILTER, place: names[0] }); setPage("library"); }}>
                  Kütüphanede göster
                </button>
              )}
            </aside>
          )}
        </div>
      ) : (
        <div className="scroll">
          <div className="page">
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: 10 }}>
              {q.data?.places.map((p) => (
                <button key={`${p.place}-${p.region}`} className="preset" style={{ flexDirection: "row", alignItems: "center", gap: 10 }}
                  onClick={() => { setView("all"); setFilter({ ...EMPTY_FILTER, place: p.place }); setPage("library"); }}>
                  <Pin size={18} style={{ color: "var(--accent)", flex: "none" }} />
                  <span className="grow" style={{ minWidth: 0 }}>
                    <b className="ellipsis" style={{ display: "block" }}>{p.place}</b>
                    <span className="muted ellipsis" style={{ display: "block", fontSize: 12 }}>{[p.region, `${fmtInt(p.n)} öğe`].filter(Boolean).join(" · ")}</span>
                  </span>
                </button>
              ))}
            </div>
            {!!n && <span className="muted" style={{ fontSize: 11.5 }}>Konumlar internete bağlanmadan şehir adına çevrilir.</span>}
          </div>
        </div>
      )}
    </>
  );
}

export function Placeholder({ title, children }: { title: string; phase?: string; children: React.ReactNode }) {
  return (
    <>
      <Toolbar title="" />
      <div className="scroll">
        <div className="empty" style={{ maxWidth: 520, margin: "40px auto" }}>
          <h2>{title}</h2>
          <span>{children}</span>
        </div>
      </div>
    </>
  );
}
