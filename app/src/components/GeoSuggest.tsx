import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, thumbUrl } from "../api";
import { useApp } from "../state";
import type { GeoGroup, GeoSuggestions } from "../types";
import { fmtDate, fmtInt } from "../util";

const LOC_KEYS = ["map", "facets", "summary", "assets", "asset", "geoSuggest"];

function gapText(s: number) {
  if (s < 90) return "aynı dakikalarda";
  if (s < 3600) return `${Math.round(s / 60)} dk arayla`;
  return `${(s / 3600).toFixed(1).replace(".", ",")} sa arayla`;
}

/** Yerler sayfasının üstündeki şerit: tahmin edilebilen konum sayısı. */
export function GeoSuggestBanner({ repoId, onOpen }: { repoId: string; onOpen: () => void }) {
  const q = useQuery({ queryKey: ["geoSuggest", repoId, ""], queryFn: () => api.geoSuggest(repoId), staleTime: 60_000 });
  if (!q.data?.total) return null;
  return (
    <div className="banner" style={{ margin: "10px 16px 0" }}>
      <span className="grow">
        {fmtInt(q.data.total)} konumsuz fotoğrafın yeri, aynı saatlerde çekilmiş konumlu fotoğraflardan tahmin edilebilir.
      </span>
      <button className="btn sm pri" onClick={onOpen}>Gözden geçir</button>
    </div>
  );
}

