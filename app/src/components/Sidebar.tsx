import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { Popover } from "./Popover";
import { DeviceIcon } from "../pages/Import";
import { api, isTauri } from "../api";
import { EMPTY_FILTER, useApp, useSummary, type LibraryView, type Page } from "../state";
import type { Album, Probe } from "../types";
import { SmartAlbumDialog, rulesText } from "./SmartAlbum";
import { cls, fmtInt } from "../util";
import { Activity } from "./Activity";
import { AddRepoDialog } from "./AddRepoDialog";
import { AlbumIcon, Broom, Check, Chevron, Clock, Dots, Film, Folder, FolderTree, Gear, Heart, MemoryIcon, People, Photos, Pin, Plus, SmartAlbum, Sparkles, Stack, Trash, Tray } from "./Icons";

async function pickFolder(): Promise<string | null> {
  if (isTauri) {
    const { open } = await import("@tauri-apps/plugin-dialog");
    const r = await open({ directory: true, multiple: false, title: "Fotoğraf ve videolarının bulunduğu klasörü seç" });
    return typeof r === "string" ? r : null;
  }
  return window.prompt("Klasör yolu:");
}

export async function revealInFinder(path: string) {
  if (isTauri) {
    const { revealItemInDir } = await import("@tauri-apps/plugin-opener");
    await revealItemInDir(path);
  }
}

function RepoMenu() {
  const { repoId, setRepoId, toast, trackJob } = useApp();
  const qc = useQueryClient();
  const repos = useQuery({ queryKey: ["repos"], queryFn: api.repos });
  const [open, setOpen] = useState(false);
  const [sub, setSub] = useState<string | null>(null);
  const [probe, setProbe] = useState<Probe | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  const btnRef = useRef<HTMLButtonElement>(null);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);
  const cur = repos.data?.find((r) => r.id === repoId);

  // Menü kenar çubuğunun kaydırma alanına hapsolmasın diye ekrana sabitlenir
  const toggle = () => {
    const r = btnRef.current?.getBoundingClientRect();
    if (r) setPos({ top: r.bottom + 4, left: r.left });
    setOpen((o) => !o);
  };

  useEffect(() => {
    if (!open) return;
    const close = () => { setOpen(false); setSub(null); };
    const h = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && close();
    const k = (e: KeyboardEvent) => e.key === "Escape" && close();
    window.addEventListener("mousedown", h);
    window.addEventListener("keydown", k);
    window.addEventListener("resize", close);
    return () => {
      window.removeEventListener("mousedown", h);
      window.removeEventListener("keydown", k);
      window.removeEventListener("resize", close);
    };
  }, [open]);

  // Seçili kütüphane listeden çıktıysa ilk bağlı olana geç
  useEffect(() => {
    if (!repos.data) return;
    if (!repoId || !repos.data.some((r) => r.id === repoId)) {
      const first = repos.data.find((r) => r.online) ?? repos.data[0];
      if ((first?.id ?? null) !== repoId) setRepoId(first?.id ?? null);
    }
  }, [repos.data, repoId, setRepoId]);

  const addFolder = async () => {
    setOpen(false);
    const path = await pickFolder();
    if (!path) return;
    try {
      const p = await api.probe(path);
      if (p.is_repo) {
        const e = await api.addRepo(p.path);
        await qc.invalidateQueries({ queryKey: ["repos"] });
        setRepoId(e.id);
        toast(`“${e.name}” açıldı`);
        trackJob(await api.scan(e.id));
        return;
      }
      if (p.inside_repo) {
        toast(`Bu klasör zaten bir kütüphanenin içinde: ${p.inside_repo}`, { error: true });
        return;
      }
      setProbe(p);
    } catch (e) {
      toast(String((e as Error).message), { error: true });
    }
  };

  // Karşılama ekranındaki “Klasör ekle…” düğmesi
  useEffect(() => {
    const h = () => { addFolder(); };
    window.addEventListener("mm:add-folder", h);
    return () => window.removeEventListener("mm:add-folder", h);
  });

  const remove = async (id: string, name: string) => {
    setOpen(false);
    setSub(null);
    await api.removeRepo(id);
    await qc.invalidateQueries({ queryKey: ["repos"] });
    toast(`“${name}” listeden kaldırıldı. Dosyalarına dokunulmadı.`);
  };

  return (
    <div ref={ref} style={{ position: "relative", marginBottom: 6 }}>
      <button ref={btnRef} className={cls("repo-btn", open && "open")} onClick={toggle} aria-expanded={open} aria-haspopup="menu">
        <span className={cls("dot", cur && !cur.online && "off")} />
        <span className="grow" style={{ minWidth: 0 }}>
          <span style={{ display: "block", fontWeight: 600 }} className="ellipsis">{cur?.name ?? "Kütüphane seç"}</span>
          <span className="ellipsis" style={{ display: "block", fontSize: 11, color: "var(--muted)" }}>
            {cur ? (cur.online ? `${fmtInt((cur.counts?.tracked ?? 0) + (cur.counts?.untracked ?? 0))} öğe` : "Disk bağlı değil") : "Henüz kütüphane yok"}
          </span>
        </span>
        <Chevron size={13} style={{ color: "var(--muted)", flex: "none" }} />
      </button>
      {open && (
        <div className="menu" role="menu" style={{ position: "fixed", top: pos?.top ?? 0, left: pos?.left ?? 0, width: 290 }}>
          {repos.data?.map((r) => (
            <div key={r.id} style={{ position: "relative" }}>
              <div className={cls("mitem", r.id === repoId && "cur")} role="menuitem" tabIndex={0} style={{ opacity: r.online ? 1 : 0.55 }}
                onClick={() => { if (r.online) { setRepoId(r.id); setOpen(false); } }}
                onKeyDown={(e) => e.key === "Enter" && r.online && (setRepoId(r.id), setOpen(false))}>
                <span style={{ width: 14, flex: "none", display: "inline-flex" }}>{r.id === repoId && <Check size={13} />}</span>
                <span className="grow" style={{ minWidth: 0 }}>
                  <span style={{ display: "block" }} className="ellipsis">{r.name}</span>
                  <span className="ellipsis sub" style={{ display: "block", fontSize: 11 }}>{r.online ? r.last_path : "Disk bağlı değil"}</span>
                </span>
                <button className="ibtn" style={{ width: 22, height: 22, color: "inherit" }} aria-label={`${r.name} seçenekleri`}
                  onClick={(e) => { e.stopPropagation(); setSub(sub === r.id ? null : r.id); }}><Dots size={14} /></button>
              </div>
              {sub === r.id && (
                <div className="menu" style={{ left: "calc(100% - 24px)", top: 0, width: 220 }}>
                  <button className="mitem" disabled={!r.online} onClick={() => { revealInFinder(r.last_path); setSub(null); }}>Finder'da göster</button>
                  <button className="mitem" disabled={!r.online} onClick={async () => { setSub(null); setOpen(false); trackJob(await api.scan(r.id)); }}>Değişiklikleri tara</button>
                  <div className="msep" />
                  <button className="mitem danger" onClick={() => remove(r.id, r.name)}>Listeden kaldır</button>
                </div>
              )}
            </div>
          ))}
          {!!repos.data?.length && <div className="msep" />}
          <button className="mitem" onClick={addFolder}><Plus size={14} />Klasör ekle…</button>
          <button className="mitem" onClick={addFolder}><Folder size={14} />Var olan kütüphaneyi aç…</button>
        </div>
      )}
      {probe && <AddRepoDialog probe={probe} onClose={() => setProbe(null)} onPickAgain={async () => { setProbe(null); await addFolder(); }} />}
    </div>
  );
}

