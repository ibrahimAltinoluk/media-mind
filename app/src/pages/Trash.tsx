import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api, thumbUrl } from "../api";
import { Check, Image, Play, Trash } from "../components/Icons";
import { Popover } from "../components/Popover";
import { Toolbar } from "../components/Toolbar";
import { useApp } from "../state";
import type { AssetItem, TrashItem } from "../types";
import { basename, cls, fmtBytes, fmtDate, fmtDuration, fmtInt } from "../util";

function toItem(t: TrashItem): AssetItem {
  return {
    file_id: t.file_id, rel_path: t.src, tracked: t.tracked, size: t.size, asset_id: t.asset_id, blake3: t.blake3, kind: t.kind,
    mime: t.mime, width: t.width, height: t.height, duration_ms: t.duration_ms, taken_at: t.taken_at, taken_at_source: null,
    camera_model: null, rating: 0, favorite: t.favorite, lat: null, lon: null, place: null, region: null, country_code: null, copies: 1, in_dup: 0,
  };
}

function dayLabel(day: string): string {
  const d = new Date();
  const iso = (x: Date) => `${x.getFullYear()}-${String(x.getMonth() + 1).padStart(2, "0")}-${String(x.getDate()).padStart(2, "0")}`;
  if (day === iso(d)) return "Bugün";
  d.setDate(d.getDate() - 1);
  if (day === iso(d)) return "Dün";
  return fmtDate(day, false);
}

function TrashTile({ repoId, t, selected, onToggle, onOpen, onMenu }: { repoId: string; t: TrashItem; selected: boolean; onToggle: (e: React.MouseEvent) => void; onOpen: () => void; onMenu: (e: React.MouseEvent) => void }) {
  const [failed, setFailed] = useState(false);
  return (
    <button className={cls("tile trash-tile", selected && "sel")} onClick={onToggle} onDoubleClick={onOpen} onContextMenu={(e) => { e.preventDefault(); onMenu(e); }}
      aria-pressed={selected} aria-label={basename(t.src)} title={`${t.src}\n${fmtBytes(t.size)} · ${fmtDate(t.applied_at)} silindi\nÇift tıkla: büyüt`}>
      {failed ? <span className="ph"><Image size={24} /></span> : (
        <img src={thumbUrl(repoId, t.blake3, "s")} loading="lazy" decoding="async" alt="" draggable={false} onError={() => setFailed(true)} />
      )}
      {t.kind === "video" && <span className="badge b-vid"><Play size={10} />{fmtDuration(t.duration_ms)}</span>}
      {selected && <span className="sel-check"><Check size={11} strokeWidth={3} /></span>}
    </button>
  );
}

