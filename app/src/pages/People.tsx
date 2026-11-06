import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useState } from "react";
import { api, faceUrl } from "../api";
import { Check, Close, Dots, People as PeopleIcon } from "../components/Icons";
import { Toolbar } from "../components/Toolbar";
import { PersonPicker } from "../components/PersonPicker";
import { Popover } from "../components/Popover";
import { EMPTY_FILTER, useApp } from "../state";
import type { AssetItem, FaceCluster, Person } from "../types";
import { cls, fmtDate, fmtInt } from "../util";

function Avatar({ repoId, face, size = 72 }: { repoId: string; face: number | null; size?: number }) {
  return (
    <span style={{ width: size, height: size, borderRadius: "50%", overflow: "hidden", background: "var(--tile-bg)", flex: "none", display: "block" }}>
      {face && <img src={faceUrl(repoId, face)} alt="" style={{ width: "100%", height: "100%", objectFit: "cover" }} loading="lazy" />}
    </span>
  );
}

/** Asset ayrıntısından görüntüleyici öğesi üretir (kümeden fotoğraf açmak için). */
async function assetItem(repoId: string, assetId: number): Promise<AssetItem> {
  const d = await api.asset(repoId, assetId);
  const f = d.files.find((x) => x.state === "present") ?? d.files[0];
  return {
    file_id: f.id, rel_path: f.rel_path, tracked: f.tracked, size: d.size, asset_id: d.id, blake3: d.blake3, kind: d.kind, mime: d.mime,
    width: d.width, height: d.height, duration_ms: d.duration_ms, taken_at: d.taken_at, taken_at_source: d.taken_at_source,
    camera_model: d.camera_model, rating: d.rating, favorite: d.favorite, lat: d.lat, lon: d.lon, place: d.place, region: d.region,
    country_code: d.country_code, copies: 1, in_dup: 0,
  };
}

function FaceThumb({ repoId, faceId, assetId, onChange }: { repoId: string; faceId: number; assetId: number; onChange: () => void }) {
  const { openViewer, toast } = useApp();
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const close = useCallback(() => setAnchor(null), []);
  const act = async (reason: "removed" | "not_face") => {
    close();
    await api.excludeFace(repoId, faceId, reason);
    toast(reason === "removed" ? "Yüz bu kümeden çıkarıldı" : "Yüz olarak sayılmayacak", {
      action: { label: "Geri al", run: async () => { await api.excludeFace(repoId, faceId, null); onChange(); } },
    });
    onChange();
  };
  return (
    <>
      <button onClick={(e) => setAnchor(anchor ? null : e.currentTarget)} aria-label="Yüz seçenekleri" aria-expanded={!!anchor}
        style={{ border: 0, padding: 0, width: "100%", aspectRatio: "1 / 1", borderRadius: 6, overflow: "hidden", cursor: "pointer", background: "var(--tile-bg)", display: "block", outline: anchor ? "2px solid var(--accent)" : undefined }}>
        <img src={faceUrl(repoId, faceId)} alt="" style={{ width: "100%", height: "100%", objectFit: "cover" }} loading="lazy" />
      </button>
      {anchor && (
        <Popover anchor={anchor} onClose={close} width={200} align="start">
          <button className="mitem" onClick={async () => { close(); openViewer([await assetItem(repoId, assetId)], 0); }}>Fotoğrafı aç</button>
          <button className="mitem" onClick={() => act("removed")}>Bu kişi değil</button>
          <button className="mitem danger" onClick={() => act("not_face")}>Yüz değil</button>
        </Popover>
      )}
    </>
  );
}