/** Bağlı telefon / kart: takılınca kenar çubuğunda belirir, tıklayınca içe aktarma açılır. */
function Devices() {
  const { page, deviceId, setDeviceId, setPage, toast } = useApp();
  const q = useQuery({ queryKey: ["devices"], queryFn: api.devices, refetchInterval: 5000 });
  const list = q.data?.devices ?? [];
  const seen = useRef<Set<string> | null>(null);
  useEffect(() => {
    if (!q.data) return;
    const ids = new Set(list.map((d) => d.id));
    if (seen.current) {
      for (const d of list) if (!seen.current.has(d.id)) toast(`${d.name} bağlandı`, { action: { label: "İçe aktar", run: () => { setDeviceId(d.id); setPage("import"); } } });
    }
    seen.current = ids;
  }, [q.data]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!list.length) return null;
  return (
    <>
      <div className="sec-title">Aygıtlar</div>
      {list.map((d) => (
        <button key={d.id} className={cls("nav", page === "import" && deviceId === d.id && "on")} onClick={() => { setDeviceId(d.id); setPage("import"); }}>
          <DeviceIcon d={d} size={16} /><span className="ellipsis grow">{d.name}</span>
          {d.locked ? <span className="cnt">kilitli</span> : !d.ready ? <span className="cnt">…</span> : null}
        </button>
      ))}
    </>
  );
}

