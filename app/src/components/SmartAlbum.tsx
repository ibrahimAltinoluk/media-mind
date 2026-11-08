import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { Close } from "./Icons";
import { EMPTY_FILTER, useApp } from "../state";
import type { Album } from "../types";
import { cls, fmtInt } from "../util";

type Rules = Record<string, string>;
type When = "any" | "last" | "year" | "range";

const LAST = [["7", "Son 7 gün"], ["30", "Son 30 gün"], ["90", "Son 3 ay"], ["365", "Son 1 yıl"]] as const;

function whenOf(r: Rules): When {
  if (r.last_days) return "last";
  if (r.year) return "year";
  if (r.date_from || r.date_to) return "range";
  return "any";
}

/** Kuralı insan diliyle (albüm başlığının altında ve kenar çubuğu ipucunda). */
export function rulesText(r: Rules | null | undefined, people: { id: number; name: string }[] = []): string {
  if (!r) return "";
  const parts: string[] = [];
  const names = (r.person ?? "").split(",").filter(Boolean).map((id) => people.find((p) => String(p.id) === id)?.name ?? "bir kişi");
  if (names.length) parts.push(names.length > 1 ? `${names.join(" + ")} birlikte` : names[0]);
  if (r.place) parts.push(r.place);
  if (r.region) parts.push(r.region);
  if (r.tag) parts.push(r.tag);
  if (r.camera) parts.push(r.camera);
  if (r.search) parts.push(`“${r.search}”`);
  if (r.last_days) parts.push(LAST.find(([d]) => d === r.last_days)?.[1].toLocaleLowerCase("tr") ?? `son ${r.last_days} gün`);
  if (r.year) parts.push(r.year);
  if (r.date_from || r.date_to) parts.push(`${r.date_from ?? "…"} – ${r.date_to ?? "…"}`);
  if (r.kind === "video") parts.push("yalnız videolar");
  if (r.kind === "photo") parts.push("yalnız fotoğraflar");
  if (r.favorite) parts.push("favoriler");
  return parts.join(" · ");
}