function ClusterSheet({ repoId, c, onClose, onChange }: { repoId: string; c: FaceCluster; onClose: () => void; onChange: () => void }) {
  const { toast } = useApp();
  const qc = useQueryClient();
  const [saving, setSaving] = useState<string | null>(null);
  const save = async (n: string) => {
    const name = n.trim();
    if (!name || saving) return;
    setSaving(name);
    try {
      const r = (await api.nameCluster(repoId, c.cluster_id, name)) as { person_id: number; name: string };
      // Listeyi hemen güncelle (sunucudan taze liste gelene kadar): küme gider, kişi eklenir/güncellenir
      qc.setQueryData<{ persons: Person[]; clusters: FaceCluster[]; pet_clusters?: FaceCluster[] }>(["people", repoId], (old) => {
        if (!old) return old;
        const exists = old.persons.some((p) => p.id === r.person_id);
        const persons = exists
          ? old.persons.map((p) => (p.id === r.person_id ? { ...p, photos: p.photos + c.photos } : p))
          : [...old.persons, { id: r.person_id, name: r.name, kind: c.animal ? "pet" : "human", photos: c.photos, faces: c.faces, cover: c.sample_faces[0] ?? null, hidden: 0 }];
        return { persons, clusters: old.clusters.filter((x) => x.cluster_id !== c.cluster_id), pet_clusters: old.pet_clusters?.filter((x) => x.cluster_id !== c.cluster_id) };
      });
      toast(`${fmtInt(c.photos)} fotoğraf “${name}” olarak etiketlendi`);
      onClose();
      onChange();
    } catch (e) {
      toast(String((e as Error).message), { error: true });
      setSaving(null);
    }
  };
  const dissolve = async () => {
    const r = await api.dissolveCluster(repoId, c.cluster_id);
    toast(`${fmtInt(r.faces)} yüz ayrı tutulacak. Fotoğraflarda tek tek ad verebilirsin.`);
    onClose();
    onChange();
  };
  return (
    <div className="overlay" onClick={onClose}>
      <div className="dialog" style={{ maxWidth: 520 }} role="dialog" aria-modal="true" aria-label="Kişiye ad ver" onClick={(e) => e.stopPropagation()}>
        <div className="body" style={{ alignItems: "center", textAlign: "center", paddingTop: 22, gap: 10 }}>
          <Avatar repoId={repoId} face={c.sample_faces[0]} size={84} />
          <h2 style={{ margin: 0, font: "700 17px/1.3 var(--font-display)" }}>Bu kim?</h2>
          <span className="muted" style={{ fontSize: 12 }}>{fmtInt(c.photos)} fotoğrafta görünüyor. Ad verdiğinde benzer yüzler de etiketlenir.</span>
          {c.suggestion && (
            <div className="row" style={{ gap: 8 }}>
              <span>{c.suggestion.name} olabilir mi?</span>
              <button className="btn sm" onClick={async () => { await api.rejectSuggestion(repoId, c.cluster_id, c.suggestion!.person_id); onChange(); }}>Hayır</button>
              <button className="btn sm pri" onClick={() => save(c.suggestion!.name)}>Evet, {c.suggestion.name}</button>
            </div>
          )}
          <div style={{ width: "100%", display: "flex" }}>
            {saving
              ? <div className="field" style={{ flex: 1, height: 34, color: "var(--muted)" }}>“{saving}” kaydediliyor, benzer yüzler aranıyor…</div>
              : <PersonPicker repoId={repoId} onPick={save} autoFocus placeholder="Ad yaz ya da kişi seç" />}
          </div>
        </div>
        <div className="body" style={{ paddingTop: 0 }}>
          <div className="lbl">Bu gruptaki yüzler</div>
          {/* Kaydırma dış kutuda; ızgaranın kendisi yükseklikle sınırlanırsa satırlar ezilip yüzler üst üste biniyordu */}
          <div style={{ maxHeight: 280, overflowY: "auto", margin: "0 -4px", padding: "0 4px" }}>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(6, minmax(0, 1fr))", gap: 4 }}>
              {c.members.map((m) => <FaceThumb key={m.face_id} repoId={repoId} faceId={m.face_id} assetId={m.asset_id} onChange={onChange} />)}
            </div>
          </div>
          <span className="muted" style={{ fontSize: 11.5 }}>Aralarında başka biri varsa yüzüne tıklayıp “Bu kişi değil” de.</span>
        </div>
        <footer style={{ justifyContent: "space-between" }}>
          <button className="btn ghost" onClick={dissolve} title="Bu gruptaki yüzler aynı kişiye ait değil">Hepsi farklı kişiler</button>
          <button className="btn" onClick={onClose}>Bitti</button>
        </footer>
      </div>
    </div>
  );
}