/** Kenar çubuğundaki albümler: tıkla aç, sağ tıkla yeniden adlandır/sil, + ile yeni albüm. */
function Albums() {
  const { repoId, page, view, filter, setPage, setView, setFilter, toast } = useApp();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["albums", repoId], queryFn: () => api.albums(repoId!), enabled: !!repoId });
  const [creating, setCreating] = useState<string | null>(null);
  const [menu, setMenu] = useState<{ el: HTMLElement; id: number; name: string } | null>(null);
  const [renaming, setRenaming] = useState<{ id: number; name: string } | null>(null);
  const [addMenu, setAddMenu] = useState<HTMLElement | null>(null);
  const [smartEdit, setSmartEdit] = useState<Album | "new" | null>(null);
  const closeMenu = useCallback(() => setMenu(null), []);
  const closeAdd = useCallback(() => setAddMenu(null), []);
  const closeSmart = useCallback(() => setSmartEdit(null), []);
  const refresh = () => qc.invalidateQueries({ queryKey: ["albums", repoId] });
  const create = async () => {
    const name = (creating ?? "").trim();
    setCreating(null);
    if (!name || !repoId) return;
    const r = await api.createAlbum(repoId, { name });
    refresh();
    setPage("library"); setView("album"); setFilter({ ...EMPTY_FILTER, album: String(r.id), label: name });
    toast(`“${name}” oluşturuldu. Fotoğraflara sağ tıklayıp “Albüme ekle”yi seç.`);
  };
  const rename = async () => {
    if (!renaming || !repoId) return;
    const { id, name } = renaming;
    setRenaming(null);
    if (name.trim()) { await api.updateAlbum(repoId, id, { name: name.trim() }); refresh(); }
  };
  const remove = async (id: number, name: string) => {
    closeMenu();
    if (!repoId) return;
    await api.deleteAlbum(repoId, id);
    refresh();
    if (view === "album" && filter.album === String(id)) { setView("all"); setFilter(EMPTY_FILTER); }
    toast(`“${name}” albümü silindi. Fotoğraflara dokunulmadı.`);
  };
  const albums = q.data?.albums ?? [];
  return (
    <>
      <div className="sec-title row" style={{ paddingRight: 2 }}>
        <span className="grow">Albümler</span>
        <button className={cls("ibtn", !!addMenu && "on")} style={{ width: 20, height: 20 }} onClick={(e) => setAddMenu(addMenu ? null : e.currentTarget)} aria-label="Yeni albüm" title="Yeni albüm"><Plus size={13} /></button>
      </div>
      {albums.map((a) => renaming?.id === a.id ? (
        <label key={a.id} className="field" style={{ margin: "1px 0" }}>
          <span className="sr">Albüm adı</span>
          <input autoFocus value={renaming.name} onChange={(e) => setRenaming({ id: a.id, name: e.target.value })} onBlur={rename}
            onKeyDown={(e) => { e.stopPropagation(); if (e.key === "Enter") rename(); if (e.key === "Escape") setRenaming(null); }} />
        </label>
      ) : (
        <button key={a.id} className={cls("nav", page === "library" && view === "album" && filter.album === String(a.id) && "on")}
          onClick={() => { setPage("library"); setView("album"); setFilter({ ...EMPTY_FILTER, album: String(a.id), label: a.name }); }}
          onContextMenu={(e) => { e.preventDefault(); setMenu({ el: e.currentTarget, id: a.id, name: a.name }); }}
          title={a.kind === "smart" ? `Akıllı albüm: ${rulesText(a.rules) || "tüm fotoğraflar"}` : undefined}>
          {a.kind === "smart" ? <SmartAlbum size={16} /> : <AlbumIcon size={16} />}
          <span className="ellipsis grow">{a.name}</span>
          {a.count ? <span className="cnt">{fmtInt(a.count)}</span> : null}
        </button>
      ))}
      {creating !== null && (
        <label className="field" style={{ margin: "1px 0" }}>
          <span className="sr">Yeni albüm adı</span>
          <input autoFocus placeholder="Albüm adı" value={creating} onChange={(e) => setCreating(e.target.value)} onBlur={create}
            onKeyDown={(e) => { e.stopPropagation(); if (e.key === "Enter") create(); if (e.key === "Escape") setCreating(null); }} />
        </label>
      )}
      {!albums.length && creating === null && <button className="nav muted" onClick={() => setCreating("")}><AlbumIcon size={16} /><span>Yeni albüm…</span></button>}
      {menu && (
        <Popover anchor={menu.el} onClose={closeMenu} width={200} align="start">
          <button className="mitem" onClick={() => { closeMenu(); setRenaming({ id: menu.id, name: menu.name }); }}>Yeniden adlandır</button>
          {albums.find((a) => a.id === menu.id)?.kind === "smart" && (
            <button className="mitem" onClick={() => { closeMenu(); setSmartEdit(albums.find((a) => a.id === menu.id)!); }}>Kuralları düzenle…</button>
          )}
          <div className="msep" />
          <button className="mitem danger" onClick={() => remove(menu.id, menu.name)}>Albümü sil</button>
        </Popover>
      )}
      {addMenu && (
        <Popover anchor={addMenu} onClose={closeAdd} width={210} align="start">
          <button className="mitem" onClick={() => { closeAdd(); setCreating(""); }}>Yeni albüm</button>
          <button className="mitem" onClick={() => { closeAdd(); setSmartEdit("new"); }}>Yeni akıllı albüm…</button>
        </Popover>
      )}
      {smartEdit && repoId && <SmartAlbumDialog repoId={repoId} album={smartEdit === "new" ? undefined : smartEdit} onClose={closeSmart} />}
    </>
  );
}