/** Akıllı albüm oluştur/düzenle: kurallar seçilir, uyan öğe sayısı canlı görünür. */
export function SmartAlbumDialog({ repoId, album, onClose }: { repoId: string; album?: Album; onClose: () => void }) {
  const { toast, setPage, setView, setFilter } = useApp();
  const qc = useQueryClient();
  const facets = useQuery({ queryKey: ["facets", repoId, "tracked"], queryFn: () => api.facets(repoId, "tracked") });
  const [name, setName] = useState(album?.name ?? "");
  const [r, setR] = useState<Rules>(() => ({ ...(album?.rules ?? {}) }));
  const [when, setWhen] = useState<When>(() => whenOf(album?.rules ?? {}));
  const [saving, setSaving] = useState(false);
  const f = facets.data;
  const set = (k: string, v: string) => setR((x) => { const n = { ...x }; if (v) n[k] = v; else delete n[k]; return n; });
  const persons = (r.person ?? "").split(",").filter(Boolean);

  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose]);

  // Zaman seçimi değişince diğer zaman kuralları temizlenir
  const chooseWhen = (w: When) => {
    setWhen(w);
    setR((x) => {
      const n = { ...x };
      if (w !== "last") delete n.last_days;
      if (w !== "year") delete n.year;
      if (w !== "range") { delete n.date_from; delete n.date_to; }
      if (w === "last" && !n.last_days) n.last_days = "30";
      if (w === "year" && !n.year && f?.years[0]) n.year = f.years[0].year;
      return n;
    });
  };

  const preview = useMemo(() => {
    const p: Record<string, string | boolean> = {};
    for (const k of ["kind", "year", "place", "region", "camera", "tag", "person", "date_from", "date_to"]) if (r[k]) p[k] = r[k];
    if (r.favorite) p.favorite = true;
    if (r.last_days) p.date_from = new Date(Date.now() - Number(r.last_days) * 864e5).toISOString().slice(0, 10);
    return p;
  }, [r]);
  const count = useQuery({
    queryKey: ["smartPreview", repoId, preview],
    queryFn: () => api.assets(repoId, { ...preview, limit: 1, offset: 0 }),
    enabled: !r.search, placeholderData: (prev) => prev,
  });

  const save = async () => {
    const n = name.trim();
    if (!n || saving) return;
    setSaving(true);
    try {
      let id = album?.id;
      if (album) await api.updateAlbum(repoId, album.id, { name: n, rules: r });
      else id = (await api.createAlbum(repoId, { name: n, kind: "smart", rules: r })).id;
      qc.invalidateQueries({ queryKey: ["albums", repoId] });
      qc.invalidateQueries({ queryKey: ["assets", repoId] });
      toast(album ? `“${n}” güncellendi` : `“${n}” akıllı albümü oluşturuldu`);
      setPage("library"); setView("album"); setFilter({ ...EMPTY_FILTER, album: String(id), label: n });
      onClose();
    } catch (e) {
      toast(String((e as Error).message), { error: true });
      setSaving(false);
    }
  };

  const sel = (label: string, k: string, opts: { v: string; l: string }[], any = "Hepsi") => (
    <label className="smart-row">
      <span>{label}</span>
      <select className="field" value={r[k] ?? ""} onChange={(e) => set(k, e.target.value)}>
        <option value="">{any}</option>
        {r[k] && !opts.some((o) => o.v === r[k]) && <option value={r[k]}>{r[k]}</option>}
        {opts.map((o) => <option key={o.v} value={o.v}>{o.l}</option>)}
      </select>
    </label>
  );

  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="dialog" style={{ maxWidth: 520 }} role="dialog" aria-modal="true" aria-labelledby="smart-title">
        <div className="body" style={{ gap: 4, paddingTop: 20 }}>
          <h2 id="smart-title" style={{ margin: 0, font: "700 17px/1.3 var(--font-display)" }}>{album ? "Akıllı albümü düzenle" : "Yeni akıllı albüm"}</h2>
          <span className="muted" style={{ fontSize: 12 }}>Kurallara uyan fotoğraflar albüme kendiliğinden girer; yeni çekilenler de.</span>
        </div>
        <div className="body smart-form" style={{ paddingTop: 0 }}>
          <label className="smart-row">
            <span>Ad</span>
            <input className="field" autoFocus={!album} value={name} onChange={(e) => setName(e.target.value)} placeholder="ör. Ayşe ile tatiller"
              onKeyDown={(e) => { e.stopPropagation(); if (e.key === "Enter") save(); }} />
          </label>

          <div className="smart-row">
            <span>Kişiler</span>
            <div className="row wrap" style={{ gap: 4, flex: 1 }}>
              {persons.map((id) => (
                <span key={id} className="tag" style={{ height: 26 }}>
                  {f?.people.find((p) => String(p.id) === id)?.name ?? "Kişi"}
                  <button className="x" aria-label="Kaldır" onClick={() => set("person", persons.filter((x) => x !== id).join(","))}><Close size={10} /></button>
                </span>
              ))}
              <select className="field" value="" style={{ flex: 1, minWidth: 120 }} onChange={(e) => e.target.value && set("person", [...persons, e.target.value].join(","))}>
                <option value="">{persons.length ? "Kişi ekle…" : "Herkes"}</option>
                {f?.people.filter((p) => !persons.includes(String(p.id))).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>
            </div>
          </div>
          {persons.length > 1 && <span className="muted smart-hint">Seçilen kişilerin hepsinin birlikte olduğu fotoğraflar.</span>}

          {sel("Yer", "place", (f?.places ?? []).map((p) => ({ v: p.place, l: p.region ? `${p.place}, ${p.region}` : p.place })), "Her yer")}

          <div className="smart-row">
            <span>Zaman</span>
            <div className="row wrap" style={{ gap: 6, flex: 1 }}>
              <select className="field" value={when} onChange={(e) => chooseWhen(e.target.value as When)} style={{ flex: 1 }}>
                <option value="any">Her zaman</option>
                <option value="last">Son günler</option>
                <option value="year">Belirli bir yıl</option>
                <option value="range">Tarih aralığı</option>
              </select>
              {when === "last" && (
                <select className="field" value={r.last_days ?? "30"} onChange={(e) => set("last_days", e.target.value)} style={{ flex: 1 }}>
                  {LAST.map(([d, l]) => <option key={d} value={d}>{l}</option>)}
                </select>
              )}
              {when === "year" && (
                <select className="field" value={r.year ?? ""} onChange={(e) => set("year", e.target.value)} style={{ flex: 1 }}>
                  {(f?.years ?? []).map((y) => <option key={y.year} value={y.year}>{y.year}</option>)}
                </select>
              )}
              {when === "range" && (
                <>
                  <input className="field" type="date" value={r.date_from ?? ""} onChange={(e) => set("date_from", e.target.value)} aria-label="Başlangıç" />
                  <input className="field" type="date" value={r.date_to ?? ""} onChange={(e) => set("date_to", e.target.value)} aria-label="Bitiş" />
                </>
              )}
            </div>
          </div>
          {when === "last" && <span className="muted smart-hint">Her açılışta bugüne göre hesaplanır.</span>}

          <div className="smart-row">
            <span>Tür</span>
            <div className="seg">
              {([["", "Hepsi"], ["photo", "Fotoğraf"], ["video", "Video"]] as const).map(([v, l]) => (
                <button key={v} className={cls((r.kind ?? "") === v && "on")} onClick={() => set("kind", v)}>{l}</button>
              ))}
            </div>
            <label className="row" style={{ gap: 6, marginLeft: "auto", fontSize: 12.5 }}>
              <input type="checkbox" checked={!!r.favorite} onChange={(e) => set("favorite", e.target.checked ? "1" : "")} />Yalnız favoriler
            </label>
          </div>

          {sel("İçerik", "tag", (f?.tags ?? []).slice(0, 150).map((t) => ({ v: t.tag, l: `${t.tag} (${fmtInt(t.n)})` })), "Hepsi")}
          {sel("Kamera", "camera", (f?.cameras ?? []).map((c) => ({ v: c.camera, l: c.camera })), "Hepsi")}
          <label className="smart-row">
            <span>Arama</span>
            <input className="field" value={r.search ?? ""} onChange={(e) => set("search", e.target.value)} placeholder="ör. kumsalda gün batımı (isteğe bağlı)"
              onKeyDown={(e) => e.stopPropagation()} />
          </label>
        </div>
        <footer style={{ justifyContent: "space-between" }}>
          <span className="muted" style={{ fontSize: 12.5 }}>
            {r.search ? "Aramalı albümün içeriği açılınca hesaplanır" : count.data ? `${fmtInt(count.data.total)} öğe uyuyor` : count.isFetching ? "Hesaplanıyor…" : ""}
          </span>
          <div className="row" style={{ gap: 6 }}>
            <button className="btn" onClick={onClose}>Vazgeç</button>
            <button className="btn pri" disabled={!name.trim() || saving} onClick={save}>{album ? "Kaydet" : "Oluştur"}</button>
          </div>
        </footer>
      </div>
    </div>
  );
}