/** Ad önerisi olan küme: tek dokunuşla onayla ya da reddet; ayrıntı için yüze tıkla. */
function SuggestTile({ repoId, c, onOpen, onChange }: { repoId: string; c: FaceCluster; onOpen: () => void; onChange: () => void }) {
  const { toast } = useApp();
  const qc = useQueryClient();
  const [busy, setBusy] = useState(false);
  const s = c.suggestion!;
  const drop = () => qc.setQueryData<{ persons: Person[]; clusters: FaceCluster[]; pet_clusters?: FaceCluster[] }>(["people", repoId], (old) => old && {
    ...old, clusters: old.clusters.filter((x) => x.cluster_id !== c.cluster_id),
    pet_clusters: old.pet_clusters?.filter((x) => x.cluster_id !== c.cluster_id),
  });
  const yes = async () => {
    setBusy(true);
    try {
      await api.nameCluster(repoId, c.cluster_id, s.name);
      drop();
      qc.setQueryData<{ persons: Person[]; clusters: FaceCluster[] }>(["people", repoId], (old) => old && {
        ...old, persons: old.persons.map((p) => (p.id === s.person_id ? { ...p, photos: p.photos + c.photos } : p)),
      });
      toast(`${fmtInt(c.photos)} fotoğraf “${s.name}” olarak etiketlendi`);
      onChange();
    } catch (e) {
      toast(String((e as Error).message), { error: true });
      setBusy(false);
    }
  };
  const no = async () => {
    setBusy(true);
    try {
      await api.rejectSuggestion(repoId, c.cluster_id, s.person_id);
      onChange();
    } catch (e) {
      toast(String((e as Error).message), { error: true });
    }
    setBusy(false);
  };
  return (
    <div className="person suggest-tile">
      <button className="person" style={{ padding: 0 }} onClick={onOpen} title="Bu gruptaki yüzleri gör">
        <Avatar repoId={repoId} face={c.sample_faces[0]} size={92} />
      </button>
      <span style={{ marginTop: 6, fontWeight: 500 }} className="ellipsis">{s.name} mi?</span>
      <span className="muted" style={{ fontSize: 11.5 }}>{fmtInt(c.photos)} fotoğraf</span>
      <div className="row" style={{ gap: 4, marginTop: 4 }}>
        <button className="btn sm" disabled={busy} onClick={no} aria-label={`${s.name} değil`}>Hayır</button>
        <button className="btn sm pri" disabled={busy} onClick={yes} aria-label={`Evet, ${s.name}`}>Evet</button>
      </div>
    </div>
  );
}