export function Sidebar() {
  const { repoId, page, setPage, view, setView, setFilter, assistantOpen, toggleAssistant, deviceId, setDeviceId } = useApp();
  const { data: s } = useSummary(repoId);
  const c = s?.counts;
  const go = (p: Page, v?: LibraryView) => () => {
    setPage(p);
    if (v) {
      setView(v);
      setFilter(EMPTY_FILTER);
    }
  };
  const isOn = (p: Page, v?: LibraryView) => {
    if (page !== p) return false;
    if (!v) return true;
    if (v === "all") return view === "all" || view === "photo";
    return view === v;
  };
  const item = (p: Page, label: string, icon: React.ReactNode, o: { v?: LibraryView; count?: number; badge?: number } = {}) => (
    <button key={label} className={cls("nav", isOn(p, o.v) && "on")} onClick={go(p, o.v)}>
      {icon}<span className="ellipsis grow">{label}</span>
      {o.badge ? <span className="badge-dot">{o.badge > 999 ? "999+" : o.badge}</span> : o.count ? <span className="cnt">{fmtInt(o.count)}</span> : null}
    </button>
  );
  return (
    <aside className="side">
      <div className="titlebar" data-tauri-drag-region />
      <RepoMenu />
      {repoId && (
        <nav aria-label="Bölümler" style={{ display: "contents" }}>
          <div className="sec-title">Fotoğraflar</div>
          {item("library", "Kütüphane", <Photos size={16} />, { v: "all", count: c?.tracked })}
          {item("library", "Videolar", <Film size={16} />, { v: "video" })}
          {item("library", "Favoriler", <Heart size={16} />, { v: "favorites" })}
          {item("memories", "Anılar", <MemoryIcon size={16} />)}
          {item("library", "Klasörler", <Folder size={16} />, { v: "folders" })}
          {item("people", "Kişiler", <People size={16} />)}
          {item("places", "Yerler", <Pin size={16} />)}
          {!!c?.untracked && item("library", "Yeni eklenenler", <Tray size={16} />, { v: "untracked", badge: c.untracked })}
          <Albums />
          <Devices />
          <div className="sec-title">Araçlar</div>
          <button className={cls("nav", page === "import" && !deviceId && "on")} onClick={() => { setDeviceId(null); setPage("import"); }}>
            <DeviceIcon d={{ kind: "device" }} size={16} /><span className="ellipsis grow">İçe aktar</span>
          </button>
          {item("cleanup", "Temizlik", <Broom size={16} />)}
          {item("dups", "Tekrarlar", <Stack size={16} />, { count: s?.dup_files })}
          {item("organize", "Klasörleri düzenle", <FolderTree size={16} />)}
          {item("history", "Geçmiş", <Clock size={16} />, { badge: s?.pending_plans })}
          {item("trash", "Son silinenler", <Trash size={16} />, { count: s?.trash_files })}
        </nav>
      )}
      <div style={{ flex: 1 }} />
      <Activity />
      <div className="side-foot">
        <button className={cls("nav", assistantOpen && "on")} onClick={toggleAssistant} title="Asistan (⌘J)">
          <Sparkles size={16} /><span className="grow">Asistan</span><span className="kbd-hint">⌘J</span>
        </button>
        <button className={cls("nav icon-only", (page === "settings" || page === "assistant") && "on")} onClick={() => setPage("settings")} title="Ayarlar (⌘,)" aria-label="Ayarlar">
          <Gear size={16} />
        </button>
      </div>
    </aside>
  );
}