/** Konum önerileri: gün + yer gruplarını onayla ya da atla; GPX kaydından da öneri alınabilir. */
export function GeoSuggestSheet({ repoId, onClose }: { repoId: string; onClose: () => void }) {
  const { toast, trackJob } = useApp();
  const qc = useQueryClient();
  const [gpx, setGpx] = useState<string>("");
  const q = useQuery({ queryKey: ["geoSuggest", repoId, gpx], queryFn: () => api.geoSuggest(repoId, gpx || undefined), retry: false });
  const [busy, setBusy] = useState<string | null>(null);
  const [done, setDone] = useState<Set<string>>(new Set());
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose]);
  useEffect(() => setDone(new Set()), [gpx]);

  const groups = (q.data?.groups ?? []).filter((g) => !done.has(g.key));
  const refreshAll = () => { for (const k of LOC_KEYS) qc.invalidateQueries({ queryKey: [k, repoId] }); };
  const hide = (keys: string[]) => setDone((d) => new Set([...d, ...keys]));

  const apply = async (gs: GeoGroup[], key: string) => {
    setBusy(key);
    try {
      const items = gs.flatMap((g) => g.items.map((i) => ({ asset_id: i.asset_id, lat: i.lat, lon: i.lon })));
      const r = await api.geoApply(repoId, items);
      if (r.job) trackJob(r.job);
      hide(gs.map((g) => g.key));
      toast(gs.length === 1 ? `${fmtInt(r.updated)} fotoğraf “${gs[0].place}” olarak konumlandı` : `${fmtInt(r.updated)} fotoğrafa konum verildi`, {
        action: { label: "Geri al", run: async () => { await api.revertEdits(repoId, items.map((i) => i.asset_id)); refreshAll(); } },
      });
      refreshAll();
    } catch (e) {
      toast(String((e as Error).message), { error: true });
    }
    setBusy(null);
  };
  const skip = async (g: GeoGroup) => {
    setBusy(g.key);
    try {
      await api.geoSkip(repoId, g.items.map((i) => i.asset_id));
      hide([g.key]);
    } catch (e) {
      toast(String((e as Error).message), { error: true });
    }
    setBusy(null);
  };
  const pickGpx = async () => {
    const { open } = await import("@tauri-apps/plugin-dialog");
    const r = await open({ multiple: false, directory: false, title: "GPX kaydını seç", filters: [{ name: "GPX", extensions: ["gpx"] }] });
    if (typeof r === "string") setGpx(r);
  };
  const clearSkips = async () => {
    const r = await api.geoClearSkips(repoId);
    toast(`${fmtInt(r.cleared)} atlanan fotoğraf yeniden önerilecek`);
    setDone(new Set());
    qc.invalidateQueries({ queryKey: ["geoSuggest", repoId] });
  };
  const data: GeoSuggestions | undefined = q.data;
  const remaining = groups.reduce((n, g) => n + g.count, 0);

  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="dialog" style={{ maxWidth: 680 }} role="dialog" aria-modal="true" aria-labelledby="geo-title">
        <div className="body" style={{ gap: 4, paddingTop: 20 }}>
          <h2 id="geo-title" style={{ margin: 0, font: "700 17px/1.3 var(--font-display)" }}>Konum önerileri</h2>
          <span className="muted" style={{ fontSize: 12 }}>
            {gpx
              ? <>GPX kaydı: <b>{gpx.split("/").pop()}</b>. Fotoğrafın çekildiği an izdeki konuma göre bulunur.</>
              : "Konumsuz fotoğraflar, aynı saatlerde çekilmiş konumlu fotoğraflara (ör. telefonundakilere) göre konumlanır."}
            {" "}Onayladığın konumlar geri alınabilir.
          </span>
        </div>
        <div className="body" style={{ paddingTop: 0, maxHeight: "62vh", overflowY: "auto", gap: 8 }}>
          {q.isLoading && <div className="loading-msg" style={{ padding: 40 }}><span className="spin" />Öneriler hazırlanıyor…</div>}
          {q.isError && <div className="banner warn">{String((q.error as Error).message)}</div>}
          {data && !groups.length && (
            <div className="muted" style={{ padding: "28px 0", textAlign: "center" }}>
              {data.missing === 0 ? "Bütün fotoğrafların konumu var." : gpx ? "Bu kayıtla zamanı eşleşen konumsuz fotoğraf yok." : "Şimdilik önerilecek konum yok. Bir GPX kaydı seçmeyi deneyebilirsin."}
            </div>
          )}
          {groups.map((g) => (
            <div key={g.key} className="preset" style={{ cursor: "default", flex: "none" }}>
              <div className="row" style={{ gap: 10, alignItems: "flex-start" }}>
                <div className="grow" style={{ minWidth: 0 }}>
                  <b className="ellipsis" style={{ display: "block" }}>{[g.place, g.region].filter(Boolean).join(", ")}</b>
                  <span className="muted" style={{ fontSize: 12 }}>
                    {fmtDate(g.day, false)} · {fmtInt(g.count)} {g.count === 1 ? "öğe" : "öğe"} · {gapText(Math.max(...g.items.map((i) => i.gap_s)))}
                  </span>
                </div>
                <button className="btn sm" disabled={busy === g.key} onClick={() => skip(g)} title="Bu fotoğraflar bir daha önerilmez">Atla</button>
                <button className="btn sm pri" disabled={busy === g.key} onClick={() => apply([g], g.key)}>Konumu ver</button>
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(8, minmax(0, 1fr))", gap: 4 }}>
                {g.items.slice(0, 8).map((i) => (
                  <img key={i.asset_id} src={thumbUrl(repoId, i.blake3, "s")} alt="" loading="lazy" title={fmtDate(i.taken_at)}
                    style={{ width: "100%", aspectRatio: "1 / 1", objectFit: "cover", borderRadius: 5, background: "var(--tile-bg)" }} />
                ))}
              </div>
              {g.count > 8 && <span className="muted" style={{ fontSize: 11.5 }}>ve {fmtInt(g.count - 8)} öğe daha</span>}
            </div>
          ))}
        </div>
        <footer style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 6 }}>
          <div className="row" style={{ gap: 4 }}>
            <button className="btn ghost" onClick={pickGpx} title="Saat, telefon ya da yürüyüş uygulamasından dışa aktarılmış iz">{gpx ? "Başka GPX seç…" : "GPX kaydından…"}</button>
            {gpx && <button className="btn ghost" onClick={() => setGpx("")}>Yakın fotoğraflara dön</button>}
            {!!data?.skipped && <button className="btn ghost" onClick={clearSkips} title="Atladığın fotoğraflar yeniden önerilsin">Atlananları geri getir ({fmtInt(data.skipped)})</button>}
          </div>
          <div className="row" style={{ gap: 6 }}>
            {groups.length > 1 && <button className="btn" disabled={busy === "all"} onClick={() => apply(groups, "all")}>Hepsini onayla ({fmtInt(remaining)})</button>}
            <button className="btn pri" onClick={onClose}>Bitti</button>
          </div>
        </footer>
      </div>
    </div>
  );
}