/** "Aynı kişi olabilir": iki ayrı adlandırılmış kişinin yüzleri çok benziyorsa birleştirme önerisi. */
function MergeSuggestions({ repoId, onChange }: { repoId: string; onChange: () => void }) {
  const { toast } = useApp();
  const q = useQuery({ queryKey: ["mergeSug", repoId], queryFn: () => api.mergeSuggestions(repoId) });
  const [busy, setBusy] = useState<string | null>(null);
  const list = q.data?.suggestions ?? [];
  if (!list.length) return null;
  const act = async (key: string, fn: () => Promise<unknown>, msg: string) => {
    setBusy(key);
    try {
      await fn();
      toast(msg);
      q.refetch();
      onChange();
    } catch (e) {
      toast(String((e as Error).message), { error: true });
    }
    setBusy(null);
  };
  return (
    <section className="col" style={{ gap: 4 }}>
      <h2>Aynı kişi olabilir</h2>
      <span className="muted" style={{ fontSize: 12 }}>Bu kişilerin yüzleri çok benziyor. Aynı kişiyse birleştir; fotoğrafları tek kişide toplanır.</span>
      <div className="merge-grid">
        {list.map((m) => {
          const key = `${m.into.id}-${m.other.id}`;
          return (
            <div key={key} className="merge-card">
              <div className="row" style={{ gap: 0, justifyContent: "center" }}>
                <Avatar repoId={repoId} face={m.into.cover} size={56} />
                <span style={{ marginLeft: -10, boxShadow: "0 0 0 3px var(--panel)", borderRadius: "50%" }}><Avatar repoId={repoId} face={m.other.cover} size={56} /></span>
              </div>
              <div style={{ textAlign: "center", fontSize: 12.5 }}><b>{m.other.name}</b> ve <b>{m.into.name}</b> aynı kişi mi?</div>
              <div className="row" style={{ gap: 6, justifyContent: "center" }}>
                <button className="btn sm" disabled={busy === key} onClick={() => act(key, () => api.dismissMerge(repoId, m.into.id, m.other.id), "Farklı kişiler olarak işaretlendi")}>Farklı</button>
                <button className="btn sm pri" disabled={busy === key} onClick={() => act(key, () => api.mergePerson(repoId, m.other.id, m.into.id), `${m.other.name}, ${m.into.name} ile birleştirildi`)}>Birleştir</button>
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

function ClusterTile({ repoId, c, onOpen }: { repoId: string; c: FaceCluster; onOpen: () => void }) {
  return (
    <button className="person" onClick={onOpen}>
      <Avatar repoId={repoId} face={c.sample_faces[0]} size={92} />
      <span className="add">{c.suggestion ? `${c.suggestion.name}?` : "Ad ekle"}</span>
      {c.animal && <span className="muted" style={{ fontSize: 11, textTransform: "capitalize" }}>{c.animal}</span>}
      <span className="muted" style={{ fontSize: 11.5 }}>{fmtInt(c.photos)} fotoğraf</span>
    </button>
  );
}

/** Kişinin kapak fotoğrafını seçme penceresi: kişinin fotoğraflarındaki yüzlerinden biri seçilir. */
function CoverSheet({ repoId, p, onClose }: { repoId: string; p: Person; onClose: () => void }) {
  const { toast } = useApp();
  const qc = useQueryClient();
  const faces = useQuery({ queryKey: ["personFaces", repoId, p.id], queryFn: () => api.personFaces(repoId, p.id) });
  const [cur, setCur] = useState<number | null>(p.cover);
  const choose = async (faceId: number | null) => {
    const prev = cur;
    setCur(faceId);
    // Liste hemen güncellensin
    qc.setQueryData<{ persons: Person[]; clusters: FaceCluster[] }>(["people", repoId], (old) => old && {
      ...old, persons: old.persons.map((x) => (x.id === p.id ? { ...x, cover: faceId ?? x.cover, cover_custom: faceId !== null } : x)),
    });
    try {
      await api.setPersonCover(repoId, p.id, faceId);
      qc.invalidateQueries({ queryKey: ["people", repoId] });
      if (faceId === null) toast("Kapak fotoğrafı otomatik seçilecek");
    } catch (e) {
      setCur(prev);
      toast(String((e as Error).message), { error: true });
      qc.invalidateQueries({ queryKey: ["people", repoId] });
    }
  };
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose]);
  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="dialog" style={{ maxWidth: 560 }} role="dialog" aria-modal="true" aria-labelledby="cover-title">
        <div className="body" style={{ alignItems: "center", textAlign: "center", paddingTop: 22, gap: 8 }}>
          <Avatar repoId={repoId} face={cur} size={96} />
          <h2 id="cover-title" style={{ margin: 0, font: "700 17px/1.3 var(--font-display)" }}>{p.name}</h2>
          <span className="muted" style={{ fontSize: 12 }}>Kapak fotoğrafı olarak kullanmak istediğin yüzü seç.</span>
        </div>
        <div className="body" style={{ paddingTop: 0 }}>
          <div style={{ maxHeight: 320, overflowY: "auto", margin: "0 -4px", padding: "4px" }}>
            {faces.isLoading && <div className="loading-msg" style={{ padding: 40 }}><span className="spin" />Yükleniyor…</div>}
            <div className="cover-grid">
              {faces.data?.map((f) => (
                <button key={f.id} className={cls("cover-opt", cur === f.id && "on")} onClick={() => choose(f.id)} aria-pressed={cur === f.id}
                  title={f.taken_at ? fmtDate(f.taken_at, false) : undefined}>
                  <img src={faceUrl(repoId, f.id)} alt="" loading="lazy" />
                  {cur === f.id && <span className="sel-check"><Check size={11} strokeWidth={3} /></span>}
                </button>
              ))}
            </div>
          </div>
        </div>
        <footer style={{ justifyContent: "space-between" }}>
          <button className="btn ghost" disabled={!p.cover_custom && cur === p.cover} onClick={() => choose(null)} title="En net yüz kendiliğinden seçilsin">Otomatik seç</button>
          <button className="btn pri" onClick={onClose} autoFocus>Bitti</button>
        </footer>
      </div>
    </div>
  );
}

function PersonCard({ repoId, p, all, onChange }: { repoId: string; p: Person; all: Person[]; onChange: () => void }) {
  const { setPage, setView, setFilter, toast } = useApp();
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const [mode, setMode] = useState<"menu" | "merge">("menu");
  const [query, setQuery] = useState("");
  const [renaming, setRenaming] = useState<string | null>(null);
  const [coverOpen, setCoverOpen] = useState(false);
  const close = useCallback(() => { setAnchor(null); setMode("menu"); setQuery(""); }, []);
  const closeCover = useCallback(() => setCoverOpen(false), []);
  const open = () => {
    setView("all");
    setFilter({ ...EMPTY_FILTER, person: String(p.id) });
    setPage("library");
  };
  const rename = async (n: string) => {
    setRenaming(null);
    if (n.trim() && n.trim() !== p.name) {
      await api.renamePerson(repoId, p.id, n.trim());
      onChange();
    }
  };
  const merge = async (into: Person) => {
    close();
    await api.mergePerson(repoId, p.id, into.id);
    toast(`${p.name} ve ${into.name} birleştirildi`);
    onChange();
  };
  const q = query.trim().toLocaleLowerCase("tr");
  const candidates = all.filter((o) => o.id !== p.id && (!q || o.name.toLocaleLowerCase("tr").includes(q)));
  return (
    <div className="person-wrap" onContextMenu={(e) => { e.preventDefault(); setAnchor(e.currentTarget.querySelector<HTMLElement>(".more")); }}>
      {renaming !== null ? (
        <div className="person">
          <Avatar repoId={repoId} face={p.cover} size={120} />
          <label className="field" style={{ width: "100%" }}>
            <span className="sr">Yeni ad</span>
            <input autoFocus value={renaming} onChange={(e) => setRenaming(e.target.value)} style={{ textAlign: "center" }}
              onKeyDown={(e) => { if (e.key === "Enter") rename(renaming); if (e.key === "Escape") setRenaming(null); }} onBlur={() => rename(renaming)} />
          </label>
        </div>
      ) : (
        <button className="person" onClick={open}>
          <Avatar repoId={repoId} face={p.cover} size={120} />
          <b className="ellipsis" style={{ maxWidth: "100%" }}>{p.name}</b>
          <span className="muted" style={{ fontSize: 11.5 }}>{fmtInt(p.photos)} fotoğraf</span>
        </button>
      )}
      <button className={cls("ibtn more", !!anchor && "on")} aria-expanded={!!anchor}
        onClick={(e) => (anchor ? close() : setAnchor(e.currentTarget))} aria-label={`${p.name} seçenekleri`}><Dots size={15} /></button>
      {anchor && (
        <Popover anchor={anchor} onClose={close} width={250}>
          {mode === "menu" ? (
            <>
              <button className="mitem" onClick={() => { close(); setRenaming(p.name); }}>Yeniden adlandır</button>
              <button className="mitem" onClick={() => { close(); setCoverOpen(true); }}>Kapak fotoğrafını değiştir…</button>
              {all.length > 1 && <button className="mitem" onClick={() => setMode("merge")}>Başka bir kişiyle birleştir…</button>}
              <div className="msep" />
              <button className="mitem danger" onClick={async () => { close(); await api.deletePerson(repoId, p.id); onChange(); }}>Kişiyi kaldır</button>
            </>
          ) : (
            <>
              <div className="lbl" style={{ padding: "6px 10px 4px" }}>{p.name} kiminle aynı kişi?</div>
              <label className="field" style={{ margin: "2px 4px 6px" }}>
                <span className="sr">Kişi ara</span>
                <input autoFocus placeholder="Ara" value={query} onChange={(e) => setQuery(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter" && candidates.length === 1) merge(candidates[0]); }} />
              </label>
              {candidates.map((o) => (
                <button key={o.id} className="mitem" onClick={() => merge(o)}>
                  <Avatar repoId={repoId} face={o.cover} size={22} />
                  <span className="grow ellipsis">{o.name}</span>
                  <span className="kbd-hint">{fmtInt(o.photos)}</span>
                </button>
              ))}
              {candidates.length === 0 && <div className="muted" style={{ padding: "6px 10px", fontSize: 12 }}>Eşleşen kişi yok</div>}
              <div className="msep" />
              <button className="mitem" onClick={() => { setMode("menu"); setQuery(""); }}>Geri</button>
            </>
          )}
        </Popover>
      )}
      {coverOpen && <CoverSheet repoId={repoId} p={p} onClose={closeCover} />}
    </div>
  );
}

export function People() {
  const { repoId, page, setPage, setView, setFilter, trackJob, toast } = useApp();
  const qc = useQueryClient();
  // Kişiler başka yerlerden de değişebilir (görüntüleyici, asistan, analiz): sayfa açıkken arada tazele
  const q = useQuery({ queryKey: ["people", repoId], queryFn: () => api.people(repoId!), enabled: !!repoId, refetchInterval: page === "people" ? 15000 : false });
  const smart = useQuery({ queryKey: ["smart", repoId], queryFn: () => api.smart(repoId!), enabled: !!repoId });
  const facets = useQuery({ queryKey: ["facets", repoId, "tracked"], queryFn: () => api.facets(repoId!, "tracked"), enabled: !!repoId });
  const [showAll, setShowAll] = useState(false);
  const [showAllSug, setShowAllSug] = useState(false);
  const [sheet, setSheet] = useState<number | null>(null);
  const [menu, setMenu] = useState<HTMLElement | null>(null);
  const closeMenu = useCallback(() => setMenu(null), []);
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["people", repoId] });
    qc.invalidateQueries({ queryKey: ["facets", repoId] });
    qc.invalidateQueries({ queryKey: ["mergeSug", repoId] });
  };
  if (!repoId) return null;
  const allPersons = q.data?.persons ?? [];
  const persons = allPersons.filter((p) => p.kind !== "pet");
  const petPersons = allPersons.filter((p) => p.kind === "pet");
  const petClusters = q.data?.pet_clusters ?? [];
  const clusters = q.data?.clusters ?? [];
  const suggested = clusters.filter((c) => c.suggestion);
  const unnamed = clusters.filter((c) => !c.suggestion);
  const s = smart.data;
  const pets = (facets.data?.tags ?? []).filter((t) => ["kedi", "köpek", "kuş", "at"].includes(t.tag));
  const openCluster = clusters.find((c) => c.cluster_id === sheet) ?? petClusters.find((c) => c.cluster_id === sheet);

  return (
    <>
      <Toolbar title="Kişiler" subtitle={q.data ? `${fmtInt(persons.length)} kişi${clusters.length ? ` · ${fmtInt(clusters.length)} adsız` : ""}` : undefined}>
        {!!s?.faces_found && (
          <button className={cls("ibtn", !!menu && "on")} onClick={(e) => setMenu(menu ? null : e.currentTarget)} aria-label="Diğer" title="Diğer"><Dots size={17} /></button>
        )}
      </Toolbar>
      {menu && (
        <Popover anchor={menu} onClose={closeMenu} width={240}>
          <button className="mitem" onClick={async () => { closeMenu(); trackJob(await api.recluster(repoId)); toast("Yüzler arka planda yeniden gruplanıyor; bitince liste kendiliğinden yenilenir."); }}>
            Yüzleri yeniden grupla
          </button>
        </Popover>
      )}
      <div className="scroll">
        <div className="page">
          {s && !s.face_model && (
            <div className="banner">
              <span className="grow">Kişileri tanımak için akıllı analizi aç. Yüzler bu Mac'te tanınır, hiçbir yere gönderilmez.</span>
              <button className="btn sm pri" onClick={() => setPage("settings")}>Aç…</button>
            </div>
          )}
          {s && s.face_model && (s.faces < s.total || s.videos_done < s.videos) && (
            <div className="banner">
              <span className="grow">Fotoğrafların taranıyor: {fmtInt(s.faces)} / {fmtInt(s.total)}. Yeni kişiler bulundukça burada görünür.</span>
              <button className="btn sm" onClick={async () => trackJob(await api.smartRun(repoId))}>Sürdür</button>
            </div>
          )}

          {q.data && persons.length === 0 && clusters.length === 0 && petPersons.length === 0 && petClusters.length === 0 && (
            <div className="empty"><People40 /><h2>Henüz kişi yok</h2><span>{s?.face_model ? "Fotoğraflarda yüz bulundukça kişiler burada toplanır." : "Akıllı analizi açtığında fotoğraflardaki kişiler burada toplanır."}</span></div>
          )}

          {persons.length > 0 && (
            <div className="people-grid" style={{ ["--av" as string]: "120px" }}>
              {persons.map((p) => <PersonCard key={p.id} repoId={repoId} p={p} all={persons} onChange={refresh} />)}
            </div>
          )}

          <MergeSuggestions repoId={repoId} onChange={refresh} />

          {suggested.length > 0 && (
            <section className="col" style={{ gap: 4 }}>
              <h2>Tanıdığın biri olabilir</h2>
              <span className="muted" style={{ fontSize: 12 }}>Bu yüzler adını verdiğin kişilere benziyor. Onaylarsan benzer yüzler de etiketlenir.</span>
              <div className="people-grid" style={{ ["--av" as string]: "92px", marginTop: 8 }}>
                {(showAllSug ? suggested : suggested.slice(0, 24)).map((c) => (
                  <SuggestTile key={c.cluster_id} repoId={repoId} c={c} onOpen={() => setSheet(c.cluster_id)} onChange={refresh} />
                ))}
              </div>
              {suggested.length > 24 && !showAllSug && <button className="btn ghost" style={{ alignSelf: "flex-start" }} onClick={() => setShowAllSug(true)}>Tümünü göster ({fmtInt(suggested.length)})</button>}
            </section>
          )}

          {unnamed.length > 0 && (
            <section className="col" style={{ gap: 4 }}>
              <h2>Adı olmayanlar</h2>
              <span className="muted" style={{ fontSize: 12 }}>En sık görünenler önce. Tanıdığın birine ad ver, benzer yüzler de etiketlensin.</span>
              <div className="people-grid" style={{ ["--av" as string]: "92px", marginTop: 8 }}>
                {(showAll ? unnamed : unnamed.slice(0, 24)).map((c) => (
                  <ClusterTile key={c.cluster_id} repoId={repoId} c={c} onOpen={() => setSheet(c.cluster_id)} />
                ))}
              </div>
              {unnamed.length > 24 && !showAll && <button className="btn ghost" style={{ alignSelf: "flex-start" }} onClick={() => setShowAll(true)}>Tümünü göster ({fmtInt(unnamed.length)})</button>}
            </section>
          )}

          {(petPersons.length > 0 || petClusters.length > 0 || pets.length > 0) && (
            <section className="col" style={{ gap: 8 }}>
              <h2>Evcil hayvanlar</h2>
              {petPersons.length > 0 && (
                <div className="people-grid" style={{ ["--av" as string]: "110px" }}>
                  {petPersons.map((p) => <PersonCard key={p.id} repoId={repoId} p={p} all={petPersons} onChange={refresh} />)}
                </div>
              )}
              {petClusters.length > 0 && (
                <>
                  <span className="muted" style={{ fontSize: 12 }}>{petPersons.length ? "Adı olmayan hayvanlar. Ad verince aynı hayvanın diğer fotoğrafları da etiketlenir." : "Fotoğraflarında bulunan hayvanlar. Birine ad ver; aynı hayvanın diğer fotoğrafları da etiketlenir."}</span>
                  <div className="people-grid" style={{ ["--av" as string]: "92px" }}>
                    {petClusters.slice(0, 48).map((c) => c.suggestion
                      ? <SuggestTile key={c.cluster_id} repoId={repoId} c={c} onOpen={() => setSheet(c.cluster_id)} onChange={refresh} />
                      : <ClusterTile key={c.cluster_id} repoId={repoId} c={c} onOpen={() => setSheet(c.cluster_id)} />)}
                  </div>
                </>
              )}
              <div className="row wrap">
                {pets.map((t) => (
                  <button key={t.tag} className="chip" style={{ height: 30, padding: "0 14px" }}
                    onClick={() => { setView("all"); setFilter({ ...EMPTY_FILTER, tag: t.tag }); setPage("library"); }}>
                    <span style={{ textTransform: "capitalize" }}>{t.tag}</span><span className="muted">{fmtInt(t.n)}</span>
                  </button>
                ))}
              </div>
            </section>
          )}
        </div>
      </div>
      {openCluster && <ClusterSheet repoId={repoId} c={openCluster} onClose={() => setSheet(null)} onChange={refresh} />}
    </>
  );
}

const People40 = () => <PeopleIcon size={40} />;

type FaceRef = { id: number; name: string | null; assigned_by: string | null; manual: number; frame_ms?: number | null; person_id?: number | null; quality?: number };

export function FaceNames({ repoId, assetId, faces, onChange }: { repoId: string; assetId: number; faces: FaceRef[]; onChange: () => void }) {
  const { toast } = useApp();
  const [editing, setEditing] = useState<number | "new" | null>(null);
  const qc = useQueryClient();
  const [faceMenu, setFaceMenu] = useState<{ el: HTMLElement; g: { key: string; rep: FaceRef; members: FaceRef[] } } | null>(null);
  const closeFaceMenu = useCallback(() => setFaceMenu(null), []);
  // Videolarda her karedeki yüz ayrı satırdır: kişi başına tek etiket, adsızlar tek "Bu kim? (N)" etiketi
  const isVideo = faces.some((f) => f.frame_ms !== null && f.frame_ms !== undefined);
  const groups: { key: string; rep: FaceRef; members: FaceRef[] }[] = [];
  if (isVideo) {
    const by = new Map<string, FaceRef[]>();
    for (const f of faces) {
      const k = f.person_id ? `p${f.person_id}` : f.manual ? `m${f.id}` : "unknown";
      by.set(k, [...(by.get(k) ?? []), f]);
    }
    for (const [key, members] of by) {
      const rep = [...members].sort((a, b) => (b.quality ?? 0) - (a.quality ?? 0))[0];
      groups.push({ key, rep, members });
    }
    groups.sort((a, b) => (a.key === "unknown" ? 1 : 0) - (b.key === "unknown" ? 1 : 0));
  } else {
    for (const f of faces) groups.push({ key: `f${f.id}`, rep: f, members: [f] });
  }
  const save = async (g: { rep: FaceRef; members: FaceRef[] }, name: string) => {
    setEditing(null);
    // Adsız grup: temsilci yüz adlandırılır; benzer diğer yüzler yeniden kümelemede otomatik atanır
    const targets = g.rep.name ? g.members : [g.rep];
    for (const f of targets) await api.assignFace(repoId, f.id, name);
    if (isVideo && !g.rep.name) await api.recluster(repoId);
    onChange();
  };
  const addWhole = async (name: string) => {
    setEditing(null);
    await api.tagPerson(repoId, assetId, name);
    toast(`${name} eklendi`);
    onChange();
  };
  const remove = async (g: { members: FaceRef[] }) => {
    for (const f of g.members) {
      if (f.manual) await api.deleteFace(repoId, f.id);
      else await api.assignFace(repoId, f.id, null);
    }
    onChange();
  };
  return (
    <div className="col" style={{ gap: 6 }}>
      <span className="lbl">Kişiler</span>
      <div className="row wrap" style={{ gap: 6 }}>
        {groups.map((g) => {
          const f = g.rep;
          const n = g.members.length;
          return editing === f.id ? (
            <div key={g.key} style={{ width: "100%", display: "flex" }}>
              <PersonPicker repoId={repoId} autoFocus initial={f.name ?? ""} onPick={(nm) => save(g, nm)} onCancel={() => setEditing(null)} height={30} />
            </div>
          ) : (
            <span key={g.key} className="tag" style={{ paddingLeft: 3, height: 30 }}
              onContextMenu={(e) => { if (f.name) { e.preventDefault(); setFaceMenu({ el: e.currentTarget, g }); } }}>
              <button className="face-av" onClick={(e) => f.name ? setFaceMenu({ el: e.currentTarget.parentElement as HTMLElement, g }) : setEditing(f.id)}
                title={f.name ? "Seçenekler" : "Ad ver"} aria-label={f.name ? `${f.name} seçenekleri` : "Ad ver"}>
                <img src={faceUrl(repoId, f.id)} alt="" />
              </button>
              <button style={{ border: 0, background: "none", padding: 0, cursor: "pointer", color: f.name ? "inherit" : "var(--accent)", font: "inherit" }}
                onClick={() => setEditing(f.id)} title={f.manual ? "Elle eklendi" : f.assigned_by === "auto" ? "Otomatik tanındı" : undefined}>
                {f.name ?? "Bu kim?"}{isVideo && !f.name && n > 1 ? ` (${n})` : ""}{f.assigned_by === "auto" ? " ·" : ""}
              </button>
              {f.name && (
                <button className="x" onClick={() => remove(g)}
                  aria-label={f.manual ? `${f.name} etiketini kaldır` : `${f.name} değil`} title={f.manual ? "Etiketi kaldır" : "Bu o değil"}><Close size={11} /></button>
              )}
            </span>
          );
        })}
        {faceMenu && (
          <Popover anchor={faceMenu.el} onClose={closeFaceMenu} width={220} align="start">
            <button className="mitem" onClick={() => { closeFaceMenu(); setEditing(faceMenu.g.rep.id); }}>Adı değiştir…</button>
            {faceMenu.g.rep.person_id && (
              <button className="mitem" onClick={async () => {
                const f = faceMenu.g.rep;
                closeFaceMenu();
                try {
                  await api.setPersonCover(repoId, f.person_id!, f.id);
                  qc.invalidateQueries({ queryKey: ["people", repoId] });
                  toast(`${f.name} için kapak fotoğrafı yapıldı`);
                } catch (e) {
                  toast(String((e as Error).message), { error: true });
                }
              }}>Kapak fotoğrafı yap</button>
            )}
            <div className="msep" />
            <button className="mitem danger" onClick={() => { closeFaceMenu(); remove(faceMenu.g); }}>{faceMenu.g.rep.manual ? "Etiketi kaldır" : `${faceMenu.g.rep.name} değil`}</button>
          </Popover>
        )}
        {editing === "new" ? (
          <div style={{ width: "100%", display: "flex" }}>
            <PersonPicker repoId={repoId} autoFocus onPick={addWhole} onCancel={() => setEditing(null)} placeholder="Kim var burada?" height={30} />
          </div>
        ) : (
          <button className="tag" style={{ background: "transparent", boxShadow: "inset 0 0 0 1px var(--line-strong)", height: 30, color: "var(--accent)" }} onClick={() => setEditing("new")}
            title="Model bulamadıysa kişiyi elle ekle (fotoğraflarda K ile yüzün üzerine kutu da çizebilirsin)">+ Kişi ekle</button>
        )}
      </div>
    </div>
  );
}