export function TrashPage() {
  const { repoId, toast, openViewer } = useApp();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["trash", repoId], queryFn: () => api.trash(repoId!), enabled: !!repoId });
  const [sel, setSel] = useState<Set<number>>(new Set());
  const [anchor, setAnchor] = useState<number | null>(null);
  const [confirm, setConfirm] = useState<number[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [menu, setMenu] = useState<{ x: number; y: number; i: number } | null>(null);
  const [menuAnchor, setMenuAnchor] = useState<HTMLSpanElement | null>(null);
  const closeMenu = useCallback(() => setMenu(null), []);
  const items = useMemo(() => q.data?.items ?? [], [q.data]);

  const groups = useMemo(() => {
    const out: { day: string; items: { t: TrashItem; i: number }[] }[] = [];
    items.forEach((t, i) => {
      const day = (t.applied_at ?? "").slice(0, 10);
      if (!out.length || out[out.length - 1].day !== day) out.push({ day, items: [] });
      out[out.length - 1].items.push({ t, i });
    });
    return out;
  }, [items]);

  // Liste değişince artık olmayan seçimleri bırak
  useEffect(() => setSel((s) => new Set([...s].filter((id) => items.some((t) => t.id === id)))), [items]);

  const toggle = (i: number, e: React.MouseEvent) => {
    const id = items[i].id;
    setSel((s) => {
      const next = new Set(s);
      if (e.shiftKey && anchor !== null) {
        const [a, b] = anchor < i ? [anchor, i] : [i, anchor];
        for (let k = a; k <= b; k++) next.add(items[k].id);
      } else if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
    setAnchor(i);
  };

  const refresh = () => qc.invalidateQueries();
  const restore = async (ids: number[]) => {
    if (!ids.length || busy) return;
    setBusy(true);
    try {
      const r = await api.restoreTrash(repoId!, ids);
      const photos = ids.length;
      toast(r.failed || r.skipped ? `${fmtInt(photos - r.failed - r.skipped)} öğe geri getirildi, ${fmtInt(r.failed + r.skipped)} öğe getirilemedi` : `${fmtInt(photos)} öğe geri getirildi`, { error: !!r.failed });
      setSel(new Set());
      refresh();
    } catch (e) {
      toast(String((e as Error).message), { error: true });
    }
    setBusy(false);
  };
  const purge = async (ids: number[]) => {
    setConfirm(null);
    setBusy(true);
    try {
      const all = ids.length === items.length && (q.data?.files ?? 0) === items.length;
      const r = all ? await api.emptyTrash(repoId!) : await api.deleteTrash(repoId!, ids);
      toast(`Kalıcı olarak silindi. ${fmtBytes(r.bytes)} yer açıldı.`);
      setSel(new Set());
      refresh();
    } catch (e) {
      toast(String((e as Error).message), { error: true });
    }
    setBusy(false);
  };

  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement)?.closest("input, textarea, select") || confirm) return;
      if (e.metaKey && e.key.toLowerCase() === "a") { setSel(new Set(items.map((t) => t.id))); e.preventDefault(); }
      else if (e.key === "Escape") setSel(new Set());
      else if ((e.key === "Backspace" || e.key === "Delete") && sel.size) { setConfirm([...sel]); e.preventDefault(); }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [items, sel, confirm]);

  if (!repoId) return null;
  const n = items.length;
  const selIds = [...sel];
  const selBytes = items.filter((t) => sel.has(t.id)).reduce((a, t) => a + t.size, 0);
  const target = selIds.length ? selIds : items.map((t) => t.id);
  const confirmBytes = confirm ? items.filter((t) => confirm.includes(t.id)).reduce((a, t) => a + t.size, 0) : 0;

  return (
    <>
      <Toolbar title="Son silinenler"
        subtitle={sel.size ? `${fmtInt(sel.size)} öğe seçili · ${fmtBytes(selBytes)}` : n ? `${fmtInt(n)} öğe · ${fmtBytes(q.data?.bytes)}` : undefined}>
        {n > 0 && (
          <>
            <button className="btn danger" disabled={busy} onClick={() => setConfirm(target)}>{sel.size ? "Sil…" : "Tümünü sil…"}</button>
            <button className="btn pri" disabled={busy} onClick={() => restore(target)}>{sel.size ? `Geri getir (${fmtInt(sel.size)})` : "Tümünü geri getir"}</button>
          </>
        )}
      </Toolbar>
      <div className="scroll" style={{ padding: "0 16px 24px" }} onClick={(e) => { if (e.target === e.currentTarget) setSel(new Set()); }}>
        {n > 0 && (
          <div className="muted" style={{ fontSize: 12, padding: "12px 0 0" }}>
            Silinen fotoğraf ve videolar sen kalıcı olarak silene kadar burada bekler. Seçmek için tıkla, büyütmek için çift tıkla.
          </div>
        )}
        {q.data && n === 0 && (
          <div className="empty"><Trash size={40} /><h2>Son silinen yok</h2><span>Sildiğin fotoğraf ve videolar kalıcı olarak silinene kadar burada durur.</span></div>
        )}
        {groups.map((g) => (
          <section key={g.day}>
            <div className="group-h"><h2>{dayLabel(g.day)}</h2><span className="muted">{fmtInt(g.items.length)} öğe</span></div>
            <div className="trash-grid">
              {g.items.map(({ t, i }) => (
                <TrashTile key={t.id} repoId={repoId} t={t} selected={sel.has(t.id)} onToggle={(e) => toggle(i, e)}
                  onMenu={(e) => { if (!sel.has(t.id)) { setSel(new Set([t.id])); setAnchor(i); } setMenu({ x: e.clientX, y: e.clientY, i }); }}
                  onOpen={() => openViewer(items.map(toItem), i, { trash: Object.fromEntries(items.map((x) => [x.file_id, x.id])) })} />
              ))}
            </div>
          </section>
        ))}
      </div>
      {menu && (() => {
        const ids = sel.size ? [...sel] : [items[menu.i]?.id].filter((x): x is number => x !== undefined);
        const n = ids.length;
        return (
          <>
            <span key={`${menu.x}-${menu.y}`} ref={setMenuAnchor} style={{ position: "fixed", left: menu.x, top: menu.y - 4, width: 0, height: 0 }} />
            {menuAnchor && (
              <Popover anchor={menuAnchor} onClose={closeMenu} width={220} align="start">
                {n > 1 && <div className="lbl" style={{ padding: "4px 10px 2px" }}>{fmtInt(n)} öğe seçili</div>}
                {n === 1 && <button className="mitem" onClick={() => { closeMenu(); openViewer(items.map(toItem), menu.i, { trash: Object.fromEntries(items.map((x) => [x.file_id, x.id])) }); }}>Büyüt</button>}
                <button className="mitem" onClick={() => { closeMenu(); restore(ids); }}>{n > 1 ? `${fmtInt(n)} öğeyi geri getir` : "Geri getir"}</button>
                <div className="msep" />
                <button className="mitem danger" onClick={() => { closeMenu(); setConfirm(ids); }}>Kalıcı olarak sil…<span className="kbd-hint">⌫</span></button>
              </Popover>
            )}
          </>
        );
      })()}
      {confirm && (
        <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && setConfirm(null)}>
          <div className="dialog" style={{ maxWidth: 400 }} role="alertdialog" aria-modal="true" aria-labelledby="purge-title">
            <header><h2 id="purge-title">{fmtInt(confirm.length)} öğe kalıcı olarak silinsin mi?</h2></header>
            <div className="body"><span className="muted">Bu işlem geri alınamaz. {fmtBytes(confirmBytes)} yer açılacak.</span></div>
            <footer>
              <button className="btn" onClick={() => setConfirm(null)} autoFocus>Vazgeç</button>
              <button className="btn danger solid" onClick={() => purge(confirm)}>Sil</button>
            </footer>
          </div>
        </div>
      )}
    </>
  );
}
